"""ffp.backtest (S5.2): the Kalshi Backtest runner, against the S5.1 fixtures pulled into a tmp root
(research/fixtures/kalshi, through the pull code with recorded responses) and synthetic nflverse rows."""

import json
import math
from dataclasses import replace

import numpy as np
import pytest

from ffp import backtest
from ffp.backtest import (
    DNP,
    NO_MARKETS,
    NO_POSITION,
    OK,
    UNMODELED,
    Context,
    Positions,
    Task,
    evaluate,
    player_games,
    points_summary,
    render_tables,
    run,
    stat_summary,
    summarize,
    tasks,
    trailing_carry_shares,
)
from ffp.outcomes import OUTCOME_STATS
from ffp.sources.kalshi import load_fantasy_lines, load_fantasy_means, load_rungs, pull
from ffp.td_share import TdHistory, rushing_td_share
from test_kalshi_pull import FixtureHttp, rosters, schedule

CHASE, BROWN, PERINE, MAYE = "00-0036900", "00-0038597", "00-0033526", "00-0039851"
GAME = "2026_10_PIT_CIN"


@pytest.fixture(scope="module")
def root(tmp_path_factory):
    root = tmp_path_factory.mktemp("kalshi")
    pull(root, [2025, 2026], http=FixtureHttp(), schedule_fetch=schedule, roster_fetch=rosters)
    return root


@pytest.fixture(scope="module")
def games(root):
    return {pg.gsis_id: pg for pg in player_games(load_rungs(root))}


def outcome(**stats):
    return {s: 0.0 for s in OUTCOME_STATS} | stats


CHASE_GAME = outcome(receptions=8, receiving_yards=97, receiving_tds=1)


# --- inputs -------------------------------------------------------------------------------


def test_player_games_group_every_stat_of_a_player_in_a_game(games):
    chase = games[CHASE]
    assert (chase.game_id, chase.season, chase.week, chase.key) == (GAME, 2026, 10, f"{GAME}|{CHASE}")
    assert {r.stat for r in chase.rungs} == {"receiving_yards", "receptions", "rush_rec_tds"}
    assert len(chase.rungs) == 14 + 11 + 3


def test_position_is_the_roster_row_of_that_week_or_the_latest_before():
    positions = Positions([
        {"season": 2025, "week": 5, "gsis_id": "P1", "position": "WR"},
        {"season": 2026, "week": 3, "gsis_id": "P1", "position": "RB"},
        {"season": "2026", "week": "9", "gsis_id": "P1", "position": "TE"},
    ])
    assert positions.at("P1", 2025, 4) is None  # nothing yet
    assert positions.at("P1", 2025, 18) == "WR"
    assert positions.at("P1", 2026, 3) == "RB"
    assert positions.at("P1", 2026, 8) == "RB"  # not the later TE row: no look-ahead
    assert positions.at("P1", 2026, 9) == "TE"
    assert positions.at("P2", 2026, 9) is None


def test_trailing_carry_share_is_the_mean_over_up_to_four_earlier_games():
    def row(week, pid, carries, team="CIN"):
        return {"player_id": pid, "game_id": f"2026_{week:02d}", "season": 2026, "week": week,
                "team": team, "carries": carries}
    rows = [row(w, "RB1", 15) for w in range(1, 7)] + [row(w, "RB2", 5) for w in range(1, 7)]
    rows += [row(1, "OTHER", 30, team="PIT")]
    shares = trailing_carry_shares(rows)
    assert ("2026_01", "RB1") not in shares and ("2026_02", "RB1") not in shares  # < 2 earlier games
    assert shares["2026_03", "RB1"] == pytest.approx(0.75)
    assert shares["2026_06", "RB2"] == pytest.approx(0.25)


def ctx(outcomes, positions=None, history=None, root=None, shares=None):
    positions = positions or Positions(
        {"season": 2026, "week": 1, "gsis_id": pid, "position": pos}
        for pid, pos in [(CHASE, "WR"), (BROWN, "RB"), (PERINE, "RB"), ("00-0023459", "QB")])
    seen = []

    def td_history(season, week):
        seen.append((season, week))
        return history or {}

    c = Context(positions, outcomes, td_history, shares or {},
                {} if root is None else {(r.game_id, r.gsis_id): r for r in load_fantasy_lines(root)},
                {} if root is None else {(r.game_id, r.gsis_id): r for r in load_fantasy_means(root)})
    return c, seen


