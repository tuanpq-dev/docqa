"""Ollama giả lập để test (không cần model thật).

- /api/embed: vector "túi từ" băm vào 1024 chiều → câu hỏi có từ trùng với đoạn sẽ có cosine cao.
- /api/chat: trả lời bằng câu đầu của các đoạn trích, kèm [1] [2]; hỗ trợ stream NDJSON.

Dùng như ASGI app (httpx.ASGITransport) trong pytest, hoặc chạy server:
    python -m tests.fake_ollama --port 11434
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata

import numpy as np
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

DIM = 1024
MODELS = ["bge-m3:latest", "qwen2.5:3b"]
STREAM_DELAY = 0.0


def _tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    words = re.findall(r"\w+", text)
    return words + [a + "_" + b for a, b in zip(words, words[1:])]


def fake_embed(text: str) -> list[float]:
    v = np.zeros(DIM, dtype=np.float32)
    for t in _tokens(text):
        h = int.from_bytes(hashlib.md5(t.encode()).digest()[:4], "little")
        v[h % DIM] += 1.0 if "_" not in t else 0.5
    v += 0.02  # thành phần chung nhỏ, giống embedding thật luôn có cosine > 0
    n = np.linalg.norm(v)
    return (v / n).tolist()


async def tags(_req: Request):
    return JSONResponse({"models": [{"name": m, "model": m} for m in MODELS]})


async def embed(req: Request):
    body = await req.json()
    if body.get("model") not in ("bge-m3", "bge-m3:latest"):
        return JSONResponse({"error": f"model '{body.get('model')}' not found"}, status_code=404)
    inp = body.get("input")
    inp = [inp] if isinstance(inp, str) else inp
    return JSONResponse({"model": body["model"], "embeddings": [fake_embed(t) for t in inp]})


def _answer(messages: list[dict]) -> str:
    system = messages[0]["content"] if messages else ""
    last = messages[-1]["content"]
    if "rewrite follow-up" in system.lower() or system.startswith("You rewrite"):
        m = re.search(r"Latest question:\s*(.+?)\n", last)
        q = m.group(1).strip() if m else last
        prev_user = re.findall(r"User: (.+)", last)
        topic = prev_user[-1] if prev_user else ""
        return f"{q} ({topic})" if topic else q
    if "example questions" in system:
        vi = bool(re.search(r"Vietnamese", system))
        qs = (["Tài liệu nói về chủ đề gì?", "Những điểm chính được nêu ra là gì?", "Có số liệu nào đáng chú ý không?"]
              if vi else ["What is the document about?", "What are the key points?", "Are there any notable figures?"])
        return json.dumps({"questions": qs}, ensure_ascii=False)
    excerpts = re.findall(r"\[(\d+)\] \([^)]*\)\n(.+?)(?=\n\n\[\d+\] \(|\n\n---)", last, flags=re.S)
    vi = "tiếng Việt" in system or "Vietnamese" in system
    if not excerpts:
        return "Không tìm thấy trong tài liệu." if vi else "Not found in the document."
    lines = ["Theo tài liệu:" if vi else "According to the document:", ""]
    for n, text in excerpts[:2]:
        first = re.split(r"(?<=[.!?])\s", text.strip())[0][:200]
        lines.append(f"- **{first}** [{n}]")
    lines.append("")
    lines.append(("Ví dụ mã: `x < y && \"<b>\"`" if vi else "Code sample: `x < y && \"<b>\"`") + " [1]")
    return "\n".join(lines)


async def chat(req: Request):
    body = await req.json()
    if body.get("model") not in MODELS and body.get("model") + ":latest" not in MODELS:
        return JSONResponse({"error": f"model '{body.get('model')}' not found"}, status_code=404)
    text = _answer(body.get("messages", []))
    if not body.get("stream", True):
        return JSONResponse({"message": {"role": "assistant", "content": text}, "done": True})

    async def gen():
        for piece in re.findall(r"\S+\s*|\s+", text):
            yield json.dumps({"message": {"role": "assistant", "content": piece}, "done": False}) + "\n"
            if STREAM_DELAY:
                await asyncio.sleep(STREAM_DELAY)
        yield json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


app = Starlette(routes=[
    Route("/api/tags", tags),
    Route("/api/embed", embed, methods=["POST"]),
    Route("/api/chat", chat, methods=["POST"]),
])

if __name__ == "__main__":
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--delay", type=float, default=0.04)
    a = ap.parse_args()
    STREAM_DELAY = a.delay
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
