from pathlib import Path

import numpy as np
import pytest
from scipy import stats as sps

from ffp import params
from ffp.copula import correlated_uniforms
from ffp.distributions import Poisson, receiving_yards
from ffp.scoring import load_rules
from ffp.simulate import DEFAULT_SEED, ScoreDistribution, sample_joint, sample_stats, simulate_scores


class DiscreteDist:
    """Test-double Stat Distribution over explicit integer values with an explicit pmf."""

    def __init__(self, values, pmf):
        self.values = np.asarray(values, dtype=float)
        self.pmf = np.asarray(pmf, dtype=float)
        self.cdf = np.cumsum(self.pmf)

    def sf(self, x):
        x = np.asarray(x)
        return np.array([self.pmf[self.values >= xi].sum() for xi in np.ravel(x)]).reshape(x.shape)

    def ppf(self, u):
        idx = np.searchsorted(self.cdf, np.asarray(u), side="left")
        return self.values[np.minimum(idx, len(self.values) - 1)]

    @property
    def mean(self):
        return float((self.values * self.pmf).sum())


# Receiving yards: 0 with 10%, then a spread of values up to 150.
REC_YDS = DiscreteDist([0, 20, 40, 60, 80, 100, 150], [0.1, 0.1, 0.2, 0.2, 0.2, 0.1, 0.1])
RECEPTIONS = DiscreteDist([0, 2, 4, 6, 8], [0.1, 0.2, 0.3, 0.3, 0.1])

ESPN_FILE = Path(__file__).resolve().parents[1] / "src" / "ffp" / "rules" / "espn-league.json"
ESPN = load_rules(ESPN_FILE)  # the bundled file by path, so a local scoring/ copy can't change it


def test_same_seed_gives_identical_percentiles():
    dists = {"receiving_yards": REC_YDS, "receptions": RECEPTIONS}
    a = simulate_scores(dists, ESPN, n=5_000, seed=123)
    b = simulate_scores(dists, ESPN, n=5_000, seed=123)
    assert a.percentiles() == b.percentiles()


def test_percentiles_are_ordered():
    dists = {"receiving_yards": REC_YDS, "receptions": RECEPTIONS}
    p = simulate_scores(dists, ESPN, n=5_000, seed=7).percentiles()
    assert list(p) == [5, 25, 50, 75, 95]
    assert p[5] <= p[25] <= p[50] <= p[75] <= p[95]
    assert p[5] < p[95]


def test_receiving_yards_only_scores_a_tenth_of_a_point_per_yard_under_espn():
    dists = {"receiving_yards": REC_YDS}
    samples = sample_stats(dists, 2_000, np.random.default_rng(99))
    result = simulate_scores(dists, ESPN, n=2_000, seed=99)
    np.testing.assert_allclose(result.points, 0.1 * samples["receiving_yards"])


class PointMass:
    """Test-double Stat Distribution that always takes one value."""

    def __init__(self, value):
        self.value = float(value)

    def sf(self, x):
        return np.where(np.asarray(x) <= self.value, 1.0, 0.0)

    def ppf(self, u):
        return np.full(np.shape(u), self.value)

    @property
    def mean(self):
        return self.value


def test_point_mass_gives_equal_percentiles_at_that_score():
    # 7 receptions * 1 + 85 yards * 0.1 + 1 TD * 6 = 7 + 8.5 + 6 = 21.5
    dists = {"receptions": PointMass(7), "receiving_yards": PointMass(85), "receiving_tds": PointMass(1)}
    result = simulate_scores(dists, ESPN, n=1_000)
    for value in result.percentiles().values():
        assert value == pytest.approx(21.5)
    assert result.mean == pytest.approx(21.5)


def test_chance_of_at_least_matches_the_samples():
    result = simulate_scores({"receiving_yards": REC_YDS}, ESPN, n=5_000, seed=3)
    for points in (0, 4, 6, 8, 10, 15, 100):
        assert result.chance_of_at_least(points) == pytest.approx(np.mean(result.points >= points))
    # Points take values 0, 2, 4, ..., 15 exactly; P(points >= 8) is P(yards >= 80) = 0.4.
    assert result.chance_of_at_least(8) == pytest.approx(0.4, abs=0.03)


def test_percentile_k_has_k_percent_at_or_below_it():
    # Pk is the smallest sample x with P(points <= x) >= k%.
    dist = ScoreDistribution(np.arange(1.0, 21.0))  # 1..20, each 5%
    assert dist.percentile(5) == 1.0
    assert dist.percentile(25) == 5.0
    assert dist.percentile(50) == 10.0
    assert dist.percentile(95) == 19.0


