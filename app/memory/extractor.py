# -*- coding: utf-8 -*-
"""
自动提炼记忆：每隔几轮，让模型从最近的对话里挑出"值得长期记住"的事。

比如：
    用户："我明天要交毕设，可能没空陪你"
    → 提炼出「用户最近在忙毕业设计，明天有截止」

做法：把最近 8 轮对话发给 deepseek-chat，要求它输出一段严格 JSON，
拿回来后再交给 MemoryStore 去重入库、算向量。失败就静默跳过，绝不打扰用户。
"""
from __future__ import annotations

import json
import re
import threading
from typing import Callable

from ..chat.client import complete_sync
from .store import MemoryStore

EXTRACT_PROMPT = """你是记忆整理器。下面是一段用户与角色扮演角色的对话记录。
请找出其中"值得长期记住的、关于用户的稳定信息"。

只记录这些类型：
- 用户的名字、称呼、身份、所在地
- 用户的喜好 / 厌恶 / 习惯
- 用户正在做的长期项目、目标、重要事件（带日期更好）
- 用户明确要求你记住的内容

不要记录：一次性的闲聊、情绪、天气、角色自己的台词、推测的内容。

严格输出 JSON，不要任何解释、不要代码块围栏：
{"new_facts": ["用第三人称陈述，20 字左右，中文", "..."], "user_name": "", "summary": "一句话概括这段对话的要点"}
没有值得记的内容时，new_facts 给空数组。new_facts 最多 3 条。
"""


class MemoryExtractor(threading.Thread):
    """
    后台线程：提炼 -> 入库。结束后通过 on_done(facts) 通知界面（Qt 里传信号 emit 即可）。
    """

    def __init__(self, store: MemoryStore, base_url: str, api_key: str, model: str,
                 turns: list[dict], on_done: Callable[[list[dict], str], None] | None = None) -> None:
        super().__init__(daemon=True)
        self.store = store
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.turns = turns          # [{"role": "user"/"assistant", "content": "..."}]
        self.on_done = on_done
        self.error = ""

    def run(self) -> None:
        added: list[dict] = []
        summary = ""
        try:
            if not self.api_key or not self.turns:
                return
            lines = []
            for turn in self.turns:
                role = "用户" if turn.get("role") == "user" else "露娜"
                lines.append(f"{role}：{turn.get('content', '')[:400]}")
            messages = [
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": "对话记录：\n" + "\n".join(lines)},
            ]
            raw = complete_sync(self.base_url, self.api_key, self.model, messages,
                                temperature=0.2, timeout=45, json_mode=True)
            payload = _parse_json(raw)

            for text in (payload.get("new_facts") or [])[:3]:
                if not isinstance(text, str):
                    continue
                fact = self.store.add_fact(text.strip(), tags=["自动提炼"], weight=1.0,
                                           source="extractor")
                if fact:
                    added.append(fact)

            name = payload.get("user_name") or ""
            if isinstance(name, str) and name.strip() and not self.store.profile.get("user_name"):
                self.store.update_profile(user_name=name.strip()[:20])

            summary = (payload.get("summary") or "").strip()
            if summary:
                self.store.add_summary(summary)
        except Exception as exc:  # noqa: BLE001 记忆提炼失败不该影响对话
            self.error = str(exc)
        finally:
            if self.on_done:
                self.on_done(added, self.error)


def _parse_json(raw: str) -> dict:
    """模型爱在 JSON 前后加解释或代码块，这里尽量把对象抠出来。"""
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}
    return {}
