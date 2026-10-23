import os
import time

import numpy as np
import pytest

from app.sessions import InvalidSession, SessionManager, validate_session_id
from app.store import DocInfo


@pytest.mark.parametrize("bad", [None, "", "short", "../../etc/passwd", "..%2F..", "a/b/cdefghij", "C:\\Windows\\x",
                                 "abc def ghij", "x" * 65, "ổđấy123456"])
def test_invalid_session_ids(bad):
    with pytest.raises(InvalidSession):
        validate_session_id(bad)


def test_valid_session_id():
    assert validate_session_id("3f2a9c1e-7b6d-4e1a-9c2b-1234567890ab")


def _age(path, t):
    for f in path.iterdir():
        os.utime(f, (t, t))
    os.utime(path, (t, t))


def _fill(mgr, sid):
    st = mgr.get(sid)
    st.add_document(DocInfo("d1", "a.pdf", "pdf", 1, 1, 1, 1, 1), [{"text": "nội dung bí mật", "page": 1}],
                    np.ones((1, 4), np.float32))
    mgr.touch(sid)
    return st


def test_sessions_are_isolated_and_delete_removes_from_disk(tmp_path):
    mgr = SessionManager(tmp_path, ttl_days=7)
    _fill(mgr, "sessionAAAA")
    mgr.get("sessionBBBB")
    assert (tmp_path / "sessionAAAA" / "vectors.npy").exists()
    assert mgr.get("sessionBBBB").chunks == []

    assert mgr.delete("sessionAAAA")
    assert not (tmp_path / "sessionAAAA").exists()
    # tạo lại phiên sau khi xóa: trống, không còn dữ liệu cũ trong bộ nhớ
    assert mgr.get("sessionAAAA").chunks == []


def test_cleanup_removes_only_old_sessions(tmp_path):
    mgr = SessionManager(tmp_path, ttl_days=1)
    _fill(mgr, "oldsession1")
    _fill(mgr, "newsession1")
    old = time.time() - 3 * 86400
    _age(tmp_path / "oldsession1", old)
    # thư mục lạ (không phải session id) không bị đụng tới
    (tmp_path / "not a session").mkdir()
    removed = mgr.cleanup()
    assert removed == 1
    assert not (tmp_path / "oldsession1").exists()
    assert (tmp_path / "newsession1").exists()
    assert (tmp_path / "not a session").exists()


def test_cleanup_skips_busy_session(tmp_path):
    mgr = SessionManager(tmp_path, ttl_days=1)
    _fill(mgr, "busysession")
    old = time.time() - 3 * 86400
    _age(tmp_path / "busysession", old)
    mgr.mark_busy("busysession", 1)
    assert mgr.cleanup() == 0
    mgr.mark_busy("busysession", -1)
    assert mgr.cleanup() == 1
