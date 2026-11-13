# -*- coding: utf-8 -*-
"""
「心情大好～」录音资源测试。

钉住两件事：
    1. 项目里确实带着一份可播放的 wav（16-bit PCM 单声道，时长合理）；
    2. 配置能覆盖路径与文案（用户想换成自己的录音时不用改代码）。

跑法：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.voice.signature import (  # noqa: E402
    DEFAULT_EMOTION,
    DEFAULT_TEXT,
    DEFAULT_WAV,
    SOURCE_OGG,
    signature_emotion,
    signature_path,
    signature_text,
)


def test_default_wav_exists():
    assert DEFAULT_WAV.is_file(), f"缺少内置录音：{DEFAULT_WAV}"
    assert DEFAULT_WAV.stat().st_size > 100_000, "wav 太小了，可能没转码成功"


def test_default_wav_is_playable_pcm():
    with wave.open(str(DEFAULT_WAV), "rb") as fp:
        assert fp.getnchannels() == 1, "winsound 播放要求单声道"
        assert fp.getsampwidth() == 2, "必须是 16-bit PCM"
        rate = fp.getframerate()
        seconds = fp.getnframes() / float(rate)
    assert rate >= 8000
    # 原录音 2.87 秒：给足容差，但别让"转错了文件"混过去
    assert 2.0 <= seconds <= 4.5, f"时长异常：{seconds:.2f} 秒"


def test_source_ogg_is_kept():
    """原始 ogg 留档，方便换设备后重新转码。"""
    assert SOURCE_OGG.is_file(), f"缺少原始录音：{SOURCE_OGG}"


def test_signature_defaults():
    assert signature_text(None) == DEFAULT_TEXT == "心情大好～"
    assert signature_emotion() == DEFAULT_EMOTION == "得意"
    assert signature_path(None) == DEFAULT_WAV


class _Cfg:
    """极小配置桩：只要 get 就够了。"""

    def __init__(self, values: dict) -> None:
        self._values = values

    def get(self, key, default=None):
        return self._values.get(key, default)


def test_signature_path_can_be_overridden(tmp_path):
    custom = tmp_path / "my_line.wav"
    custom.write_bytes(b"RIFF")
    cfg = _Cfg({"voice.signature_file": str(custom)})
    assert signature_path(cfg) == custom


def test_signature_text_can_be_overridden():
    cfg = _Cfg({"voice.signature_text": "今天心情非常好。"})
    assert signature_text(cfg) == "今天心情非常好。"


def test_blank_overrides_fall_back_to_default():
    cfg = _Cfg({"voice.signature_file": "   ", "voice.signature_text": ""})
    assert signature_path(cfg) == DEFAULT_WAV
    assert signature_text(cfg) == DEFAULT_TEXT