def test_injected_uniforms_are_used():
    u = np.array([0.05, 0.15, 0.95, 0.5])
    # REC_YDS cdf: 0.1, 0.2, 0.4, 0.6, 0.8, 0.9, 1.0 -> u maps to 0, 20, 150, 60.
    samples = sample_stats(
        {"receiving_yards": REC_YDS, "receptions": RECEPTIONS},
        4,
        np.random.default_rng(0),
        uniforms={"receiving_yards": u},
    )
    np.testing.assert_array_equal(samples["receiving_yards"], [0, 20, 150, 60])
    assert samples["receptions"].shape == (4,)


def test_defaults_are_20000_samples_and_a_fixed_seed():
    dists = {"receiving_yards": REC_YDS}
    default = simulate_scores(dists, ESPN)
    assert default.points.shape == (20_000,)
    np.testing.assert_array_equal(default.points, simulate_scores(dists, ESPN, n=20_000, seed=DEFAULT_SEED).points)


# --- Correlated sampling (Gaussian copula, S3.3 / S3.6) ------------------------------

WR_STATS = ["receptions", "receiving_yards", "receiving_tds", "rushing_yards"]
WR_R = np.array(
    [
        [1.00, 0.77, 0.35, 0.05],
        [0.77, 1.00, 0.43, -0.00],
        [0.35, 0.43, 1.00, -0.03],
        [0.05, -0.00, -0.03, 1.00],
    ]
)


class ScipyDist:
    """Test-double Stat Distribution wrapping a frozen scipy distribution."""

    def __init__(self, frozen):
        self.frozen = frozen

    def sf(self, x):
        """P(Y >= x), the protocol's meaning; scipy's sf is P(Y > x), which differs for counts."""
        x = np.asarray(x, dtype=float)
        if isinstance(self.frozen.dist, sps.rv_discrete):
            return self.frozen.sf(np.ceil(x) - 1)
        return self.frozen.sf(x)

    def ppf(self, u):
        return self.frozen.ppf(np.asarray(u, dtype=float))

    @property
    def mean(self):
        return float(self.frozen.mean())


def nb(mu, r):
    """Negative binomial receptions with mean mu and size r (scipy: n=r, p=r/(r+mu))."""
    return ScipyDist(sps.nbinom(r, r / (r + mu)))


def continuous_wr():
    """Continuous margins for every WR stat, so the Spearman of the samples tracks R."""
    return {s: ScipyDist(sps.gamma(a=2 + i, scale=10)) for i, s in enumerate(WR_STATS)}


def discrete_wr(mu_rec=5.0, r=16.4, yards_mu=60.0, yards_sd=30.0, td_lam=0.35):
    """WR-like margins; the yards pi0 is P(receptions = 0), as the integrator will set it."""
    rec = nb(mu_rec, r)
    return {
        "receptions": rec,
        "receiving_yards": receiving_yards(yards_mu, yards_sd, "WR", pi0=float(rec.frozen.pmf(0))),
        "receiving_tds": Poisson(td_lam),
        "rushing_yards": ScipyDist(sps.gamma(a=1.5, scale=4)),
        "fumbles_lost": Poisson(0.05),
    }


def te2():
    return discrete_wr(mu_rec=1.5, r=1.61 * 1.5**1.45, yards_mu=15.0, yards_sd=12.0, td_lam=0.15)


def test_spearman_of_samples_matches_the_input_matrix_for_continuous_margins():
    samples = sample_joint(continuous_wr(), 40_000, np.random.default_rng(10), corr=(WR_STATS, WR_R))
    rho_s = sps.spearmanr(np.column_stack([samples[s] for s in WR_STATS])).statistic
    np.testing.assert_allclose(rho_s, WR_R, atol=0.03)


def test_discrete_samples_are_each_margins_ppf_of_the_copula_uniforms():
    # Without receptions the Rec = 0 rule can't fire, so this is pure ppf(u).
    names = ["receiving_yards", "receiving_tds"]
    dists = {s: discrete_wr()[s] for s in names}
    r = WR_R[1:3, 1:3]
    samples = sample_joint(dists, 2_000, np.random.default_rng(11), corr=(names, r))
    u = correlated_uniforms(names, r, 2_000, np.random.default_rng(11))
    for s in names:
        np.testing.assert_array_equal(samples[s], dists[s].ppf(u[s]))


