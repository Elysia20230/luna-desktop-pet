# -*- coding: utf-8 -*-
"""
播放 wav 文件。

为什么不用 QtMultimedia：本项目装的是 PySide6-Essentials，里面没有 Multimedia 模块
（`from PySide6 import QtMultimedia` 会 ImportError）。为了不为了一段语音再拉一个
上百 MB 的 PySide6-Addons，这里直接用 Windows 自带的 winsound——够用、零依赖。

音量：winsound 自己没有音量参数，所以调音量时把 PCM 采样乘一个系数另存一份 wav。
有 numpy 就用 numpy 算（快），没有就用标准库 array 慢慢乘（也能用）。
"""
from __future__ import annotations

import hashlib
import wave
from pathlib import Path

from ..config import DATA_DIR

try:  # winsound 只在 Windows 上有
    import winsound
except Exception:  # noqa: BLE001
    winsound = None  # type: ignore[assignment]

PLAY_DIR = DATA_DIR / "voice" / "play"


def wav_duration(path: Path | str) -> float:
    """读 wav 头算时长（秒），读不了就返回 0。"""
    try:
        with wave.open(str(path), "rb") as fp:
            rate = fp.getframerate() or 1
            return fp.getnframes() / float(rate)
    except Exception:  # noqa: BLE001
        return 0.0


def _scaled_copy(path: Path, volume: float) -> Path:
    """把音量乘进 PCM，另存一份（同样内容同样音量只做一次）。"""
    PLAY_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(
        f"{path}|{path.stat().st_mtime_ns}|{volume:.2f}".encode("utf-8")
    ).hexdigest()[:16]
    out = PLAY_DIR / f"v{volume:.2f}_{digest}.wav"
    if out.is_file():
        return out

    with wave.open(str(path), "rb") as src:
        params = src.getparams()
        frames = src.readframes(src.getnframes())

    try:  # 有 numpy 就用 numpy，快得多
        import numpy as np

        samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) * float(volume)
        np.clip(samples, -32768, 32767, out=samples)
        payload = samples.astype(np.int16).tobytes()
    except Exception:  # noqa: BLE001 没 numpy 就走下面这条
        from array import array

        arr = array("h")
        arr.frombytes(frames)
        for i, value in enumerate(arr):
            scaled = int(value * volume)
            arr[i] = 32767 if scaled > 32767 else (-32768 if scaled < -32768 else scaled)
        payload = arr.tobytes()

    with wave.open(str(out), "wb") as dst:
        dst.setparams(params)
        dst.writeframes(payload)
    return out


class WavPlayer:
    """异步播放 wav；同时只播一条，新的一条会把上一条掐掉。"""

    def __init__(self) -> None:
        self._current: Path | None = None

    @property
    def supported(self) -> bool:
        return winsound is not None

    def play(self, path: Path | str, volume: float = 1.0) -> float:
        """开始播放，返回这段音频的时长（秒）。失败会抛异常。"""
        if winsound is None:
            raise RuntimeError("当前系统没有 winsound，无法播放语音")
        src = Path(path)
        if not src.is_file():
            raise RuntimeError(f"找不到音频文件：{src}")

        level = max(0.0, min(1.0, float(volume)))
        target = _scaled_copy(src, level) if abs(level - 1.0) > 0.01 else src
        self.stop()
        winsound.PlaySound(
            str(target), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
        )
        self._current = target
        return wav_duration(src)

    def stop(self) -> None:
        if winsound is not None:
            try:
                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:  # noqa: BLE001
                pass
        self._current = None