def test_tasks_skip_players_without_a_modelled_roster_position(games):
    positions = Positions([{"season": 2026, "week": 1, "gsis_id": CHASE, "position": "WR"},
                           {"season": 2026, "week": 1, "gsis_id": BROWN, "position": "K"}])
    c, _ = ctx({}, positions)
    todo, skipped = tasks([games[CHASE], games[BROWN], games[PERINE]], c)
    assert [t.game.gsis_id for t in todo] == [CHASE]
    assert {(r["gsis_id"], r["status"]) for r in skipped} == {(BROWN, UNMODELED), (PERINE, NO_POSITION)}


def test_tasks_take_the_td_share_from_history_before_the_game_and_the_fantasy_markets(games, root):
    history = {CHASE: TdHistory(carries=6, targets=400, receiving_tds=32)}
    c, seen = ctx({(GAME, CHASE): CHASE_GAME}, history=history, root=root)
    [task], _ = tasks([games[CHASE]], c)
    assert seen == [(2026, 10)]
    assert task.share == pytest.approx(rushing_td_share("WR", history[CHASE]))
    assert task.outcome == CHASE_GAME
    assert task.line == (19.9, pytest.approx(0.52))
    assert task.market_mean == pytest.approx(21.5)


def test_a_player_with_snaps_but_no_stat_row_scored_zero(games):
    # nflverse player stats have no row for a player who played without touching the ball
    # (a blocking TE, a WR never targeted): that is an all-zero outcome, not a DNP.
    c, _ = ctx({})
    c.played = {(GAME, CHASE)}
    [task], _ = tasks([games[CHASE]], c)
    assert task.outcome == outcome() and task.no_stat_row
    record = evaluate(task, samples=300, diagnostics=False)
    assert record["status"] == OK and record["no_stat_row"] and record["stats"]["receptions"]["y"] == 0


def test_snap_counts_map_to_gsis_ids_through_the_roster_pfr_id():
    snaps = [{"game_id": "G1", "pfr_player_id": "ChasJa00", "offense_snaps": 40.0},
             {"game_id": "G1", "pfr_player_id": "BlocTE00", "offense_snaps": 0.0},  # special teams only
             {"game_id": "G1", "pfr_player_id": "Unknown00", "offense_snaps": 10.0}]
    roster = [{"gsis_id": CHASE, "pfr_id": "ChasJa00"}, {"gsis_id": "P2", "pfr_id": "BlocTE00"}]
    assert backtest.played_games(snaps, roster) == {("G1", CHASE)}


# --- one player-game ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def chase_record(games, root):
    c, _ = ctx({(GAME, CHASE): CHASE_GAME}, root=root)
    [task], _ = tasks([games[CHASE]], c)
    return task, evaluate(task, samples=2000, seed=11)


def test_a_record_scores_each_stat_distribution_against_the_outcome(chase_record):
    task, record = chase_record
    assert record["status"] == OK and record["complete"] and record["position"] == "WR"
    assert set(record["stats"]) == {"receptions", "receiving_yards", "rush_rec_tds"}
    yds = record["stats"]["receiving_yards"]
    assert yds["y"] == 97 and record["stats"]["rush_rec_tds"]["y"] == 1
    assert len(yds["k"]) == len(yds["mid"]) == len(yds["model"]) == 14
    assert yds["fair"] == pytest.approx(yds["mid"], abs=1e-9)  # Kalshi: the mids are the Fair Probabilities
    assert 0 < yds["pit"] < 1 and yds["crps"] > 0
    assert yds["resid_rmse"] < 0.5  # a 2-parameter fit tracks a clean 2026 Ladder closely
    assert yds["sd"] > 0 and yds["sd_rel"] > 0
    assert "free" in yds and "lognormal" in yds  # the diagnostics


def test_a_record_holds_both_score_distributions_and_the_fantasy_markets(chase_record):
    _, record = chase_record
    pts = record["points"]
    assert pts["y"] == pytest.approx(8 + 9.7 + 6)  # Sleeper: full PPR
    assert pts["line"] == 19.9 and pts["line_mid"] == pytest.approx(0.52)
    assert pts["market_mean"] == pytest.approx(21.5)
    for model in ("copula", "independent"):
        m = pts[model]
        assert list(m["pct"]) == ["5", "10", "25", "50", "75", "90", "95"]
        assert m["pct"]["5"] <= m["pct"]["50"] <= m["pct"]["95"]
        assert 0 < m["p_over"] < 1 and 0 < m["pit"] < 1
    # Same margins and seed: the means agree, the spreads don't (Rec-RecYd correlation widens it).
    assert pts["copula"]["mean"] == pytest.approx(pts["independent"]["mean"], rel=0.03)
    assert pts["copula"]["pct"]["95"] - pts["copula"]["pct"]["5"] > pts["independent"]["pct"]["95"] - pts["independent"]["pct"]["5"]


