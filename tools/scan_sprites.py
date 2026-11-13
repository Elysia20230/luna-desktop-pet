# -*- coding: utf-8 -*-
"""
扫描立绘素材目录，生成 assets/sprite_catalog.json。

为什么需要这个脚本？
    素材文件夹里有 600 多张 png，文件名形如  lun_s_0_0_07.png
    拆开来是：前缀 lun_ + 尺寸族 + 服装组 a + 服装组 b + 表情编号
    桌宠运行时如果每次都去遍历磁盘会很慢，所以这里先扫一遍存成清单文件，
    程序启动时只读这个清单即可。

用法（在本目录 LunaPet 下执行）：
    python tools/scan_sprites.py            # 默认扫描 ..\\Sprite
    python tools/scan_sprites.py D:\\某个目录  # 也可以手动指定

输出： assets/sprite_catalog.json
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from PIL import Image  # Pillow：用来读取图片尺寸与透明通道信息

# Windows 控制台默认是 GBK，中文输出会变乱码，这里统一改成 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

# 文件名规则：lun_<尺寸族>_<a>_<b>_<表情编号>.png
# 例：lun_s_0_0_07.png  -> 尺寸族 s，服装组 0_0，表情 07
NAME_RE = re.compile(r"^lun_([fslm])_(\d+)_(\d+)_(\d+)\.png$", re.IGNORECASE)

# 项目根目录（本文件在 LunaPet/tools/ 下，所以往上两级）
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPRITE_DIR = ROOT.parent / "Sprite"    # 工作区里的 Sprite 文件夹
OUT_FILE = ROOT / "assets" / "sprite_catalog.json"


def scan(sprite_dir: Path) -> dict:
    """遍历目录，返回可序列化的清单字典。"""
    families: dict[str, dict] = {}          # 尺寸族 -> {count, sizes}
    groups: dict[str, dict] = {}            # 服装组 -> {尺寸族 -> {表情编号 -> 文件名}}
    warnings: list[str] = []                # 命名不规范 / 无法读取的文件，记录下来人工看
    skipped = 0

    files = sorted(sprite_dir.glob("*.png")) + sorted(sprite_dir.glob("*.PNG"))
    # 去重（Windows 文件名大小写不敏感，两次 glob 可能拿到同一批）
    uniq: dict[str, Path] = {p.name.lower(): p for p in files}
    files = list(uniq.values())

    for path in files:
        m = NAME_RE.match(path.name)
        if not m:
            warnings.append(f"命名不符合规则，已跳过：{path.name}")
            skipped += 1
            continue

        family = m.group(1).lower()
        group = f"{m.group(2)}_{m.group(3)}"
        idx = int(m.group(4))

        # 读一下真实尺寸；同时确认是否带透明通道（立绘必须是透明背景才能抠到桌面上）
        try:
            with Image.open(path) as im:
                w, h = im.size
                has_alpha = im.mode in ("RGBA", "LA") or "transparency" in im.info
        except Exception as exc:  # noqa: BLE001 素材损坏时不要让整个脚本挂掉
            warnings.append(f"无法读取，已跳过：{path.name} ({exc})")
            skipped += 1
            continue

        if not has_alpha:
            warnings.append(f"没有透明通道（放桌面上会是白底）：{path.name}")

        fam = families.setdefault(family, {"count": 0, "sizes": []})
        fam["count"] += 1
        if [w, h] not in fam["sizes"]:
            fam["sizes"].append([w, h])

        groups.setdefault(group, {}).setdefault(family, {})[str(idx)] = path.name

    catalog = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_dir": str(sprite_dir),
        "file_count": sum(f["count"] for f in families.values()),
        "skipped": skipped,
        "families": families,
        "groups": groups,
        "warnings": warnings,
    }
    return catalog


def main() -> int:
    sprite_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPRITE_DIR
    if not sprite_dir.is_dir():
        print(f"[错误] 找不到素材目录：{sprite_dir}")
        print("       请把参数写成 Sprite 文件夹的实际路径，例如：")
        print(r"       python tools/scan_sprites.py <你的立绘目录>")
        return 1

    catalog = scan(sprite_dir)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"素材目录：{sprite_dir}")
    print(f"共 {catalog['file_count']} 张立绘，跳过 {catalog['skipped']} 个不规范文件")
    for family, info in sorted(catalog["families"].items()):
        sizes = " / ".join(f"{w}x{h}" for w, h in info["sizes"])
        print(f"  尺寸族 {family}: {info['count']} 张，尺寸 {sizes}")
    print(f"服装组共 {len(catalog['groups'])} 组：")
    for group, fams in sorted(catalog["groups"].items()):
        detail = "，".join(f"{fam}:{len(idxs)}张" for fam, idxs in sorted(fams.items()))
        print(f"  {group}  ->  {detail}")
    if catalog["warnings"]:
        print(f"警告 {len(catalog['warnings'])} 条（详见 {OUT_FILE.name} 的 warnings 字段）")
    print(f"清单已写入：{OUT_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
