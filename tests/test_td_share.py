"""ffp.td_share: the per-player rushing TD share (S4.3, research/nflverse-data.md section 6). No network."""

import logging
import math

import pytest

from ffp import params
from ffp.td_share import TdHistory, load_td_history, rushing_td_share


def fake_params():
    return {
        "td_rushing_share": {"QB": 0.988, "RB": 0.8, "WR": 0.033, "TE": 0.022},
        "td_share": {
            "rho_rush": {"QB": 0.048, "RB": 0.032, "WR": 0.043, "TE": 0.092},
            "rho_rec": {"QB": 0.093, "RB": 0.029, "WR": 0.049, "TE": 0.053},
            "kappa": 2,
            "k": 30,
            "prior_seasons": 2,
        },
    }


# --- the share ----------------------------------------------------------------


@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE"])
def test_no_history_is_the_position_share(position):
    p = fake_params()
    m = p["td_rushing_share"][position]
    assert rushing_td_share(position, None, p) == pytest.approx(m)
    # All-zero counts reduce to m too: v = κm/κ, share = k·v/k.
    assert rushing_td_share(position, TdHistory(), p) == pytest.approx(m)


def test_no_history_with_the_committed_params_is_the_committed_position_share():
    br = params.load("base_rates")
    for position in ("QB", "RB", "WR", "TE"):
        assert rushing_td_share(position) == pytest.approx(br["td_rushing_share"][position])


def test_pure_volume_rb_without_tds_is_his_volume_share():
    # v = (.032·200 + 2·.8) / (.032·200 + .029·40 + 2) = 8 / 9.56; no TDs, so share = v.
    h = TdHistory(carries=200, targets=40)
    assert rushing_td_share("RB", h, fake_params()) == pytest.approx(8 / 9.56)


def test_henry_like_workhorse():
    # research: Derrick Henry 43/46 → 0.93. v = (25.6 + 1.6) / (25.6 + 1.74 + 2) = 27.2 / 29.34.
    h = TdHistory(carries=800, targets=60, rushing_tds=43, receiving_tds=3)
    v = 27.2 / 29.34
    expected = (43 + 30 * v) / (46 + 30)
    assert rushing_td_share("RB", h, fake_params()) == pytest.approx(expected)
    assert expected == pytest.approx(0.932, abs=0.001)


def test_chase_like_wr():
    # research: Ja'Marr Chase 0/32 → 0.007. v = (.043·6 + 2·.033) / (.043·6 + .049·400 + 2) = .324 / 21.858.
    h = TdHistory(carries=6, targets=400, rushing_tds=0, receiving_tds=32)
    v = 0.324 / 21.858
    expected = 30 * v / (32 + 30)
    assert rushing_td_share("WR", h, fake_params()) == pytest.approx(expected)
    assert expected == pytest.approx(0.007, abs=0.001)


def test_qb_stays_near_the_position_share():
    h = TdHistory(carries=250, targets=2, rushing_tds=40, receiving_tds=1)
    share = rushing_td_share("QB", h, fake_params())
    assert share == pytest.approx(0.988, abs=0.01)


def test_null_rho_counts_as_no_volume_of_that_kind():
    # A position with no carries in the build seasons gets rho_rush null; its carries then add nothing.
    p = fake_params()
    p["td_share"]["rho_rush"]["TE"] = None
    h = TdHistory(carries=10, targets=0)
    assert rushing_td_share("TE", h, p) == pytest.approx(0.022)


def test_unknown_position_raises():
    with pytest.raises(ValueError, match="position"):
        rushing_td_share("K", None, fake_params())


# --- the history loader -------------------------------------------------------


def row(player_id, season, week, season_type="REG", position="RB", **stats):
    return {"player_id": player_id, "season": season, "week": week, "season_type": season_type,
            "position": position, "carries": 0, "targets": 0, "rushing_tds": 0, "receiving_tds": 0, **stats}


def fetch_from(by_season, calls=None):
    def fetch(season):
        if calls is not None:
            calls.append(season)
        value = by_season.get(season, [])
        if isinstance(value, Exception):
            raise value
        return value
    return fetch


def test_loader_fetches_this_season_and_the_previous_two():
    calls = []
    load_td_history(2026, fetch=fetch_from({}, calls), params=fake_params())
    assert sorted(calls) == [2024, 2025, 2026]


def test_loader_sums_across_seasons_and_season_types_and_ignores_position():
    rows = {
        2024: [row("P1", 2024, 3, carries=10, rushing_tds=1, position="WR"),
               row("P1", 2024, 19, "POST", carries=5, targets=2, receiving_tds=1, position="WR")],
        2025: [row("P1", 2025, 1, carries=20, targets=4, rushing_tds=2, position="WR"),
               row("P2", 2025, 1, targets=7, receiving_tds=1, position="TE")],
        2026: [row("P1", 2026, 2, carries=1, targets=1, position="QB")],
    }
    hist = load_td_history(2026, fetch=fetch_from(rows), params=fake_params())
    assert hist == {
        "P1": TdHistory(carries=36, targets=7, rushing_tds=3, receiving_tds=1),
        "P2": TdHistory(carries=0, targets=7, rushing_tds=0, receiving_tds=1),
    }


