# -*- coding: utf-8 -*-
"""
记忆卡存储（data/memory.json）。

这是记忆的"权威源"：所有事实都明文写在 JSON 里，用户可以直接打开看、直接改、直接删。
向量库（ChromaDB）只是它的索引，删掉也能靠这个文件一键重建。

一条事实长这样：
    {"id": "f_0001", "text": "用户在做露娜桌宠", "tags": ["项目"], "weight": 1.0,
     "created_at": "...", "last_hit_at": "...", "pinned": false, "vec_state": "ok"}

写入口有三条：
    1. 用户在对话里说「记住：……」  → add_fact(立即)
    2. 每 N 轮由模型自动提炼       → add_fact(批量)
    3. 记忆面板里手动添加          → add_fact(手动)
"""
from __future__ import annotations

import functools
import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from ..config import DATA_DIR

MEMORY_FILE = DATA_DIR / "memory.json"
DEFAULT_MEMORY: dict = {
    "version": 1,
    "profile": {
        "user_name": "",
        "likes": [],
        "dislikes": [],
        "affinity": 0,        # 亲密度，聊得越多越高（0-100），目前只用于调味
    },
    "facts": [],
    "summaries": [],
    "index": {
        "backend": "",
        "embedding": "",
        "dim": 0,
        "fingerprint": "",
    },
}


def normalize_text(text: str) -> str:
    """去空白、去标点、转小写，用于判重。"""
    return re.sub(r"[\s\u3000，。！？、；：,.!?;:\"'（）()【】\[\]…~～\-]+", "", (text or "").lower())


def _grams(text: str) -> set[str]:
    """中文按 2-gram 切，英文/数字按词切，用于估算两句话有多像。"""
    norm = normalize_text(text)
    grams = {norm[i:i + 2] for i in range(len(norm) - 1)} if len(norm) > 1 else {norm}
    grams.update(re.findall(r"[a-z0-9]{2,}", norm))
    return {g for g in grams if g}


