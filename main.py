# -*- coding: utf-8 -*-
"""
露娜桌宠 · 启动入口。

这个文件只做一件事：把各个零件拼起来，然后把控制权交给 Qt 的事件循环。
零件清单（想改某一块，就去看对应文件）：
    app/ui/pet_window.py   透明置顶的立绘窗口
    app/ui/chat_window.py  对话气泡窗口
    app/ui/panels.py       设置 / 记忆面板
    app/ui/tray.py         右下角托盘图标
    app/chat/engine.py     把"人设+记忆+屏幕+用户输入"串起来
    app/memory/*           记忆的存取、向量化、检索、自动提炼
    app/screen/*           截图与要看懂屏幕
    app/sprites.py         表情与服装的切换逻辑

直接运行：  python main.py
打包后：    双击 启动露娜.bat（或 exe）
"""
from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

# 允许从任意目录启动（比如双击 .bat 时工作目录可能不对）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QGuiApplication, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from app.chat.engine import HISTORY_FILE, ChatEngine
from app.config import ROOT, Config, ensure_dirs
from app.memory.embedder import EmbeddingCache, build_embedder
from app.memory.retriever import MemoryRetriever
from app.memory.store import MemoryStore
from app.memory.vector_store import build_vector_store
from app.persona import Persona
from app.roles import load_roles, pick_role
from app.screen.context import ScreenWorker
from app.sprites import SpriteCache, SpriteLibrary
from app.ui.bubble import SpeechBubble, estimate_msec
from app.ui.chat_window import ChatWindow
from app.ui.input_bar import InputBar
from app.ui.panels import MemoryDialog, SettingsDialog
from app.ui.pet_window import PetWindow
from app.ui.quick_replies import QuickReplies
from app.ui.tray import LunaTray
from app.voice.call import CallSession
from app.voice.service import VoiceService
from app.voice.signature import (
    signature_candidates,
    signature_emotion,
    signature_hint,
    signature_path,
    signature_text,
)
from app.wardrobe import Wardrobe, load_wardrobe


def connect_engine_signals(controller, engine: ChatEngine) -> None:
    """
    把引擎的"她自己开口"和"后台小提示"接到界面上。

    为什么要单独抽成函数：改完设置会重建一个全新的 ChatEngine 对象，
    旧引擎身上的接线会随之作废。忘了重接的症状非常隐蔽——
    她明明生成了台词，界面却什么都不显示（用户以为"超时不说话"）。
    所以启动时和重建后都必须调用这一处。
    """
    engine.proactive_said.connect(controller._say)
    engine.screen_said.connect(controller._on_screen_said)
    engine.note.connect(controller._on_engine_note)


def connect_reply_signals(controller, engine: ChatEngine) -> None:
    """
    普通回复的三段式（开始/逐字/说完）也接到界面上。

    和上面同一类问题：引擎重建后这些信号也会断，所以单独成一个函数，
    启动时和"设置保存后"都要调一次。
    """
    engine.started.connect(controller._on_reply_started)
    engine.text_chunk.connect(controller._on_reply_chunk)
    engine.finished.connect(controller._on_reply)
    engine.failed.connect(controller._on_reply_failed)


