# -*- coding: utf-8 -*-
"""
"接一句"小按钮：她主动搭话时，头顶气泡下方冒出几个可以直接点的回复。

为什么要有它：主动搭话时如果你想接话，得先双击桌宠、等输入框弹出来、再打字，
打断感太强。这里给三个现成的短句 + 一个「自己写…」，点一下就算你说了一句话。

和气泡是两套东西：
    气泡（bubble.py）显示**她**说的话；
    本文件显示**你能接的话**，点击后交给 main.py 走正常对话流程。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

# 配色跟气泡保持一致，避免两个浮层看起来不是一个东西
C_BG = "rgba(38, 31, 56, 236)"
C_BORDER = "rgba(122, 92, 168, 200)"
C_TEXT = "#f4eeff"
C_HOVER = "rgba(122, 92, 168, 220)"

GAP = 8          # 与锚点（气泡）之间的空隙
PAD = 8          # 内部留白
BTN_H = 30       # 按钮高度
SPACING = 6      # 按钮间距
MAX_W = 460      # 整排最大宽度，超了就折行


class QuickReplies(QWidget):
    """一排"接一句"按钮。用完即隐藏，不常驻。"""

    chosen = Signal(str)          # 用户点了某个现成短句
    custom_requested = Signal()   # 用户点了「自己写…」

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # ToolTip + 置顶 + 无边框：不抢焦点、不占任务栏
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(PAD, PAD, PAD, PAD)
        self._layout.setSpacing(SPACING)
        self._rows: list[QHBoxLayout] = []
        self._anchor = QRect()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_chips)

        self._apply_style()

    # ---- 外观 -------------------------------------------------------------
    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#chipsBox {{
                background: {C_BG};
                border: 1px solid {C_BORDER};
                border-radius: 14px;
            }}
            QPushButton {{
                background: rgba(255, 255, 255, 18);
                color: {C_TEXT};
                border: 1px solid {C_BORDER};
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{ background: {C_HOVER}; }}
            QPushButton:pressed {{ background: rgba(122, 92, 168, 255); }}
            """
        )

    # ---- 显示 / 隐藏 ------------------------------------------------------
    def show_for(self, anchor: QRect, options: list[str], seconds: int = 12) -> None:
        """
        在 anchor（通常是她头顶的气泡）下方显示按钮。

        options：现成短句，最多 4 条；空字符串会被忽略。
        另外总会追加一个「自己写…」。
        """
        texts = [t.strip() for t in options if str(t).strip()][:4]
        if not texts:
            return
        self._rebuild(texts)
        self._anchor = QRect(anchor)
        self.place_near(self._anchor)
        self.show()
        self.raise_()
        if seconds and seconds > 0:
            self._timer.start(int(seconds) * 1000)
        else:
            self._timer.stop()

    def hide_chips(self) -> None:
        self._timer.stop()
        self.hide()

    def is_showing(self) -> bool:
        return self.isVisible()

    def reposition(self, anchor: QRect) -> None:
        """桌宠被拖动/缩放时跟着走（不动计时器）。"""
        if not self.isVisible():
            return
        self._anchor = QRect(anchor)
        self.place_near(self._anchor)

    # ---- 内部 -------------------------------------------------------------
    def _clear(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            lay = item.layout()
            if lay is not None:
                self._drop_layout(lay)
        self._rows.clear()

    def _drop_layout(self, lay) -> None:
        while lay.count():
            item = lay.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild(self, texts: list[str]) -> None:
        self._clear()
        box = QWidget(self)
        box.setObjectName("chipsBox")
        box_lay = QVBoxLayout(box)
        box_lay.setContentsMargins(PAD, PAD, PAD, PAD)
        box_lay.setSpacing(SPACING)

        # 先算每行放几个：超宽就折行（窄屏/长句时不会跑出屏幕）
        entries = list(texts) + ["自己写…"]
        row: QHBoxLayout | None = None
        used = 0
        for text in entries:
            width = self._button_width(text)
            if row is None or (used + width + SPACING > MAX_W - PAD * 2 and used > 0):
                row = QHBoxLayout()
                row.setSpacing(SPACING)
                row.setContentsMargins(0, 0, 0, 0)
                box_lay.addLayout(row)
                used = 0
            btn = QPushButton(text, box)
            btn.setFixedHeight(BTN_H)
            btn.setCursor(Qt.PointingHandCursor)
            if text == "自己写…":
                btn.clicked.connect(self._on_custom)
            else:
                btn.clicked.connect(lambda _=False, t=text: self._on_chosen(t))
            row.addWidget(btn)
            used += width + SPACING

        self._layout.addWidget(box)
        box.adjustSize()
        self.adjustSize()

    def _button_width(self, text: str) -> int:
        """粗估按钮宽度：中文按 14px 算，标点字母稍窄，够用来折行就行。"""
        width = 0
        for ch in text:
            width += 14 if ord(ch) > 0x2E80 else 8
        return width + 26

    def _on_chosen(self, text: str) -> None:
        self.hide_chips()
        self.chosen.emit(text)

    def _on_custom(self) -> None:
        self.hide_chips()
        self.custom_requested.emit()

    def place_near(self, anchor: QRect) -> None:
        """优先贴在锚点下方；下面放不下就翻到上方；横向夹进所在屏幕。"""
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        size: QSize = self.sizeHint()
        w, h = max(1, size.width()), max(1, size.height())

        below = anchor.bottom() + GAP
        above = anchor.top() - h - GAP
        if below + h <= area.bottom():
            y = below
        elif above >= area.top():
            y = above
        else:
            y = max(area.top(), min(below, area.bottom() - h))

        x = anchor.center().x() - w // 2
        x = max(area.left() + 4, min(x, area.right() - w - 4))
        self.resize(w, h)
        self.move(x, y)
