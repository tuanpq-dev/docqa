"""Truy xuất kết hợp: vector (cosine) + từ khóa (BM25), có xử lý riêng cho giáo trình.

Vì sao không chỉ dùng vector:
- Câu hỏi giáo trình thường chứa thuật ngữ chính xác ("phép biện chứng duy vật") → BM25 bắt rất tốt.
- Copy nguyên câu hỏi trong sách thì đoạn khớp nhất lại là mục "Câu hỏi ôn tập" (chứa đúng câu đó)
  chứ không phải đoạn có lời giải → hạ điểm các đoạn chỉ toàn câu hỏi.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

import numpy as np

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_QLIST_HEAD_RE = re.compile(
    r"(câu hỏi (ôn tập|thảo luận|củng cố|tự luận|trắc nghiệm)|bài tập|review questions|exercises|discussion questions)",
    re.I,
)
# Rác thường gặp khi copy câu hỏi từ đề/giáo trình: "Câu 3 (2 điểm):", "1.", "(...)"
_EXAM_PREFIX_RE = re.compile(
    r"^\s*(câu( hỏi)?|question|bài)\s*\d+\s*(\(\s*\d+([.,]\d+)?\s*(điểm|đ|points?|pts?)\s*\))?\s*[.:)\-–]?\s*", re.I)
_EXAM_SUFFIX_RE = re.compile(r"\(\s*\d+([.,]\d+)?\s*(điểm|đ|points?|pts?)\s*\)\s*$", re.I)
_NUM_PREFIX_RE = re.compile(r"^\s*\d{1,3}\s*[.)]\s+")


def normalize(text: str) -> str:
    return unicodedata.normalize("NFC", text).lower()


def tokens(text: str) -> list[str]:
    """Âm tiết + cặp âm tiết liền nhau (từ ghép tiếng Việt thường là 2 âm tiết)."""
    words = _WORD_RE.findall(normalize(text))
    return words + [a + "_" + b for a, b in zip(words, words[1:])]


def clean_query(q: str) -> str:
    q = _EXAM_PREFIX_RE.sub("", q.strip())
    q = _NUM_PREFIX_RE.sub("", q)
    q = _EXAM_SUFFIX_RE.sub("", q)
    return q.strip() or q


def is_question_list(text: str) -> bool:
    """Đoạn chủ yếu là danh sách câu hỏi (mục Câu hỏi ôn tập/Bài tập), không chứa lời giải."""
    if _QLIST_HEAD_RE.search(text):
        return True
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 8]
    qs = sum(s.strip().endswith("?") for s in sentences)
    return qs >= 3 and qs / max(len(sentences), 1) >= 0.5


class BM25:
    def __init__(self, texts: list[str], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(tokens(t)) for t in texts]
        self.len = np.array([sum(c.values()) for c in self.tf], dtype=np.float32)
        self.avg = float(self.len.mean()) if len(self.len) else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(texts)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> np.ndarray:
        q = set(tokens(query))
        out = np.zeros(len(self.tf), dtype=np.float32)
        if not q:
            return out
        norm = self.k1 * (1 - self.b + self.b * self.len / max(self.avg, 1e-6))
        for i, tf in enumerate(self.tf):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm[i])
            out[i] = s
        return out


class LexIndex:
    """Chỉ mục từ khóa + cờ 'danh sách câu hỏi' cho mọi đoạn của một phiên (dựng lười, có cache)."""

    def __init__(self, chunks: list[dict]):
        texts = [c["text"] for c in chunks]
        self.bm25 = BM25(texts)
        self.qlist = np.array([is_question_list(t) for t in texts], dtype=bool)
        self.norm_texts = [_squash(t) for t in texts]


def _squash(text: str) -> str:
    return " ".join(_WORD_RE.findall(normalize(text)))


def hybrid_rank(
    vectors: np.ndarray,
    qvec: np.ndarray,
    lex: LexIndex,
    query: str,
    k: int,
    rrf_k: int = 60,
    qlist_penalty: float = 0.5,
) -> tuple[list[int], np.ndarray]:
    """Trộn thứ hạng vector và BM25 bằng Reciprocal Rank Fusion.
    Trả về (chỉ số top-k theo điểm trộn, cosine của MỌI đoạn) – cosine dùng để hiển thị và xét ngưỡng."""
    n = len(vectors)
    if n == 0:
        return [], np.zeros(0, dtype=np.float32)
    q = np.asarray(qvec, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1) * (np.linalg.norm(q) or 1.0)
    norms[norms == 0] = 1.0
    cos = (vectors @ q) / norms
    bm = lex.bm25.scores(query)

    dense_rank = np.empty(n, dtype=np.int64)
    dense_rank[np.argsort(-cos, kind="stable")] = np.arange(n)
    fused = 1.0 / (rrf_k + dense_rank)
    if bm.max() > 0:
        lex_rank = np.empty(n, dtype=np.int64)
        lex_rank[np.argsort(-bm, kind="stable")] = np.arange(n)
        fused = fused + np.where(bm > 0, 1.0 / (rrf_k + lex_rank), 0.0)
    fused = np.where(lex.qlist, fused * qlist_penalty, fused)
    top = np.argsort(-fused, kind="stable")[: min(k, n)]
    return [int(i) for i in top], cos.astype(np.float32)


# ---------------------------------------------------------------- "đoạn này ở trang nào?"
_LOCATE_RE = re.compile(
    r"(trang\s*(nào|mấy|bao nhiêu|số mấy)|(ở|nằm ở|thuộc|nằm trong)\s*(trang|chương|mục|phần)\s*(nào|mấy)?"
    r"|nằm ở đâu|ở đâu trong (tài liệu|giáo trình|sách|file)|tìm (đoạn|câu|chỗ|vị trí)"
    r"|which page|what page|on what page|where (in the|is this in the) (document|book|text))",
    re.I,
)
_QUOTE_RE = re.compile(r"[“\"«'‘]([^”\"»'’]{12,})[”\"»'’]")
_LOCATE_STRIP_RE = re.compile(
    r"(cho (tôi|mình|tớ|em) (biết|hỏi)|hãy|vui lòng|làm ơn|giúp (tôi|mình|tớ|em)?|tìm (giúp )?|xem )"
    r"|(đoạn văn|đoạn|câu|nội dung|phần|ý)\s*(này|sau|dưới đây|trên)?\s*[:：]?"
    r"|(nằm |thuộc |có |được nhắc |được viết |xuất hiện )?(ở|trong)?\s*(trang|chương|mục)\s*(nào|mấy|bao nhiêu|số mấy)"
    r"|nằm ở đâu|ở đâu|\?|which page|what page|is (this|it) on|on what page",
    re.I,
)


def is_locate_question(q: str) -> bool:
    return bool(_LOCATE_RE.search(q))


def locate_target(q: str) -> str:
    """Lấy phần văn bản cần tìm vị trí: trong ngoặc kép, sau dấu ':', hoặc phần còn lại sau khi bỏ cụm hỏi."""
    m = _QUOTE_RE.search(q)
    if m:
        return m.group(1).strip()
    if ":" in q:
        before, after = q.split(":", 1)
        if len(after.strip()) >= 12:
            return after.strip()
    return _LOCATE_STRIP_RE.sub(" ", q).strip(" .,:;-–")


def find_passage(lex: LexIndex, target: str) -> tuple[int, float]:
    """Tìm đoạn chứa văn bản `target` (khớp nguyên văn hoặc gần đúng theo cụm 3 từ).
    Trả về (chỉ số đoạn, độ khớp 0..1); (-1, 0) nếu không tìm được."""
    t = _squash(target)
    words = t.split()
    if len(words) < 3:
        return -1, 0.0
    exact = [i for i, txt in enumerate(lex.norm_texts) if t in txt]
    if exact:  # ưu tiên đoạn nội dung hơn mục "Câu hỏi ôn tập" chứa đúng câu đó
        return next((i for i in exact if not lex.qlist[i]), exact[0]), 1.0
    grams = {" ".join(words[j : j + 3]) for j in range(len(words) - 2)}
    best, best_i = 0.0, -1
    for i, txt in enumerate(lex.norm_texts):
        hit = sum(1 for g in grams if g in txt)
        if hit:
            score = hit / len(grams) * (0.8 if lex.qlist[i] else 1.0)
            if score > best:
                best, best_i = score, i
    return best_i, best


def passage_offset(text: str, target: str) -> int:
    """Vị trí (gần đúng) bắt đầu của `target` trong `text` gốc, chịu lệch dấu câu/khoảng trắng/chữ hoa."""
    words = _WORD_RE.findall(normalize(target))
    src = normalize(text)
    for n in (5, 4, 3):
        for start in range(0, max(len(words) - n + 1, 1)):
            part = words[start : start + n]
            if len(part) < 3:
                break
            m = re.search(r"\W+".join(map(re.escape, part)), src)
            if m:
                return m.start()
    return 0


def split_question(q: str, max_parts: int = 3) -> list[str]:
    """Câu hỏi giáo trình hay gộp nhiều ý: "Vấn đề cơ bản của triết học. Cơ sở để phân biệt …?"
    → tách thành từng ý (mỗi ý ≥ 3 từ) để tìm riêng rồi gộp kết quả."""
    parts = [p.strip(" .;:?!") for p in re.split(r"(?<=[.?!;])\s+", q.strip())]
    parts = [p for p in parts if len(p.split()) >= 3]
    return parts[:max_parts] if len(parts) >= 2 else [q]
