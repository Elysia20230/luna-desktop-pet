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

from .. import paths
from ..config import ASSETS_DIR

# 素材文件名（wav 用来播放，ogg 只是留档）
SIGNATURE_NAME = "taihen_ni_kibun_ga_ii.wav"
SOURCE_OGG_NAME = "taihen_ni_kibun_ga_ii.ogg"

# 素材目录：assets/voice/
SIGNATURE_DIR = ASSETS_DIR / "voice"

# 播放用的 wav（16-bit PCM，单声道 44.1 kHz）
DEFAULT_WAV = SIGNATURE_DIR / SIGNATURE_NAME

# 原始 ogg（留档用，方便以后换设备重新转码）
SOURCE_OGG = SIGNATURE_DIR / SOURCE_OGG_NAME

# 气泡里显示的中文（音频本身是日语）
DEFAULT_TEXT = "心情大好～"

# 这句台词对应的心情：露娜的招牌句「心情非常好。」——用「得意」最贴
# （「得意」在 assets/expressions.json 的 emotion_pose 里映射到动作 2 = 抱臂）
DEFAULT_EMOTION = "得意"


def signature_candidates(cfg=None) -> list[Path]:
    """
    这段彩蛋语音"可能在哪"——按优先级排一遍。

    为什么要这么多个位置：源码运行时它在 `assets/voice/`；打包成 exe 以后，
    打包资源在 `_internal\\assets`（只读），而素材包是让用户解压到 exe 同级的
    `LunaPet\\assets\\voice\\`，还有人事先扔进 `data\\voice\\`——三个都得认。
    """
    out: list[Path] = []

    if cfg is not None:
        custom = str(cfg.get("voice.signature_file", "") or "").strip()
        if custom:
            p = Path(custom).expanduser()
            # 相对路径按"程序所在目录"算，免得用户被 cwd 坑
            if not p.is_absolute():
                p = paths.ROOT / p
            out.append(p)

    for base in (paths.ASSETS_DIR, paths.assets_overlay(), paths.DATA_DIR, paths.BUNDLE / "assets"):
        out.append(base / "voice" / SIGNATURE_NAME)

    # 去重但保留顺序
    seen: set[str] = set()
    unique: list[Path] = []
    for p in out:
        key = str(p).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def signature_path(cfg=None) -> Path:
    """
    取这段语音的 wav 路径。

    - 配了 `voice.signature_file`：就用它（哪怕文件不在——用户明写了就该按他说的来，
      找不到时由调用方提示，而不是偷偷放另一段）
    - 没配：按 `signature_candidates()` 顺序找第一个真实存在的；
      都找不到返回第一个候选，供错误提示显示"本来该在哪"。
    """
    candidates = signature_candidates(cfg)
    if cfg is not None and str(cfg.get("voice.signature_file", "") or "").strip():
        return candidates[0]
    for p in candidates:
        if p.is_file():
            return p
    return candidates[0]


def signature_hint() -> str:
    """找不到音频时给用户看的说明（中文、可操作、别只说"失败了"）。"""
    return (
        "这段彩蛋语音不在本机。两种办法：\n"
        "1) 去 Release 下载素材包（luna-assets-*.zip），解压到 LunaPet 文件夹里，"
        "让 assets\\voice\\taihen_ni_kibun_ga_ii.wav 出现；\n"
        "2) 或者随便找个 wav，在 设置 → 其它 →「彩蛋音频」里选上。\n"
        "（这段只影响这个按钮，聊天、语音朗读、立绘都不受影响。）"
    )


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
