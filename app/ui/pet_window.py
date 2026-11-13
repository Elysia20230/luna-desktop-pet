# -*- coding: utf-8 -*-
"""
桌宠主窗口：一块真正透明的"贴纸"，只有露娜本人，没有窗口边框。

操作方式（都写在这里，方便日后改）：
    左键拖动   —— 搬家
    滚轮       —— 放大 / 缩小（0.3 ~ 2.0，以脚下位置为锚点，不会跑飞）
    双击       —— 打开 / 收起对话窗口
    右键       —— 菜单：聊天、看屏幕、换造型、换动作、恢复默认姿势、换尺寸、设置、记忆、退出

"换动作不换衣服"：组名是 a_b，a 是造型、b 是动作（0 抬手 / 2 抱臂）。
换动作只改 b；某个组合没有素材（例如造型⑦没有抱臂）就保持原样。

三个"活着"的小细节：
    眨眼       —— 每隔几秒换成一个闭眼表情，80~140 毫秒后换回来；
    呼吸       —— 整张图极轻微地上下浮动 1~2 像素；
    情绪动作   —— 说到得意/生气时换成抱臂（可在设置或右键菜单里关）。
前两个能在 config.json 的 behavior 段关掉。
"""
from __future__ import annotations

import random

from PySide6.QtCore import QPoint, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication, QImageReader, QPainter
from PySide6.QtWidgets import QMenu, QWidget

from .. import mood
from ..sprites import FAMILY_NAMES, SpriteCache, SpriteLibrary


