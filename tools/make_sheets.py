# -*- coding: utf-8 -*-
"""
把同一套服装的所有表情拼成一张"对照表"（contact sheet），方便人眼核对。

桌宠要按情绪换表情，但素材文件名里只有数字编号，没有"这是害羞"这种标签。
所以先拼图，一张图看完全部表情，人工把 编号 -> 情绪 的对应关系填进
assets/expressions.json。

用法（在本目录 LunaPet 下执行）：
    python tools/make_sheets.py                # 生成所有服装组（默认尺寸族 s）
    python tools/make_sheets.py --family s --out D:\\临时目录
    python tools/make_sheets.py --group 0_0    # 只生成某一组

输出： assets/sheets/<服装组>.png
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "assets" / "sprite_catalog.json"
DEFAULT_OUT = ROOT / "assets" / "sheets"

CELL_W, CELL_H = 380, 430     # 每个格子的大小（图 + 编号文字）
LABEL_H = 26                  # 顶部编号文字占的高度

# Windows 控制台默认是 GBK，中文输出会变乱码，这里统一改成 UTF-8
try:
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def build_sheet(sprite_dir: Path, entries: dict[str, str], out_path: Path) -> None:
    """entries: {表情编号 -> 文件名}，按编号从小到大排成网格。"""
    idxs = sorted(entries, key=lambda s: int(s))
    cols = 5
    rows = (len(idxs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * CELL_W, rows * CELL_H), (235, 235, 235))
    draw = ImageDraw.Draw(sheet)

    for i, idx in enumerate(idxs):
        col, row = i % cols, i // cols
        x0, y0 = col * CELL_W, row * CELL_H
        draw.text((x0 + 6, y0 + 4), f"idx {int(idx):02d}", fill=(20, 20, 20))

        try:
            img = Image.open(sprite_dir / entries[idx]).convert("RGBA")
        except Exception:  # noqa: BLE001
            draw.text((x0 + 6, y0 + 40), "(读取失败)", fill=(180, 0, 0))
            continue

        # 等比缩放到格子里，居中；用灰底把透明区域显出来，方便看轮廓
        img.thumbnail((CELL_W - 16, CELL_H - LABEL_H - 12), Image.LANCZOS)
        box = Image.new("RGBA", img.size, (205, 205, 205, 255))
        box.alpha_composite(img)
        px = x0 + (CELL_W - img.width) // 2
        py = y0 + LABEL_H + (CELL_H - LABEL_H - 4 - img.height) // 2
        sheet.paste(box.convert("RGB"), (px, py))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    print(f"  {out_path.name}  ({len(idxs)} 个表情)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", default="s", help="尺寸族 f/s/m/l，默认 s（半身）")
    ap.add_argument("--group", default=None, help="只生成指定服装组，如 0_0")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    args = ap.parse_args()

    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    sprite_dir = Path(catalog["source_dir"])
    if not sprite_dir.is_dir():
        print(f"[错误] 清单里的素材目录不存在：{sprite_dir}")
        return 1

    out_dir = Path(args.out)
    groups = catalog["groups"]
    targets = [args.group] if args.group else sorted(groups)
    print(f"生成对照表 -> {out_dir}")
    for group in targets:
        fams = groups.get(group, {})
        entries = fams.get(args.family)
        if not entries:
            print(f"  跳过 {group}：该组没有尺寸族 {args.family} 的图")
            continue
        build_sheet(sprite_dir, entries, out_dir / f"{args.family}_{group}.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
