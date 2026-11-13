# -*- coding: utf-8 -*-
"""
设置 → 其它 里「心情大好～」按钮的界面回归测试。

用户明确要求：这个按钮要**和测试功能独立**。
所以这里既验证它能发信号，也验证它不会连带触发
「让她现在说一句」和「用日语说一句」这两条测试链路。

跑法：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402

from app.config import Config  # noqa: E402
from app.sprites import SpriteLibrary  # noqa: E402
from app.ui.panels import SettingsDialog  # noqa: E402

BUTTON_TEXT = "让她说一句「心情大好～」"


@pytest.fixture(scope="module")
def app():
    """整套测试共用一个 QApplication（Qt 不允许重复创建）。"""
    instance = QApplication.instance() or QApplication([])
    yield instance


@pytest.fixture()
def dialog(app):
    cfg = Config()
    dlg = SettingsDialog(cfg, SpriteLibrary(cfg), roles=[], roles_source="")
    yield dlg
    dlg.close()
    dlg.deleteLater()


def _find_button(dialog: SettingsDialog) -> QPushButton:
    for btn in dialog.findChildren(QPushButton):
        if btn.text().strip() == BUTTON_TEXT:
            return btn
    raise AssertionError(f"没找到按钮：{BUTTON_TEXT}")


def test_button_exists(dialog):
    assert _find_button(dialog).isEnabled()


def test_click_emits_signature_only(dialog):
    """点一下只该发 signature_requested，别的测试信号一次都不该响。"""
    hits = {"signature": 0, "proactive": 0, "voice": 0}
    dialog.signature_requested.connect(lambda: hits.__setitem__("signature", hits["signature"] + 1))
    dialog.proactive_test_requested.connect(
        lambda: hits.__setitem__("proactive", hits["proactive"] + 1)
    )
    dialog.voice_test_requested.connect(lambda: hits.__setitem__("voice", hits["voice"] + 1))

    _find_button(dialog).click()

    assert hits == {"signature": 1, "proactive": 0, "voice": 0}