def similarity(a: str, b: str) -> float:
    """2-gram Jaccard 相似度，0~1。"""
    ga, gb = _grams(a), _grams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def _locked(method):
    """
    给"会改数据"的方法套上 self._lock 的小装饰器。

    写成装饰器而不是在每个方法体里写 `with self._lock:`，是为了不动原有缩进、
    以后加新方法只要贴一行 @_locked 就行。锁是可重入的，方法内部再调 save() 不会死锁。
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MemoryStore:
    """
    记忆文件的读写封装。

    线程安全：所有读写都在 self._lock（可重入锁）里做——因为事实有三个写入方
    （对话里的"记住"、每 N 轮的自动提炼、记忆面板的手动添加），其中自动提炼跑在
    后台线程，可能和界面线程同时写，没锁会丢数据或把 JSON 写坏。
    """

    def __init__(self, path: Path | None = None, max_facts: int = 2000) -> None:
        self.path = Path(path) if path else MEMORY_FILE
        self.max_facts = max_facts
        self._lock = threading.RLock()
        self.data: dict = json.loads(json.dumps(DEFAULT_MEMORY))  # 深拷贝默认结构
        self.load_error = ""
        self.load()

    # ---- 读写 -------------------------------------------------------------
    def load(self) -> None:
        with self._lock:
            self._load_unlocked()

    def _load_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save()
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for key, value in DEFAULT_MEMORY.items():
                raw.setdefault(key, json.loads(json.dumps(value)))
            self.data = raw
        except Exception as exc:  # noqa: BLE001 记忆文件坏了也要能启动
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = self.path.with_suffix(f".json.bak.{stamp}")
            try:
                self.path.replace(backup)
                self.load_error = f"记忆文件损坏（{exc}），已备份到 {backup.name} 并重建空记忆。"
            except Exception:  # noqa: BLE001
                self.load_error = f"记忆文件损坏（{exc}），已重建空记忆。"
            self.data = json.loads(json.dumps(DEFAULT_MEMORY))
            self.save()

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)

    # ---- 事实卡 -----------------------------------------------------------
    def facts(self) -> list[dict]:
        return self.data["facts"]

    def next_id(self) -> str:
        used = {f["id"] for f in self.data["facts"]}
        n = len(self.data["facts"]) + 1
        while f"f_{n:04d}" in used:
            n += 1
        return f"f_{n:04d}"

    def find_duplicate(self, text: str, threshold: float = 0.85) -> dict | None:
        """找有没有说过几乎一样的话，避免记忆里堆重复内容。"""
        for fact in self.data["facts"]:
            if similarity(fact["text"], text) >= threshold:
                return fact
        return None

    def add_fact(self, text: str, tags: list[str] | None = None, weight: float = 1.0,
                 pinned: bool = False, source: str = "") -> dict | None:
        """
        新增一条记忆。返回新事实；如果和已有记忆重复，返回 None（只会加重权重）。
        """
        text = (text or "").strip()
        if len(text) < 2:
            return None

        dup = self.find_duplicate(text)
        if dup:
            dup["weight"] = min(3.0, float(dup.get("weight", 1.0)) + 0.2)
            dup["last_hit_at"] = _now()
            self.save()
            return None

        fact = {
            "id": self.next_id(),
            "text": text,
            "tags": list(tags or []),
            "weight": float(weight),
            "created_at": _now(),
            "last_hit_at": _now(),
            "pinned": bool(pinned),
            "vec_state": "pending",   # 还没算向量；embedder 处理完会改成 ok
            "source": source,
        }
        self.data["facts"].append(fact)
        self._maybe_compress()
        self.save()
        return fact

    def update_fact(self, fact_id: str, **fields) -> dict | None:
        for fact in self.data["facts"]:
            if fact["id"] == fact_id:
                fact.update(fields)
                self.save()
                return fact
        return None

    def remove_fact(self, fact_id: str) -> bool:
        before = len(self.data["facts"])
        self.data["facts"] = [f for f in self.data["facts"] if f["id"] != fact_id]
        changed = len(self.data["facts"]) != before
        if changed:
            self.save()
        return changed

    def remove_by_keyword(self, keyword: str) -> list[str]:
        """按关键词删记忆（"忘掉代码"这类指令用）。返回被删掉的 id 列表。"""
        keyword = (keyword or "").strip()
        if not keyword:
            return []
        hit = [f for f in self.data["facts"] if keyword in f["text"]]
        if not hit:
            return []
        ids = [f["id"] for f in hit]
        self.data["facts"] = [f for f in self.data["facts"] if f["id"] not in ids]
        self.save()
        return ids

    def touch(self, fact_ids: list[str]) -> None:
        """记录"这条记忆刚被用到了"，用于新近度加权。"""
        now = _now()
        wanted = set(fact_ids)
        for fact in self.data["facts"]:
            if fact["id"] in wanted:
                fact["last_hit_at"] = now
        self.save()

    def pending_facts(self) -> list[dict]:
        """还没算向量的事实（比如当时没网、没填 Key）。"""
        return [f for f in self.data["facts"] if f.get("vec_state") != "ok"]

    # ---- 用户画像 ---------------------------------------------------------
    @property
    def profile(self) -> dict:
        return self.data["profile"]

    def update_profile(self, **fields) -> None:
        self.data["profile"].update(fields)
        self.save()

    def bump_affinity(self, delta: int = 1) -> int:
        value = max(0, min(100, int(self.data["profile"].get("affinity", 0)) + delta))
        self.data["profile"]["affinity"] = value
        self.save()
        return value

    # ---- 摘要与压缩 -------------------------------------------------------
    def add_summary(self, text: str, date_range: str = "") -> dict:
        summary = {
            "id": f"s_{len(self.data['summaries']) + 1:04d}",
            "range": date_range,
            "text": text.strip(),
            "created_at": _now(),
        }
        self.data["summaries"].append(summary)
        self.save()
        return summary

    def _maybe_compress(self) -> None:
        """事实太多时，把最旧、权重最低的 50 条压成一条摘要，控制文件体积。"""
        facts = self.data["facts"]
        if len(facts) <= self.max_facts:
            return
        victims = sorted(
            [f for f in facts if not f.get("pinned")],
            key=lambda f: (float(f.get("weight", 1.0)), f.get("created_at", "")),
        )[:50]
        if not victims:
            return
        victim_ids = {f["id"] for f in victims}
        merged = "；".join(f["text"] for f in victims)
        self.data["summaries"].append({
            "id": f"s_{len(self.data['summaries']) + 1:04d}",
            "range": f"{victims[0].get('created_at', '')[:10]} 及更早",
            "text": f"（已压缩的旧记忆）{merged}",
            "created_at": _now(),
        })
        self.data["facts"] = [f for f in facts if f["id"] not in victim_ids]

    # ---- 索引元信息 -------------------------------------------------------
    def index_meta(self) -> dict:
        return self.data.get("index", {})

    def set_index_meta(self, **fields) -> None:
        self.data.setdefault("index", {}).update(fields)
        self.save()

    def stats(self) -> dict:
        facts = self.data["facts"]
        return {
            "facts": len(facts),
            "pinned": sum(1 for f in facts if f.get("pinned")),
            "pending": sum(1 for f in facts if f.get("vec_state") != "ok"),
            "summaries": len(self.data["summaries"]),
            "affinity": self.data["profile"].get("affinity", 0),
        }


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
