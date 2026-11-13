# -*- coding: utf-8 -*-
"""
识别层的测试：不下载模型、不联网。

本地识别用"注入假模型"验证清洗逻辑；
云端识别用"假 httpx"验证状态码分支；
wav 编码只验文件头，保证能被标准工具读。
"""
from __future__ import annotations

import struct
import sys
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.voice import asr as asr_mod  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent


def _samples(n: int = 1600, value: float = 0.1) -> bytes:
    return array("f", [value] * n).tobytes()


# ---------------------------------------------------------------- wav 编码
def test_encode_wav_header():
    wav = asr_mod.encode_wav(_samples(800))
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[12:16] == b"fmt "
    channels, rate = struct.unpack("<HI", wav[22:28])
    assert channels == 1
    assert rate == 16000
    bits = struct.unpack("<H", wav[34:36])[0]
    assert bits == 16
    data_size = struct.unpack("<I", wav[40:44])[0]
    assert data_size == 800 * 2          # float32 转 int16 = 每个样点 2 字节


# ---------------------------------------------------------------- 本地识别
class _FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeModel:
    def __init__(self, texts) -> None:
        self.texts = texts
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return [_FakeSegment(t) for t in self.texts], {"language": "zh"}


def test_local_transcribe_joins_and_cleans():
    local = asr_mod.LocalASR(model="base", cache_dir=_ROOT / "data" / "models")
    local._model = _FakeModel(["  现在", "几点？ "])
    assert local.transcribe(_samples()) == "现在几点？"


def test_local_transcribe_drops_punctuation_only():
    local = asr_mod.LocalASR(model="base", cache_dir=_ROOT / "data" / "models")
    local._model = _FakeModel(["。", "  "])
    assert local.transcribe(_samples()) == ""


def test_local_transcribe_returns_empty_on_error():
    class _Boom:
        def transcribe(self, audio, **kwargs):
            raise RuntimeError("模型炸了")

    local = asr_mod.LocalASR(model="base", cache_dir=_ROOT / "data" / "models")
    local._model = _Boom()
    assert local.transcribe(_samples()) == ""


# ---------------------------------------------------------------- 云端识别
class _FakeResp:
    def __init__(self, status: int, payload=None) -> None:
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, resp) -> None:
        self.resp = resp
        self.seen = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, data=None, files=None):
        self.seen = {"url": url, "headers": headers, "data": data, "files": files}
        return self.resp


def test_cloud_transcribe_ok(monkeypatch):
    client = _FakeClient(_FakeResp(200, {"text": " 你好呀 "}))
    monkeypatch.setattr(asr_mod.httpx, "Client", lambda timeout=None: client)
    cloud = asr_mod.CloudASR("https://api.example.com/v1", "m", "k")
    assert cloud.transcribe(_samples()) == "你好呀"
    assert client.seen["url"].endswith("/audio/transcriptions")
    assert client.seen["headers"]["Authorization"] == "Bearer k"


def test_cloud_transcribe_bad_status(monkeypatch):
    monkeypatch.setattr(asr_mod.httpx, "Client",
                        lambda timeout=None: _FakeClient(_FakeResp(429)))
    cloud = asr_mod.CloudASR("https://api.example.com/v1", "m", "k")
    assert cloud.transcribe(_samples()) == ""


def test_cloud_without_key_is_noop():
    cloud = asr_mod.CloudASR("https://api.example.com/v1", "m", "")
    assert cloud.transcribe(_samples()) == ""


# ---------------------------------------------------------------- 后端选择
def test_build_asr_prefers_local_when_available(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(asr_mod, "has_local_backend", lambda: True)
    monkeypatch.setattr(asr_mod, "LocalASR", lambda **kw: "LOCAL")
    assert asr_mod.build_asr(Config()) == "LOCAL"


def test_build_asr_falls_back_to_cloud(monkeypatch):
    from app.config import Config

    monkeypatch.setattr(asr_mod, "has_local_backend", lambda: False)
    cfg = Config()
    cfg.set("voice.asr_key", "cloud-key")
    out = asr_mod.build_asr(cfg)
    assert isinstance(out, asr_mod.CloudASR)
    assert out.api_key == "cloud-key"


def test_build_asr_returns_none_without_anything(monkeypatch, tmp_path):
    from app.config import Config

    monkeypatch.setattr(asr_mod, "has_local_backend", lambda: False)
    cfg = Config()
    cfg.set("voice.asr_key", "")
    cfg.set("memory.embedding.api_key", "")
    assert asr_mod.build_asr(cfg) is None
    _ = tmp_path
