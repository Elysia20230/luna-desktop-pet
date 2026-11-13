# -*- coding: utf-8 -*-
"""
服装管家的单元测试：季节判断、夜间睡衣、冬天不穿夏装、手动保护期。

这些用例都不碰 Qt，也不读真实配置文件——Config 用的是临时目录里的副本，
所以跑测试不会改你桌面上露娜当前穿的那套衣服。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Config                     # noqa: E402
from app.wardrobe import (                        # noqa: E402
    DEFAULT_TABLE,
    Wardrobe,
    load_wardrobe,
    season_of,
)


@pytest.fixture()
def cfg(tmp_path):
    """一份干净的配置（写到临时目录，不污染真实 config/config.json）。"""
    return Config(path=tmp_path / "config.json")


def _table():
    return {"outfits": {k: dict(v) for k, v in DEFAULT_TABLE["outfits"].items()}}


def _ward(cfg, table=None):
    w = Wardrobe(cfg, table or _table())
    return w


# ---- 季节 ---------------------------------------------------------------
@pytest.mark.parametrize(
    "month,season",
    [(3, "spring"), (5, "spring"), (6, "summer"), (8, "summer"),
     (9, "autumn"), (11, "autumn"), (12, "winter"), (1, "winter"), (2, "winter")],
)
def test_season_of_boundaries(month, season):
    assert season_of(datetime(2026, month, 15, 12, 0)) == season


# ---- 深夜穿睡衣 ---------------------------------------------------------
def test_night_uses_sleep_outfit(cfg):
    w = _ward(cfg)
    w.current = "0"
    late = datetime(2026, 11, 8, 23, 30)
    assert w.is_night(late) is True
    assert w.desired(now=late) == "5"          # 5 = 家居服


def test_night_ends_after_six(cfg):
    w = _ward(cfg)
    w.current = "5"
    morning = datetime(2026, 11, 8, 10, 0)
    assert w.is_night(morning) is False
    assert w.desired(now=morning) != "5"       # 早饭时间该换回常服了


def test_night_range_can_wrap_or_be_disabled(cfg):
    w = _ward(cfg)
    cfg.set("behavior.outfit_night_from", 9)
    cfg.set("behavior.outfit_night_to", 9)     # 起止相同 = 关掉"夜间睡衣"
    w.refresh()
    assert w.is_night(datetime(2026, 11, 8, 23, 0)) is False


# ---- 季节约束：冬天绝不穿夏装 -------------------------------------------
def test_winter_never_picks_summer(cfg):
    w = _ward(cfg)
    for current in list(DEFAULT_TABLE["outfits"]):
        w.current = current
        for _ in range(30):
            got = w.desired(now=datetime(2026, 1, 15, 14, 0), ignore_gap=True)
            assert got in {"2", "3", "4"}, f"冬天不该选到 {got}"


def test_autumn_also_excludes_summer(cfg):
    w = _ward(cfg)
    for current in list(DEFAULT_TABLE["outfits"]):
        w.current = current
        got = w.desired(now=datetime(2026, 10, 15, 14, 0), ignore_gap=True)
        assert got in {"2", "3"}, f"秋天不该选到 {got}"


def test_summer_never_picks_winter(cfg):
    w = _ward(cfg)
    for current in list(DEFAULT_TABLE["outfits"]):
        w.current = current
        got = w.desired(now=datetime(2026, 7, 15, 14, 0), ignore_gap=True)
        assert got in {"0", "1"}, f"夏天不该选到 {got}"


# ---- 随机 / 最短间隔 / 手动保护期 ---------------------------------------
def test_random_disabled_takes_first(cfg):
    cfg.set("behavior.outfit_random_in_season", False)
    w = _ward(cfg)
    w.current = "0"
    assert w.desired(now=datetime(2026, 1, 15, 14, 0), ignore_gap=True) == "2"


def test_same_outfit_returns_none(cfg):
    """已经穿对了就别再动（返回 None 表示"不用改"）。"""
    cfg.set("behavior.outfit_random_in_season", False)
    w = _ward(cfg)
    w.current = "2"                              # 冬天不随机时第一套就是 2
    assert w.desired(now=datetime(2026, 1, 15, 14, 0), ignore_gap=True) is None
    assert w.desired(now=datetime(2026, 1, 15, 14, 0)) is None


def test_min_gap_blocks_repeat_switch(cfg):
    w = _ward(cfg)
    w.current = "0"
    cfg.set("behavior.outfit_random_in_season", False)
    w.refresh()
    now = datetime(2026, 1, 15, 14, 0)
    first = w.desired(now=now, ignore_gap=True)
    assert first == "2"
    w.mark_switched(first, now)
    cfg.save()
    # 立刻再问一次：刚换过、同季节同时段 → 不该再动
    assert w.desired(now=now) is None


def test_manual_hold_blocks_auto(cfg):
    w = _ward(cfg)
    w.current = "1"
    now = datetime(2026, 11, 8, 23, 0)
    w.note_manual("1", now=now)
    cfg.save()
    assert w.desired(now=now) is None           # 你刚手动换过，别插嘴


def test_auto_disabled_returns_none(cfg):
    cfg.set("behavior.outfit_auto", False)
    w = _ward(cfg)
    w.current = "0"
    assert w.desired(now=datetime(2026, 11, 8, 23, 0), ignore_gap=True) is None


def test_season_override(cfg):
    cfg.set("behavior.outfit_season", "summer")
    w = _ward(cfg)
    w.current = "2"
    got = w.desired(now=datetime(2026, 1, 15, 14, 0), ignore_gap=True)
    assert got in {"0", "1"}


# ---- 服装表读取 ---------------------------------------------------------
def test_load_missing_file_uses_default(tmp_path):
    table, warning = load_wardrobe(tmp_path / "nope.json")
    assert warning == ""
    assert "outfits" in table


def test_load_broken_json_warns(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    table, warning = load_wardrobe(bad)
    assert "失败" in warning
    assert table["outfits"]["5"]["occasion"] == "sleep"


def test_load_custom_file(tmp_path):
    path = tmp_path / "wardrobe.json"
    path.write_text(json.dumps({
        "outfits": {
            "0": {"name": "自定义", "seasons": ["winter"], "occasion": "day", "random": True},
        }
    }, ensure_ascii=False), encoding="utf-8")
    table, warning = load_wardrobe(path)
    assert warning == ""
    assert table["outfits"]["0"]["name"] == "自定义"
