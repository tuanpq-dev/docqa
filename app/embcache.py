"""Bộ đệm embedding trên đĩa cho một lần nạp file đang dở.

Mỗi batch vừa tạo xong được ghi ngay thành một file nhỏ trong data/<session>/embcache/.
Nếu nạp thất bại giữa chừng (Ollama quá chậm, mất kết nối, đóng tab…), nạp lại cùng file
sẽ dùng lại các vector đã có và chỉ tạo phần còn thiếu. Nạp thành công thì xóa bộ đệm.
"""
from __future__ import annotations

import hashlib
import shutil
import uuid
from pathlib import Path

import numpy as np


def text_key(model: str, text: str) -> str:
    return hashlib.sha1((model + "\\0" + text).encode("utf-8")).hexdigest()


class EmbedCache:
    def __init__(self, session_dir: Path, model: str):
        self.dir = session_dir / "embcache"
        self.model = model
        self.vecs: dict[str, np.ndarray] = {}
        self._load()

    def _load(self):
        if not self.dir.is_dir():
            return
        for f in sorted(self.dir.glob("*.npz")):
            try:
                with np.load(f, allow_pickle=False) as z:
                    if str(z["model"]) != self.model:
                        continue
                    for k, v in zip(z["keys"].tolist(), z["vecs"]):
                        self.vecs[k] = v
            except Exception:  # noqa: BLE001 – file dở dang thì bỏ qua
                continue

    def key(self, text: str) -> str:
        return text_key(self.model, text)

    def get(self, text: str) -> np.ndarray | None:
        return self.vecs.get(self.key(text))

    def add(self, texts: list[str], vecs: np.ndarray):
        keys = [self.key(t) for t in texts]
        for k, v in zip(keys, vecs):
            self.vecs[k] = v
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / f".{uuid.uuid4().hex}.tmp.npz"
        np.savez(tmp, keys=np.array(keys), vecs=np.asarray(vecs, dtype=np.float32), model=np.array(self.model))
        tmp.rename(self.dir / (uuid.uuid4().hex + ".npz"))

    def clear(self):
        shutil.rmtree(self.dir, ignore_errors=True)
        self.vecs.clear()
