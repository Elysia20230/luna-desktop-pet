# -*- coding: utf-8 -*-
"""
「看看我在干什么」的测试。

这个功能最容易做错的地方就一件事：**别让它变成一次对话**。
一旦偷懒复用 send()，就会连带发生三件事——
写进 history.json、出现在聊天记录窗口、被记忆提炼当成事实记下来。
所以下面每条测试盯的都是"没发生什么"，不是"显示了什么"。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat import engine as engine_mod                       # noqa: E402
from app.chat.engine import SCREEN_TRIGGER, ChatEngine          # noqa: E402
from app.config import Config                                   # noqa: E402
from app.memory.store import MemoryStore                        # noqa: E402
from app.persona import Persona                                 # noqa: E402
from app.roles import Role                                      # noqa: E402


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
        self.running = False

    def start(self) -> None:
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
    """记下每次拼 prompt 的参数，用来断言"是看屏幕场合、不是主动搭话场合"。"""

    def __init__(self, role: Role | None = None) -> None:
        self.role = role
        self.calls: list[dict] = []

    def system_prompt(self, memory_text: str = "", screen_text: str = "",
                      max_chars: int = 60, proactive: bool = False,
                      screen_comment: bool = False, now=None, last_talk=None) -> str:
        self.calls.append({
            "proactive": proactive,
            "screen_comment": screen_comment,
            "screen_text": screen_text,
            "max_chars": max_chars,
        })
        return "SYSTEM-SCREEN" if screen_comment else "SYSTEM"


def _make_engine(tmp_path: Path, persona=None) -> ChatEngine:
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("api.api_key", "sk-test-123")
    store = MemoryStore(path=tmp_path / "memory.json")
    return ChatEngine(cfg, store, _StubRetriever(), persona or _StubPersona())


# --------------------------------------------------------------------------
# 1. 触发方式正确，而且不污染历史
# --------------------------------------------------------------------------
def test_send_screen_comment_uses_trigger_and_keeps_history_clean(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    monkeypatch.setattr(engine_mod, "HISTORY_FILE", tmp_path / "history.json")

    persona = _StubPersona()
    engine = _make_engine(tmp_path, persona)
    engine.set_screen_text("她瞄到你正在用的窗口叫「设计稿.psd」。")

    said: list[tuple[str, str]] = []
    engine.screen_said.connect(lambda text, emo: said.append((text, emo)))

    engine.send_screen_comment()

    kwargs = _RecordingWorker.last_kwargs
    assert kwargs is not None, "在线时应该构造 ChatWorker"
    assert kwargs["messages"][-1]["content"] == SCREEN_TRIGGER
    assert kwargs["messages"][-1]["role"] == "user", "得伪装成一条用户消息才能触发"

    # 真正的 ChatWorker 在工作线程里调这个 factory；假 worker 不会，这里手动调
    kwargs["system_factory"]()
    call = persona.calls[-1]
    assert call["screen_comment"] is True, "人设要按「看屏幕」场合拼"
    assert call["proactive"] is False, "看屏幕和自言自语是两种场合，不能混"
    assert "设计稿" in call["screen_text"], "画面描述要带进去"

    _RecordingWorker.last_instance.finished_ok.emit("又在改那套裙子啊。<mood:无语>")

    assert engine.history == [], "偷看一眼不能写进 history"
    assert engine._turn_count == 0, "不算一轮对话，不触发记忆提炼"
    assert said == [("又在改那套裙子啊。", "无语")]


def test_send_screen_comment_streams_chunks(tmp_path: Path, monkeypatch):
    """看屏幕是要边生成边冒字的，所以必须走流式（stream=True）。"""
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    engine = _make_engine(tmp_path)

    chunks: list[str] = []
    engine.text_chunk.connect(chunks.append)

    engine.send_screen_comment()
    worker = _RecordingWorker.last_instance
    worker.chunk.emit("又在")
    worker.chunk.emit("改裙子啊。")

    assert chunks, "应该往气泡里填字"
    assert "又在改裙子啊。" in chunks[-1]


# --------------------------------------------------------------------------
# 2. 她正说着话时不要抢
# --------------------------------------------------------------------------
def test_send_screen_comment_ignored_while_busy(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    engine = _make_engine(tmp_path)

    engine.send_screen_comment()
    engine._worker.running = True          # 假装上一句还在生成

    _RecordingWorker.last_kwargs = None
    engine.send_screen_comment()

    assert _RecordingWorker.last_kwargs is None, "忙的时候不该再开一个线程"


# --------------------------------------------------------------------------
# 3. 失败要退到内置台词，而不是一声不吭
# --------------------------------------------------------------------------
def test_send_screen_comment_falls_back_on_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    persona = _StubPersona(Role(id="asahi", label="小仓朝日", rules="调戏他。"))
    engine = _make_engine(tmp_path, persona)

    notes: list[str] = []
    said: list[tuple[str, str]] = []
    engine.note.connect(notes.append)
    engine.screen_said.connect(lambda text, emo: said.append((text, emo)))

    engine.send_screen_comment()
    _RecordingWorker.last_instance.failed.emit("连接超时")

    assert notes and "看屏幕没说话成" in notes[0]
    assert said, "失败也要有话说（退回内置台词）"
    assert said[0][0] and "{name}" not in said[0][0]
    assert engine.busy is False


# --------------------------------------------------------------------------
# 4. 离线：不建线程，直接用内置台词
# --------------------------------------------------------------------------
def test_offline_screen_comment_uses_builtin_lines(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    engine = _make_engine(tmp_path)
    engine.cfg.set("api.api_key", "")       # 没填 Key：离线

    said: list[tuple[str, str]] = []
    engine.screen_said.connect(lambda text, emo: said.append((text, emo)))

    _RecordingWorker.last_kwargs = None     # 清掉上一条用例留下的记录
    engine.send_screen_comment()            # 离线分支只挂一个 320ms 定时器
    assert _RecordingWorker.last_kwargs is None, "离线不该联网"

    engine._offline_screen()                # 测试里没有事件循环，手动触发那一步
    assert len(said) == 1
    text, emotion = said[0]
    assert text and "{name}" not in text
    assert emotion


# --------------------------------------------------------------------------
# 5. 人设：只在看屏幕时多出【场合】那一段
# --------------------------------------------------------------------------
def test_persona_adds_screen_comment_section_only_when_asked():
    persona = Persona(include_relations=False,
                      role=Role(id="asahi", label="小仓朝日", rules="你叫他「朝日」。")).load()

    normal = persona.system_prompt(max_chars=40)
    screen = persona.system_prompt(max_chars=40, screen_comment=True)
    proactive = persona.system_prompt(max_chars=40, proactive=True)

    assert "【场合】" not in normal, "普通对话不该有场合段"
    assert "【场合】" in screen and "看了一眼他的屏幕" in screen
    assert "25 字以内" not in screen, "别把自言自语的字数限制串过来"
    assert "25 字以内" in proactive
    assert screen.count("【场合】") == 1
