# -*- coding: utf-8 -*-
"""
可折叠的小区块（设置面板用）。

为什么要有它：语音页有二十来个控件，全摊开会把「念不念出来」「音量」这种
天天要改的项也埋进去。折叠之后，常用的留在一块，高级参数点一下才展开。

用法：
    sec = CollapsibleSection("服务与模型（高级）")
    form = QFormLayout()
    form.addRow("服务端口", spin)
    sec.set_content_layout(form)
    page_layout.addWidget(sec)

折叠状态不写进配置 —— 每次打开设置都按「展开哪几块」的默认值来，
免得用户上次关掉某块、下次找不到东西。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QToolButton, QVBoxLayout, QWidget


class CollapsibleSection(QWidget):
    """一个标题按钮 + 一块可折叠的内容区。"""

    toggled = Signal(bool)          # True = 展开

    def __init__(self, title: str, expanded: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = title

        self._header = QToolButton(self)
        self._header.setObjectName("sectionHeader")   # 样式在 _DIALOG_QSS 里
        self._header.setCheckable(True)
        self._header.setChecked(bool(expanded))
        self._header.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self._header.setCursor(Qt.PointingHandCursor)
        self._header.setSizePolicy(self._header.sizePolicy().horizontalPolicy(), self._header.sizePolicy().verticalPolicy())
        self._header.toggled.connect(self._sync)

        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(12, 4, 0, 0)   # 缩进一点点，视觉上从属于标题
        self._body_layout.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(self._header)
        layout.addWidget(self._body)

        self._sync(self._header.isChecked())

    # ---- 对外接口 --------------------------------------------------------
    def content_layout(self) -> QVBoxLayout:
        """往里塞控件用的布局（一般是 QFormLayout 直接 addLayout 进去）。"""
        return self._body_layout

    def set_content_layout(self, layout) -> None:
        """把整块 QFormLayout 放进来（比逐个 addWidget 省事）。"""
        self._body_layout.addLayout(layout)

    def add_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def set_expanded(self, expanded: bool) -> None:
        self._header.setChecked(bool(expanded))

    def is_expanded(self) -> bool:
        return self._header.isChecked()

    # ---- 内部 ------------------------------------------------------------
    def _sync(self, expanded: bool) -> None:
        # 用 ＋/－ 而不是三角箭头：U+25B6(▶)/U+25B8(▸) 在中文字体里常常缺字形，
        # 会渲染成方框（实测 msyh 就没有），全角加减号到处都有。
        self._header.setText(("－ " if expanded else "＋ ") + self._title)
        self._body.setVisible(bool(expanded))
        self.toggled.emit(bool(expanded))
