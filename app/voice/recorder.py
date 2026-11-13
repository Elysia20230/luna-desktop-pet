# -*- coding: utf-8 -*-
"""
麦克风录音 + 简易断句（VAD）。

为什么自己断句：语音通话是"你说完一句，她回一句"的轮流制。
如果一直录到底再识别，她会等你半天；如果一直不停，会把她的声音也录进去。
所以这里用最朴素的办法：音量超过阈值算"在说话"，安静够久就算"这句说完"。

依赖 sounddevice（PortAudio）。没装或者没有麦克风时 available=False，
界面据此把通话开关弹回去并给出提示，而不是抛异常。
"""
from __future__ import annotations

import math
import time
from array import array

from PySide6.QtCore import QObject, QTimer, Signal

from . import mics

SAMPLE_RATE = 16000      # 识别用的采样率（模型按这个尺寸训练）
CHANNELS = 1
BLOCK = 1600             # 100 ms 一块（16 kHz × 0.1 s）
BYTES_PER_SAMPLE = 4     # float32

# 开了麦克风之后，多少秒内一点声都没有就提示"可能没声音"
SILENT_WARN_MS = 6000
# 低于这个音量就当作"完全没声"（正常房间底噪也在这个数量级之上）
SILENT_FLOOR = 0.001

# 真实设备千奇百怪：同一台机器上 MME 可能完全打不开，只有 WDM-KS 能用；
# WASAPI 又常常要求"声道数跟它报告的一模一样"。所以这里不写死一套参数，
# 而是按"先上次成功过的 → WDM-KS → WASAPI → DirectSound → MME → 系统默认"顺序挨个试。
_HOST_API_ORDER = {
    "Windows WDM-KS": 0,
    "Windows WASAPI": 1,
    "Windows DirectSound": 2,
    "MME": 3,
}

# 这些名字是"把扬声器当输入"的回环/混音设备：能开，但录到的是电脑自己的声音，
# 通话里会把她自己念的话再听一遍，形成自问自答。默认一律跳过。
_LOOPBACK_HINTS = (
    "立体声混音", "stereo mix", "what u hear", "loopback", "内录",
    "扬声器", "speaker", "output", "wave out", "立体声",
)
_MIC_HINTS = ("麦克风", "microphone", "microph", "mic", "input", "line in", "阵列")