def test_marginals_are_unchanged_by_the_copula():
    dists = discrete_wr()
    joint = sample_joint(dists, 20_000, np.random.default_rng(12), corr=(WR_STATS, WR_R))
    indep = sample_stats(dists, 20_000, np.random.default_rng(13))
    # The Rec = 0 rule never touches these three.
    for s in ("receptions", "rushing_yards", "fumbles_lost"):
        assert sps.ks_2samp(joint[s], indep[s]).pvalue > 0.001, s


def test_marginals_of_receiving_stats_are_unchanged_without_receptions():
    dists = {s: d for s, d in discrete_wr().items() if s != "receptions"}
    joint = sample_joint(dists, 20_000, np.random.default_rng(14), corr=(WR_STATS, WR_R))
    indep = sample_stats(dists, 20_000, np.random.default_rng(15))
    for s in dists:
        assert sps.ks_2samp(joint[s], indep[s]).pvalue > 0.001, s


@pytest.mark.parametrize("make", [discrete_wr, te2])
def test_receiving_yards_and_tds_are_zero_whenever_receptions_are_zero(make):
    dists = make()
    samples = sample_joint(dists, 20_000, np.random.default_rng(16), corr=(WR_STATS, WR_R))
    no_catch = samples["receptions"] == 0
    assert no_catch.any()
    assert np.all(samples["receiving_yards"][no_catch] == 0)
    assert np.all(samples["receiving_tds"][no_catch] == 0)
    # Games with a catch keep the copula's ordering: they are re-ranked among themselves,
    # so a higher copula draw never gives fewer yards.
    u = correlated_uniforms(WR_STATS, WR_R, 20_000, np.random.default_rng(16))
    order = np.argsort(u["receiving_yards"][~no_catch])
    assert np.all(np.diff(samples["receiving_yards"][~no_catch][order]) >= 0)


def test_the_rec_zero_rule_keeps_a_wr_yards_marginal_close_when_pi0_is_p_rec_zero():
    dists = discrete_wr()
    joint = sample_joint(dists, 40_000, np.random.default_rng(20), corr=(WR_STATS, WR_R))
    indep = sample_stats(dists, 40_000, np.random.default_rng(21))
    assert sps.ks_2samp(joint["receiving_yards"], indep["receiving_yards"]).statistic < 0.02


def test_stats_outside_the_matrix_are_sampled_independently():
    samples = sample_joint(discrete_wr(), 40_000, np.random.default_rng(17), corr=(WR_STATS, WR_R))
    assert abs(sps.spearmanr(samples["fumbles_lost"], samples["receptions"]).statistic) < 0.02


def test_matrix_stats_the_player_lacks_are_skipped():
    dists = {"receptions": nb(5, 16.4), "receiving_yards": ScipyDist(sps.gamma(a=3, scale=20))}
    samples = sample_joint(dists, 20_000, np.random.default_rng(18), corr=(WR_STATS, WR_R))
    assert set(samples) == set(dists)
    assert sps.spearmanr(samples["receptions"], samples["receiving_yards"]).statistic > 0.6


def test_an_invalid_matrix_fails_even_if_the_player_uses_part_of_it():
    bad = WR_R.copy()
    bad[3, 3] = 2.0
    with pytest.raises(ValueError, match="unit diagonal"):
        sample_joint({"receptions": nb(5, 16.4)}, 10, np.random.default_rng(0), corr=(WR_STATS, bad))


def test_correlated_simulation_is_deterministic():
    a = simulate_scores(discrete_wr(), ESPN, n=5_000, seed=5, corr=(WR_STATS, WR_R))
    b = simulate_scores(discrete_wr(), ESPN, n=5_000, seed=5, corr=(WR_STATS, WR_R))
    np.testing.assert_array_equal(a.points, b.points)


def test_correlation_widens_the_score_distribution():
    # Positively correlated receptions, yards and TDs spread the score more than independence.
    dists = discrete_wr()
    indep = simulate_scores(dists, ESPN, n=20_000, seed=6)
    joint = simulate_scores(dists, ESPN, n=20_000, seed=6, corr=(WR_STATS, WR_R))
    assert joint.points.std() > 1.1 * indep.points.std()
    assert joint.mean == pytest.approx(indep.mean, rel=0.03)


