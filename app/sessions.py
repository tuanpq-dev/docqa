"""Quản lý phiên: kiểm tra session id, tách thư mục dữ liệu, xóa và tự dọn phiên cũ."""
from __future__ import annotations

import logging
import re
import shutil
import threading
import time
from pathlib import Path

from .store import SessionStore

log = logging.getLogger("docqa.sessions")

# Chỉ chữ, số, '-', '_' → không thể chứa '/', '..', ký tự ổ đĩa Windows...
SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
TOUCH_FILE = ".last_access"


class InvalidSession(ValueError):
    pass


def validate_session_id(sid: str | None) -> str:
    if not sid or not SESSION_RE.fullmatch(sid):
        raise InvalidSession("Session id không hợp lệ.")
    return sid


def short(sid: str) -> str:
    """Rút gọn session id cho log."""
    return sid[:6] + "…"


class SessionManager:
    def __init__(self, root: Path, ttl_days: float):
        self.root = root.resolve()
        self.ttl_seconds = ttl_days * 86400
        self.root.mkdir(parents=True, exist_ok=True)
        self._stores: dict[str, SessionStore] = {}
        self._lock = threading.Lock()
        self._busy: dict[str, int] = {}

    def path_for(self, sid: str) -> Path:
        sid = validate_session_id(sid)
        p = (self.root / sid).resolve()
        # phòng thủ thêm một lớp: đường dẫn phải nằm ngay trong root
        if p.parent != self.root:
            raise InvalidSession("Session id không hợp lệ.")
        return p

    def get(self, sid: str) -> SessionStore:
        path = self.path_for(sid)
        with self._lock:
            st = self._stores.get(sid)
            if st is None:
                st = SessionStore(path)
                self._stores[sid] = st
        self.touch(sid)
        return st

    def touch(self, sid: str):
        path = self.path_for(sid)
        if path.is_dir():
            try:
                (path / TOUCH_FILE).touch()
            except OSError:
                pass

    def last_access(self, path: Path) -> float:
        times = []
        for f in (path / TOUCH_FILE, path / "meta.json", path):
            try:
                times.append(f.stat().st_mtime)
            except OSError:
                pass
        return max(times) if times else 0.0

    def mark_busy(self, sid: str, delta: int):
        with self._lock:
            self._busy[sid] = self._busy.get(sid, 0) + delta
            if self._busy[sid] <= 0:
                self._busy.pop(sid, None)

    def delete(self, sid: str) -> bool:
        path = self.path_for(sid)
        with self._lock:
            st = self._stores.pop(sid, None)
        existed = path.exists()
        if st is not None:
            with st.lock:
                shutil.rmtree(path, ignore_errors=True)
        else:
            shutil.rmtree(path, ignore_errors=True)
        log.info("session %s deleted", short(sid))
        return existed

    def cleanup(self, now: float | None = None) -> int:
        """Xóa các phiên không dùng quá ttl. Trả về số phiên đã xóa."""
        if self.ttl_seconds <= 0:
            return 0
        now = now or time.time()
        removed = 0
        for child in self.root.iterdir():
            if not child.is_dir() or not SESSION_RE.fullmatch(child.name):
                continue
            if child.name in self._busy:
                continue
            if now - self.last_access(child) > self.ttl_seconds:
                self.delete(child.name)
                removed += 1
        if removed:
            log.info("cleanup removed %d old session(s)", removed)
        return removed
