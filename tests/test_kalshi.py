"""Kalshi source (S5.1): settled NFL player-prop markets → Backtest inputs."""

from datetime import date

import pytest

from ffp.sources import fanduel
from ffp.sources.kalshi import (
    FANTASY_LINE_SERIES,
    FANTASY_MEAN_SERIES,
    LADDER_SERIES,
    SERIES,
    fantasy_line,
    parse_ticker,
    rung_threshold,
)

# --- tickers and thresholds ------------------------------------------------------------


def test_parse_ticker_splits_series_game_date_teams_player_and_threshold():
    t = parse_ticker("KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90")
    assert t.series == "KXNFLRECYDS"
    assert t.event == "KXNFLRECYDS-26NOV15PITCIN"
    assert t.game_date == date(2026, 11, 15)
    assert t.teams == "PITCIN"
    assert t.player == "CINJCHASE1"
    assert t.suffix == "90"


def test_parse_ticker_without_a_threshold_suffix():
    t = parse_ticker("KXNFLANYTD-25OCT05MINCLE-MINJMASON27")
    assert (t.series, t.game_date, t.teams, t.player, t.suffix) == (
        "KXNFLANYTD", date(2025, 10, 5), "MINCLE", "MINJMASON27", None)


def test_parse_ticker_rejects_a_non_game_ticker():
    with pytest.raises(ValueError):
        parse_ticker("KXNFLRECYDS-SOMETHING")


def test_threshold_suffix_90_means_90_or_more_with_floor_strike_89_5():
    market = {"ticker": "KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90", "floor": 89.5}
    assert rung_threshold(market) == 90


def test_threshold_comes_from_floor_strike_when_the_suffix_is_fused_to_the_jersey():
    # Early 2025 tickers: Brenton Strange #85, 20+ yards → "JACBSTRANGE8520".
    market = {"ticker": "KXNFLRECYDS-25OCT06KCJAC-JACBSTRANGE8520", "floor": 19.5}
    assert rung_threshold(market) == 20


def test_threshold_falls_back_to_the_suffix_without_floor_strike():
    assert rung_threshold({"ticker": "KXNFLREC-26NOV15PITCIN-CINJCHASE1-7", "floor": None}) == 7


def test_2025_td_series_are_fixed_rungs():
    assert rung_threshold({"ticker": "KXNFLANYTD-25OCT05MINCLE-MINJMASON27", "floor": None}) == 1
    assert rung_threshold({"ticker": "KXNFL2TD-25OCT12CINGB-CINCBROWN30", "floor": None}) == 2


def test_2026_td_ladder_rungs_from_floor_strike():
    assert rung_threshold({"ticker": "KXNFLTD-26OCT18ARILAR-ARIMWILSON14-1", "floor": 0.5}) == 1
    assert rung_threshold({"ticker": "KXNFLTD-26OCT18ARILAR-ARIMWILSON14-3", "floor": 2.5}) == 3


def test_fantasy_line_is_strictly_greater_than_the_floor_strike():
    assert fantasy_line({"ticker": "KXNFLFFPTS-26OCT18CARPHI-CARRFITZGERALD10-7P4", "floor": 7.4}) == 7.4
    assert fantasy_line({"ticker": "KXNFLFFPTS-26OCT18CARPHI-CARRFITZGERALD10-7P4", "floor": None}) == 7.4


# --- series → stats --------------------------------------------------------------------


def test_series_map_to_our_stats():
    assert LADDER_SERIES == {
        "KXNFLRECYDS": "receiving_yards",
        "KXNFLREC": "receptions",
        "KXNFLRSHYDS": "rushing_yards",
        "KXNFLRSHATT": "carries",
        "KXNFLPASSYDS": "passing_yards",
        "KXNFLPASSTDS": "passing_tds",
        "KXNFLPASSINT": "passing_interceptions",
        "KXNFLTD": fanduel.TD_STAT,
        "KXNFLANYTD": fanduel.TD_STAT,
        "KXNFL2TD": fanduel.TD_STAT,
    }
    assert (FANTASY_LINE_SERIES, FANTASY_MEAN_SERIES) == ("KXNFLFFPTS", "KXNFLFFPTSLADDER")
    assert set(SERIES) == set(LADDER_SERIES) | {FANTASY_LINE_SERIES, FANTASY_MEAN_SERIES}
    assert not [s for s in SERIES if any(v in s for v in ("PROT", "PRECISE", "ESCALATOR", "LADDERREC"))]
