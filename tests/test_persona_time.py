# -*- coding: utf-8 -*-
"""
人设里的时间段：只有传了 now 才出现，而且必须排在【篇幅】之前。

守住两件事：
    1. 不传 now 时输出与以前完全一致（老测试、老调用方不受影响）；
    2. 传了以后模型能看到具体日期和"不要凭印象猜"的约束。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.persona import Persona  # noqa: E402


def _persona() -> Persona:
    return Persona(include_relations=False, extra_note="").load()


def test_no_time_section_without_now():
    text = _persona().system_prompt()
    assert "【现在是什么时候】" not in text


def test_time_section_appears_and_ordered():
    now = datetime(2026, 11, 7, 13, 54)
    text = _persona().system_prompt(now=now)
    assert "【现在是什么时候】" in text
    assert "2026年11月7日" in text
    assert "13:54" in text
    # 必须在【篇幅】之前、在【身份】之后
    assert text.index("【身份】") < text.index("【现在是什么时候】") < text.index("【篇幅】")


def test_time_section_warns_against_guessing():
    text = _persona().system_prompt(now=datetime(2026, 11, 7, 9))
    assert "不要凭印象猜" in text


def test_time_section_mentions_gap():
    now = datetime(2026, 11, 7, 13, 54)
    text = _persona().system_prompt(now=now, last_talk=now.replace(hour=10, minute=0))
    assert "上一次说话" in text


def test_time_section_coexists_with_proactive():
    text = _persona().system_prompt(now=datetime(2026, 11, 7, 22), proactive=True)
    assert "【现在是什么时候】" in text
    assert "【场合】" in text
