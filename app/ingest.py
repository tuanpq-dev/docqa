"""Đọc file PDF / DOCX / TXT / MD thành danh sách đoạn văn bản kèm số trang."""
from __future__ import annotations

import io
import logging
import re
import unicodedata
import zipfile
from dataclasses import dataclass

log = logging.getLogger("docqa.ingest")
logging.getLogger("pypdf").setLevel(logging.ERROR)  # pypdf hay cảnh báo vụn vặt, không cần in ra

SUPPORTED_EXT = {".pdf", ".docx", ".txt", ".md", ".markdown"}


class IngestError(Exception):
    """Lỗi nạp file, có mã để giao diện hiển thị và thông điệp tiếng Việt."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


@dataclass
class Segment:
    text: str
    page: int | None  # số trang (PDF, bắt đầu từ 1); None với DOCX/TXT


@dataclass
class ParsedDoc:
    kind: str  # pdf | docx | txt
    segments: list[Segment]
    pages: int | None


_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​﻿]")
_SPACES_RE = re.compile(r"[ \t  -  　]+")


def normalize_text(text: str) -> str:
    """Chuẩn hóa Unicode NFC (quan trọng với tiếng Việt: dấu tổ hợp -> ký tự dựng sẵn)."""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_RE.sub("", text)
    text = _SPACES_RE.sub(" ", text)
    # Nối từ bị gạch nối cuối dòng (thường gặp trong PDF tiếng Anh): "infor-\nmation"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    lines = [ln.strip() for ln in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def safe_filename(name: str) -> str:
    """Tên file chỉ dùng để hiển thị: bỏ đường dẫn, ký tự điều khiển, giới hạn độ dài."""
    name = (name or "").replace("\\", "/").split("/")[-1]
    name = unicodedata.normalize("NFC", name)
    name = _CTRL_RE.sub("", name).strip().strip(".") or "tai-lieu"
    name = re.sub(r'[<>:"|?*]', "_", name)
    if len(name) > 120:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:100] + "…." + ext) if dot and len(ext) <= 8 else name[:120]
    return name


def detect_kind(filename: str, data: bytes) -> str:
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if data[:5] == b"%PDF-" or (ext == ".pdf" and b"%PDF-" in data[:1024]):
        return "pdf"
    if ext == ".docx" or (data[:2] == b"PK" and ext not in (".txt", ".md", ".markdown")):
        return "docx"
    if ext in (".txt", ".md", ".markdown"):
        return "txt"
    if ext == ".doc":
        raise IngestError(
            "unsupported",
            "File .doc (Word đời cũ) chưa được hỗ trợ.",
            "Mở file trong Word/LibreOffice và lưu lại dưới dạng .docx hoặc .pdf.",
        )
    raise IngestError(
        "unsupported",
        f"Định dạng {ext or 'không rõ'} chưa được hỗ trợ.",
        "Hãy dùng PDF, Word (.docx), .txt hoặc .md.",
    )


def _corrupt(kind: str) -> IngestError:
    return IngestError(
        "corrupt",
        f"Không đọc được file {kind.upper()} – file có thể bị hỏng hoặc không đúng định dạng.",
        "Thử mở file bằng ứng dụng khác rồi lưu/xuất lại.",
    )


def pdf_paragraphs(text: str) -> str:
    """PDF thường chỉ có xuống dòng đơn. Đoán ranh giới đoạn văn theo độ dài dòng:
    dòng ngắn kết thúc bằng dấu câu (cuối đoạn) hoặc dòng ngắn không dấu câu (tiêu đề)."""
    lines = text.split("\n")
    lengths = sorted(len(ln) for ln in lines if ln.strip())
    if len(lengths) < 3:
        return text
    full = lengths[int(len(lengths) * 0.8)]  # độ dài "dòng đầy" điển hình
    out = []
    for i, ln in enumerate(lines):
        out.append(ln)
        if i == len(lines) - 1 or not ln.strip() or not lines[i + 1].strip():
            continue
        short = len(ln) < full * 0.75
        ends_sentence = ln.rstrip().endswith((".", "!", "?", ":", "…", "\"", "”"))
        if short and (ends_sentence or len(ln) < full * 0.6):
            out.append("")
    return "\n".join(out)


_PAGE_NO_RE = re.compile(r"^\s*(?:trang|page)?\s*[-–]?\s*\d{1,4}\s*[-–]?\s*$", re.I)


def strip_page_numbers(text: str) -> str:
    """Bỏ số trang in ở đầu/cuối trang (dòng chỉ có '12', '- 12 -', 'Trang 12')."""
    lines = text.split("\n")
    while lines and (not lines[0].strip() or _PAGE_NO_RE.match(lines[0])):
        lines.pop(0)
    while lines and (not lines[-1].strip() or _PAGE_NO_RE.match(lines[-1])):
        lines.pop()
    return "\n".join(lines)


_ENCRYPTED = IngestError("encrypted", "PDF được bảo vệ bằng mật khẩu.", "Hãy bỏ mật khẩu (in ra PDF mới) rồi tải lên lại.")


def _pdf_pages_pymupdf(data: bytes) -> list[str]:
    """PyMuPDF: trích chữ tiếng Việt đúng hơn hẳn pypdf (pypdf hay chèn dấu cách giữa âm tiết: 'tri ết h ọc')."""
    import pymupdf

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        log.info("pdf open failed: %s", type(e).__name__)
        raise _corrupt("pdf") from e
    with doc:
        if doc.needs_pass and not doc.authenticate(""):
            raise _ENCRYPTED
        pages = []
        for i, page in enumerate(doc, start=1):
            try:
                pages.append(page.get_text("text") or "")
            except Exception as e:  # noqa: BLE001 – một trang lỗi thì bỏ qua trang đó
                log.info("pdf page %d extract failed: %s", i, type(e).__name__)
                pages.append("")
        return pages


def _pdf_pages_pypdf(data: bytes) -> list[str]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            try:
                ok = reader.decrypt("")
            except Exception:  # noqa: BLE001
                ok = 0
            if not ok:
                raise _ENCRYPTED
        pages = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                pages.append(page.extract_text() or "")
            except Exception as e:  # noqa: BLE001
                log.info("pdf page %d extract failed: %s", i, type(e).__name__)
                pages.append("")
        return pages
    except IngestError:
        raise
    except Exception as e:  # noqa: BLE001 – pypdf ném nhiều loại lỗi
        log.info("pdf parse failed: %s", type(e).__name__)
        raise _corrupt("pdf") from e


def parse_pdf(data: bytes) -> ParsedDoc:
    try:
        raw_pages = _pdf_pages_pymupdf(data)
    except ImportError:  # thiếu pymupdf → dùng pypdf (kém hơn với tiếng Việt)
        log.warning("pymupdf not installed – falling back to pypdf")
        raw_pages = _pdf_pages_pypdf(data)

    n_pages = len(raw_pages)
    if n_pages == 0:
        raise _corrupt("pdf")
    segments: list[Segment] = []
    total_chars = 0
    for i, raw in enumerate(raw_pages, start=1):
        text = pdf_paragraphs(strip_page_numbers(normalize_text(raw)))
        if text:
            segments.append(Segment(text=text, page=i))
            total_chars += len(text)

    # PDF scan: gần như không có lớp chữ
    if total_chars < max(40, 15 * n_pages) and total_chars < 2000:
        raise IngestError(
            "scanned",
            "PDF này có vẻ là bản scan (ảnh chụp), không chứa lớp chữ. docqa không hỗ trợ OCR.",
            "Hãy chạy OCR trước (ví dụ: ocrmypdf, Adobe Acrobat, hoặc Google Drive → Mở bằng Google Docs) rồi tải lên bản có chữ.",
        )
    return ParsedDoc(kind="pdf", segments=segments, pages=n_pages)


def parse_docx(data: bytes) -> ParsedDoc:
    import docx  # python-docx

    try:
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise _corrupt("docx")
        document = docx.Document(io.BytesIO(data))
    except IngestError:
        raise
    except Exception as e:  # noqa: BLE001
        log.info("docx parse failed: %s", type(e).__name__)
        raise _corrupt("docx") from e

    parts: list[str] = []
    # Duyệt theo thứ tự xuất hiện: đoạn văn và bảng
    body = document.element.body
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, document)
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name or "").lower() if p.style is not None else ""
            if style.startswith("heading") or style.startswith("title"):
                parts.append("\n" + t)  # tiêu đề mở đoạn mới
            elif "list" in style:
                parts.append("- " + t)
            else:
                parts.append(t)
        elif tag == "tbl":
            table = Table(child, document)
            rows = []
            for row in table.rows:
                cells = []
                for c in row.cells:
                    ct = " ".join(c.text.split())
                    if not cells or cells[-1] != ct:  # ô gộp lặp lại
                        cells.append(ct)
                rows.append(" | ".join(cells))
            if rows:
                parts.append("\n".join(rows))

    text = normalize_text("\n\n".join(parts))
    if not text:
        raise IngestError("empty", "File Word không có nội dung chữ.", "Kiểm tra lại file hoặc thử xuất ra PDF.")
    return ParsedDoc(kind="docx", segments=[Segment(text=text, page=None)], pages=None)


def parse_text(data: bytes) -> ParsedDoc:
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1258", "cp1252"):
        try:
            if enc == "utf-16" and not data[:2] in (b"\xff\xfe", b"\xfe\xff"):
                continue
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    text = normalize_text(text)
    if not text:
        raise IngestError("empty", "File văn bản rỗng.", "")
    return ParsedDoc(kind="txt", segments=[Segment(text=text, page=None)], pages=None)


def parse_file(filename: str, data: bytes, max_bytes: int) -> ParsedDoc:
    if len(data) > max_bytes:
        raise IngestError(
            "too_large",
            f"File quá lớn ({len(data) / 1024 / 1024:.1f} MB). Giới hạn là {max_bytes // (1024 * 1024)} MB.",
            "Hãy tách file thành các phần nhỏ hơn.",
        )
    if not data:
        raise IngestError("empty", "File rỗng.", "")
    kind = detect_kind(filename, data)
    if kind == "pdf":
        return parse_pdf(data)
    if kind == "docx":
        return parse_docx(data)
    return parse_text(data)
