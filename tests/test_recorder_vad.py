# -*- coding: utf-8 -*-
"""
麦克风断句（VAD）的测试：直接喂合成的音频块，不需要真麦克风。

断句规则很简单，但错一点体验就很难受：
    * 只有噪音 → 一句都不能发；
    * 咳嗽一下（太短）→ 不发；
    * 正常说完一句 → 恰好发一次；
    * 一口气说太久 → 到上限强行切成一句。
"""
from __future__ import annotations

import struct
import sys
from array import array
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.voice.recorder import BLOCK, MicRecorder  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])


def _frame(value: float) -> bytes:
    """一块 100ms 的 float32 音频（BLOCK 个样点）。"""
    return array("f", [value] * BLOCK).tobytes()


def _make(**overrides) -> tuple[Config, MicRecorder, list]:
    cfg = Config()
    cfg.set("voice.call_silence_rms", 0.05)
    cfg.set("voice.call_silence_ms", 300)
    cfg.set("voice.call_min_utterance_ms", 200)
    cfg.set("voice.call_max_utterance_s", 30)
    for key, value in overrides.items():
        cfg.set(f"voice.{key}", value)
    rec = MicRecorder(cfg)
    got: list[tuple[bytes, float]] = []
    rec.utterance.connect(lambda data, dur: got.append((data, dur)))
    return cfg, rec, got


def test_silence_only_never_emits():
    _cfg, rec, got = _make()
    for _ in range(40):
        rec._feed(_frame(0.001))
    assert got == []


def test_noise_before_speech_is_ignored():
    _cfg, rec, got = _make()
    for _ in range(10):
        rec._feed(_frame(0.001))     # 先来 1 秒底噪
    for _ in range(5):
        rec._feed(_frame(0.3))       # 说 0.5 秒
    for _ in range(4):
        rec._feed(_frame(0.001))     # 安静 0.4 秒 → 说完
    assert len(got) == 1
    data, duration = got[0]
    # 说话 5 块 + 判定前的静音 3 块（第 3 块时刚好满 300ms）
    assert len(data) == 8 * BLOCK * 4
    assert 0.75 < duration < 0.85


def test_too_short_blip_is_dropped():
    _cfg, rec, got = _make()
    rec._feed(_frame(0.3))               # 只有 0.1 秒，低于 200ms 门槛
    for _ in range(5):
        rec._feed(_frame(0.001))
    assert got == []


def test_long_speech_is_force_cut():
    _cfg, rec, got = _make(call_max_utterance_s=1.0)
    for _ in range(15):                  # 1.5 秒一直说，超过 1 秒上限
        rec._feed(_frame(0.3))
    assert len(got) == 1
    assert 0.9 < got[0][1] < 1.2


def test_buffer_resets_between_utterances():
    _cfg, rec, got = _make()
    for _ in range(5):
        rec._feed(_frame(0.3))
    for _ in range(4):
        rec._feed(_frame(0.001))
    for _ in range(5):
        rec._feed(_frame(0.3))
    for _ in range(4):
        rec._feed(_frame(0.001))
    assert len(got) == 2
    assert abs(got[0][1] - got[1][1]) < 0.05     # 两次长度应当一样


def test_level_signal_reports_rms():
    _cfg, rec, _got = _make()
    levels: list[float] = []
    rec.level.connect(levels.append)
    rec._feed(_frame(0.25))
    assert levels and pytest.approx(levels[-1], rel=1e-6) == 0.25


def test_odd_byte_length_is_tolerated():
    _cfg, rec, got = _make()
    # 正常块之间夹一个被截断 2 字节的块，不能抛异常
    rec._feed(_frame(0.3))
    rec._feed(_frame(0.3))
    rec._feed(b"\x00\x00")
    for _ in range(4):
        rec._feed(_frame(0.001))
    assert len(got) == 1
    assert got[0][1] > 0.3
    _ = struct
