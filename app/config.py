# -*- coding: utf-8 -*-
"""
配置读写（config/config.json）。

设计原则：
1. 第一次启动自动生成一份带默认值的配置，用户不用手写 JSON。
2. 改坏 JSON 不会让程序崩：自动备份成 config.json.bak.<时间戳>，再重建默认配置。
3. 界面上的设置项最终都写回这个文件，所以"改配置"和"改代码"是两件事，
   后续想调默认值，改下面 DEFAULT_CONFIG 即可。
"""
from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime
from pathlib import Path

from . import paths

# 目录解析交给 app/paths.py：源码运行 = 项目根；打包成 exe 后 = exe 同级（不可写则退到
# %LOCALAPPDATA%\LunaPet），这样双击 exe 也能正常存配置和记忆。
ROOT = paths.HOME
CONFIG_DIR = paths.CONFIG_DIR
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = paths.DATA_DIR
ASSETS_DIR = paths.ASSETS_DIR

# ---------------------------------------------------------------------------
# 默认配置。字段含义见 docs/修改指南.md，改这里等于改"出厂设置"。
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    # 对话用的接口（OpenAI 兼容格式；服务商预设见 app/providers.py）
    "api": {
        "provider": "deepseek",     # 设置里选的哪家；只用来记忆 Key 与填默认地址
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-chat",
        "api_key": "",              # 当前服务商的 Key（留空 = 离线台词模式）
        "keys": {},                 # 每个服务商各自的 Key，切来切去不用重填
        "temperature": 1.1,         # 稍微高一点，露娜的"小恶魔"劲儿更足
        "max_chars": 60,            # 单次回复字数上限（提示词里会约束）
        "timeout_connect": 20,      # 等第一个字的超时（秒）
        "timeout_total": 120,       # 整段回复的总超时（秒）
    },
    # 屏幕内容的"看图"能力（DeepSeek 官方没有视觉模型，需要另配）
    "vision": {
        "enabled": False,
        "base_url": "",             # 例如 https://api.siliconflow.cn/v1
        "model": "",                # 例如 Qwen/Qwen2.5-VL-7B-Instruct
        "api_key": "",
    },
    # 人设
    "persona": {
        "include_relations": True,  # 是否把 Part B1（关系图谱）也塞进提示词
        "extra_note": "",           # 想临时微调人设，写在这里，会追加到系统提示词末尾
        "user_role": "asahi",       # 对面是谁（角色卡里的 id）：asahi=小仓朝日 / yuusei=大藏游星
        "roles_file": "",           # 角色卡路径，留空 = 自动找 assets/roles.json 等几个位置
    },
    # 记忆：事实卡 + 硅基流动 bge-m3 向量 + ChromaDB 本地索引
    "memory": {
        "embedding": {
            "provider": "siliconflow",
            "base_url": "https://api.siliconflow.cn/v1",
            "model": "BAAI/bge-m3",
            "api_key": "",          # 硅基流动的 Key，留空则用本地 hash 向量兜底
            "batch": 16,            # 一次最多送多少条文本去算向量
            "query_timeout": 8,     # 对话时"给这一句话算向量"最多等几秒（超时就退回关键词检索）
        },
        "vector_db": {
            "backend": "auto",      # auto / chroma / sqlite
            "path": "data/chroma",
        },
        "top_k": 6,                 # 每次对话注入几条记忆
        "auto_extract_every": 8,    # 每几轮自动提炼一次新事实（0 = 关闭）
        "max_facts": 2000,          # 事实卡上限，超出会压缩成摘要
        "allow_upload": True,       # 明确同意把记忆文本送到硅基流动算向量
    },
    # 立绘显示
    "sprite": {
        "dir": "",                  # 留空 = 自动找 ..\\Sprite；找不到再到设置里手动选
        "family": "s",              # f 头部特写 / s 半身 / m 中景 / l 近景
        "group": "0_0",             # 当前服装组（a_b：a=造型，b=动作）
        "auto_pose": True,          # 说话时按情绪顺手换个动作（抱臂/抬手），但绝不换衣服
        "scale": 1.0,               # 显示缩放 0.3 ~ 2.0
        "always_on_top": True,
    },
    # 屏幕感知
    "screen": {
        "auto": False,              # 自动看屏幕（默认关，避免打扰和隐私顾虑）
        "interval_min": 10,         # 自动模式的最小间隔（分钟）
    },
    # 窗口位置记忆
    "window": {
        "pet_pos": None,            # [x, y]，None = 自动贴右下角
        "chat_pos": None,
        "chat_size": [420, 620],
    },
    # 小动作
    "behavior": {
        "blink": True,              # 自动眨眼
        "breathe": True,            # 轻微呼吸起伏
        "idle_lines_min": 8,        # 每隔多少分钟主动说一句（0 = 关闭）
        "idle_use_screen": False,   # 主动说话前先偷看一眼屏幕（默认关，隐私优先）
        "idle_min_gap_min": 3,      # 你刚说完话的几分钟内，她不主动开口
        "bubble_seconds": 9,        # 桌面气泡停留几秒
        "restore_after_reply": True,  # 说完一句后是否自动恢复成"默认姿势 + 平静"
        "emotion_hold_seconds": 8,  # 表情/动作保持几秒后恢复（0 = 一直保持到下次说话）
        "time_aware": True,         # 把"现在几点、今天几号、隔了多久"告诉她
        "debug_save": False,        # 把截图/原始回复存到 data/ 便于排查
        # ---- 按时段/季节自动换装 ----
        "outfit_auto": True,            # 她开口时按时间/季节自己换衣服
        "outfit_night_outfit": "5",     # 夜里穿哪套（5 = 粉色家居服/睡衣）
        "outfit_night_from": 22,        # 几点开始算"该穿睡衣"
        "outfit_night_to": 6,           # 几点结束（支持跨零点）
        "outfit_min_gap_min": 30,       # 两次自动换装的最短间隔（分钟，0 = 不限）
        "outfit_manual_hold_min": 120,  # 你手动换衣服后，多少分钟内不自动改
        "outfit_random_in_season": True,  # 当季有多套时随机挑一套
        "outfit_season": "",            # 强制季节 spring/summer/autumn/winter（留空 = 按月份）
        # ---- 主动发言时的"接一句"按钮 ----
        "quick_replies": True,          # 她主动说话时在气泡下方给出快捷回复
        "reply_chips": ["嗯？", "我在呢", "怎么了？"],  # 现成短句（最多用 4 条）
        "chips_seconds": 12,            # 几秒后自动收起
    },
    # 服装管家的状态（自动换装记账，不用手改）
    "wardrobe": {
        "last_switch_at": "",       # 上次自动换装的本地时间（ISO 格式）
        "last_bucket": "",          # 上次换装时的时段（上午/晚上…）
        "last_season": "",          # 上次换装时的季节
        "manual_hold_until": "",    # 手动保护期截止时间
    },
    # 日语语音（GPT-SoVITS）。默认关闭：不启动服务、不联网、不占内存
    "voice": {
        "enabled": False,           # 总开关
        "autostart": True,          # 打开程序时就把语音服务拉起来（省掉第一次的 14 秒等待）
        # GPT-SoVITS 的安装目录（里面要有 api_v2.py 和 runtime\python.exe）
        "root": "",
        "python": "",               # 留空 = <root>\runtime\python.exe
        "port": 9888,               # 语音服务端口（避开 webui 常用的 9880）
        # 露娜的音色权重（相对 root 的路径，也可以用绝对路径）
        "gpt_weights": "GPT_weights_v2Pro/[GPT_weights_v2Pro]sakurakouji_luna-e15.ckpt",
        "sovits_weights": "SoVITS_weights_v2Pro/[SoVITS_weights_v2Pro]sakurakouji_luna_e8_s992.pth",
        "voice_map_file": "",       # 留空 = assets/voice_emotions.json（情绪 → 音色对照表）
        "default_voice": "normal1", # 对照不上情绪时用哪套音色
        "volume": 1.0,              # 音量 0~1
        "max_chars": 30,            # 最多朗读前 N 个字（屏幕上的文字不截断）
        "first_chars": 12,          # 第一段最多几个字：越小第一声越快
        "segment_chars": 20,        # 之后每段最多几个字
        # 朗读范围：first=只念第一段（最快） / pipeline=分段念完（默认） / full=整句一次合成（最连贯但最慢）
        "speak_scope": "pipeline",
        "start_timeout": 45,        # 等服务就绪最多几秒（原写死 120 秒，太久了）
        "timeout_seconds": 30,      # 单段合成超时（秒）
        "probe_timeout": 0.4,       # 探测服务是否活着时的超时（秒），别卡界面
        "keep_alive": True,         # 说完不退出语音服务，关程序才退
        "speak_proactive": True,    # 主动搭话也出声
        "translate_model": "",      # 留空 = 复用 api.model 把中文翻成日语
        # 「心情大好～」那句彩蛋（设置 → 其它 里的独立按钮，不走语音服务）
        "signature_file": "",       # 留空 = assets/voice/taihen_ni_kibun_ga_ii.wav
        "signature_text": "",       # 留空 = 气泡显示「心情大好～」

        # 实时语音通话（对着麦克风说话 → 识别 → 她回答 → 继续听）
        "call_enabled": False,      # 开关（也可在右键/托盘菜单里点）
        "call_device": "",          # 麦克风编号，留空 = 系统默认
        "call_silence_rms": 0.008,  # 多大声音算"在说话"（环境吵就调大）
        "call_silence_ms": 900,     # 安静多久算一句说完（毫秒）
        "call_min_utterance_ms": 400,  # 短于这个长度的声音当噪音丢掉
        "call_max_utterance_s": 30,    # 单句最长几秒（到点强制提交）
        "speech_wait_timeout": 20,     # 等她说完日语最多几秒，然后重新开始听

        # 语音识别（默认本地 faster-whisper：免费、离线；云端只作备选）
        "asr_backend": "local",           # local / cloud
        "asr_local_model": "base",        # tiny / base / small
        "asr_local_dir": "",              # 留空 = data/models（模型缓存放工作区内）
        "asr_base_url": "https://api.siliconflow.cn/v1",
        "asr_model": "FunAudioLLM/SenseVoiceSmall",
        "asr_key": "",                    # 留空 = 复用 memory.embedding.api_key
        "asr_timeout": 30,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    """把用户配置合并到默认配置上：新增字段自动补齐，缺失字段用默认值。"""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Config:
    """配置文件的对象封装。用法：cfg = Config(); cfg.get("api.model"); cfg.save()"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else CONFIG_FILE
        self.data: dict = copy.deepcopy(DEFAULT_CONFIG)
        self.load()

    # ---- 读写 -------------------------------------------------------------
    def load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save()                      # 首次运行：落一份默认配置
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.data = _deep_merge(DEFAULT_CONFIG, raw)
        except Exception:                    # noqa: BLE001 配置坏了也要能启动
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = self.path.with_suffix(f".json.bak.{stamp}")
            try:
                shutil.copy2(self.path, backup)
            except Exception:                # noqa: BLE001
                backup = None
            self.data = copy.deepcopy(DEFAULT_CONFIG)
            self.save()
            self.last_load_error = (f"配置文件无法解析，已重建默认配置。备份：{backup}")

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)               # 先写临时文件再替换，避免写到一半断电损坏

    # ---- 便捷访问 ---------------------------------------------------------
    def get(self, dotted: str, default=None):
        """cfg.get("memory.top_k") -> 6"""
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, dotted: str, value) -> None:
        """cfg.set("sprite.scale", 1.2) 只改内存，记得再 save()。"""
        parts = dotted.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value

    @property
    def api_key(self) -> str:
        """当前服务商的 Key：优先用 keys 里记着的，退回老字段 api.api_key。"""
        provider = (self.get("api.provider") or "").strip()
        saved = self.get("api.keys") or {}
        if isinstance(saved, dict) and provider:
            value = (saved.get(provider) or "").strip()
            if value:
                return value
        return (self.get("api.api_key") or "").strip()

    def set_api_key(self, value: str) -> None:
        """
        设置当前服务商的 Key（界面保存时用）。

        同时写两处：keys[provider] 给"切回来还在"，api.api_key 保持旧字段可用
        （记忆提炼、翻译这些老代码都还读 cfg.api_key）。
        """
        provider = (self.get("api.provider") or "custom").strip() or "custom"
        value = (value or "").strip()
        self.set("api.api_key", value)
        keys = self.get("api.keys")
        if not isinstance(keys, dict):
            keys = {}
        keys[provider] = value
        self.set("api.keys", keys)

    @property
    def embedding_key(self) -> str:
        return (self.get("memory.embedding.api_key") or "").strip()

    # ---- 素材目录 ---------------------------------------------------------
    def sprite_dir(self) -> Path:
        """立绘目录：配置优先；留空时尝试 ../Sprite；都没有返回 None。"""
        configured = self.get("sprite.dir") or ""
        if configured:
            p = Path(configured)
            if not p.is_absolute():
                p = ROOT / p
            if p.is_dir():
                return p
        fallback = ROOT.parent / "Sprite"
        return fallback if fallback.is_dir() else None


def ensure_dirs() -> None:
    """确保运行期需要的目录存在（config/、data/、data/voice/、assets/）。"""
    for d in (CONFIG_DIR, DATA_DIR, DATA_DIR / "voice", ASSETS_DIR):
        d.mkdir(parents=True, exist_ok=True)