def test_loader_counts_nulls_as_zero_and_skips_rows_without_an_id():
    rows = {2026: [row("P1", 2026, 1, carries=None, targets="", rushing_tds=math.nan, receiving_tds=1),
                   row(None, 2026, 1, carries=99), row("", 2026, 1, carries=99)]}
    hist = load_td_history(2026, fetch=fetch_from(rows), params=fake_params())
    assert hist == {"P1": TdHistory(receiving_tds=1)}


def test_loader_reads_csv_strings():
    rows = {2026: [{"player_id": "P1", "season": "2026", "week": "1", "season_type": "REG", "position": "RB",
                    "carries": "12", "targets": "3", "rushing_tds": "1", "receiving_tds": "0"}]}
    hist = load_td_history(2026, fetch=fetch_from(rows), params=fake_params())
    assert hist == {"P1": TdHistory(carries=12, targets=3, rushing_tds=1)}


def test_current_season_404_before_week_1_means_no_current_season_rows(caplog):
    not_yet = ConnectionError("Failed to download https://.../stats_player_week_2027.parquet: "
                              "404 Client Error: Not Found for url: https://...")
    rows = {2025: [row("P1", 2025, 1, carries=10)], 2026: [row("P1", 2026, 1, carries=5)], 2027: not_yet}
    with caplog.at_level(logging.WARNING):
        hist = load_td_history(2027, fetch=fetch_from(rows), params=fake_params())
    assert hist == {"P1": TdHistory(carries=15)}
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "failures",
    [
        {2026: ConnectionError("Failed to download ...: Max retries exceeded")},  # offline, not a 404
        {2024: ConnectionError("Failed to download ...: 404 Client Error")},  # a past season must exist
        {2025: ValueError("Failed to parse data")},
        {2026: RuntimeError("anything else")},
    ],
)
def test_any_other_fetch_failure_logs_a_warning_and_gives_no_history(failures, caplog):
    rows = {2024: [row("P1", 2024, 1, carries=10)], 2025: [row("P1", 2025, 1, carries=10)],
            2026: [row("P1", 2026, 1, carries=10)], **failures}
    with caplog.at_level(logging.WARNING, logger="ffp.td_share"):
        hist = load_td_history(2026, fetch=fetch_from(rows), params=fake_params())
    assert hist == {}
    assert any(r.levelno == logging.WARNING and "position share" in r.getMessage() for r in caplog.records)
    # ...so every player falls back to m.
    assert rushing_td_share("RB", hist.get("P1"), fake_params()) == pytest.approx(0.8)


def test_prior_seasons_come_from_the_params():
    calls = []
    p = fake_params()
    p["td_share"]["prior_seasons"] = 1
    load_td_history(2026, fetch=fetch_from({}, calls), params=p)
    assert sorted(calls) == [2025, 2026]


def test_before_keeps_only_rows_strictly_before_that_season_and_week():
    # The Backtest's no-look-ahead rule: a Game in 2026 Week 3 sees 2024, 2025 and 2026 Weeks 1-2.
    rows = {
        2024: [row("P1", 2024, 19, "POST", carries=4)],
        2025: [row("P1", 2025, 18, carries=10, rushing_tds=1)],
        2026: [row("P1", 2026, 2, carries=20), row("P1", 2026, 3, carries=99, rushing_tds=9),
               row("P1", 2026, 4, carries=99), row("P2", 2026, 3, targets=9)],
    }
    hist = load_td_history(2026, fetch=fetch_from(rows), params=fake_params(), before=(2026, 3))
    assert hist == {"P1": TdHistory(carries=34, rushing_tds=1)}


def test_before_reads_week_and_season_from_csv_strings():
    rows = {2026: [{"player_id": "P1", "season": "2026", "week": "2", "carries": "5"},
                   {"player_id": "P1", "season": "2026", "week": "10", "carries": "7"}]}
    hist = load_td_history(2026, fetch=fetch_from(rows), params=fake_params(), before=(2026, 10))
    assert hist == {"P1": TdHistory(carries=5)}


def test_the_weekly_download_keeps_every_column_a_reader_uses():
    # Review of PRs 9-11: the shared loader kept all 100+ columns; it now selects these.
    from ffp import estimates, outcomes, td_share, weekly

    used = {c for cols in (*outcomes.COLUMNS.values(), *estimates.COLUMNS.values()) for c in cols}
    used |= set(td_share._COUNTS) | {"player_id", "game_id", "season", "week"}
    assert used <= set(weekly.PLAYER_STATS_COLUMNS)
