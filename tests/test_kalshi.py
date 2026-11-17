"""Kalshi source (S5.1): settled NFL player-prop markets → Backtest inputs."""

from datetime import date

import pytest

from ffp.fair import KALSHI_PRIOR_C, fair_points
from ffp.odds import american_to_implied
from ffp.players import ScheduledGame
from ffp.sources import fanduel
from ffp.sources.kalshi import (
    FANTASY_LINE_SERIES,
    FANTASY_MEAN_SERIES,
    LADDER_SERIES,
    SERIES,
    Cache,
    KalshiRung,
    Quote,
    compact_market,
    drop_counts,
    fantasy_line,
    kickoff_quote,
    load_fantasy_lines,
    load_fantasy_means,
    load_rungs,
    match_game,
    parse_ticker,
    player_directory,
    price_drop_reason,
    rung_threshold,
    sportradar_to_gsis,
    to_prop_markets,
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


# --- the cache → Rungs, fantasy lines and means ----------------------------------------

CHASE, MASON, NOBODY = "uuid-chase", "uuid-mason", "uuid-dst"
SNF_KO = "2026-11-16T01:20:00Z"


def _market(ticker, player=CHASE, floor=None, opened="2026-11-13T15:00:00Z", result="no", value=""):
    raw = {"ticker": ticker, "custom_strike": {"football_player": player}, "floor_strike": floor,
           "open_time": opened, "settlement_ts": "2026-11-16T04:30:00Z", "result": result,
           "expiration_value": value, "rules_primary": "long text that is not kept", "volume_fp": "3.00"}
    return compact_market(raw)


def _quote(ticker, bid, ask, end=KO):
    return {"ticker": ticker, "bid": bid, "ask": ask, "end": end, "volume": 5.0, "interval": 1}


@pytest.fixture
def cache(tmp_path):
    c = Cache(tmp_path)
    c.save_games([*GAMES, _game("2025_05_MIN_CLE", "MIN", "CLE", "2025-10-05T13:30:00Z")])
    c.save_players({
        CHASE: {"name": "Ja'Marr Chase", "position": "WR", "source_id": "sr1", "gsis_id": "00-0036900"},
        MASON: {"name": "Jordan Mason", "position": "RB", "source_id": "sr2", "gsis_id": "00-0037000"},
        "uuid-nogsis": {"name": "Practice Squad", "position": "WR", "source_id": "sr3", "gsis_id": None},
    })
    rec = "KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-"
    c.save_markets("KXNFLRECYDS", [
        _market(rec + "50", floor=49.5, result="yes"),
        _market(rec + "90", floor=89.5),
        _market(rec + "120", floor=119.5),  # one-sided
        _market(rec + "150", floor=149.5),  # wide spread
        _market(rec + "160", floor=159.5),  # no candle before kickoff
        _market(rec + "170", floor=169.5),  # candles not pulled yet
        _market(rec + "200", floor=199.5, opened="2026-11-16T02:28:00Z"),  # listed in-game
        _market("KXNFLRECYDS-26NOV15PITCIN-CINTHIGGINS5-40", player="uuid-higgins", floor=39.5),
        _market("KXNFLRECYDS-26NOV15PITCIN-PITPRACTICE1-40", player="uuid-nogsis", floor=39.5),
        _market("KXNFLRECYDS-26NOV15PITCIN-PITDNP1-40", player=MASON, floor=39.5, result="scalar",
                value="Cancelled"),
        _market("KXNFLRECYDS-26AUG13DETCIN-CINJCHASE1-40", floor=39.5),  # preseason
    ])
    c.save_markets("KXNFLTD", [_market("KXNFLTD-26NOV15PITCIN-CINJCHASE1-1", floor=0.5),
                               _market("KXNFLTD-26NOV15PITCIN-CINJCHASE1-2", floor=1.5)])
    c.save_markets("KXNFLANYTD", [_market("KXNFLANYTD-25OCT05MINCLE-MINJMASON27", player=MASON,
                                          opened="2025-09-27T19:18:00Z")])
    c.save_markets("KXNFL2TD", [_market("KXNFL2TD-25OCT05MINCLE-MINJMASON27", player=MASON,
                                        opened="2025-09-27T19:18:00Z")])
    c.save_markets("KXNFLFFPTS", [_market("KXNFLFFPTS-26NOV15PITCIN-CINJCHASE1-17P4", floor=17.4)])
    c.save_markets("KXNFLFFPTSLADDER", [
        _market("KXNFLFFPTSLADDER-26NOV15PITCIN-CINJCHASE1", result="scalar", value="21.3"),
        _market("KXNFLFFPTSLADDER-26NOV15PITCIN-PITDNP1", player=MASON, result="scalar", value=""),
    ])
    for q in [_quote(rec + "50", 0.88, 0.90), _quote(rec + "90", 0.44, 0.45), _quote(rec + "120", 0.0, 0.03),
              _quote(rec + "150", 0.10, 0.25), {"ticker": rec + "160", "bid": None, "ask": None, "end": None,
                                                "volume": 0.0, "interval": None},
              _quote("KXNFLTD-26NOV15PITCIN-CINJCHASE1-1", 0.47, 0.48),
              _quote("KXNFLTD-26NOV15PITCIN-CINJCHASE1-2", 0.11, 0.12),
              _quote("KXNFLANYTD-25OCT05MINCLE-MINJMASON27", 0.40, 0.42, end=1759671000),
              _quote("KXNFL2TD-25OCT05MINCLE-MINJMASON27", 0.09, 0.11, end=1759671000),
              _quote("KXNFLFFPTS-26NOV15PITCIN-CINJCHASE1-17P4", 0.55, 0.58),
              _quote("KXNFLFFPTSLADDER-26NOV15PITCIN-CINJCHASE1", 0.18, 0.19)]:
        c.append_quote(q)
    return tmp_path


def test_compact_market_keeps_only_the_stored_fields():
    m = _market("KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90", floor=89.5)
    assert m == {"ticker": "KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90", "player": CHASE, "floor": 89.5,
                 "open": "2026-11-13T15:00:00Z", "settled": "2026-11-16T04:30:00Z", "result": "no", "value": ""}


def test_load_rungs_keeps_two_sided_pre_kickoff_rungs_of_mapped_players(cache):
    rec = [r for r in load_rungs(cache) if r.stat == "receiving_yards"]
    assert [(r.threshold, r.bid, r.ask) for r in rec] == [(50, 0.88, 0.90), (90, 0.44, 0.45)]
    r = rec[1]
    assert (r.game_id, r.season, r.week, r.kickoff, r.gsis_id, r.player) == (
        "2026_10_PIT_CIN", 2026, 10, SNF_KO, "00-0036900", "Ja'Marr Chase")
    assert r.mid == pytest.approx(0.445)


def test_drop_counts_say_why_each_market_was_left_out(cache):
    drops = drop_counts(cache)
    assert {reason: n for (series, reason), n in drops.items() if series == "KXNFLRECYDS"} == {
        "one_sided": 1, "wide_spread": 1, "no_candle": 1, "not_fetched": 1, "opened_in_game": 1,
        "unmapped_player": 2, "dnp": 1, "no_game": 1}


def test_td_series_of_both_seasons_load_as_rush_rec_td_rungs(cache):
    tds = sorted((r.season, r.threshold, r.mid) for r in load_rungs(cache) if r.stat == fanduel.TD_STAT)
    assert tds == [(2025, 1, pytest.approx(0.41)), (2025, 2, pytest.approx(0.10)),
                   (2026, 1, pytest.approx(0.475)), (2026, 2, pytest.approx(0.115))]


def test_fantasy_lines_and_means_are_their_own_records(cache):
    (line,) = load_fantasy_lines(cache)
    assert (line.game_id, line.gsis_id, line.line, line.bid, line.ask) == (
        "2026_10_PIT_CIN", "00-0036900", 17.4, 0.55, 0.58)
    assert line.mid == pytest.approx(0.565)
    (mean,) = load_fantasy_means(cache)  # Mason's scalar ladder settled with no value: did not play
    assert (mean.gsis_id, mean.price, mean.bid, mean.ask) == ("00-0036900", pytest.approx(0.185), 0.18, 0.19)
    assert mean.expected_points == pytest.approx(18.5)
    assert drop_counts(cache)[("KXNFLFFPTSLADDER", "dnp")] == 1


def test_loading_reads_only_the_cache(cache, monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: pytest.fail("network"))
    assert load_rungs(cache) and load_fantasy_lines(cache) and load_fantasy_means(cache)


def test_an_empty_cache_loads_nothing(tmp_path):
    assert load_rungs(tmp_path) == [] and load_fantasy_lines(tmp_path) == [] and not drop_counts(tmp_path)


# --- Rungs → Prop Markets → Fair Probabilities -----------------------------------------


def test_to_prop_markets_gives_one_kalshi_ladder_per_game_player_and_stat(cache):
    markets = to_prop_markets(load_rungs(cache))
    by_stat = {(m.game_id, m.stat): m for m in markets}
    assert len(markets) == len(by_stat) == 3
    rec = by_stat[("2026_10_PIT_CIN", "receiving_yards")]
    assert (rec.book, rec.player, rec.main_line) == ("kalshi", "Ja'Marr Chase", None)
    assert [r.threshold for r in rec.ladder] == [50, 90]
    assert {r.book for r in rec.ladder} == {"kalshi"}


def test_fair_points_with_no_vig_returns_the_mids(cache):
    rungs = load_rungs(cache)
    for market in to_prop_markets(rungs):
        mids = sorted((r.threshold, r.mid) for r in rungs
                      if (r.game_id, r.player, r.stat) == (market.game_id, market.player, market.stat))
        points = fair_points([market], prior_c={"kalshi": KALSHI_PRIOR_C})
        assert [(p.threshold, p.prob) for p in points] == [(t, pytest.approx(m, abs=1e-12)) for t, m in mids]


@pytest.mark.parametrize("mid", [0.01, 0.25, 0.5, 0.505, 0.75, 0.99])
def test_a_rung_s_odds_are_the_american_odds_of_its_mid(mid):
    rung = KalshiRung("g", 2026, 10, SNF_KO, "00-1", "P", "receptions", 5, mid - 0.005, mid + 0.005)
    (market,) = to_prop_markets([rung])
    assert american_to_implied(market.ladder[0].odds) == pytest.approx(mid, abs=1e-12)


# --- Sprint 6: D/ST lines and wide kicker lines ----------------------------------------

def test_a_kalshi_dst_target_is_the_teams_dst():
    from ffp.sources.kalshi import dst_id

    assert dst_id("SF 49ers D/ST") == "DST-SF"
    assert dst_id("LA Chargers D/ST") == "DST-LAC"
    assert dst_id("Ja'Marr Chase") is None and dst_id(None) is None
    assert dst_id("Nowhere Nobodies D/ST") is None


def test_wide_fantasy_lines_load_only_when_asked(tmp_path):
    from ffp.sources.kalshi import dst_id

    c = Cache(tmp_path)
    c.save_games(GAMES)
    c.save_players({"uuid-pit": {"name": "PIT Steelers D/ST", "position": "N/A", "source_id": "sr9",
                                 "gsis_id": dst_id("PIT Steelers D/ST")}})
    ffpts = "KXNFLFFPTS-26NOV15PITCIN-PITPITDST-5P7"
    td = "KXNFLTD-26NOV15PITCIN-PITPITDST-1"
    c.save_markets("KXNFLFFPTS", [_market(ffpts, player="uuid-pit", floor=5.7)])
    c.save_markets("KXNFLTD", [_market(td, player="uuid-pit", floor=0.5)])
    c.append_quote(_quote(ffpts, 0.30, 0.45))  # 15¢ wide
    c.append_quote(_quote(td, 0.10, 0.12))
    assert load_fantasy_lines(tmp_path) == []
    (line,) = load_fantasy_lines(tmp_path, keep_wide=True)
    assert (line.gsis_id, line.line, line.wide) == ("DST-PIT", 5.7, True)
    drops = drop_counts(tmp_path)
    assert drops[("KXNFLFFPTS", "wide_spread")] == 1
    assert drops[("KXNFLTD", "dst_rung")] == 1 and load_rungs(tmp_path) == []  # a D/ST's TD Rung is not a player's


def test_a_dst_mean_market_is_not_counted_as_a_rung(tmp_path):
    # Review of PRs 9-11: a D/ST's KXNFLFFPTSLADDER market was filed under "dst_rung".
    from ffp.sources.kalshi import _market_drop_reason, dst_id

    game = GAMES[0]
    players = {"uuid-pit": {"gsis_id": dst_id("PIT Steelers D/ST")}}
    market = {"player": "uuid-pit", "value": 7.5}
    assert _market_drop_reason("KXNFLFFPTSLADDER", market, game, players) == "dst_mean"
    assert _market_drop_reason("KXNFLTD", {"player": "uuid-pit"}, game, players) == "dst_rung"
    assert _market_drop_reason("KXNFLFFPTS", {"player": "uuid-pit"}, game, players) is None
