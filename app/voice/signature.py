# -*- coding: utf-8 -*-
"""
「心情大好～」这句彩蛋的素材位置与文案（2026-11-06 新增）。

背景
----
设置 → 其它 里有个按钮「让她说一句「心情大好～」」，点一下会：
    1. 播放 assets/voice/taihen_ni_kibun_ga_ii.wav（日语「大変に気分がいい～」）；
    2. 把她切成「得意」表情 + 抱臂动作；
    3. 在头顶气泡显示中文「心情大好～」。

这条路**故意不走**语音服务：不走翻译、不走 GPT-SoVITS 合成、不写聊天记录，
所以哪怕语音开关是关着的、服务没起来，这个按钮也照样能响。

想换台词怎么办
-------------
- 换音频：把自己的录音转成 16-bit PCM 单声道 wav，覆盖
  `assets/voice/taihen_ni_kibun_ga_ii.wav`（或者用 tools/make_signature_audio.py 从 ogg 转）；
  也可以在设置里只改配置 `voice.signature_file` 指到别的 wav。
- 换中文文案：改配置 `voice.signature_text`，不用动代码。
"""
from __future__ import annotations

from pathlib import Path

from ..config import ASSETS_DIR

# 素材目录：assets/voice/
SIGNATURE_DIR = ASSETS_DIR / "voice"

# 播放用的 wav（16-bit PCM，单声道 44.1 kHz）
DEFAULT_WAV = SIGNATURE_DIR / "taihen_ni_kibun_ga_ii.wav"

# 原始 ogg（留档用，方便以后换设备重新转码）
SOURCE_OGG = SIGNATURE_DIR / "taihen_ni_kibun_ga_ii.ogg"

# 气泡里显示的中文（音频本身是日语）
DEFAULT_TEXT = "心情大好～"

# 这句台词对应的心情：露娜的招牌句「心情非常好。」——用「得意」最贴
# （「得意」在 assets/expressions.json 的 emotion_pose 里映射到动作 2 = 抱臂）
DEFAULT_EMOTION = "得意"


def signature_path(cfg=None) -> Path:
    """
    取这段语音的 wav 路径。

    配置 `voice.signature_file` 非空就用它（方便用户换成自己的录音），
    否则用项目内置的 assets/voice/taihen_ni_kibun_ga_ii.wav。
    """
    if cfg is not None:
        custom = str(cfg.get("voice.signature_file", "") or "").strip()
        if custom:
            return Path(custom).expanduser()
    return DEFAULT_WAV


def signature_text(cfg=None) -> str:
    """气泡里那句中文；配置 `voice.signature_text` 非空就覆盖。"""
    if cfg is not None:
        custom = str(cfg.get("voice.signature_text", "") or "").strip()
        if custom:
            return custom
    return DEFAULT_TEXT


def signature_emotion() -> str:
    """固定「得意」：招牌句的场景不该被配置改来改去，所以写死。"""
    return DEFAULT_EMOTION
