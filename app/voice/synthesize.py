# -*- coding: utf-8 -*-
"""
日语台词 + 音色 → 一段 wav。

调的是 GPT-SoVITS 自带的 HTTP 接口（`api_v2.py` 的 `POST /tts`）：
    请求体里带 text（日语）、ref_audio_path（参考音频 = 音色）、
    prompt_text（那段参考音频的原文）、speed_factor（语速）等。
    返回的就是 wav 二进制，这里直接落盘缓存。

注意几个实测过的坑：
- 必须用 POST + JSON；GET 少参数会 500；
- 参考音频路径里有空格、日文、方括号，必须原样放进 JSON，不要自己去转义；
- 纯 CPU 上一句 4 秒语音要合成 8~10 秒，所以调用方一定要放在后台线程里。
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

import httpx

from ..config import DATA_DIR
from .voices import RefVoice

VOICE_DIR = DATA_DIR / "voice"
CACHE_KEEP = 50            # 最多留多少个 wav（超出按修改时间删最旧的）


def clip_for_speech(text: str, max_chars: int) -> str:
    """
    朗读用的文本：只读前 max_chars 个字，避免长段落合成半分钟。
    只影响"念出来的部分"，屏幕上的文字照旧完整显示。
    """
    text = (text or "").strip()
    limit = max(0, int(max_chars or 0))
    if limit and len(text) > limit:
        clipped = text[:limit]
        # 尽量断在标点处，听起来自然一点
        for mark in ("。", "！", "？", "…", "，", "、"):
            pos = clipped.rfind(mark)
            if pos >= limit // 2:
                return clipped[: pos + 1]
        return clipped
    return text


# 断句用的标点：硬标点立刻断，软标点只在自己已经够长时才断
STRONG_MARKS = "。！？!?…"
SOFT_MARKS = "，,、；;：:"


def split_for_speech(text: str, first_chars: int = 12, segment_chars: int = 20,
                     max_chars: int = 30) -> list[str]:
    """
    把一句话切成几段，好让第一段先合成、先出声（其余在播放时慢慢合成）。

    规则（都能在设置里调）：
        - 先按 max_chars 截断，避免一句话合成半分钟；
        - 遇到「。！？…」立刻断句；
        - 遇到「，、；：」只有在当前这段已经够长时才断（太碎听起来断气）；
        - 没标点就一直攒到上限再硬切；
        - 第一段上限是 first_chars（小一点 → 第一声更快），之后每段是 segment_chars。

    返回切好的中文片段列表；原文一个字都不会丢（拼起来等于截断后的原文）。
    """
    raw = (text or "").strip()
    if not raw:
        return []
    limit = max(0, int(max_chars or 0))
    if limit and len(raw) > limit:
        raw = clip_for_speech(raw, limit)
    first = max(4, int(first_chars or 12))
    other = max(first, int(segment_chars or 20))

    parts: list[str] = []
    buf = ""
    for ch in raw:
        buf += ch
        cap = first if not parts else other
        if ch in STRONG_MARKS and len(buf) >= 2:
            parts.append(buf)
            buf = ""
        elif ch in SOFT_MARKS and len(buf) >= cap:
            parts.append(buf)
            buf = ""
        elif len(buf) >= cap:
            parts.append(buf)
            buf = ""
    if buf:
        parts.append(buf)
    # 去掉纯空白的小碎片（比如句首的空格），保证不为空
    return [p.strip() for p in parts if p.strip()]


def build_payload(text_ja: str, voice: RefVoice, cfg=None) -> dict:
    """
    组装 POST /tts 的请求体（单独抽出来，测试里可以不联网络就检查字段）。

    参数含义：
        text_lang / prompt_lang 都写 "ja"，因为两边都是日语；
        text_split_method="cut5" 是官方默认的断句方式，短句差别不大；
        speed_factor 来自音色表（想让我生气时说得快一点就改 assets/voice_emotions.json）。
    """
    return {
        "text": text_ja,
        "text_lang": "ja",
        "ref_audio_path": str(voice.audio),
        "prompt_text": voice.prompt,
        "prompt_lang": "ja",
        "text_split_method": "cut5",
        "media_type": "wav",
        "streaming_mode": False,
        "speed_factor": float(voice.speed or 1.0),
        "top_k": 15,
        "top_p": 1.0,
        "temperature": 1.0,
        "repetition_penalty": 1.35,
        "seed": -1,
        "batch_size": 1,
        "parallel_infer": True,
    }


def cache_path(text_ja: str, voice: RefVoice) -> Path:
    """缓存文件名：同一句话 + 同一音色 + 同一语速 = 同一个文件。"""
    raw = f"{text_ja}|{voice.name}|{voice.speed:.3f}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return VOICE_DIR / f"{digest}.wav"


def synthesize(text_ja: str, voice: RefVoice, base_url: str,
               timeout: float = 180.0, force: bool = False) -> Path:
    """
    合成一句日语，返回 wav 路径。命中缓存就直接返回，不再请求。

    base_url 例：http://127.0.0.1:9888
    失败会抛异常，由 VoiceService 统一兜底（朗读被跳过，聊天不受影响）。
    """
    text_ja = (text_ja or "").strip()
    if not text_ja:
        raise RuntimeError("没有可朗读的日语文案")
    out = cache_path(text_ja, voice)
    if out.is_file() and out.stat().st_size > 1024 and not force:
        return out

    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    url = (base_url or "http://127.0.0.1:9888").rstrip("/") + "/tts"
    payload = build_payload(text_ja, voice)

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=payload)
    if resp.status_code != 200:
        body = resp.text[:200] if resp.text else ""
        raise RuntimeError(f"语音合成失败（HTTP {resp.status_code}）{body}")
    data = resp.content
    if len(data) < 1024:
        raise RuntimeError("语音合成返回的内容不像 wav")

    tmp = out.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(out)
    _prune_cache()
    return out


def _prune_cache() -> None:
    """只保留最近 CACHE_KEEP 个 wav，别让 data/voice 无限长。"""
    try:
        files = sorted(VOICE_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[CACHE_KEEP:]:
            old.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 清理失败不影响主流程
        pass


def probe_alive(base_url: str, timeout: float = 2.0) -> bool:
    """服务是否活着：只要 HTTP 有响应就算活着（/control 无参数会返回 400，那也是活着）。"""
    try:
        httpx.get((base_url or "").rstrip("/") + "/control", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_alive(base_url: str, seconds: float = 90.0) -> bool:
    """等语音服务起来（模型加载纯 CPU 要十几秒）。"""
    deadline = time.time() + max(5.0, float(seconds))
    while time.time() < deadline:
        if probe_alive(base_url):
            return True
        time.sleep(1.0)
    return False
