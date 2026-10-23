"""Kiểm thử API end-to-end với Ollama giả lập (không cần Ollama thật)."""
import json
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.embeddings import OllamaClient
from app.main import create_app
from tests import fake_ollama

SID = "testsession-0001"
H = {"X-Session-Id": SID}


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        ev, data = "message", ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            events.append((ev, json.loads(data)))
    return events


def make_client(tmp_path, transport=None, **over):
    over = {"suggestions": False, "min_similarity": 0.1, **over}
    s = replace(Settings(), data_dir=str(tmp_path / "data"), **over)
    oc = OllamaClient(s, transport=transport or httpx.ASGITransport(app=fake_ollama.app))
    oc.retry_delay = 0
    return TestClient(create_app(s, oc))


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def upload(c, path, name=None, headers=H, **params):
    r = c.post("/api/upload", params={"filename": name or path.name, **params}, content=path.read_bytes(), headers=headers)
    assert r.status_code == 200, r.text
    return parse_sse(r.text)


def ask(c, q, history=None):
    r = c.post("/api/chat", json={"question": q, "history": history or []}, headers=H)
    assert r.status_code == 200
    return parse_sse(r.text)


def test_health_ok(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["ollama"] and h["models"]["bge-m3"] and h["models"]["qwen2.5:3b"]


def test_health_ollama_down(tmp_path):
    def boom(_req):
        raise httpx.ConnectError("refused")
    with make_client(tmp_path, transport=httpx.MockTransport(boom)) as c:
        h = c.get("/api/health").json()
        assert not h["ok"] and h["problems"][0]["code"] == "ollama_down"


def test_health_missing_model(tmp_path):
    with make_client(tmp_path, llm_model="qwen2.5:7b") as c:
        h = c.get("/api/health").json()
        assert not h["ok"]
        assert any(p["code"] == "model_missing" and "ollama pull qwen2.5:7b" in p["hint"] for p in h["problems"])


def test_upload_progress_and_doc(client, fixtures, tmp_path):
    ev = upload(client, fixtures / "vi_sample.pdf")
    names = [e for e, _ in ev]
    assert names[0] == "progress" and names[-1] == "done"
    stages = [d["stage"] for e, d in ev if e == "progress"]
    assert stages[:3] == ["reading", "chunking", "embedding"]
    emb = [d for e, d in ev if e == "progress" and d["stage"] == "embedding"]
    assert emb[-1]["done"] == emb[-1]["total"] > 0
    done = ev[-1][1]
    assert done["doc"]["pages"] == 3 and done["stats"]["dim"] == 1024
    doc = client.get("/api/doc", headers=H).json()
    assert doc["docs"][0]["name"] == "vi_sample.pdf"
    assert (tmp_path / "data" / SID / "vectors.npy").exists()


def test_chat_vietnamese_with_sources(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    ev = ask(client, "Mỗi gốc cà phê cần bao nhiêu lít nước cho một lần tưới?")
    kinds = [e for e, _ in ev]
    assert "sources" in kinds and kinds[-1] == "done"
    src = next(d for e, d in ev if e == "sources")
    assert len(src["sources"]) <= 5 and src["query_xy"] is not None
    assert src["sources"][0]["score"] >= src["sources"][-1]["score"]
    top = src["sources"][0]
    assert "500 đến" in top["text"] and top["page"] <= 2 <= top["page_end"]
    answer = "".join(d["text"] for e, d in ev if e == "token")
    assert "[1]" in answer and "Theo tài liệu" in answer


def test_chat_english_doc(client, fixtures):
    upload(client, fixtures / "en_sample.pdf")
    ev = ask(client, "How long do string inverters usually last?")
    answer = "".join(d["text"] for e, d in ev if e == "token")
    assert answer.startswith("According to the document")
    src = next(d for e, d in ev if e == "sources")["sources"]
    assert any("10 to 15 years" in s["text"] for s in src[:2])


def test_low_relevance_skips_llm(tmp_path, fixtures):
    with make_client(tmp_path, min_similarity=0.99) as c:
        upload(c, fixtures / "vi_sample.pdf")
        ev = ask(c, "Giá vàng hôm nay bao nhiêu?")
        done = ev[-1][1]
        assert done["low_relevance"] is True
        assert "không liên quan" in "".join(d["text"] for e, d in ev if e == "token")
        assert not any(e == "status" and d["stage"] == "writing" for e, d in ev)


def test_followup_is_rewritten(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    hist = [{"role": "user", "content": "Đất trồng cà phê phù hợp là gì?"},
            {"role": "assistant", "content": "Đất đỏ bazan [1]."}]
    ev = ask(client, "còn mật độ trồng thì sao?", hist)
    rw = [d for e, d in ev if e == "rewritten"]
    assert rw and "Đất trồng cà phê" in rw[0]["question"]


def test_chat_without_document(client):
    ev = ask(client, "Xin chào")
    assert ev[-1][0] == "error" and ev[-1][1]["code"] == "no_document"


def test_upload_errors(client, fixtures):
    ev = upload(client, fixtures / "scanned.pdf")
    assert ev[-1] == ("error", ev[-1][1]) and ev[-1][1]["code"] == "scanned"
    ev = upload(client, fixtures / "corrupt.pdf")
    assert ev[-1][1]["code"] == "corrupt"


def test_upload_too_large(tmp_path):
    with make_client(tmp_path, max_upload_mb=1) as c:
        r = c.post("/api/upload", params={"filename": "big.txt"}, content=b"a" * (1024 * 1024 + 10), headers=H)
        assert r.status_code == 413 and r.json()["code"] == "too_large"


def test_upload_ollama_down_reports_vietnamese_error(tmp_path, fixtures):
    def boom(_req):
        raise httpx.ConnectError("refused")
    with make_client(tmp_path, transport=httpx.MockTransport(boom)) as c:
        ev = upload(c, fixtures / "vi_sample.pdf")
        assert ev[-1][0] == "error" and ev[-1][1]["code"] == "ollama_down"
        assert "Ollama" in ev[-1][1]["message"]


def test_embed_timeout_is_reported(tmp_path, fixtures):
    def slow(_req):
        raise httpx.ReadTimeout("slow")
    with make_client(tmp_path, transport=httpx.MockTransport(slow)) as c:
        ev = upload(c, fixtures / "vi_sample.pdf")
        assert ev[-1][1]["code"] == "timeout"


def test_filename_path_traversal_is_neutralized(client, fixtures, tmp_path):
    ev = upload(client, fixtures / "vi_sample.pdf", name="../../../evil.pdf")
    assert ev[-1][1]["doc"]["name"] == "evil.pdf"
    files = {p.name for p in (tmp_path / "data" / SID).iterdir()}
    assert files <= {"meta.json", "chunks.json", "vectors.npy", ".last_access"}
    assert not (tmp_path / "evil.pdf").exists()


@pytest.mark.parametrize("sid", ["../../etc", "..", "a", "x/y/zzzzzzzz", "%2e%2e%2f%2e%2e"])
def test_bad_session_ids_rejected(client, sid):
    r = client.get("/api/doc", headers={"X-Session-Id": sid})
    assert r.status_code == 400


def test_missing_session_header(client):
    assert client.get("/api/doc").status_code == 400


def test_sessions_isolated(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    other = {"X-Session-Id": "othersession-01"}
    assert client.get("/api/doc", headers=other).json()["docs"] == []


def test_delete_session_wipes_disk(client, fixtures, tmp_path):
    upload(client, fixtures / "vi_sample.pdf")
    assert (tmp_path / "data" / SID).exists()
    assert client.delete("/api/session", headers=H).json()["ok"]
    assert not (tmp_path / "data" / SID).exists()
    assert client.get("/api/doc", headers=H).json()["docs"] == []


def test_map_chunk_and_remove_doc(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    upload(client, fixtures / "en_sample.pdf")
    m = client.get("/api/map", headers=H).json()
    assert len(m["points"]) >= 4 and m["dim"] == 1024
    assert {"x", "y", "pos", "preview"} <= set(m["points"][0])
    cid = m["points"][0]["id"]
    assert client.get(f"/api/chunk/{cid}", headers=H).json()["id"] == cid
    doc_id = client.get("/api/doc", headers=H).json()["docs"][0]["id"]
    after = client.delete(f"/api/doc/{doc_id}", headers=H).json()
    assert len(after["docs"]) == 1 and after["docs"][0]["name"] == "en_sample.pdf"


def test_replace_document(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    doc_id = client.get("/api/doc", headers=H).json()["docs"][0]["id"]
    upload(client, fixtures / "en_sample.pdf", replace=doc_id)
    docs = client.get("/api/doc", headers=H).json()["docs"]
    assert [d["name"] for d in docs] == ["en_sample.pdf"]


def test_suggestions_generated_in_background(tmp_path, fixtures):
    import time
    with make_client(tmp_path, suggestions=True) as c:
        upload(c, fixtures / "vi_sample.docx")
        for _ in range(50):
            sug = c.get("/api/doc", headers=H).json()["suggestions"]
            if sug["status"] != "pending":
                break
            time.sleep(0.05)
        assert sug["status"] == "done" and len(sug["items"]) == 3


def test_foreign_host_header_rejected(client):
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


def test_index_served_with_csp(client):
    r = client.get("/")
    assert r.status_code == 200 and "Content-Security-Policy" in r.headers


# ---------------------------------------------------------------- máy chậm / nạp dở dang
def _embed_response(req):
    body = json.loads(req.content)
    inp = body["input"]
    return httpx.Response(200, json={"embeddings": [fake_ollama.fake_embed(t) for t in inp]})


def _client_with(tmp_path, handler, **over):
    over = {"suggestions": False, "min_similarity": 0.1, "embed_batch_size": 4, **over}
    s = replace(Settings(), data_dir=str(tmp_path / "data"), **over)
    oc = OllamaClient(s, transport=httpx.MockTransport(handler))
    oc.retry_delay = 0
    return TestClient(create_app(s, oc)), oc


def test_slow_batch_is_split_instead_of_failing(tmp_path, fixtures):
    seen = []

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in fake_ollama.MODELS]})
        n = len(json.loads(req.content)["input"])
        seen.append(n)
        if n > 1:  # máy quá bận: batch nhiều đoạn luôn quá giờ
            raise httpx.ReadTimeout("slow")
        return _embed_response(req)

    c, _ = _client_with(tmp_path, handler, embed_batch_size=2)
    with c:
        ev = upload(c, fixtures / "vi_sample.pdf")
    assert ev[-1][0] == "done", ev[-1]
    assert 2 in seen and 1 in seen


def test_failed_ingest_resumes_from_saved_progress(tmp_path, fixtures):
    state = {"calls": 0, "broken": True}

    def handler(req):
        if req.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m} for m in fake_ollama.MODELS]})
        state["calls"] += 1
        if state["broken"] and state["calls"] > 1:  # batch đầu thành công, sau đó Ollama "chết"
            raise httpx.ConnectError("down")
        return _embed_response(req)

    doc = fixtures / "notes_en.md"
    big = tmp_path / "big.md"
    big.write_text("\n\n".join(f"Section {i}. " + doc.read_text() for i in range(4)), encoding="utf-8")
    c, _ = _client_with(tmp_path, handler, embed_batch_size=2)
    with c:
        ev = upload(c, big)
        assert ev[-1][0] == "error" and ev[-1][1]["code"] == "ollama_down"
        assert "Đã lưu tiến độ 2/" in ev[-1][1]["hint"]
        assert (tmp_path / "data" / SID / "embcache").is_dir()

        state["broken"] = False
        ev = upload(c, big)
        first_emb = next(d for e, d in ev if e == "progress" and d["stage"] == "embedding")
        assert first_emb["done"] == 2 and "tiếp tục" in first_emb["message"]
        assert ev[-1][0] == "done"
        assert not (tmp_path / "data" / SID / "embcache").exists()
        # vector của 2 đoạn lấy từ bộ đệm phải giống hệt vector tính mới
        r = c.post("/api/chat", json={"question": "How often should panels be cleaned?"}, headers=H)
        assert any(e == "sources" for e, _ in parse_sse(r.text))


def test_locate_question_answered_directly_without_llm(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    ev = ask(client, "Đoạn “Tưới nhỏ giọt giúp tiết kiệm khoảng 30 đến 40 phần trăm lượng nước” nằm ở trang nào?")
    assert not any(e == "status" and d["stage"] == "writing" for e, d in ev)
    answer = "".join(d["text"] for e, d in ev if e == "token")
    assert "trang 2" in answer and "[1]" in answer
    assert ev[-1][1].get("exact") is True


def test_standalone_question_ignores_history(client, fixtures):
    upload(client, fixtures / "vi_sample.pdf")
    hist = [{"role": "user", "content": "Đất trồng cà phê phù hợp là gì?"}, {"role": "assistant", "content": "Đất đỏ bazan [1]."}]
    ev = ask(client, "Mỗi gốc cà phê cần bao nhiêu lít nước cho một lần tưới?", hist)
    assert not any(e == "status" and d["stage"] == "rewriting" for e, d in ev)
    assert not any(e == "rewritten" for e, _ in ev)
