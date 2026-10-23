import numpy as np

from app.ingest import strip_page_numbers
from app.rag import _drop_unanswered, clean_history, looks_followup
from app.retrieval import (
    BM25, LexIndex, clean_query, split_question, find_passage, hybrid_rank, is_locate_question, is_question_list, locate_target,
)

QLIST = ("Câu hỏi ôn tập\n\n1. Vấn đề cơ bản của triết học là gì?\n\n2. Cơ sở để phân biệt chủ nghĩa duy vật và chủ nghĩa duy tâm?\n\n"
         "3. Vai trò của triết học trong đời sống xã hội?")
ANSWER = ("Vấn đề cơ bản của triết học là mối quan hệ giữa vật chất và ý thức. Vấn đề này có hai mặt: mặt thứ nhất trả lời "
          "câu hỏi cái nào có trước, mặt thứ hai trả lời con người có nhận thức được thế giới hay không.")
OTHER = "Triết học Ấn Độ cổ đại ra đời trong điều kiện xã hội phân chia đẳng cấp khắc nghiệt."


def test_question_list_detection():
    assert is_question_list(QLIST)
    assert not is_question_list(ANSWER)
    assert not is_question_list(OTHER)


def test_clean_query_removes_exam_noise():
    assert clean_query("Câu 3 (2 điểm): Trình bày vấn đề cơ bản của triết học (2 điểm)") == "Trình bày vấn đề cơ bản của triết học"
    assert clean_query("2. Vai trò của triết học?") == "Vai trò của triết học?"


def test_bm25_prefers_exact_terms():
    bm = BM25([OTHER, ANSWER, "Giá cà phê tăng mạnh."])
    s = bm.scores("vấn đề cơ bản của triết học")
    assert int(np.argmax(s)) == 1


def test_hybrid_rank_pushes_question_list_down():
    chunks = [{"text": t} for t in (QLIST, ANSWER, OTHER)]
    lex = LexIndex(chunks)
    # vector: câu hỏi copy từ sách giống đoạn câu hỏi ôn tập nhất (như thực tế)
    vecs = np.array([[1.0, 0.0], [0.9, 0.3], [0.0, 1.0]], dtype=np.float32)
    top, cos = hybrid_rank(vecs, np.array([1.0, 0.05]), lex, "Vấn đề cơ bản của triết học là gì?", 3)
    assert top.index(1) < top.index(0), "đoạn có lời giải phải đứng trước mục câu hỏi ôn tập"
    assert cos[0] > cos[1]  # điểm cosine hiển thị không bị thay đổi


def test_locate_question_detection_and_target():
    assert is_locate_question("Đoạn “vấn đề cơ bản của triết học” nằm ở trang nào?")
    assert is_locate_question("Phép biện chứng duy vật ở trang mấy?")
    assert is_locate_question("Which page talks about the inverter warranty?")
    assert not is_locate_question("Trình bày vấn đề cơ bản của triết học")
    assert locate_target("Đoạn “mối quan hệ giữa vật chất và ý thức” nằm ở trang nào?") == "mối quan hệ giữa vật chất và ý thức"
    assert locate_target("Tìm giúp tớ đoạn sau ở trang nào: Vấn đề này có hai mặt, mặt thứ nhất") == "Vấn đề này có hai mặt, mặt thứ nhất"


def test_find_passage_exact_and_fuzzy():
    lex = LexIndex([{"text": t} for t in (OTHER, ANSWER, QLIST)])
    assert find_passage(lex, "mối quan hệ giữa vật chất và ý thức") == (1, 1.0)
    # copy lệch dấu câu / xuống dòng / chữ hoa vẫn khớp
    i, score = find_passage(lex, "MẶT THỨ NHẤT trả lời câu hỏi, cái nào có trước; mặt thứ hai")
    assert i == 1 and score >= 0.5
    assert find_passage(lex, "hoàn toàn không có trong tài liệu này đâu nhé")[1] < 0.5


def test_followup_detection():
    assert looks_followup("còn mặt thứ hai thì sao?", "vi")
    assert looks_followup("cái thứ hai là gì?", "vi")
    assert looks_followup("giải thích thêm", "vi")
    assert not looks_followup("Trình bày quan điểm của chủ nghĩa duy vật biện chứng về vật chất", "vi")
    assert looks_followup("and what about the second one?", "en")
    assert not looks_followup("How long do string inverters usually last?", "en")


def test_unanswered_turns_are_dropped_from_history():
    hist = [
        {"role": "user", "content": "Câu A?"}, {"role": "assistant", "content": "Không tìm thấy trong tài liệu."},
        {"role": "user", "content": "Câu B?"}, {"role": "assistant", "content": "Trả lời B [1]."},
    ]
    assert _drop_unanswered(hist) == hist[2:]
    assert [m["content"] for m in clean_history(hist, 4)] == ["Câu B?", "Trả lời B."]


def test_strip_page_numbers():
    assert strip_page_numbers("11\nNội dung trang\nDòng hai\n12") == "Nội dung trang\nDòng hai"
    assert strip_page_numbers("- 5 -\nA\nTrang 6") == "A"
    assert strip_page_numbers("Năm 1848 là mốc quan trọng") == "Năm 1848 là mốc quan trọng"


def test_split_multi_part_textbook_question():
    q = "Vấn đề cơ bản của triết học. Cơ sở để phân biệt chủ nghĩa duy vật và chủ nghĩa duy tâm trong triết học?"
    assert split_question(q) == ["Vấn đề cơ bản của triết học",
                                 "Cơ sở để phân biệt chủ nghĩa duy vật và chủ nghĩa duy tâm trong triết học"]
    assert split_question("Vật chất là gì?") == ["Vật chất là gì?"]
    assert split_question("Trình bày khái niệm. Ví dụ?") == ["Trình bày khái niệm. Ví dụ?"]  # ý quá ngắn → không tách


def test_merge_adjacent_removes_overlap():
    from app.rag import merge_adjacent
    a = {"id": "a", "doc_id": "d", "idx": 4, "page": 5, "page_end": 5, "text": "Câu một rất dài để gối. Câu hai cũng khá dài để làm phần gối."}
    b = {"id": "b", "doc_id": "d", "idx": 5, "page": 5, "page_end": 6, "text": "…Câu hai cũng khá dài để làm phần gối. Câu ba mới."}
    c = {"id": "c", "doc_id": "d", "idx": 9, "page": 8, "page_end": 8, "text": "Đoạn xa."}
    out = merge_adjacent([(b, 0.7), (c, 0.5), (a, 0.6)])
    assert len(out) == 2
    m, score = out[0]
    assert m["text"] == "Câu một rất dài để gối. Câu hai cũng khá dài để làm phần gối. Câu ba mới."
    assert m["ids"] == ["a", "b"] and m["page"] == 5 and m["page_end"] == 6 and score == 0.7
    assert out[1][0]["id"] == "c"


def test_merge_with_previous_fallback():
    from app.rag import merge_with_previous
    out = merge_with_previous("còn ý nghĩa phương pháp luận của định nghĩa đó thì sao?", "Trình bày định nghĩa vật chất của Lênin", "vi")
    assert out == "Ý nghĩa phương pháp luận của định nghĩa đó (về: định nghĩa vật chất của Lênin)?"
