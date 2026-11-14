# -*- coding: utf-8 -*-
"""
设置面板 + 记忆管理面板。

设计目标：让"完全不懂代码的人"也能把 API Key 填进去、把记忆删掉。
所以这里全部是看得见摸得着的输入框和按钮，没有需要手改 JSON 的地方
（当然 config/config.json 仍然可以手改，改完重启即可）。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSlider, QSpinBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..mood import EMOTIONS
from .. import providers
from ..sprites import FAMILY_NAMES, SpriteLibrary
from ..voice import mics
from ..voice import probe as voice_probe
from ..voice.signature import signature_path
from ..voice.voices import save_emotion_overrides
from .section import CollapsibleSection

_DIALOG_QSS = """
QDialog{background:#241d38;color:#f0eaff;}
QLabel{color:#ddd2f5;font-size:12px;}
QLabel#hint{color:#9a8fbe;font-size:11px;}
QLabel#voiceWarn{color:#ffb0b0;font-size:11px;background:#4a2030;border:1px solid #a04a5a;
    border-radius:6px;padding:6px 8px;}
QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox{
    background:#1b1630;color:#f0eaff;border:1px solid #4b3f68;border-radius:6px;padding:4px 6px;}
QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus,QComboBox:focus{border-color:#8a6fc4;}
/* 下拉箭头与弹出列表：不单独指定的话，选中项会用系统浅色高亮 + 白字，等于看不见 */
QComboBox::drop-down{border:none;width:22px;}
QComboBox QAbstractItemView{background:#1b1630;color:#f0eaff;border:1px solid #4b3f68;outline:0;
    selection-background-color:#6b568f;selection-color:#ffffff;}
QComboBox QAbstractItemView::item{min-height:24px;padding:4px 8px;color:#f0eaff;}
QComboBox QAbstractItemView::item:selected{background:#6b568f;color:#ffffff;}
QComboBox QAbstractItemView::item:hover{background:#3a3055;color:#ffffff;}
/* 滚动区（语音页用）：不声明透明背景的话，内层 QWidget 会用系统调色板刷出一块浅灰底，
   和别的页签的深紫底对不上；三条规则缺一不可（滚动区本体 / viewport 内容 / 命名页）。 */
QScrollArea{background:transparent;border:none;}
QScrollArea > QWidget > QWidget{background:transparent;}
QWidget#voicePage{background:transparent;}
/* 滚动条：默认是系统浅色细条，在深色面板上很突兀 */
QScrollBar:vertical{background:#1b1630;width:10px;margin:0;border-radius:5px;}
QScrollBar::handle:vertical{background:#4b3f68;border-radius:5px;min-height:24px;}
QScrollBar::handle:vertical:hover{background:#6b568f;}
QScrollBar::add-line,QScrollBar::sub-line{height:0;width:0;}
QScrollBar::add-page,QScrollBar::sub-page{background:transparent;}
/* 音量滑杆：同理，不主题化就是系统浅灰槽 + 白手柄 */
QSlider::groove:horizontal{height:4px;background:#1b1630;border-radius:2px;}
QSlider::sub-page:horizontal{background:#8a6fc4;border-radius:2px;}
QSlider::handle:horizontal{background:#e0d3ff;width:12px;margin:-5px 0;border-radius:6px;}
QCheckBox{color:#ddd2f5;font-size:12px;}
/* 折叠区块的标题按钮：看起来像一行可点的小标题，不要做成大按钮 */
QToolButton#sectionHeader{color:#d9caff;background:transparent;border:none;
    padding:4px 2px;font-size:12px;font-weight:600;text-align:left;}
QToolButton#sectionHeader:hover{color:#f0eaff;}
QPushButton{background:#4b3f68;color:#f0eaff;border:none;border-radius:6px;padding:6px 14px;font-size:12px;}
QPushButton:hover{background:#6b568f;}
QPushButton#primary{background:#8a6fc4;font-weight:600;}
QPushButton#primary:hover{background:#9f84d8;}
QTabWidget::pane{border:1px solid #4b3f68;border-radius:8px;top:-1px;}
QTabBar::tab{background:#2c2443;color:#c9bde6;padding:6px 16px;border-top-left-radius:8px;border-top-right-radius:8px;}
QTabBar::tab:selected{background:#4b3f68;color:#fff;}
QTableWidget{background:#1b1630;color:#eee6ff;border:1px solid #4b3f68;border-radius:6px;
    gridline-color:#332a4d;selection-background-color:#6b568f;}
QHeaderView::section{background:#2c2443;color:#c9bde6;border:none;padding:5px;}
QTableCornerButton::section{background:#2c2443;border:none;}
"""


class SettingsDialog(QDialog):
    """设置窗口。保存时直接把值写回 config 并 save()。"""

    saved = Signal()
    memory_open_requested = Signal()
    rebuild_requested = Signal()
    screen_test_requested = Signal()      # 点「测试一下截图」
    proactive_test_requested = Signal()   # 点「让她现在说一句」
    voice_test_requested = Signal()       # 点「用日语说一句」
    signature_requested = Signal()        # 点「让她说一句「心情大好～」」（播本地录音，不算测试）

    def __init__(self, cfg, library: SpriteLibrary, roles=None,
                 roles_source: str = "", voice=None, call=None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.library = library
        self.roles = list(roles or [])          # 角色卡：设置里「我是谁」的下拉项
        self.roles_source = roles_source
        self.voice = voice                      # VoiceService（可能为 None：单独打开面板时）
        self.call = call                        # CallSession（用来判断"通话中别去试开设备"）
        self._mic_recorder = None               # 「测试麦克风」用的临时录音器
        self._mic_peak = 0.0
        self.setWindowTitle("露娜桌宠 · 设置")
        self.setStyleSheet(_DIALOG_QSS)
        self.resize(560, 560)
        self._build()

    # ---- 搭建 -------------------------------------------------------------
    def _build(self) -> None:
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        tabs.addTab(self._build_chat_tab(), "对话")
        tabs.addTab(self._build_vision_tab(), "看屏幕")
        tabs.addTab(self._build_memory_tab(), "记忆")
        tabs.addTab(self._build_sprite_tab(), "立绘")
        tabs.addTab(self._build_voice_tab(), "语音")
        tabs.addTab(self._build_behavior_tab(), "其它")

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.button(QDialogButtonBox.Save).setObjectName("primary")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_chat_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        # 「我是谁」：决定她用什么态度、什么称呼面对你
        self.cb_role = QComboBox()
        if self.roles:
            for role in self.roles:
                self.cb_role.addItem(role.label, role.id)
            self._select_data(self.cb_role, self.cfg.get("persona.user_role", "asahi"))
        else:
            self.cb_role.addItem("（没找到角色卡，按默认处理）", "")
            self.cb_role.setEnabled(False)
        form.addRow("我是谁", self.cb_role)
        tip = QLabel("换个身份她就会换一套态度和称呼；人设来自角色卡："
                     + (self.roles_source or "内置角色"))
        tip.setObjectName("hint")
        tip.setWordWrap(True)
        form.addRow(tip)

        # ---- 对话模型：先选服务商，地址和模型名自动带出来 ----
        self.cb_provider = QComboBox()
        for provider in providers.PROVIDERS:
            self.cb_provider.addItem(provider.label, provider.id)
        current_provider = self.cfg.get("api.provider", "") or providers.guess_provider(
            self.cfg.get("api.base_url", ""))
        self._select_data(self.cb_provider, current_provider)

        self.ed_key = QLineEdit(self.cfg.api_key)
        self.ed_key.setEchoMode(QLineEdit.Password)
        self.ed_key.setPlaceholderText("sk-…（留空则用内置台词，不联网）")
        form.addRow("服务商", self.cb_provider)
        form.addRow("API Key", self.ed_key)

        self.ed_base = QLineEdit(self.cfg.get("api.base_url", ""))
        form.addRow("接口地址", self.ed_base)

        # 模型名用「可编辑下拉」：常见模型点一下就有，想用别的直接敲
        self.ed_model = QComboBox()
        self.ed_model.setEditable(True)
        for name in providers.models_of(current_provider):
            self.ed_model.addItem(name)
        want_model = str(self.cfg.get("api.model", "") or "").strip()
        if want_model:
            if self.ed_model.findText(want_model) < 0:
                self.ed_model.addItem(want_model)
            self.ed_model.setCurrentText(want_model)
        form.addRow("模型名", self.ed_model)

        # 切服务商：填好地址、换一批模型候选、把该家之前填过的 Key 找回来
        self.cb_provider.currentIndexChanged.connect(self._on_provider_changed)
        tip_key = QLabel("每家 Key 分开记：切回某一家时，之前填过的 Key 会自动填回来。")
        tip_key.setObjectName("hint")
        tip_key.setWordWrap(True)
        form.addRow(tip_key)

        self.sp_temp = QDoubleSpinBox()
        self.sp_temp.setRange(0.0, 2.0)
        self.sp_temp.setSingleStep(0.1)
        self.sp_temp.setValue(float(self.cfg.get("api.temperature", 1.1)))
        form.addRow("发挥空间（temperature）", self.sp_temp)

        self.sp_chars = QSpinBox()
        self.sp_chars.setRange(20, 300)
        self.sp_chars.setValue(int(self.cfg.get("api.max_chars", 60)))
        form.addRow("单次回复字数上限", self.sp_chars)

        hint = QLabel("留空 Key 时她会念内置台词、不联网也不花钱；填上就是真对话。"
                      "换模型只需改这两行，记忆、翻译、语音都跟着走同一个接口。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        return page

    def _on_provider_changed(self) -> None:
        """换服务商：带出预设地址与模型候选，并把该家存过的 Key 填回来。"""
        pid = self.cb_provider.currentData() or "custom"
        preset = providers.get(pid)
        if preset.base_url:
            self.ed_base.setText(preset.base_url)

        models = providers.models_of(pid)
        current = self.ed_model.currentText().strip()
        self.ed_model.blockSignals(True)
        self.ed_model.clear()
        for name in models:
            self.ed_model.addItem(name)
        if current and self.ed_model.findText(current) < 0:
            self.ed_model.addItem(current)
        self.ed_model.setCurrentText(models[0] if models else current)
        self.ed_model.blockSignals(False)

        keys = self.cfg.get("api.keys") or {}
        saved = ""
        if isinstance(keys, dict):
            saved = str(keys.get(pid, "") or "")
        self.ed_key.setText(saved)

    def _build_vision_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.ck_vision = QCheckBox("启用看图（需要另配一个多模态模型）")
        self.ck_vision.setChecked(bool(self.cfg.get("vision.enabled", False)))
        form.addRow(self.ck_vision)

        self.ed_vbase = QLineEdit(self.cfg.get("vision.base_url", ""))
        self.ed_vbase.setPlaceholderText("例如 https://api.siliconflow.cn/v1")
        form.addRow("视觉接口地址", self.ed_vbase)

        self.ed_vmodel = QLineEdit(self.cfg.get("vision.model", ""))
        self.ed_vmodel.setPlaceholderText("例如 Qwen/Qwen2.5-VL-7B-Instruct")
        form.addRow("视觉模型名", self.ed_vmodel)

        self.ed_vkey = QLineEdit(self.cfg.get("vision.api_key", ""))
        self.ed_vkey.setEchoMode(QLineEdit.Password)
        form.addRow("视觉接口 Key", self.ed_vkey)

        hint = QLabel("不启用也能用：露娜只会根据「你正在用什么窗口」来搭话，"
                      "不会截图、不会上传任何画面。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)

        self.ck_auto_screen = QCheckBox("允许她隔一段时间自己看一眼")
        self.ck_auto_screen.setChecked(bool(self.cfg.get("screen.auto", False)))
        form.addRow(self.ck_auto_screen)

        self.sp_screen_interval = QSpinBox()
        self.sp_screen_interval.setRange(1, 120)
        self.sp_screen_interval.setValue(int(self.cfg.get("screen.interval_min", 10)))
        self.sp_screen_interval.setSuffix(" 分钟")
        form.addRow("自动看的间隔", self.sp_screen_interval)

        # 主动搭话时要不要先看一眼屏幕：默认不勾，隐私优先
        self.ck_idle_screen = QCheckBox("她主动开口前，先偷看一眼屏幕")
        self.ck_idle_screen.setChecked(bool(self.cfg.get("behavior.idle_use_screen", False)))
        form.addRow(self.ck_idle_screen)

        row = QHBoxLayout()
        btn_test = QPushButton("测试一下截图")
        btn_test.clicked.connect(self.screen_test_requested.emit)
        row.addWidget(btn_test)
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(holder)

        hint2 = QLabel("「测试一下截图」只会截一张你自己看得见的画面，用来确认权限没问题；"
                       "密码、银行、支付类窗口任何时候都会跳过。")
        hint2.setObjectName("hint")
        hint2.setWordWrap(True)
        form.addRow(hint2)
        return page

    def _build_memory_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.ed_embed_key = QLineEdit(self.cfg.get("memory.embedding.api_key", ""))
        self.ed_embed_key.setEchoMode(QLineEdit.Password)
        self.ed_embed_key.setPlaceholderText("硅基流动的 Key（bge-m3）")
        form.addRow("向量服务 Key", self.ed_embed_key)

        self.ed_embed_model = QLineEdit(self.cfg.get("memory.embedding.model", "BAAI/bge-m3"))
        form.addRow("向量模型", self.ed_embed_model)

        self.ck_upload = QCheckBox("同意把记忆文本发送到向量服务（会消耗少量额度）")
        self.ck_upload.setChecked(bool(self.cfg.get("memory.allow_upload", True)))
        form.addRow(self.ck_upload)

        self.sp_topk = QSpinBox()
        self.sp_topk.setRange(0, 20)
        self.sp_topk.setValue(int(self.cfg.get("memory.top_k", 6)))
        form.addRow("每次注入几条记忆", self.sp_topk)

        self.sp_extract = QSpinBox()
        self.sp_extract.setRange(0, 50)
        self.sp_extract.setValue(int(self.cfg.get("memory.auto_extract_every", 8)))
        form.addRow("每几轮自动提炼记忆", self.sp_extract)

        row = QHBoxLayout()
        btn_view = QPushButton("查看 / 编辑记忆…")
        btn_view.clicked.connect(self.memory_open_requested.emit)
        btn_rebuild = QPushButton("重建向量索引")
        btn_rebuild.clicked.connect(self.rebuild_requested.emit)
        row.addWidget(btn_view)
        row.addWidget(btn_rebuild)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow(holder)

        hint = QLabel("Key 留空时会退化成「本地哈希向量」：完全离线、不花钱，"
                      "但语义检索会弱一些。记忆本体始终以明文存在 data/memory.json。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        return page

    def _build_sprite_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        row = QHBoxLayout()
        self.ed_dir = QLineEdit(str(self.library.sprite_dir or ""))
        btn_browse = QPushButton("选择…")
        btn_browse.clicked.connect(self._pick_dir)
        row.addWidget(self.ed_dir, 1)
        row.addWidget(btn_browse)
        holder = QWidget()
        holder.setLayout(row)
        form.addRow("立绘目录", holder)

        self.cb_family = QComboBox()
        for family in self.library.families:
            self.cb_family.addItem(FAMILY_NAMES.get(family, family), family)
        self._select_data(self.cb_family, self.cfg.get("sprite.family", "s"))
        form.addRow("尺寸", self.cb_family)

        self.cb_group = QComboBox()
        for group in self.library.groups:
            self.cb_group.addItem(self.library.outfit_label(group), group)
        self._select_data(self.cb_group, self.cfg.get("sprite.group", "0_0"))
        form.addRow("造型", self.cb_group)

        self.sp_scale = QDoubleSpinBox()
        self.sp_scale.setRange(0.3, 2.0)
        self.sp_scale.setSingleStep(0.05)
        self.sp_scale.setDecimals(2)
        self.sp_scale.setValue(float(self.cfg.get("sprite.scale", 1.0)))
        form.addRow("缩放比例", self.sp_scale)

        self.ck_auto_pose = QCheckBox("说话时按情绪顺手换个动作（衣服不变）")
        self.ck_auto_pose.setChecked(bool(self.cfg.get("sprite.auto_pose", True)))
        form.addRow(self.ck_auto_pose)
        hint_pose = QLabel("「动作」指抬手／抱臂这类姿势：得意、生气、无语、思考时会抱臂。"
                           "哪个情绪配哪个动作，写在 assets/expressions.json 的 emotion_pose 里。")
        hint_pose.setObjectName("hint")
        hint_pose.setWordWrap(True)
        form.addRow(hint_pose)

        hint = QLabel("半身（s）最适合当桌宠；头部特写（f）适合放在屏幕角落；"
                      "近景（l）细节最多但很大。也可以直接在立绘上滚轮缩放。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)

        # ---- 按时间 / 季节自动换装 ----------------------------------------
        self.ck_outfit_auto = QCheckBox("按时间和季节自己换衣服（深夜穿家居服，冬天不穿夏装）")
        self.ck_outfit_auto.setChecked(bool(self.cfg.get("behavior.outfit_auto", True)))
        form.addRow(self.ck_outfit_auto)

        self.cb_night_outfit = QComboBox()
        for outfit in self.library.outfits:
            self.cb_night_outfit.addItem(self._outfit_label(outfit), outfit)
        self._select_data(self.cb_night_outfit, str(self.cfg.get("behavior.outfit_night_outfit", "5")))
        form.addRow("夜里穿哪套", self.cb_night_outfit)

        row_night = QHBoxLayout()
        self.sp_night_from = QSpinBox()
        self.sp_night_from.setRange(0, 23)
        self.sp_night_from.setSuffix(" 点")
        self.sp_night_from.setValue(int(self.cfg.get("behavior.outfit_night_from", 22)))
        row_night.addWidget(self.sp_night_from)
        row_night.addWidget(QLabel("到"))
        self.sp_night_to = QSpinBox()
        self.sp_night_to.setRange(0, 23)
        self.sp_night_to.setSuffix(" 点")
        self.sp_night_to.setValue(int(self.cfg.get("behavior.outfit_night_to", 6)))
        row_night.addWidget(self.sp_night_to)
        row_night.addStretch(1)
        holder_night = QWidget()
        holder_night.setLayout(row_night)
        form.addRow("夜间时段", holder_night)

        self.sp_outfit_gap = QSpinBox()
        self.sp_outfit_gap.setRange(0, 240)
        self.sp_outfit_gap.setSuffix(" 分钟")
        self.sp_outfit_gap.setValue(int(self.cfg.get("behavior.outfit_min_gap_min", 30)))
        form.addRow("两次换装最短间隔", self.sp_outfit_gap)

        self.ck_outfit_random = QCheckBox("当季有好几套时随机挑一套")
        self.ck_outfit_random.setChecked(bool(self.cfg.get("behavior.outfit_random_in_season", True)))
        form.addRow(self.ck_outfit_random)

        self.cb_season = QComboBox()
        self.cb_season.addItem("自动（按月份）", "")
        self.cb_season.addItem("春（3–5 月）", "spring")
        self.cb_season.addItem("夏（6–8 月）", "summer")
        self.cb_season.addItem("秋（9–11 月）", "autumn")
        self.cb_season.addItem("冬（12–2 月）", "winter")
        self._select_data(self.cb_season, str(self.cfg.get("behavior.outfit_season", "") or ""))
        form.addRow("按哪个季节挑", self.cb_season)

        hint_outfit = QLabel("哪套算睡衣、哪套冬天穿，写在 assets/wardrobe.json 里；"
                             "你手动换的衣服两小时内不会被自动改掉。")
        hint_outfit.setObjectName("hint")
        hint_outfit.setWordWrap(True)
        form.addRow(hint_outfit)
        return page

    def _chips_text(self) -> str:
        """把配置里的接话选项拼成一行文本（配置可能是列表，也可能是字符串）。"""
        raw = self.cfg.get("behavior.reply_chips", []) or []
        if isinstance(raw, str):
            return raw
        return "/".join(str(item).strip() for item in raw if str(item).strip())

    def _outfit_label(self, outfit: str) -> str:
        """造型下拉的显示名：优先用 wardrobe.json 里的中文名，没有就用素材标签。"""
        try:
            from ..wardrobe import load_wardrobe

            table = load_wardrobe()[0].get("outfits", {})
            name = table.get(str(outfit), {}).get("name")
            if name:
                return f"{outfit} · {name}"
        except Exception:  # noqa: BLE001 名字取不到不影响选择
            pass
        return self.library.outfit_name(outfit)

    def _build_behavior_tab(self) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        form.setLabelAlignment(Qt.AlignRight)

        self.ck_blink = QCheckBox("会眨眼")
        self.ck_blink.setChecked(bool(self.cfg.get("behavior.blink", True)))
        form.addRow(self.ck_blink)

        self.ck_breathe = QCheckBox("会轻轻呼吸")
        self.ck_breathe.setChecked(bool(self.cfg.get("behavior.breathe", True)))
        form.addRow(self.ck_breathe)

        self.ck_always_top = QCheckBox("始终显示在其它窗口上面")
        self.ck_always_top.setChecked(bool(self.cfg.get("sprite.always_on_top", True)))
        form.addRow(self.ck_always_top)

        self.sp_idle = QSpinBox()
        self.sp_idle.setRange(0, 120)
        self.sp_idle.setValue(int(self.cfg.get("behavior.idle_lines_min", 8)))
        self.sp_idle.setSuffix(" 分钟")
        form.addRow("每隔多久主动说一句（0=不说）", self.sp_idle)

        self.sp_gap = QSpinBox()
        self.sp_gap.setRange(0, 60)
        self.sp_gap.setValue(int(self.cfg.get("behavior.idle_min_gap_min", 3)))
        self.sp_gap.setSuffix(" 分钟")
        form.addRow("你说话后多久内不打扰", self.sp_gap)

        self.sp_bubble = QSpinBox()
        self.sp_bubble.setRange(2, 60)
        self.sp_bubble.setValue(int(self.cfg.get("behavior.bubble_seconds", 9)))
        self.sp_bubble.setSuffix(" 秒")
        form.addRow("桌面气泡停留多久", self.sp_bubble)

        self.sp_emotion_hold = QSpinBox()
        self.sp_emotion_hold.setRange(0, 300)
        self.sp_emotion_hold.setValue(int(self.cfg.get("behavior.emotion_hold_seconds", 8)))
        self.sp_emotion_hold.setSuffix(" 秒")
        form.addRow("表情/动作保持多久后恢复（0=不恢复）", self.sp_emotion_hold)

        self.ck_restore = QCheckBox("说完一句后自动恢复默认姿势与表情")
        self.ck_restore.setChecked(bool(self.cfg.get("behavior.restore_after_reply", True)))
        form.addRow(self.ck_restore)

        # ---- 主动搭话时的"接一句"按钮 --------------------------------------
        self.ck_chips = QCheckBox("她主动说话时，在气泡下给我几个接话选项")
        self.ck_chips.setChecked(bool(self.cfg.get("behavior.quick_replies", True)))
        form.addRow(self.ck_chips)

        self.ed_chips = QLineEdit(self._chips_text())
        self.ed_chips.setPlaceholderText("用 / 分隔，最多四条，例如：嗯？/我在呢/怎么了？")
        form.addRow("接话选项", self.ed_chips)

        self.sp_chips_seconds = QSpinBox()
        self.sp_chips_seconds.setRange(3, 60)
        self.sp_chips_seconds.setValue(int(self.cfg.get("behavior.chips_seconds", 12)))
        self.sp_chips_seconds.setSuffix(" 秒")
        form.addRow("选项停留多久", self.sp_chips_seconds)

        hint_chips = QLabel("点其中一个 = 当成你说的一句话发出去（会进聊天记录、也会被记住）；"
                            "点「自己写…」会打开脚下的输入框。")
        hint_chips.setObjectName("hint")
        hint_chips.setWordWrap(True)
        form.addRow(hint_chips)

        btn_say = QPushButton("让她现在说一句")
        btn_say.clicked.connect(self.proactive_test_requested.emit)
        hint_say = QLabel("不用等计时器，点一下立刻试一次（用来确认主动搭话确实通）")
        hint_say.setObjectName("hint")
        hint_say.setWordWrap(True)
        form.addRow(btn_say)
        form.addRow(hint_say)

        # 「心情大好～」彩蛋：放本地录音 + 换得意抱臂，和上面的测试按钮互不影响
        btn_signature = QPushButton("让她说一句「心情大好～」")
        btn_signature.setObjectName("btn_signature")
        btn_signature.clicked.connect(self.signature_requested.emit)
        hint_signature = QLabel(
            "放这段日语录音（大変に気分がいい～），同时换成抱臂得意的样子；"
            "不走语音服务，也不用填 Key"
        )
        hint_signature.setObjectName("hint")
        hint_signature.setWordWrap(True)
        form.addRow(btn_signature)
        form.addRow(hint_signature)

        # 彩蛋音频：素材包解压后会自动找到；也可以在这里指定自己的 wav
        row_sig = QHBoxLayout()
        self.ed_signature_file = QLineEdit(str(self.cfg.get("voice.signature_file", "") or ""))
        self.ed_signature_file.setPlaceholderText("留空 = 自动找 assets\\voice\\taihen_ni_kibun_ga_ii.wav")
        btn_sig_pick = QPushButton("选择…")
        btn_sig_pick.clicked.connect(
            lambda: self._pick_into(self.ed_signature_file, "选择「心情大好～」的 wav", "音频 (*.wav)")
        )
        row_sig.addWidget(self.ed_signature_file, 1)
        row_sig.addWidget(btn_sig_pick)
        holder_sig = QWidget()
        holder_sig.setLayout(row_sig)
        form.addRow("彩蛋音频", holder_sig)

        self.lbl_signature_state = QLabel("")
        self.lbl_signature_state.setObjectName("hint")
        self.lbl_signature_state.setWordWrap(True)
        form.addRow(self.lbl_signature_state)
        # 改了路径就立刻重新判断（这个 stat 很便宜，不用去抖）
        self.ed_signature_file.textChanged.connect(lambda _t: self._refresh_signature_state())
        self._refresh_signature_state()

        self.ck_debug = QCheckBox("保存调试文件（截图、原始回复）")
        self.ck_debug.setChecked(bool(self.cfg.get("behavior.debug_save", False)))
        form.addRow(self.ck_debug)
        return page

    # ---- 语音 -------------------------------------------------------------
    def _build_voice_tab(self) -> QWidget:
        """
        语音页：开关 + 服务参数 + 「哪种情绪用哪套参考音频」。

        内容比一屏高，所以塞进 QScrollArea；底部"试听 / 状态"固定在下面不滚走。
        """
        page = QWidget()
        outer = QVBoxLayout(page)
        # 和其它页签保持同一份留白（滚动条出现时内容不会整体右移几像素）
        outer.setContentsMargins(11, 11, 11, 11)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        # 这一页的内容比一屏高，所以套一层滚动区。
        # 注意别再给 inner 单独写 QLabel 颜色样式：子控件的样式表会盖掉 _DIALOG_QSS，
        # 结果这一页的标签色、hint 色都和别的页签对不上。底色统一交给 _DIALOG_QSS 的
        # QScrollArea / QWidget#voicePage 两条透明规则。
        inner.setObjectName("voicePage")
        inner.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setLabelAlignment(Qt.AlignRight)
        # 页面内部再分四块：① 常用（常显）② 音色 ③ 服务与模型 ④ 通话（后三块可折叠）
        root = QVBoxLayout(inner)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.addLayout(form)
        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)

        # ---- 开关 ----
        self.ck_voice = QCheckBox("用日语念出来（文字仍然是中文）")
        self.ck_voice.setChecked(bool(self.cfg.get("voice.enabled", False)))
        form.addRow(self.ck_voice)

        self.ck_voice_auto = QCheckBox("需要时自动拉起语音服务")
        self.ck_voice_auto.setChecked(bool(self.cfg.get("voice.autostart", True)))
        form.addRow(self.ck_voice_auto)

        # ===== ③ 服务与模型（折叠）：装环境时才会动 =====
        self.sec_service = CollapsibleSection("服务与模型（高级）", expanded=False)
        svc = QFormLayout()
        svc.setContentsMargins(0, 0, 0, 0)
        svc.setLabelAlignment(Qt.AlignRight)

        self.sp_voice_port = QSpinBox()
        self.sp_voice_port.setRange(1024, 65535)
        self.sp_voice_port.setValue(int(self.cfg.get("voice.port", 9888) or 9888))
        svc.addRow("服务端口", self.sp_voice_port)

        row_root = QHBoxLayout()
        self.ed_voice_root = QLineEdit(str(self.cfg.get("voice.root", "") or ""))
        self.ed_voice_root.setPlaceholderText(
            r"例如 D:\GPT-SoVITS-v2pro（这一层里能看到 api_v2.py 和 runtime 文件夹）"
        )
        btn_root = QPushButton("选择…")
        btn_root.clicked.connect(lambda: self._pick_into(self.ed_voice_root, "选择 GPT-SoVITS 根目录"))
        btn_find = QPushButton("自动查找")
        btn_find.setToolTip("在 下载 / 桌面 / 用户目录 里浅扫一遍，找含 api_v2.py 的文件夹")
        btn_find.clicked.connect(self._auto_find_voice_root)
        row_root.addWidget(self.ed_voice_root, 1)
        row_root.addWidget(btn_root)
        row_root.addWidget(btn_find)
        holder_root = QWidget()
        holder_root.setLayout(row_root)
        svc.addRow("GPT-SoVITS 根目录", holder_root)

        # 实时说明这一层对不对（用户最容易选深一层，这里直接点出来）
        self.lbl_voice_root_state = QLabel("")
        self.lbl_voice_root_state.setObjectName("hint")
        self.lbl_voice_root_state.setWordWrap(True)
        svc.addRow(self.lbl_voice_root_state)
        self._voice_root_timer = QTimer(self)
        self._voice_root_timer.setSingleShot(True)
        self._voice_root_timer.setInterval(300)      # 去抖：别每敲一个字就 stat 一次
        self._voice_root_timer.timeout.connect(self._refresh_voice_root_state)
        self.ed_voice_root.textChanged.connect(lambda _t: self._voice_root_timer.start())
        self._refresh_voice_root_state()

        self.ed_voice_python = QLineEdit(str(self.cfg.get("voice.python", "") or ""))
        self.ed_voice_python.setPlaceholderText(r"留空 = 用根目录里的 runtime\python.exe")
        svc.addRow("Python（可选）", self.ed_voice_python)

        self.ed_gpt = QLineEdit(str(self.cfg.get("voice.gpt_weights", "") or ""))
        self.ed_gpt.setPlaceholderText(
            r"相对根目录，如 GPT_weights_v2Pro\xxx.ckpt；留空 = 用 api_v2.py 里的默认"
        )
        svc.addRow("GPT 权重", self.ed_gpt)

        self.ed_sovits = QLineEdit(str(self.cfg.get("voice.sovits_weights", "") or ""))
        self.ed_sovits.setPlaceholderText(
            r"相对根目录，如 SoVITS_weights_v2Pro\xxx.pth；留空 = 用默认"
        )
        svc.addRow("SoVITS 权重", self.ed_sovits)

        hint_weights = QLabel(
            "权重文件由模型作者提供，本项目不附带；两者的路径都相对上面的根目录写，"
            "写错只会影响「用日语念出来」，不影响聊天。"
        )
        hint_weights.setObjectName("hint")
        hint_weights.setWordWrap(True)
        svc.addRow(hint_weights)

        row_map = QHBoxLayout()
        self.ed_voice_map = QLineEdit(str(self.cfg.get("voice.voice_map_file", "") or ""))
        self.ed_voice_map.setPlaceholderText("留空 = assets\\voice_emotions.json")
        btn_map = QPushButton("选择…")
        btn_map.clicked.connect(lambda: self._pick_into(self.ed_voice_map, "选择音色表 JSON"))
        row_map.addWidget(self.ed_voice_map, 1)
        row_map.addWidget(btn_map)
        holder_map = QWidget()
        holder_map.setLayout(row_map)
        svc.addRow("音色表", holder_map)

        self.ed_translate_model = QLineEdit(str(self.cfg.get("voice.translate_model", "") or ""))
        self.ed_translate_model.setPlaceholderText("留空 = 和对话用同一个模型")
        svc.addRow("翻译用的模型", self.ed_translate_model)
        self.sec_service.set_content_layout(svc)
        root.addWidget(self.sec_service)

        # ===== ② 音色（折叠）：哪档情绪配哪段参考录音 =====
        self.sec_tone = CollapsibleSection("音色：哪种情绪用哪套参考音频", expanded=False)
        tone = QFormLayout()
        tone.setContentsMargins(0, 0, 0, 0)
        tone.setLabelAlignment(Qt.AlignRight)

        # 音色下拉的选项来自音色表里的 refs；读不到就全禁用并提示
        self.voice_map = getattr(self.voice, "voice_map", None)
        names = self.voice_map.names() if self.voice_map is not None else []
        self._voice_names = names
        enabled = bool(names)

        self.cb_voice_default = QComboBox()
        for name in names:
            self.cb_voice_default.addItem(self.voice_map.label(name), name)
        self._select_data(self.cb_voice_default, self.cfg.get("voice.default_voice", "normal1"))
        self.cb_voice_default.setEnabled(enabled)
        tone.addRow("默认音色", self.cb_voice_default)

        # 12 档情绪排成 3 列 × 4 行：比竖着排 12 行省一半高度，扫一眼就能比
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self._emotion_combos: dict[str, QComboBox] = {}
        for index, emotion in enumerate(EMOTIONS):
            combo = QComboBox()
            for name in names:
                combo.addItem(self.voice_map.label(name), name)
            want = ""
            if self.voice_map is not None:
                want = self.voice_map.emotion_map.get(emotion, "") or ""
            if want:
                self._select_data(combo, want)
            combo.setEnabled(enabled)
            self._emotion_combos[emotion] = combo

            cell = QHBoxLayout()
            cell.setContentsMargins(0, 0, 0, 0)
            cell.setSpacing(4)
            name_label = QLabel(emotion)
            name_label.setMinimumWidth(28)
            cell.addWidget(name_label)
            cell.addWidget(combo, 1)
            holder = QWidget()
            holder.setLayout(cell)
            row, col = divmod(index, 3)
            grid.addWidget(holder, row, col)

        grid_holder = QWidget()
        grid_holder.setLayout(grid)
        tone.addRow("情绪 → 音色", grid_holder)

        hint_map = QLabel(
            "参考音频决定「音色」：同一句日语，用柔和那段录音它就用柔和语气。"
            "这里改的是 assets\\voice_emotions.json 里的 emotion_map，改完立刻生效。"
        )
        hint_map.setObjectName("hint")
        hint_map.setWordWrap(True)
        tone.addRow(hint_map)

        if not enabled:
            warn = QLabel("⚠ 还没读到可用的参考音频。先在上面填对 GPT-SoVITS 目录与权重，"
                          "并确认音色表里的 refs 指向存在的 wav。")
            warn.setObjectName("hint")
            warn.setWordWrap(True)
            tone.addRow(warn)
        elif getattr(self.voice_map, "missing", None):
            warn = QLabel("⚠ 这些参考音频不在了：" + "、".join(self.voice_map.missing))
            warn.setObjectName("hint")
            warn.setWordWrap(True)
            tone.addRow(warn)
        self.sec_tone.set_content_layout(tone)
        root.addWidget(self.sec_tone)
        # 视觉顺序：常用 → 音色 → 服务与模型 → 通话。
        # 服务块比音色先创建（它上面的控件要先建出来才能填），这里把它挪到音色后面。
        root.removeWidget(self.sec_service)
        root.insertWidget(root.indexOf(self.sec_tone) + 1, self.sec_service)

        # ---- 音质 / 长度（常用区：音量和字数挤在同一行，省一行高度）----
        row_vol = QHBoxLayout()
        self.sl_voice_volume = QSlider(Qt.Horizontal)
        self.sl_voice_volume.setRange(0, 100)
        self.sl_voice_volume.setValue(int(round(float(self.cfg.get("voice.volume", 1.0) or 1.0) * 100)))
        self.lbl_voice_volume = QLabel(f"{self.sl_voice_volume.value()}%")
        self.sl_voice_volume.valueChanged.connect(
            lambda v: self.lbl_voice_volume.setText(f"{v}%"))
        row_vol.addWidget(self.sl_voice_volume, 1)
        row_vol.addWidget(self.lbl_voice_volume)
        row_vol.addSpacing(14)

        self.sp_voice_chars = QSpinBox()
        self.sp_voice_chars.setRange(10, 120)
        self.sp_voice_chars.setValue(int(self.cfg.get("voice.max_chars", 30) or 30))
        self.sp_voice_chars.setSuffix(" 字以内")
        row_vol.addWidget(QLabel("每次最多念"))
        row_vol.addWidget(self.sp_voice_chars)
        holder_vol = QWidget()
        holder_vol.setLayout(row_vol)
        form.addRow("音量 / 长度", holder_vol)

        self.ck_voice_proactive = QCheckBox("她主动搭话时也念出来")
        self.ck_voice_proactive.setChecked(bool(self.cfg.get("voice.speak_proactive", True)))
        form.addRow(self.ck_voice_proactive)

        hint_cost = QLabel("纯 CPU 合成一句约 8–10 秒；程序会把回复切成短句边翻边合成，"
                           "所以第一声大概等 6–9 秒，后面几句几乎不用等。")
        hint_cost.setObjectName("hint")
        hint_cost.setWordWrap(True)
        form.addRow(hint_cost)

        # ===== ④ 实时语音通话（折叠，实验性）=====
        self.sec_call = CollapsibleSection("实时语音通话（实验性）", expanded=False)
        call = QFormLayout()
        call.setContentsMargins(0, 0, 0, 0)
        call.setLabelAlignment(Qt.AlignRight)
        self._build_call_section(call)
        self.sec_call.set_content_layout(call)
        root.addWidget(self.sec_call)
        root.addStretch(1)

        # ---- 底部固定区：试听 + 状态 ----
        bottom = QHBoxLayout()
        btn_test = QPushButton("用日语说一句")
        btn_test.clicked.connect(self.voice_test_requested.emit)
        bottom.addWidget(btn_test)
        self.lbl_voice_status = QLabel("语音：未启动")
        self.lbl_voice_status.setObjectName("hint")
        bottom.addWidget(self.lbl_voice_status, 1)
        outer.addLayout(bottom)
        return page

    def set_voice_status(self, text: str) -> None:
        """main.py 把语音流水线的进度写到这里（"翻译中""合成中""正在播放"…）。"""
        label = getattr(self, "lbl_voice_status", None)
        if label is not None:
            label.setText(text or "")

    # ---- GPT-SoVITS 根目录：实时体检 + 自动查找 ---------------------------
    def _refresh_voice_root_state(self) -> None:
        """把「这一层到底对不对」写在输入框下面（用户最容易选深一层）。"""
        label = getattr(self, "lbl_voice_root_state", None)
        if label is None:
            return
        _code, text = voice_probe.voice_root_state(self.ed_voice_root.text())
        label.setText(text)
        # 红的显眼一点：路径不存在/选错层时直接标红
        label.setStyleSheet("" if text.startswith("√") else "color:#ffb0b0;")

    def _auto_find_voice_root(self) -> None:
        """
        在 下载 / 桌面 / 用户目录 里浅扫一遍，找含 api_v2.py 的文件夹。

        扫得很浅（两层），几十毫秒；找到就填第一个，并告诉用户找到几个。
        """
        found = voice_probe.find_voice_roots()
        if not found:
            QMessageBox.information(
                self, "没找到 GPT-SoVITS",
                "常见位置里没找到含 api_v2.py 的文件夹。\n\n"
                "如果你还没下载：到 github.com/RVC-Boss/GPT-SoVITS 的 Releases 下整合包，"
                "解压后点「选择…」指到那层。\n"
                "（这一步只影响「用日语念出来」，不做也能正常聊天。）",
            )
            return
        self.ed_voice_root.setText(str(found[0]))
        self._refresh_voice_root_state()
        extra = "" if len(found) == 1 else f"（共找到 {len(found)} 个，已填第一个）"
        QMessageBox.information(self, "找到啦", f"已填入：\n{found[0]}\n{extra}")

    def _refresh_signature_state(self) -> None:
        """彩蛋音频在不在——素材包解压后这里会自动变成 √。"""
        label = getattr(self, "lbl_signature_state", None)
        if label is None:
            return
        # 输入框里刚敲的路径优先（还没点保存也要能看对不对）
        typed = self.ed_signature_file.text().strip() if hasattr(self, "ed_signature_file") else ""
        path = Path(typed).expanduser() if typed else signature_path(self.cfg)
        if path.is_file():
            label.setText(f"√ 已找到：{path}")
            label.setStyleSheet("")
        else:
            label.setText(
                "× 还没找到这段录音。下载素材包（luna-assets-*.zip）解压到 LunaPet 文件夹，"
                "或用上面的「选择…」指一个 wav。"
            )
            label.setStyleSheet("color:#ffb0b0;")

    # ---- 测试麦克风 -------------------------------------------------------
    def _test_mic(self) -> None:
        """
        录 3 秒，看有没有声音。

        为什么不复用通话里的那个录音器：这里要能在"没开通话"时试，
        而且必须自己收掉，不能和通话抢设备（Windows 上同一支麦克风基本是独占的）。
        """
        self._stop_mic_test()
        try:
            from ..voice.recorder import MicRecorder
        except Exception as exc:  # noqa: BLE001
            self.lbl_mic_level.setText(f"录音库没装好：{exc}")
            return
        rec = MicRecorder(self.cfg, self)
        rec.level.connect(self._on_mic_level)
        rec.failed.connect(lambda msg: self.lbl_mic_level.setText(f"打不开：{msg}"))
        self._mic_recorder = rec
        self._mic_peak = 0.0
        dev = self.cb_call_device.currentData() or None
        if not rec.start(dev):
            self.lbl_mic_level.setText("这个设备打不开，换成「自动（推荐）」再试一次。")
            self._mic_recorder = None
            return
        self.lbl_mic_level.setText("录音中…对着麦克风说句话（3 秒）")
        self._mic_timer = QTimer(self)
        self._mic_timer.setSingleShot(True)
        self._mic_timer.timeout.connect(self._finish_mic_test)
        self._mic_timer.start(3000)

    def _on_mic_level(self, rms: float) -> None:
        self._mic_peak = max(self._mic_peak, float(rms))
        bars = "▁▂▃▄▅▆▇█"
        step = int(min(len(bars), max(0, round(rms * 120))))
        self.lbl_mic_level.setText(
            f"当前音量 {rms:.4f}  {bars[:step]}{'·' * (len(bars) - step)}")

    def _finish_mic_test(self) -> None:
        self._stop_mic_test()
        peak = self._mic_peak
        if mics.desktop_access() == "deny":
            self.lbl_mic_level.setText(mics.blocked_hint())
        elif peak < 0.002:
            self.lbl_mic_level.setText(
                f"一点声音都没收到（峰值 {peak:.4f}）：可能是这支麦克风没插东西、被静音，"
                "或者选错了设备 —— 换一个再试。")
        else:
            self.lbl_mic_level.setText(f"✓ 能听到（峰值 {peak:.3f}），通话应该没问题了。")

    def _stop_mic_test(self) -> None:
        timer = getattr(self, "_mic_timer", None)
        if timer is not None:
            timer.stop()
        rec = getattr(self, "_mic_recorder", None)
        if rec is not None:
            try:
                rec.stop()
            except Exception:  # noqa: BLE001
                pass
            self._mic_recorder = None

    def done(self, result: int) -> None:  # noqa: D401 关闭面板时把麦克风放开
        self._stop_mic_test()
        super().done(result)

    def _build_call_section(self, form) -> None:
        """
        「实时语音通话」一组控件。

        为什么放在语音页签：它复用的就是这套语音设施（麦克风、识别、朗读、服务），
        单独开一页反而要重复解释环境。
        """
        head = QLabel("实时语音通话：开着就能直接对着麦克风说话，她听见后自己回答、自己继续听。")
        head.setObjectName("hint")
        head.setWordWrap(True)
        form.addRow(head)

        # 隐私开关被关掉时，红条直接顶在最上面 —— 这是"录不到声音"最常见的原因
        self.lbl_call_warn = QLabel(mics.blocked_hint())
        self.lbl_call_warn.setObjectName("voiceWarn")
        self.lbl_call_warn.setWordWrap(True)
        self.lbl_call_warn.setVisible(mics.desktop_access() == "deny")
        form.addRow(self.lbl_call_warn)

        self.ck_call = QCheckBox("打开实时语音通话（关掉 = 只打字）")
        self.ck_call.setChecked(bool(self.cfg.get("voice.call_enabled", False)))
        form.addRow(self.ck_call)

        self.cb_call_device = QComboBox()
        self.cb_call_device.addItem("自动（推荐）", "")
        try:
            # 通话中设备被自己占着，去"试着打开"会误判成打不开，所以这时不加标注
            busy = bool(getattr(self.call, "running", False))
            found = mics.list_inputs(prefer_openable=not busy)
            for dev in found:
                self.cb_call_device.addItem(dev.label, str(dev.index))
            if not found:
                self.cb_call_device.addItem("（现在列不出设备：多半被别的程序占着）", "")
        except Exception:  # noqa: BLE001 没装录音库就只留"自动"
            self.cb_call_device.addItem("（没装录音库，只能自动）", "")
        self._select_data(self.cb_call_device, str(self.cfg.get("voice.call_device", "") or ""))
        form.addRow("麦克风", self.cb_call_device)

        row = QHBoxLayout()
        btn_test_mic = QPushButton("测试麦克风")
        btn_test_mic.clicked.connect(self._test_mic)
        btn_privacy = QPushButton("打开 Windows 麦克风设置")
        btn_privacy.clicked.connect(mics.open_privacy_settings)
        row.addWidget(btn_test_mic)
        row.addWidget(btn_privacy)
        form.addRow(row)

        self.lbl_mic_level = QLabel("（点「测试麦克风」，对着麦克风说句话，看这里有没有动静）")
        self.lbl_mic_level.setObjectName("hint")
        self.lbl_mic_level.setWordWrap(True)
        form.addRow(self.lbl_mic_level)

        self.sp_call_rms = QDoubleSpinBox()
        self.sp_call_rms.setDecimals(3)
        self.sp_call_rms.setRange(0.001, 0.200)
        self.sp_call_rms.setSingleStep(0.001)
        self.sp_call_rms.setValue(float(self.cfg.get("voice.call_silence_rms", 0.008) or 0.008))
        form.addRow("多大声音算在说话", self.sp_call_rms)

        self.sp_call_silence = QSpinBox()
        self.sp_call_silence.setRange(300, 3000)
        self.sp_call_silence.setSingleStep(100)
        self.sp_call_silence.setSuffix(" 毫秒")
        self.sp_call_silence.setValue(int(self.cfg.get("voice.call_silence_ms", 900) or 900))
        form.addRow("安静多久算说完", self.sp_call_silence)

        self.sp_call_max = QSpinBox()
        self.sp_call_max.setRange(5, 60)
        self.sp_call_max.setSuffix(" 秒")
        self.sp_call_max.setValue(int(self.cfg.get("voice.call_max_utterance_s", 30) or 30))
        form.addRow("单句最长", self.sp_call_max)

        self.cb_asr_backend = QComboBox()
        self.cb_asr_backend.addItem("本地免费（faster-whisper）", "local")
        self.cb_asr_backend.addItem("云端（硅基流动，按量计费）", "cloud")
        self._select_data(self.cb_asr_backend, str(self.cfg.get("voice.asr_backend", "local")))
        form.addRow("识别方式", self.cb_asr_backend)

        self.cb_asr_model = QComboBox()
        for text, value in (("tiny（最快，不太准 ≈75 MB）", "tiny"),
                            ("base（推荐，≈145 MB）", "base"),
                            ("small（更准，≈480 MB）", "small")):
            self.cb_asr_model.addItem(text, value)
        self._select_data(self.cb_asr_model, str(self.cfg.get("voice.asr_local_model", "base")))
        form.addRow("本地模型", self.cb_asr_model)

        self.ed_asr_key = QLineEdit(str(self.cfg.get("voice.asr_key", "") or ""))
        self.ed_asr_key.setEchoMode(QLineEdit.Password)
        self.ed_asr_key.setPlaceholderText("留空 = 复用记忆里的硅基流动 Key")
        form.addRow("云端识别 Key", self.ed_asr_key)

        self.ed_asr_base = QLineEdit(str(self.cfg.get("voice.asr_base_url", "") or ""))
        form.addRow("云端识别地址", self.ed_asr_base)

        self.ed_asr_model_name = QLineEdit(str(self.cfg.get("voice.asr_model", "") or ""))
        form.addRow("云端识别模型", self.ed_asr_model_name)

        hint = QLabel("首次开通话会下载本地模型到 data/models（走 hf-mirror 镜像，一次即可）。"
                      "之后断网也能用，且不产生费用。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)

    @staticmethod
    def _pick_into(edit: QLineEdit, title: str, name_filter: str = "") -> None:
        """
        选文件/文件夹后填进输入框。

        name_filter 非空时表示"就要选文件"（例如彩蛋音频的 *.wav），
        此时先弹文件框、取消后再让用户选文件夹（兼容"选到某个目录"的用法）。
        """
        current = edit.text().strip()
        if name_filter:
            path, _ = QFileDialog.getOpenFileName(None, title, current, name_filter)
            if path:
                edit.setText(path)
                return
            path = QFileDialog.getExistingDirectory(None, title, current)
        elif current and Path(current).is_dir():
            path = QFileDialog.getExistingDirectory(None, title, current)
        else:
            path, _ = QFileDialog.getOpenFileName(None, title, current)
            if not path:
                path = QFileDialog.getExistingDirectory(None, title, current)
        if path:
            edit.setText(path)

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择立绘所在文件夹", self.ed_dir.text())
        if path:
            self.ed_dir.setText(path)

    # ---- 保存 -------------------------------------------------------------
    def _save(self) -> None:
        cfg = self.cfg
        # 先记服务商，再存 Key（Key 是按服务商分槽存的，顺序不能反）
        cfg.set("api.provider", self.cb_provider.currentData() or "custom")
        cfg.set_api_key(self.ed_key.text())
        cfg.set("api.base_url", self.ed_base.text().strip() or "https://api.deepseek.com")
        cfg.set("api.model", self.ed_model.currentText().strip() or "deepseek-chat")
        cfg.set("api.temperature", round(self.sp_temp.value(), 2))
        cfg.set("api.max_chars", self.sp_chars.value())

        cfg.set("vision.enabled", self.ck_vision.isChecked())
        cfg.set("vision.base_url", self.ed_vbase.text().strip())
        cfg.set("vision.model", self.ed_vmodel.text().strip())
        cfg.set("vision.api_key", self.ed_vkey.text().strip())
        cfg.set("screen.auto", self.ck_auto_screen.isChecked())
        cfg.set("screen.interval_min", self.sp_screen_interval.value())

        cfg.set("memory.embedding.api_key", self.ed_embed_key.text().strip())
        cfg.set("memory.embedding.model", self.ed_embed_model.text().strip() or "BAAI/bge-m3")
        cfg.set("memory.allow_upload", self.ck_upload.isChecked())
        cfg.set("memory.top_k", self.sp_topk.value())
        cfg.set("memory.auto_extract_every", self.sp_extract.value())

        cfg.set("sprite.dir", self.ed_dir.text().strip())
        cfg.set("sprite.family", self.cb_family.currentData())
        cfg.set("sprite.group", self.cb_group.currentData())
        cfg.set("sprite.scale", round(self.sp_scale.value(), 2))

        cfg.set("behavior.blink", self.ck_blink.isChecked())
        cfg.set("behavior.breathe", self.ck_breathe.isChecked())
        cfg.set("sprite.always_on_top", self.ck_always_top.isChecked())
        cfg.set("behavior.idle_lines_min", self.sp_idle.value())
        cfg.set("behavior.idle_min_gap_min", self.sp_gap.value())
        cfg.set("behavior.bubble_seconds", self.sp_bubble.value())
        cfg.set("behavior.emotion_hold_seconds", self.sp_emotion_hold.value())
        cfg.set("behavior.restore_after_reply", self.ck_restore.isChecked())
        cfg.set("sprite.auto_pose", self.ck_auto_pose.isChecked())
        cfg.set("behavior.idle_use_screen", self.ck_idle_screen.isChecked())
        cfg.set("persona.user_role", self.cb_role.currentData() or "asahi")
        cfg.set("behavior.debug_save", self.ck_debug.isChecked())

        # ---- 自动换装 ----
        cfg.set("behavior.outfit_auto", self.ck_outfit_auto.isChecked())
        cfg.set("behavior.outfit_night_outfit", self.cb_night_outfit.currentData() or "5")
        cfg.set("behavior.outfit_night_from", self.sp_night_from.value())
        cfg.set("behavior.outfit_night_to", self.sp_night_to.value())
        cfg.set("behavior.outfit_min_gap_min", self.sp_outfit_gap.value())
        cfg.set("behavior.outfit_random_in_season", self.ck_outfit_random.isChecked())
        cfg.set("behavior.outfit_season", self.cb_season.currentData() or "")

        # ---- 接一句按钮 ----
        cfg.set("behavior.quick_replies", self.ck_chips.isChecked())
        cfg.set("behavior.reply_chips",
                [part.strip() for part in self.ed_chips.text().replace("|", "/").split("/")
                 if part.strip()][:4])
        cfg.set("behavior.chips_seconds", self.sp_chips_seconds.value())

        # ---- 语音 ----
        if hasattr(self, "ck_voice"):
            cfg.set("voice.enabled", self.ck_voice.isChecked())
            cfg.set("voice.autostart", self.ck_voice_auto.isChecked())
            cfg.set("voice.port", self.sp_voice_port.value())
            cfg.set("voice.root", self.ed_voice_root.text().strip())
            cfg.set("voice.python", self.ed_voice_python.text().strip())
            cfg.set("voice.gpt_weights", self.ed_gpt.text().strip())
            cfg.set("voice.sovits_weights", self.ed_sovits.text().strip())
            cfg.set("voice.voice_map_file", self.ed_voice_map.text().strip())
            cfg.set("voice.default_voice", self.cb_voice_default.currentData() or "normal1")
            cfg.set("voice.volume", round(self.sl_voice_volume.value() / 100.0, 2))
            cfg.set("voice.max_chars", self.sp_voice_chars.value())
            cfg.set("voice.speak_proactive", self.ck_voice_proactive.isChecked())
            cfg.set("voice.translate_model", self.ed_translate_model.text().strip())
            # 彩蛋音频（留空 = 自动按 assets\voice → data\voice 的顺序找）
            cfg.set("voice.signature_file", self.ed_signature_file.text().strip())

            # 实时语音通话 + 识别后端
            cfg.set("voice.call_enabled", self.ck_call.isChecked())
            cfg.set("voice.call_device", self.cb_call_device.currentData() or "")
            cfg.set("voice.call_silence_rms", round(float(self.sp_call_rms.value()), 3))
            cfg.set("voice.call_silence_ms", self.sp_call_silence.value())
            cfg.set("voice.call_max_utterance_s", self.sp_call_max.value())
            cfg.set("voice.asr_backend", self.cb_asr_backend.currentData() or "local")
            cfg.set("voice.asr_local_model", self.cb_asr_model.currentData() or "base")
            cfg.set("voice.asr_key", self.ed_asr_key.text().strip())
            cfg.set("voice.asr_base_url", self.ed_asr_base.text().strip())
            cfg.set("voice.asr_model", self.ed_asr_model_name.text().strip())

            # 「情绪 → 音色」写回音色表（写失败只提示，不影响其它设置保存）
            mapping = {emo: combo.currentData()
                       for emo, combo in getattr(self, "_emotion_combos", {}).items()}
            if any(mapping.values()):
                try:
                    map_path = (Path(self.ed_voice_map.text().strip()) if self.ed_voice_map.text().strip()
                                else (getattr(self.voice_map, "source", None)
                                      or (self.cfg.get("voice.voice_map_file") or "")))
                    if map_path:
                        save_emotion_overrides(map_path, mapping)
                except Exception as exc:  # noqa: BLE001
                    QMessageBox.warning(self, "音色表没写成",
                                        f"「情绪 → 音色」没能保存到文件：\n{exc}\n\n"
                                        "其它设置已经保存，可以稍后重试。")

        cfg.save()
        # 保存后按新配置再体检一次，用户能立刻看到 √ / ×
        self._refresh_voice_root_state()
        self._refresh_signature_state()
        self.saved.emit()
        self.accept()


class MemoryDialog(QDialog):
    """记忆管理：看得见每一条她记住了什么，也能手动删。"""

    changed = Signal()
    rebuild_requested = Signal()

    def __init__(self, store, retriever, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.store = store
        self.retriever = retriever
        self.setWindowTitle("露娜的记忆")
        self.setStyleSheet(_DIALOG_QSS)
        self.resize(760, 520)
        self._build()
        self.reload()

    def _build(self) -> None:
        root = QVBoxLayout(self)

        top = QHBoxLayout()
        self.ed_filter = QLineEdit()
        self.ed_filter.setPlaceholderText("搜索记忆…")
        self.ed_filter.textChanged.connect(self.reload)
        top.addWidget(self.ed_filter, 1)
        btn_rebuild = QPushButton("重建索引")
        btn_rebuild.clicked.connect(self.rebuild_requested.emit)
        top.addWidget(btn_rebuild)
        root.addLayout(top)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["内容", "权重", "次数", "时间"])
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in (1, 2, 3):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        # 左侧行号列也用暗色，否则默认是刺眼的灰色方块（含下方空白区域）
        self.table.verticalHeader().setStyleSheet(
            "QHeaderView{background:#1b1630;}"
            "QHeaderView::section{background:#2c2443;color:#9d92bb;border:none;}")
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        root.addWidget(self.table, 1)

        bottom = QHBoxLayout()
        self.ed_new = QLineEdit()
        self.ed_new.setPlaceholderText("手动加一条记忆，例如：主人喜欢喝黑咖啡")
        self.ed_new.returnPressed.connect(self._add)
        bottom.addWidget(self.ed_new, 1)
        btn_add = QPushButton("添加")
        btn_add.clicked.connect(self._add)
        bottom.addWidget(btn_add)
        btn_del = QPushButton("删除选中")
        btn_del.clicked.connect(self._delete)
        bottom.addWidget(btn_del)
        root.addLayout(bottom)

        self.lbl_stats = QLabel("")
        self.lbl_stats.setObjectName("hint")
        root.addWidget(self.lbl_stats)

    # ---- 数据 -------------------------------------------------------------
    def reload(self) -> None:
        keyword = self.ed_filter.text().strip()
        facts = self.store.facts()
        if keyword:
            facts = [f for f in facts if keyword in f["text"]]
        facts.sort(key=lambda f: f.get("created_at", ""), reverse=True)

        self.table.setRowCount(len(facts))
        for row, fact in enumerate(facts):
            item = QTableWidgetItem(fact["text"])
            item.setData(Qt.UserRole, fact["id"])
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(f"{fact.get('weight', 1.0):.1f}"))
            self.table.setItem(row, 2, QTableWidgetItem(str(fact.get("hits", 0))))
            self.table.setItem(row, 3, QTableWidgetItem(fact.get("created_at", "")))

        stats = self.store.stats()
        self.lbl_stats.setText(
            f"共 {stats['facts']} 条记忆、{stats['summaries']} 条摘要；"
            f"向量索引 {self.retriever.vector_store.count()} 条。"
        )

    def _add(self) -> None:
        text = self.ed_new.text().strip()
        if not text:
            return
        self.store.add_fact(text, tags=["手动"], weight=1.5)
        self.ed_new.clear()
        self.reload()
        self.changed.emit()

    def _delete(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "提示", "先在列表里选中要删的记忆。")
            return
        if QMessageBox.question(self, "确认", f"真的要让露娜忘记这 {len(rows)} 件事吗？",
                                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        ids = [self.table.item(row, 0).data(Qt.UserRole) for row in rows]
        for fact_id in ids:
            self.store.remove_fact(fact_id)
        self.reload()
        self.changed.emit()