def test_no_correlation_is_the_independent_simulation():
    dists = discrete_wr()
    independent = sample_stats(dists, 2_000, np.random.default_rng(8))
    joint = sample_joint(dists, 2_000, np.random.default_rng(8), corr=None)
    for s in dists:
        np.testing.assert_array_equal(joint[s], independent[s])
    np.testing.assert_array_equal(
        simulate_scores(dists, ESPN, n=2_000, seed=8).points,
        simulate_scores(dists, ESPN, n=2_000, seed=8, corr=None).points,
    )


CLEARLY_DIFFERENT = {
    "source": "test",
    "default": "within",
    "within": {"WR": {"stats": ["receptions", "receiving_yards"], "matrix": [[1, 0.8], [0.8, 1]]}},
    "pooled": {"WR": {"stats": ["receptions", "receiving_yards"], "matrix": [[1, -0.3], [-0.3, 1]]}},
}


@pytest.mark.parametrize("kind, expected", [("within", 0.8), ("pooled", -0.3), (None, 0.8)])
def test_config_selects_the_correlation_matrix(monkeypatch, kind, expected):
    monkeypatch.setattr(params, "load", lambda name: CLEARLY_DIFFERENT)
    margin = sps.gamma(a=3, scale=10)
    dists = {s: ScipyDist(margin) for s in ("receptions", "receiving_yards")}
    samples = sample_joint(dists, 20_000, np.random.default_rng(19), corr=params.correlation("WR", kind))
    z = sps.norm.ppf(margin.cdf(np.vstack([samples["receptions"], samples["receiving_yards"]])))
    assert np.corrcoef(z)[0, 1] == pytest.approx(expected, abs=0.03)


@pytest.mark.parametrize("kind", ["within", "pooled"])
@pytest.mark.parametrize("position", ["WR", "TE"])
def test_bundled_matrices_drive_a_simulation(position, kind):
    result = simulate_scores(discrete_wr(), ESPN, n=2_000, seed=1, corr=params.correlation(position, kind))
    assert result.percentile(5) < result.percentile(95)



def te2(td_lam=0.15):
    """A backup TE: mu_rec 1.5 (r from the TE relation), yards pi0 = P(receptions = 0)."""
    rec = nb(1.5, 1.61 * 1.5**1.45)
    return {
        "receptions": rec,
        "receiving_yards": receiving_yards(15.0, 15.0, "TE", pi0=float(rec.frozen.pmf(0))),
        "receiving_tds": Poisson(td_lam),
    }


def test_the_no_catch_coupling_keeps_a_te2s_marginals():
    # Copula review: zeroing yards and TDs after the copula pushed a TE2's P(yards = 0) from
    # 0.30 to 0.39 (KS 0.09) and his mean yards down 7%. The coupling instead puts every
    # no-catch game on each stat's own zero mass and re-ranks the catch games into the rest.
    dists = te2()
    joint = sample_joint(dists, 100_000, np.random.default_rng(30), corr=(WR_STATS, WR_R))
    indep = sample_stats(dists, 100_000, np.random.default_rng(31))
    no_catch = joint["receptions"] == 0
    assert no_catch.mean() == pytest.approx(0.30, abs=0.01)
    assert np.all(joint["receiving_yards"][no_catch] == 0) and np.all(joint["receiving_tds"][no_catch] == 0)
    for stat in ("receiving_yards", "receiving_tds"):
        assert sps.ks_2samp(joint[stat], indep[stat]).statistic < 0.01, stat
    assert joint["receiving_yards"].mean() == pytest.approx(dists["receiving_yards"].mean, rel=0.02)
    assert (joint["receiving_tds"] >= 1).mean() == pytest.approx(1 - np.exp(-0.15), abs=0.005)
    # ...and the dependence survives.
    assert sps.spearmanr(joint["receptions"], joint["receiving_yards"]).statistic > 0.6


def test_a_td_rate_too_high_for_the_no_catch_games_still_keeps_them_at_zero():
    # P(TD = 0) = e^-1.5 = 0.22 is below P(receptions = 0) = 0.30: the TD marginal can't be
    # kept exactly, but the structural rule still holds and nothing breaks.
    joint = sample_joint(te2(td_lam=1.5), 50_000, np.random.default_rng(32), corr=(WR_STATS, WR_R))
    no_catch = joint["receptions"] == 0
    assert np.all(joint["receiving_tds"][no_catch] == 0)
    assert (joint["receiving_tds"] == 0).mean() >= no_catch.mean()
