# -*- coding: utf-8 -*-
"""
记忆检索：把"跟这句话最相关的几条记忆"挑出来，塞进系统提示词。

打分是混合的（这是 RAG 里的常见做法）：
    0.65 * 向量余弦相似度      —— 懂语义（"我在写桌宠" ↔ "桌面宠物项目"）
    0.20 * 关键词 BM25 得分    —— 懂字面（人名、专有名词命中时很关键）
    0.15 * 权重 × 新近度       —— 重要的事、常被提起的事优先
然后用 MMR 去掉"意思重复"的结果，取前 top_k 条。

断网时（向量算不出来）只走 BM25，功能不中断，只是召回差一点。
"""
from __future__ import annotations

import math
import re
import time
from collections import Counter

from .embedder import BaseEmbedder, EmbeddingUnavailable
from .store import MemoryStore, normalize_text
from .vector_store import BaseVectorStore


def _tokenize(text: str) -> list[str]:
    """中文按 2 字切 + 英文数字按词切。够用，不需要分词库。"""
    norm = normalize_text(text)
    tokens = [norm[i:i + 2] for i in range(max(0, len(norm) - 1))]
    tokens += re.findall(r"[a-z0-9]{2,}", norm)
    return tokens or [norm]


class _BM25:
    """极简 BM25：事实总量只有几千条，内存里现算完全够快。"""

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.docs: dict[str, Counter] = {}
        self.lengths: dict[str, int] = {}
        self.idf: dict[str, float] = {}
        self.avg_len = 1.0

    def build(self, facts: list[dict]) -> None:
        self.docs.clear()
        self.lengths.clear()
        df: Counter = Counter()
        for fact in facts:
            tokens = _tokenize(fact["text"])
            counter = Counter(tokens)
            self.docs[fact["id"]] = counter
            self.lengths[fact["id"]] = len(tokens)
            for token in counter:
                df[token] += 1

        total = max(1, len(facts))
        self.idf = {t: math.log(1 + (total - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        self.avg_len = sum(self.lengths.values()) / total if total else 1.0

    def score(self, query: str) -> dict[str, float]:
        q_tokens = _tokenize(query)
        scores: dict[str, float] = {}
        for doc_id, counter in self.docs.items():
            dl = self.lengths.get(doc_id, 1)
            s = 0.0
            for token in q_tokens:
                if token not in counter:
                    continue
                f = counter[token]
                idf = self.idf.get(token, 0.0)
                s += idf * (f * (self.k1 + 1)) / (f + self.k1 * (1 - self.b + self.b * dl / self.avg_len))
            if s:
                scores[doc_id] = s
        return scores


class MemoryRetriever:
    """检索器：负责建索引、补向量、算得分、做 MMR。"""

    def __init__(self, store: MemoryStore, embedder: BaseEmbedder,
                 vector_store: BaseVectorStore, top_k: int = 6) -> None:
        self.store = store
        self.embedder = embedder
        self.vector_store = vector_store
        self.top_k = top_k
        self.bm25 = _BM25()
        self._built_signature = ""
        self.last_note = ""

    # ---- 索引维护 ---------------------------------------------------------
    def _signature(self) -> str:
        """事实集合的指纹：条数 + 最后一条 id，变了就重建 BM25。"""
        facts = self.store.facts()
        return f"{len(facts)}:{facts[-1]['id'] if facts else '-'}"

    def ensure_bm25(self) -> None:
        sig = self._signature()
        if sig != self._built_signature:
            self.bm25.build(self.store.facts())
            self._built_signature = sig

    def sync_index(self, limit: int = 200) -> str:
        """
        把还没向量化的事实补上，并删掉"记忆已删、向量还在"的僵尸条目。
        返回一句给界面显示的状态话。
        """
        self.ensure_bm25()
        facts = self.store.facts()
        fact_ids = {f["id"] for f in facts}

        # 清理僵尸向量
        try:
            stale = [i for i in self.vector_store.all_ids() if i not in fact_ids]
            if stale:
                self.vector_store.delete(stale)
        except Exception:  # noqa: BLE001
            pass

        pending = [f for f in self.store.pending_facts() if f.get("vec_state") != "failed"][:limit]
        if not pending:
            return self.last_note or "记忆索引已是最新"

        try:
            vectors = self.embedder.embed([f["text"] for f in pending])
        except EmbeddingUnavailable as exc:
            for fact in pending:
                fact["vec_state"] = "failed"
            self.store.save()
            self.last_note = f"向量服务暂时不可用（{exc}），先只用关键词检索。"
            return self.last_note

        metas = [{
            "tags": ",".join(f.get("tags") or []),
            "weight": float(f.get("weight", 1.0)),
            "pinned": bool(f.get("pinned")),
            "created_at": f.get("created_at", ""),
        } for f in pending]

        self.vector_store.upsert([f["id"] for f in pending], vectors, metas)
        for fact in pending:
            fact["vec_state"] = "ok"
        self.store.save()
        self.last_note = f"已为 {len(pending)} 条记忆建立向量（{self.embedder.name}）"
        return self.last_note

    def rebuild(self) -> str:
        """清空索引后全量重建（换 embedding 模型、或索引坏了时用）。"""
        try:
            self.vector_store.reset()
        except Exception:  # noqa: BLE001
            pass
        for fact in self.store.facts():
            fact["vec_state"] = "pending"
        self.store.save()
        self._built_signature = ""
        self.ensure_bm25()
        return self.sync_index(limit=10000)

    # ---- 检索 -------------------------------------------------------------
    def retrieve(self, query: str, top_k: int | None = None) -> list[dict]:
        """返回最相关的记忆（已经按 id 映射回完整事实卡）。"""
        k = top_k or self.top_k
        facts = self.store.facts()
        if not facts or not (query or "").strip():
            return []
        self.ensure_bm25()

        by_id = {f["id"]: f for f in facts}
        cos_scores: dict[str, float] = {}
        note = ""

        # ① 向量召回
        try:
            q_vec = self.embedder.embed_query(query)
            for fid, sim in self.vector_store.query(q_vec, k=min(20, max(k * 3, 10))):
                if fid in by_id:
                    cos_scores[fid] = sim
        except EmbeddingUnavailable as exc:
            note = f"（向量检索不可用：{exc}）"

        # ② 关键词召回
        bm25_scores = self.bm25.score(query)
        max_bm25 = max(bm25_scores.values()) if bm25_scores else 1.0

        # ③ 固定注入：被钉住的记忆永远进候选
        pinned = [f for f in facts if f.get("pinned")]

        candidates = set(cos_scores) | set(bm25_scores) | {f["id"] for f in pinned}
        if not candidates:
            # 什么都没命中时，给最新的几条，避免"完全失忆"的观感
            latest = sorted(facts, key=lambda f: f.get("created_at", ""), reverse=True)[:k]
            return latest

        now = time.time()
        ranked: list[tuple[dict, float]] = []
        for fid in candidates:
            fact = by_id.get(fid)
            if not fact:
                continue
            cos = cos_scores.get(fid, 0.0)
            bm = bm25_scores.get(fid, 0.0) / max_bm25 if max_bm25 else 0.0
            weight = min(1.0, float(fact.get("weight", 1.0)) / 3.0)
            recency = self._recency(fact.get("last_hit_at") or fact.get("created_at", ""), now)
            score = 0.65 * cos + 0.20 * bm + 0.15 * (0.5 * weight + 0.5 * recency)
            if fact.get("pinned"):
                score += 0.05            # 钉住的略优先，但不至于压过强相关
            ranked.append((fact, score))

        ranked.sort(key=lambda x: x[1], reverse=True)

        # ④ MMR：避免挑出好几条意思差不多的记忆
        selected: list[dict] = []
        pool = ranked[: max(k * 4, 12)]
        while pool and len(selected) < k:
            best_idx, best_val = 0, -1e9
            for i, (fact, score) in enumerate(pool):
                redundancy = max(
                    (_text_sim(fact["text"], s["text"]) for s in selected),
                    default=0.0,
                )
                mmr = 0.7 * score - 0.3 * redundancy
                if mmr > best_val:
                    best_idx, best_val = i, mmr
            selected.append(pool.pop(best_idx)[0])

        self.last_note = note
        return selected

    @staticmethod
    def _recency(stamp: str, now: float) -> float:
        """越近的事越"新鲜"，7 天半衰。"""
        try:
            t = time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))
        except Exception:  # noqa: BLE001
            return 0.5
        days = max(0.0, (now - t) / 86400.0)
        return math.exp(-days / 7.0)

    def format_for_prompt(self, facts: list[dict]) -> str:
        """把检索结果拼成给模型看的一段文字。"""
        if not facts:
            return ""
        lines = []
        for fact in facts:
            text = fact["text"].strip()
            if len(text) > 120:
                text = text[:120] + "…"
            lines.append(f"- {text}")
        return "\n".join(lines)


def _text_sim(a: str, b: str) -> float:
    """MMR 用的句间相似度（2-gram 重合），避免依赖外部库。"""
    na, nb = normalize_text(a), normalize_text(b)
    ga = {na[i:i + 2] for i in range(max(0, len(na) - 1))}
    gb = {nb[i:i + 2] for i in range(max(0, len(nb) - 1))}
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)
