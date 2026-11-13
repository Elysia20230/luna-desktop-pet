# -*- coding: utf-8 -*-
"""
角色卡（roles.json）的读取测试。

背景：她是"哪种露娜"取决于对面是谁（朝日 / 游星）。
角色卡是纯文本 json，用户随时可能手改，所以这里重点测"改坏了会怎样"：
不该崩，该退回内置角色，并给一句能看懂的提示。

跑法（在 LunaPet 目录下）：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import roles as roles_mod                    # noqa: E402
from app.config import Config                        # noqa: E402
from app.roles import Role, load_roles, pick_role    # noqa: E402


def _write_roles(path: Path, items: list[dict]) -> None:
    path.write_text(json.dumps({"roles": items}, ensure_ascii=False), encoding="utf-8")


def test_load_custom_roles_file(tmp_path: Path):
    card = tmp_path / "roles.json"
    _write_roles(card, [
        {"id": "asahi", "label": "小仓朝日", "rules": "调戏他。"},
        {"id": "yuusei", "label": "大藏游星", "rules": "对他撒娇。"},
    ])
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("persona.roles_file", str(card))

    roles, source, warning = load_roles(cfg)

    assert [r.id for r in roles] == ["asahi", "yuusei"]
    assert roles[0].label == "小仓朝日"
    assert source == str(card), "来源要指回这份文件，设置界面会显示它"
    assert warning == ""


def test_missing_file_falls_back_to_builtin(tmp_path: Path, monkeypatch):
    # 把默认的两个位置也指到空目录，模拟"一份角色卡都没有"
    monkeypatch.setattr(roles_mod, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(roles_mod, "LUNA_CHAT_ROLES", tmp_path / "Luna-Chat" / "roles.json")
    cfg = Config(path=tmp_path / "config.json")

    roles, source, warning = load_roles(cfg)

    assert source == "内置角色"
    assert [r.id for r in roles] == ["asahi", "yuusei"], "内置至少要有朝日和游星"
    assert warning == "", "内置兜底不算警告，不用拿它打扰用户"


def test_broken_json_falls_back_with_warning(tmp_path: Path):
    card = tmp_path / "roles.json"
    card.write_text("{ 这不是 json", encoding="utf-8")
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("persona.roles_file", str(card))

    roles, source, warning = load_roles(cfg)

    assert roles, "坏文件也要能用"
    assert source == "内置角色"
    assert "角色卡读取失败" in warning


def test_empty_roles_falls_back_with_warning(tmp_path: Path):
    card = tmp_path / "roles.json"
    card.write_text(json.dumps({"roles": []}), encoding="utf-8")
    cfg = Config(path=tmp_path / "config.json")
    cfg.set("persona.roles_file", str(card))

    roles, _source, warning = load_roles(cfg)

    assert roles
    assert "没有可用条目" in warning


def test_pick_role_by_id_and_unknown(tmp_path: Path):
    roles = [
        Role(id="asahi", label="小仓朝日", rules="A"),
        Role(id="yuusei", label="大藏游星", rules="B"),
    ]

    assert pick_role(roles, "yuusei").label == "大藏游星"
    assert pick_role(roles, "没这个人").id == "asahi", "未知 id 回落第一条"
    assert pick_role(roles, "").id == "asahi"
