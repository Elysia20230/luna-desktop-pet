# -*- coding: utf-8 -*-
"""屏幕感知：截图、识别当前在用什么窗口、把结果变成一句给露娜看的话。"""
from .context import ScreenDigest, grab_screen_png, foreground_window, build_digest, ScreenWorker
from .vision import describe_image, VisionError

__all__ = [
    "ScreenDigest", "grab_screen_png", "foreground_window", "build_digest",
    "ScreenWorker", "describe_image", "VisionError",
]
