# -*- coding: utf-8 -*-
"""
麦克风的"体检"与"清单"。

为什么单独一层：
    1. Windows 有一项系统级开关（设置 → 隐私和安全性 → 麦克风 → 允许桌面应用访问麦克风）。
       它关掉时，pythonw / ffmpeg 这类"非应用商店"程序一律录不到声音，但 Windows 自己的
       麦克风测试（商店应用）却是好的 —— 表现出来就是"系统麦克风正常，她却听不见"。
    2. sounddevice 会把同一支麦克风在 MME / DirectSound / WASAPI / WDM-KS 下各列一遍，
       再加上"立体声混音""扬声器""声音映射器"这些根本不是麦克风的条目，
       设置里的下拉就会变成十几个看不懂的选项。

这里只做两件事：读那个隐私开关的状态、把设备清单收拾成"人能看懂的三五项"。
"""
from __future__ import annotations

from dataclasses import dataclass

# 隐私开关存放在这个注册表键下：
#   ...\ConsentStore\microphone            的 Value 管"应用商店"的麦克风权限
#   ...\ConsentStore\microphone\NonPackaged 的 Value 管"经典桌面程序"（python.exe 就在这一类）
# 两处都是 Allow / Deny 字符串 —— 只看父键会得出"能用"的错误结论（踩过）。
_CONSENT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion"
    r"\CapabilityAccessManager\ConsentStore\microphone"
)
_NON_PACKAGED = "NonPackaged"

# 这些名字明显不是"对着说话的麦克风"，列表里直接扔掉
_JUNK_HINTS = (
    "声音映射器", "sound mapper", "主声音捕获", "primary sound capture",
    "立体声混音", "stereo mix", "what u hear", "loopback", "内录",
    "扬声器", "speaker", "wave out", "output", "aux",
)

# 同一支麦克风在不同接口下的优先级：WDM-KS 在本机最靠得住，MME 往往直接打不开
_HOST_ORDER = {
    "Windows WDM-KS": 0,
    "Windows WASAPI": 1,
    "Windows DirectSound": 2,
    "MME": 3,
}


@dataclass
class InputDevice:
    """下拉里的一行。index 交给 sounddevice，name 给人看。"""

    index: int
    name: str
    host: str = ""
    note: str = ""

    @property
    def label(self) -> str:
        text = self.name
        if self.host:
            text = f"{text}　[{self.host}]"
        if self.note:
            text = f"{text} {self.note}"
        return text


# ---------------------------------------------------------------- 隐私开关
def desktop_access() -> str:
    """
    桌面程序能不能用麦克风。

    注意要读的是 **NonPackaged 子键** 里的 Value：父键只管应用商店里的程序，
    就算父键是 Allow、NonPackaged 是 Deny，pythonw 依然一个字都录不到
    （这就是"系统麦克风正常，她却听不见"的根因）。

    返回 "allow" / "deny" / "unknown"（读不到就 unknown，绝不抛异常 ——
    这里是诊断用的，不能因为读注册表失败就把通话搞崩）。
    """
    try:
        import winreg  # 只有 Windows 有；非 Windows 会 ImportError
    except Exception:  # noqa: BLE001
        return "unknown"
    for sub in (rf"{_CONSENT_KEY}\{_NON_PACKAGED}", _CONSENT_KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub) as key:
                value, _kind = winreg.QueryValueEx(key, "Value")
        except FileNotFoundError:
            continue
        except OSError:
            continue
        text = str(value or "").strip().lower()
        if text in ("allow", "1"):
            return "allow"
        if text in ("deny", "0"):
            return "deny"
        return "unknown"
    return "unknown"


def open_privacy_settings() -> bool:
    """
    打开 Windows 的麦克风隐私页面（只是跳转，不改任何系统设置）。

    改隐私设置要用户自己点头；我们能做的是把他直接送到那一页。
    """
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return bool(QDesktopServices.openUrl(QUrl("ms-settings:privacy-microphone")))
    except Exception:  # noqa: BLE001
        return False


def blocked_hint() -> str:
    """给用户看的一句话：去哪开、开哪一项。"""
    return (
        "Windows 正在禁止桌面应用使用麦克风。"
        "请打开：设置 → 隐私和安全性 → 麦克风 → 把「允许桌面应用访问你的麦克风」打开，"
        "然后重新点一次语音通话。"
    )


# ---------------------------------------------------------------- 设备清单
def _sd():
    """
    拿到 sounddevice 模块（没装就 None）。

    单独抽成一个函数是为了测试：测试里可以把它替换成假模块，
    不必真的去碰声卡。
    """
    try:
        import sounddevice

        return sounddevice
    except Exception:  # noqa: BLE001
        return None


def _is_junk(name: str) -> bool:
    low = (name or "").lower()
    if not low.strip():
        return True
    if any(hint in low for hint in _JUNK_HINTS):
        return True
    # 「麦克风阵列 1 ()」这类空括号条目是驱动重复枚举出来的影子设备
    return low.rstrip().endswith("()")


