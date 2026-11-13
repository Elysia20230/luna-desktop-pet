# -*- coding: utf-8 -*-
"""
离线台词库的测试。

台词是"没有 Key 时的下限体验"：占位符漏替换会露出 `{name}`，
情绪对不上会让立绘表情说反话，这两类都是肉眼能立刻发现、但代码不会报错的坑。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import mood                             # noqa: E402
from app.chat.offline import (                   # noqa: E402
    OFFLINE_LINES, PROACTIVE_LINES, reply, reply_proactive,
)


def test_proactive_lines_have_no_placeholder_left():
    """每种称呼下都不能残留 {name}。"""
    for name in ("小仓朝日", "大藏游星", ""):
        for _ in range(40):                      # 随机抽，多抽几次覆盖到多数句子
            line, emotion = reply_proactive(name)
            assert "{name}" not in line
            assert line.strip()
            assert emotion in mood.EMOTIONS, f"情绪 {emotion} 不在 12 档里"


def test_proactive_lines_fill_name_when_given():
    """有称呼时，句子里的 {name} 要换成它（抽到没有占位符的句子就跳过）。"""
    filled = []
    for _ in range(80):
        line, _emo = reply_proactive("小仓朝日")
        filled.append(line)
    assert any("小仓朝日" in line for line in filled), PROACTIVE_LINES


def test_reply_still_works_for_user_text():
    line, emotion = reply("你好")
    assert line and emotion in mood.EMOTIONS


def test_reply_uses_role_name():
    """用户称呼会替换掉 {name}：挑一条一定含占位符的句子来验证。"""
    line, _emo = reply("你今天在发呆吗", "大藏游星")
    assert "{" not in line


def test_all_offline_emotions_are_known():
    for emotion in OFFLINE_LINES:
        assert mood.normalize(emotion) == emotion, f"{emotion} 不是标准情绪名"
