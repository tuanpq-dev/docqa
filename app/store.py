"""Kho vector numpy thuần cho một phiên: lưu đoạn + vector, tìm top-k theo cosine."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np


def top_k_cosine(matrix: np.ndarray, query: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
    """Trả về (chỉ số, điểm) của k hàng gần query nhất theo cosine, sắp giảm dần."""
    if matrix.size == 0 or k <= 0:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=np.float32)
    q = np.asarray(query, dtype=np.float32).reshape(-1)
    qn = np.linalg.norm(q)
    if qn == 0:
        return np.zeros(0, dtype=int), np.zeros(0, dtype=np.float32)
    m_norm = np.linalg.norm(matrix, axis=1)
    m_norm[m_norm == 0] = 1.0
    scores = (matrix @ q) / (m_norm * qn)
    k = min(k, len(scores))
    idx = np.argpartition(-scores, k - 1)[:k]
    idx = idx[np.argsort(-scores[idx], kind="stable")]
    return idx, scores[idx].astype(np.float32)


@dataclass
class DocInfo:
    id: str
    name: str
    kind: str
    pages: int | None
    size: int
    n_chunks: int
    chars: int
    tokens: int
    added_at: float = field(default_factory=time.time)


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class SessionStore:
    """Dữ liệu của một phiên trong data/<session_id>/."""

    def __init__(self, path: Path):
        self.path = path
        self.lock = threading.RLock()
        self.docs: list[DocInfo] = []
        self.chunks: list[dict] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.suggestions: dict = {"status": "none", "items": []}
        self._pca_cache = None
        self._dup_cache = None
        self._lex_cache = None
        self._load()

    # ---------- lưu / đọc ----------
    def _load(self):
        meta = self.path / "meta.json"
        if not meta.is_file():
            return
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            self.docs = [DocInfo(**d) for d in m.get("docs", [])]
            self.suggestions = m.get("suggestions", self.suggestions)
            self.chunks = json.loads((self.path / "chunks.json").read_text(encoding="utf-8"))
            vec_path = self.path / "vectors.npy"
            self.vectors = np.load(vec_path) if vec_path.is_file() else np.zeros((0, 0), np.float32)
            if len(self.chunks) != len(self.vectors):
                raise ValueError("chunks/vectors mismatch")
        except Exception:  # noqa: BLE001 – dữ liệu hỏng thì coi như phiên trống
            self.docs, self.chunks = [], []
            self.vectors = np.zeros((0, 0), dtype=np.float32)
        if self.suggestions.get("status") == "pending":
            self.suggestions = {"status": "failed", "items": []}

    def save(self):
        with self.lock:
            self.path.mkdir(parents=True, exist_ok=True)
            np.save(self.path / "vectors.tmp.npy", self.vectors)
            os.replace(self.path / "vectors.tmp.npy", self.path / "vectors.npy")
            _atomic_write(self.path / "chunks.json", json.dumps(self.chunks, ensure_ascii=False).encode("utf-8"))
            meta = {"docs": [asdict(d) for d in self.docs], "suggestions": self.suggestions}
            _atomic_write(self.path / "meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))

    def save_meta(self):
        with self.lock:
            if not self.path.is_dir():
                return
            meta = {"docs": [asdict(d) for d in self.docs], "suggestions": self.suggestions}
            _atomic_write(self.path / "meta.json", json.dumps(meta, ensure_ascii=False).encode("utf-8"))

    # ---------- thay đổi ----------
    def _invalidate(self):
        self._pca_cache = None
        self._dup_cache = None
        self._lex_cache = None

    def add_document(self, info: DocInfo, chunk_texts: list[dict], vectors: np.ndarray):
        """chunk_texts: [{text, page, page_end}] theo thứ tự trong tài liệu."""
        with self.lock:
            n = len(chunk_texts)
            new = []
            for i, c in enumerate(chunk_texts):
                new.append({
                    "id": uuid.uuid4().hex[:12],
                    "doc_id": info.id,
                    "doc_name": info.name,
                    "idx": i,
                    "pos": (i / (n - 1)) if n > 1 else 0.0,
                    "page": c.get("page"),
                    "page_end": c.get("page_end"),
                    "marks": c.get("marks") or [],
                    "text": c["text"],
                })
            vectors = np.asarray(vectors, dtype=np.float32)
            if self.vectors.size == 0:
                self.vectors = vectors
            else:
                if vectors.shape[1] != self.vectors.shape[1]:
                    raise ValueError("embedding dimension changed")
                self.vectors = np.vstack([self.vectors, vectors])
            self.chunks.extend(new)
            self.docs.append(info)
            self._invalidate()
            self.save()

    def remove_document(self, doc_id: str) -> bool:
        with self.lock:
            if not any(d.id == doc_id for d in self.docs):
                return False
            keep = [i for i, c in enumerate(self.chunks) if c["doc_id"] != doc_id]
            self.chunks = [self.chunks[i] for i in keep]
            self.vectors = self.vectors[keep] if keep else np.zeros((0, 0), dtype=np.float32)
            self.docs = [d for d in self.docs if d.id != doc_id]
            self.suggestions = {"status": "none", "items": []}
            self._invalidate()
            self.save()
            return True

    # ---------- truy vấn ----------
    @property
    def dim(self) -> int:
        return int(self.vectors.shape[1]) if self.vectors.ndim == 2 and self.vectors.size else 0

    def search(self, query_vec: np.ndarray, k: int) -> list[tuple[dict, float]]:
        with self.lock:
            idx, scores = top_k_cosine(self.vectors, query_vec, k)
            return [(self.chunks[int(i)], float(s)) for i, s in zip(idx, scores)]

    def lex(self):
        from .retrieval import LexIndex

        with self.lock:
            if self._lex_cache is None:
                self._lex_cache = LexIndex(self.chunks)
            return self._lex_cache

    def hybrid_search(self, query_vec: np.ndarray, query_text: str, k: int, neighbors: int = 1) -> list[tuple[dict, float]]:
        """Top-k theo điểm trộn vector + từ khóa, kèm `neighbors` đoạn liền sau đoạn khớp nhất
        (lời giải trong giáo trình thường nằm ngay sau tiêu đề khớp với câu hỏi). Điểm trả về là cosine thật."""
        from .retrieval import hybrid_rank

        with self.lock:
            if not self.chunks:
                return []
            top, cos = hybrid_rank(self.vectors, query_vec, self.lex(), query_text, k)
            picked = list(top)
            if top and neighbors > 0:
                best = self.chunks[top[0]]
                for step in range(1, neighbors + 1):
                    j = top[0] + step
                    if j < len(self.chunks) and j not in picked and self.chunks[j]["doc_id"] == best["doc_id"]:
                        picked.insert(step, j)  # đặt ngay sau đoạn khớp nhất
            return [(self.chunks[i], float(cos[i])) for i in picked]

    def chunk_by_id(self, chunk_id: str) -> dict | None:
        with self.lock:
            return next((c for c in self.chunks if c["id"] == chunk_id), None)

    def stats(self) -> dict:
        from .chunking import estimate_tokens

        with self.lock:
            return {
                "n_docs": len(self.docs),
                "n_chunks": len(self.chunks),
                "dim": self.dim,
                "tokens": sum(d.tokens for d in self.docs) if self.docs else 0,
                "chars": sum(d.chars for d in self.docs),
                "avg_chunk_tokens": int(np.mean([estimate_tokens(c["text"]) for c in self.chunks])) if self.chunks else 0,
            }