class LunaApp(QObject):
    """总控：持有全部窗口与后台对象，负责它们之间的信号接线。"""

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self.app = app

        # ---- 基础对象 -----------------------------------------------------
        self.cfg = Config()
        ensure_dirs()          # 确保 config/ data/ 等目录存在

        self.library = SpriteLibrary(self.cfg)
        self.cache = SpriteCache(self.library)
        self.store = MemoryStore(max_facts=int(self.cfg.get("memory.max_facts", 2000)))

        # 角色卡：她是"哪种露娜"，取决于对面是谁（朝日 / 游星 / …）
        self.roles, self.roles_source, self.roles_warning = load_roles(self.cfg)
        self.role = pick_role(self.roles, self.cfg.get("persona.user_role", "asahi"))

        self.persona = Persona(
            include_relations=bool(self.cfg.get("persona.include_relations", True)),
            extra_note=self.cfg.get("persona.extra_note", ""),
            role=self.role,
        ).load()
        self.embedder, self.embed_label = build_embedder(self.cfg, EmbeddingCache())
        self.vector_store = build_vector_store(self.cfg, self._fingerprint())
        self.retriever = MemoryRetriever(
            self.store, self.embedder, self.vector_store,
            top_k=int(self.cfg.get("memory.top_k", 6)),
        )
        self.engine = ChatEngine(self.cfg, self.store, self.retriever, self.persona)
        # 被换下来的旧引擎先挂在这里，等它的对话线程自己结束再释放
        self._retired_engines: list = []

        # ---- 窗口 ---------------------------------------------------------
        self.pet = PetWindow(self.cfg, self.library, self.cache)
        # 第一次启动（还没有记忆位置）时，先按屏幕高度挑个合适的尺寸再摆位，
        # 免得在高分辨率/高 DPI 屏幕上一上来就占掉大半个桌面。
        if self.cfg.get("window.pet_pos") is None:
            self.pet.fit_to_screen()
            self.pet.place_default()

        # 服装管家：读 assets/wardrobe.json，决定"现在该穿哪套"
        wardrobe_table, self.wardrobe_warning = load_wardrobe()
        self.wardrobe = Wardrobe(self.cfg, wardrobe_table)
        self.wardrobe.current = self.pet.outfit
        self.chat = ChatWindow(self.engine)     # 只读的聊天记录窗口
        self.tray = LunaTray(self.library)
        self.bubble = SpeechBubble()            # 她说话时头顶那个小气泡
        self.input_bar = InputBar()             # 双击她之后，脚下的输入框
        # "接一句"小按钮：她主动说话时冒出来，省得你为了回一句还得双击打字
        self.quick = QuickReplies()
        self.voice = VoiceService(self.cfg, self)   # 日语语音（默认关）
        # 实时语音通话：麦克风 → 识别 → 对话 → 朗读 → 再听（默认关）
        self.call = CallSession(self.cfg, self.engine, self.voice, self)
        self._screen_worker: ScreenWorker | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._memory_dialog: MemoryDialog | None = None

        # 主动搭话用的小状态：你上次说话的时间、是否正在等截图
        self._last_user_at = 0.0
        self._pending_proactive = False

        # 流式气泡的节流状态（每 80ms 最多刷一次，别把界面刷爆）
        self._last_chunk_at = 0.0
        self._reply_text = ""

        # 角色卡里写了名字，就顺手记进记忆档案（提炼记忆时不会再被覆盖）
        try:
            self.store.update_profile(user_name=self.role.label)
        except Exception:  # noqa: BLE001 记不进档案也不影响启动
            pass

        self._wire()
        self._start_timers()
        self._startup_notes()

    # ---- 指纹：向量索引和"用哪个模型"绑在一起，换了模型要重建 ------------
    def _fingerprint(self) -> str:
        model = self.cfg.get("memory.embedding.model", "BAAI/bge-m3")
        return f"{self.embed_label}|{model}"

    # ---- 信号接线 ---------------------------------------------------------
    def _wire(self) -> None:
        # 立绘窗口发出的意图（双击 = 冒出脚下输入框）
        self.pet.chat_requested.connect(self.toggle_input_bar)
        self.pet.screen_requested.connect(lambda: self.look_at_screen(announce=True))
        self.pet.settings_requested.connect(self.open_settings)
        self.pet.memory_requested.connect(self.open_memory)
        self.pet.history_requested.connect(self.show_history)
        self.pet.voice_toggle_requested.connect(self.toggle_voice)
        self.pet.call_toggle_requested.connect(self.toggle_call)
        self.pet.quit_requested.connect(self.app.quit)

        # 脚下输入框
        self.input_bar.send_requested.connect(self._on_user_send)
        self.input_bar.history_requested.connect(self.show_history)
        self.input_bar.closed.connect(self._restore_default_pose)

        # 聊天记录窗口（只读）：清空记录 / 看屏幕 / 设置
        self.chat.clear_requested.connect(self.clear_history)
        self.chat.screen_requested.connect(lambda: self.look_at_screen(announce=True))
        self.chat.settings_requested.connect(self.open_settings)
        self.chat.closed.connect(self._restore_default_pose)

        # 引擎：她自己开口 + 普通回复（两套都要接，重建引擎后也要重接）
        connect_engine_signals(self, self.engine)
        connect_reply_signals(self, self.engine)
        self.bubble.clicked.connect(self.show_input_bar)

        # 换衣服：手动换的记保护期；自动换装的开关三处同步
        self.pet.outfit_changed.connect(self._on_outfit_changed)
        self.pet.outfit_auto_requested.connect(self._set_outfit_auto)
        self.tray.outfit_auto_requested.connect(self._set_outfit_auto)

        # "接一句"按钮：点了就当你说了一句话
        self.quick.chosen.connect(self._on_chip)
        self.quick.custom_requested.connect(self._on_chip_custom)

        # 她一动（拖动/缩放/换造型），脚下输入框和头顶气泡跟着走
        self.pet.pet_moved.connect(self._on_pet_moved)

        # 语音服务：出问题只在托盘轻提示，不打断聊天
        self.voice.failed.connect(self._on_engine_note)
        self.voice.status.connect(self._on_voice_status)

        # 实时语音通话：状态变了刷新托盘/输入框，听清了什么给她一个短暂提示
        self.call.state_changed.connect(self._on_call_state)
        self.call.heard.connect(self._on_call_heard)
        self.call.failed.connect(self._on_engine_note)

        # 托盘
        self.tray.chat_requested.connect(self.show_input_bar)
        self.tray.history_requested.connect(self.show_history)
        self.tray.screen_requested.connect(lambda: self.look_at_screen(announce=True))
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.memory_requested.connect(self.open_memory)
        self.tray.call_toggle_requested.connect(self.toggle_call)
        self.tray.toggle_pet.connect(self._toggle_pet)
        self.tray.quit_requested.connect(self.app.quit)
        self.tray.activated.connect(self._on_tray_activated)

    # ---- 定时器：自动看屏幕 / 自己嘀咕 ------------------------------------
    def _start_timers(self) -> None:
        # 自动看屏幕（默认关，设置里打开）
        self._screen_timer = QTimer(self)
        self._screen_timer.timeout.connect(lambda: self.look_at_screen(announce=False))
        self._apply_screen_timer()

        # 每隔一段时间她自己开口（可选先看一眼屏幕）
        self._idle_timer = QTimer(self)
        self._idle_timer.timeout.connect(self._proactive_tick)
        self._apply_idle_timer()

        # 表情/动作保持计时器：说完 N 秒后回到"默认姿势 + 平静"
        self._emotion_timer = QTimer(self)
        self._emotion_timer.setSingleShot(True)
        self._emotion_timer.timeout.connect(self._restore_default_pose)

        # 跟随去抖：拖动时 moveEvent 一秒能来几十次，16ms 合一次就够顺了
        self._follow_timer = QTimer(self)
        self._follow_timer.setSingleShot(True)
        self._follow_timer.setInterval(16)
        self._follow_timer.timeout.connect(self._follow_overlays)

    def _apply_screen_timer(self) -> None:
        if self.cfg.get("screen.auto", False):
            minutes = max(1, int(self.cfg.get("screen.interval_min", 10)))
            self._screen_timer.start(minutes * 60_000)
        else:
            self._screen_timer.stop()

    def _apply_idle_timer(self) -> None:
        minutes = int(self.cfg.get("behavior.idle_lines_min", 8) or 0)
        if minutes > 0:
            self._idle_timer.start(minutes * 60_000)
        else:
            self._idle_timer.stop()

    def _note_user_activity(self) -> None:
        """你刚发了消息：记下时间，接下来一小段她不会主动打扰。"""
        self._last_user_at = time.time()

    def _proactive_tick(self) -> None:
        """
        定时器到点：她该自己开口了。

        三个前提，缺一个就安静待着：
        1. 没有正在生成的回复（别抢话）；
        2. 你最近没说话（默认 3 分钟内不打扰）；
        3. 设置窗口没开着（免得气泡糊在设置面板上）。
        """
        if self.engine.busy:
            return
        gap = int(self.cfg.get("behavior.idle_min_gap_min", 3) or 0) * 60
        if gap > 0 and self._last_user_at and (time.time() - self._last_user_at) < gap:
            return
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            return

        # 要开口了：先看看现在几点/什么季节，该换衣服就换（深夜穿家居服等）
        self._wear_now()

        if self.cfg.get("behavior.idle_use_screen", False):
            # 先看一眼屏幕再说话：截图是异步的，等它回来再触发（见 _on_screen_done）
            if self._screen_worker is not None and self._screen_worker.isRunning():
                return      # 已经有一次截图在跑，别排队
            self._pending_proactive = True
            self.look_at_screen(announce=False)
        else:
            self.engine.send_proactive()

    # ---- 跟随：输入框 / 气泡追着桌宠跑 -------------------------------------
    def _on_pet_moved(self, _rect=None) -> None:
        """她动了：不要立刻挪（拖动时一秒几十次），攒 16ms 再统一挪一次。"""
        timer = getattr(self, "_follow_timer", None)
        if timer is not None:
            timer.start()
        else:
            self._follow_overlays()

    def _follow_overlays(self) -> None:
        """把脚下输入框、头顶气泡、"接一句"按钮重新贴到桌宠旁边。"""
        if self.input_bar.isVisible():
            self.input_bar.follow(self.pet.frameGeometry())
        self.bubble.reposition(self._bubble_anchor())
        if self.quick.isVisible():
            self.quick.reposition(self.bubble.frameGeometry())

    def _bubble_anchor(self) -> QRect:
        """
        气泡该贴在谁头上：

        平时贴桌宠头顶；如果输入框已经翻到桌宠头顶了（她贴着屏幕底），
        气泡就改贴"输入框的头顶"，否则两个会叠在一起看不清。
        """
        if self.input_bar.isVisible() and self.input_bar.side == "above":
            return self.input_bar.frameGeometry()
        return self.pet.frameGeometry()

    # ---- 说话：气泡 + 语音 + 表情 -----------------------------------------
    def _on_user_send(self, text: str) -> None:
        """用户在脚下输入框里敲了一句话。"""
        text = (text or "").strip()
        if not text or self.engine.busy:
            return
        self.quick.hide_chips()      # 真要说话了，快捷回复按钮就没用了
        self._note_user_activity()
        self.input_bar.set_busy(True)
        self._reply_text = ""
        self.engine.send(text)

    def _on_reply_started(self) -> None:
        """她开始"想"了：气泡先冒一个省略号，输入框先置灰。"""
        self.quick.hide_chips()
        self._wear_now()             # 说话这一刻顺便对一下衣服
        self.input_bar.set_busy(True)
        self._reply_text = ""
        self._last_chunk_at = 0.0
        self.bubble.show_text("…", self._bubble_anchor(), 60_000)
        # 语音流水线：现在就开始等（服务没起来的话她一边想一边起）
        self.voice.begin_reply()

    def _on_reply_chunk(self, text: str) -> None:
        """流式文字：最多每 80ms 刷一次气泡，省 CPU 也不闪；文字同时喂给语音分段。"""
        self._reply_text = text or ""
        self.voice.feed(self._reply_text)
        now = time.time()
        if now - self._last_chunk_at < 0.08:
            return
        self._last_chunk_at = now
        self.bubble.update_text(self._reply_text, self._bubble_anchor())

    def _on_reply(self, text: str, emotion: str) -> None:
        """一句说完了：气泡定格、换表情、记进记录、念出来。"""
        text = text or self._reply_text
        self.input_bar.set_busy(False)
        self._apply_emotion(emotion)
        default_ms = int(self.cfg.get("behavior.bubble_seconds", 9) or 9) * 1000
        self.bubble.show_text(text, self._bubble_anchor(),
                              estimate_msec(text, default_ms))
        if self.chat.isVisible():
            self.chat.add_message("assistant", text)
        # 收尾：让语音把剩下的文字合完、按顺序放出来
        self.voice.end_reply(emotion)

    def _on_reply_failed(self, message: str) -> None:
        """出错（没 Key、网络断、超时）：气泡说一句人话，表情转失落。"""
        self.input_bar.set_busy(False)
        self.voice.end_reply("")
        self._apply_emotion("失落")
        self.bubble.show_text(message, self._bubble_anchor(), 12_000)

    def _on_screen_said(self, text: str, emotion: str) -> None:
        """
        她看完屏幕随口说的那句。

        和普通回复一样出气泡、换表情、念出来，
        但**不写进聊天记录、不算一轮对话**（engine 那边也没存）。
        """
        text = text or ""
        self._wear_now()
        self.input_bar.set_busy(False)
        self._apply_emotion(emotion)
        default_ms = int(self.cfg.get("behavior.bubble_seconds", 9) or 9) * 1000
        self.bubble.show_text(text, self._bubble_anchor(),
                              estimate_msec(text, default_ms))
        # 收尾：让语音把剩下的文字合完、按顺序放出来
        self.voice.end_reply(emotion)

    def _say(self, text: str, emotion: str) -> None:
        """她主动开口（自己嘀咕）：和普通回复一样走气泡，必要时也念出来。"""
        self._wear_now()
        self._apply_emotion(emotion)
        default_ms = int(self.cfg.get("behavior.bubble_seconds", 9) or 9) * 1000
        self.bubble.show_text(text, self._bubble_anchor(),
                              estimate_msec(text, default_ms))
        if self.chat.isVisible():
            self.chat.add_message("assistant", text)
        self.voice.speak(text, emotion, kind="proactive")
        # 主动搭话最容易"想接却懒得打字"，所以这时给几个可以直接点的回复
        self._show_reply_chips()

    # ---- 表情 / 动作 -------------------------------------------------------
    def _apply_emotion(self, emotion: str, use_pose: bool = True) -> None:
        """按情绪换表情，并（可选）换个动作；随后开始"几秒后恢复默认"的倒计时。"""
        self.pet.set_emotion(emotion, use_pose=use_pose)
        self._arm_emotion_reset()

    def _arm_emotion_reset(self) -> None:
        """说完一句后重新开始计时；用户不想自动恢复就别启。"""
        timer = getattr(self, "_emotion_timer", None)
        if timer is None:
            return
        if not self.cfg.get("behavior.restore_after_reply", True):
            timer.stop()
            return
        seconds = int(self.cfg.get("behavior.emotion_hold_seconds", 8) or 0)
        if seconds <= 0:
            timer.stop()          # 0 = 一直保持到下次说话
            return
        timer.start(seconds * 1000)

    def _restore_default_pose(self) -> None:
        """回到默认动作 + 平静表情。对话窗关掉时也会立刻调用一次。"""
        self.pet.restore_default()

    # ---- 换衣服：按时间 / 季节 -------------------------------------------
    def _prune_retired_engines(self) -> None:
        """
        回收已经退役的旧引擎。

        判定标准是"线程对象已经没了"：ChatEngine._on_worker_done 在线程真正结束后
        才清引用，所以这里过滤掉还挂着线程的引擎，剩下的才允许被垃圾回收。
        """
        try:
            self._retired_engines = [e for e in self._retired_engines if e.has_worker()]
        except Exception:  # noqa: BLE001 清理失败不影响主流程
            self._retired_engines = []

    def _on_outfit_changed(self, outfit: str) -> None:
        """
        用户手动换了衣服（右键菜单或设置面板）。

        记一个"保护期"：接下来两小时里自动换装不插手，否则你刚挑好一套，
        她到点就先给换了，体验很差。
        """
        self.wardrobe.note_manual(outfit)

    def _set_outfit_auto(self, enabled: bool) -> None:
        """打开/关闭「按时间自动换装」（立绘右键、托盘、设置面板三处共用）。"""
        enabled = bool(enabled)
        self.cfg.set("behavior.outfit_auto", enabled)
        self.cfg.save()
        self.wardrobe.refresh()
        self._sync_outfit_menus()
        if enabled:
            self._wear_now(force=True)
            self.tray.showMessage(
                "露娜", f"好，我会按时段自己换衣服。现在：{self.wardrobe.describe()}",
                QSystemTrayIcon.Information, 4000,
            )
        else:
            self.tray.showMessage(
                "露娜", "那我不自己换衣服了，你想换随时右键「换造型」。",
                QSystemTrayIcon.Information, 3000,
            )

    def _sync_outfit_menus(self) -> None:
        """把两个菜单里的对勾刷成配置里的真实状态。"""
        on = bool(self.cfg.get("behavior.outfit_auto", True))
        self.pet.set_outfit_auto_checked(on)
        self.tray.set_outfit_auto_checked(on)

    def _wear_now(self, force: bool = False) -> None:
        """
        该换衣服了吗？该换就换，不该换就什么都不做。

        force=True 表示"忽略最短间隔"，只在开关刚打开/刚改设置时用。
        """
        try:
            available = set(self.library.outfits)
            desired = self.wardrobe.desired(available=available)
        except Exception as exc:  # noqa: BLE001 换装失败不能影响说话
            if not getattr(self, "_warned_wardrobe", False):
                self._warned_wardrobe = True
                self.tray.showMessage("露娜", f"换衣服的时候出了点岔子：{exc}",
                                      QSystemTrayIcon.Warning, 4000)
            return

        if force:
            # 强制换：绕开"最短换装间隔"重算一次（开关刚打开/刚改完设置时用）
            desired = self.wardrobe.desired(available=available, ignore_gap=True)

        if not desired or desired == self.pet.outfit:
            return
        self.pet.set_outfit(desired, manual=False)
        self.wardrobe.mark_switched(desired)
        self.cfg.save()
        if self.cfg.get("behavior.debug_save", False):
            print(f"[服装] 换成 {desired}（{self.wardrobe.name_of(desired)}）")

    # ---- 接一句：她主动说话时的小按钮 -------------------------------------
    def _chip_options(self) -> list[str]:
        """读配置里的现成短句（支持列表或「嗯？/我在呢」这种斜杠写法）。"""
        raw = self.cfg.get("behavior.reply_chips", []) or []
        if isinstance(raw, str):
            raw = [part for part in raw.replace("/", "|").split("|")]
        return [str(item).strip() for item in raw if str(item).strip()][:4]

    def _show_reply_chips(self) -> None:
        """她主动开口之后，在气泡下方给出几个可以直接点的回复。"""
        if not self.cfg.get("behavior.quick_replies", True):
            return
        if self.engine.busy or self.call.running:
            return          # 通话中本来就是实时接话，不用再给按钮
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            return
        if not self.bubble.isVisible():
            return
        options = self._chip_options()
        if not options:
            return
        seconds = int(self.cfg.get("behavior.chips_seconds", 12) or 0)
        self.quick.show_for(self.bubble.frameGeometry(), options, seconds)

    def _on_chip(self, text: str) -> None:
        """点了某个现成短句：当成你说的一句话，走正常对话（有记录、有记忆、有语音）。"""
        self.quick.hide_chips()
        self.bubble.hide_bubble()
        if self.engine.busy:
            self.tray.showMessage("露娜", "她正说着呢，等这句说完。",
                                  QSystemTrayIcon.Information, 2500)
            return
        self._note_user_activity()
        self._reply_text = ""
        self.engine.send(text)

    def _on_chip_custom(self) -> None:
        """点了「自己写…」：收起按钮、打开脚下输入框。"""
        self.quick.hide_chips()
        self.show_input_bar()

    def test_proactive(self) -> None:
        """设置里「让她现在说一句」：绕过计时器，立刻演一次。"""
        if self.engine.busy:
            self.tray.showMessage("露娜", "她正说着呢，等这句说完。",
                                  QSystemTrayIcon.Information, 3000)
            return
        self.engine.send_proactive()

    def _on_screen_failed(self, err: str) -> None:
        """
        看一眼屏幕失败时别把"搭话"这件事一起吞掉。

        症状：勾了「先偷看屏幕」以后，截图失败就把 _pending_proactive 永久挂住，
        于是她再也不主动开口，用户只会觉得"超时了"。
        """
        self.tray.showMessage("露娜", f"这回没看清屏幕（{err}）。我先照常说一句。",
                              QSystemTrayIcon.Warning, 4000)
        if self._pending_proactive:
            self._pending_proactive = False
            self.engine.send_proactive()

    def _on_engine_note(self, text: str) -> None:
        """引擎的后台小提示（自己搭话失败之类），用托盘轻量提示，不打断。"""
        self.tray.showMessage("樱小路露娜", text, QSystemTrayIcon.Information, 4000)

    def _on_voice_status(self, text: str) -> None:
        """
        语音流水线的进度（"正在准备语音……""翻译中……"之类）。

        只写进设置里那个状态标签：她不说话时不该被托盘提示刷屏。
        """
        dialog = self._settings_dialog
        if dialog is not None and hasattr(dialog, "set_voice_status"):
            try:
                dialog.set_voice_status(text)
            except Exception:  # noqa: BLE001 界面细节出错不影响她说话
                pass


    def self_test_screen(self) -> None:
        """
        自检识屏：先抓一张屏，确认"看得见"。

        为什么要有：截图依赖系统权限（远程桌面、某些安全软件都会挡），
        挡了以后她只会用窗口标题搭话，用户可能以为是"她变笨了"。
        """
        from app.screen.context import grab_screen_png

        try:
            png = grab_screen_png(1280)
        except Exception as exc:  # noqa: BLE001
            self.tray.showMessage("截图失败", f"抓屏出错了：{exc}",
                                  QSystemTrayIcon.Warning, 6000)
            return
        if png:
            self.tray.showMessage("看得见",
                                  f"截图正常（{len(png) // 1024} KB）。她可以对着画面搭话了。",
                                  QSystemTrayIcon.Information, 5000)
        else:
            self.tray.showMessage("看不见",
                                  "抓到的是空图：可能被安全软件挡住了，"
                                  "她只能靠窗口标题跟你搭话。",
                                  QSystemTrayIcon.Warning, 6000)

    # ---- 动作 -------------------------------------------------------------
    def start(self) -> None:
        self.pet.show()
        self.tray.show()
        if self.chat.geometry().x() == 0 and self.chat.geometry().y() == 0:
            self._place_chat_near_pet()
        self._sync_outfit_menus()     # 三个地方的对勾先对齐
        self._wear_now(force=True)    # 一启动就先穿对（深夜直接是家居服）
        self.voice.warm_up()          # 语音开着就先悄悄把服务拉起来（不阻塞界面）
        # 上次关程序时通话是开着的，就自动接着开（麦克风权限失败会自己弹回）
        if bool(self.cfg.get("voice.call_enabled", False)):
            QTimer.singleShot(1200, self.toggle_call)

    # ---- 输入框 / 记录窗口 -------------------------------------------------
    def toggle_input_bar(self) -> None:
        """双击立绘（或点托盘「跟她说话」）：冒出 / 收起脚下输入框。"""
        self.quick.hide_chips()
        self.input_bar.toggle(self.pet.frameGeometry())
        self._follow_overlays()      # 输入框换了位置，气泡也让一让

    def show_input_bar(self) -> None:
        self.quick.hide_chips()
        if not self.input_bar.isVisible():
            self.input_bar.show_bar(self.pet.frameGeometry())
        self.input_bar.focus_input()
        self._follow_overlays()

    def show_history(self) -> None:
        """打开聊天记录：从 data/history.json 读最近 200 条。"""
        self.quick.hide_chips()
        self.chat.load_entries(self._read_history())
        geo = self.chat.geometry()
        if geo.x() == 0 and geo.y() == 0:
            self._place_chat_near_pet()
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()

    def clear_history(self) -> None:
        """清空聊天记录（引擎内存 + 磁盘文件 + 窗口）。"""
        try:
            self.engine.clear_history()
        except Exception:  # noqa: BLE001 引擎没这个方法也不要紧，文件照清
            pass
        try:
            HISTORY_FILE.write_text("[]", encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        self.chat.load_entries([])
        self.tray.showMessage("露娜", "都忘掉了。这样也好。",
                              QSystemTrayIcon.Information, 3000)

    @staticmethod
    def _read_history() -> list[dict]:
        """读 history.json（引擎写的格式是 [{"role":…,"content":…}, …]）。"""
        import json

        try:
            if HISTORY_FILE.is_file():
                data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return data
        except Exception:  # noqa: BLE001 读坏了就当没有记录
            pass
        return []

    def toggle_voice(self, enabled: bool) -> None:
        """右键菜单「语音朗读」勾选：立刻生效并记住。"""
        self.cfg.set("voice.enabled", bool(enabled))
        self.cfg.save()
        if enabled:
            self.voice.reload()
            self.voice.warm_up()
            tip = "那就用日语说话吧。"
        else:
            self.voice.stop()
            self.voice.server.stop()
            tip = "不想听就算了。"
        self.tray.showMessage("露娜", tip, QSystemTrayIcon.Information, 3000)

    def toggle_call(self) -> None:
        """
        开 / 关实时语音通话（右键立绘、托盘、设置里的开关都走这里）。

        开之前先确认语音朗读是开着的：通话的"回答"靠日语朗读，
        朗读关着的话她能听懂但不出声，体验很怪。
        """
        if self.call.running:
            self.call.stop()
            self.cfg.set("voice.call_enabled", False)
            self.cfg.save()
            self.tray.showMessage("露娜", "那就先这样。", QSystemTrayIcon.Information, 3000)
            return

        self.quick.hide_chips()      # 通话是实时接话，屏幕上别再多一排按钮

        if not bool(self.cfg.get("voice.enabled", False)):
            self.tray.showMessage(
                "语音没开",
                "去设置 → 语音里打开「用日语念出来」，再回来开通话。",
                QSystemTrayIcon.Warning, 5000,
            )
            return

        if self.call.start():
            self.cfg.set("voice.call_enabled", True)
            self.cfg.save()
            self.tray.showMessage("露娜", "说吧，我听着。", QSystemTrayIcon.Information, 3000)
        else:
            self.cfg.set("voice.call_enabled", False)
            self.cfg.save()
            self._on_call_state(self.call.state)

    def _on_call_state(self, text: str) -> None:
        """通话状态变了：托盘 tooltip、输入框提示、两个菜单的对勾一起更新。"""
        running = self.call.running
        self.tray.setToolTip(f"樱小路露娜 · {text}" if running else "樱小路露娜 · 桌面宠物（右键有菜单）")
        try:
            self.input_bar.set_call_hint(text if running else "")
        except Exception:  # noqa: BLE001 输入框还没建好也不影响
            pass
        self.pet.set_call_checked(running)
        self.tray.set_call_checked(running)

    def _on_call_heard(self, text: str) -> None:
        """通话里听清了你说什么：气泡上短暂显示一下"你说：…"，方便确认没听错。"""
        if not text:
            return
        self.bubble.show_text(f"你说：{text}", self._bubble_anchor(), 900)

    def test_voice(self) -> None:
        """设置里「用日语说一句」：确认整条链路（翻译→合成→播放）是通的。"""
        if not self.voice.should_speak("reply"):
            self.tray.showMessage(
                "语音没开",
                "先去设置 → 语音里打开开关，把参考音频配好再试。",
                QSystemTrayIcon.Warning, 5000,
            )
            return
        line = "……哼，声音还听得入耳吧？"
        self.bubble.show_text(line, self._bubble_anchor(), 8000)
        self.voice.speak(line, "得意", kind="reply")

    def play_signature(self) -> None:
        """
        设置里「让她说一句「心情大好～」」。

        和上面几个"测试"按钮刻意分开：这里只做三件事——
            放本地这段日语录音 → 切成得意 + 抱臂 → 头顶气泡显示中文，
        不经过翻译、不经过 GPT-SoVITS 合成、不写聊天记录，
        所以语音开关关着、服务没起来，这个按钮也能正常响。
        """
        path = signature_path(self.cfg)
        if not path.is_file():
            # 把四个候选位置都列出来，用户一眼就知道该往哪放
            where = "\n".join("· " + str(p) for p in signature_candidates(self.cfg)[:4])
            self.tray.showMessage(
                "找不到音频",
                signature_hint() + "\n\n找过这些地方：\n" + where,
                QSystemTrayIcon.Warning, 9000,
            )
            return

        volume = float(self.cfg.get("voice.volume", 1.0) or 1.0)
        try:
            duration = float(self.voice.player.play(path, volume=volume))
        except Exception as exc:  # noqa: BLE001 播放失败不该把程序带崩
            self.tray.showMessage(
                "放不出来",
                f"{exc}\n\n这段只影响这个按钮，聊天和立绘都正常。",
                QSystemTrayIcon.Warning, 6000,
            )
            return

        text = signature_text(self.cfg)
        # 先换表情动作（`_apply_emotion` 会顺手开始"几秒后恢复默认"的倒计时）
        self._apply_emotion(signature_emotion())
        # 气泡至少留到音频放完，稍多 0.8 秒，免得话音刚落字就没了
        self.bubble.show_text(
            text, self._bubble_anchor(),
            max(int(duration * 1000) + 800, estimate_msec(text)),
        )

    def _place_chat_near_pet(self) -> None:
        """把记录窗口摆在立绘左侧，别挡住她。"""
        geo = self.pet.frameGeometry()
        screen = QGuiApplication.primaryScreen().availableGeometry()
        x = max(screen.left() + 8, geo.left() - self.chat.width() - 12)
        y = min(max(screen.top() + 8, geo.top()), screen.bottom() - self.chat.height() - 8)
        self.chat.move(x, y)

    def _toggle_pet(self) -> None:
        if self.pet.isVisible():
            self.quick.hide_chips()      # 人都不在屏幕上了，按钮也别留着
        self.pet.setVisible(not self.pet.isVisible())

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:      # 单击托盘图标 = 冒出发送框
            self.toggle_input_bar()

    def look_at_screen(self, announce: bool = True) -> None:
        """
        announce=True：用户主动点「看看我在干什么」——截一张，让她就着画面随口说一句
                        （走 engine.send_screen_comment()，**不算一次对话**）；
        announce=False：定时自动看，只更新背景信息，不打断主人。
        """
        if announce and self.engine.busy:
            # 她正说着话：这会儿再拍一张也没用，界面还会毫无反应，容易以为程序坏了
            self.tray.showMessage("露娜", "她正说着呢，等这句说完。",
                                  QSystemTrayIcon.Information, 3000)
            return
        if self._screen_worker is not None and self._screen_worker.isRunning():
            return
        self._screen_worker = ScreenWorker(
            self.cfg, with_vision=bool(self.cfg.get("vision.enabled", False)))
        self._screen_worker.done.connect(lambda digest: self._on_screen_done(digest, announce))
        self._screen_worker.failed.connect(self._on_screen_failed)
        self._screen_worker.start()

    def _on_screen_done(self, digest, announce: bool) -> None:
        text = digest.as_prompt()
        self.engine.set_screen_text(text)
        if self.cfg.get("behavior.debug_save", False) and digest.image_png:
            shot = Path(self.cfg.path.parent.parent) / "data" / "last_screen.png"
            shot.parent.mkdir(parents=True, exist_ok=True)
            shot.write_bytes(digest.image_png)

        if announce:
            # 主人让她看的：直接让她就着画面说一句。
            # 注意这里**不再** show_input_bar() + engine.send("看看我在干什么？")——
            # 那条路径会写 history、进聊天记录、触发记忆提炼，等于凭空多出一轮对话。
            # 输入框如果本来就开着，就保持原样（不强制打开、也不收起）。
            self.engine.send_screen_comment()
        elif self._pending_proactive:
            # 这次截图是"自己搭话"的前置动作：拿到了画面，就让她开口
            self._pending_proactive = False
            self.engine.send_proactive()
        elif text:
            self.tray.showMessage("露娜偷偷看了一眼", text[:60],
                                  QSystemTrayIcon.Information, 4000)

    # ---- 设置 / 记忆面板 --------------------------------------------------
    def open_settings(self) -> None:
        self.quick.hide_chips()      # 别让按钮糊在设置面板上
        dialog = SettingsDialog(self.cfg, self.library,
                                roles=self.roles, roles_source=self.roles_source,
                                voice=self.voice, call=self.call)
        dialog.memory_open_requested.connect(self.open_memory)
        dialog.rebuild_requested.connect(self.rebuild_index)
        dialog.screen_test_requested.connect(self.self_test_screen)
        dialog.proactive_test_requested.connect(self.test_proactive)
        dialog.voice_test_requested.connect(self.test_voice)
        dialog.signature_requested.connect(self.play_signature)
        dialog.saved.connect(self._on_settings_saved)
        self._settings_dialog = dialog
        dialog.exec()

    def _on_settings_saved(self) -> None:
        """设置改完：重建人设、向量服务、引擎，并让立绘立即应用新参数。"""
        # 身份可能刚被改过（朝日 / 游星 / …）：重读角色卡再拼人设
        self.roles, self.roles_source, self.roles_warning = load_roles(self.cfg)
        self.role = pick_role(self.roles, self.cfg.get("persona.user_role", "asahi"))
        self.persona = Persona(
            include_relations=bool(self.cfg.get("persona.include_relations", True)),
            extra_note=self.cfg.get("persona.extra_note", ""),
            role=self.role,
        ).load()
        try:
            self.store.update_profile(user_name=self.role.label)
        except Exception:  # noqa: BLE001
            pass
        self.embedder, self.embed_label = build_embedder(self.cfg, EmbeddingCache())
        self.vector_store = build_vector_store(self.cfg, self._fingerprint())
        self.retriever = MemoryRetriever(
            self.store, self.embedder, self.vector_store,
            top_k=int(self.cfg.get("memory.top_k", 6)),
        )
        retired, self.engine = self.engine, ChatEngine(self.cfg, self.store, self.retriever, self.persona)
        # 旧引擎不能直接丢：它的对话线程可能还在跑，直接销毁会触发
        # qFatal("QThread: Destroyed while thread is still running") → 闪退。
        retired.stop()
        self._retired_engines.append(retired)
        self._prune_retired_engines()
        # 新引擎是一张白纸：主动搭话 / 后台提示 / 普通回复都必须重新接上
        # （漏了就会"她说话了但界面没反应"，用户只会以为她超时不吭声）
        connect_engine_signals(self, self.engine)
        connect_reply_signals(self, self.engine)

        # 语音：重读音色表，按开关决定是预热还是直接关掉服务
        self.voice.reload()
        if self.voice.enabled:
            self.voice.warm_up()
        else:
            self.voice.stop()
            self.voice.server.stop()

        # 通话：引擎/语音都换了新的，引用要跟上；开关状态以设置为准
        self.call.engine = self.engine
        self.call.voice = self.voice
        self.call.recorder.cfg = self.cfg      # 麦克风/阈值可能刚被改过
        want_call = bool(self.cfg.get("voice.call_enabled", False))
        if want_call and not self.call.running and self.voice.enabled:
            self.call.start()
        elif not want_call and self.call.running:
            self.call.stop()
        self._on_call_state(self.call.state)

        # 立绘：目录/尺寸/造型/缩放
        self.library.reload()
        self.cache.clear()
        self.pet.sync_from_cfg(self.library, self.cache)
        self.pet.set_emotion(self.pet.emotion)

        # 服装管家：配置里的时段/季节/睡衣造型可能刚被改过，立刻按新规则穿一次
        self.wardrobe.current = self.pet.outfit
        self.wardrobe.refresh()
        self._sync_outfit_menus()
        self._wear_now(force=True)

        self._apply_screen_timer()
        self._apply_idle_timer()
        self.tray.showMessage("露娜", "设置好了。这回别再乱改了吧。",
                              QSystemTrayIcon.Information, 3000)

    def open_memory(self) -> None:
        dialog = MemoryDialog(self.store, self.retriever)
        dialog.rebuild_requested.connect(self.rebuild_index)
        dialog.changed.connect(self._sync_index_safely)
        self._memory_dialog = dialog
        dialog.exec()

    def rebuild_index(self) -> None:
        try:
            note = self.retriever.rebuild()
        except Exception as exc:  # noqa: BLE001
            note = f"重建失败：{exc}"
        self.tray.showMessage("露娜的记忆", note, QSystemTrayIcon.Information, 5000)
        if self._memory_dialog is not None:
            self._memory_dialog.reload()

    def _sync_index_safely(self) -> None:
        """手动加/删记忆后，补一下这条的向量（失败也不影响用）。"""
        try:
            self.retriever.sync_index()
        except Exception:  # noqa: BLE001
            pass

    # ---- 首次启动的小提示 -------------------------------------------------
    def _startup_notes(self) -> None:
        notes = []
        if not self.cfg.api_key:
            notes.append("还没填 DeepSeek 的 Key，现在只能听见内置台词。右键 → 设置。")
        if self.library.warnings:
            notes.extend(self.library.warnings)
        if self.persona.load_warning:
            notes.append(self.persona.load_warning)
        if self.roles_warning:
            notes.append(self.roles_warning)
        if getattr(self, "wardrobe_warning", ""):
            notes.append(self.wardrobe_warning)
        if notes:
            QTimer.singleShot(1500, lambda: self.tray.showMessage(
                "露娜桌宠", "；".join(notes), QSystemTrayIcon.Information, 8000))

        # 后台顺手补一下没建过向量的记忆（不阻塞启动）
        QTimer.singleShot(4000, self._sync_index_safely)


def _install_excepthook(tray: LunaTray | None = None) -> Path:
    """
    装一个"兜底"：任何没人接住的 Python 异常都写进 logs/运行错误.txt。

    为什么需要：双击 .bat 启动时用的是 pythonw，没有控制台，Qt 槽函数里抛出的异常
    会被静默吞掉（界面就卡在"露娜正在斟酌用词…"这种状态，用户完全不知道发生了什么）。
    写进日志 + 弹一次托盘气泡，至少能让人知道"出错了、去哪看"。
    """
    log_dir = ROOT / "logs"
    log_path = log_dir / "运行错误.txt"
    state = {"notified": False}

    def hook(exc_type, exc, tb) -> None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            text = "".join(traceback.format_exception(exc_type, exc, tb))
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} 未捕获的异常 =====\n")
                fh.write(text)
        except Exception:  # noqa: BLE001 写日志都失败了就只能放弃
            pass

        # 保留 Python 原本的行为（有控制台时仍然打印），但 pythonw 下 stderr 可能是 None
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

        if tray is not None and not state["notified"]:
            state["notified"] = True          # 每个进程只打扰一次，别刷屏
            try:
                tray.showMessage("露娜桌宠",
                                 "出了点小状况，详情见 logs/运行错误.txt",
                                 QSystemTrayIcon.Warning, 6000)
            except Exception:  # noqa: BLE001
                pass

    sys.excepthook = hook
    return log_path


def main() -> int:
    # Qt6 默认就处理高 DPI；这里只关掉"最后一个窗口关闭就退出"，
    # 因为桌宠窗口被隐藏时程序应该继续待命。
    QApplication.setQuitOnLastWindowClosed(False)
    app = QApplication(sys.argv)
    app.setApplicationName("LunaPet")

    controller = LunaApp(app)
    _install_excepthook(controller.tray)
    app.setWindowIcon(QIcon(controller.tray.icon()))
    # 退出时把语音服务（我们自己拉起的 api_v2.py 进程）一起收掉，别留后台
    app.aboutToQuit.connect(controller.call.stop)      # 先放掉麦克风，再关语音服务
    app.aboutToQuit.connect(controller.voice.shutdown)
    controller.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
