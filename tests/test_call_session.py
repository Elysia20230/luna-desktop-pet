# -*- coding: utf-8 -*-
"""
通话状态机的测试：录音、识别、对话、朗读全靠假的替身，
只验证"什么时候停录、什么时候交给她、什么时候重新开麦"这套流程。

真实麦克风与真实模型不参与，所以可以放心在 CI / 无设备环境跑。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.voice.call import (  # noqa: E402
    ST_IDLE, ST_LISTENING, ST_SPEAKING, ST_THINKING, CallSession,
)

_app = QApplication.instance() or QApplication([])


class _FakeRecorder(QObject):
    utterance = Signal(bytes, float)
    failed = Signal(str)
    silent = Signal(str)

    def __init__(self, available: bool = True, start_ok: bool = True) -> None:
        super().__init__()
        self._available = available
        self._start_ok = start_ok
        self.running = False
        self.starts = 0
        self.stops = 0
        self.blocked = False        # 隐私开关；真的接系统，这里默认不拦

    @property
    def available(self) -> bool:
        return self._available

    def start(self, device=None) -> bool:
        _ = device
        self.starts += 1
        self.running = self._start_ok
        return self._start_ok

    def stop(self) -> None:
        self.stops += 1
        self.running = False

    @property
    def stopped(self) -> int:
        """`stop()` 被调用了几次（有些用例想用这个说法）。"""
        return self.stops


class _FakeASR:
    ready = True
    last_error = ""

    def __init__(self, text: str = "现在几点了") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, samples, rate: int = 16000) -> str:
        _ = samples, rate
        self.calls += 1
        return self.text


class _FakeEngine(QObject):
    finished = Signal(str, str)
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.busy = False
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        self.sent.append(text)


class _FakeVoice(QObject):
    speech_done = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.stopped = 0

    def stop(self) -> None:
        self.stopped += 1


def _wait(ms: int = 120) -> None:
    end = time.time() + ms / 1000.0
    while time.time() < end:
        _app.processEvents()


def _make(recorder=None, asr=None, engine=None, voice=None):
    cfg = Config()
    cfg.set("voice.speech_wait_timeout", 3)
    engine = engine or _FakeEngine()
    voice = voice or _FakeVoice()
    rec = recorder or _FakeRecorder()
    session = CallSession(cfg, engine, voice, recorder=rec, asr=asr or _FakeASR())
    return cfg, session, rec, engine, voice


def test_start_puts_it_in_listening():
    _cfg, session, rec, _engine, _voice = _make()
    assert session.start() is True
    assert session.running is True
    assert session.state == ST_LISTENING
    assert rec.running is True


def test_unavailable_mic_refuses_to_start():
    cfg, session, rec, _engine, _voice = _make(recorder=_FakeRecorder(available=False))
    errors: list[str] = []
    session.failed.connect(errors.append)
    assert session.start() is False
    assert session.running is False
    assert errors and "录音设备" in errors[0]
    _ = cfg, rec


def test_utterance_is_transcribed_and_sent():
    _cfg, session, _rec, engine, _voice = _make()
    session.start()
    session.recorder.utterance.emit(b"\x00" * 6400, 0.5)
    _wait(200)
    assert engine.sent == ["现在几点了"]
    assert session.state == ST_THINKING


def test_empty_transcript_goes_back_to_listening():
    _cfg, session, rec, engine, _voice = _make(asr=_FakeASR(text=""))
    session.start()
    session.recorder.utterance.emit(b"\x00" * 6400, 0.5)
    _wait(200)
    assert engine.sent == []
    assert session.state == ST_LISTENING
    assert rec.running is True


def test_utterance_ignored_while_she_is_busy():
    _cfg, session, _rec, engine, _voice = _make()
    session.start()
    engine.busy = True
    errors: list[str] = []
    session.failed.connect(errors.append)
    session.recorder.utterance.emit(b"\x00" * 6400, 0.5)
    _wait(80)
    assert engine.sent == []
    assert errors and "还在说" in errors[0]


def test_reply_finished_then_speech_done_listens_again():
    _cfg, session, rec, engine, voice = _make()
    seen: list[str] = []
    session.state_changed.connect(seen.append)
    session.start()
    engine.finished.emit("嗯。", "平静")
    assert session.state == ST_SPEAKING
    before = rec.starts
    voice.speech_done.emit()
    assert rec.starts == before + 1
    assert session.state == ST_LISTENING
    assert ST_SPEAKING in seen


def test_stop_resets_state_and_stops_recording():
    _cfg, session, rec, _engine, voice = _make()
    session.start()
    session.stop()
    assert session.running is False
    assert session.state == ST_IDLE
    assert rec.stopped >= 1
    assert voice.stopped >= 1


def test_reply_failed_keeps_listening():
    _cfg, session, rec, engine, _voice = _make()
    session.start()
    seen: list[str] = []
    session.failed.connect(seen.append)
    engine.failed.emit("网络不好")
    assert session.state == ST_LISTENING
    assert rec.running is True
    assert seen and "这轮没答上" in seen[0]


def test_toggle_flips_running():
    _cfg, session, _rec, _engine, _voice = _make()
    session.toggle()
    assert session.running is True
    session.toggle()
    assert session.running is False
    _ = pytest
