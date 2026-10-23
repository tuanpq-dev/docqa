"""docqa – server FastAPI. Chạy: python -m app.main"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import unquote

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import rag
from .analysis import fit_pca, near_duplicates
from .chunking import chunk_segments, estimate_tokens
from .config import ROOT, Settings, settings as default_settings
from .embcache import EmbedCache
from .embeddings import OllamaClient, OllamaError
from .ingest import IngestError, parse_file, safe_filename
from .sessions import InvalidSession, SessionManager, short, validate_session_id
from .store import DocInfo, SessionStore

log = logging.getLogger("docqa")
STATIC_DIR = ROOT / "static"


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ChatBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    history: list[dict] = Field(default_factory=list, max_length=40)
    top_k: int | None = Field(default=None, ge=1, le=20)


def create_app(s: Settings | None = None, client: OllamaClient | None = None) -> FastAPI:
    s = s or default_settings
    client = client or OllamaClient(s)
    sessions = SessionManager(s.data_path, s.session_ttl_days)
    suggestion_tasks: dict[str, asyncio.Task] = {}
    ingest_locks: dict[str, asyncio.Lock] = {}

    async def cleanup_loop():
        while True:
            try:
                await asyncio.to_thread(sessions.cleanup)
            except Exception:  # noqa: BLE001
                log.exception("cleanup failed")
            await asyncio.sleep(6 * 3600)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        logging.basicConfig(
            level=getattr(logging, s.log_level.upper(), logging.INFO),
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        logging.getLogger("httpx").setLevel(logging.WARNING)  # không log từng request tới Ollama
        h = await client.health()
        if not h["ollama"]:
            log.warning("Không kết nối được Ollama tại %s – hãy mở Ollama (ollama serve).", s.ollama_url)
        else:
            for m, ok in h["models"].items():
                if not ok:
                    log.warning("Thiếu model %s – chạy: ollama pull %s", m, m)
        log.info("docqa sẵn sàng: http://%s:%s  (LLM=%s, embed=%s)", s.host, s.port, s.llm_model, s.embed_model)
        task = asyncio.create_task(cleanup_loop())
        yield
        task.cancel()
        for t in suggestion_tasks.values():
            t.cancel()
        await client.aclose()

    app = FastAPI(title="docqa", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = s
    app.state.sessions = sessions
    app.state.client = client

    # Chống DNS-rebinding: chỉ chấp nhận Host là máy local (hoặc host đã cấu hình)
    allowed = {"localhost", "127.0.0.1", "[::1]", "testserver", s.host}
    if s.host in ("0.0.0.0", "::"):
        allowed = {"*"}
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(allowed))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/static") or request.url.path == "/":
            resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.exception_handler(InvalidSession)
    async def _bad_session(_req, exc):
        return JSONResponse({"code": "bad_session", "message": str(exc)}, status_code=400)

    def session_id(request: Request) -> str:
        return validate_session_id(request.headers.get("x-session-id"))

    def get_store(sid: str = Depends(session_id)) -> SessionStore:
        return sessions.get(sid)

    def pca_for(store: SessionStore):
        with store.lock:
            if store._pca_cache is None and len(store.chunks):
                store._pca_cache = fit_pca(store.vectors)
            return store._pca_cache

    def dups_for(store: SessionStore):
        with store.lock:
            if store._dup_cache is None:
                store._dup_cache = near_duplicates(store.vectors, s.duplicate_threshold) if len(store.chunks) else []
            return store._dup_cache

    def cancel_suggestions(sid: str):
        t = suggestion_tasks.pop(sid, None)
        if t and not t.done():
            t.cancel()

    async def run_suggestions(sid: str, store: SessionStore):
        try:
            items = await asyncio.wait_for(rag.generate_suggestions(client, list(store.chunks)), timeout=240)
            store.suggestions = {"status": "done" if items else "failed", "items": items}
        except asyncio.CancelledError:
            store.suggestions = {"status": "skipped", "items": []}
            store.save_meta()
            raise
        except (OllamaError, asyncio.TimeoutError, Exception) as e:  # noqa: BLE001 – gợi ý lỗi thì bỏ qua
            log.info("suggestions failed for %s: %s", short(sid), type(e).__name__)
            store.suggestions = {"status": "failed", "items": []}
        store.save_meta()
        suggestion_tasks.pop(sid, None)

    def doc_payload(store: SessionStore) -> dict:
        pca = pca_for(store)
        return {
            "docs": [d.__dict__ for d in store.docs],
            "stats": {
                **store.stats(),
                "duplicates": len(dups_for(store)),
                "explained": pca.explained if pca else [0, 0],
            },
            "suggestions": store.suggestions,
        }

    # ------------------------------------------------------------------ routes
    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    async def health():
        h = await client.health()
        h["config"] = {
            "max_upload_mb": s.max_upload_mb,
            "max_files": s.max_files,
            "top_k": s.top_k,
            "min_similarity": s.min_similarity,
        }
        return h

    @app.get("/api/doc")
    async def get_doc(store: SessionStore = Depends(get_store)):
        return doc_payload(store)

    @app.delete("/api/doc/{doc_id}")
    async def delete_doc(doc_id: str, sid: str = Depends(session_id)):
        store = sessions.get(sid)
        cancel_suggestions(sid)
        if not await asyncio.to_thread(store.remove_document, doc_id):
            raise HTTPException(404, "Không tìm thấy tài liệu.")
        return doc_payload(store)

    @app.delete("/api/session")
    async def delete_session(sid: str = Depends(session_id)):
        cancel_suggestions(sid)
        existed = await asyncio.to_thread(sessions.delete, sid)
        return {"ok": True, "existed": existed}

    @app.get("/api/chunk/{chunk_id}")
    async def get_chunk(chunk_id: str, store: SessionStore = Depends(get_store)):
        c = store.chunk_by_id(chunk_id)
        if not c:
            raise HTTPException(404, "Không tìm thấy đoạn.")
        return c

    @app.get("/api/map")
    async def get_map(store: SessionStore = Depends(get_store)):
        pca = await asyncio.to_thread(pca_for, store)
        dups = await asyncio.to_thread(dups_for, store)
        with store.lock:
            chunks = list(store.chunks)
        points = []
        if pca is not None:
            for c, (x, y) in zip(chunks, pca.coords.tolist()):
                points.append({
                    "id": c["id"], "x": x, "y": y, "pos": c["pos"], "idx": c["idx"],
                    "doc_id": c["doc_id"], "doc_name": c["doc_name"], "page": c.get("page"), "page_end": c.get("page_end"),
                    "preview": c["text"][:220],
                })
        return {
            "points": points,
            "explained": pca.explained if pca else [0, 0],
            "dim": store.dim,
            "duplicates": {
                "threshold": s.duplicate_threshold,
                "count": len(dups),
                "pairs": [{"a": chunks[i]["id"], "b": chunks[j]["id"], "score": round(sc, 4)} for i, j, sc in dups[:50]],
            },
        }

    @app.post("/api/upload")
    async def upload(request: Request, sid: str = Depends(session_id)):
        """Body là nội dung file thô; tên file trong query ?filename=. Trả về SSE tiến độ."""
        filename = safe_filename(unquote(request.query_params.get("filename", "")))
        replace = request.query_params.get("replace") or None
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > s.max_upload_bytes:
            return JSONResponse(
                {"code": "too_large", "message": f"File quá lớn. Giới hạn là {s.max_upload_mb} MB.", "hint": "Hãy tách file thành các phần nhỏ hơn."},
                status_code=413,
            )
        buf = bytearray()
        async for part in request.stream():
            buf.extend(part)
            if len(buf) > s.max_upload_bytes:
                return JSONResponse(
                    {"code": "too_large", "message": f"File quá lớn. Giới hạn là {s.max_upload_mb} MB.", "hint": ""},
                    status_code=413,
                )
        data = bytes(buf)
        store = sessions.get(sid)
        lock = ingest_locks.setdefault(sid, asyncio.Lock())

        async def gen():
            if lock.locked():
                yield sse("error", {"code": "busy", "message": "Đang nạp một file khác, vui lòng chờ.", "hint": ""})
                return
            async with lock:
                sessions.mark_busy(sid, 1)
                t0 = time.monotonic()
                try:
                    if not replace and len(store.docs) >= s.max_files:
                        raise IngestError("too_many", f"Mỗi phiên chỉ nạp tối đa {s.max_files} file.", "Gỡ bớt một file rồi thử lại.")
                    yield sse("progress", {"stage": "reading", "message": "Đang đọc file…"})
                    parsed = await asyncio.to_thread(parse_file, filename, data, s.max_upload_bytes)
                    yield sse("progress", {"stage": "chunking", "message": "Đang chia đoạn…", "pages": parsed.pages})
                    chunks = await asyncio.to_thread(chunk_segments, parsed.segments, s.chunk_size, s.chunk_overlap)
                    if not chunks:
                        raise IngestError("empty", "Không tìm thấy nội dung chữ trong file.", "")
                    if len(chunks) > s.max_chunks:
                        raise IngestError(
                            "too_long",
                            f"Tài liệu quá dài ({len(chunks)} đoạn, giới hạn {s.max_chunks}). Tạo vector trên CPU sẽ rất lâu.",
                            "Hãy tách tài liệu, hoặc tăng max_chunks trong config.yaml.",
                        )
                    total = len(chunks)
                    texts = [c.text for c in chunks]
                    cache = await asyncio.to_thread(EmbedCache, store.path, s.embed_model)
                    have = [cache.get(t) for t in texts]
                    resumed = sum(v is not None for v in have)
                    todo = [i for i, v in enumerate(have) if v is None]
                    msg = f"Đang tạo vector {resumed}/{total}" + (" (tiếp tục lần trước)" if resumed else "")
                    yield sse("progress", {"stage": "embedding", "done": resumed, "total": total, "message": msg})
                    bs = max(1, s.embed_batch_size)
                    t_emb = time.monotonic()
                    for start in range(0, len(todo), bs):
                        idx = todo[start : start + bs]
                        batch = [texts[i] for i in idx]
                        try:
                            vecs = await client.embed_resilient(batch)
                        except OllamaError as e:
                            saved = total - len(todo) + start
                            if saved:
                                e.hint = (e.hint + " " if e.hint else "") + f"Đã lưu tiến độ {saved}/{total} đoạn – nạp lại cùng file để chạy tiếp."
                            raise
                        await asyncio.to_thread(cache.add, batch, vecs)
                        for i, v in zip(idx, vecs):
                            have[i] = v
                        made = start + len(idx)
                        done = resumed + made
                        rate = (time.monotonic() - t_emb) / made
                        eta = rate * (len(todo) - made)
                        yield sse("progress", {"stage": "embedding", "done": done, "total": total, "eta_s": round(eta),
                                               "message": f"Đang tạo vector {done}/{total}"})
                    matrix = np.vstack(have).astype(np.float32)
                    if store.dim and matrix.shape[1] != store.dim:
                        raise IngestError("dim_mismatch", "Model embedding đã thay đổi so với tài liệu đang có.", "Hãy xóa tài liệu & lịch sử rồi nạp lại.")
                    text_all = "\n".join(c.text for c in chunks)
                    info = DocInfo(
                        id=uuid.uuid4().hex[:10], name=filename, kind=parsed.kind, pages=parsed.pages,
                        size=len(data), n_chunks=total, chars=sum(len(seg.text) for seg in parsed.segments),
                        tokens=estimate_tokens(text_all),
                    )
                    cancel_suggestions(sid)
                    if replace:
                        await asyncio.to_thread(store.remove_document, replace)
                    store.suggestions = {"status": "pending" if s.suggestions else "off", "items": []}
                    await asyncio.to_thread(
                        store.add_document, info,
                        [{"text": c.text, "page": c.page, "page_end": c.page_end, "marks": c.page_marks} for c in chunks], matrix,
                    )
                    await asyncio.to_thread(cache.clear)
                    log.info("session %s: ingested %s file (%d KB, %d chunks) in %.1fs",
                             short(sid), parsed.kind, len(data) // 1024, total, time.monotonic() - t0)
                    if s.suggestions:
                        suggestion_tasks[sid] = asyncio.create_task(run_suggestions(sid, store))
                    yield sse("done", {"doc": info.__dict__, **doc_payload(store)})
                except IngestError as e:
                    log.info("session %s: ingest rejected (%s)", short(sid), e.code)
                    yield sse("error", e.to_dict())
                except OllamaError as e:
                    log.warning("session %s: ollama error during ingest (%s)", short(sid), e.code)
                    yield sse("error", e.to_dict())
                except Exception:  # noqa: BLE001
                    log.exception("session %s: ingest failed", short(sid))
                    yield sse("error", {"code": "internal", "message": "Lỗi không mong đợi khi nạp file.", "hint": "Xem log trên terminal."})
                finally:
                    sessions.mark_busy(sid, -1)

        return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/api/chat")
    async def chat(body: ChatBody, sid: str = Depends(session_id)):
        store = sessions.get(sid)
        # Câu hỏi của người dùng được ưu tiên hơn việc sinh câu hỏi gợi ý
        cancel_suggestions(sid)

        async def gen():
            t0 = time.monotonic()
            sessions.mark_busy(sid, 1)
            try:
                async for ev, data in rag.answer_stream(
                    store, client, s, body.question, body.history, body.top_k, pca_getter=lambda: pca_for(store)
                ):
                    yield sse(ev, data)
                log.info("session %s: answered in %.1fs", short(sid), time.monotonic() - t0)
            except asyncio.CancelledError:
                log.info("session %s: answer stopped by client", short(sid))
                raise
            except Exception:  # noqa: BLE001
                log.exception("session %s: chat failed", short(sid))
                yield sse("error", {"code": "internal", "message": "Lỗi không mong đợi khi trả lời.", "hint": "Xem log trên terminal."})
            finally:
                sessions.mark_busy(sid, -1)

        return StreamingResponse(gen(), media_type="text/event-stream", headers=SSE_HEADERS)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


app = create_app()


def main():
    import uvicorn

    s = default_settings
    uvicorn.run(app, host=s.host, port=s.port, log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
