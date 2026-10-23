"""Phân tích vector: PCA 2D bằng numpy SVD và tìm đoạn gần trùng lặp."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class PCA2D:
    mean: np.ndarray        # (d,)
    components: np.ndarray  # (2, d)
    coords: np.ndarray      # (n, 2)
    explained: list[float]  # tỉ lệ phương sai giữ lại của 2 trục

    def project(self, vecs: np.ndarray) -> np.ndarray:
        v = np.atleast_2d(np.asarray(vecs, dtype=np.float32))
        return (v - self.mean) @ self.components.T


def fit_pca(x: np.ndarray, n_components: int = 2) -> PCA2D:
    x = np.asarray(x, dtype=np.float32)
    n = x.shape[0]
    d = x.shape[1] if x.ndim == 2 else 0
    if n == 0 or d == 0:
        return PCA2D(np.zeros(max(d, 1), np.float32), np.zeros((2, max(d, 1)), np.float32), np.zeros((0, 2), np.float32), [0.0, 0.0])
    mean = x.mean(axis=0)
    xc = x - mean
    if n == 1:
        comps = np.zeros((2, d), dtype=np.float32)
        return PCA2D(mean, comps, np.zeros((1, 2), np.float32), [0.0, 0.0])
    # SVD trên dữ liệu đã trừ trung bình: hàng của Vt là các trục chính
    _, s, vt = np.linalg.svd(xc, full_matrices=False)
    comps = np.zeros((2, d), dtype=np.float32)
    k = min(n_components, vt.shape[0])
    comps[:k] = vt[:k]
    # Cố định dấu để bản đồ không lật ngẫu nhiên giữa các lần tính
    for i in range(k):
        j = int(np.argmax(np.abs(comps[i])))
        if comps[i, j] < 0:
            comps[i] = -comps[i]
    var = s**2
    total = float(var.sum()) or 1.0
    explained = [float(var[i] / total) if i < len(var) else 0.0 for i in range(2)]
    coords = xc @ comps.T
    return PCA2D(mean, comps, coords.astype(np.float32), explained)


def near_duplicates(x: np.ndarray, threshold: float = 0.95, block: int = 512, limit: int = 200) -> list[tuple[int, int, float]]:
    """Các cặp (i, j, cosine) với i < j và cosine >= threshold. Tính theo khối để tiết kiệm RAM."""
    x = np.asarray(x, dtype=np.float32)
    n = x.shape[0]
    if n < 2:
        return []
    norms = np.linalg.norm(x, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    xn = x / norms
    pairs: list[tuple[int, int, float]] = []
    for start in range(0, n, block):
        sims = xn[start : start + block] @ xn.T  # (b, n)
        rows, cols = np.nonzero(sims >= threshold)
        for r, c in zip(rows, cols):
            i = start + int(r)
            if c > i:
                pairs.append((i, int(c), float(sims[r, c])))
    pairs.sort(key=lambda t: -t[2])
    return pairs[:limit]
