# -*- coding: utf-8 -*-
"""
时间感的测试：日期/星期/时段/间隔的描述。

这些全是纯函数，不依赖网络和时钟真值，所以断言可以写得很具体。
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import timecontext as tc  # noqa: E402


def _dt(month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute)


def test_weekday_names():
    # 2026-11-07 是周六
    assert tc.weekday_cn(_dt(11, 7, 9)) == "周六"
    assert tc.weekday_cn(_dt(11, 9, 9)) == "周一"


def test_part_of_day_edges():
    assert tc.part_of_day(_dt(11, 7, 5)) == "清晨"
    assert tc.part_of_day(_dt(11, 7, 7)) == "清晨"
    assert tc.part_of_day(_dt(11, 7, 8)) == "上午"
    assert tc.part_of_day(_dt(11, 7, 11)) == "中午"
    assert tc.part_of_day(_dt(11, 7, 13)) == "下午"
    assert tc.part_of_day(_dt(11, 7, 17)) == "傍晚"
    assert tc.part_of_day(_dt(11, 7, 19)) == "晚上"
    assert tc.part_of_day(_dt(11, 7, 23)) == "深夜"
    assert tc.part_of_day(_dt(11, 7, 3)) == "深夜"


def test_describe_contains_date_and_weekday():
    text = tc.describe(_dt(11, 7, 13, 54))
    assert "2026年11月7日" in text
    assert "周六" in text
    assert "13:54" in text


def test_gap_text_buckets():
    assert tc.gap_text(None) == ""
    assert tc.gap_text(10) == "刚才"
    assert tc.gap_text(120) == "2 分钟前"
    assert tc.gap_text(3 * 3600) == "3 个多小时前"
    assert tc.gap_text(30 * 3600) == "昨天这个点"
    assert tc.gap_text(60 * 3600) == "2 天前"
    assert tc.gap_text(3 * 86400) == "3 天前"


def test_time_brief_without_history():
    text = tc.time_brief(_dt(11, 7, 13, 54))
    assert "现在：" in text
    assert "下午" in text
    assert "周六" in text
    assert "上次" not in text


def test_time_brief_with_history():
    now = _dt(11, 7, 13, 54)
    last = now - timedelta(hours=25)
    text = tc.time_brief(now, last)
    assert "上一次说话" in text
    assert "昨天这个点" in text
