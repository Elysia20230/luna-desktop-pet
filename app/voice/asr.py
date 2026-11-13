# -*- coding: utf-8 -*-
"""
语音识别（把你说的话变成文字）。

两条路：
1. 本地 faster-whisper（**默认**）：完全离线、免费，CPU 跑。第一次用会从
   hf-mirror.com 下载模型（base 约 145 MB），下完就不用联网了。
2. 云端硅基流动（备选）：不占 CPU，但按音频时长计费，需要 Key。

选哪条由 config 的 voice.asr_backend 决定（local / cloud）。
两条路都返回**纯文本**，识别不出来就返回空串，不抛异常。
"""
from __future__ import annotations

import io
import os
import struct
import threading

import httpx

from ..config import DATA_DIR

# 模型放在工作区内，方便备份、也方便直接删掉重下
DEFAULT_MODEL_DIR = DATA_DIR / "models"

# 国内直连 huggingface.co 通常不通，官方给了 hf-mirror 镜像
MIRROR_ENDPOINT = "https://hf-mirror.com"


# ---------------------------------------------------------------- 音频编码
def encode_wav(samples: bytes, rate: int = 16000) -> bytes:
    """
    float32 样点 → 16-bit 单声道 WAV（内存里拼，不落盘）。

    云端接口要的是"能上传的文件"，所以必须转成标准 wav。
    float32 的取值范围是 [-1, 1]，转 int16 要乘 32767 并夹紧，否则会爆音。
    """
    from array import array

    floats = array("f")
    floats.frombytes(samples[: len(samples) - (len(samples) % 4)])
    pcm = array("h", (max(-32768, min(32767, int(v * 32767))) for v in floats)).tobytes()

    out = bytearray()
    out += b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    out += b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    out += b"data" + struct.pack("<I", len(pcm)) + pcm
    return bytes(out)


def _clean(text: str) -> str:
    """去掉首尾空白和只剩标点的碎片（识别不到内容时常见）。"""
    text = (text or "").strip()
    body = "".join(ch for ch in text if ch.isalnum())
    return text if body else ""


# ---------------------------------------------------------------- 本地识别
class LocalASR:
    """faster-whisper（CPU、int8）。免费离线，代价是占一点内存和 CPU。"""

    def __init__(self, model: str = "base", cache_dir=None,
                 compute_type: str = "int8") -> None:
        self.model_name = model or "base"
        self.cache_dir = str(cache_dir or DEFAULT_MODEL_DIR)
        self.compute_type = compute_type
        self._model = None
        self._lock = threading.Lock()
        self._error = ""

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def last_error(self) -> str:
        return self._error

    def load(self) -> bool:
        """（后台线程调用）加载模型，必要时从镜像下载。"""
        with self._lock:
            if self._model is not None:
                return True
            os.makedirs(self.cache_dir, exist_ok=True)
            # 必须在 import/下载之前设好，huggingface_hub 读的是环境变量
            os.environ.setdefault("HF_ENDPOINT", MIRROR_ENDPOINT)
            try:
                from faster_whisper import WhisperModel

                self._model = WhisperModel(
                    self.model_name,
                    device="cpu",
                    compute_type=self.compute_type,
                    download_root=self.cache_dir,
                )
                self._error = ""
                return True
            except Exception as exc:  # noqa: BLE001 下载失败/磁盘满/模型名错
                self._error = str(exc)
                self._model = None
                return False

    def transcribe(self, samples: bytes, rate: int = 16000) -> str:
        if self._model is None and not self.load():
            return ""
        try:
            import numpy as np

            audio = np.frombuffer(samples, dtype="float32").astype("float32")
            segments, _info = self._model.transcribe(
                audio,
                language=None,          # 中日英混说都能认
                beam_size=1,            # 快一点，通话场景够用
                vad_filter=True,
                condition_on_previous_text=False,
            )
            return _clean("".join(seg.text for seg in segments))
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            return ""


# ---------------------------------------------------------------- 云端识别
class CloudASR:
    """硅基流动的 SenseVoice 接口（可选备选，按量计费）。"""

    def __init__(self, base_url: str, model: str, api_key: str,
                 timeout: float = 30.0) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model or "FunAudioLLM/SenseVoiceSmall"
        self.api_key = api_key or ""
        self.timeout = timeout

    def transcribe(self, samples: bytes, rate: int = 16000) -> str:
        if not self.base_url or not self.api_key:
            return ""
        wav = encode_wav(samples, rate)
        files = {"file": ("speech.wav", io.BytesIO(wav), "audio/wav")}
        data = {"model": self.model}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(f"{self.base_url}/audio/transcriptions",
                                   headers=headers, data=data, files=files)
            if resp.status_code != 200:
                return ""
            payload = resp.json()
            return _clean(payload.get("text", ""))
        except Exception:  # noqa: BLE001 网络问题就当作没听见
            return ""


# ---------------------------------------------------------------- 组装
def has_local_backend() -> bool:
    """
    本地识别库在不在（faster-whisper 装没装）。

    单独抽成函数：测试里可以直接替换它，不用真的去 import 一个几 MB 的库。
    """
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def build_asr(cfg):
    """按配置挑后端。本地不可用（没装依赖）时自动退回云端；都不行返回 None。"""
    backend = str(cfg.get("voice.asr_backend", "local") or "local").lower()
    if backend == "cloud":
        key = cfg.get("voice.asr_key", "") or cfg.get("memory.embedding.api_key", "")
        cloud = CloudASR(
            base_url=cfg.get("voice.asr_base_url", ""),
            model=cfg.get("voice.asr_model", ""),
            api_key=key,
            timeout=float(cfg.get("voice.asr_timeout", 30) or 30),
        )
        if cloud.api_key:
            return cloud

    local_dir = cfg.get("voice.asr_local_dir", "") or DEFAULT_MODEL_DIR
    local = LocalASR(model=cfg.get("voice.asr_local_model", "base"),
                     cache_dir=local_dir)
    if has_local_backend():
        return local
    key = cfg.get("voice.asr_key", "") or cfg.get("memory.embedding.api_key", "")
    cloud = CloudASR(
        base_url=cfg.get("voice.asr_base_url", ""),
        model=cfg.get("voice.asr_model", ""),
        api_key=key,
        timeout=float(cfg.get("voice.asr_timeout", 30) or 30),
    )
    return cloud if cloud.api_key else None


def transcribe(samples: bytes, cfg, backend=None) -> str:
    """统一入口：拿一段 16k 样点，返回文字（失败给空串）。"""
    asr = backend or build_asr(cfg)
    if asr is None:
        return ""
    try:
        return asr.transcribe(samples, 16000)
    except Exception:  # noqa: BLE001
        return ""
