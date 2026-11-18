"""scripts/backtest_k_dst.py (S6.6): its pieces on synthetic data (the full run needs nflverse)."""

import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import backtest_k_dst as bkd  # noqa: E402

sys.path.remove(str(SCRIPTS))


def test_an_nflverse_team_game_scores_as_a_kicker_line():
    row = {"pat_made": 2, "pat_att": 3, "made": [33, 52], "missed": [48]}
    # ESPN league: 2 PATs, 3 + 5, a miss −1, no missed-PAT penalty. Sleeper: also −1 for the PAT.
    assert bkd.score_kicker(bkd.kicker_line(row), bkd.RULES["espn"]) == 9
    assert bkd.score_kicker(bkd.kicker_line(row), bkd.RULES["sleeper"]) == 8
    assert bkd.score_kicker(bkd.kicker_line({"pat_made": 0, "pat_att": 0, "made": [], "missed": []}),
                            bkd.RULES["espn"]) == 0


def test_rows_summarise_to_crps_coverage_and_bias():
    rng = np.random.default_rng(0)
    samples = np.arange(0, 21, dtype=float)
    rows = [bkd._row(samples, y, rng) for y in (3, 10, 18, 30)]
    s = bkd.summarize(rows)
    assert s["n"] == 4 and s["cov90"] == pytest.approx(0.75)  # 30 is above P95
    assert s["bias"] == pytest.approx(np.mean([3, 10, 18, 30]) - 10)
    assert bkd.summarize([]) == {"n": 0}


def test_the_report_has_both_walk_forwards_and_kalshi():
    s = {"n": 10, "crps": 2.5, "pit_p": 0.5, "cov90": 0.9, "cov50": 0.5, "bias": 0.1}
    text = bkd.render({"kicker": {"model espn all": s}, "dst": {"baseline sleeper 2025": s},
                       "kalshi": {"K priced": {"n": 3, "observed": 0.5, "model_mean_p": 0.5, "mid_mean_p": 0.5,
                                               "log_loss_model": 0.69, "log_loss_mid": 0.7, "log_loss_coin": 0.693,
                                               "brier_model": 0.25, "brier_mid": 0.26, "corr_model_mid": 0.8}}})
    assert "### Kicker: walk-forward" in text and "### DST: walk-forward" in text
    assert "| K priced | 3 |" in text


def _synthetic_dst(seasons=range(2015, 2019), games=60):
    import polars as pl

    sys.path.insert(0, str(SCRIPTS))
    import build_params as bp

    sys.path.remove(str(SCRIPTS))
    rng = np.random.default_rng(7)
    rows = []
    for s in seasons:
        for w in range(games):
            row = {c: int(rng.integers(0, 3)) for c in bp.DST_COLUMNS}
            row.update(season=s, week=w % 18 + 1, team=f"T{w}", opp=f"O{w}", itt=float(rng.uniform(14, 30)),
                       opp_itt=float(rng.uniform(14, 30)), points_allowed_all=int(rng.integers(0, 45)),
                       yards_allowed=int(rng.integers(150, 500)))
            rows.append(row)
    return pl.DataFrame(rows).select(bp.DST_COLUMNS)


def test_seasons_in_worker_processes_give_the_same_rows():
    # S8.3: each test season is seeded on its own, so --jobs only changes how long it takes.
    d = _synthetic_dst()
    serial = bkd.dst_walk_forward(d, [2017, 2018], 200)
    parallel = bkd.dst_walk_forward(d, [2017, 2018], 200, jobs=2)
    assert serial.keys() == parallel.keys() and serial
    assert all(serial[k] == parallel[k] for k in serial)


def test_seasons_run_in_workers_after_another_pool_has_run():
    # Review finding 2: a forkserver started by an earlier pool lacked scripts/ on sys.path.
    from ffp import workers

    with workers.process_pool(1) as pool:
        list(pool.map(abs, [-1]))
    d = _synthetic_dst()
    assert bkd.dst_walk_forward(d, [2017, 2018], 50, jobs=2).keys() == bkd.dst_walk_forward(d, [2017, 2018], 50).keys()
