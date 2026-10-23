import numpy as np

from app.analysis import fit_pca, near_duplicates


def test_pca_recovers_main_axes():
    rng = np.random.default_rng(1)
    n = 200
    x = np.zeros((n, 10), dtype=np.float32)
    x[:, 0] = rng.normal(scale=10, size=n)
    x[:, 3] = rng.normal(scale=3, size=n)
    x += rng.normal(scale=0.01, size=x.shape)
    p = fit_pca(x)
    assert p.coords.shape == (n, 2)
    assert abs(abs(p.components[0, 0]) - 1) < 1e-2
    assert abs(abs(p.components[1, 3]) - 1) < 1e-2
    assert p.explained[0] > p.explained[1] > 0
    assert sum(p.explained) <= 1.0001
    # chiếu lại chính dữ liệu cho ra toạ độ giống hệt
    assert np.allclose(p.project(x), p.coords, atol=1e-3)


def test_pca_matches_covariance_eigen():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(50, 6)) @ rng.normal(size=(6, 6))
    p = fit_pca(x)
    cov = np.cov(x - x.mean(0), rowvar=False)
    w, v = np.linalg.eigh(cov)
    top = v[:, np.argsort(-w)[:2]].T
    for i in range(2):
        assert abs(abs(float(top[i] @ p.components[i])) - 1) < 1e-4


def test_pca_edge_cases():
    assert fit_pca(np.zeros((0, 0))).coords.shape == (0, 2)
    assert fit_pca(np.ones((1, 4))).coords.shape == (1, 2)
    p = fit_pca(np.array([[0, 0, 1], [1, 0, 0]], dtype=np.float32))
    assert p.coords.shape == (2, 2)


def test_near_duplicates():
    a = np.array([1, 0, 0, 0], dtype=np.float32)
    x = np.stack([a, a * 2 + 0.01, np.array([0, 1, 0, 0]), np.array([0, 0, 1, 0])])
    pairs = near_duplicates(x, 0.95, block=2)
    assert [(i, j) for i, j, _ in pairs] == [(0, 1)]
    assert near_duplicates(x[:1]) == []
