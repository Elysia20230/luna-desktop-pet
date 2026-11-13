# -*- coding: utf-8 -*-
"""
输入框"跟随"与气泡避让的几何测试。

为什么值得测：这两块全是坐标算术，肉眼看着没事、换个分辨率/多显示器就跑飞。
这里用离屏 QApplication，直接构造矩形做断言，不需要真显示器。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.ui.bubble import SpeechBubble, estimate_msec  # noqa: E402
from app.ui.input_bar import BAR_H, InputBar  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _area() -> QRect:
    return _app.primaryScreen().availableGeometry()


def test_follow_sits_below_when_room():
    """左边留了很多空位：输入框在她脚下。"""
    area = _area()
    bar = InputBar()
    anchor = QRect(area.center().x() - 100, area.top() + 40, 200, 200)
    bar.follow(anchor)
    assert bar.side == "below"
    assert bar.y() >= anchor.bottom()


def test_follow_flips_above_near_bottom():
    """她站在屏幕底部（任务栏上）：输入框翻到头顶，不能掉到屏幕外。"""
    area = _area()
    bar = InputBar()
    anchor = QRect(area.center().x() - 100, area.bottom() - 200, 200, 200)
    bar.follow(anchor)
    assert bar.side == "above"
    assert bar.y() + bar.height() <= anchor.top() + 1


def test_follow_stays_inside_screen_at_edges():
    """她贴到屏幕最左边/最右边：输入框被夹回屏幕内，一整条都点得到。"""
    area = _area()
    bar = InputBar()
    for x in (area.left() - 120, area.right() - 40):
        anchor = QRect(x, area.top() + 100, 200, 200)
        bar.follow(anchor)
        assert bar.x() >= area.left()
        assert bar.x() + bar.width() <= area.right()


def test_follow_keeps_side_sticky():
    """已经翻到头顶后，只要还放得下就别来回跳（去抖，避免上下抖）。"""
    area = _area()
    bar = InputBar()
    bar.follow(QRect(area.center().x() - 100, area.bottom() - 200, 200, 200))
    assert bar.side == "above"
    bar.follow(QRect(area.center().x() - 100, area.bottom() - 240, 200, 200))
    assert bar.side == "above"


def test_follow_width_fits_narrow_screen():
    """屏幕很窄时输入框自动缩短，不会横向溢出。"""
    bar = InputBar()
    area = _area()
    bar.follow(QRect(area.center().x() - 50, area.top() + 50, 100, 100))
    assert bar.width() <= max(280, area.width() - 40)
    assert bar.height() == BAR_H


def test_bubble_prefer_bottom_goes_below():
    """prefer="bottom"（输入框占了头顶）时气泡改贴脚下。"""
    area = _area()
    bubble = SpeechBubble()
    bubble.show_text("测试一下。", QRect(area.center().x() - 100, area.top() + 300, 200, 200), 5000,
                     "bottom")
    anchor = QRect(area.center().x() - 100, area.top() + 300, 200, 200)
    assert bubble.y() >= anchor.bottom() - 1


def test_bubble_default_prefers_top():
    """默认贴头顶。"""
    area = _area()
    bubble = SpeechBubble()
    anchor = QRect(area.center().x() - 100, area.center().y(), 200, 200)
    bubble.show_text("测试一下。", anchor, 5000)
    assert bubble.y() + bubble.height() <= anchor.top() + 1


def test_estimate_msec_grows_and_caps():
    """停留时间随字数变长，但有上限（不能让气泡永远挂着）。"""
    assert estimate_msec("嗯。") >= 4000
    assert estimate_msec("嗯。" * 5) < estimate_msec("嗯。" * 50)
    assert estimate_msec("很长" * 500) <= 20000
