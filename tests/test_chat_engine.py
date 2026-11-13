# -*- coding: utf-8 -*-
"""
对话链路的回归测试（不联网、不开窗口）。

这几个用例是为了防住一类"看不见的 bug"：
    参数名写错 → 构造线程时抛 TypeError → Qt 把异常吞掉 → 界面永远停在
    "露娜正在斟酌用词…"，用户只看到"很慢"，其实是一个字都没发出去。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat import client as client_mod                  # noqa: E402
from app.chat import engine as engine_mod                  # noqa: E402
from app.chat.client import ChatWorker                     # noqa: E402
from app.chat.engine import ChatEngine                     # noqa: E402
from app.config import Config                              # noqa: E402
from app.memory.store import MemoryStore                   # noqa: E402


# --------------------------------------------------------------------------
# 假的零件：不联网、不起线程，只记录"被怎么调用的"
# --------------------------------------------------------------------------
class _FakeSignal:
    """冒充 Qt 的 Signal：只把槽函数存起来，emit 时同步调用。"""

    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, func) -> None:
        self.slots.append(func)

    def emit(self, *args) -> None:
        for func in list(self.slots):
            func(*args)


class _RecordingWorker:
    """冒充 ChatWorker：把构造参数记下来，start() 只置个标记。"""

    last_kwargs: dict | None = None

    def __init__(self, **kwargs) -> None:
        type(self).last_kwargs = kwargs
        self.chunk = _FakeSignal()
        self.finished_ok = _FakeSignal()
        self.failed = _FakeSignal()
        self.started = False

    def start(self) -> None:
        self.started = True

    def isRunning(self) -> bool:  # noqa: N802 跟 Qt 的写法保持一致
        return False

    def stop(self) -> None:
        pass


class _ExplodingWorker:
    """冒充 ChatWorker：一构造就炸，用来验证"异常也会被报出来"。"""

    def __init__(self, **kwargs) -> None:
        raise TypeError("模拟的参数写错")


class _StubRetriever:
    def retrieve(self, query: str) -> list[dict]:
        return []

    def format_for_prompt(self, facts: list[dict]) -> str:
        return ""


class _StubPersona:
    def system_prompt(self, memory_text: str = "", screen_text: str = "",
                      max_chars: int = 60) -> str:
        return "SYSTEM"


def _make_engine(tmp_path: Path) -> ChatEngine:
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("api.api_key", "sk-test-123")
    cfg.set("api.timeout_connect", 25)
    cfg.set("api.timeout_total", 99)
    store = MemoryStore(path=tmp_path / "memory.json")
    return ChatEngine(cfg, store, _StubRetriever(), _StubPersona())


# --------------------------------------------------------------------------
# 1. 超时参数必须按名字对上（就是这次踩的坑）
# --------------------------------------------------------------------------
def test_engine_passes_timeouts_to_worker(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _RecordingWorker)
    engine = _make_engine(tmp_path)

    engine.send("在吗")

    kwargs = _RecordingWorker.last_kwargs
    assert kwargs is not None, "应该构造了 ChatWorker"
    assert kwargs["timeout_connect"] == 25, "连接超时应该取自 api.timeout_connect"
    assert kwargs["timeout_total"] == 99, "总超时应该取自 api.timeout_total"
    assert callable(kwargs["system_factory"]), "人设拼装要交给工作线程"
    # 对话消息：最后一条是用户这句话，且此时还没插入 system（由工作线程补）
    assert kwargs["messages"][-1] == {"role": "user", "content": "在吗"}


# --------------------------------------------------------------------------
# 2. 构造线程失败时，必须发 failed 信号，而不是静默卡住
# --------------------------------------------------------------------------
def test_engine_reports_worker_error(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _ExplodingWorker)
    engine = _make_engine(tmp_path)

    reported: list[BaseException] = []
    monkeypatch.setattr(engine, "_report_internal_error", reported.append)

    failed: list[str] = []
    engine.failed.connect(failed.append)

    engine.send("在吗")                       # 不允许把异常抛给调用方

    assert reported and isinstance(reported[0], TypeError)
    assert failed and "内部错误" in failed[0]
    assert engine.busy is False


# --------------------------------------------------------------------------
# 3. system 提示词在 worker 里拼，且 SSE 流能被正确解析
# --------------------------------------------------------------------------
class _FakeResponse:
    status_code = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def iter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"你"}}]}'
        yield 'data: {"choices":[{"delta":{"content":"好"}}]}'
        yield "data: [DONE]"


class _FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def stream(self, method, url, json=None, headers=None):
        return _FakeResponse()


def test_worker_builds_system_in_thread(monkeypatch):
    monkeypatch.setattr(client_mod.httpx, "Client", _FakeClient)

    called: dict = {}

    def factory() -> str:
        called["thread"] = threading.current_thread().name
        return "SYSTEM-PROMPT"

    worker = ChatWorker(
        base_url="https://example.invalid",
        api_key="sk-test-123",
        model="test-model",
        messages=[{"role": "user", "content": "在吗"}],
        system_factory=factory,
    )
    # 直接同步调用 run()：不需要事件循环，也能验证消息拼装与流解析
    assert worker._final_messages()[0] == {"role": "system", "content": "SYSTEM-PROMPT"}

    chunks: list[str] = []
    done: list[str] = []
    worker.chunk.connect(chunks.append)
    worker.finished_ok.connect(done.append)
    worker.run()

    assert chunks == ["你", "好"]
    assert done == ["你好"]
    assert called, "system_factory 应该被调用过"


def test_worker_reports_factory_error(monkeypatch):
    monkeypatch.setattr(client_mod.httpx, "Client", _FakeClient)

    def boom() -> str:
        raise RuntimeError("检索炸了")

    worker = ChatWorker(base_url="https://example.invalid", api_key="sk-test-123",
                        model="test-model", messages=[], system_factory=boom)
    failed: list[str] = []
    worker.failed.connect(failed.append)
    worker.run()

    assert failed and "准备台词的时候出错了" in failed[0]


# --------------------------------------------------------------------------
# 4. 记忆库要能扛住"后台线程边写、界面线程边读"
# --------------------------------------------------------------------------
def test_memory_store_concurrent_writes(tmp_path: Path):
    store = MemoryStore(path=tmp_path / "memory.json")

    def work(tag: int) -> None:
        for i in range(20):
            # 每条塞一个不同的汉字，避免被"近似去重"合并掉
            store.add_fact(f"并发{chr(0x4E00 + tag * 40 + i)}条")

    threads = [threading.Thread(target=work, args=(t,)) for t in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(store.facts()) == 80, "4 个线程各写 20 条，不该丢"
    saved = json.loads((tmp_path / "memory.json").read_text(encoding="utf-8"))
    assert len(saved["facts"]) == 80, "落盘的内容也要完整"
