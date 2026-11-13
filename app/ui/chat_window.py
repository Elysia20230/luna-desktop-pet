# -*- coding: utf-8 -*-
"""
聊天记录窗口（只读）。

以前这里是一个带输入框的大聊天窗；现在"说话"的入口改成桌下的小输入框
（`app/ui/input_bar.py`），她说的话用头顶气泡显示。这个窗口退居二线：
专门用来**往前翻记录**——谁说过什么、什么时候说的，都在这里。

它不做 AI 的事，只负责把一份消息列表画成气泡：
    load_entries([{"role": "user", "text": "…"}, …])
数据来自 `data/history.json`（由 ChatEngine 滚动保存，最多 200 条）。
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextBrowser, QVBoxLayout, QWidget,
)

# 界面配色（想换皮肤改这里就行）
C_PANEL = QColor(38, 31, 56, 236)
C_BORDER = QColor(122, 100, 168, 170)
C_USER = QColor(122, 92, 168)
C_LUNA = QColor(52, 44, 76)
C_TEXT = "#f3ecff"
C_HINT = "#a99dc4"


class ChatWindow(QWidget):
    """聊天记录（只读气泡列表）。"""

    closed = Signal()
    clear_requested = Signal()      # 用户点了「清空」——真正的清理由 main.py 做
    screen_requested = Signal()
    settings_requested = Signal()

    def __init__(self, engine=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.messages: list[dict] = []
        self._drag_offset: QPoint | None = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("与露娜的聊天记录")
        self.resize(430, 560)

        self._build_ui()
        self._render()

    # ---- 界面搭建 ---------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(8)

        # 顶部标题栏（可拖动）
        title_row = QHBoxLayout()
        self.title = QLabel("聊天记录 · 樱小路露娜")
        self.title.setStyleSheet(
            "color:#f3ecff;font-size:15px;font-weight:600;letter-spacing:1px;"
            "font-family:'霞鹜文楷','Microsoft YaHei UI';"
        )
        title_row.addWidget(self.title)
        title_row.addStretch(1)

        self.btn_clear = QPushButton("清空")
        self.btn_screen = QPushButton("看屏幕")
        self.btn_settings = QPushButton("设置")
        self.btn_close = QPushButton("×")
        for btn, tip in (
            (self.btn_clear, "把聊天记录全部删掉（不可恢复）"),
            (self.btn_screen, "让她看一眼你的屏幕并说点什么"),
            (self.btn_settings, "打开设置"),
            (self.btn_close, "收起记录窗口"),
        ):
            btn.setToolTip(tip)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(
                "QPushButton{color:#d9cdf2;background:transparent;border:none;padding:2px 8px;"
                "border-radius:6px;font-size:13px;}"
                "QPushButton:hover{background:#3d3358;color:#fff;}"
            )
            title_row.addWidget(btn)
        root.addLayout(title_row)

        # 消息区
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.setStyleSheet("QTextBrowser{background:transparent;border:none;}")
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        root.addWidget(self.view, 1)

        # 底部提示行
        self.status = QLabel("往下滚可以看更早的话")
        self.status.setStyleSheet(f"color:{C_HINT};font-size:11px;")
        root.addWidget(self.status)

        # 事件
        self.btn_close.clicked.connect(self.hide)
        self.btn_clear.clicked.connect(self.clear_requested.emit)
        self.btn_screen.clicked.connect(self.screen_requested.emit)
        self.btn_settings.clicked.connect(self.settings_requested.emit)

    # ---- 数据 -------------------------------------------------------------
    def load_entries(self, entries: list[dict]) -> None:
        """从 history.json 读来的列表（兼容 content / text 两种字段）。"""
        self.messages = []
        for item in entries or []:
            role = "user" if str(item.get("role", "")) == "user" else "assistant"
            text = str(item.get("text") or item.get("content") or "")
            if text:
                self.messages.append({"role": role, "text": text})
        self._render()

    def add_message(self, role: str, text: str) -> None:
        self.messages.append({"role": role, "text": text})
        self._render()

    def set_status(self, text: str) -> None:
        self.status.setText(text or "")

    # ---- 画气泡 -----------------------------------------------------------
    def _render(self) -> None:
        """把消息列表整段渲染成 HTML（消息不多，重画最简单也最不容易出错）。"""
        block = (
            '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            '<td align="{align}">'
            '<table cellspacing="0" cellpadding="8" style="background:{bg};border-radius:10px;">'
            '<tr><td><span style="color:{color};font-size:13px;'
            "font-family:'Microsoft YaHei UI';\">{text}</span></td></tr>"
            "</table></td></tr></table>"
            '<div style="height:6px;"></div>'
        )
        parts = []
        for msg in self.messages:
            is_user = msg["role"] == "user"
            text = _escape(msg["text"]).replace("\n", "<br>") or "…"
            parts.append(block.format(
                align="right" if is_user else "left",
                bg=C_USER.name() if is_user else C_LUNA.name(),
                color=C_TEXT,
                text=text,
            ))
        if not parts:
            parts.append(
                '<div style="color:#a99dc4;font-size:12px;padding:12px;">'
                "还没有聊天记录。双击露娜，在脚下的输入框里跟她说句话吧。</div>"
            )
        html = (
            "<div style=\"color:%s;font-size:13px;font-family:'Microsoft YaHei UI';\">%s</div>"
            % (C_TEXT, "".join(parts))
        )
        self.view.setHtml(html)
        # 滚到最底下，看最新一句
        self.view.moveCursor(QTextCursor.End)

    # ---- 外观：圆角半透明 -------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 14, 14)
        painter.fillPath(path, C_PANEL)
        painter.setPen(C_BORDER)
        painter.drawPath(path)

    # ---- 拖动窗口 ---------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and event.position().y() < 46:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._drag_offset = None

    def closeEvent(self, event) -> None:  # noqa: N802
        event.ignore()
        self.hide()
        self.closed.emit()


def _escape(text: str) -> str:
    """把用户输入里的尖括号转义掉，不然会被当成 HTML 标签。"""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
