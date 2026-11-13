# -*- coding: utf-8 -*-
"""
设置窗口的配色回归测试。

背景：语音页因为套了一层 QScrollArea，曾经出现两个问题——
  1. 内层 QWidget 用系统调色板刷出一块浅灰底（#efefef），和别的页签的深紫底（#241d38）不一致；
  2. 给内层单独写的 `QLabel{color:...}` 盖掉了全局样式，标签色和 hint 色都跑偏。
这条测试就是钉住这两点：语音页的底色、标签色、说明色必须和全局样式一致。
"""
from __future__ import annotations

import os
import sys
from collections import Counter

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QTabWidget  # noqa: E402

from app.config import Config  # noqa: E402
from app.sprites import SpriteLibrary  # noqa: E402
from app.voice.service import VoiceService  # noqa: E402
from app.ui.panels import SettingsDialog  # noqa: E402

# 全局样式里写死的三个颜色（改 _DIALOG_QSS 时这里要一起改）
BG = "#241d38"
LABEL = "#ddd2f5"
HINT = "#9a8fbe"


@pytest.fixture(scope="module")
def app():
    """整个模块共用一个 QApplication（Qt 不允许重复创建）。"""
    inst = QApplication.instance() or QApplication([])
    # 离屏平台没有字体目录，手动挂一份中文字体，否则标签宽度算不出来
    QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\msyh.ttc")
    yield inst


def _open_voice_tab(app) -> SettingsDialog:
    cfg = Config()
    dlg = SettingsDialog(cfg, SpriteLibrary(cfg), roles=[], roles_source="",
                         voice=VoiceService(cfg))
    dlg.resize(600, 720)
    dlg.show()
    app.processEvents()
    tabs = dlg.findChild(QTabWidget)
    for index in range(tabs.count()):
        if tabs.tabText(index) == "语音":
            tabs.setCurrentIndex(index)
            break
    app.processEvents()
    app.processEvents()
    return dlg


def test_voice_tab_matches_global_theme(app):
    dlg = _open_voice_tab(app)
    try:
        scroll = dlg.findChild(QScrollArea)
        assert scroll is not None, "语音页应该是套在滚动区里的"
        inner = scroll.widget()
        assert inner.styleSheet() == "", "内层不要再单独写样式，否则会盖掉全局 QSS"

        # 1) 底色：标签列那一溜的像素主色必须是深紫，不能是浅灰
        image = dlg.grab().toImage()
        counter = Counter()
        for y in range(240, 380, 2):
            for x in range(10, 170, 2):
                counter[image.pixelColor(x, y).name()] += 1
        if not counter:
            pytest.skip("离屏环境没渲染出页签内容")
        dominant = counter.most_common(1)[0][0]
        assert dominant.lower() == BG, f"语音页底色是 {dominant}，其它页签是 {BG}"

        # 2) 字色：普通标签 / 说明标签分别对齐全局样式
        labels = inner.findChildren(QLabel)
        assert labels, "语音页应该有标签"
        normal = [w for w in labels if w.objectName() != "hint"]
        assert normal, "语音页应该有普通标签"
        got = normal[0].palette().color(QPalette.WindowText).name().lower()
        assert got == LABEL, f"标签色是 {got}，其它页签是 {LABEL}"

        hints = [w for w in labels if w.objectName() == "hint"]
        if hints:  # 音色表读不到时也会有 hint，正常构造下一定存在
            got_hint = hints[0].palette().color(QPalette.WindowText).name().lower()
            assert got_hint == HINT, f"说明文字颜色是 {got_hint}，其它页签是 {HINT}"
    finally:
        dlg.close()