class PetWindow(QWidget):
    """透明置顶的立绘窗口。"""

    # 交给 main.py 去接的"意图"信号，窗口本身不认识对话/设置这些模块
    chat_requested = Signal()
    screen_requested = Signal()
    settings_requested = Signal()
    memory_requested = Signal()
    history_requested = Signal()            # 打开聊天记录
    voice_toggle_requested = Signal(bool)   # 语音朗读开/关
    call_toggle_requested = Signal()        # 实时语音通话开/关（点一下切换）
    pet_moved = Signal(QRect)               # 她挪了位置/变了尺寸（输入框与气泡靠它跟随）
    outfit_changed = Signal(str)            # 衣服换了（无论自动还是手动）：带上造型编号
    outfit_auto_requested = Signal(bool)    # 右键菜单里"按时间自动换装"被点：True=开
    quit_requested = Signal()

    def __init__(self, cfg, library: SpriteLibrary, cache: SpriteCache) -> None:
        super().__init__(None)
        self.cfg = cfg
        self.library = library
        self.cache = cache

        # 显示状态
        self.family = cfg.get("sprite.family", "s") or "s"
        self.group = cfg.get("sprite.group", "0_0") or "0_0"
        self.outfit = self.library.outfit_of(self.group)   # 穿什么（换动作时不动它）
        self.pose = self.library.pose_of(self.group)       # 怎么站（0 抬手 / 2 抱臂）
        self.scale = float(cfg.get("sprite.scale", 1.0) or 1.0)
        self.emotion = "平静"
        self.index = self.library.resolve_index(self.emotion, self.family, self.group)

        self._pixmap = None
        self._closed_idx: int | None = None      # 眨眼时临时替换用的编号
        self._breathe = 0.0
        self._drag_offset: QPoint | None = None
        self._anchor_bottom_center = True

        self._setup_window()
        self._setup_timers()
        self.refresh(keep_anchor=False)
        self._restore_position()

    # ---- 窗口基础设置 -----------------------------------------------------
    def _setup_window(self) -> None:
        self.setWindowTitle("樱小路露娜")
        self.setWindowFlags(
            Qt.FramelessWindowHint          # 没有标题栏
            | Qt.WindowStaysOnTopHint       # 永远在最上面
            | Qt.Tool                       # 不在任务栏占一个格子
        )
        self.setAttribute(Qt.WA_TranslucentBackground)   # 关键：背景真透明
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setMouseTracking(True)

    def _setup_timers(self) -> None:
        # 眨眼
        self._blink_timer = QTimer(self)
        self._blink_timer.setSingleShot(True)
        self._blink_timer.timeout.connect(self._start_blink)
        self._schedule_blink()

        # 呼吸（33ms ≈ 30fps，足够顺滑又几乎不占 CPU）
        self._breathe_timer = QTimer(self)
        self._breathe_timer.timeout.connect(self._tick_breathe)
        if self.cfg.get("behavior.breathe", True):
            self._breathe_timer.start(33)

    # ---- 显示：换图 -------------------------------------------------------
    def set_emotion(self, emotion: str, use_pose: bool = False) -> None:
        """
        按情绪换表情。情绪词不认识时按"平静"处理。

        use_pose=True 且设置里开着"按情绪换动作"时，会顺手换个动作（只改 b，衣服 a 不动）。
        换动作只在这套造型 + 这个尺寸族确实有素材时才做，绝不切出一张空白图。
        """
        emo = mood.normalize(emotion) or "平静"
        self.emotion = emo
        if use_pose and self.cfg.get("sprite.auto_pose", True):
            self._apply_pose(self.library.pose_for(emo), persist=False)
        self.index = self.library.resolve_index(emo, self.family, self.group)
        self.refresh()

    def _apply_pose(self, pose: str, persist: bool = False) -> bool:
        """
        换动作（造型不变）。返回是否真的换成了。

        persist=False：说话时的自动换动作不写进配置——否则每句话都要存一次盘，
        重启后还会"记住"上一个情绪的姿势，用户会以为设置乱了。
        """
        pose = str(pose)
        if pose == self.pose:
            return True
        if not self.library.pose_usable(self.family, self.outfit, pose):
            return False            # 这套衣服没这个动作（例如造型⑦只有抬手）
        self.pose = pose
        self.group = self.library.group_key(self.outfit, pose)
        if persist:
            self.cfg.set("sprite.group", self.group)
            self.cfg.save()
        return True

    def set_pose(self, pose: str) -> None:
        """手动换动作（右键菜单用），会记住。"""
        if self._apply_pose(pose, persist=True):
            self.index = self.library.resolve_index(self.emotion, self.family, self.group)
            self.refresh()

    def _toggle_auto_pose(self) -> None:
        """切换「按情绪自动换动作」，立刻生效并写进配置。"""
        value = not bool(self.cfg.get("sprite.auto_pose", True))
        self.cfg.set("sprite.auto_pose", value)
        self.cfg.save()
        if value:
            self.set_emotion(self.emotion, use_pose=True)

    def restore_default(self) -> None:
        """回到默认动作 + 平静（说完话几秒后自动调用，也可手动触发）。"""
        self.group = self.library.default_group(self.outfit, self.family)
        self.outfit = self.library.outfit_of(self.group)
        self.pose = self.library.pose_of(self.group)
        self.emotion = "平静"
        self.index = self.library.resolve_index(self.emotion, self.family, self.group)
        self.refresh()

    def set_group(self, group: str, manual: bool = True) -> None:
        """换造型（完整组名 a_b，设置面板里的"造型"下拉用）。"""
        if group not in self.library.groups:
            return
        self.group = group
        self.outfit = self.library.outfit_of(group)
        self.pose = self.library.pose_of(group)
        self.cfg.set("sprite.group", group)
        self.cfg.save()
        self.index = self.library.resolve_index(self.emotion, self.family, self.group)
        self.refresh()
        if manual:
            # 手动换的：告诉 main.py，好让她记一个"别乱改"的保护期
            self.outfit_changed.emit(self.outfit)

    def set_outfit(self, outfit: str, manual: bool = True) -> None:
        """
        换衣服（右键菜单用）：尽量保留当前动作；这套造型没这个动作就退回默认动作。

        manual=False 表示"这是到点自动换的"，不要当成用户手动选择。
        """
        if outfit not in self.library.outfits:
            return
        pose = self.pose
        if not self.library.pose_usable(self.family, outfit, pose):
            pose = str(self.library.expressions.get("default_pose", "0"))
        group = self.library.group_key(outfit, pose)
        if not self.library.available(self.family, group):
            group = self.library.default_group(outfit, self.family)
        self.set_group(group, manual=manual)

    def _usable_group(self, family: str) -> str:
        """当前造型/动作在这个尺寸族下没素材时，找一个能用的组（优先同造型）。"""
        for pose in self.library.poses_for(self.outfit):
            group = self.library.group_key(self.outfit, pose)
            if self.library.available(family, group):
                return group
        for outfit in self.library.outfits:
            for pose in self.library.poses_for(outfit):
                group = self.library.group_key(outfit, pose)
                if self.library.available(family, group):
                    return group
        return self.group

    def set_family(self, family: str) -> None:
        """换尺寸族：f 头部特写 / s 半身 / m 中景 / l 近景。"""
        if family not in self.library.families:
            return
        self.family = family
        # l 族缺部分造型（如 4_x / 6_0）：换过去之前先确认有图
        if not self.library.available(self.family, self.group):
            self.group = self._usable_group(self.family)
            self.outfit = self.library.outfit_of(self.group)
            self.pose = self.library.pose_of(self.group)
        self.cfg.set("sprite.family", family)
        self.cfg.set("sprite.group", self.group)
        self.cfg.save()
        self.index = self.library.resolve_index(self.emotion, self.family, self.group)
        self.refresh()

    def set_scale(self, scale: float) -> None:
        """手动缩放（滚轮、菜单、设置面板都会走这里）。"""
        self.scale = max(0.3, min(2.0, float(scale)))
        self.cfg.set("sprite.scale", round(self.scale, 2))
        self.cfg.save()
        self.refresh()

    def fit_to_screen(self, ratio: float = 0.42) -> None:
        """
        首次启动时按屏幕高度挑一个合适的缩放，避免 4K/高 DPI 下立绘占满半个屏幕。

        ratio：立绘高度占「可用屏幕高度」的比例。想让她更大/更小，改这个数就行
        （例如 0.55 更大，0.30 更小）；用户之后用滚轮缩放会被记住，不再自动覆盖。
        """
        screen = QGuiApplication.primaryScreen().availableGeometry()
        path = self.library.path(self.family, self.group, self.index)
        if path is None or screen.height() <= 0:
            return
        # 只读图片头拿原始高度，不把 700KB 的整图解码一遍
        reader = QImageReader(str(path))
        raw_h = reader.size().height()
        if raw_h <= 0:
            return
        target = max(0.3, min(1.0, ratio * screen.height() / raw_h))
        self.set_scale(target)

    def refresh(self, keep_anchor: bool = True) -> None:
        """重新取图并调整窗口尺寸。"""
        dpr = self.devicePixelRatioF() or 1.0
        idx = self._closed_idx if self._closed_idx is not None else self.index
        pixmap = self.cache.get(self.family, self.group, idx, self.scale, dpr)
        if pixmap is None:
            # 该服装缺这个表情：退回该组第一个可用表情，避免窗口一片空白
            available = self.library.available(self.family, self.group)
            if available:
                self.index = available[0]
                pixmap = self.cache.get(self.family, self.group, self.index, self.scale, dpr)
        self._pixmap = pixmap

        if pixmap is None:
            self.resize(220, 320)
            self.update()
            return

        width = max(1, round(pixmap.width() / dpr))
        height = max(1, round(pixmap.height() / dpr))
        if keep_anchor and self._anchor_bottom_center:
            self._resize_keeping_feet(width, height)
        else:
            self.resize(width, height)
        self.update()

    def _resize_keeping_feet(self, width: int, height: int) -> None:
        """缩放时让脚下位置不动，视觉上像"长大/缩小"而不是"飞走"。"""
        geo = self.geometry()
        center_x = geo.x() + geo.width() // 2
        bottom = geo.y() + geo.height()
        self.resize(width, height)
        self.move(center_x - width // 2, bottom - height)

    # ---- 绘制 -------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 Qt 的命名
        if self._pixmap is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        dy = int(round(self._breathe))
        # 立绘底部对齐窗口底部：素材在下半身被裁掉，这样看起来像站在桌面上
        rect = QRect(0, dy, self.width(), self.height())
        painter.drawPixmap(rect, self._pixmap)

    # ---- 眨眼 / 呼吸 ------------------------------------------------------
    def _schedule_blink(self) -> None:
        if not self.cfg.get("behavior.blink", True):
            return
        self._blink_timer.start(random.randint(2800, 6500))

    def _start_blink(self) -> None:
        closed = self.library.closed_faces(self.family, self.group)
        if closed:
            self._closed_idx = random.choice(closed)
            self.refresh()
            QTimer.singleShot(random.randint(80, 140), self._end_blink)
        else:
            self._schedule_blink()

    def _end_blink(self) -> None:
        self._closed_idx = None
        self.refresh()
        self._schedule_blink()

    def _tick_breathe(self) -> None:
        import math

        self._breathe_phase = getattr(self, "_breathe_phase", 0.0) + 0.06
        # 振幅只有 1.5 像素，够"活着"又不打扰看字
        self._breathe = math.sin(self._breathe_phase) * 1.5
        self.update()

    # ---- 鼠标交互 ---------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._drag_offset is not None:
            self._drag_offset = None
            self.cfg.set("window.pet_pos", [self.x(), self.y()])
            self.cfg.save()

    # ---- 位置 / 尺寸变化通知 ----------------------------------------------
    def moveEvent(self, event) -> None:  # noqa: N802
        """
        她被拖走或自己挪位置时告诉外面一声。

        main.py 收到 `pet_moved` 就让"脚下的输入框"和"头顶的气泡"跟着走，
        所以拖到哪、输入框贴到哪，不会留在原地。
        """
        super().moveEvent(event)
        self.pet_moved.emit(self.frameGeometry())

    def resizeEvent(self, event) -> None:  # noqa: N802
        """缩放、换尺寸族、换造型导致窗口变了，也要重新贴一次。"""
        super().resizeEvent(event)
        self.pet_moved.emit(self.frameGeometry())

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.chat_requested.emit()

    def wheelEvent(self, event) -> None:  # noqa: N802
        step = 0.05 if event.angleDelta().y() > 0 else -0.05
        self.set_scale(round(self.scale + step, 2))
        event.accept()

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#2b2440;color:#f2e9ff;border:1px solid #6b5b8f;border-radius:8px;padding:6px;}"
            "QMenu::item{padding:6px 22px;border-radius:6px;}"
            "QMenu::item:selected{background:#8a6fc4;}"
            "QMenu::separator{height:1px;background:#4b3f68;margin:4px 8px;}"
        )

        act_chat = QAction("跟她说说话", menu)
        act_chat.triggered.connect(self.chat_requested.emit)
        menu.addAction(act_chat)

        act_history = QAction("聊天记录…", menu)
        act_history.triggered.connect(self.history_requested.emit)
        menu.addAction(act_history)

        act_screen = QAction("看看我在干什么", menu)
        act_screen.triggered.connect(self.screen_requested.emit)
        menu.addAction(act_screen)

        # 语音朗读：勾选状态直接读配置，改完立即生效
        act_voice = QAction("日语语音朗读", menu)
        act_voice.setCheckable(True)
        act_voice.setChecked(bool(self.cfg.get("voice.enabled", False)))
        act_voice.triggered.connect(
            lambda checked: self.voice_toggle_requested.emit(bool(checked))
        )
        menu.addAction(act_voice)

        # 实时语音通话：勾选 = 正在通话（状态由主窗口回灌，见 set_call_checked）
        act_call = QAction("语音通话（对着麦克风说话）", menu)
        act_call.setCheckable(True)
        act_call.setChecked(bool(self.cfg.get("voice.call_enabled", False)))
        act_call.triggered.connect(lambda _checked=False: self.call_toggle_requested.emit())
        self._act_call = act_call
        menu.addAction(act_call)
        menu.addSeparator()

        # 换造型（只列 7 套衣服，动作在下面的子菜单里单独选）
        outfit_menu = menu.addMenu("换造型")
        for outfit in self.library.outfits:
            act = QAction(self.library.outfit_name(outfit), outfit_menu)
            act.setCheckable(True)
            act.setChecked(outfit == self.outfit)
            act.triggered.connect(lambda _=False, o=outfit: self.set_outfit(o))
            outfit_menu.addAction(act)
        outfit_menu.addSeparator()
        # 自动换装开关也放在这个子菜单里：跟"衣服"是同一件事
        act_auto_outfit = QAction("按时间自动换装", outfit_menu)
        act_auto_outfit.setCheckable(True)
        act_auto_outfit.setChecked(bool(self.cfg.get("behavior.outfit_auto", True)))
        act_auto_outfit.triggered.connect(
            lambda checked: self.outfit_auto_requested.emit(bool(checked))
        )
        self._act_auto_outfit = act_auto_outfit
        outfit_menu.addAction(act_auto_outfit)

        # 换动作（姿势，不换衣服）
        pose_menu = menu.addMenu("换动作")
        for pose in ("0", "2"):
            poses_here = self.library.poses_for(self.outfit)
            act = QAction(self.library.pose_label(pose), pose_menu)
            act.setCheckable(True)
            act.setChecked(pose == self.pose)
            act.setEnabled(pose in poses_here)
            act.triggered.connect(lambda _=False, p=pose: self.set_pose(p))
            pose_menu.addAction(act)
        pose_menu.addSeparator()
        act_auto = QAction("说到得意/生气时自动抱臂", pose_menu)
        act_auto.triggered.connect(self._toggle_auto_pose)
        pose_menu.addAction(act_auto)

        act_restore = QAction("恢复默认姿势", menu)
        act_restore.triggered.connect(self.restore_default)
        menu.addAction(act_restore)

        # 换尺寸族
        size_menu = menu.addMenu("换尺寸")
        for family in self.library.families:
            act = QAction(FAMILY_NAMES.get(family, family), size_menu)
            act.setCheckable(True)
            act.setChecked(family == self.family)
            act.triggered.connect(lambda _=False, f=family: self.set_family(f))
            size_menu.addAction(act)

        # 缩放
        scale_menu = menu.addMenu(f"缩放（当前 {int(self.scale * 100)}%）")
        for pct in (50, 75, 100, 125, 150, 200):
            act = QAction(f"{pct}%", scale_menu)
            act.triggered.connect(lambda _=False, v=pct / 100: self.set_scale(v))
            scale_menu.addAction(act)

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

        menu.exec(event.globalPos())

    # ---- 位置记忆 ---------------------------------------------------------
    def place_default(self) -> None:
        """贴到可用区域右下角（避开任务栏），留一点边距。"""
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 40, screen.bottom() - self.height() - 10)

    def _restore_position(self) -> None:
        saved = self.cfg.get("window.pet_pos")
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if isinstance(saved, (list, tuple)) and len(saved) == 2:
            x, y = int(saved[0]), int(saved[1])
            # 万一之前那块显示器没了，别把它挪到看不见的地方
            if screen.contains(QPoint(x + self.width() // 2, y + 10)):
                self.move(x, y)
                return
        self.place_default()

    def place_near(self, x: int, y: int) -> None:
        """设置窗口位置（被 main.py 用来让对话窗贴着立绘摆放）。"""
        self.move(x, y)

    def sync_from_cfg(self, library: SpriteLibrary | None = None,
                      cache: SpriteCache | None = None) -> None:
        """
        设置面板保存后调用：把 config 里的族/造型/缩放重新读一遍。
        立绘目录换了时，library 和 cache 会一起换掉。
        """
        if library is not None:
            self.library = library
        if cache is not None:
            self.cache = cache
        self.family = self.cfg.get("sprite.family", self.family) or self.family
        self.group = self.cfg.get("sprite.group", self.group) or self.group
        self.scale = float(self.cfg.get("sprite.scale", self.scale) or self.scale)
        if self.group not in self.library.groups and self.library.groups:
            self.group = self.library.groups[0]
        if self.family not in self.library.families:
            self.family = self.library.families[0]
        # 造型/动作跟着组名走；换尺寸族后没素材就自动找一个能用的
        self.outfit = self.library.outfit_of(self.group)
        self.pose = self.library.pose_of(self.group)
        if not self.library.available(self.family, self.group):
            self.group = self._usable_group(self.family)
            self.outfit = self.library.outfit_of(self.group)
            self.pose = self.library.pose_of(self.group)
            self.cfg.set("sprite.group", self.group)
        self.index = self.library.resolve_index(self.emotion, self.family, self.group)
        self.refresh(keep_anchor=True)

    def set_call_checked(self, on: bool) -> None:
        """
        把右键菜单里"语音通话"的对勾刷新成真实状态。

        为什么不是菜单自己记：通话也可能从托盘点开，或者因为麦克风失败被弹回，
        所以勾选态统一由 main.py 按 CallSession.running 回灌。
        """
        act = getattr(self, "_act_call", None)
        if act is not None and act.isChecked() != bool(on):
            act.setChecked(bool(on))

    def set_outfit_auto_checked(self, on: bool) -> None:
        """刷新右键菜单里"按时间自动换装"的对勾（从设置面板改过之后也要同步）。"""
        act = getattr(self, "_act_auto_outfit", None)
        if act is not None and act.isChecked() != bool(on):
            act.setChecked(bool(on))