def test_the_copula_draws_are_spread_over_each_margin(chase_record):
    # Regression: one joint draw of n = 1 put receiving yards at their median every time (the Rec = 0
    # coupling re-ranks the catch games within the draw), which faked a weak Rec-RecYd dependence.
    _, record = chase_record
    joint = record["joint"]
    assert len(joint["receptions"]) == len(joint["receiving_yards"]) == backtest.JOINT_DRAWS
    assert np.std(joint["receiving_yards"]) > 0.15  # U(0, 1) has sd 0.29
    za, zb = (np.array(joint[s]) for s in ("receptions", "receiving_yards"))
    assert np.corrcoef(za, zb)[0, 1] > 0.3


def test_a_record_is_reproducible(chase_record):
    task, record = chase_record
    assert evaluate(task, samples=2000, seed=11) == record
    assert json.loads(json.dumps(record)) == record  # JSON-able


def test_a_player_without_an_nflverse_row_did_not_play(games):
    c, _ = ctx({})
    [task], _ = tasks([games[CHASE]], c)
    assert evaluate(task, samples=500)["status"] == DNP


def test_a_carries_ladder_alone_is_no_markets(games):
    c, _ = ctx({(GAME, PERINE): outcome(carries=4, rushing_yards=12)})
    [task], _ = tasks([games[PERINE]], c)
    assert evaluate(task, samples=500)["status"] == NO_MARKETS


def test_an_rb_record_scores_carries_as_a_usage_stat(games):
    c, _ = ctx({(GAME, BROWN): outcome(carries=14, rushing_yards=60, receptions=4, receiving_yards=30)})
    [task], _ = tasks([games[BROWN]], c)
    record = evaluate(task, samples=500, diagnostics=False)
    assert record["stats"]["carries"]["y"] == 14
    assert "incomplete: missing rushing yards" in record["flags"] and not record["complete"]
    assert "joint" not in record


# --- the run ------------------------------------------------------------------------------


def test_run_caches_each_record_and_resumes(games, tmp_path, monkeypatch):
    c, _ = ctx({(GAME, pid): outcome(receptions=3, receiving_yards=30) for pid in (CHASE, BROWN)})
    todo, _ = tasks([games[CHASE], games[BROWN]], c)
    cache = tmp_path / "bt" / "records.jsonl"
    first = run(todo[:1], cache, samples=300, diagnostics=False, progress=lambda m: None)
    assert len(cache.read_text().splitlines()) == 1

    calls = []
    real = backtest.evaluate
    monkeypatch.setattr(backtest, "evaluate", lambda *a: calls.append(a[0].game.gsis_id) or real(*a))
    both = run(todo, cache, samples=300, diagnostics=False, progress=lambda m: None)
    assert calls == [BROWN]  # Chase came from the cache
    assert both[0] == first[0] and [r["gsis_id"] for r in both] == [CHASE, BROWN]
    assert len(cache.read_text().splitlines()) == 2


def test_run_recomputes_a_cached_record_whose_inputs_changed(games, tmp_path):
    # A pull that adds Rungs (a weekly top-up, or a pull still running) or a new sample count
    # must not leave a stale record in the cache.
    c, _ = ctx({(GAME, CHASE): CHASE_GAME})
    [task], _ = tasks([games[CHASE]], c)
    cache = tmp_path / "records.jsonl"
    quiet = {"diagnostics": False, "progress": lambda m: None}
    [full] = run([task], cache, samples=300, **quiet)
    fewer = Task(**{**task.__dict__, "game": replace(task.game, rungs=task.game.rungs[:-3])})
    [stale] = run([fewer], cache, samples=300, **quiet)
    assert "rush_rec_tds" not in stale["stats"] and stale["fingerprint"] != full["fingerprint"]
    [again] = run([task], cache, samples=400, **quiet)
    assert again["points"]["samples"] == 400


def test_run_in_parallel_gives_the_same_records(games, tmp_path):
    c, _ = ctx({(GAME, pid): outcome(receptions=3, receiving_yards=30) for pid in (CHASE, BROWN)})
    todo, _ = tasks([games[CHASE], games[BROWN]], c)
    serial = run(todo, tmp_path / "a.jsonl", samples=300, diagnostics=False, progress=lambda m: None)
    parallel = run(todo, tmp_path / "b.jsonl", samples=300, diagnostics=False, jobs=2, progress=lambda m: None)
    assert parallel == serial


# --- summaries ----------------------------------------------------------------------------