class MicRecorder(QObject):
    """持续录音 → 攒够一句话就发 utterance。"""

    utterance = Signal(bytes, float)   # (16k 单声道 float32 样点, 时长秒)
    failed = Signal(str)               # 人话错误提示
    level = Signal(float)              # 当前音量（给输入框做跳动提示，可选）
    silent = Signal(str)               # 开了麦却一直没声（多半是选错设备或被静音）

    def __init__(self, cfg, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self._stream = None
        self._rate = SAMPLE_RATE      # 当前流的采样率（可能不是 16k）
        self._channels = CHANNELS
        self._speaking = False
        self._frames = bytearray()
        self._voiced_bytes = 0
        self._silent_bytes = 0
        self._started_at = 0.0
        self._heard_anything = False

        # 静音看门狗：开麦 6 秒还一点声都没有，就提醒一次（每次 start 最多一次）
        self._watchdog = QTimer(self)
        self._watchdog.setSingleShot(True)
        self._watchdog.timeout.connect(self._on_watchdog)
        self._warned_silent = False

    # ---- 能力探测 ---------------------------------------------------------
    @property
    def available(self) -> bool:
        try:
            import sounddevice  # noqa: F401
        except Exception:  # noqa: BLE001
            return False
        return True

    @property
    def blocked(self) -> bool:
        """Windows 的隐私开关是不是把桌面程序挡在外面了。"""
        return mics.desktop_access() == "deny"

    def devices(self) -> list[tuple[int, str]]:
        """
        [(编号, 名字)]，给设置里的麦克风下拉用。

        这里已经做过"去重 + 扔掉扬声器/立体声混音"，所以同一支麦克风只出现一次。
        """
        return [(dev.index, dev.name) for dev in mics.list_inputs()]

    # ---- 开关 -------------------------------------------------------------
    @staticmethod
    def _is_loopback(name: str) -> bool:
        low = (name or "").lower()
        return any(hint in low for hint in _LOOPBACK_HINTS)

    @staticmethod
    def _is_mic_like(name: str) -> bool:
        low = (name or "").lower()
        return any(hint in low for hint in _MIC_HINTS)

    def _candidate_devices(self, sd) -> list:
        """按"最可能成功"的顺序排出候选设备。去重、跳过回环设备。"""
        order: list = []

        def _add(value, name: str = "") -> None:
            if value in ("", None) or value in order:
                return
            if name and self._is_loopback(name):
                return          # 别把扬声器当麦克风
            order.append(int(value) if isinstance(value, str) and value.isdigit() else value)

        cfg_dev = self.cfg.get("voice.call_device", "")
        if cfg_dev not in ("", None):
            _add(cfg_dev)       # 用户手动指定的，无条件尊重

        try:
            apis = sd.query_hostapis()
        except Exception:  # noqa: BLE001
            apis = []

        ranked = []
        for idx, info in enumerate(sd.query_devices()):
            if int(info.get("max_input_channels", 0) or 0) <= 0:
                continue
            name = str(info.get("name", ""))
            api_name = ""
            host_index = info.get("hostapi")
            if host_index is not None and 0 <= int(host_index) < len(apis):
                api_name = str(apis[int(host_index)].get("name", ""))
            ranked.append((
                0 if self._is_mic_like(name) else 1,      # 先真麦克风
                _HOST_API_ORDER.get(api_name, 9),         # 再按主机接口的可用性
                idx,
                name,
            ))
        ranked.sort()

        # 上次成功的设备（确认它仍像麦克风）排最前
        last = self.cfg.get("voice.call_device_last", "")
        if last not in ("", None):
            try:
                # 一定要带 "input"：不带的话，输出设备（比如「耳机」）也会被当成候选，
                # 一开必失败，白白浪费两秒
                last_info = sd.query_devices(int(last), "input")
                _add(last, str(last_info.get("name", "")))
            except Exception:  # noqa: BLE001 设备没了 / 不是输入设备 → 忽略
                pass

        for _rank_mic, _rank_api, idx, name in ranked:
            _add(idx, name)
        order.append(None)      # 最后再试一次系统默认设备
        return order

    def _formats_for(self, sd, device) -> list[tuple[int, int]]:
        """给某个设备列出几个值得试的 (采样率, 声道数)。"""
        try:
            info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
        except Exception:  # noqa: BLE001
            return [(16000, 1)]
        max_ch = max(1, int(info.get("max_input_channels", 1) or 1))
        default_sr = int(info.get("default_samplerate", 44100) or 44100)
        out = [(default_sr, min(2, max_ch)), (default_sr, max_ch), (48000, min(2, max_ch)),
               (16000, 1), (16000, min(2, max_ch))]
        seen = []
        for item in out:
            if item not in seen:
                seen.append(item)
        return seen

    def start(self, device=None) -> bool:
        """开始听。返回是否真的开起来了。"""
        if not self.available:
            self.failed.emit("没装录音库：请运行 pip install sounddevice")
            return False
        if self._stream is not None:
            return True
        self._reset_buffer()
        try:
            import sounddevice as sd

            devices = [device] if device not in ("", None) else self._candidate_devices(sd)
            for dev in devices:
                for rate, channels in self._formats_for(sd, dev):
                    try:
                        stream = sd.InputStream(
                            samplerate=rate, channels=channels, dtype="float32",
                            blocksize=int(rate * 0.1), device=dev, callback=self._on_audio,
                        )
                        stream.start()
                    except Exception:  # noqa: BLE001 这个组合不行，换下一个
                        continue
                    self._stream = stream
                    self._rate = rate
                    self._channels = channels
                    self._started_at = time.time()
                    self._heard_anything = False
                    self._warned_silent = False
                    self._watchdog.start(SILENT_WARN_MS)
                    if dev is not None:
                        self.cfg.set("voice.call_device_last", str(dev))
                        try:
                            self.cfg.save()      # 记住这次成功的设备，下次先试它
                        except Exception:        # noqa: BLE001 存不下来也不影响录音
                            pass
                    return True
            self.failed.emit(
                "麦克风打不开：系统里没有可用的输入设备，"
                "或被别的程序占用了。去设置 → 语音里手动选一个麦克风试试。"
            )
            return False
        except Exception as exc:  # noqa: BLE001 没麦克风 / 被占用
            self._stream = None
            self.failed.emit(f"麦克风打不开：{exc}")
            return False

    def stop(self) -> None:
        self._watchdog.stop()
        if self._stream is None:
            return
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self._stream = None
        self._reset_buffer()

    @property
    def running(self) -> bool:
        return self._stream is not None

    # ---- 音频回调 ---------------------------------------------------------
    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        """
        注意：这个函数由 PortAudio 在**音频线程**里调用，
        里面不要碰界面、不要做重活；发信号让主线程去处理。
        """
        try:
            arr = indata
            if self._channels > 1:
                # 多声道先混成单声道（取平均），识别只吃单声道
                arr = indata.mean(axis=1)
            self._feed(arr.tobytes())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"录音处理出错：{exc}")

    def _feed(self, frame: bytes) -> None:
        """处理一块音频。抽成单独方法，测试里可以直接喂合成波形。"""
        samples = array("f")
        samples.frombytes(frame[: len(frame) - (len(frame) % BYTES_PER_SAMPLE)])
        if not samples:
            return

        rms = math.sqrt(sum(x * x for x in samples) / len(samples))
        self.level.emit(rms)
        if rms >= SILENT_FLOOR:
            self._heard_anything = True
            if not self._warned_silent:
                self._watchdog.stop()      # 有声音了，不用再提醒

        threshold = float(self.cfg.get("voice.call_silence_rms", 0.008) or 0.008)
        silence_ms = int(self.cfg.get("voice.call_silence_ms", 900) or 900)
        min_ms = int(self.cfg.get("voice.call_min_utterance_ms", 400) or 400)
        max_s = float(self.cfg.get("voice.call_max_utterance_s", 30) or 30)
        rate = self._rate or SAMPLE_RATE

        block_ms = len(samples) * 1000.0 / rate
        loud = rms >= threshold

        if loud:
            self._speaking = True
            self._frames.extend(frame)
            self._voiced_bytes += len(frame)
            self._silent_bytes = 0
        elif self._speaking:
            # 已经开口了，安静时也继续收（保留尾音），累计到阈值就算说完
            self._frames.extend(frame)
            self._silent_bytes += len(frame)

        voiced_ms = self._voiced_bytes / BYTES_PER_SAMPLE * 1000.0 / rate
        silent_ms_now = self._silent_bytes / BYTES_PER_SAMPLE * 1000.0 / rate
        total_ms = len(self._frames) / BYTES_PER_SAMPLE * 1000.0 / rate

        finished = False
        if self._speaking and silent_ms_now >= silence_ms and voiced_ms >= min_ms:
            finished = True
        if self._speaking and total_ms >= max_s * 1000.0:
            finished = True      # 说太久了，强行切成一句

        if finished:
            payload = self._to_16k(bytes(self._frames), rate)
            duration = len(payload) / BYTES_PER_SAMPLE / SAMPLE_RATE
            self._reset_buffer()
            if duration > 0.05:
                self.utterance.emit(payload, duration)
        elif not self._speaking and not self._frames:
            # 一直没开口：什么都不做，别攒噪音
            return
        _ = block_ms           # 仅用于说明"块大小与毫秒的换算"，无需额外计算

    @staticmethod
    def _to_16k(payload: bytes, rate: int) -> bytes:
        """
        把任意采样率的单声道 float32 重采样成 16 kHz。

        只有在整句收完之后做一次（不是每块都做），所以既省 CPU 又不会切断音素。
        没有 numpy 时退化成"隔几个取一个"的粗暴抽样，识别质量会差一点但不会崩。
        """
        if rate == SAMPLE_RATE or not payload:
            return payload
        try:
            import numpy as np

            data = np.frombuffer(payload, dtype="<f4").astype(np.float32)
            target = max(1, int(len(data) * SAMPLE_RATE / rate))
            out = np.interp(np.linspace(0, len(data) - 1, target),
                            np.arange(len(data)), data).astype(np.float32)
            return out.tobytes()
        except Exception:  # noqa: BLE001
            samples = array("f")
            samples.frombytes(payload)
            step = max(1, rate // SAMPLE_RATE)
            return array("f", samples[::step]).tobytes()

    def _reset_buffer(self) -> None:
        self._speaking = False
        self._frames = bytearray()
        self._voiced_bytes = 0
        self._silent_bytes = 0

    def _on_watchdog(self) -> None:
        """
        开麦 6 秒一点声都没有 —— 多数是三种情况：
            1. Windows 隐私开关没开（桌面程序根本拿不到麦克风）；
            2. 选了一支没插东西的插孔（本机 Realtek 麦克风就是"未插入"状态）；
            3. 麦克风被系统静音了。
        只提醒一次，别一直弹。
        """
        if self._heard_anything or self._warned_silent or self._stream is None:
            return
        self._warned_silent = True
        if mics.desktop_access() == "deny":
            self.silent.emit(mics.blocked_hint())
        else:
            self.silent.emit(
                "麦克风打开着，但六秒里一点声音都没有：可能选错了设备，"
                "或者设备被静音了。去设置 → 语音 → 点「测试麦克风」看一眼。"
            )
