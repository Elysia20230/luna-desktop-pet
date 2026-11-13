# -*- coding: utf-8 -*-
"""
语音分段与合成的"纯逻辑"测试（不起服务、不联网）。

为什么单独测这些：语音慢的问题就出在"整句一次合成"上，
分段规则一旦写错（切漏字、段太长），首声延迟又会退回去，所以这层必须有测试兜着。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.voice.synthesize import (  # noqa: E402
    build_payload, clip_for_speech, split_for_speech,
)
from app.voice.voices import RefVoice  # noqa: E402


def test_short_text_is_single_segment():
    """一句话不到首段上限：就一段，不硬切。"""
    assert split_for_speech("心情非常好。") == ["心情非常好。"]


def test_first_segment_uses_first_chars():
    """第一段按 first_chars 切，后面按 segment_chars 切（第一段更短才能更快出声）。"""
    text = "今天天气不错我们出去走走吧" * 3      # 39 字
    segs = split_for_speech(text, first_chars=12, segment_chars=20, max_chars=30)
    assert len(segs) >= 2
    assert len(segs[0]) <= 12
    for seg in segs[1:]:
        assert len(seg) <= 20


def test_no_character_is_lost():
    """切段只允许在标点处断开，拼回来必须和原文（截断后）一模一样。"""
    text = "嗯……你真的要这么做吗？我可不觉得这算什么好主意呢。"
    segs = split_for_speech(text, first_chars=8, segment_chars=10, max_chars=30)
    assert "".join(segs) == text


def test_prefers_punctuation_boundary():
    """有标点就在标点后断，别把词切开。"""
    segs = split_for_speech("好的。那就这样吧。", first_chars=4, segment_chars=20, max_chars=30)
    assert segs[0].endswith("。")


def test_max_chars_truncates_long_text():
    """超过 max_chars 的部分直接不要：这么长合成要几十秒，不如不念。"""
    text = "字" * 100
    segs = split_for_speech(text, first_chars=5, segment_chars=10, max_chars=25)
    assert "".join(segs) == text[:25]


def test_clip_for_speech_keeps_whole_sentence():
    """截断时尽量停在句子边界，别把半句话念出来。"""
    text = "第一句话在这里。第二句话有点长。第三句"
    out = clip_for_speech(text, 12)
    assert len(out) <= 12
    assert out.endswith("。") or out == text[:12]


def test_build_payload_fields():
    """发给 api_v2.py 的参数：日语、参考音频、语速都带上。"""
    voice = RefVoice(name="soft", label="柔和", audio=Path("C:/x/soft.wav"),
                     prompt="……", speed=1.05)
    payload = build_payload("おはよう。", voice)
    assert payload["text"] == "おはよう。"
    assert payload["text_lang"] == "ja"
    assert payload["prompt_lang"] == "ja"
    assert payload["ref_audio_path"] == str(voice.audio)
    assert payload["prompt_text"] == "……"
    assert payload["speed_factor"] == pytest.approx(1.05)
    assert payload["media_type"] == "wav"
    assert payload["streaming_mode"] is False


def test_empty_text_produces_nothing():
    """空字符串不该产生段（否则会去合成一大堆空白）。"""
    assert split_for_speech("") == []
    assert split_for_speech("   ") == []
