# -*- coding: utf-8 -*-
"""
情绪解析：从模型回复里抠出情绪，并给出一个"情绪 → 立绘"的通用词汇表。

为什么需要它？
    模型被要求在回复末尾输出 <mood:害羞> 这样的标记，程序读到它就把立绘换成害羞的表情。
    但模型不一定老实，可能会写成 [[mood:害羞]]、【情绪：害羞】，甚至干脆忘了写，
    所以这里做三件事：
      1. 识别多种写法的标记，并把它从"要显示给用户的文字"里删掉；
      2. 标记缺失时，用关键词猜一个情绪（兜底，保证立绘永远有表情）；
      3. 把各式各样的情绪词归一化到 12 档，方便和 expressions.json 对齐。
"""
from __future__ import annotations

import re

# 立绘支持的情绪档位（与 assets/expressions.json 的 emotions 一一对应）
EMOTIONS = ["平静", "微笑", "开心", "得意", "害羞", "生气", "惊讶", "失落", "思考", "无语", "温柔", "难过"]

# 模型可能给出的各种写法，统一映射到上面 12 档
SYNONYMS = {
    "平静": ["平静", "平淡", "neutral", "default", "usual", "冷静"],
    "微笑": ["微笑", "轻笑", "浅笑", "smile", "含笑", "愉悦"],
    "开心": ["开心", "高兴", "快乐", "happy", "大笑", "欢喜", "兴奋"],
    "得意": ["得意", "自豪", "欣慰", "proud", "自信", "傲然", "神气"],
    "害羞": ["害羞", "脸红", "羞", "臊", "shy", "blush", "难为情", "娇羞"],
    "生气": ["生气", "恼", "怒", "angry", "愠", "不满", "发脾气", "凶"],
    "惊讶": ["惊讶", "吃惊", "意外", "surprise", "震惊", "诧异"],
    "失落": ["失落", "沮丧", "低落", "sad", "郁闷", "消沉"],
    "思考": ["思考", "沉吟", "think", "沉思", "琢磨"],
    "无语": ["无语", "无奈", "speechless", "汗", "扶额", "吐槽"],
    "温柔": ["温柔", "柔和", "怜爱", "gentle", "轻柔", "宠溺"],
    "难过": ["难过", "悲伤", "委屈", "哭泣", "cry", "伤心", "哽咽"],
}

# 允许出现在标记里的"键名"
_MOOD_KEYS = "情绪|心情|表情|mood|emotion"

# 三种容错写法：<mood:害羞> / [[mood:害羞]] / 【情绪：害羞】
_PATTERNS = [
    re.compile(r"<\s*(" + _MOOD_KEYS + r")\s*[:：]\s*([^>\n]{1,12})\s*>", re.IGNORECASE),
    re.compile(r"\[\[\s*(" + _MOOD_KEYS + r")\s*[:：]\s*([^\]\n]{1,12})\s*\]\]", re.IGNORECASE),
    re.compile(r"[【\[]\s*(" + _MOOD_KEYS + r")\s*[:：]\s*([^】\]\n]{1,12})\s*[】\]]", re.IGNORECASE),
]

_DEFAULT = "平静"


def normalize(emotion: str) -> str | None:
    """把任意情绪词归一化成 12 档之一；认不出来返回 None。"""
    text = (emotion or "").strip().strip("。.!！~～、 ")
    if not text:
        return None
    if text in EMOTIONS:
        return text
    lowered = text.lower()
    for canonical, words in SYNONYMS.items():
        for word in words:
            if word.lower() in lowered:
                return canonical
    return None


def parse(raw_text: str) -> tuple[str, str | None]:
    """
    从模型原始回复中剥离情绪标记。
    返回 (干净的正文, 情绪)；找不到标记时情绪返回 None，交给调用方兜底。
    """
    if not raw_text:
        return "", None
    text = raw_text
    found: str | None = None
    for pattern in _PATTERNS:
        for match in pattern.finditer(text):
            candidate = normalize(match.group(2))
            if candidate and not found:
                found = candidate
        text = pattern.sub("", text)
    # 标记被删掉后可能留下空行或行尾空格，清理一下
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, found


def guess(text: str) -> str:
    """没有标记时，根据文字猜情绪（简单关键词 + 标点规则，够用即可）。"""
    if not text:
        return _DEFAULT
    t = text

    if any(k in t for k in ("对不起", "抱歉", "不会了", "原谅", "呜")):
        return "难过"
    if any(k in t for k in ("哼", "笨蛋", "蠢", "惩罚", "不许", "给我", "闭嘴")):
        return "生气"
    if any(k in t for k in ("……", "...", "不必", "随你", "无所谓", "讨厌")):
        return "害羞"
    if any(k in t for k in ("是嘛", "哦？", "有趣", "当然", "自然", "心情非常好")):
        return "得意"
    if any(k in t for k in ("诶", "欸", "真的吗", "怎么会", "骗人")):
        return "惊讶"
    if any(k in t for k in ("怎么办", "想想", "让我看看", "大约")):
        return "思考"
    if any(k in t for k in ("你", "吗", "吧", "呢")) and t.endswith(("。", "嘛", "呢", "吧")):
        return "温柔" if any(k in t for k in ("呢", "吧", "嘛")) else "微笑"
    return _DEFAULT


def to_emotion(cleaned_emotion: str | None, text: str) -> str:
    """优先用模型给的情绪，没有就用关键词猜；结果一定落在 12 档之内。"""
    return normalize(cleaned_emotion or "") or guess(text)
