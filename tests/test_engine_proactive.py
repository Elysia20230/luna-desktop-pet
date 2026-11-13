# -*- coding: utf-8 -*-
"""
"她自己开口说话"的测试。

主动说话和正常对话只差几个细节，但这几个细节错了会很烦人：
- 把自言自语写进 history → 下一轮模型以为你在跟它聊天；
- 触发记忆提炼 → 把一句"……今天好安静"提炼成"事实"；
- 走 text_chunk → 对话窗里冒出一个空气泡。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat import engine as engine_mod              # noqa: E402
from app.chat.engine import PROACTIVE_TRIGGER, ChatEngine   # noqa: E402
from app.config import Config                          # noqa: E402
from app.memory.store import MemoryStore               # noqa: E402
from app.roles import Role                             # noqa: E402


class _FakeSignal:
    """冒充 Qt 的 Signal：只记录槽函数，emit 时同步调用。"""

    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, func) -> None:
        self.slots.append(func)

    def emit(self, *args) -> None:
        for func in list(self.slots):
            func(*args)


class _RecordingWorker:
    """冒充 ChatWorker：记下构造参数，并提供可以"手动回放"的信号。"""

    last_kwargs: dict | None = None
    last_instance: "_RecordingWorker | None" = None

    def __init__(self, **kwargs) -> None:
        type(self).last_kwargs = kwargs
        type(self).last_instance = self
        self.chunk = _FakeSignal()
        self.finished_ok = _FakeSignal()
        self.failed = _FakeSignal()
        self.started = False
        self.running = False

    def start(self) -> None:
        self.started = True
        self.running = True

    def isRunning(self) -> bool:  # noqa: N802 跟 Qt 的写法保持一致
        return self.running

    def stop(self) -> None:
        self.running = False


class _StubRetriever:
    def retrieve(self, query: str) -> list[dict]:
        return []

    def format_for_prompt(self, facts: list[dict]) -> str:
        return ""

    def sync_index(self) -> str:
        return ""


class _StubPersona:
    """记下每次被要求拼 prompt 时的参数，方便断言"是主动场合"。"""

    def __init__(self, role: Role | None = None) -> None:
        self.role = role
        self.calls: list[dict] = []

    def system_prompt(self, memory_text: str = "", screen_text: str = "",
                      max_chars: int = 60, proactive: bool = False,
                      screen_comment: bool = False, now=None, last_talk=None) -> str:
        self.calls.append({"proactive": proactive, "max_chars": max_chars,
                           "now": now, "last_talk": last_talk})
        return "SYSTEM-PROACTIVE" if proactive else "SYSTEM"


def _make_engine(tmp_path: Path, persona=None) -> ChatEngine:
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("api.api_key", "sk-test-123")
    store = MemoryStore(path=tmp_path / "memory.json")
    return ChatEngine(cfg, store, _StubRetriever(), persona or _StubPersona())


# --------------------------------------------------------------------------
# 1. 主动说话：用假用户消息触发，且不污染历史
# --------------------------------------------------------------------------
def test_send_proactive_uses_trigger_and_keeps_history_clean(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    monkeypatch.setattr(engine_mod, "HISTORY_FILE", tmp_path / "history.json")

    persona = _StubPersona()
    engine = _make_engine(tmp_path, persona)
    said: list[tuple[str, str]] = []
    engine.proactive_said.connect(lambda text, emo: said.append((text, emo)))

    engine.send_proactive()

    kwargs = _RecordingWorker.last_kwargs
    assert kwargs is not None, "应该构造了 ChatWorker"
    assert kwargs["messages"][-1]["content"] == PROACTIVE_TRIGGER
    assert kwargs["messages"][-1]["role"] == "user", "得伪装成一条用户消息才能触发"

    # 真正的 ChatWorker 会在工作线程里调这个 factory；假 worker 不会，这里手动调
    kwargs["system_factory"]()
    assert persona.calls[-1]["proactive"] is True, "人设那边要按「主动场合」拼"

    # 模拟模型回话：正文 + 情绪标记
    _RecordingWorker.last_instance.finished_ok.emit("……今天倒挺安静。<mood:温柔>")

    assert engine.history == [], "自言自语不能写进 history"
    assert engine._turn_count == 0, "自言自语不算一轮对话，不触发记忆提炼"
    assert said == [("……今天倒挺安静。", "温柔")]


def test_send_proactive_does_not_stream_chunks(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    engine = _make_engine(tmp_path)

    chunks: list[str] = []
    engine.text_chunk.connect(chunks.append)

    engine.send_proactive()
    worker = _RecordingWorker.last_instance
    worker.chunk.emit("……")
    worker.finished_ok.emit("……<mood:平静>")

    assert chunks == [], "主动说话不该往对话窗的气泡里填字"


# --------------------------------------------------------------------------
# 2. 正在回话时不要抢话
# --------------------------------------------------------------------------
def test_send_proactive_ignored_while_busy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    engine = _make_engine(tmp_path)

    engine.send_proactive()
    engine._worker.running = True          # 假装上一句还在生成

    _RecordingWorker.last_kwargs = None
    engine.send_proactive()

    assert _RecordingWorker.last_kwargs is None, "忙的时候不该再开一个线程"


# --------------------------------------------------------------------------
# 3. 失败要退到内置台词，而不是安静不动
# --------------------------------------------------------------------------
def test_send_proactive_falls_back_on_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    persona = _StubPersona(Role(id="asahi", label="小仓朝日", rules="调戏他。"))
    engine = _make_engine(tmp_path, persona)

    notes: list[str] = []
    said: list[tuple[str, str]] = []
    engine.note.connect(notes.append)
    engine.proactive_said.connect(lambda text, emo: said.append((text, emo)))

    engine.send_proactive()
    _RecordingWorker.last_instance.failed.emit("连接超时")

    assert notes and "自己搭话没成功" in notes[0]
    assert said, "失败也要有话说（退回内置台词）"
    assert said[0][0], "台词不能是空的"
    assert engine.busy is False


# --------------------------------------------------------------------------
# 4. 离线模式下主动说话走内置台词（不需要 threading，直接看台词函数）
# --------------------------------------------------------------------------
def test_offline_proactive_uses_builtin_lines(tmp_path: Path):
    engine = _make_engine(tmp_path)
    engine.cfg.set("api.api_key", "")       # 没填 Key：离线

    said: list[tuple[str, str]] = []
    engine.proactive_said.connect(lambda text, emo: said.append((text, emo)))
    engine._offline_proactive()

    assert len(said) == 1
    text, emotion = said[0]
    assert text
    assert "{name}" not in text, "占位符必须被替换掉"
    assert emotion
