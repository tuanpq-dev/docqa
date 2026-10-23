import numpy as np

from app.store import DocInfo, SessionStore, top_k_cosine


def test_top_k_cosine_order_and_scores():
    m = np.array([[1, 0, 0], [0, 1, 0], [0.9, 0.1, 0], [-1, 0, 0]], dtype=np.float32)
    idx, scores = top_k_cosine(m, np.array([1, 0, 0]), 3)
    assert list(idx) == [0, 2, 1]
    assert abs(scores[0] - 1.0) < 1e-6
    assert scores[0] >= scores[1] >= scores[2]


def test_top_k_larger_than_n_and_unnormalized():
    m = np.array([[3, 0], [0, 5]], dtype=np.float32)
    idx, scores = top_k_cosine(m, np.array([0, 2]), 10)
    assert list(idx) == [1, 0]
    assert np.allclose(scores, [1.0, 0.0])


def test_top_k_empty():
    idx, scores = top_k_cosine(np.zeros((0, 0), np.float32), np.array([1.0]), 5)
    assert len(idx) == 0 and len(scores) == 0


def _doc(i, n):
    return DocInfo(id=f"d{i}", name=f"f{i}.pdf", kind="pdf", pages=1, size=10, n_chunks=n, chars=100, tokens=30)


def test_store_persist_search_and_remove(tmp_path):
    st = SessionStore(tmp_path / "s1")
    rng = np.random.default_rng(0)
    v1 = rng.normal(size=(3, 8)).astype(np.float32)
    v2 = rng.normal(size=(2, 8)).astype(np.float32)
    st.add_document(_doc(1, 3), [{"text": f"a{i}", "page": i + 1} for i in range(3)], v1)
    st.add_document(_doc(2, 2), [{"text": f"b{i}", "page": None} for i in range(2)], v2)
    assert st.stats()["n_chunks"] == 5 and st.dim == 8

    reloaded = SessionStore(tmp_path / "s1")
    assert len(reloaded.chunks) == 5
    hit, score = reloaded.search(v2[1], 1)[0]
    assert hit["text"] == "b1" and score > 0.999
    assert reloaded.chunks[2]["pos"] == 1.0 and reloaded.chunks[0]["pos"] == 0.0

    assert reloaded.remove_document("d1")
    assert [c["text"] for c in reloaded.chunks] == ["b0", "b1"]
    assert reloaded.vectors.shape == (2, 8)
    assert not reloaded.remove_document("nope")
