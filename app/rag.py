"""Luồng RAG: viết lại câu hỏi nối tiếp → truy xuất top-k → prompt → stream câu trả lời."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator

import numpy as np

from .config import Settings
from .embeddings import OllamaClient, OllamaError
from .store import SessionStore

log = logging.getLogger("docqa.rag")

_VI_CHARS = re.compile(
    r"[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]", re.IGNORECASE
)
_VI_PLAIN_WORDS = {
    "la", "cua", "gi", "nao", "khong", "nhung", "duoc", "trong", "nhu", "the", "bao", "nhieu",
    "tai", "lieu", "cho", "toi", "hoi", "ve", "nay", "do", "con", "thi", "sao", "va", "cac",
}

NOT_FOUND = {"vi": "Không tìm thấy trong tài liệu.", "en": "Not found in the document."}
LANG_NAME = {"vi": "Vietnamese (tiếng Việt)", "en": "English"}


def detect_lang(text: str) -> str:
    """'vi' nếu có dấu tiếng Việt (hoặc nhiều từ tiếng Việt không dấu), ngược lại 'en'."""
    if _VI_CHARS.search(text):
        letters = sum(ch.isalpha() for ch in text) or 1
        if len(_VI_CHARS.findall(text)) / letters > 0.01:
            return "vi"
    words = re.findall(r"[a-z]+", text.lower())
    if words and sum(w in _VI_PLAIN_WORDS for w in words) >= max(2, len(words) * 0.3):
        return "vi"
    return "en"


def _strip_citations(text: str) -> str:
    return re.sub(r"\s*\[\d+(?:\s*[,–-]\s*\d+)*\]", "", text)


_UNANSWERED_RE = re.compile(
    r"^(không tìm thấy trong tài liệu|not found in the document|tài liệu có vẻ không liên quan|the document does not seem)", re.I)


def _drop_unanswered(msgs: list[dict]) -> list[dict]:
    """Bỏ cặp (câu hỏi, câu trả lời 'không tìm thấy'): giữ lại chỉ khiến model nhỏ trả lời lại câu cũ."""
    out: list[dict] = []
    for m in msgs:
        if m["role"] == "assistant" and _UNANSWERED_RE.match(m["content"].strip()):
            if out and out[-1]["role"] == "user":
                out.pop()
            continue
        out.append(m)
    return out


def clean_history(history: list[dict], turns: int) -> list[dict]:
    """Giữ `turns` lượt gần nhất, bỏ trích dẫn cũ, cắt ngắn câu trả lời dài."""
    msgs = [m for m in history if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
    msgs = [{"role": m["role"], "content": m["content"].strip()} for m in msgs if m["content"].strip()]
    msgs = _drop_unanswered(msgs)
    msgs = msgs[-turns * 2 :] if turns > 0 else []
    out = []
    for m in msgs:
        c = m["content"]
        if m["role"] == "assistant":
            c = _strip_citations(c)
            if len(c) > 700:
                c = c[:700] + "…"
        else:
            c = c[:1000]
        out.append({"role": m["role"], "content": c})
    return out


# ---------------------------------------------------------------- viết lại câu hỏi
REWRITE_SYSTEM = (
    "You rewrite follow-up messages. Rewrite the user's latest message into a standalone question for a search engine. "
    "Use the conversation to resolve pronouns, ordinals and ellipsis (it, that one, the second one, and what about..., "
    "nó, cái đó, cái thứ hai, còn ... thì sao). Keep the same language and intent, do not answer it. "
    "If the message is already self-contained, copy it. Reply with the question only."
)

# Ví dụ mẫu (few-shot) giúp model nhỏ làm đúng. Cố ý dùng chủ đề khác tài liệu thật.
REWRITE_EXAMPLES = [
    ("User: Nhân viên được nghỉ phép bao nhiêu ngày mỗi năm?\nAssistant: 12 ngày mỗi năm.",
     "còn nghỉ ốm thì sao?", "Nhân viên được nghỉ ốm bao nhiêu ngày mỗi năm?"),
    ("User: Có những gói cước nào?\nAssistant: Gói Cơ bản, gói Nâng cao và gói Gia đình.",
     "cái thứ hai giá bao nhiêu?", "Gói Nâng cao giá bao nhiêu?"),
    ("User: What are the two payment options?\nAssistant: Credit card and bank transfer.",
     "what are the fees for the second one?", "What are the fees for bank transfer?"),
    ("User: Who signed the contract?\nAssistant: John Smith and Acme Corp.",
     "is he the CEO?", "Is John Smith the CEO?"),
]


def _rewrite_prompt(convo: str, question: str) -> str:
    return f"Conversation:\n{convo}\n\nLatest question: {question}\n"


async def rewrite_question(client: OllamaClient, history: list[dict], question: str) -> str:
    convo = "\n".join(
        ("User: " if m["role"] == "user" else "Assistant: ") + m["content"][:300] for m in history[-4:]
    )
    msgs = [{"role": "system", "content": REWRITE_SYSTEM}]
    for c, q, a in REWRITE_EXAMPLES:
        msgs += [{"role": "user", "content": _rewrite_prompt(c, q)}, {"role": "assistant", "content": a}]
    msgs.append({"role": "user", "content": _rewrite_prompt(convo, question)})
    out = await client.chat(msgs, timeout=90, temperature=0, num_predict=80)
    out = out.strip().strip('"“”').strip()
    out = re.sub(r"^(standalone question|câu hỏi( độc lập)?)\s*:\s*", "", out, flags=re.I).strip()
    out = out.split("\n")[0].strip()
    if not out or len(out) > len(question) * 4 + 150 or detect_lang(out) != detect_lang(question):
        return question
    return out


def _same_question(a: str, b: str) -> bool:
    norm = lambda t: re.sub(r"[\W_]+", " ", t.lower()).strip()  # noqa: E731
    return norm(a) == norm(b)


def _trigrams(t: str) -> set[str]:
    t = re.sub(r"\s+", " ", t.lower())
    return {t[i : i + 3] for i in range(max(len(t) - 2, 0))}


def _sim(a: str, b: str) -> float:
    x, y = _trigrams(a), _trigrams(b)
    return len(x & y) / max(len(x | y), 1)


# Dấu hiệu câu nối tiếp (cần ngữ cảnh lượt trước mới hiểu được)
_FOLLOWUP_VI = re.compile(
    r"(^\s*(còn|vậy|thế|và|nhưng|rồi|ngoài ra|tiếp|thêm)\b)|\b(nó|chúng|họ|cái (đó|này|kia|thứ \w+)|điều (đó|này)|"
    r"vấn đề (đó|này)|ý (đó|này|trên|thứ \w+)|trên đây|vừa (nói|nêu|rồi)|như vậy|thì sao|thế nào nữa|thứ (nhất|hai|ba|tư|năm|\d+)|"
    r"(phần|mục|điểm|loại|bước) (đó|này|thứ \w+|kia)|nói rõ hơn|giải thích thêm|chi tiết hơn|ví dụ (đi|thêm)|tóm tắt lại)\b",
    re.I,
)
_FOLLOWUP_EN = re.compile(
    r"(^\s*(and|but|so|also|what about|how about|then)\b)|\b(it|its|they|them|their|this|that|these|those|he|she|"
    r"the (first|second|third|last|former|latter)( one)?|above|previous|more detail|elaborate|explain more|example)\b",
    re.I,
)


def looks_followup(question: str, lang: str) -> bool:
    """Câu hỏi có cần ngữ cảnh hội thoại không? Câu độc lập thì hỏi 'sạch' – tránh việc model
    trả lời lại câu hỏi cũ (lỗi hay gặp với model nhỏ khi bị nhồi lịch sử)."""
    q = question.strip()
    if len(q.split()) <= 3:
        return True
    rx = _FOLLOWUP_VI if lang == "vi" else _FOLLOWUP_EN
    return bool(rx.search(q))


_LEAD_RE = re.compile(r"^\s*(còn|vậy|thế|và|nhưng|rồi|and|but|so|also|what about|how about)\b[\s,]*", re.I)
_TAIL_RE = re.compile(r"\s*(thì sao|thế nào|ra sao|nữa|then)?\s*\??\s*$", re.I)
_REF_RE = re.compile(r"\b(của )?(nó|đó|này|kia|ấy|trên|it|that|this|them)\b", re.I)


def merge_with_previous(question: str, prev_user: str, lang: str) -> str:
    """Dự phòng không cần LLM: 'còn ý nghĩa của định nghĩa đó thì sao?' + 'Trình bày định nghĩa vật chất của Lênin'
    → 'Ý nghĩa của định nghĩa đó (về: định nghĩa vật chất của Lênin)?'."""
    from .retrieval import clean_query

    core = _TAIL_RE.sub("", _LEAD_RE.sub("", question)).strip(" ,.")
    topic = re.sub(r"^(trình bày|phân tích|nêu|giải thích|cho biết|hãy|explain|describe|what is|what are)\s+", "",
                   clean_query(prev_user).strip(" ?."), flags=re.I)
    if not core:
        return question
    core = core[0].upper() + core[1:]
    joiner = "về" if lang == "vi" else "regarding"
    return f"{core} ({joiner}: {topic[:200]})?"


# ---------------------------------------------------------------- prompt trả lời
def system_prompt(lang: str, k: int) -> str:
    nf = NOT_FOUND[lang]
    if lang == "vi":
        # Model nhỏ bám ngôn ngữ của hướng dẫn → viết hướng dẫn bằng tiếng Việt cho câu hỏi tiếng Việt.
        # Đo thực tế với qwen2.5:3b: nói rõ "đoạn trích đã được tìm sẵn và thường chứa câu trả lời" giảm hẳn
        # việc trả lời "Không tìm thấy" sai, trong khi vẫn cấm dùng kiến thức ngoài.
        return (
            "Bạn là trợ lý học tập, trả lời câu hỏi dựa trên các đoạn trích từ tài liệu của người dùng (thường là giáo trình).\n"
            "- Các đoạn trích đã được tìm sẵn và thường chứa câu trả lời: hãy đọc kỹ và tổng hợp chúng.\n"
            "- Chỉ dùng thông tin trong đoạn trích, không thêm kiến thức bên ngoài, không bịa số liệu hay tên.\n"
            f"- Ghi nguồn ngay sau mỗi ý bằng số trong ngoặc vuông, ví dụ [1] hoặc [2][3] (chỉ dùng số từ 1 đến {k}).\n"
            "- Trả lời đầy đủ từng ý của câu hỏi, có cấu trúc (gạch đầu dòng khi có nhiều ý), không lặp lại cùng một ý.\n"
            "- Mỗi đoạn trích có ghi số trang; nếu được hỏi nội dung ở trang nào thì dùng số trang đó.\n"
            f"- Chỉ khi KHÔNG đoạn nào nói về chủ đề của câu hỏi mới trả lời đúng một câu: \"{nf}\"\n"
            "- Chỉ trả lời câu hỏi hiện tại. Viết hoàn toàn bằng tiếng Việt."
        )
    return (
        "You are a study assistant answering questions from excerpts of the user's document (often a textbook).\n"
        "- The excerpts were already retrieved and usually contain the answer: read them carefully and combine them.\n"
        "- Use only information from the excerpts; no outside knowledge, never invent facts, numbers or names.\n"
        f"- Cite the source right after each point with a number in square brackets, e.g. [1] or [2][3] (only 1 to {k}).\n"
        "- Answer every part of the question, in a structured way (bullets for several points), without repeating yourself.\n"
        "- Each excerpt shows its page; use it if asked which page something is on.\n"
        f"- Only when NO excerpt is about the topic of the question, reply exactly: \"{nf}\"\n"
        "- Answer only the current question. Write entirely in English."
    )


def build_messages(question: str, hits: list[tuple[dict, float]], history: list[dict], lang: str,
                   parts: list[str] | None = None) -> list[dict]:
    blocks = []
    for i, (c, _score) in enumerate(hits, start=1):
        loc = c.get("doc_name", "")
        if c.get("page"):
            loc += f", trang {c['page']}" + (f"-{c['page_end']}" if c.get("page_end") and c["page_end"] != c["page"] else "")
        blocks.append(f"[{i}] ({loc})\n{c['text']}")
    context = "\n\n".join(blocks)
    # Lịch sử được đưa vào như ghi chú ngữ cảnh (không phải các lượt chat) để model nhỏ không "trả lời tiếp" câu cũ.
    prev = ""
    if history:
        u = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
        a = next((m["content"] for m in reversed(history) if m["role"] == "assistant"), "")
        if u:
            prev = (f"Ngữ cảnh lượt trước (chỉ để hiểu câu hỏi, KHÔNG trả lời lại):\n- Người dùng đã hỏi: {u[:300]}\n- Trợ lý đã trả lời: {a[:400]}\n\n"
                    if lang == "vi" else
                    f"Previous turn (only to understand the question, do NOT answer it again):\n- User asked: {u[:300]}\n- Assistant answered: {a[:400]}\n\n")
    if parts and len(parts) > 1:
        task = ("Câu hỏi hiện tại gồm các ý:\n" if lang == "vi" else "The current question has these parts:\n") + "\n".join(
            f"{i}) {p}" for i, p in enumerate(parts, 1))
    else:
        task = ("Câu hỏi hiện tại: " if lang == "vi" else "Current question: ") + question
    head = "Các đoạn trích từ tài liệu:" if lang == "vi" else "Excerpts from the document:"
    user = f"{head}\n\n{context}\n\n---\n{prev}{task}"
    return [{"role": "system", "content": system_prompt(lang, len(hits))}, {"role": "user", "content": user}]


def merge_adjacent(hits: list[tuple[dict, float]]) -> list[tuple[dict, float]]:
    """Gộp các đoạn liền kề nhau (cùng tài liệu, idx liên tiếp) thành một, bỏ phần gối trùng.
    Tránh việc model lặp lại cùng một ý từ hai đoạn chồng lên nhau và làm prompt ngắn hơn."""
    out: list[tuple[dict, float]] = []
    used = set()
    by_key = {(c["doc_id"], c.get("idx")): (c, sc) for c, sc in hits}
    for c, sc in hits:
        if c["id"] in used:
            continue
        group = [(c, sc)]
        used.add(c["id"])
        # mở rộng về sau rồi về trước
        j = c.get("idx")
        while j is not None and (c["doc_id"], j + 1) in by_key and by_key[(c["doc_id"], j + 1)][0]["id"] not in used:
            nxt = by_key[(c["doc_id"], j + 1)]
            group.append(nxt); used.add(nxt[0]["id"]); j += 1
        j = c.get("idx")
        while j is not None and (c["doc_id"], j - 1) in by_key and by_key[(c["doc_id"], j - 1)][0]["id"] not in used:
            prv = by_key[(c["doc_id"], j - 1)]
            group.insert(0, prv); used.add(prv[0]["id"]); j -= 1
        if len(group) == 1:
            out.append((c, sc))
            continue
        text = group[0][0]["text"]
        for g, _ in group[1:]:
            text = _join_overlap(text, g["text"])
        merged = {**group[0][0], "text": text, "page_end": group[-1][0].get("page_end") or group[-1][0].get("page"),
                  "ids": [g["id"] for g, _ in group]}
        # giữ vị trí đổi trang của các đoạn sau (gần đúng) để tra trang vẫn chạy
        out.append((merged, max(x for _, x in group)))
    return out


def _join_overlap(a: str, b: str) -> str:
    """Nối b vào sau a, bỏ phần đầu của b trùng với phần cuối của a (so theo từ, bỏ qua khoảng trắng)."""
    b_clean = b.lstrip("…").lstrip()
    wa = re.findall(r"\S+", a)
    mb = list(re.finditer(r"\S+", b_clean))
    wb = [m.group(0) for m in mb]
    for m in range(min(len(wa), len(wb)), 2, -1):
        if wa[-m:] == wb[:m]:
            rest = b_clean[mb[m - 1].end():].lstrip()
            return (a + " " + rest) if rest else a
    return a + "\n\n" + b_clean


def low_relevance_message(lang: str, best: float) -> str:
    if lang == "vi":
        return (
            f"Tài liệu có vẻ không liên quan đến câu hỏi này (độ tương đồng cao nhất chỉ {best:.2f}). "
            "Hãy thử hỏi cụ thể hơn, hoặc dùng từ ngữ xuất hiện trong tài liệu."
        )
    return (
        f"The document does not seem to be related to this question (best similarity only {best:.2f}). "
        "Try a more specific question or use wording that appears in the document."
    )


def _page_label(c: dict, lang: str) -> str:
    p, pe = c.get("page"), c.get("page_end")
    word = "trang" if lang == "vi" else "page"
    if not p:
        return ("đoạn " if lang == "vi" else "chunk ") + str((c.get("idx") or 0) + 1)
    return f"{word} {p}" + (f"–{pe}" if pe and pe != p else "")


def locate_answer(hits: list[tuple[dict, float]], exact: bool, lang: str, page: int | None = None) -> str:
    """Trả lời câu hỏi vị trí trực tiếp từ dữ liệu (không cần LLM → nhanh, không bịa số trang)."""
    if not hits:
        return NOT_FOUND[lang]
    first = hits[0][0]
    has_pages = any(c.get("page") for c, _ in hits)
    note_vi = "\n\n_Số trang tính theo file PDF (trang 1 là trang đầu tiên của file), có thể lệch vài trang so với số in trong sách._"
    note_en = "\n\n_Page numbers count from the first page of the PDF file and may differ from the printed page numbers._"
    if exact:
        label = (("trang " if lang == "vi" else "page ") + str(page)) if page else _page_label(first, lang)
        if lang == "vi":
            out = f"Đoạn này nằm ở **{label}** của file “{first['doc_name']}” [1]."
        else:
            out = f"This passage is on **{label}** of “{first['doc_name']}” [1]."
        return out + ((note_vi if lang == "vi" else note_en) if has_pages else "")
    lines = ["Các vị trí liên quan nhất trong tài liệu:" if lang == "vi" else "Most relevant places in the document:", ""]
    for i, (c, _score) in enumerate(hits[:3], start=1):
        head = re.sub(r"\s+", " ", c["text"].lstrip("…"))[:90].rstrip()
        lines.append(f"- **{_page_label(c, lang)}** – “{c['doc_name']}”: {head}… [{i}]")
    return "\n".join(lines) + ((note_vi if lang == "vi" else note_en) if has_pages else "")


def _source_payload(hits, pca, qvec) -> dict:
    sources = []
    for i, (c, score) in enumerate(hits, start=1):
        sources.append({
            "n": i,
            "id": c["id"],
            "ids": c.get("ids") or [c["id"]],
            "doc_id": c["doc_id"],
            "doc_name": c.get("doc_name", ""),
            "page": c.get("page"),
            "page_end": c.get("page_end"),
            "idx": c.get("idx"),
            "score": round(score, 4),
            "text": c["text"],
        })
    q_xy = None
    if pca is not None and len(pca.coords):
        q_xy = [float(v) for v in pca.project(qvec)[0]]
    return {"sources": sources, "query_xy": q_xy}


async def answer_stream(
    store: SessionStore,
    client: OllamaClient,
    s: Settings,
    question: str,
    history: list[dict],
    top_k: int | None = None,
    pca_getter=None,
) -> AsyncIterator[tuple[str, dict]]:
    """Sinh các sự kiện (tên, dữ liệu) cho SSE."""
    from .retrieval import clean_query, find_passage, is_locate_question, locate_target, split_question

    question = question.strip()
    lang = detect_lang(question)
    hist = clean_history(history, s.history_turns)

    if not store.chunks:
        yield "error", {"code": "no_document", "message": "Chưa có tài liệu nào. Hãy tải lên một file trước.", "hint": ""}
        return

    k = top_k or s.top_k
    pca = pca_getter() if pca_getter else None

    # 0) "Đoạn này ở trang nào?" → tìm trực tiếp, không cần LLM
    if is_locate_question(question):
        yield "status", {"stage": "searching", "message": "Đang tìm vị trí trong tài liệu…"}
        target = locate_target(question)
        lex = store.lex()
        idx, match = await asyncio.to_thread(find_passage, lex, target)
        try:
            qvec = (await client.embed([clean_query(target) or question]))[0]
        except OllamaError as e:
            yield "error", e.to_dict()
            return
        hits = store.hybrid_search(qvec, target, k, neighbors=0)
        pasted = bool(re.search(r"[“\"«'‘:]", question)) or len(target.split()) >= 8
        exact = pasted and idx >= 0 and match >= 0.5
        if exact:
            c = store.chunks[idx]
            hits = [(c, float(np.dot(store.vectors[idx], qvec)))] + [
                h for h in hits if h[0]["id"] != c["id"]][: k - 1]
        yield "sources", _source_payload(hits, pca, qvec)
        if not exact and (not hits or hits[0][1] < s.min_similarity):
            yield "token", {"text": low_relevance_message(lang, hits[0][1] if hits else 0.0)}
            yield "done", {"low_relevance": True, "best": hits[0][1] if hits else 0.0}
            return
        page = None
        if exact:
            from .chunking import page_at
            from .retrieval import passage_offset

            page = page_at(hits[0][0], passage_offset(hits[0][0]["text"], target))
        yield "token", {"text": locate_answer(hits, exact, lang, page)}
        yield "done", {"low_relevance": False, "best": hits[0][1], "located": True, "exact": exact}
        return

    # 1) Câu nối tiếp → viết lại thành câu độc lập. Câu độc lập → bỏ qua lịch sử hoàn toàn.
    followup = bool(hist) and looks_followup(question, lang)
    if not followup:
        hist = []
    search_q = question
    if followup and s.rewrite_followups:
        yield "status", {"stage": "rewriting", "message": "Đang hiểu câu hỏi trong ngữ cảnh…"}
        try:
            search_q = await rewrite_question(client, hist, question)
        except OllamaError as e:
            if e.code in ("ollama_down", "model_missing"):
                yield "error", e.to_dict()
                return
            search_q = question
        prev_user = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        # Chặn trường hợp model "viết lại" thành chính câu hỏi trước (→ trả lời lại câu cũ)
        if prev_user and _sim(search_q, prev_user) > _sim(search_q, question) + 0.25:
            search_q = question
        # Câu viết lại vẫn còn "nó/đó/thì sao…" → model chưa giải được tham chiếu: tự ghép chủ đề câu trước
        if search_q != question and looks_followup(search_q, lang) and len(search_q.split()) > 3:
            search_q = question
        if search_q == question and prev_user:
            search_q = merge_with_previous(question, prev_user, lang)
        if _same_question(search_q, question):
            search_q = question
        else:
            yield "rewritten", {"question": search_q}

    # 2) Truy xuất (vector + từ khóa)
    yield "status", {"stage": "searching", "message": "Đang tìm trong tài liệu…"}
    query_text = clean_query(search_q)
    embed_q = query_text
    if followup and search_q == question:
        # Dự phòng khi không viết lại được câu nối tiếp: tìm bằng câu hỏi trước + câu hiện tại.
        prev_user = next((m["content"] for m in reversed(hist) if m["role"] == "user"), "")
        embed_q = (prev_user[:300] + "\n" + query_text) if prev_user else query_text
    parts = split_question(query_text) if embed_q == query_text else [embed_q]
    try:
        vecs = await client.embed([embed_q] + (parts if len(parts) > 1 else []))
    except OllamaError as e:
        yield "error", e.to_dict()
        return
    qvec = vecs[0]
    if len(parts) > 1:
        # Tìm riêng từng ý rồi gộp xen kẽ (ý 1, ý 2, ý 1, ý 2…) để ý nào cũng có đoạn trả lời
        per = [await asyncio.to_thread(store.hybrid_search, v, p, k, 1) for v, p in zip(vecs[1:], parts)]
        hits, seen = [], set()
        for rank in range(max(len(r) for r in per)):
            for r in per:
                if rank < len(r) and r[rank][0]["id"] not in seen and len(hits) < k + 1:
                    seen.add(r[rank][0]["id"])
                    hits.append(r[rank])
    else:
        hits = await asyncio.to_thread(store.hybrid_search, qvec, embed_q, k)
    hits = merge_adjacent(hits)
    yield "sources", _source_payload(hits, pca, qvec)

    best = max((h[1] for h in hits), default=0.0)
    if best < s.min_similarity:
        msg = low_relevance_message(lang, best)
        yield "token", {"text": msg}
        yield "done", {"low_relevance": True, "best": best}
        return

    # 3) Sinh câu trả lời
    yield "status", {"stage": "writing", "message": "Đang viết câu trả lời…"}
    messages = build_messages(clean_query(search_q), hits, hist[-2:] if followup else [], lang,
                              parts if len(parts) > 1 else None)
    n_tokens = 0
    try:
        async for piece in client.chat_stream(messages, num_predict=800):
            n_tokens += 1
            yield "token", {"text": piece}
    except OllamaError as e:
        yield "error", e.to_dict()
        return
    if n_tokens == 0:
        yield "error", {
            "code": "empty_answer",
            "message": "Mô hình không trả về nội dung nào.",
            "hint": "Thường do Ollama chạy trên GPU yếu hoặc thiếu bộ nhớ. Đặt num_gpu: 0 trong config.yaml (chạy CPU) rồi thử lại.",
        }
        return
    yield "done", {"low_relevance": False, "best": best, "pieces": n_tokens}


# ---------------------------------------------------------------- câu hỏi gợi ý
def pick_sample_chunks(chunks: list[dict], max_chars: int = 3000, max_n: int = 6) -> list[dict]:
    if not chunks:
        return []
    n = len(chunks)
    count = min(max_n, n)
    idxs = sorted({int(round(i * (n - 1) / max(count - 1, 1))) for i in range(count)})
    out, total = [], 0
    for i in idxs:
        t = chunks[i]["text"]
        if total + len(t) > max_chars and out:
            break
        out.append(chunks[i])
        total += len(t)
    return out


def parse_questions(raw: str) -> list[str]:
    qs: list[str] = []
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            obj = obj.get("questions") or next((v for v in obj.values() if isinstance(v, list)), [])
        if isinstance(obj, list):
            qs = [str(q) for q in obj if isinstance(q, (str, int, float))]
    except ValueError:
        qs = [re.sub(r"^\s*(\d+[.)]|[-*•])\s*", "", ln) for ln in raw.splitlines()]
    qs = [q.strip().strip('"').strip() for q in qs]
    qs = [q for q in qs if 5 <= len(q) <= 200]
    seen, uniq = set(), []
    for q in qs:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq[:3]


async def generate_suggestions(client: OllamaClient, chunks: list[dict]) -> list[str]:
    sample = pick_sample_chunks(chunks)
    if not sample:
        return []
    text = "\n\n".join(c["text"] for c in sample)
    lang = detect_lang(text)
    msgs = [
        {
            "role": "system",
            "content": (
                "You write example questions for a document Q&A app. Write exactly 3 short, specific and diverse "
                "questions that a reader could ask and that ARE answered by the excerpts. "
                f"Write the questions in {LANG_NAME[lang]}. "
                'Return JSON only: {"questions": ["...", "...", "..."]}'
            ),
        },
        {"role": "user", "content": f"Excerpts:\n\n{text}"},
    ]
    raw = await client.chat(msgs, timeout=180, format="json", temperature=0.4, num_predict=200)
    return parse_questions(raw)

