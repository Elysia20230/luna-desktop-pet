# -*- coding: utf-8 -*-
"""
信号接线回归测试。

为什么值得单独测：改完设置会重建一个全新的 ChatEngine，
如果忘记把 proactive_said / note 重新接上，症状是"她明明说了话，界面毫无反应"，
而且不会抛异常、不写日志——只在真机上表现为"主动搭话没反应"。
这里用假的引擎和假的控制器把接线本身钉死。

跑法：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from main import connect_engine_signals      # noqa: E402


class _FakeSignal:
    """只为记录"谁接上来了"的极简信号。"""

    def __init__(self) -> None:
        self.slots: list = []

    def connect(self, fn) -> None:
        self.slots.append(fn)


class _FakeEngine:
    def __init__(self) -> None:
        self.proactive_said = _FakeSignal()
        self.screen_said = _FakeSignal()
        self.note = _FakeSignal()


class _FakeController:
    def __init__(self) -> None:
        self.said: list[tuple[str, str]] = []
        self.screened: list[tuple[str, str]] = []
        self.notes: list[str] = []

    def _say(self, text: str, emotion: str) -> None:
        self.said.append((text, emotion))

    def _on_screen_said(self, text: str, emotion: str) -> None:
        self.screened.append((text, emotion))

    def _on_engine_note(self, text: str) -> None:
        self.notes.append(text)


def test_engine_signals_are_connected():
    controller = _FakeController()
    engine = _FakeEngine()

    connect_engine_signals(controller, engine)

    assert engine.proactive_said.slots == [controller._say]
    assert engine.screen_said.slots == [controller._on_screen_said]
    assert engine.note.slots == [controller._on_engine_note]


def test_signals_actually_route_to_controller():
    controller = _FakeController()
    engine = _FakeEngine()
    connect_engine_signals(controller, engine)

    engine.proactive_said.slots[0]("……今天倒挺安静。", "温柔")
    engine.screen_said.slots[0]("又在改裙子啊。", "无语")
    engine.note.slots[0]("自己搭话没成功，先用了内置台词")

    assert controller.said == [("……今天倒挺安静。", "温柔")]
    assert controller.screened == [("又在改裙子啊。", "无语")]
    assert controller.notes == ["自己搭话没成功，先用了内置台词"]


def test_reconnect_after_engine_rebuild():
    """模拟"改完设置重建引擎"：新引擎也得接上，别只接旧的。"""
    controller = _FakeController()
    old_engine = _FakeEngine()
    connect_engine_signals(controller, old_engine)

    new_engine = _FakeEngine()
    connect_engine_signals(controller, new_engine)

    assert new_engine.proactive_said.slots == [controller._say]
    assert new_engine.screen_said.slots == [controller._on_screen_said]
    assert new_engine.note.slots == [controller._on_engine_note]
