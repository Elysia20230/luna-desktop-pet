# -*- coding: utf-8 -*-
"""
翻译层的两道守卫 + 缓存键版本化。

为什么重要：
    1. 纯标点/标记碎片不能送去翻译（模型会自己编一整句日语）；
    2. 不像日语的结果不能写进缓存（一句坏译文会被每句回复复用）；
    3. 缓存键带版本前缀，老版本写进去的污染条目必须立刻失效，
       否则用户升级后仍然会听到那句多余的日语。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.voice import translate as tr  # noqa: E402


class _Cfg:
    """只提供翻译函数用得到的两个接口。"""
    api_key = "test-key"

    def get(self, key, default=None):
        return default


def test_speech_text_strips_markup():
    assert tr.speech_text("哼，<m") == "哼，"
    assert tr.speech_text("哼，<mood:平静>") == "哼，"
    assert tr.speech_text("哼，【情绪：平") == "哼，"
    assert tr.speech_text(" 哼， ") == "哼，"


@pytest.mark.parametrize("text,ok", [
    ("。", False),
    ("……", False),
    ("<m", False),
    ("<", False),
    ("，，", False),
    ("  ", False),
    ("怎么了？", True),
    ("嗯", True),
    ("ok", True),
])
def test_has_speech_content(text, ok):
    assert tr.has_speech_content(text) is ok


def test_punctuation_is_not_translated_or_cached(tmp_path):
    """纯标点：直接返回空串，不调 API、不写缓存。"""
    cache = tr.TranslationCache(tmp_path / "translate.json")
    called: list[dict] = []
    monkey = lambda **kw: called.append(kw) or "…何か言いたいことがあるなら、はっきり言ったら？"
    original = tr.complete_sync
    tr.complete_sync = monkey
    try:
        assert tr.translate_to_japanese("。", _Cfg(), cache) == ""
    finally:
        tr.complete_sync = original
    assert called == [], "纯标点不该发请求"
    assert cache.data == {}, "纯标点不该写缓存"


def test_non_japanese_reply_is_not_cached(tmp_path, monkeypatch):
    """模型回了中文解释：当作失败，绝不写缓存。"""
    cache = tr.TranslationCache(tmp_path / "translate.json")
    monkeypatch.setattr(tr, "complete_sync", lambda **kw: "这是中文解释，不是日语。")
    assert tr.translate_to_japanese("怎么了？", _Cfg(), cache) == ""
    assert cache.data == {}


def test_japanese_reply_is_cached(tmp_path, monkeypatch):
    cache = tr.TranslationCache(tmp_path / "translate.json")
    monkeypatch.setattr(tr, "complete_sync", lambda **kw: "「どうしたの？」")
    ja = tr.translate_to_japanese("怎么了？", _Cfg(), cache)
    assert ja == "どうしたの？"
    assert cache.get("怎么了？") == "どうしたの？"
    # 第二次不该再发请求
    monkeypatch.setattr(tr, "complete_sync", lambda **kw: pytest.fail("命中缓存却又发了请求"))
    assert tr.translate_to_japanese("怎么了？", _Cfg(), cache) == "どうしたの？"


def test_legacy_cache_key_does_not_hit(tmp_path):
    """
    老版本缓存（键 = 不带版本前缀的 sha1）里的污染条目必须失效。

    线上那条就是 sha1("<m") → 「…朝日、何ぼーっとしてるの？」。
    """
    legacy_key = hashlib.sha1("<m".encode("utf-8")).hexdigest()
    path = tmp_path / "translate.json"
    path.write_text(json.dumps({legacy_key: "…朝日、何ぼーっとしてるの？"},
                              ensure_ascii=False), encoding="utf-8")
    cache = tr.TranslationCache(path)
    assert cache.get("<m") is None, "旧污染条目仍然被命中了"
    assert tr._key("<m") != legacy_key
