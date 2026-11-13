# -*- coding: utf-8 -*-
"""
隐私保护测试：碰到"看起来私密"的窗口，绝不截图。

为什么单独钉一条：这条逻辑平时根本不触发，一旦哪天被重构掉（比如把
_is_sensitive 的判断挪到截图之后），日志里也不会有任何异常，
只有真正看银行页面那天才会露出问题——所以必须用测试守住顺序。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config                                    # noqa: E402
from app.screen import context as ctx                            # noqa: E402


def _no_grab(*_args, **_kwargs):
    raise AssertionError("命中隐私词时不该调用截图函数")


def test_sensitive_title_skips_screenshot(monkeypatch):
    cfg = Config()
    # 只要标题里出现隐私词，就必须在截图之前返回
    monkeypatch.setattr(ctx, "foreground_window", lambda: ("1Password — 登录", "1Password.exe"))
    monkeypatch.setattr(ctx, "grab_screen_png", _no_grab)

    digest = ctx.build_digest(cfg, with_image=True, with_vision=False)

    assert not digest.image_png, "命中隐私词就不该有截图数据（_no_grab 会在这之前抛错）"
    assert digest.skipped_reason == "屏幕内容看起来比较私密"
    assert digest.window_title == "（不显示的窗口）", "连标题都不该带出去"
    prompt = digest.as_prompt()
    assert "私密" in prompt and "只看了一眼窗口标题" in prompt


def test_normal_title_still_captures(monkeypatch):
    """反向确认：正常窗口照常截图，别为了隐私把功能关死了。"""
    cfg = Config()
    monkeypatch.setattr(ctx, "foreground_window", lambda: ("设计稿.psd", "Photoshop.exe"))
    monkeypatch.setattr(ctx, "grab_screen_png", lambda _n: b"fake-png")

    digest = ctx.build_digest(cfg, with_image=True, with_vision=False)

    assert digest.image_png == b"fake-png"
    assert digest.skipped_reason == ""
    assert "设计稿" in digest.as_prompt()