def _normalize(name: str) -> str:
    """把「⋯ Wave」「⋯　」这类写法抹平，用于跨接口去重。"""
    import re

    text = (name or "").strip()
    if text.lower().endswith(" wave)"):
        text = text[:-6] + ")"
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\s+", " ", text)
    # 有些驱动把名字截断了（"麦克风 (Steam Streaming Microphone"），补个右括号才好看
    if text.count("(") > text.count(")"):
        text += ")" * (text.count("(") - text.count(")"))
    return text


def _host_of(apis, info) -> str:
    try:
        idx = int(info.get("hostapi", 0))
        return str(apis[idx].get("name", ""))
    except Exception:  # noqa: BLE001
        return ""


def list_inputs(prefer_openable: bool = False, timeout_ms: int = 250) -> list[InputDevice]:
    """
    收拾好的输入设备清单：同一支麦克风只留一条，垃圾条目去掉。

    prefer_openable=True 时会真去开一下（每个 0.25 秒），打不开的标「（打不开）」。
    通话正在进行时别用 True —— 设备被自己占着，会全部显示成打不开。
    """
    sd = _sd()
    if sd is None:
        return []
    try:
        raw = sd.query_devices()
    except Exception:  # noqa: BLE001 设备被独占时整张表都可能拿不到
        return []
    try:
        apis = sd.query_hostapis()
    except Exception:  # noqa: BLE001 拿不到接口名就退化成"没有接口信息"
        apis = []

    candidates: dict[str, tuple[int, int, InputDevice]] = {}
    for idx, info in enumerate(raw):
        try:
            if int(info.get("max_input_channels", 0) or 0) <= 0:
                continue
        except Exception:  # noqa: BLE001
            continue
        name = str(info.get("name", "") or "").strip()
        if _is_junk(name):
            continue
        host = _host_of(apis, info)
        key = _normalize(name)
        rank = _HOST_ORDER.get(host, 9)
        if key in candidates and candidates[key][0] <= rank:
            continue
        candidates[key] = (rank, idx, InputDevice(index=idx, name=_normalize(name), host=host))

    devices = [item[2] for item in candidates.values()]
    devices.sort(key=lambda d: (_HOST_ORDER.get(d.host, 9), d.index))

    # 兜底：清理规则万一把所有条目都滤掉了（比如全是没名字的虚拟设备），
    # 也别让下拉空着 —— 至少把"有输入通道的设备"原样列出来。
    if not devices:
        for idx, info in enumerate(raw):
            try:
                if int(info.get("max_input_channels", 0) or 0) <= 0:
                    continue
            except Exception:  # noqa: BLE001
                continue
            name = str(info.get("name", "") or f"设备 {idx}").strip()
            devices.append(InputDevice(index=idx, name=name, host=_host_of(apis, info)))

    if prefer_openable:
        for dev in devices:
            if not _opens(sd, dev.index, timeout_ms):
                dev.note = "（打不开）"
    return devices


def _opens(sd, index: int, timeout_ms: int = 250) -> bool:
    """
    快速试开：只关心"能不能开"，不关心有没有声音。

    必须用**回调模式**并且逐个试采样率/声道：本机 WDM-KS 在阻塞模式下会直接报
    `Blocking API not supported`，固定按默认格式试也会被 `Invalid sample rate` 挡掉，
    于是好设备全被打成"打不开"（踩过这个坑）。
    timeout_ms 目前用不到，保留只是为了让调用方不用改签名。
    """
    _ = timeout_ms
    try:
        info = sd.query_devices(index, "input")
    except Exception:  # noqa: BLE001
        return False
    max_channels = max(1, int(info.get("max_input_channels", 1) or 1))
    default_rate = int(info.get("default_samplerate", 48000) or 48000)
    formats: list[tuple[int, int]] = []
    for rate, channels in ((default_rate, min(2, max_channels)),
                           (default_rate, max_channels),
                           (48000, min(2, max_channels)),
                           (16000, 1),
                           (16000, min(2, max_channels))):
        if rate > 0 and (rate, channels) not in formats:
            formats.append((rate, channels))

    for rate, channels in formats:
        stream = None
        try:
            stream = sd.InputStream(device=index, samplerate=rate, channels=channels,
                                    dtype="float32", blocksize=int(rate * 0.1),
                                    callback=lambda *a: None)
            stream.start()
            stream.stop()
            stream.close()
            return True
        except Exception:  # noqa: BLE001 这一组格式不行，换下一组
            if stream is not None:
                try:
                    stream.close()
                except Exception:  # noqa: BLE001
                    pass
    return False


def current_default_index() -> int | None:
    """系统默认输入设备的编号（拿不到就 None）。"""
    sd = _sd()
    if sd is None:
        return None
    try:
        return int(sd.default.device[0])
    except Exception:  # noqa: BLE001
        return None


def probe_input(index: int, timeout_ms: int = 250) -> bool:
    """试着打开某个输入设备（用给定的超时时间），只回答"能不能开"。"""
    sd = _sd()
    if sd is None:
        return False
    return _opens(sd, index, timeout_ms)
