# -*- coding: utf-8 -*-
"""
桌下输入框：双击露娜之后，从她脚下冒出来的那条小横杠。

为什么不沿用原来那个 430×620 的大聊天窗：
    桌宠的场景是"她就站在桌角，我顺口跟她说一句"，一大块窗口会把桌面挡住。
    所以这里只留一个输入框，她说的话用头顶气泡显示，想翻旧账再点「记录」。

用法（main.py 里）：
    bar = InputBar()
    bar.toggle(pet.frameGeometry())     # 双击立绘
    bar.send_requested.connect(...)     # 用户按回车/点「说」
    bar.history_requested.connect(...)  # 点了「记录」
"""
from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QWidget

# 外观（想改大小/颜色就动这里）
BAR_W = 460          # 期望宽度（屏幕窄时会自动缩短）
BAR_H = 54           # 高度
GAP = 8              # 和立绘之间的间距
RADIUS = 16
C_BG = QColor(38, 31, 56, 240)
C_BORDER = QColor(122, 100, 168, 190)


class InputBar(QWidget):
    """贴在桌宠脚下的一句话输入框。"""

    send_requested = Signal(str)
    history_requested = Signal()
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool                      # 不在任务栏占位
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setWindowTitle("对露娜说话")
        self.setFixedHeight(BAR_H)
        self.resize(BAR_W, BAR_H)
        self._busy = False
        self._side = "below"     # 贴在脚下还是翻到头顶（见 follow()）
        self._anchor: QRect | None = None
        self._build_ui()

    # ---- 界面 -------------------------------------------------------------
    def _build_ui(self) -> None:
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 9, 10, 9)
        row.setSpacing(8)

        self.input = QLineEdit()
        self.input.setPlaceholderText("对露娜说点什么…（回车发送，Esc 收起）")
        self.input.setStyleSheet(
            "QLineEdit{background:transparent;border:none;color:#f4f0ff;"
            "font-size:13px;font-family:'Microsoft YaHei UI';}"
            "QLineEdit:disabled{color:#9d92bb;}"
        )
        row.addWidget(self.input, 1)

        self.btn_send = QPushButton("说")
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send.setFixedWidth(50)
        self.btn_send.setStyleSheet(
            "QPushButton{background:#8a6fc4;color:#fff;border:none;border-radius:9px;"
            "padding:7px 0;font-size:13px;font-weight:600;}"
            "QPushButton:hover{background:#9f84d8;}"
            "QPushButton:disabled{background:#4a4162;color:#9b90b5;}"
        )
        row.addWidget(self.btn_send)

        self.btn_history = QPushButton("记录")
        self.btn_history.setCursor(Qt.PointingHandCursor)
        self.btn_history.setToolTip("翻看和露娜的全部聊天记录")
        self.btn_history.setFixedWidth(52)
        self.btn_history.setStyleSheet(
            "QPushButton{background:transparent;color:#c9bce4;border:1px solid #4b3f68;"
            "border-radius:9px;padding:7px 0;font-size:12px;}"
            "QPushButton:hover{background:#3d3358;color:#fff;}"
        )
        row.addWidget(self.btn_history)

        self.input.returnPressed.connect(self._on_send)
        self.btn_send.clicked.connect(self._on_send)
        self.btn_history.clicked.connect(self.history_requested.emit)

    # ---- 对外 -------------------------------------------------------------
    def toggle(self, anchor: QRect) -> None:
        """双击立绘时调用：开着就收起，收着就冒出来。"""
        if self.isVisible():
            self.hide_bar()
        else:
            self.show_bar(anchor)

    def show_bar(self, anchor: QRect) -> None:
        self.place_under(anchor)
        self.show()
        self.raise_()
        self.activateWindow()          # 输入框是要抢焦点的，不然打不了字
        self.input.setFocus()

    def hide_bar(self) -> None:
        self.hide()
        self.closed.emit()

    def focus_input(self) -> None:
        if not self.isVisible():
            return
        self.activateWindow()
        self.raise_()
        self.input.setFocus()

    def set_busy(self, busy: bool) -> None:
        """她正在想/正在说的时候，把输入框置灰，避免连发。"""
        self._busy = bool(busy)
        self.input.setEnabled(not busy)
        self.btn_send.setEnabled(not busy)
        if not busy:
            self.input.setFocus()

    def set_call_hint(self, state: str) -> None:
        """
        通话状态写进输入框的占位提示（"在听/思考中/说话中"）。

        为什么放这里：通话时用户眼睛多半在她的立绘上，
        输入框就在脚下，抬眼就能看见"现在轮到谁说话"。
        """
        if state:
            self.input.setPlaceholderText(f"语音通话中 · {state}（也可以直接打字）")
        else:
            self.input.setPlaceholderText("对露娜说点什么…（回车发送，Esc 收起）")

    # ---- 位置 -------------------------------------------------------------
    def follow(self, anchor: QRect) -> None:
        """
        贴着桌宠摆好（她一动就再调一次，所以叫 follow）。

        规则（按重要性排）：
            1. 默认在她**脚下**正中（+8px 间隙）；
            2. 脚下放不下（她贴着任务栏/屏幕底）就翻到**头顶**；
            3. 头顶也放不下（立绘比屏幕还高），就贴在可用区域里最合适的一侧；
            4. 横向永远夹在屏幕内，不让她半个输入框飘到看不见的地方；
            5. 用**她所在那块屏幕**的可用区域算（多显示器时不会跑到主屏上去）。

        侧边会"记住"（self.side）：只有真的放不下才翻面，避免她上下轻轻拖动时
        输入框在两行之间来回跳。
        """
        self._anchor = QRect(anchor)
        screen = QGuiApplication.screenAt(anchor.center()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else QRect(0, 0, 1280, 800)

        width = max(280, min(BAR_W, area.width() - 40))
        if width != self.width():
            self.resize(width, BAR_H)

        x = anchor.center().x() - width // 2
        x = max(area.left() + 8, min(x, area.right() - width - 8))

        room_below = area.bottom() - 4 - (anchor.bottom() + GAP)
        room_above = (anchor.top() - GAP) - (area.top() + 4)
        # 带 24px 迟滞：脚下还宽裕就回到底下；不够了才翻上去。
        # 没有迟滞的话，她上下挪一点点输入框就在两行之间来回跳。
        if room_below >= BAR_H + 24:
            self._side = "below"
        elif room_above >= BAR_H:
            self._side = "above"
        else:
            # 上下都紧张：谁更宽裕贴谁，这样至少不会整个飘在屏幕外
            self._side = "below" if room_below >= room_above else "above"

        if self._side == "above":
            y = anchor.top() - BAR_H - GAP
        else:
            y = anchor.bottom() + GAP

        # 最后一道保险：两边都塞不下（屏幕特别矮）就夹进可用区域
        y = max(area.top() + 4, min(y, area.bottom() - BAR_H - 4))
        self.move(x, y)

    # 老名字保留：main.py / 测试里原来调的是 place_under
    place_under = follow

    @property
    def side(self) -> str:
        """"below" = 在她脚下，"above" = 翻到了头顶。气泡要靠它避让。"""
        return self._side

    # ---- 内部 -------------------------------------------------------------
    def _on_send(self) -> None:
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.send_requested.emit(text)

    def keyPressEvent(self, event) -> None:  # noqa: N802 Qt 的命名
        if event.key() == Qt.Key_Escape:
            self.hide_bar()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS
        )
        painter.fillPath(path, C_BG)
        painter.setPen(C_BORDER)
        painter.drawPath(path)
