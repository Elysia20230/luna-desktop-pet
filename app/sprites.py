# -*- coding: utf-8 -*-
"""
立绘：素材清单、情绪→表情编号的解析、以及 QPixmap 缓存。

素材文件名规则（由 tools/scan_sprites.py 扫描后写进 assets/sprite_catalog.json）：
    lun_<尺寸族>_<造型a>_<动作b>_<表情编号>.png
    例：lun_s_0_2_07.png = 半身(s) 第 0 套造型、抱臂动作(2)、第 07 号表情
    a = 穿什么（造型），b = 怎么站（动作：0 抬手 / 2 抱臂）——"换动作不换衣服"就是只改 b。

尺寸族含义：
    f = 头部特写（约 300x290）
    s = 半身（约 732x819，默认，最像"桌宠立绘"）
    m = 中景（约 876x886）
    l = 近景大头（约 1211x1197）

情绪→编号的对应关系放在 assets/expressions.json，按键值顺序取第一个"该服装确实有"的编号，
因此某套服装缺某个表情时会自动退到备选，不会出现空白立绘。
"""
from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QTransform

from .config import ASSETS_DIR, ROOT

CATALOG_FILE = ASSETS_DIR / "sprite_catalog.json"
EXPRESSIONS_FILE = ASSETS_DIR / "expressions.json"
OUTFITS_FILE = ASSETS_DIR / "outfits.json"

FAMILY_NAMES = {
    "f": "头部特写",
    "s": "半身",
    "m": "中景",
    "l": "近景",
}

# 13 套服装的默认显示名（可在 assets/outfits.json 里改名，不影响逻辑）
DEFAULT_OUTFIT_LABELS = {
    "0_0": "造型①", "0_2": "造型②", "1_0": "造型③", "1_2": "造型④",
    "2_0": "造型⑤", "2_2": "造型⑥", "3_0": "造型⑦", "3_2": "造型⑧",
    "4_0": "造型⑨", "4_2": "造型⑩", "5_0": "造型⑪", "5_2": "造型⑫",
    "6_0": "造型⑬",
}


