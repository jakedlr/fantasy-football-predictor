"""S11.2: each result's Score Distribution mean, its Expected Points under other Scoring Rules (the same
draws scored again) and the mean of each Scoring Stat in the draws."""

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from ffp.predict import load_fixture_books, load_fixture_nflverse, predict, resolve_targets
from ffp.scoring import DST_STATS, KICKER_STATS, SCORING_STATS, SLEEPER_FG_BUCKETS, load_rules, score_dst

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"
BEFORE_MNF = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
PPR = load_rules("ppr")
OTHERS = [load_rules(name) for name in ("half-ppr", "standard", "espn-league")]


@pytest.fixture(scope="module")
def results():
    roster, schedule = load_fixture_nflverse(FIXTURES, 2026)
    targets, skipped = resolve_targets(["Zay Flowers", "Justin Herbert", "Cameron Dicker", "Ravens D/ST"],
                                       roster, schedule, BEFORE_MNF)
    assert skipped == []
    found, unfitted = predict(targets, load_fixture_books(FIXTURES), PPR, also_score=OTHERS)
    assert unfitted == []
    return {r.player: r for r in found}


def test_the_mean_is_the_expected_points_under_the_run_s_rules(results):
    for r in results.values():
        assert set(r.expected) == {"ppr", "half-ppr", "standard", "espn-league"}
        assert r.mean == r.expected["ppr"]
        assert r.percentiles[5] <= r.mean <= r.percentiles[95]


def test_without_other_rules_only_the_run_s_own_is_scored():
    roster, schedule = load_fixture_nflverse(FIXTURES, 2026)
    targets, _ = resolve_targets(["Zay Flowers"], roster, schedule, BEFORE_MNF)
    [zay], _ = predict(targets, load_fixture_books(FIXTURES), PPR)
    assert set(zay.expected) == {"ppr"} and zay.mean == zay.expected["ppr"]


def _linear(stat_means, rules):
    return sum(rules.offense[s] * v for s, v in stat_means.items())


def test_offense_stat_means_are_the_scoring_stats_and_score_to_the_expected_points(results):
    zay = results["Zay Flowers"]
    assert set(zay.stat_means) <= set(SCORING_STATS)
    # Base Rate stats are in the draws too.
    assert {"receptions", "receiving_yards", "receiving_tds", "fumbles_lost", "receiving_2pt"} <= set(zay.stat_means)
    for rules in (PPR, *OTHERS):  # offense scoring is linear: E[points] = Σ points per unit × E[stat]
        assert zay.expected[rules.name] == pytest.approx(_linear(zay.stat_means, rules), rel=1e-9)
    assert zay.expected["ppr"] - zay.expected["standard"] == pytest.approx(zay.stat_means["receptions"])
    herbert = results["Justin Herbert"]
    assert {"passing_yards", "passing_tds", "passing_interceptions", "rushing_yards"} <= set(herbert.stat_means)
    assert herbert.expected["ppr"] == herbert.expected["standard"]  # QB receptions are not modelled


def test_kicker_stat_means_are_pats_misses_and_fgs_by_sleeper_bucket(results):
    k = results["Cameron Dicker"]
    assert set(k.stat_means) == {*KICKER_STATS, *SLEEPER_FG_BUCKETS}
    assert all(v >= 0 for v in k.stat_means.values())
    assert k.stat_means["fgm_0_19"] < 0.05 < k.stat_means["fgm_40_49"]
    # The buckets break at every tier edge of ESPN's kicker rules, so the means score exactly.
    m = k.stat_means
    fg = 3 * (m["fgm_0_19"] + m["fgm_20_29"] + m["fgm_30_39"]) + 4 * m["fgm_40_49"] + 5 * (m["fgm_50_59"] + m["fgm_60p"])
    assert k.expected["ppr"] == pytest.approx(m["pat_made"] - m["fg_missed"] + fg, rel=1e-9)
    assert k.expected["ppr"] == k.expected["half-ppr"] == k.expected["standard"]


def test_dst_stat_means_are_its_counts_and_what_it_allowed(results):
    d = results["Ravens D/ST"]
    allowed = {"points_allowed_all", "opponent_defensive_tds_points", "opponent_return_tds_points", "yards_allowed"}
    assert set(d.stat_means) == {*DST_STATS, *allowed}
    assert 10 < d.stat_means["points_allowed_all"] < 35 and 200 < d.stat_means["yards_allowed"] < 450
    assert all(v >= 0 for v in d.stat_means.values())
    # Tiers are nonlinear, so each Scoring Rules option is scored on the draws, not the means.
    assert d.expected["ppr"] == d.expected["standard"] == d.mean
    assert abs(d.expected["ppr"] - score_dst(d.stat_means, PPR)) > 0.1
    sleeper = load_rules("sleeper")  # other tiers, forced fumbles: another Expected Points from the same draws
    [again], _ = predict(_targets(["Ravens D/ST"]), load_fixture_books(FIXTURES), PPR, also_score=[sleeper])
    assert again.expected["ppr"] == d.expected["ppr"] and again.expected["sleeper"] != d.expected["ppr"]


def _targets(names):
    roster, schedule = load_fixture_nflverse(FIXTURES, 2026)
    targets, _ = resolve_targets(names, roster, schedule, BEFORE_MNF)
    return targets


def test_a_result_s_draws_are_scored_once_per_scoring_rules_not_refitted(results, monkeypatch):
    import ffp.predict

    calls = []
    real = ffp.predict.simulate_scores
    monkeypatch.setattr(ffp.predict, "simulate_scores", lambda *a, **k: calls.append(1) or real(*a, **k))
    roster, schedule = load_fixture_nflverse(FIXTURES, 2026)
    targets, _ = resolve_targets(["Zay Flowers"], roster, schedule, BEFORE_MNF)
    [zay], _ = predict(targets, load_fixture_books(FIXTURES), PPR, also_score=OTHERS)
    assert len(calls) == 1
    assert zay.expected == results["Zay Flowers"].expected


def test_also_scoring_does_not_change_the_percentiles(results):
    roster, schedule = load_fixture_nflverse(FIXTURES, 2026)
    targets, _ = resolve_targets(["Zay Flowers"], roster, schedule, BEFORE_MNF)
    [zay], _ = predict(targets, load_fixture_books(FIXTURES), PPR)
    assert zay.percentiles == results["Zay Flowers"].percentiles
    assert np.isclose(zay.mean, results["Zay Flowers"].mean)


def test_worker_processes_score_the_other_rules_too(results):
    roster, schedule = load_fixture_nflverse(FIXTURES, 2026)
    targets, _ = resolve_targets(["Zay Flowers", "Justin Herbert"], roster, schedule, BEFORE_MNF)
    found, _ = predict(targets, load_fixture_books(FIXTURES), PPR, also_score=OTHERS, jobs=2)
    for r in found:
        assert r.expected == results[r.player].expected
        assert r.stat_means == results[r.player].stat_means