def test_stat_summary_scores_our_probabilities_and_the_mids_at_the_rungs():
    rows = [
        {"y": 5, "k": [3, 6], "mid": [0.8, 0.3], "fair": [0.8, 0.3], "model": [0.7, 0.4], "pit": 0.4, "crps": 1.0, "mean": 4.0},
        {"y": 2, "k": [3], "mid": [0.6], "fair": [0.6], "model": [0.5], "pit": 0.1, "crps": 2.0, "mean": 4.0},
    ]
    s = stat_summary(rows)
    hits = np.array([1, 0, 0])
    assert s["rungs"] == 3 and s["crps"] == pytest.approx(1.5) and s["mean_error"] == pytest.approx(-0.5)
    assert s["model"]["brier"] == pytest.approx(np.mean((np.array([0.7, 0.4, 0.5]) - hits) ** 2))
    assert s["market"]["brier"] == pytest.approx(np.mean((np.array([0.8, 0.3, 0.6]) - hits) ** 2))
    lg = lambda p: math.log(p / (1 - p))  # noqa: E731
    expected = math.sqrt(((lg(.7) - lg(.8)) ** 2 + (lg(.4) - lg(.3)) ** 2 + (lg(.5) - lg(.6)) ** 2) / 3)
    assert s["resid_rmse"] == pytest.approx(expected)


def _points(y, lo, hi, line=None, mid=None):
    m = {"pct": {"5": lo, "10": lo, "25": lo + 1, "50": (lo + hi) / 2, "75": hi - 1, "90": hi, "95": hi},
         "mean": (lo + hi) / 2, "capped_mean": (lo + hi) / 2, "pit": 0.5, "crps": 1.0, "p_over": 0.6}
    p = {"y": y, "samples": 1000, "copula": m, "independent": m}
    if line is not None:
        p |= {"line": line, "line_mid": mid}
    return {"points": p}


def test_points_summary_coverage_and_the_line_against_the_market():
    records = [_points(10, 5, 20, line=12, mid=0.5), _points(25, 5, 20, line=12, mid=0.5),
               _points(3, 5, 20), _points(12, 5, 20)]
    s = points_summary(records, "copula")
    assert s["cov90"] == pytest.approx(0.5) and s["above_p95"] == pytest.approx(0.25)
    line = s["line"]
    assert line["n"] == 2 and line["observed"] == 0.5
    assert line["brier"] == pytest.approx((0.6**2 + 0.4**2) / 2) and line["market_brier"] == pytest.approx(0.25)


def test_summarize_and_render_tables_on_real_records(games, root):
    c, _ = ctx({(GAME, CHASE): CHASE_GAME,
                (GAME, BROWN): outcome(carries=14, rushing_yards=60, receptions=4, receiving_yards=30)}, root=root)
    todo, skipped = tasks([games[CHASE], games[BROWN], games[MAYE]], c)
    records = [evaluate(t, samples=500) for t in todo] + skipped
    summary = summarize(records)
    assert summary["counts"]["status"] == {OK: 2, NO_POSITION: 1}
    assert summary["stats"]["receptions"]["all"]["n"] == 2
    assert summary["points"]["2026"]["copula"]["line"]["n"] == 1
    text = render_tables(summary)
    for heading in ("### Fantasy points", "### Per-stat calibration", "### Dispersion", "### Rushing yards",
                    "### Gamma vs lognormal", "### Joint calibration", "### TD Rungs"):
        assert heading in text
    json.dumps(summary)


# --- the CLI ------------------------------------------------------------------------------


def test_main_runs_the_backtest_and_writes_the_tables_and_summary(root, tmp_path, capsys):
    stats = {2026: [{"player_id": CHASE, "game_id": GAME, "season": 2026, "week": 10, "team": "CIN",
                     "receptions": 8, "receiving_yards": 97, "receiving_tds": 1}]}
    weekly = {2026: [{"season": 2026, "week": 10, "gsis_id": CHASE, "position": "WR"}]}
    out = tmp_path / "bt"
    code = backtest.main(["--root", str(root), "--out", str(out), "--samples", "300", "--seasons", "2026"],
                         stats_fetch=lambda s: stats.get(s, []), rosters_fetch=lambda s: weekly.get(s, []),
                         snaps_fetch=lambda s: [])
    assert code == 0
    assert (out / "records.jsonl").exists()
    summary = json.loads((out / "summary.json").read_text())
    assert summary["counts"]["status"]["ok"] == 1
    assert "### Fantasy points" in (out / "tables.md").read_text()
    assert "ok 1" in capsys.readouterr().out
