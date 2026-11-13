# -*- coding: utf-8 -*-
"""
向量库：默认用 ChromaDB，装不上/坏掉时自动退回 SQLite + numpy 的简易实现。

两者对外接口完全一样，所以上层（retriever）不用关心到底用哪一个：
    upsert(ids, vectors, metas)      写入或更新
    delete(ids)                      删除
    query(vector, k, where)          按向量找回最相似的若干条 -> [(id, 相似度0~1)]
    count() / reset()                条数 / 清空

为什么要留 SQLite 兜底？
    ChromaDB 依赖比较重（会拉 numpy、pydantic 等一堆包），有的机器装不上或加载报错。
    记忆只有几千条，用 numpy 全表算余弦完全够快，这样"向量检索"这个功能永远不会因为
    一个第三方库而整块瘫掉。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..config import ROOT

try:  # numpy 由 chromadb 一并装上；万一没有，下面有纯 Python 兜底
    import numpy as np
except Exception:  # noqa: BLE001
    np = None


class BaseVectorStore:
    backend = "base"
    ok = True
    note = ""

    def upsert(self, ids: list[str], vectors: list[list[float]], metas: list[dict]) -> None:
        raise NotImplementedError

    def delete(self, ids: list[str]) -> None:
        raise NotImplementedError

    def query(self, vector: list[float], k: int = 20, where: dict | None = None) -> list[tuple[str, float]]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def all_ids(self) -> list[str]:
        """列出索引里所有 id（用来清理"记忆已删、向量还在"的僵尸条目）。"""
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# ChromaDB 实现
# ---------------------------------------------------------------------------
class ChromaVectorStore(BaseVectorStore):
    backend = "chroma"
    COLLECTION = "luna_memory"

    def __init__(self, path: Path, fingerprint: str) -> None:
        import chromadb  # 延迟导入：装不上时由工厂函数 catch 住并降级

        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)
        self.fingerprint = fingerprint
        self.client = chromadb.PersistentClient(path=str(self.path))
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION,
            metadata={"hnsw:space": "cosine", "fingerprint": fingerprint},
            embedding_function=None,     # 关键：向量由我们自己算、自己传，绝不让 Chroma 偷偷调模型
        )
        self._check_fingerprint()

    def _check_fingerprint(self) -> None:
        """向量模型换了（比如从 hash 换到 bge-m3），旧向量的维度对不上，必须重建。"""
        meta = dict(self.collection.metadata or {})
        if meta.get("fingerprint") and meta["fingerprint"] != self.fingerprint:
            self.note = (f"向量模型已变化（{meta.get('fingerprint')} → {self.fingerprint}），"
                         f"旧索引已清空，需要重建。")
            self.reset()

    def upsert(self, ids, vectors, metas) -> None:
        if not ids:
            return
        self.collection.upsert(ids=ids, embeddings=vectors, metadatas=metas)
        # 只刷新指纹。注意：hnsw:space（距离函数）在 Chroma 里创建后不能改，
        # 一旦传进去就会抛 "Changing the distance function ... not supported"。
        try:
            self.collection.modify(metadata={"fingerprint": self.fingerprint})
        except Exception:  # noqa: BLE001 元数据更新失败不影响检索
            pass

    def delete(self, ids) -> None:
        if ids:
            self.collection.delete(ids=ids)

    def query(self, vector, k: int = 20, where: dict | None = None):
        total = self.count()
        if total == 0:
            return []
        result = self.collection.query(
            query_embeddings=[vector],
            n_results=min(k, total),
            where=where or None,
            include=["distances"],
        )
        ids = (result.get("ids") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        # cosine 距离 0~2 → 相似度 1-距离
        return [(i, max(0.0, 1.0 - float(d))) for i, d in zip(ids, dists)]

    def count(self) -> int:
        try:
            return int(self.collection.count())
        except Exception:  # noqa: BLE001
            return 0

    def all_ids(self) -> list[str]:
        try:
            return list(self.collection.get(include=[])["ids"])
        except Exception:  # noqa: BLE001
            return []

    def reset(self) -> None:
        try:
            self.client.delete_collection(self.COLLECTION)
        except Exception:  # noqa: BLE001
            pass
        self.collection = self.client.get_or_create_collection(
            name=self.COLLECTION,
            metadata={"hnsw:space": "cosine", "fingerprint": self.fingerprint},
            embedding_function=None,
        )


# ---------------------------------------------------------------------------
# SQLite + numpy 实现（兜底）
# ---------------------------------------------------------------------------
class SQLiteVectorStore(BaseVectorStore):
    backend = "sqlite"

    def __init__(self, path: Path, fingerprint: str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fingerprint = fingerprint
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS vectors(id TEXT PRIMARY KEY, dim INTEGER, vec BLOB, meta TEXT)"
        )
        self._conn.commit()
        self._check_fingerprint()

    def _get_meta(self, key: str) -> str:
        row = self._conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else ""

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES(?,?)", (key, value))
        self._conn.commit()

    def _check_fingerprint(self) -> None:
        stored = self._get_meta("fingerprint")
        if stored and stored != self.fingerprint:
            self.note = f"向量模型已变化（{stored} → {self.fingerprint}），旧索引已清空，需要重建。"
            self.reset()
        self._set_meta("fingerprint", self.fingerprint)

    def upsert(self, ids, vectors, metas) -> None:
        import struct
        for i, vec, meta in zip(ids, vectors, metas):
            blob = struct.pack(f"<{len(vec)}f", *vec)
            self._conn.execute(
                "INSERT OR REPLACE INTO vectors(id, dim, vec, meta) VALUES(?,?,?,?)",
                (i, len(vec), blob, json.dumps(meta, ensure_ascii=False)),
            )
        self._conn.commit()

    def delete(self, ids) -> None:
        for i in ids:
            self._conn.execute("DELETE FROM vectors WHERE id=?", (i,))
        self._conn.commit()

    def query(self, vector, k: int = 20, where: dict | None = None):
        import struct
        rows = self._conn.execute("SELECT id, dim, vec, meta FROM vectors").fetchall()
        if not rows:
            return []

        scored: list[tuple[str, float]] = []
        if np is not None:
            q = np.asarray(vector, dtype="float32")
            q_norm = float(np.linalg.norm(q)) or 1.0
            for rid, dim, blob, meta_json in rows:
                if where and not _meta_match(json.loads(meta_json or "{}"), where):
                    continue
                v = np.frombuffer(blob, dtype="<f4")
                if v.size != q.size:
                    continue
                denom = (float(np.linalg.norm(v)) or 1.0) * q_norm
                scored.append((rid, float(np.dot(q, v)) / denom))
        else:
            q = vector
            q_norm = sum(x * x for x in q) ** 0.5 or 1.0
            for rid, dim, blob, meta_json in rows:
                if where and not _meta_match(json.loads(meta_json or "{}"), where):
                    continue
                v = list(struct.unpack(f"<{dim}f", blob))
                if len(v) != len(q):
                    continue
                dot = sum(a * b for a, b in zip(q, v))
                denom = (sum(b * b for b in v) ** 0.5) * q_norm
                scored.append((rid, dot / denom if denom else 0.0))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [(rid, max(0.0, min(1.0, sim))) for rid, sim in scored[:k]]

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM vectors").fetchone()
        return int(row[0]) if row else 0

    def all_ids(self) -> list[str]:
        return [row[0] for row in self._conn.execute("SELECT id FROM vectors").fetchall()]

    def reset(self) -> None:
        self._conn.execute("DELETE FROM vectors")
        self._conn.commit()


def _meta_match(meta: dict, where: dict) -> bool:
    """非常简单的 where 过滤：只支持 {"key": 值} 的相等比较。"""
    for key, value in where.items():
        if meta.get(key) != value:
            return False
    return True


# ---------------------------------------------------------------------------
# 工厂
# ---------------------------------------------------------------------------
def build_vector_store(cfg, fingerprint: str) -> BaseVectorStore:
    """
    按配置选后端。返回的对象一定有 ok/note 两个属性，界面据此显示状态。
    顺序：配置要求 → 能用的就用；ChromaDB 出任何问题都安静降级到 SQLite。
    """
    backend = (cfg.get("memory.vector_db.backend") or "auto").lower()
    raw_path = cfg.get("memory.vector_db.path") or "data/chroma"
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path

    if backend in ("auto", "chroma"):
        try:
            return ChromaVectorStore(path, fingerprint)
        except Exception as exc:  # noqa: BLE001 装不上/加载失败都不该影响使用
            store = SQLiteVectorStore(path.with_suffix(".sqlite"), fingerprint)
            store.ok = False
            store.note = f"ChromaDB 不可用（{type(exc).__name__}: {exc}），已改用内置 SQLite 向量库。"
            return store

    return SQLiteVectorStore(path.with_suffix(".sqlite"), fingerprint)
