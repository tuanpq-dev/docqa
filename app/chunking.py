"""Chia văn bản thành các đoạn ~chunk_size ký tự, gối nhau ~overlap ký tự.

Thứ tự ưu tiên điểm cắt: đoạn văn → câu → từ → (bất đắc dĩ) ký tự.
Không bao giờ cắt giữa một ký tự gốc và dấu tổ hợp của nó (tiếng Việt).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .ingest import Segment

# Kết thúc câu: . ! ? … ; (kèm ngoặc/nháy đóng) rồi khoảng trắng
_SENT_END_RE = re.compile(r"(?<=[.!?…;:])[\"'”’)\]]*\s+")


@dataclass
class Unit:
    text: str
    page: int | None
    para_start: bool = False  # mở đầu một đoạn văn mới


@dataclass
class Chunk:
    text: str
    page: int | None
    page_end: int | None
    index: int = 0
    # [[vị trí ký tự, số trang], ...] tại những chỗ đổi trang bên trong đoạn → chỉ đúng trang khi tra vị trí
    page_marks: list = field(default_factory=list)


def _safe_cut(text: str, pos: int) -> int:
    """Lùi vị trí cắt để không tách dấu tổ hợp khỏi ký tự gốc."""
    pos = max(1, min(pos, len(text)))
    while pos < len(text) and pos > 1 and unicodedata.combining(text[pos]):
        pos -= 1
    return pos


def _split_words(text: str, limit: int) -> list[str]:
    """Cắt câu quá dài theo khoảng trắng; nếu một 'từ' vẫn quá dài thì cắt cứng."""
    out: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind(" ", 0, limit + 1)
        if cut < limit * 0.5:
            cut = _safe_cut(rest, limit)
            out.append(rest[:cut])
            rest = rest[cut:]
        else:
            out.append(rest[:cut])
            rest = rest[cut + 1 :]
    if rest.strip():
        out.append(rest)
    return [p.strip() for p in out if p.strip()]


def split_sentences(paragraph: str) -> list[str]:
    parts = _SENT_END_RE.split(paragraph)
    return [p.strip() for p in parts if p and p.strip()]


def _units_from_segments(segments: list[Segment], max_unit: int) -> list[Unit]:
    units: list[Unit] = []
    for seg in segments:
        # Đoạn văn: phân tách bởi dòng trống. Dòng đơn trong PDF thường chỉ là xuống dòng hiển thị.
        paragraphs = re.split(r"\n\s*\n", seg.text)
        for para in paragraphs:
            lines = [ln.strip() for ln in para.split("\n") if ln.strip()]
            if not lines:
                continue
            # Giữ xuống dòng cho gạch đầu dòng / bảng; nối các dòng còn lại bằng dấu cách
            if all(re.match(r"^([-*•–]|\d+[.)])\s", ln) or " | " in ln for ln in lines):
                sentences = lines
            else:
                sentences = split_sentences(" ".join(lines))
            first = True
            for s in sentences:
                pieces = [s] if len(s) <= max_unit else _split_words(s, max_unit)
                for piece in pieces:
                    units.append(Unit(text=piece, page=seg.page, para_start=first))
                    first = False
    return units


def _join(units: list[Unit]) -> str:
    out = ""
    for i, u in enumerate(units):
        if i == 0:
            out = u.text
        elif u.para_start:
            out += "\n\n" + u.text
        else:
            out += " " + u.text
    return out


def _page_marks(units: list[Unit]) -> list[list[int]]:
    marks: list[list[int]] = []
    pos, last = 0, None
    for i, u in enumerate(units):
        if i > 0:
            pos += 2 if u.para_start else 1
        if u.page is not None and u.page != last:
            marks.append([pos, u.page])
            last = u.page
        pos += len(u.text)
    return marks


def page_at(chunk: dict, pos: int) -> int | None:
    """Số trang tại vị trí ký tự `pos` trong đoạn."""
    page = chunk.get("page")
    for off, p in chunk.get("marks") or []:
        if off <= pos:
            page = p
    return page


def _overlap_tail(units: list[Unit], overlap: int) -> list[Unit]:
    """Lấy phần đuôi của đoạn trước (ưu tiên trọn câu) để gối sang đoạn sau."""
    if overlap <= 0 or not units:
        return []
    tail: list[Unit] = []
    total = 0
    for u in reversed(units):
        if total + len(u.text) + 1 > overlap * 1.3:
            break
        tail.insert(0, Unit(u.text, u.page, para_start=False))
        total += len(u.text) + 1
    if tail and total >= overlap * 0.4:
        return tail
    # Câu cuối dài hơn overlap: lấy ~overlap ký tự cuối, bắt đầu từ ranh giới từ
    last = units[-1].text
    start = len(last) - overlap
    if start <= 0:
        return [Unit(last, units[-1].page)]
    sp = last.find(" ", start)
    if sp == -1 or sp - start > overlap * 0.5:
        sp = _safe_cut(last, start)
        frag = last[sp:]
    else:
        frag = last[sp + 1 :]
    frag = frag.strip()
    return [Unit("…" + frag, units[-1].page)] if frag else []


def chunk_segments(segments: list[Segment], chunk_size: int = 700, overlap: int = 100) -> list[Chunk]:
    chunk_size = max(100, chunk_size)
    overlap = max(0, min(overlap, chunk_size // 2))
    max_unit = max(50, chunk_size - overlap)
    units = _units_from_segments(segments, max_unit)

    chunks: list[Chunk] = []
    fresh_texts: list[str] = []  # phần nội dung mới của mỗi đoạn (không gồm phần gối)
    current: list[Unit] = []
    cur_len = 0
    fresh = 0  # số unit mới (không phải phần gối) trong current

    def emit():
        nonlocal current, cur_len, fresh
        if fresh == 0:
            return
        text = _join(current).strip()
        if text:
            pages = [u.page for u in current if u.page is not None]
            chunks.append(
                Chunk(text=text, page=pages[0] if pages else None, page_end=pages[-1] if pages else None,
                      page_marks=_page_marks(current))
            )
            fresh_texts.append(_join(current[len(current) - fresh :]))
        current = _overlap_tail(current, overlap)
        cur_len = len(_join(current))
        fresh = 0

    for u in units:
        add = len(u.text) + (2 if u.para_start else 1)
        if current and fresh > 0 and cur_len + add > chunk_size:
            # Ưu tiên cắt ở ranh giới đoạn văn khi đoạn hiện tại đã đủ dài
            emit()
        elif current and fresh > 0 and u.para_start and cur_len >= chunk_size * 0.85:
            emit()
        current.append(u)
        cur_len += add
        fresh += 1
    emit()

    # Đoạn cuối quá ngắn: gộp vào đoạn trước nếu vẫn trong giới hạn
    if len(chunks) >= 2 and len(chunks[-1].text) < chunk_size * 0.25:
        tail = fresh_texts[-1]
        prev = chunks[-2]
        if len(prev.text) + len(tail) + 1 <= chunk_size * 1.3:
            last = chunks.pop()
            if last.page_end and last.page_end != prev.page_end:
                prev.page_marks.append([len(prev.text) + 1, last.page_end])
            prev.text = prev.text + " " + tail
            prev.page_end = last.page_end or prev.page_end

    for i, c in enumerate(chunks):
        c.index = i
    return chunks


def estimate_tokens(text: str) -> int:
    """Ước lượng thô số token (tokenizer kiểu BPE): ~1.4 token/từ tiếng Việt, ~1.3 tiếng Anh."""
    words = len(text.split())
    return int(max(words * 1.35, len(text) / 4))
