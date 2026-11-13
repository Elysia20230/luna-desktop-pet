# -*- coding: utf-8 -*-
"""
服装管家：决定"现在该穿哪一套"。

它只做判断，不动界面——真正的换装由 main.py 调 pet.set_outfit() 完成。
判断依据有三样：
    1. 现在是几点：晚上/深夜穿睡衣（默认 22:00–06:00，可改）
    2. 现在是几月：冬天不会穿夏季的衣服（避免"十二月穿短袖"）
    3. 你手动换过衣服：两小时内不再自动动它（免得你刚换好就被她换掉）

想改"哪套算睡衣/哪套冬天穿"，去改 assets/wardrobe.json，不用动这里。
"""
from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path

from .config import ASSETS_DIR
from .timecontext import now_local, part_of_day

# 四个季节的键名（wardrobe.json 里用这个拼写）
SEASONS = ("spring", "summer", "autumn", "winter")

# 季节的中文名，给人看（设置面板、托盘提示用）
SEASON_NAMES = {
    "spring": "春",
    "summer": "夏",
    "autumn": "秋",
    "winter": "冬",
}

# 万一 assets/wardrobe.json 被删了/写坏了，用这份兜底，保证功能还能跑
DEFAULT_TABLE = {
    "outfits": {
        "0": {"name": "学院装·夏", "seasons": ["spring", "summer"], "occasion": "day", "random": True},
        "1": {"name": "学院装·蓝", "seasons": ["spring", "summer"], "occasion": "day", "random": True},
        "2": {"name": "学院装·秋冬", "seasons": ["autumn", "winter"], "occasion": "day", "random": True},
        "3": {"name": "洋装·浅蓝", "seasons": ["autumn", "winter"], "occasion": "day", "random": True},
        "4": {"name": "黑大衣", "seasons": ["winter"], "occasion": "day", "random": True},
        "5": {"name": "家居服", "seasons": list(SEASONS), "occasion": "sleep", "random": False},
        "6": {"name": "白色礼服", "seasons": ["summer"], "occasion": "formal", "random": False},
    }
}

WARDROBE_FILE = ASSETS_DIR / "wardrobe.json"


def season_of(dt: datetime) -> str:
    """按月份判季节：3–5 春 / 6–8 夏 / 9–11 秋 / 12–2 冬。"""
    month = dt.month
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def load_wardrobe(path: Path | None = None) -> tuple[dict, str]:
    """
    读服装表。

    返回 (表, 警告文字)。警告非空表示"用了兜底表"，main.py 会把它显示在托盘提示里。
    文件不存在不算错（用户可能就想要默认），只有 JSON 写坏了才警告。
    """
    target = Path(path) if path else WARDROBE_FILE
    if not target.exists():
        return {"outfits": dict(DEFAULT_TABLE["outfits"])}, ""
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 坏文件不该让程序起不来
        return {"outfits": dict(DEFAULT_TABLE["outfits"])}, f"服装表读取失败，先用默认设置：{exc}"

    outfits = raw.get("outfits")
    if not isinstance(outfits, dict) or not outfits:
        return {"outfits": dict(DEFAULT_TABLE["outfits"])}, "服装表里没有 outfits 段，先用默认设置"

    # 逐条补全字段，防止手写时漏一个键就崩
    clean: dict[str, dict] = {}
    for key, item in outfits.items():
        if not isinstance(item, dict):
            continue
        seasons = [s for s in item.get("seasons", []) if s in SEASONS]
        clean[str(key)] = {
            "name": str(item.get("name", f"造型 {key}")),
            "seasons": seasons,
            "occasion": str(item.get("occasion", "day")),
            "random": bool(item.get("random", True)),
        }
    if not clean:
        return {"outfits": dict(DEFAULT_TABLE["outfits"])}, "服装表内容看不懂，先用默认设置"
    return {"outfits": clean}, ""


