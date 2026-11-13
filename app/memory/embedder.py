# -*- coding: utf-8 -*-
"""
把文本变成向量（embedding）。

主用：硅基流动的 BAAI/bge-m3（1024 维，中文效果好，接口是 OpenAI 兼容格式）。
兜底：本地 HashEmbedder（字符 n-gram 哈希 + L2 归一化，离线、零依赖、不需要 Key）。
     它不"懂"语义，只按字面相似度算，但足以在断网时不让记忆功能彻底瘫掉。

另外带一个 SQLite 缓存：同一句话不重复算向量，省时间也省钱。
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import struct
import time
from pathlib import Path

import httpx

from ..config import DATA_DIR

CACHE_FILE = DATA_DIR / "embed_cache.sqlite"
HASH_DIM = 1024
BGE_M3_DIM = 1024


class EmbeddingUnavailable(RuntimeError):
    """向量服务不可用（没 Key / 网络断 / 限额），上层据此降级。"""


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
class EmbeddingCache:
    """把算过的向量存进 sqlite，key 用文本的 sha1，避免重复付费。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else CACHE_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors("
            " model TEXT NOT NULL, key TEXT NOT NULL, dim INTEGER NOT NULL, vec BLOB NOT NULL,"
            " PRIMARY KEY(model, key))"
        )
        self._conn.commit()

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def get(self, model: str, text: str) -> list[float] | None:
        row = self._conn.execute(
            "SELECT dim, vec FROM vectors WHERE model=? AND key=?", (model, self._key(text))
        ).fetchone()
        if not row:
            return None
        dim, blob = row
        return list(struct.unpack(f"<{dim}f", blob))

    def put(self, model: str, text: str, vec: list[float]) -> None:
        blob = struct.pack(f"<{len(vec)}f", *vec)
        self._conn.execute(
            "INSERT OR REPLACE INTO vectors(model, key, dim, vec) VALUES(?,?,?,?)",
            (model, self._key(text), len(vec), blob),
        )
        self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 基类
# ---------------------------------------------------------------------------
class BaseEmbedder:
    name = "base"
    dim = 0
    remote = False           # 是否会把文本发到外网（界面要据此提示隐私）

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        """
        只为一个"查询句子"算向量（对话里每说一句话就要算一次）。

        单独留这个方法，是因为"给记忆批量建索引"和"给一句提问算向量"的容错要求不一样：
        批量建索引值得多试几次，提问只值得等一小会儿，不行就退回关键词检索。
        """
        return self.embed([text])[0]


# ---------------------------------------------------------------------------
# 硅基流动 bge-m3
# ---------------------------------------------------------------------------
class SiliconFlowEmbedder(BaseEmbedder):
    """调用 https://api.siliconflow.cn/v1/embeddings，模型 BAAI/bge-m3。"""

    remote = True

    def __init__(self, base_url: str, api_key: str, model: str = "BAAI/bge-m3",
                 batch: int = 16, timeout: float = 60.0, query_timeout: float = 8.0,
                 cache: EmbeddingCache | None = None) -> None:
        self.base_url = (base_url or "https://api.siliconflow.cn/v1").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.model = model or "BAAI/bge-m3"
        self.batch = max(1, int(batch))
        self.timeout = timeout
        # 只算"一句提问"时的超时（比批量建索引短很多）+ 失败冷却
        self.query_timeout = max(2.0, float(query_timeout))
        self._cooldown_until = 0.0
        self.cache = cache or EmbeddingCache()
        self.name = f"siliconflow:{self.model}"
        self.dim = BGE_M3_DIM

    # ---- 单批请求 ---------------------------------------------------------
    def _request(self, texts: list[str], timeout: float | None = None,
                 attempts: int = 3) -> list[list[float]]:
        if not self.api_key:
            raise EmbeddingUnavailable("没有填硅基流动的 API Key")

        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts, "encoding_format": "float"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        use_timeout = float(timeout or self.timeout)

        last_error = "未知错误"
        for attempt in range(max(1, attempts)):
            try:
                with httpx.Client(timeout=use_timeout) as client:
                    resp = client.post(url, json=payload, headers=headers)
                if resp.status_code == 401:
                    raise EmbeddingUnavailable("硅基流动 API Key 无效（401）")
                if resp.status_code == 429:
                    last_error = "被限流（429）"
                    time.sleep(2 * (attempt + 1))
                    continue
                if resp.status_code >= 500:
                    last_error = f"服务器错误（{resp.status_code}）"
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    raise EmbeddingUnavailable(f"向量接口返回 {resp.status_code}：{resp.text[:120]}")

                data = resp.json().get("data") or []
                vectors = [item.get("embedding") or [] for item in data]
                if len(vectors) != len(texts) or not vectors:
                    raise EmbeddingUnavailable("向量接口返回条数与请求不一致")
                self.dim = len(vectors[0])
                return vectors
            except EmbeddingUnavailable:
                raise
            except httpx.TimeoutException:
                last_error = "请求超时"
                time.sleep(1.5 * (attempt + 1))
            except Exception as exc:  # noqa: BLE001 网络类错误统一重试
                last_error = str(exc)

        raise EmbeddingUnavailable(f"向量服务不可用：{last_error}")

    # ---- 对外接口 ---------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """批量算向量；命中缓存的直接取，只把没算过的送给接口。"""
        texts = [t or "" for t in texts]
        if not texts:
            return []

        result: list[list[float] | None] = [None] * len(texts)
        missing: list[tuple[int, str]] = []
        for i, text in enumerate(texts):
            cached = self.cache.get(self.name, text) if text.strip() else None
            if cached:
                result[i] = cached
            else:
                missing.append((i, text))

        for start in range(0, len(missing), self.batch):
            chunk = missing[start:start + self.batch]
            payload_texts = [t if t.strip() else "（空）" for _, t in chunk]
            vectors = self._request(payload_texts)
            for (idx, original), vec in zip(chunk, vectors):
                result[idx] = vec
                if original.strip():
                    self.cache.put(self.name, original, vec)

        return [v or [0.0] * self.dim for v in result]

    def embed_query(self, text: str) -> list[float]:
        """
        给"一句提问"算向量：只试一次、超时很短（默认 8 秒）。

        失败后进入 60 秒冷却期，这期间直接抛 EmbeddingUnavailable，让检索退回
        纯关键词（BM25）。这样做的好处：硅基流动偶尔抽风时，聊天不会被拖慢，
        也不会每句话都重新等一次超时。
        """
        text = (text or "").strip()
        if not text:
            raise EmbeddingUnavailable("空查询")

        if time.time() < self._cooldown_until:
            raise EmbeddingUnavailable("向量服务刚失败过，先只用关键词检索")

        cached = self.cache.get(self.name, text)
        if cached:
            return cached

        try:
            vector = self._request([text], timeout=self.query_timeout, attempts=1)[0]
        except EmbeddingUnavailable as exc:
            self._cooldown_until = time.time() + 60.0
            raise EmbeddingUnavailable(str(exc)) from None

        self.cache.put(self.name, text, vector)
        return vector


