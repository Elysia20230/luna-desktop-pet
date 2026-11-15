# -*- coding: utf-8 -*-
"""
流式"半截情绪标记"的清理测试。

为什么单独测：v1.0.2 线上每次回复末尾都多一句日语（气泡里没有）。
根因是流式最初几帧的 "<"、"<m" 被当成正文发了出去，语音那边把它们送去翻译，
模型对没有语义的碎片凭空编了一整句日语，还被缓存下来反复复用。
所以"没闭合的标记尾巴必须藏住"是一条必须钉死的规则。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.chat.engine import ChatEngine  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    ("哼，", "哼，"),
    # 尖括号标记的半截（流式前几帧就是这些）
    ("哼，<", "哼，"),
    ("哼，<m", "哼，"),
    ("哼，<mo", "哼，"),
    ("哼，<moo", "哼，"),
    ("哼，<mood", "哼，"),
    ("哼，<mood:", "哼，"),
    ("哼，<mood:平", "哼，"),
    # 完整标记（mood.parse 负责）
    ("哼，<mood:平静>", "哼，"),
    # 中文方括号写法
    ("哼，【情绪：平", "哼，"),
    ("哼，【情绪：平静】", "哼，"),
    # 双方括号写法
    ("哼，[[mo", "哼，"),
    ("哼，[[mood:平静]]", "哼，"),
    # 圆括号写法
    ("哼，（mood", "哼，"),
    ("哼，(emotion: calm", "哼，"),
])
def test_partial_mood_tag_is_hidden(raw, expected):
    """没闭合的标记尾巴一律不显示、也不进语音。"""
    assert ChatEngine._clean_partial(raw) == expected


def test_normal_text_is_untouched():
    """正常正文一个字都不能少。"""
    text = "哼，这么晚了还磨磨蹭蹭的。"
    assert ChatEngine._clean_partial(text) == text