class Wardrobe:
    """把配置和服装表拼起来，回答"现在穿哪套"。"""

    def __init__(self, cfg, table: dict | None = None) -> None:
        self.cfg = cfg
        self.table: dict[str, dict] = dict((table or {}).get("outfits") or DEFAULT_TABLE["outfits"])
        self.current = ""          # 当前穿着的造型编号，由 main.py 同步进来
        self.refresh()

    # ---- 配置读取 ---------------------------------------------------------
    def _int(self, key: str, default: int) -> int:
        """
        读一个整数配置。

        注意不能用 `cfg.get(key, default) or default`：0 是合法值
        （比如"0 点开始换睡衣"、"最短间隔 0 = 不限"），falsy 兜底会把 0 吃掉。
        """
        raw = self.cfg.get(key, default)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default

    def refresh(self) -> None:
        """设置面板保存后重新读一遍配置（数值改了立刻生效）。"""
        self.auto = bool(self.cfg.get("behavior.outfit_auto", True))
        self.night_from = self._int("behavior.outfit_night_from", 22)
        self.night_to = self._int("behavior.outfit_night_to", 6)
        self.night_outfit = str(self.cfg.get("behavior.outfit_night_outfit", "5") or "5")
        self.min_gap_min = self._int("behavior.outfit_min_gap_min", 30)
        self.manual_hold_min = self._int("behavior.outfit_manual_hold_min", 120)
        self.random_in_season = bool(self.cfg.get("behavior.outfit_random_in_season", True))
        self.season_override = str(self.cfg.get("behavior.outfit_season", "") or "")

    # ---- 小工具 -----------------------------------------------------------
    def name_of(self, outfit: str) -> str:
        """造型的中文名（服装表里的 name）。"""
        return self.table.get(str(outfit), {}).get("name", f"造型 {outfit}")

    def is_night(self, now: datetime | None = None) -> bool:
        """现在算不算"该换睡衣"的时段。支持跨零点（22 → 6）。"""
        now = now or now_local()
        hour = now.hour
        if self.night_from == self.night_to:
            return False
        if self.night_from < self.night_to:
            return self.night_from <= hour < self.night_to
        return hour >= self.night_from or hour < self.night_to

    def _parse(self, value) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:  # noqa: BLE001 - 配置被人手改坏了也别崩
            return None

    # ---- 核心判断 ---------------------------------------------------------
    def desired(self, now: datetime | None = None, available: set[str] | None = None,
                ignore_gap: bool = False) -> str | None:
        """
        现在应该穿哪套？返回 None 表示"不用动"。

        available：当前尺寸族里实际有图的造型编号集合；传了就只在其中有图的里面挑。
        ignore_gap：忽略"最短换装间隔"（开关刚打开、刚改完设置时用）。
        """
        if not self.auto:
            return None
        now = now or now_local()
        if self._manual_locked(now):
            return None

        pool = self._pool(available)
        if not pool:
            return None

        if self.is_night(now):
            target = self.night_outfit if self.night_outfit in pool else None
        else:
            season = self.season_override or season_of(now)
            target = self._pick_for_season(season, pool, now)

        if target is None or target == self.current:
            return None

        # 距上次换装太近、且时段与季节都没变 → 保持不动，别每句话都换
        last = self._parse(self.cfg.get("wardrobe.last_switch_at", ""))
        if not ignore_gap and last is not None and self.min_gap_min > 0:
            elapsed = (now - last).total_seconds() / 60.0
            same_bucket = str(self.cfg.get("wardrobe.last_bucket", "")) == self._bucket(now)
            same_season = str(self.cfg.get("wardrobe.last_season", "")) == season_of(now)
            if elapsed < self.min_gap_min and same_bucket and same_season:
                return None
        return target

    def _pool(self, available: set[str] | None) -> set[str]:
        keys = set(self.table.keys())
        return keys & available if available else keys

    def _pick_for_season(self, season: str, pool: set[str], now: datetime) -> str | None:
        """当季常服里挑一套。"""
        candidates = [
            key for key in sorted(pool, key=lambda k: int(k) if str(k).isdigit() else 99)
            if self.table.get(key, {}).get("random")
            and season in self.table.get(key, {}).get("seasons", [])
        ]
        if not candidates:
            # 该季没有"随机"的衣服：退一步，允许当季任何一套（含礼服）
            candidates = [
                key for key in sorted(pool, key=lambda k: int(k) if str(k).isdigit() else 99)
                if season in self.table.get(key, {}).get("seasons", [])
            ]
        if not candidates:
            return None
        if self.current in candidates and len(candidates) == 1:
            return self.current
        if self.random_in_season:
            others = [c for c in candidates if c != self.current]
            return random.choice(others or candidates)
        return candidates[0]

    def _bucket(self, now: datetime) -> str:
        """粗时段：判断"时段变了没"，用 part_of_day 就够。"""
        return part_of_day(now)

    def _manual_locked(self, now: datetime) -> bool:
        until = self._parse(self.cfg.get("wardrobe.manual_hold_until", ""))
        return until is not None and now < until

    # ---- 状态更新 ---------------------------------------------------------
    def mark_switched(self, outfit: str, now: datetime | None = None) -> None:
        """换装完成后记账：写回配置，作为下次的"上次换装时间/时段/季节"。"""
        now = now or now_local()
        self.current = str(outfit)
        self.cfg.set("wardrobe.last_switch_at", now.isoformat(timespec="seconds"))
        self.cfg.set("wardrobe.last_bucket", self._bucket(now))
        self.cfg.set("wardrobe.last_season", season_of(now))

    def note_manual(self, outfit: str, now: datetime | None = None) -> None:
        """
        你手动换了衣服：记一个"手动保护期"，这段时间内自动换装不插手。
        """
        now = now or now_local()
        self.current = str(outfit)
        if self.manual_hold_min > 0:
            until = now.timestamp() + self.manual_hold_min * 60
            self.cfg.set(
                "wardrobe.manual_hold_until",
                datetime.fromtimestamp(until).isoformat(timespec="seconds"),
            )
            self.cfg.save()

    def describe(self, now: datetime | None = None) -> str:
        """一句话说明现在的穿着状态（托盘提示、状态行用）。"""
        now = now or now_local()
        when = "夜间" if self.is_night(now) else SEASON_NAMES.get(
            self.season_override or season_of(now), ""
        )
        if self._manual_locked(now):
            return f"{when}·{self.name_of(self.current)}（你手动换的，暂时不自动改）"
        return f"{when}·{self.name_of(self.current)}"