# ---------------------------------------------------------------------------
# 本地哈希向量（兜底）
# ---------------------------------------------------------------------------
class HashEmbedder(BaseEmbedder):
    """
    完全离线的"伪向量"：把中文按 1~3 字切片，哈希到 1024 个桶里再归一化。
    它算的是"字面重合度"，不等于语义，但在没有 Key 时比什么都不做强。
    """

    remote = False

    def __init__(self, dim: int = HASH_DIM) -> None:
        self.dim = dim
        self.name = f"hash:{dim}"

    def _hash(self, token: str) -> int:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        return int.from_bytes(digest[:4], "little") % self.dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            clean = "".join(ch for ch in (text or "") if not ch.isspace())
            for n in (1, 2, 3):
                for i in range(max(0, len(clean) - n + 1)):
                    token = clean[i:i + n]
                    vec[self._hash(f"{n}:{token}")] += 1.0
            norm = math.sqrt(sum(v * v for v in vec))
            out.append([v / norm for v in vec] if norm else vec)
        return out


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def build_embedder(cfg, cache: EmbeddingCache | None = None) -> tuple[BaseEmbedder, str]:
    """
    按配置创建 embedding 器。
    返回 (embedder, 提示信息)：提示信息用于界面状态栏，告诉用户现在用的是哪种向量。
    """
    provider = (cfg.get("memory.embedding.provider") or "siliconflow").lower()
    api_key = cfg.embedding_key
    model = cfg.get("memory.embedding.model") or "BAAI/bge-m3"
    base_url = cfg.get("memory.embedding.base_url") or "https://api.siliconflow.cn/v1"

    if provider == "siliconflow" and api_key and cfg.get("memory.allow_upload", True):
        return SiliconFlowEmbedder(base_url, api_key, model,
                                   batch=int(cfg.get("memory.embedding.batch", 16) or 16),
                                   query_timeout=float(
                                       cfg.get("memory.embedding.query_timeout", 8) or 8),
                                   cache=cache), f"bge-m3（{model}）"

    if provider == "siliconflow" and api_key and not cfg.get("memory.allow_upload", True):
        return HashEmbedder(), "本地 hash（你关闭了上传）"
    if provider == "siliconflow" and not api_key:
        return HashEmbedder(), "本地 hash（未填硅基流动 Key）"
    return HashEmbedder(), "本地 hash"


def vector_to_json(vec: list[float]) -> str:
    """调试用：把向量压成一行字符串，方便看日志。"""
    return json.dumps([round(v, 5) for v in vec[:8]]) + f"…({len(vec)}维)"
