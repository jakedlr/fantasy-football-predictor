"""Kalshi source (S5.1): settled NFL player-prop markets → Backtest inputs."""

from datetime import date

import pytest

from ffp.players import ScheduledGame
from ffp.sources import fanduel
from ffp.sources.kalshi import (
    FANTASY_LINE_SERIES,
    FANTASY_MEAN_SERIES,
    LADDER_SERIES,
    SERIES,
    Quote,
    fantasy_line,
    kickoff_quote,
    match_game,
    parse_ticker,
    player_directory,
    price_drop_reason,
    rung_threshold,
    sportradar_to_gsis,
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


# --- the price rule --------------------------------------------------------------------

KO = 1794792000  # 2026-11-16T01:20:00Z, SNF PIT @ CIN


def _live_candle(end, bid, ask, volume="0.00"):
    return {"end_period_ts": end, "volume_fp": volume,
            "yes_bid": {"close_dollars": bid}, "yes_ask": {"close_dollars": ask}}


def _historical_candle(end, bid, ask, volume="0.00"):
    return {"end_period_ts": end, "volume": volume, "yes_bid": {"close": bid}, "yes_ask": {"close": ask}}


def test_kickoff_quote_is_the_last_candle_ending_at_or_before_kickoff():
    candles = [_live_candle(KO - 120, "0.40", "0.42"), _live_candle(KO, "0.44", "0.45", "12.00"),
               _live_candle(KO + 60, "0.70", "0.72", "900.00")]  # in-game: cut
    assert kickoff_quote(candles, KO) == Quote(bid=0.44, ask=0.45, end=KO, volume=12.0)


def test_kickoff_quote_reads_historical_field_names():
    assert kickoff_quote([_historical_candle(KO - 60, "0.5200", "0.5600", "2.00")], KO) == Quote(
        0.52, 0.56, KO - 60, 2.0)


def test_kickoff_quote_is_none_when_every_candle_is_in_game():
    assert kickoff_quote([_live_candle(KO + 60, "0.4", "0.5")], KO) is None
    assert kickoff_quote([], KO) is None


def test_a_missing_side_reads_as_none():
    q = kickoff_quote([{"end_period_ts": KO, "yes_bid": {}, "yes_ask": {"close_dollars": "0.03"}}], KO)
    assert (q.bid, q.ask) == (None, 0.03)


@pytest.mark.parametrize("bid, ask, reason", [
    (0.44, 0.45, None),
    (0.40, 0.50, None),  # a spread of exactly $0.10 is kept
    (0.40, 0.51, "wide_spread"),
    (0.0, 0.03, "one_sided"),  # far tail: no bid
    (None, 0.03, "one_sided"),
    (0.97, 1.0, "one_sided"),  # no ask
    (0.97, None, "one_sided"),
])
def test_price_drop_rule(bid, ask, reason):
    assert price_drop_reason(Quote(bid, ask, KO, 0.0)) == reason


# --- kickoff join ----------------------------------------------------------------------


def _game(game_id, away, home, kickoff):
    season, week = int(game_id[:4]), int(game_id[5:7])
    return ScheduledGame(game_id, season, week, home, away, kickoff)


GAMES = [
    _game("2026_10_PIT_CIN", "PIT", "CIN", "2026-11-16T01:20:00Z"),  # SNF: Nov 15 in ET
    _game("2026_10_LA_SEA", "LA", "SEA", "2026-11-15T21:25:00Z"),
    _game("2025_05_KC_JAX", "KC", "JAX", "2025-10-07T00:15:00Z"),
    _game("2026_11_PIT_CIN", "CIN", "PIT", "2026-11-23T18:00:00Z"),
]


def test_an_event_joins_the_nflverse_game_on_its_eastern_date_and_teams():
    assert match_game("KXNFLRECYDS-26NOV15PITCIN", GAMES).game_id == "2026_10_PIT_CIN"


def test_kalshi_team_codes_lar_and_jac_join_nflverse_la_and_jax():
    assert match_game("KXNFLREC-26NOV15LARSEA", GAMES).game_id == "2026_10_LA_SEA"
    assert match_game("KXNFLRECYDS-25OCT06KCJAC", GAMES).game_id == "2025_05_KC_JAX"


def test_team_order_does_not_matter_and_the_date_must():
    assert match_game("KXNFLREC-26NOV15SEALAR", GAMES).game_id == "2026_10_LA_SEA"
    assert match_game("KXNFLREC-26NOV16PITCIN", GAMES) is None
    assert match_game("KXNFLREC-26AUG13DETCIN", GAMES) is None  # preseason: not in the schedule


# --- players ---------------------------------------------------------------------------


def test_player_directory_keeps_nfl_players_with_their_sportradar_id():
    targets = [
        {"id": "u1", "name": "Ja'Marr Chase", "source_id": "sr1",
         "details": {"league": "NFL", "position": "WR"}},
        {"id": "u2", "name": "George French II", "source_id": "sr2",
         "details": {"league": "NCAAFB", "position": "DB"}},
        {"id": "u3", "name": "Old Format", "source_ids": {"source_3_id": "sr3"},
         "details": {"league": "NFL", "position": "RB"}},
    ]
    assert player_directory(targets) == {
        "u1": {"name": "Ja'Marr Chase", "position": "WR", "source_id": "sr1"},
        "u3": {"name": "Old Format", "position": "RB", "source_id": "sr3"},
    }


def test_sportradar_ids_map_to_gsis_ids_through_nflverse_rosters():
    rows = {2025: [{"sportradar_id": "sr1", "gsis_id": "00-0036900"}, {"sportradar_id": None, "gsis_id": "x"}],
            2026: [{"sportradar_id": "sr9", "gsis_id": "00-0041489"}]}
    assert sportradar_to_gsis([2025, 2026], fetch=rows.__getitem__) == {
        "sr1": "00-0036900", "sr9": "00-0041489"}
