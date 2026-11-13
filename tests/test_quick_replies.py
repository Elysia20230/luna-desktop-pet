# -*- coding: utf-8 -*-
"""
"接一句"按钮的测试：按钮怎么长出来、点了发什么信号、窄屏会不会跑出屏幕。

用 offscreen 平台跑，不需要真的桌面。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest                                 # noqa: E402
from PySide6.QtCore import QRect              # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from app.ui.quick_replies import QuickReplies  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _buttons(widget: QuickReplies) -> list[str]:
    return [b.text() for b in widget.findChildren(QPushButton)]


def _anchor() -> QRect:
    return QRect(400, 400, 240, 60)


def test_buttons_follow_config(qapp):
    w = QuickReplies()
    w.show_for(_anchor(), ["嗯？", "我在呢", "", "  "], seconds=0)
    try:
        texts = _buttons(w)
        assert texts == ["嗯？", "我在呢", "自己写…"]      # 空串被丢掉，末尾总加"自己写"
    finally:
        w.hide_chips()


def test_click_emits_chosen_and_hides(qapp):
    w = QuickReplies()
    got = []
    w.chosen.connect(got.append)
    w.show_for(_anchor(), ["我在呢"], seconds=0)
    try:
        button = next(b for b in w.findChildren(QPushButton) if b.text() == "我在呢")
        button.click()
        assert got == ["我在呢"]
        assert not w.isVisible()
    finally:
        w.hide_chips()


def test_custom_emits_request(qapp):
    w = QuickReplies()
    hit = []
    w.custom_requested.connect(lambda: hit.append(True))
    w.show_for(_anchor(), ["嗯？"], seconds=0)
    try:
        button = next(b for b in w.findChildren(QPushButton) if b.text() == "自己写…")
        button.click()
        assert hit == [True]
    finally:
        w.hide_chips()


def test_stays_on_screen_for_narrow_anchor(qapp):
    """锚点贴右边/贴底部时，按钮整体要夹进屏幕，不能跑出去。"""
    w = QuickReplies()
    screen = qapp.primaryScreen().availableGeometry()
    anchor = QRect(screen.right() - 20, screen.bottom() - 30, 40, 30)
    w.show_for(anchor, ["嗯？", "我在呢", "怎么了？", "自己写…"], seconds=0)
    try:
        geo = w.frameGeometry()
        assert geo.left() >= screen.left()
        assert geo.right() <= screen.right()
        assert geo.top() >= screen.top()
        assert geo.bottom() <= screen.bottom()
    finally:
        w.hide_chips()


def test_reposition_keeps_visible(qapp):
    w = QuickReplies()
    w.show_for(_anchor(), ["嗯？"], seconds=0)
    try:
        w.reposition(QRect(50, 120, 200, 50))
        assert w.isVisible()
    finally:
        w.hide_chips()


def test_timeout_hides(qapp):
    """秒数到点自动收起（直接调 _timer 的超时槽，别真等 12 秒）。"""
    w = QuickReplies()
    w.show_for(_anchor(), ["嗯？"], seconds=12)
    try:
        assert w.isVisible()
        w._timer.timeout.emit()      # 模拟到点
        assert not w.isVisible()
    finally:
        w.hide_chips()
