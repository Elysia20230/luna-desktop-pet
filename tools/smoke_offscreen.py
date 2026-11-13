# -*- coding: utf-8 -*-
"""
离屏冒烟脚本：不弹真窗口，把桌宠渲染成 png 检查"是不是真的画出来了"。

用法（在 LunaPet 目录）：
    .venv\\Scripts\\python.exe tools\\smoke_offscreen.py
输出：
    data/smoke/<情绪>.png  —— 12 档情绪各一张
    data/smoke/<尺寸>.png  —— 4 个尺寸族各一张

它只依赖 PySide6，不会联网、不会调用 DeepSeek。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 无窗口模式，CI 也能跑

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt
from PySide6.QtGui import (QColor, QFont, QFontDatabase, QLinearGradient,  # noqa: E402
                           QPainter, QPixmap)
from PySide6.QtWidgets import QApplication            # noqa: E402

from app import mood                                  # noqa: E402
from app.config import Config                          # noqa: E402
from app.sprites import SpriteCache, SpriteLibrary     # noqa: E402
from app.ui.pet_window import PetWindow                # noqa: E402

OUT = ROOT / "data" / "smoke"
PREVIEW = ROOT / "docs" / "preview_pet.png"
CELL_W, CELL_H = 310, 330          # 预览图里每格的尺寸（宽, 高）


def _load_cjk_font() -> str:
    """
    离屏模式（offscreen）拿不到系统字体，中文标签会渲染成方块。
    这里直接从 Windows 字体目录加载一个中文字体给 Qt 用。
    """
    win_dir = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhl.ttc", "simhei.ttf", "Deng.ttf", "simsun.ttc"):
        path = win_dir / name
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id == -1:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            return families[0]
    return "Microsoft YaHei"



def _fit(pixmap: QPixmap, max_w: int, max_h: int) -> QPixmap:
    """把立绘等比缩到格子能放下，保持清晰。"""
    if pixmap.isNull():
        return pixmap
    return pixmap.scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _make_preview(shots: list[tuple[str, QPixmap]], font_family: str) -> QPixmap:
    """把 12 张情绪图拼成一张带标签的预览图，方便贴到 README。"""
    cols = 4
    rows = (len(shots) + cols - 1) // cols
    canvas = QPixmap(CELL_W * cols, CELL_H * rows)
    canvas.fill(Qt.transparent)

    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.SmoothPixmapTransform, True)

    # 背景：从浅紫到米白的斜向渐变，和界面配色一致
    grad = QLinearGradient(0, 0, canvas.width(), canvas.height())
    grad.setColorAt(0.0, QColor(58, 46, 82))
    grad.setColorAt(1.0, QColor(24, 20, 34))
    painter.fillRect(canvas.rect(), grad)

    font = QFont(font_family)
    font.setPointSize(10)
    painter.setFont(font)

    for i, (label, pixmap) in enumerate(shots):
        col, row = i % cols, i // cols
        x, y = col * CELL_W, row * CELL_H
        # 画立绘（底部对齐，像真的站在桌面上）
        fitted = _fit(pixmap, CELL_W - 24, CELL_H - 46)
        painter.drawPixmap(
            x + (CELL_W - fitted.width()) // 2,
            y + CELL_H - 34 - fitted.height(),
            fitted,
        )
        # 画情绪名
        painter.setPen(QColor(226, 214, 245))
        painter.drawText(x, y + CELL_H - 26, CELL_W, 24, Qt.AlignHCenter, label)

    painter.end()
    return canvas


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    app = QApplication([])                             # noqa: F841 需要它来创建 QPixmap

    cfg = Config()
    library = SpriteLibrary(cfg)
    cache = SpriteCache(library)

    if not library.groups:
        print("找不到立绘清单，先运行 tools/scan_sprites.py")
        return 1

    window = PetWindow(cfg, library, cache)
    font_family = _load_cjk_font()      # 预览图上的中文标签要用它

    failures = 0
    shots: list[tuple[str, QPixmap]] = []
    for emotion in mood.EMOTIONS:
        window.set_emotion(emotion)
        if window._pixmap is None:
            print(f"[FAIL] 情绪 {emotion} 没有渲染出图")
            failures += 1
            continue
        shot = OUT / f"{emotion}.png"
        grabbed = window.grab()
        grabbed.save(str(shot))
        shots.append((emotion, grabbed))
        print(f"[OK] {emotion:<3} -> {window.index:02d}  {shot.name}")

    # 逐族渲染。注意：set_family 会把选择写进配置文件，所以先记住原值、跑完再还回去，
    # 免得「冒烟一次」就把主人的桌宠尺寸改成 l。
    original_family = window.family
    for family in library.families:
        window.set_family(family)
        if window._pixmap is None:
            print(f"[FAIL] 尺寸 {family} 没有渲染出图")
            failures += 1
            continue
        shot = OUT / f"family_{family}.png"
        window.grab().save(str(shot))
        print(f"[OK] 尺寸 {family}  {shot.name}")
    window.set_family(original_family)

    # 把 12 档情绪拼成一张预览图（README 会用到）
    if shots:
        PREVIEW.parent.mkdir(parents=True, exist_ok=True)
        preview = _make_preview(shots, font_family)
        ok = preview.save(str(PREVIEW))
        print(f"[{'OK' if ok else 'FAIL'}] 预览图 -> {PREVIEW}")
        failures += 0 if ok else 1

    print(f"\n完成，失败 {failures} 项。输出目录：{OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
