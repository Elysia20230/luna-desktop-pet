# -*- coding: utf-8 -*-
"""
实时语音通话：麦克风 → 识别 → 她回答 → 说完再听。

一次通话是这样转的（半双工，轮流说话）：
    空闲 → [开] → 在听 →(你说完一句)→ 识别中 → 思考中 → 说话中 →(日语念完)→ 在听 …

为什么不做"打断"：要打断就得一边播一边录，还得做回声消除，
否则她会把自己的声音当成你在说话。轮流说最稳，也最适合桌宠。
"""
from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QObject, QTimer, Signal

from ..config import ROOT
from . import mics
from .asr import build_asr, transcribe
from .recorder import MicRecorder

# 状态文字（设置面板 / 托盘 tooltip 直接显示这些中文）
ST_IDLE = "空闲"
ST_LISTENING = "在听"
ST_THINKING = "思考中"
ST_SPEAKING = "说话中"
ST_RECOGNIZING = "识别中"

_logger: logging.Logger | None = None


def _log() -> logging.Logger:
    """
    通话专用日志（logs/voice_call.log）。

    为什么单独一个文件：这类"没反应"的问题，光看界面看不出是
    「没打开麦克风」「打开了但没声」「识别成空」「她没回」中的哪一步。
    """
    global _logger
    if _logger is not None:
        return _logger
    logger = logging.getLogger("luna.voice.call")
    if not logger.handlers:
        try:
            (ROOT / "logs").mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(ROOT / "logs" / "voice_call.log",
                                          maxBytes=512 * 1024, backupCount=1,
                                          encoding="utf-8")
            handler.setFormatter(logging.Formatter(
                "%(asctime)s | %(message)s", "%Y-%m-%d %H:%M:%S"))
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        except Exception:  # noqa: BLE001 日志写不了也不能影响通话
            pass
    _logger = logger
    return logger


