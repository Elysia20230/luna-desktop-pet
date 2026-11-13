# -*- coding: utf-8 -*-
"""
屏幕内容采集（Windows）。

做两件事：
1. 抓一张屏幕截图（用 Qt 自带的 QScreen.grabWindow，不需要额外库）；
2. 看看现在在前台的是哪个窗口（标题 + 程序名），用来在"看不了图"时也能说句话。

隐私保护：
- 默认不自动看屏幕，必须在设置里手动打开；
- 前台窗口标题命中敏感词（密码、银行、支付…）时直接跳过截图，只上报"她在忙别的事"。
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wt
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QBuffer, QIODevice, QObject, QThread, Qt, Signal
from PySide6.QtGui import QGuiApplication

# 这些窗口标题出现时，不截图、不描述内容（只上报一个模糊的说法）
SENSITIVE_HINTS = (
    "密码", "银行", "支付", "钱包", "转账", "身份证", "验证码",
    "password", "wallet", "bank", "payment", "1password", "bitwarden",
)

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


@dataclass
class ScreenDigest:
    """一次"看屏幕"的结果。"""
    at: str = ""
    window_title: str = ""
    process_name: str = ""
    vision_text: str = ""          # 视觉模型对截图的描述；没配视觉模型时为空
    skipped_reason: str = ""       # 因为隐私原因跳过了截图
    image_png: bytes = b""         # 原始截图的 PNG 字节（debug 用）

    def as_prompt(self) -> str:
        """拼成塞进系统提示词的一段文字。"""
        parts = []
        if self.window_title:
            parts.append(f"她瞄到你正在用的窗口叫「{self.window_title}」")
        if self.process_name:
            parts.append(f"程序是 {self.process_name}")
        if self.vision_text:
            parts.append(f"屏幕画面大致是：{self.vision_text}")
        if self.skipped_reason:
            parts.append(f"（{self.skipped_reason}，所以只看了一眼窗口标题）")
        if not parts:
            return ""
        return "【你现在刚偷看了一眼主人的屏幕】" + "；".join(parts) + "。"


def grab_screen_png(max_side: int = 1280) -> bytes:
    """
    截一张全屏，缩到 max_side 以内，返回 PNG 字节。
    用 Qt 抓屏的好处：不用装 mss / pyautogui，也不会被 DPI 缩放坑到。
    """
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return b""
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return b""
    # 太大的图既慢又费 token，等比缩一下
    if pixmap.width() > max_side or pixmap.height() > max_side:
        pixmap = pixmap.scaled(max_side, max_side,
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


def foreground_window() -> tuple[str, str]:
    """
    返回 (窗口标题, 进程名)。取不到就是两个空字符串——Windows 上偶尔会因为
    权限问题取不到某些窗口，这里全部吞掉，不让它影响主流程。
    """
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()

        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        name = ""
        if handle:
            try:
                size = wt.DWORD(260)
                path_buf = ctypes.create_unicode_buffer(260)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size)):
                    name = path_buf.value.replace("\\", "/").rsplit("/", 1)[-1]
            finally:
                kernel32.CloseHandle(handle)
        return title, name
    except Exception:  # noqa: BLE001
        return "", ""


def _is_sensitive(text: str) -> bool:
    low = (text or "").lower()
    return any(hint.lower() in low for hint in SENSITIVE_HINTS)


def build_digest(cfg, with_image: bool = True, with_vision: bool = True) -> ScreenDigest:
    """
    采集一次屏幕信息。
    with_image=False  → 只取窗口标题（最快，不发任何网络请求）
    with_vision=False → 有截图但不调用视觉模型
    """
    digest = ScreenDigest(at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    digest.window_title, digest.process_name = foreground_window()

    if not with_image:
        return digest

    if _is_sensitive(digest.window_title):
        # 主人大概在处理私密的事，露娜懂事地不细看
        digest.skipped_reason = "屏幕内容看起来比较私密"
        digest.window_title = "（不显示的窗口）"
        return digest

    try:
        digest.image_png = grab_screen_png(int(cfg.get("screen.capture_max_side", 1280)))
    except Exception:  # noqa: BLE001
        digest.image_png = b""

    if with_vision and digest.image_png and cfg.get("vision.enabled"):
        from .vision import VisionError, describe_image

        try:
            digest.vision_text = describe_image(digest.image_png, cfg)
        except VisionError as exc:
            digest.skipped_reason = f"看图失败：{exc}"
        except Exception as exc:  # noqa: BLE001
            digest.skipped_reason = f"看图失败：{exc}"
    return digest


class ScreenWorker(QThread):
    """
    在后台线程里截图 + 调视觉模型（网络请求不能卡住界面）。
    信号：done(ScreenDigest) / failed(str)
    """
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, cfg, with_vision: bool = True, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.with_vision = with_vision

    def run(self) -> None:
        try:
            self.done.emit(build_digest(self.cfg, with_image=True, with_vision=self.with_vision))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


def png_to_data_url(png: bytes) -> str:
    """转成视觉接口能直接吃的 base64 data URL。"""
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")
