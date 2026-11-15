# -*- coding: utf-8 -*-
"""
对话线程的生命周期：线程没真正结束之前，engine 不能放掉它。

背景（v1.0.2 线上崩溃）：
    打包版里用户填完 API 之后发第一句话，进程直接消失。
    Windows 事件日志是 Qt6Core.dll / 0xc0000409 / BEX64 —— 这是 Qt 的 qFatal，
    最常见的一句就是 "QThread: Destroyed while thread is still running"。

    原因：ChatWorker 在工作线程里 emit finished_ok，主线程收到槽时 run() 可能还没返回，
    如果这时把 engine._worker 置空，Python 引用归零 → QThread 的 C++ 对象被销毁 →
    Qt 发现线程还在跑 → abort。

    修法：引用只在 QThread.finished（线程真正退出）之后释放。
    本文件就是钉住这个行为，防止以后又被人"顺手"改回去。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import app.chat.engine as engine_mod  # noqa: E402
from app.chat.engine import ChatEngine  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """Qt 的线程对象必须有 QApplication 才能跑。"""
    app = QApplication.instance() or QApplication([])
    yield app


class _StubCfg:
    """只提供 engine 会读的那几个键。"""

    def __init__(self) -> None:
        self.api_key = "sk-test"
        self.data = {
            "api.base_url": "https://api.deepseek.com",
            "api.model": "deepseek-chat",
            "api.temperature": 1.0,
            "api.timeout_connect": 5,
            "api.timeout_total": 10,
            "api.max_chars": 60,
            "behavior.time_aware": False,
            "memory.auto_extract_every": 0,
            "memory.top_k": 6,
        }

    def get(self, key, default=None):
        return self.data.get(key, default)


class _StubStore:
    def touch(self, ids):
        return None

    def facts(self):
        return []


class _StubRetriever:
    def retrieve(self, text):
        return []

    def format_for_prompt(self, hits):
        return ""


class _StubPersona:
    role = None

    def system_prompt(self, *args, **kwargs):
        return "你是一只桌宠。"


class _SlowWorker(QThread):
    """假的对话线程：先 emit 结果，再磨蹭一会儿才真正退出。"""

    def __init__(self, **kwargs) -> None:
        super().__init__()
        self.chunk = _FakeSignal()
        self.finished_ok = _FakeSignal()
        self.failed = _FakeSignal()
        self.emitted = False

    def run(self) -> None:
        self.finished_ok.emit("说完了")
        self.emitted = True
        # 故意拖一段时间：模拟"槽函数已经执行、但 run() 还没返回"的真实窗口
        time.sleep(0.25)

    def stop(self) -> None:
        return None


class _FakeSignal:
    def __init__(self) -> None:
        self._slots = []

    def connect(self, slot):
        self._slots.append(slot)

    def emit(self, *args):
        for slot in list(self._slots):
            slot(*args)


def _make_engine(monkeypatch):
    monkeypatch.setattr(engine_mod, "ChatWorker", _SlowWorker)
    return ChatEngine(_StubCfg(), _StubStore(), _StubRetriever(), _StubPersona())


def test_worker_reference_survives_until_thread_finishes(qapp, monkeypatch):
    """结果已经到手，但线程还在跑时，引用必须保留。"""
    engine = _make_engine(monkeypatch)
    finished = []
    engine.finished.connect(lambda text, emo: finished.append(text))

    engine.send("你好")
    worker = engine._worker
    assert isinstance(worker, _SlowWorker)

    # 等结果传回主线程（此时 run() 还没返回）
    deadline = time.time() + 3
    while not finished and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert finished == ["说完了"]
    assert engine._worker is not None, "run() 还没返回就丢了引用 → Qt 会 abort"
    assert worker.isRunning()

    # 线程真正结束后，引用才允许被清掉
    assert worker.wait(3000)
    deadline = time.time() + 2
    while engine._worker is not None and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert engine._worker is None


def test_failure_path_keeps_reference_too(qapp, monkeypatch):
    """失败分支同理：不能在 _on_failed 里提前把线程放掉。"""
    engine = _make_engine(monkeypatch)
    failed = []
    engine.failed.connect(failed.append)

    engine.send("你好")
    worker = engine._worker
    worker.failed.emit("网络不好")
    qapp.processEvents()
    assert failed == ["网络不好"]
    assert engine._worker is worker, "失败也要等线程退出后再释放"
    assert worker.wait(3000)


def test_has_worker_tracks_reference(qapp, monkeypatch):
    """main.py 用 has_worker() 判断旧引擎能不能回收。"""
    engine = _make_engine(monkeypatch)
    assert engine.has_worker() is False
    engine.send("你好")
    assert engine.has_worker() is True
    assert engine._worker.wait(3000)
    deadline = time.time() + 2
    while engine.has_worker() and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    assert engine.has_worker() is False
