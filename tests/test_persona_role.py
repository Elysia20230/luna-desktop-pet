# -*- coding: utf-8 -*-
"""
人设拼装里「对面是谁」那一段的测试。

要守住的两件事：
1. 角色卡的规则必须进 system prompt，而且位置在"运行时指令"之前
   （先告诉她面对的是谁，再告诉她该怎么说话）；
2. 没选角色时不能出现空称呼，要退回「用户」。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.persona import Persona        # noqa: E402
from app.roles import Role             # noqa: E402

ASAHI = Role(id="asahi", label="小仓朝日", rules="你叫他「朝日」，他叫你「露娜大人」。")
YUUSEI = Role(id="yuusei", label="大藏游星", rules="你叫他「游星」，他是你的丈夫。")


def _persona(role=None) -> Persona:
    return Persona(include_relations=False, role=role).load()


def test_role_block_present_and_named():
    text = _persona(ASAHI).system_prompt(max_chars=50)

    assert "【你面前的人是谁】小仓朝日" in text
    assert "露娜大人" in text, "角色卡的规则原文要进去"
    assert "【身份】你正在作为桌面宠物陪伴「小仓朝日」" in text
    assert "50 字以内" in text


def test_role_block_comes_before_runtime_rules():
    text = _persona(ASAHI).system_prompt(max_chars=50)

    assert text.index("【你面前的人是谁】") < text.index("【身份】"), \
        "先交代对面是谁，再说运行时规则"


def test_switching_role_changes_wording():
    asahi_text = _persona(ASAHI).system_prompt(max_chars=50)
    yuusei_text = _persona(YUUSEI).system_prompt(max_chars=50)

    assert "小仓朝日" in asahi_text and "大藏游星" not in asahi_text
    assert "大藏游星" in yuusei_text and "小仓朝日" not in yuusei_text


def test_without_role_uses_generic_user():
    text = _persona(None).system_prompt(max_chars=60)

    assert "【你面前的人是谁】" not in text, "没有角色卡就不要输出空段落"
    assert "「用户」" in text


def test_proactive_adds_occasion_block():
    normal = _persona(ASAHI).system_prompt(max_chars=50)
    proactive = _persona(ASAHI).system_prompt(max_chars=50, proactive=True)

    assert "【场合】" not in normal
    assert "【场合】" in proactive
    assert "25 字以内" in proactive


def test_memory_and_screen_blocks_are_optional():
    empty = _persona(ASAHI).system_prompt(max_chars=50)
    filled = _persona(ASAHI).system_prompt(
        memory_text="- 主人喜欢黑咖啡", screen_text="她瞄到你在写代码", max_chars=50)

    assert "【你记得的事】" not in empty
    assert "【你现在看到的画面】" not in empty
    assert "主人喜欢黑咖啡" in filled
    assert "她瞄到你在写代码" in filled
