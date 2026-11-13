# -*- coding: utf-8 -*-
"""
时间感：把「现在几点、今天几号、星期几、距上次说话多久」算成一句话，塞进人设。

为什么要单独一个模块
--------------------
大模型没有时钟，它说的"现在"永远是训练数据里的时间。只要我们在每次请求里把
真实时间写进 system prompt，她回答「今天几号」「现在几点」就是准的。

这里的函数都是纯函数（除了 now_local），方便直接单元测试。
时间段划分：
    清晨 05:00-08:00   上午 08:00-11:00   中午 11:00-13:00
    下午 13:00-17:00   傍晚 17:00-19:00   晚上 19:00-23:00
    深夜 23:00-05:00
"""
from __future__ import annotations

from datetime import datetime

WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def now_local() -> datetime:
    """本机当前时间（不做时区转换，跟随系统设置）。"""
    return datetime.now()


def weekday_cn(dt: datetime) -> str:
    """周几：datetime.weekday() 里周一=0，正好对上 WEEKDAYS 下标。"""
    return WEEKDAYS[dt.weekday()]


def part_of_day(dt: datetime) -> str:
    """把一天切成七段，给她一个"现在是什么时候"的直观概念。"""
    hour = dt.hour
    if 5 <= hour < 8:
        return "清晨"
    if 8 <= hour < 11:
        return "上午"
    if 11 <= hour < 13:
        return "中午"
    if 13 <= hour < 17:
        return "下午"
    if 17 <= hour < 19:
        return "傍晚"
    if 19 <= hour < 23:
        return "晚上"
    return "深夜"


def describe(dt: datetime) -> str:
    """2026年11月7日 周五 13:54"""
    return f"{dt.year}年{dt.month}月{dt.day}日 {weekday_cn(dt)} {dt:%H:%M}"


def gap_text(seconds: float | None) -> str:
    """
    距上次说话多久的"人话"版本。

    None 表示没有历史（第一次聊天），返回空串，让调用方决定要不要写这一行。
    """
    if seconds is None:
        return ""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return "刚才"
    if seconds < 3600:
        return f"{int(seconds // 60)} 分钟前"
    if seconds < 6 * 3600:
        return f"{int(seconds // 3600)} 个多小时前"
    if seconds < 48 * 3600:
        # 一天到两天之间统称"昨天这个点"：说成"1 天前"反而不像人话
        return "昨天这个点"
    days = int(seconds // 86400)
    return f"{days} 天前"


def time_brief(now: datetime, last_talk: datetime | None = None) -> str:
    """
    拼成给模型看的短文本（两行以内）。

       现在：2026年11月7日 周五 13:54（下午，周五，工作日）
       上一次说话：昨天这个点（约 26 小时前）
    """
    with_weekday = f"{part_of_day(now)}，{weekday_cn(now)}"
    kind = "周末" if now.weekday() >= 5 else "工作日"
    lines = [f"现在：{describe(now)}（{with_weekday}，{kind}）"]

    if last_talk is not None:
        gap = (now - last_talk).total_seconds()
        text = gap_text(gap)
        if text:
            hours = gap / 3600.0
            if hours >= 1:
                lines.append(f"上一次说话：{text}（约 {hours:.0f} 小时前）")
            else:
                lines.append(f"上一次说话：{text}")
    return "\n".join(lines)
