# -*- coding: utf-8 -*-
"""
麦克风枚举与"隐私开关"判定的测试。

这台机器上踩过的坑：
    1. Windows 的「允许桌面应用访问麦克风」被关掉时，任何录音库都打不开设备；
    2. 同一支麦克风在 MME / DirectSound / WASAPI / WDM-KS 下各有一个编号，
       直接列 sd.query_devices() 会出现四五条重名项，看着就晕；
    3. 还有「立体声混音」「扬声器」「声音映射器」这种根本不是麦克风的条目。

这里全部用假数据，不碰真声卡。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.voice import mics  # noqa: E402


class _FakeDevice(dict):
    """模仿 sounddevice 的 device 字典。"""


def _dev(name: str, hostapi: int, ins: int = 2, outs: int = 0, rate: float = 48000):
    return _FakeDevice(
        name=name,
        hostapi=hostapi,
        max_input_channels=ins,
        max_output_channels=outs,
        default_samplerate=rate,
    )


class _FakeSD:
    def __init__(self, devices, hostapis, default=(0, -1)):
        self._devices = devices
        self._hostapis = [{"name": n} for n in hostapis]
        self.default = type("D", (), {"device": default})()

    def query_devices(self, index=None, kind=None):
        if index is None:
            return list(self._devices)
        info = self._devices[index]
        if kind == "input" and int(info["max_input_channels"]) <= 0:
            raise ValueError("不是输入设备")
        if kind == "output" and int(info["max_output_channels"]) <= 0:
            raise ValueError("不是输出设备")
        return info

    def query_hostapis(self, index=None):
        return self._hostapis if index is None else self._hostapis[index]


_HOSTS = ["MME", "Windows DirectSound", "Windows WASAPI", "Windows WDM-KS"]


def _sample_sd() -> _FakeSD:
    return _FakeSD(
        devices=[
            _dev("麦克风 (Realtek(R) Audio)", hostapi=0),            # 0 MME
            _dev("耳机 ()", hostapi=0, ins=0, outs=2),               # 1 输出设备
            _dev("立体声混音 (Realtek(R) Audio)", hostapi=3),        # 2 回环
            _dev("麦克风阵列 (英特尔® 智音技术)", hostapi=3),         # 3 真麦
            _dev("麦克风 (Steam Streaming Microphone)", hostapi=3),  # 4
            _dev("麦克风 (Realtek(R) Audio)", hostapi=3),            # 5 与 0 同名
            _dev("声音映射器", hostapi=0),                            # 6 系统占位
        ],
        hostapis=_HOSTS,
    )


def test_is_junk_filters_loopback_and_outputs():
    assert mics._is_junk("立体声混音 (Realtek(R) Audio)")
    assert mics._is_junk("耳机 ()")
    assert mics._is_junk("声音映射器")
    assert not mics._is_junk("麦克风阵列 (英特尔® 智音技术)")


def test_normalize_merges_host_suffix_variants():
    assert mics._normalize("麦克风 (Realtek(R) Audio)") == \
        mics._normalize("麦克风  (Realtek(R) Audio) ")


def test_list_inputs_dedupes_and_prefers_wdm_ks(monkeypatch):
    sd = _sample_sd()
    monkeypatch.setattr(mics, "_sd", lambda: sd)
    monkeypatch.setattr(mics, "_opens", lambda *_a, **_k: True)
    out = mics.list_inputs(prefer_openable=False)
    names = [d.name for d in out]
    # 同名设备只留一条，且留的是 WDM-KS 那条（编号 5）
    realtek = [d for d in out if "Realtek" in d.name]
    assert len(realtek) == 1
    assert realtek[0].index == 5
    assert realtek[0].host == "Windows WDM-KS"
    # 回环 / 输出 / 声音映射器都不该出现
    assert not any("立体声混音" in n for n in names)
    assert not any("耳机" in n for n in names)
    assert not any("声音映射器" in n for n in names)


def test_list_inputs_marks_unopenable(monkeypatch):
    sd = _sample_sd()

    def _opens(_sd, index, _timeout_ms=250):
        return int(index) == 5          # 只有 WDM-KS 的 Realtek 能开

    monkeypatch.setattr(mics, "_sd", lambda: sd)
    monkeypatch.setattr(mics, "_opens", _opens)
    out = mics.list_inputs(prefer_openable=True)
    by_index = {d.index: d for d in out}
    assert by_index[5].note == ""                    # 能开的：不加标注
    assert "打不开" in by_index[3].note               # 开不了的：明确写出来


def test_desktop_access_reads_nonpackaged_subkey(monkeypatch):
    """
    必须读 NonPackaged 子键，而不是父键。

    父键（应用商店的权限）在这台机器上一直是 Allow，真正挡住 pythonw 的是
    NonPackaged 子键里的 Deny —— 读错了就会误判成"能用"，然后用户对着麦克风
    说半天她一点反应都没有。
    """
    opened: list[str] = []

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import winreg

    def _open(_root, path):
        opened.append(path)
        if "NonPackaged" in path:
            return _Key()
        raise FileNotFoundError("父键测试里故意不提供")

    monkeypatch.setattr(winreg, "OpenKey", _open)
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a, **k: ("Deny", 1))
    assert mics.desktop_access() == "deny"
    assert opened and "NonPackaged" in opened[0]


def test_desktop_access_reads_consent_store(monkeypatch):
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import winreg

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a, **k: ("Deny", 1))
    assert mics.desktop_access() == "deny"
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a, **k: ("Allow", 1))
    assert mics.desktop_access() == "allow"
    monkeypatch.setattr(winreg, "QueryValueEx", lambda *a, **k: ("", 1))
    assert mics.desktop_access() == "unknown"


def test_desktop_access_survives_missing_key(monkeypatch):
    import winreg

    def _boom(*a, **k):
        raise FileNotFoundError("没有这个键")

    monkeypatch.setattr(winreg, "OpenKey", _boom)
    assert mics.desktop_access() == "unknown"


def test_blocked_hint_mentions_settings_path():
    hint = mics.blocked_hint()
    assert "麦克风" in hint
    assert "隐私" in hint or "设置" in hint


def test_opens_tries_callback_mode_and_formats():
    """
    试开必须用回调模式，并且在"默认采样率不行"时换一组再试。

    本机 WDM-KS 阻塞模式直接报 Blocking API not supported，
    固定格式会被 Invalid sample rate 挡掉 —— 这正是好设备被误标"打不开"的原因。
    """

    class _Stream:
        def __init__(self, device, samplerate, channels, dtype, blocksize, callback):
            self.rate = samplerate
            self.channels = channels
            self.callback = callback

        def start(self):
            if self.rate == 48000 and self.channels == 2:
                raise ValueError("Invalid sample rate")
            assert callable(self.callback)

        def stop(self):
            pass

        def close(self):
            pass

    class _SD:
        def query_devices(self, index, kind=None):
            return {"max_input_channels": 2, "default_samplerate": 48000}

        def InputStream(self, **kw):
            return _Stream(**kw)

    assert mics._opens(_SD(), 0) is True
