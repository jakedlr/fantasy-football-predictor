import numpy as np
import pytest
from scipy import stats

from ffp.copula import correlated_uniforms, submatrix

STATS = ["receptions", "receiving_yards", "receiving_tds", "rushing_yards"]
# WR latent R from research/nflverse-data.md 3c.
WR_R = np.array(
    [
        [1.00, 0.77, 0.35, 0.05],
        [0.77, 1.00, 0.43, -0.00],
        [0.35, 0.43, 1.00, -0.03],
        [0.05, -0.00, -0.03, 1.00],
    ]
)


def as_matrix(u: dict[str, np.ndarray], names) -> np.ndarray:
    return np.column_stack([u[s] for s in names])


def test_returns_one_array_of_n_uniforms_per_stat():
    u = correlated_uniforms(STATS, WR_R, 1_000, np.random.default_rng(0))
    assert list(u) == STATS
    for values in u.values():
        assert values.shape == (1_000,)
        assert np.all((values > 0) & (values < 1))


def test_latent_normal_scores_have_the_input_correlation():
    u = correlated_uniforms(STATS, WR_R, 40_000, np.random.default_rng(1))
    z = stats.norm.ppf(as_matrix(u, STATS))
    np.testing.assert_allclose(np.corrcoef(z, rowvar=False), WR_R, atol=0.03)


def test_spearman_of_uniforms_matches_the_input_matrix():
    # Continuous margins: Spearman = (6/pi) asin(rho/2), within 0.02 of rho for any rho.
    u = correlated_uniforms(STATS, WR_R, 40_000, np.random.default_rng(2))
    rho_s = stats.spearmanr(as_matrix(u, STATS)).statistic
    np.testing.assert_allclose(rho_s, WR_R, atol=0.03)


def test_each_uniform_is_uniform():
    u = correlated_uniforms(STATS, WR_R, 20_000, np.random.default_rng(3))
    for values in u.values():
        assert stats.kstest(values, "uniform").pvalue > 0.001


def test_same_seed_gives_identical_uniforms():
    a = correlated_uniforms(STATS, WR_R, 500, np.random.default_rng(42))
    b = correlated_uniforms(STATS, WR_R, 500, np.random.default_rng(42))
    for s in STATS:
        np.testing.assert_array_equal(a[s], b[s])


def test_a_single_stat_is_just_uniform():
    u = correlated_uniforms(["receptions"], np.array([[1.0]]), 1_000, np.random.default_rng(4))
    assert list(u) == ["receptions"]
    assert stats.kstest(u["receptions"], "uniform").pvalue > 0.001


@pytest.mark.parametrize(
    "corr, message",
    [
        (np.ones((2, 3)), "square"),
        (np.array([[1.0, 0.5], [0.4, 1.0]]), "symmetric"),
        (np.array([[2.0, 0.5], [0.5, 1.0]]), "unit diagonal"),
        (np.array([[1.0, 1.2], [1.2, 1.0]]), "positive definite"),
        (np.array([[1.0, 1.0], [1.0, 1.0]]), "positive definite"),
        (np.array([[1.0, np.nan], [np.nan, 1.0]]), "finite"),
    ],
)
def test_invalid_matrices_are_rejected(corr, message):
    with pytest.raises(ValueError, match=message):
        correlated_uniforms(["a", "b"], corr, 10, np.random.default_rng(0))


def test_stat_count_must_match_the_matrix():
    with pytest.raises(ValueError, match="3 stats"):
        correlated_uniforms(["a", "b", "c"], np.eye(2), 10, np.random.default_rng(0))


def test_duplicate_stats_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        correlated_uniforms(["a", "a"], np.eye(2), 10, np.random.default_rng(0))


def test_submatrix_keeps_matrix_order_and_drops_unknown_stats():
    names, r = submatrix(STATS, WR_R, ["receiving_tds", "fumbles_lost", "receptions"])
    assert names == ["receptions", "receiving_tds"]
    np.testing.assert_array_equal(r, [[1.0, 0.35], [0.35, 1.0]])


def test_submatrix_of_nothing_is_empty():
    names, r = submatrix(STATS, WR_R, ["fumbles_lost"])
    assert names == []
    assert r.shape == (0, 0)


def test_submatrix_of_everything_is_the_whole_matrix():
    names, r = submatrix(STATS, WR_R, set(STATS))
    assert names == STATS
    np.testing.assert_array_equal(r, WR_R)
