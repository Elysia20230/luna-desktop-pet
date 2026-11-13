# -*- coding: utf-8 -*-
"""
中文台词 → 日语台词。

为什么需要这一步：露娜说中文（你看到的字幕是中文），但语音必须是日语。
所以每句话说完后，把中文再"演"一遍成日语的露娜口吻，交给语音合成。

设计取舍：
- 单独发一次小请求，而不是让主模型同时输出中日双语。
  好处是主对话的提示词/风格一点不受影响，翻译坏了也只是没声音，不会毁掉聊天内容；
  坏处是多花约 1 秒——反正后面合成要 8 秒以上，这点时间看不出来。
- 翻译结果按原文缓存到 `data/voice/translate.json`，同一句话不会反复花钱。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from ..chat.client import complete_sync
from ..config import DATA_DIR

CACHE_FILE = DATA_DIR / "voice" / "translate.json"
CACHE_LIMIT = 500          # 最多记多少条翻译（超了丢最旧的）

# 提示词：只要一行日语，保留傲娇/强势，不要大小姐腔
TRANSLATE_SYSTEM = (
    "你是动画《近月少女的礼仪》里樱小路露娜的台词改写者。\n"
    "把用户给的中文台词改写成日语口语，要求：\n"
    "1. 保持原意和她说话的长度、节奏，不要扩写，不要加解释；\n"
    "2. 用她的口吻：简洁、带一点居高临下或小恶魔的意味，可以保留「…」；\n"
    "3. 人称：她自称「私」，称对方按中文里的称呼（朝日=朝日、游星=遊星）；\n"
    "4. 绝对不要用「ですわ」「かしら」「なさい」这类大小姐腔；\n"
    "5. 只输出一行日语，不要引号、不要翻译说明、不要中文。"
)


class TranslationCache:
    """极简的"原文 → 日语"磁盘缓存。"""

    def __init__(self, path: Path | None = None, limit: int = CACHE_LIMIT) -> None:
        self.path = Path(path) if path else CACHE_FILE
        self.limit = limit
        self.data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = {str(k): str(v) for k, v in raw.items()}
        except Exception:  # noqa: BLE001 缓存坏了当没有，绝不打断朗读流程
            self.data = {}

    def get(self, text: str) -> str | None:
        return self.data.get(_key(text))

    def put(self, text: str, japanese: str) -> None:
        self.data[_key(text)] = japanese
        if len(self.data) > self.limit:
            # dict 保持插入顺序，砍掉最早的
            for old in list(self.data)[: len(self.data) - self.limit]:
                self.data.pop(old, None)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            pass


def _key(text: str) -> str:
    return hashlib.sha1((text or "").strip().encode("utf-8")).hexdigest()


def build_messages(text_zh: str) -> list[dict]:
    """拼出翻译请求的 messages（单独抽出来，方便测试）。"""
    return [
        {"role": "system", "content": TRANSLATE_SYSTEM},
        {"role": "user", "content": (text_zh or "").strip()},
    ]


def clean_reply(raw: str) -> str:
    """把模型可能多给的引号、括号、解释行清理掉，只留一行日语。"""
    text = (raw or "").strip()
    if not text:
        return ""
    # 只取第一行非空内容
    for line in text.splitlines():
        line = line.strip()
        if line:
            text = line
            break
    # 去掉包着的引号/括号
    for left, right in (("「", "」"), ("『", "』"), ("\"", "\""), ("“", "”"), ("'", "'")):
        if text.startswith(left) and text.endswith(right) and len(text) > 2:
            text = text[len(left):-len(right)].strip()
    # 有的模型会写"日语：xxx"
    text = re.sub(r"^(日语|日文|日本語|Japanese)\s*[:：]\s*", "", text)
    # 出现中文说明就截断（简单判断：连续两个汉字且不在常见日语汉字里…这里只做最基本清理）
    return text.strip()


def translate_to_japanese(text_zh: str, cfg, cache: TranslationCache | None = None) -> str:
    """
    把一句中文翻成日语。缓存命中直接返回。
    失败会抛异常（API Key 没填、网络错等），由 VoiceService 统一兜底。
    """
    text_zh = (text_zh or "").strip()
    if not text_zh:
        return ""
    cache = cache or TranslationCache()
    hit = cache.get(text_zh)
    if hit:
        return hit

    api_key = cfg.api_key
    if not api_key:
        raise RuntimeError("没有填 DeepSeek 的 Key，没法把台词翻成日语")

    model = str(cfg.get("voice.translate_model", "") or "").strip() or str(
        cfg.get("api.model", "deepseek-chat")
    )
    raw = complete_sync(
        base_url=cfg.get("api.base_url", "https://api.deepseek.com"),
        api_key=api_key,
        model=model,
        messages=build_messages(text_zh),
        temperature=0.3,
        timeout=45.0,
    )
    japanese = clean_reply(raw)
    if japanese:
        cache.put(text_zh, japanese)
    return japanese