class CallSession(QObject):
    """把录音、识别、对话、朗读串成一个循环。"""

    state_changed = Signal(str)   # 状态文字
    heard = Signal(str)           # 听清了什么（界面上可以短暂提示）
    failed = Signal(str)          # 人话错误提示

    def __init__(self, cfg, engine, voice, parent: QObject | None = None, *,
                 recorder=None, asr=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.engine = engine
        self.voice = voice
        self.recorder = recorder or MicRecorder(cfg, self)
        self._asr = asr              # 测试可注入；None = 按配置现建
        self._running = False
        self._busy_recognizing = False
        self._state = ST_IDLE
        self._wait_timer = QTimer(self)
        self._wait_timer.setSingleShot(True)
        self._wait_timer.timeout.connect(self._after_speaking)

        self.recorder.utterance.connect(self._on_utterance)
        self.recorder.failed.connect(self._on_recorder_failed)
        self.recorder.silent.connect(self._on_recorder_silent)
        # 她念完了 → 重新开麦（语音关掉时不会有这个信号，靠兜底计时器）
        if voice is not None and hasattr(voice, "speech_done"):
            voice.speech_done.connect(self._on_speech_done)
        if engine is not None:
            engine.finished.connect(self._on_reply_finished)
            engine.failed.connect(self._on_reply_failed)

    # ---- 对外 -------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def state(self) -> str:
        return self._state

    def toggle(self) -> None:
        self.stop() if self._running else self.start()

    def start(self) -> bool:
        if self._running:
            return True
        if not self.recorder.available:
            self.failed.emit("没有可用的录音设备（先 pip install sounddevice）")
            return False
        # 先把"为什么可能听不见"说清楚，再照常启动 ——
        # 用户可能刚把开关打开，程序这边还读不到新状态，所以不在这里硬拦。
        if self.recorder.blocked:
            _log().info("隐私开关 NonPackaged=Deny，麦克风很可能录不到声音")
            self.failed.emit(mics.blocked_hint())
        self._running = True
        self._set_state(ST_LISTENING)
        self._prepare_asr()
        if not self.recorder.start():
            self._running = False
            self._set_state(ST_IDLE)
            return False
        _log().info("通话开始；设备=%s 采样率=%s 声道=%s",
                    self.cfg.get("voice.call_device_last", "(默认)"),
                    getattr(self.recorder, "_rate", "?"),
                    getattr(self.recorder, "_channels", "?"))
        return True

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._wait_timer.stop()
        self.recorder.stop()
        try:
            if self.voice is not None:
                self.voice.stop()
        except Exception:  # noqa: BLE001
            pass
        _log().info("通话结束")
        self._set_state(ST_IDLE)

    # ---- 内部：录音 → 识别 ------------------------------------------------
    def _prepare_asr(self) -> None:
        """第一次用本地模型时后台加载（可能要下载），不阻塞界面。"""
        if self._asr is None:
            self._asr = build_asr(self.cfg)
        asr = self._asr
        if asr is None:
            self.failed.emit("没有可用的识别后端，请到设置里检查")
            return
        if hasattr(asr, "ready") and not asr.ready:
            self._set_state(ST_RECOGNIZING)
            self.failed.emit("正在准备识别模型（首次约 1–2 分钟，之后秒开）")

            def _warm() -> None:
                ok = asr.load()
                if self._running:
                    self._set_state(ST_LISTENING if ok else ST_IDLE)
                if not ok:
                    self.failed.emit(f"识别模型没准备好：{asr.last_error}")

            threading.Thread(target=_warm, daemon=True).start()

    def _on_recorder_failed(self, message: str) -> None:
        _log().warning("录音出错：%s", message)
        self.failed.emit(message)
        if self._running and not self.recorder.running:
            self.stop()

    def _on_recorder_silent(self, message: str) -> None:
        """开麦后一直没声音：把原因告诉用户，但别自动关掉（他可能正在调设备）。"""
        _log().warning("麦克风静音：%s", message)
        self.failed.emit(message)

    def _on_utterance(self, samples: bytes, duration: float) -> None:
        """听到一整句：立刻停录（否则会把她的声音也录进来），后台识别。"""
        if not self._running or self._busy_recognizing:
            return
        if self.engine is not None and self.engine.busy:
            self.failed.emit("她还在说，这句先略过")
            return
        self._busy_recognizing = True
        self.recorder.stop()
        self._set_state(ST_RECOGNIZING)

        def _work() -> None:
            try:
                text = transcribe(samples, self.cfg, self._asr)
            finally:
                self._busy_recognizing = False
            if not self._running:
                return
            text = (text or "").strip()
            if not text:
                _log().info("识别结果为空（%.2f 秒音频），回监听", duration)
                self._set_state(ST_LISTENING)
                self.recorder.start()
                return
            _log().info("听清（%.2f 秒）：%s", duration, text)
            self.heard.emit(text)
            self._set_state(ST_THINKING)
            if self.engine is not None:
                self.engine.send(text)

        threading.Thread(target=_work, daemon=True).start()

    # ---- 内部：她说完了，重新听 ------------------------------------------
    def _on_reply_finished(self, _text: str = "", _emotion: str = "") -> None:
        if not self._running:
            return
        self._set_state(ST_SPEAKING)
        # 语音关了 / 合成失败时不会有 speech_done，这里必须兜底
        timeout = float(self.cfg.get("voice.speech_wait_timeout", 20) or 20)
        self._wait_timer.start(int(max(3.0, timeout) * 1000))

    def _on_speech_done(self) -> None:
        if not self._running:
            return
        self._after_speaking()

    def _after_speaking(self) -> None:
        """一轮结束：回到"在听"。"""
        if not self._running:
            return
        self._wait_timer.stop()
        if self.recorder.start():
            self._set_state(ST_LISTENING)
        else:
            self.stop()

    def _on_reply_failed(self, message: str) -> None:
        if not self._running:
            return
        self.failed.emit(f"这轮没答上：{message}")
        self._after_speaking()

    # ---- 状态广播 ---------------------------------------------------------
    def _set_state(self, text: str) -> None:
        if text == self._state:
            return
        self._state = text
        self.state_changed.emit(text)