class SpriteLibrary:
    """只负责"哪张图该显示"的逻辑，不碰界面。"""

    def __init__(self, config) -> None:
        self.config = config
        self.catalog: dict = {}
        self.expressions: dict = {}
        self.outfit_labels: dict = dict(DEFAULT_OUTFIT_LABELS)
        self.warnings: list[str] = []
        self.reload()

    # ---- 载入 -------------------------------------------------------------
    def reload(self) -> None:
        self.catalog = self._read_json(CATALOG_FILE, {})
        self.expressions = self._read_json(EXPRESSIONS_FILE, {})
        custom = self._read_json(OUTFITS_FILE, None)
        if isinstance(custom, dict):
            self.outfit_labels.update({k: str(v) for k, v in custom.items()})

        # 素材目录：配置里指定的优先，其次是扫描时记下的路径
        cfg_dir = self.config.sprite_dir()
        recorded = str(self.catalog.get("source_dir") or "").strip()
        self.sprite_dir = cfg_dir or (Path(recorded) if recorded else None)
        if self.sprite_dir is None or not Path(self.sprite_dir).is_dir():
            self.warnings.append(
                "没找到立绘：把素材包解压到 LunaPet\\Sprite（和 LunaPet.exe 同一个文件夹），"
                "或在 设置 → 立绘 → 选目录。"
            )

    @staticmethod
    def _read_json(path: Path, fallback):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 文件缺失/损坏时用兜底值，界面会给出提示
            return fallback

    # ---- 查询 -------------------------------------------------------------
    @property
    def groups(self) -> list[str]:
        return sorted(self.catalog.get("groups", {}).keys())

    @property
    def families(self) -> list[str]:
        fams = self.catalog.get("families", {})
        order = ["f", "s", "m", "l"]
        return [f for f in order if f in fams] or ["s"]

    def entries(self, family: str, group: str) -> dict[int, str]:
        """返回 {表情编号: 文件名}，某套服装缺某个表情时不会出现在这里。"""
        raw = self.catalog.get("groups", {}).get(group, {}).get(family, {})
        return {int(k): v for k, v in raw.items()}

    def available(self, family: str, group: str) -> list[int]:
        return sorted(self.entries(family, group).keys())

    def labels_for(self, family: str, group: str) -> dict[int, str]:
        """每个表情编号的可读说明（来自 expressions.json 的 index_labels）。"""
        marks = self.expressions.get("index_labels", {})
        return {idx: marks.get(str(idx), f"表情{idx:02d}") for idx in self.available(family, group)}

    # ---- 造型（衣服）与动作（姿势） ---------------------------------------
    # 组名是 a_b：a 决定穿什么，b 决定怎么站/怎么摆手（0 抬手，2 抱臂）。
    # 换动作时只动 b，a 保持不变 —— 这就是"换动作不换衣服"。
    @staticmethod
    def outfit_of(group: str) -> str:
        return str(group).split("_")[0]

    @staticmethod
    def pose_of(group: str) -> str:
        parts = str(group).split("_")
        return parts[1] if len(parts) > 1 else "0"

    @property
    def outfits(self) -> list[str]:
        """所有造型编号（去重后的 a），按出现顺序。"""
        seen: list[str] = []
        for group in self.groups:
            outfit = self.outfit_of(group)
            if outfit not in seen:
                seen.append(outfit)
        return seen

    def poses_for(self, outfit: str) -> list[str]:
        """这套造型在素材里实际有哪些动作。"""
        poses = [self.pose_of(g) for g in self.groups if self.outfit_of(g) == outfit]
        return sorted(set(poses))

    def group_key(self, outfit: str, pose: str) -> str:
        return f"{outfit}_{pose}"

    def pose_usable(self, family: str, outfit: str, pose: str) -> bool:
        """这个「造型 + 动作 + 尺寸族」组合有没有素材（没素材就别切，免得一片空白）。"""
        return bool(self.available(family, self.group_key(outfit, pose)))

    def default_group(self, outfit: str, family: str) -> str:
        """
        恢复到默认姿势时的组名：优先 default_pose，没有就退这套造型里任意可用动作，
        再不行就用 a_0（refresh() 里还有一层兜底）。
        """
        want = str(self.expressions.get("default_pose", "0"))
        if want not in self.poses_for(outfit):
            want = "0"
        if not self.pose_usable(family, outfit, want):
            for pose in self.poses_for(outfit):
                if self.pose_usable(family, outfit, pose):
                    return self.group_key(outfit, pose)
        return self.group_key(outfit, want)

    def pose_for(self, emotion: str) -> str:
        """这个情绪习惯配哪个动作（在 expressions.json 的 emotion_pose 里改）。"""
        table = self.expressions.get("emotion_pose", {})
        value = table.get(emotion)
        if isinstance(value, str) and value in self.expressions.get("pose_labels", {}):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        return str(self.expressions.get("default_pose", "0"))

    def pose_label(self, pose: str) -> str:
        return self.expressions.get("pose_labels", {}).get(str(pose), f"动作{pose}")

    def outfit_name(self, outfit: str) -> str:
        """造型的可读名字：取这套造型第一个组的名字（兼容旧的 outfits.json）。"""
        for pose in self.poses_for(outfit):
            label = self.outfit_labels.get(self.group_key(outfit, pose))
            if label:
                return label
        return f"造型{outfit}"

    # ---- 情绪 → 表情编号 --------------------------------------------------
    def resolve_index(self, emotion: str, family: str, group: str) -> int:
        """把情绪词解析成该服装组里真实存在的表情编号（带三级回退）。"""
        idxs = self.available(family, group)
        if not idxs:
            return 0

        candidates: list[int] = []

        # ① 服装组覆盖表：优先用专门给这套衣服配的编号
        override = self.expressions.get("groups", {}).get(group, {})
        if isinstance(override.get(emotion), list):
            candidates += [int(i) for i in override[emotion]]
        elif isinstance(override.get(emotion), int):
            candidates.append(int(override[emotion]))

        # ② 动作专属表：抱臂（b=2）那套的表情编号语义和抬手不同，单独一张表
        pose = self.pose_of(group)
        if pose != "0":
            pose_table = self.expressions.get("pose_candidates", {}).get(pose, {})
            pose_list = pose_table.get(emotion)
            if isinstance(pose_list, list):
                candidates += [int(i) for i in pose_list]

        # ③ 通用表（动作 0 的语义）
        base = self.expressions.get("candidates", {}).get(emotion)
        if isinstance(base, list):
            candidates += [int(i) for i in base]

        for idx in candidates:
            if idx in idxs:
                return idx

        # ④ 全都没命中：取离第一个候选最近的可用编号，兜底用组里最小的那个
        anchor = candidates[0] if candidates else 0
        return min(idxs, key=lambda i: abs(i - anchor))

    def closed_faces(self, family: str, group: str) -> list[int]:
        """可用于"眨眼"的闭眼表情编号（不同动作的闭眼编号不一样）。"""
        by_pose = self.expressions.get("closed_indices_by_pose", {})
        pose_list = by_pose.get(self.pose_of(group))
        if isinstance(pose_list, list):
            closed = {int(i) for i in pose_list}
        else:
            closed = {int(i) for i in self.expressions.get("closed_indices", [])}
        return [i for i in self.available(family, group) if i in closed]

    def path(self, family: str, group: str, idx: int) -> Path | None:
        name = self.entries(family, group).get(idx)
        if not name or not self.sprite_dir:
            return None
        return Path(self.sprite_dir) / name

    def outfit_label(self, group: str) -> str:
        return self.outfit_labels.get(group, group)


class SpriteCache:
    """
    把"某个服装 + 某个表情 + 某个缩放比例"渲染好的 QPixmap 缓存起来，
    避免每次换表情都重新解码 700KB 的 png（素材总共 420MB，绝不能全读进内存）。
    最多存 40 张，超出后丢掉最久没用的（LRU）。
    """

    def __init__(self, library: SpriteLibrary, capacity: int = 40) -> None:
        self.library = library
        self.capacity = capacity
        self._cache: OrderedDict[tuple, QPixmap] = OrderedDict()

    def clear(self) -> None:
        self._cache.clear()

    def get(self, family: str, group: str, idx: int, scale: float, dpr: float = 1.0) -> QPixmap | None:
        """取一张缩放好的立绘。scale 是用户设定的显示比例，dpr 是屏幕缩放系数。"""
        key = (family, group, idx, round(scale, 3), round(dpr, 2))
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        path = self.library.path(family, group, idx)
        if not path or not path.exists():
            return None

        raw = QPixmap(str(path))
        if raw.isNull():
            return None

        # 按"实际像素"缩放：dpr 让我们在高 DPI 屏上也能保持清晰
        target_w = max(1, int(raw.width() * scale * dpr))
        target_h = max(1, int(raw.height() * scale * dpr))
        if (target_w, target_h) != (raw.width(), raw.height()):
            raw = raw.transformed(
                QTransform().scale(target_w / raw.width(), target_h / raw.height()),
                Qt.SmoothTransformation,
            )
        # 告诉 Qt 这张图对应的屏幕缩放比，避免被再次拉伸
        raw.setDevicePixelRatio(dpr)

        self._cache[key] = raw
        if len(self._cache) > self.capacity:
            self._cache.popitem(last=False)     # 丢掉最久没用的那张
        return raw
