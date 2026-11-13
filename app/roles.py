# -*- coding: utf-8 -*-
"""
「你在我眼里是谁」——角色卡加载。

同一只露娜，面对不同的人脾气完全不一样：面对女装混进学院的朝日，她是
抖S恋人；面对婚后本名的游星，她是妻子。这些差别不该写死在提示词里，
而是放进一份角色卡（roles.json），界面上选一下就能换。

角色卡长这样（可以照抄自己的）：
    {
      "roles": [
        {"id": "asahi", "label": "小仓朝日", "user_avatar": "", "rules": "……"}
      ]
    }

找文件的顺序（前面的优先）：
    1. 设置里指定的路径（config: persona.roles_file）
    2. assets/roles.json           ← 推荐：自己改这份
    3. C:\\Users\\<你>\\Luna-Chat\\shared\\roles.json
    4. 内置的朝日 / 游星两份精简版（连文件都没有时也能用）
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .config import ASSETS_DIR, ROOT

# 找不到任何角色卡时，至少还能在这两个人之间选
BUILTIN_ROLES: list[dict] = [
    {
        "id": "asahi",
        "label": "小仓朝日",
        "user_avatar": "",
        "rules": (
            "你面前的人是**小仓朝日**——化着女装潜入学院、成为你贴身女仆的男人，"
            "也是你最好的版型师、你的抖S专属受害者、你的恋人。\n"
            "你叫他「朝日」，他叫你「露娜大人」。\n"
            "对待他的方式：尽情调戏，看他慌张是你每天的娱乐；"
            "偶尔在最意想不到的时刻给一句真的温柔；绝不让给别人。"
        ),
    },
    {
        "id": "yuusei",
        "label": "大藏游星",
        "user_avatar": "",
        "rules": (
            "你面前的人是**大藏游星**——脱下女装后真实的他，你的丈夫、孩子的父亲、"
            "你此生最重要的搭档。你们已结婚，现居纽约。\n"
            "你叫他「游星」或「亲爱的」，他叫你「露娜」。\n"
            "对待他的方式：妻子的从容，骄傲还在但多了只给他看的甜腻；"
            "设计是你的、打版是他的；可以对他撒娇，方式仍是你的风格。"
        ),
    },
]

# 家里那份角色卡（用户的另一个项目），存在就当第三优先级
LUNA_CHAT_ROLES = Path.home() / "Luna-Chat" / "shared" / "roles.json"


@dataclass
class Role:
    """一条角色卡：她的「对面是谁」。"""

    id: str
    label: str
    rules: str
    user_avatar: str = ""

    @property
    def name(self) -> str:
        """给台词占位用的短名字：优先取「小仓朝日」这类标签本身。"""
        return self.label or self.id


def _parse_roles(data) -> list[Role]:
    """
    把 json 里的 roles 数组转成 Role 列表。
    单条缺字段就跳过那一条——一份烂角色卡不应该让整个程序打不开。
    """
    items = data.get("roles") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    roles: list[Role] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        role_id = str(item.get("id") or "").strip()
        label = str(item.get("label") or "").strip()
        rules = str(item.get("rules") or "").strip()
        if not role_id or not rules:
            continue
        roles.append(
            Role(
                id=role_id,
                label=label or role_id,
                rules=rules,
                user_avatar=str(item.get("user_avatar") or ""),
            )
        )
    return roles


def _candidate_files(cfg=None) -> list[Path]:
    """按优先级列出可能存在的角色卡路径。"""
    paths: list[Path] = []
    if cfg is not None:
        custom = str(cfg.get("persona.roles_file", "") or "").strip()
        if custom:
            paths.append(Path(custom).expanduser())
    paths.append(ASSETS_DIR / "roles.json")
    paths.append(LUNA_CHAT_ROLES)
    return paths


def load_roles(cfg=None) -> tuple[list[Role], str, str]:
    """
    读角色卡。

    返回 (角色列表, 来源说明, 警告文字)。
    来源说明会显示在设置界面的提示行上，方便你知道现在用的是哪份文件。
    """
    for path in _candidate_files(cfg):
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8")
            roles = _parse_roles(json.loads(raw))
        except Exception as exc:  # noqa: BLE001 坏文件不该弄崩程序
            return _builtin(f"角色卡读取失败（{path.name}：{exc}），已改用内置角色。")
        if roles:
            return roles, str(path), ""
        return _builtin(f"角色卡里没有可用条目（{path.name}），已改用内置角色。")
    return _builtin("")


def _builtin(warning: str) -> tuple[list[Role], str, str]:
    return _parse_roles({"roles": BUILTIN_ROLES}), "内置角色", warning


def pick_role(roles: list[Role], role_id: str) -> Role:
    """按 id 选角色；id 空了或者对不上，就退回第一条（默认朝日）。"""
    wanted = (role_id or "").strip()
    for role in roles:
        if role.id == wanted:
            return role
    return roles[0]


def roles_dir_hint() -> Path:
    """给文档/提示用：默认角色卡该放在哪。"""
    return ROOT / "assets" / "roles.json"
