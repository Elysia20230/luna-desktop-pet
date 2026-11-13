# -*- coding: utf-8 -*-
"""
历史里的时间戳：新条目要带 at，旧文件要能兼容，别把老聊天记录读崩。

这里用假的 store/retriever/persona，只验证 engine 自己对 history 的处理。
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QCoreApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.chat import engine as engine_mod  # noqa: E402
from app.chat.engine import ChatEngine  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication([])


class _StubStore:
    def touch(self, ids):
        _ = ids

    def pending_facts(self):
        return []


class _StubRetriever:
    def retrieve(self, query, k=6):
        _ = query, k
        return []

    def format_for_prompt(self, hits):
        _ = hits
        return ""


class _StubPersona:
    role = None

    def system_prompt(self, **kwargs):
        return f"角色：{kwargs.get('now')}"


def _make(tmp_path: Path, monkeypatch) -> ChatEngine:
    history = tmp_path / "history.json"
    monkeypatch.setattr(engine_mod, "HISTORY_FILE", history)
    cfg = Config()
    return ChatEngine(cfg, _StubStore(), _StubRetriever(), _StubPersona())


def test_stamp_has_seconds_precision(tmp_path, monkeypatch):
    engine = _make(tmp_path, monkeypatch)
    row = engine._stamp("user", "在吗")
    assert row["role"] == "user" and row["content"] == "在吗"
    parsed = datetime.fromisoformat(row["at"])
    assert parsed.tzinfo is None           # 本地时间，不带时区
    assert len(row["at"]) == 19            # YYYY-MM-DDTHH:MM:SS


def test_old_history_without_at_is_tolerated(tmp_path, monkeypatch):
    history = tmp_path / "history.json"
    history.write_text(json.dumps([
        {"role": "user", "content": "老消息一"},
        {"role": "assistant", "content": "老消息二"},
    ], ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(engine_mod, "HISTORY_FILE", history)
    engine = ChatEngine(Config(), _StubStore(), _StubRetriever(), _StubPersona())
    assert len(engine.history) == 2
    # 老数据按文件修改时间兜底，不至于"从来没有聊过"
    assert all(str(row.get("at", "")).count("-") == 2 for row in engine.history)
    assert engine.last_talk_at is not None


def test_last_talk_from_history(tmp_path, monkeypatch):
    engine = _make(tmp_path, monkeypatch)
    engine.history = [
        engine._stamp("user", "上午好"),
        engine._stamp("assistant", "嗯。"),
    ]
    latest = datetime.fromisoformat(engine.history[-1]["at"])
    assert latest.date() == datetime.now().date()
    # _find_last_talk() 是"从 history 里推最近一次"的入口
    assert engine._find_last_talk() is not None


def test_time_pair_respects_switch(tmp_path, monkeypatch):
    engine = _make(tmp_path, monkeypatch)
    now, last = engine._time_pair()
    assert now is not None
    assert last is None                    # 没有历史
    engine.cfg.set("behavior.time_aware", False)
    now2, last2 = engine._time_pair()
    assert (now2, last2) == (None, None)


def test_save_history_keeps_at_field(tmp_path, monkeypatch):
    engine = _make(tmp_path, monkeypatch)
    engine.history = [engine._stamp("user", "记账")]
    engine._save_history()
    raw = json.loads(engine_mod.HISTORY_FILE.read_text(encoding="utf-8"))
    assert raw and raw[0]["content"] == "记账"
    assert "at" in raw[0]
    assert time.time() > 0
