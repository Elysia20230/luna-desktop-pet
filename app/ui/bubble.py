# -*- coding: utf-8 -*-
"""
桌面气泡：她主动说话时，在头顶冒一个对话泡。

为什么不直接用托盘通知：托盘消息跑到右下角，和"她就站在你面前说话"
的感觉差太远。这里用一个小窗口贴在立绘头顶，几秒后自己消失。

实现要点：
- Qt.ToolTip + WA_ShowWithoutActivating：不抢焦点，不会把你正在打的字打断；
- 背景真透明 + 自己画圆角矩形和指向下方的小尖角；
- 单击气泡 = 打开对话窗。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen, QPolygonF,
)
from PySide6.QtWidgets import QLabel, QWidget

# 气泡的尺寸与配色（想改外观就动这里）
TEXT_MAX_W = 340        # 文字最宽多少像素，超了就换行
PAD_X = 14              # 左右内边距
PAD_Y = 10              # 上下内边距
TAIL_H = 10             # 下方小尖角的高度
RADIUS = 14             # 圆角半径
BG = QColor(38, 31, 56, 236)         # 底色（和对话窗一致）
BORDER = QColor(122, 92, 168, 200)   # 边框
TEXT_COLOR = "#f4f0ff"


def estimate_msec(text: str, default: float = 9_000) -> int:
    """
    一句话该停留多久：字数越多停得越久（读得完）。
    4 秒起步，每字 60 毫秒，最多 20 秒；没给字数就用 default。
    """
    body = (text or "").strip()
    ms = 4_000 + 60 * len(body)
    ms = min(20_000, ms)
    return int(max(default, ms)) if body else int(default)


class SpeechBubble(QWidget):
    """贴在桌宠头顶的小气泡。"""

    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.ToolTip                 # ToolTip 不抢焦点，最不容易打扰人
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_NoSystemBackground)

        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # 字号用 QFont 设，而不是只写在样式表里：
        # 样式表的 font-size 不会改变 fontMetrics() 的结果，算出来宽度会对不上、字被裁掉。
        font = QFont()
        font.setPixelSize(13)
        self._label.setFont(font)
        self._label.setStyleSheet(
            f"color:{TEXT_COLOR}; background:transparent;"
        )

        # 到点自动消失
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide_bubble)

        # 贴哪一侧 / 贴谁：reposition() 跟着桌宠挪位置时要用
        self._prefer = "top"
        self._anchor: QRect | None = None

    # ---- 对外 -------------------------------------------------------------
    def show_text(self, text: str, anchor: QRect, msec: int | None = None,
                  prefer: str = "top") -> None:
        """
        显示一句话。

        text   : 要说的话（可以是多行）
        anchor : 桌宠当前所在的矩形，用来决定气泡贴在哪
        msec   : 停留多少毫秒；不传就按字数自动算（4 秒起，每字 60ms，最多 20 秒）
        prefer : "top" 贴头顶（默认）/ "bottom" 贴脚下——输入框翻到头顶时用它避让
        """
        text = (text or "").strip()
        if not text:
            return
        self._prefer = prefer
        self._anchor = QRect(anchor)
        self._label.setText(text)
        self._relayout()
        self.place_near(anchor, prefer)
        self.show()
        self.raise_()
        self._timer.start(max(1500, int(msec if msec is not None else estimate_msec(text))))

    def update_text(self, text: str, anchor: QRect, prefer: str = "top") -> None:
        """
        流式更新（她还在"想"的时候逐字长出来）。
        和 show_text 的区别：不重置计时器，也不闪一下——避免长回复时气泡疯狂闪。
        """
        text = (text or "").strip()
        if not text:
            return
        self._prefer = prefer
        self._anchor = QRect(anchor)
        self._label.setText(text)
        self._relayout()
        self.place_near(anchor, prefer)
        if not self.isVisible():
            self.show()
        self.raise_()

    def reposition(self, anchor: QRect) -> None:
        """
        只跟着桌宠挪位置：文字、计时器都不动。

        拖动她的时候要是气泡留在原地，会像"飘在空中的字"；
        main.py 收到 pet_moved 就调这个。
        """
        self._anchor = QRect(anchor)
        if self.isVisible():
            self.place_near(anchor, self._prefer)

    def stop_timer(self) -> None:
        """暂停自动消失（比如正在等语音播放时想让它多留一会儿）。"""
        self._timer.stop()

    def hide_bubble(self) -> None:
        self._timer.stop()
        self.hide()

    def place_near(self, anchor: QRect, prefer: str = "top") -> None:
        """
        默认贴在桌宠头顶上方；头顶放不下（人物很高/贴着屏幕顶）就翻到脚下。
        prefer="bottom" 时反过来（脚下优先）——输入框翻到头顶时气泡让位。
        横向夹在屏幕内，避免跑到屏幕外面点不到；多显示器按她所在那块屏算。
        """
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)
        x = anchor.center().x() - self.width() // 2
        x = max(area.left() + 6, min(x, area.right() - self.width() - 6))
        y_top = anchor.top() - self.height() - 6
        y_bottom = anchor.bottom() + 6
        if prefer == "bottom":
            y = y_bottom
            if y + self.height() > area.bottom() - 6:
                y = y_top
        else:
            y = y_top
            if y < area.top() + 6:
                y = y_bottom
        y = max(area.top() + 6, min(y, area.bottom() - self.height() - 6))
        self.move(x, y)

    # ---- 内部 -------------------------------------------------------------
    def _relayout(self) -> None:
        """按文字长度算出合适的宽度和高度（文字多就自动换行变高）。"""
        fm = self._label.fontMetrics()
        text = self._label.text() or " "
        # 先按"不换行"算自然宽度；超过上限就固定成最宽，让字自己换行
        natural = max(fm.horizontalAdvance(line) for line in (text.splitlines() or [""]))
        inner_w = min(TEXT_MAX_W, max(96, natural))
        self._label.setFixedWidth(inner_w)
        # 换行后的高度只能用 heightForWidth 问，adjustSize() 对自动换行的 QLabel 不可靠
        inner_h = self._label.heightForWidth(inner_w)
        if inner_h <= 0:
            inner_h = fm.height() * max(1, len(text.splitlines()))
        self._label.setFixedHeight(inner_h)
        self.setFixedSize(inner_w + PAD_X * 2, inner_h + PAD_Y * 2 + TAIL_H)
        self._label.move(PAD_X, PAD_Y)

    def paintEvent(self, event) -> None:  # noqa: N802 Qt 的命名
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        body = QRectF(0.5, 0.5, self.width() - 1, self.height() - TAIL_H - 0.5)
        path = QPainterPath()
        path.addRoundedRect(body, RADIUS, RADIUS)

        # 下方小尖角：从气泡底部中间伸出来，指着她
        cx = self.width() / 2
        tail = QPolygonF([
            QPointF(cx - 9, self.height() - TAIL_H),
            QPointF(cx + 9, self.height() - TAIL_H),
            QPointF(cx, self.height() - 0.5),
        ])
        path.addPolygon(tail)

        painter.setPen(QPen(BORDER, 1))
        painter.setBrush(BG)
        painter.drawPath(path.simplified())

    def mousePressEvent(self, event) -> None:  # noqa: N802 Qt 的命名
        """点一下气泡 = 打开对话窗（想接着聊）。"""
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            self.hide_bubble()
