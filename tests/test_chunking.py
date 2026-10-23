import unicodedata

from app.chunking import chunk_segments, estimate_tokens, split_sentences
from app.ingest import Segment, normalize_text

VI = (
    "Cà phê vối là cây công nghiệp chủ lực của Tây Nguyên. Cây ưa khí hậu nóng ẩm, nhiệt độ từ 22 đến 26 độ C. "
    "Đất đỏ bazan là loại đất phù hợp nhất! Mật độ trồng khuyến nghị là 1.100 cây trên một héc-ta? "
)


def test_vietnamese_chunks_keep_diacritics_and_sizes():
    text = "\n\n".join([VI * 3] * 6)
    chunks = chunk_segments([Segment(text, 1)], chunk_size=700, overlap=100)
    assert len(chunks) > 3
    for c in chunks:
        assert len(c.text) <= 700 * 1.3
        assert unicodedata.is_normalized("NFC", c.text)
        # không có dấu tổ hợp đứng đầu đoạn (dấu bị tách khỏi chữ)
        assert not unicodedata.combining(c.text.lstrip("…")[0])
    joined = " ".join(c.text for c in chunks)
    assert "héc-ta" in joined and "Tây Nguyên" in joined


def test_overlap_between_consecutive_chunks():
    text = " ".join(f"Câu số {i} nói về nội dung thứ {i} của tài liệu." for i in range(80))
    chunks = chunk_segments([Segment(text, None)], chunk_size=600, overlap=100)
    assert len(chunks) >= 4
    for a, b in zip(chunks, chunks[1:]):
        head = b.text.lstrip("…")[:40]
        assert head[:25] in a.text, "đoạn sau phải bắt đầu bằng phần đuôi của đoạn trước"


def test_short_text_single_chunk():
    chunks = chunk_segments([Segment("Xin chào. Đây là tài liệu ngắn.", 1)], 700, 100)
    assert len(chunks) == 1
    assert chunks[0].text == "Xin chào. Đây là tài liệu ngắn."
    assert chunks[0].page == 1


def test_empty_input():
    assert chunk_segments([], 700, 100) == []
    assert chunk_segments([Segment("   ", 1)], 700, 100) == []


def test_very_long_sentence_and_word_are_split():
    long_sentence = " ".join(["từ"] * 1200)  # một câu ~3600 ký tự, không dấu chấm
    long_word = "a" * 2000
    chunks = chunk_segments([Segment(long_sentence + "\n\n" + long_word, 2)], 700, 100)
    assert len(chunks) >= 6
    assert all(len(c.text) <= 700 * 1.3 for c in chunks)
    assert all(c.page == 2 for c in chunks)


def test_decomposed_vietnamese_is_normalized_and_never_split_from_base():
    nfd = unicodedata.normalize("NFD", "Tiếng Việt có dấu " * 200)
    text = normalize_text(nfd)
    assert unicodedata.is_normalized("NFC", text)
    for c in chunk_segments([Segment(text.replace(" ", ""), 1)], 300, 50):  # buộc cắt cứng
        assert not unicodedata.combining(c.text.lstrip("…")[0])


def test_pages_tracked_across_segments():
    segs = [Segment(VI * 2, 1), Segment(VI * 2, 2), Segment(VI * 2, 3)]
    chunks = chunk_segments(segs, 700, 100)
    assert chunks[0].page == 1
    assert chunks[-1].page_end == 3
    assert all(c.page <= c.page_end for c in chunks)


def test_split_sentences():
    assert split_sentences("Một. Hai! Ba? Bốn") == ["Một.", "Hai!", "Ba?", "Bốn"]


def test_estimate_tokens():
    assert estimate_tokens("") == 0
    assert 10 < estimate_tokens(VI) < 120


def test_page_marks_locate_exact_page_inside_chunk():
    from app.chunking import page_at
    segs = [Segment("Câu ở trang một. " * 10, 1), Segment("Câu ở trang hai rất đặc biệt.", 2)]
    c = chunk_segments(segs, 700, 100)[0]
    assert c.page == 1 and c.page_end == 2
    d = {"page": c.page, "marks": c.page_marks}
    assert page_at(d, 0) == 1
    assert page_at(d, c.text.index("rất đặc biệt")) == 2
