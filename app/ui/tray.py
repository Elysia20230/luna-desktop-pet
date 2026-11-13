# -*- coding: utf-8 -*-
"""
系统托盘：右键桌面右下角的图标，就能找到所有功能。

为什么要托盘：桌宠立绘被误点到屏幕外面、或者被别的窗口挡住时，
托盘图标是随时能把对话窗叫回来的"保险栓"。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class LunaTray(QSystemTrayIcon):
    """托盘图标 + 菜单。所有动作都通过信号抛给 main.py。"""

    chat_requested = Signal()
    history_requested = Signal()
    screen_requested = Signal()
    call_toggle_requested = Signal()     # 实时语音通话开/关
    outfit_auto_requested = Signal(bool)  # 按时间自动换装开/关
    settings_requested = Signal()
    memory_requested = Signal()
    toggle_pet = Signal()
    quit_requested = Signal()

    def __init__(self, library, parent=None) -> None:
        super().__init__(self._make_icon(library), parent)
        self.setToolTip("樱小路露娜 · 桌面宠物（右键有菜单）")
        self._menu = QMenu()
        self._menu.setStyleSheet(
            "QMenu{background:#2b2440;color:#f2e9ff;border:1px solid #6b5b8f;border-radius:8px;padding:6px;}"
            "QMenu::item{padding:6px 22px;border-radius:6px;}"
            "QMenu::item:selected{background:#8a6fc4;}"
            "QMenu::separator{height:1px;background:#4b3f68;margin:4px 8px;}"
        )
        self._build_menu()
        self.setContextMenu(self._menu)

    def _build_menu(self) -> None:
        menu = self._menu
        menu.clear()

        act_chat = QAction("跟她说话", menu)
        act_chat.triggered.connect(self.chat_requested.emit)
        menu.addAction(act_chat)

        act_history = QAction("聊天记录…", menu)
        act_history.triggered.connect(self.history_requested.emit)
        menu.addAction(act_history)

        act_screen = QAction("让她看看我的屏幕", menu)
        act_screen.triggered.connect(self.screen_requested.emit)
        menu.addAction(act_screen)

        # 语音通话：勾选态由 main.py 按 CallSession.running 回灌，菜单自己不改状态
        act_call = QAction("语音通话", menu)
        act_call.setCheckable(True)
        act_call.triggered.connect(lambda _checked=False: self.call_toggle_requested.emit())
        self._act_call = act_call
        menu.addAction(act_call)

        act_toggle = QAction("显示 / 隐藏立绘", menu)
        act_toggle.triggered.connect(self.toggle_pet.emit)
        menu.addAction(act_toggle)

        # 按时段/季节换装：跟立绘右键里的同名开关是一回事
        # 对勾状态由 main.py 启动时回灌（托盘这边不读配置，保持单一来源）
        act_outfit = QAction("按时间自动换装", menu)
        act_outfit.setCheckable(True)
        act_outfit.setChecked(True)
        act_outfit.triggered.connect(
            lambda checked: self.outfit_auto_requested.emit(bool(checked))
        )
        self._act_outfit = act_outfit
        menu.addAction(act_outfit)

        menu.addSeparator()
        act_settings = QAction("设置…", menu)
        act_settings.triggered.connect(self.settings_requested.emit)
        menu.addAction(act_settings)

        act_memory = QAction("记忆…", menu)
        act_memory.triggered.connect(self.memory_requested.emit)
        menu.addAction(act_memory)

        menu.addSeparator()
        act_quit = QAction("退出", menu)
        act_quit.triggered.connect(self.quit_requested.emit)
        menu.addAction(act_quit)

    def set_call_checked(self, on: bool) -> None:
        """把「语音通话」的对勾刷成真实状态（可能从立绘右键开的，也可能是被弹回的）。"""
        act = getattr(self, "_act_call", None)
        if act is not None and act.isChecked() != bool(on):
            act.setChecked(bool(on))

    def set_outfit_auto_checked(self, on: bool) -> None:
        """把「按时间自动换装」的对勾刷成真实状态（可能在立绘右键或设置里改过）。"""
        act = getattr(self, "_act_outfit", None)
        if act is not None and act.isChecked() != bool(on):
            act.setChecked(bool(on))

    @staticmethod
    def _make_icon(library) -> QIcon:
        """
        优先用露娜的头部特写当图标；素材不可用时退回到一个手绘的小圆点，
        保证托盘一定能显示出来。
        """
        try:
            groups = library.groups
            if groups:
                path = library.path("f", groups[0], library.available("f", groups[0])[0])
                if path and path.exists():
                    pixmap = QPixmap(str(path))
                    if not pixmap.isNull():
                        square = min(pixmap.width(), pixmap.height())
                        pixmap = pixmap.copy((pixmap.width() - square) // 2, 0, square, square)
                        return QIcon(pixmap.scaled(64, 64, Qt.KeepAspectRatio,
                                                   Qt.SmoothTransformation))
        except Exception:  # noqa: BLE001 图标失败绝不能影响启动
            pass

        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(QColor(138, 111, 196))
        painter.setPen(QColor(238, 230, 255))
        painter.drawEllipse(2, 2, 60, 60)
        painter.setPen(QColor(255, 255, 255))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(28)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, "露")
        painter.end()
        return QIcon(pixmap)
