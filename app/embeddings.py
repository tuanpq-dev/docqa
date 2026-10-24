"""Gọi Ollama qua httpx: embedding, chat (stream) và kiểm tra model.

Mọi lời gọi đều có timeout; lỗi được chuyển thành OllamaError với thông điệp tiếng Việt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator

import httpx
import numpy as np

from .config import Settings

log = logging.getLogger("docqa.ollama")


class OllamaError(Exception):
    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "hint": self.hint}


def _model_matches(wanted: str, available: set[str]) -> bool:
    if wanted in available:
        return True
    if ":" not in wanted and f"{wanted}:latest" in available:
        return True
    return False


class OllamaClient:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.s = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self.retry_delay = 3.0  # giây chờ trước khi thử lại

    def client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.s.ollama_url.rstrip("/"),
                transport=self._transport,
                timeout=httpx.Timeout(self.s.llm_timeout, connect=self.s.connect_timeout),
            )
        return self._client

    async def aclose(self):
        if self._client is not None:
            await self._client.aclose()

    # ---------- lỗi ----------
    def _conn_error(self) -> OllamaError:
        return OllamaError(
            "ollama_down",
            "Không kết nối được Ollama tại " + self.s.ollama_url + ".",
            "Hãy mở ứng dụng Ollama hoặc chạy lệnh `ollama serve`, rồi bấm Kiểm tra lại.",
        )

    def _missing_model(self, model: str) -> OllamaError:
        return OllamaError(
            "model_missing",
            f"Chưa có model “{model}” trong Ollama.",
            f"Chạy lệnh: ollama pull {model}",
        )

    async def _raise_for(self, resp: httpx.Response, model: str):
        if resp.status_code < 400:
            return
        try:
            body = (await resp.aread()).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        if resp.status_code == 404 or "not found" in body.lower():
            raise self._missing_model(model)
        log.warning("ollama http %s", resp.status_code)
        raise OllamaError("ollama_error", f"Ollama báo lỗi (HTTP {resp.status_code}).", body[:200])

    # ---------- kiểm tra ----------
    async def health(self) -> dict:
        out = {
            "ollama": False,
            "models": {self.s.embed_model: False, self.s.llm_model: False},
            "embed_model": self.s.embed_model,
            "llm_model": self.s.llm_model,
            "problems": [],
        }
        try:
            r = await self.client().get("/api/tags", timeout=self.s.connect_timeout + 3)
            r.raise_for_status()
            names = {m.get("name", "") for m in r.json().get("models", [])}
            names |= {m.get("model", "") for m in r.json().get("models", [])}
            out["ollama"] = True
            for m in out["models"]:
                out["models"][m] = _model_matches(m, names)
                if not out["models"][m]:
                    out["problems"].append(self._missing_model(m).to_dict())
        except (httpx.HTTPError, ValueError):
            out["problems"].append(self._conn_error().to_dict())
        out["ok"] = out["ollama"] and all(out["models"].values())
        return out

    # ---------- embedding ----------
    async def embed(self, texts: list[str], timeout: float | None = None) -> np.ndarray:
        """Trả về ma trận (n, d) float32 đã chuẩn hóa L2."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        try:
            r = await self.client().post(
                "/api/embed",
                json={"model": self.s.embed_model, "input": texts, "truncate": True, **self._gpu_opts()},
                timeout=httpx.Timeout(timeout or self.s.embed_timeout, connect=self.s.connect_timeout),
            )
        except httpx.ConnectError as e:
            raise self._conn_error() from e
        except httpx.TimeoutException as e:
            raise OllamaError(
                "timeout",
                "Ollama phản hồi quá chậm khi tạo vector.",
                "Máy có thể đang bận (ứng dụng AI khác, nhiều tab nặng…). Đóng bớt rồi nạp lại cùng file để chạy tiếp.",
            ) from e
        except httpx.HTTPError as e:
            raise self._conn_error() from e
        await self._raise_for(r, self.s.embed_model)
        data = r.json().get("embeddings")
        if not data or len(data) != len(texts):
            raise OllamaError("ollama_error", "Ollama trả về embedding không hợp lệ.", "")
        arr = np.asarray(data, dtype=np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return arr / norms

    async def embed_resilient(self, texts: list[str], timeout: float | None = None, retries: int = 2) -> np.ndarray:
        """Như embed() nhưng chịu được máy chậm/bận: quá giờ thì chia đôi batch,
        lỗi tạm thời thì chờ rồi thử lại (thời gian chờ tăng dần)."""
        timeout = timeout or self.s.embed_timeout
        try:
            return await self.embed(texts, timeout)
        except OllamaError as e:
            if e.code == "model_missing":
                raise
            if e.code == "timeout" and len(texts) > 1:
                log.info("embed timeout with batch of %d – splitting", len(texts))
                mid = len(texts) // 2
                left = await self.embed_resilient(texts[:mid], timeout, retries)
                right = await self.embed_resilient(texts[mid:], timeout, retries)
                return np.vstack([left, right])
            if retries > 0:
                log.info("embed failed (%s) – retrying", e.code)
                await asyncio.sleep(self.retry_delay)
                return await self.embed_resilient(texts, timeout * 1.5, retries - 1)
            raise

    # ---------- chat ----------
    def _gpu_opts(self) -> dict:
        """num_gpu=0 ép Ollama chạy CPU; giá trị âm để Ollama tự quyết."""
        return {"options": {"num_gpu": self.s.num_gpu}} if self.s.num_gpu >= 0 else {}

    def _chat_payload(self, messages: list[dict], stream: bool, **opts) -> dict:
        options = {"num_ctx": self.s.num_ctx, "temperature": self.s.temperature, **self._gpu_opts().get("options", {})}
        options.update({k: v for k, v in opts.items() if k not in ("format",)})
        payload = {"model": self.s.llm_model, "messages": messages, "stream": stream, "options": options,
                   "keep_alive": "30m", "think": self.s.think}
        if "format" in opts:
            payload["format"] = opts["format"]
        return payload

    async def chat(self, messages: list[dict], timeout: float | None = None, **opts) -> str:
        """Gọi chat không stream (dùng cho viết lại câu hỏi, câu hỏi gợi ý)."""
        timeout = timeout or self.s.llm_timeout
        try:
            r = await self.client().post(
                "/api/chat",
                json=self._chat_payload(messages, False, **opts),
                timeout=httpx.Timeout(timeout, connect=self.s.connect_timeout),
            )
        except httpx.ConnectError as e:
            raise self._conn_error() from e
        except httpx.TimeoutException as e:
            raise OllamaError("timeout", "Mô hình phản hồi quá chậm.", "") from e
        except httpx.HTTPError as e:
            raise self._conn_error() from e
        await self._raise_for(r, self.s.llm_model)
        return (r.json().get("message") or {}).get("content", "")

    async def chat_stream(self, messages: list[dict], **opts) -> AsyncIterator[str]:
        """Stream từng mẩu token. Dừng khi quá llm_total_timeout hoặc không có token trong llm_timeout."""
        deadline = time.monotonic() + self.s.llm_total_timeout
        try:
            async with self.client().stream(
                "POST",
                "/api/chat",
                json=self._chat_payload(messages, True, **opts),
                timeout=httpx.Timeout(self.s.llm_timeout, connect=self.s.connect_timeout),
            ) as r:
                await self._raise_for(r, self.s.llm_model)
                async for line in r.aiter_lines():
                    if time.monotonic() > deadline:
                        raise OllamaError(
                            "timeout",
                            "Câu trả lời mất quá nhiều thời gian nên đã dừng.",
                            "Thử câu hỏi ngắn hơn hoặc dùng model nhỏ hơn.",
                        )
                    if not line.strip():
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    if obj.get("error"):
                        raise OllamaError("ollama_error", "Ollama báo lỗi khi sinh câu trả lời.", str(obj["error"])[:200])
                    piece = (obj.get("message") or {}).get("content", "")
                    if piece:
                        yield piece
                    if obj.get("done"):
                        break
        except httpx.ConnectError as e:
            raise self._conn_error() from e
        except httpx.TimeoutException as e:
            raise OllamaError(
                "timeout",
                "Mô hình không phản hồi kịp (quá thời gian chờ).",
                "CPU có thể đang bận; thử lại sau ít phút.",
            ) from e
        except httpx.HTTPError as e:
            raise self._conn_error() from e
        except asyncio.CancelledError:
            log.info("chat stream cancelled")
            raise
