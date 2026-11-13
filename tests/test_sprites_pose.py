# -*- coding: utf-8 -*-
"""
立绘「造型 + 动作」的解析测试。

背景（改代码前先看这段）：
    文件名是 lun_<尺寸族>_<造型a>_<动作b>_<表情编号>.png
    a＝穿什么（造型），b＝怎么站（动作：0 抬手 / 2 抱臂）。
    "按情绪换动作但不换衣服"＝只改 b；某个组合没有素材时（造型⑦只有抬手、
    近景族 l 没有造型④⑥）必须保持原样，绝不能切出一张空白图。

跑法：
    .venv\\Scripts\\python.exe -m pytest tests -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import mood                       # noqa: E402
from app.config import Config              # noqa: E402
from app.sprites import SpriteLibrary      # noqa: E402


@pytest.fixture(scope="module")
def library(tmp_path_factory) -> SpriteLibrary:
    """用临时配置目录，避免污染项目里的 config/config.json。"""
    cfg = Config(path=tmp_path_factory.mktemp("cfg") / "config.json")
    lib = SpriteLibrary(cfg)
    if not lib.catalog.get("groups"):
        pytest.skip("没有找到 assets/sprite_catalog.json，先运行 tools/scan_sprites.py")
    return lib


def test_outfits_and_poses(library: SpriteLibrary):
    assert library.outfits == ["0", "1", "2", "3", "4", "5", "6"]
    assert library.poses_for("0") == ["0", "2"]
    # 造型⑦（6）在素材里只有抬手，没有抱臂
    assert library.poses_for("6") == ["0"]


def test_every_emotion_resolves_in_every_pose(library: SpriteLibrary):
    """每套造型 × 每个实际存在的动作 × 每个尺寸族 × 12 情绪，都必须落到真实存在的编号。"""
    for outfit in library.outfits:
        for pose in library.poses_for(outfit):
            group = library.group_key(outfit, pose)
            for family in library.families:
                available = library.available(family, group)
                if not available:
                    continue
                for emotion in mood.EMOTIONS:
                    idx = library.resolve_index(emotion, family, group)
                    assert idx in available, f"{group}/{family} 的 {emotion} 解析出了不存在的 {idx}"


def test_pose_table_is_used_for_pose_two(library: SpriteLibrary):
    """抱臂组要读 pose_candidates，不能被抬手组的表带偏。"""
    table = library.expressions["pose_candidates"]["2"]
    for emotion, candidates in table.items():
        idx = library.resolve_index(emotion, "s", "0_2")
        assert idx in candidates, f"{emotion} 没按抱臂表解析（得到 {idx}，表里是 {candidates}）"


def test_default_group_falls_back_when_pose_missing(library: SpriteLibrary):
    # 造型⑦没有抱臂，默认姿势只能回到 6_0
    assert library.default_group("6", "s") == "6_0"
    # 造型①有抬手，默认姿势就是 1_0
    assert library.default_group("1", "s") == "1_0"


def test_pose_usable_guards_missing_material(library: SpriteLibrary):
    assert library.pose_usable("s", "0", "2") is True
    assert library.pose_usable("s", "6", "2") is False
    # 近景族 l 没有造型④
    assert library.pose_usable("l", "4", "0") is False


def test_emotion_pose_values_are_valid(library: SpriteLibrary):
    labels = library.expressions["pose_labels"]
    for emotion, pose in library.expressions["emotion_pose"].items():
        assert emotion in mood.EMOTIONS, f"emotion_pose 里出现了未知情绪：{emotion}"
        assert str(pose) in labels, f"{emotion} 的动作 {pose} 没有在 pose_labels 里登记"


def test_pose_candidates_cover_all_emotions(library: SpriteLibrary):
    table = library.expressions["pose_candidates"]["2"]
    for emotion in mood.EMOTIONS:
        assert emotion in table, f"pose_candidates['2'] 缺少 {emotion}"
        assert table[emotion], f"{emotion} 的抱臂候选是空的"


def test_closed_faces_exist_in_each_pose(library: SpriteLibrary):
    for group in ("0_0", "0_2"):
        faces = library.closed_faces("s", group)
        assert faces, f"{group} 找不到可用于眨眼的闭眼表情"
        available = library.available("s", group)
        assert set(faces).issubset(available)


def test_expressions_file_has_pose_section():
    data = json.loads((ROOT / "assets" / "expressions.json").read_text(encoding="utf-8"))
    assert data["default_pose"] in data["pose_labels"]
    assert isinstance(data["pose_candidates"]["2"], dict)
