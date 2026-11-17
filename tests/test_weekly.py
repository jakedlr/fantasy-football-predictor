"""ffp.weekly: nflverse weekly player stats, loaded once and indexed by player and week."""

import pytest

from ffp import weekly
from ffp.weekly import WeeklyStats, fetch_seasons, game_week, memoized, played_games


def row(pid, season, week, **stats):
    return {"player_id": pid, "season": season, "week": week, "game_id": f"{season}_{week:02d}_AAA_BBB", **stats}


def test_game_week_reads_season_and_week_from_the_game_id():
    assert game_week("2026_10_LAC_BAL") == (2026, 10)


def test_number_reads_polars_and_csv_values():
    assert [weekly.number(v) for v in (3, 2.5, None, "", "NA", "4")] == [3.0, 2.5, 0.0, 0.0, 0.0, 4.0]
    assert weekly.number(float("nan")) == 0.0


def test_fetch_seasons_skips_an_unstarted_current_season_only():
    def fetch(season):
        if season == 2027:
            raise ConnectionError("HTTP Error 404: Not Found")
        return [row("p", season, 1)]

    assert set(fetch_seasons(fetch, [2025, 2026, 2027], current=2027)) == {2025, 2026}
    with pytest.raises(ConnectionError):
        fetch_seasons(fetch, [2027], current=2028)  # a missing past season is an error


def test_memoized_fetches_each_season_once():
    calls = []
    fetch = memoized(lambda s: calls.append(s) or [row("p", s, 1)])
    fetch(2026), fetch(2026), fetch(2025)
    assert calls == [2026, 2025]


def test_totals_over_a_window_strictly_before_a_week():
    ws = WeeklyStats([row("p", 2024, 17, carries=5), row("p", 2025, 3, carries=10), row("p", 2026, 2, carries=20),
                      row("p", 2026, 5, carries=40), row("q", 2026, 1, carries="NA")])
    t = ws.totals(["carries"], since_season=2025, before=(2026, 5))
    assert t["p"] == [30.0] and t["q"] == [0.0]
    assert ws.totals(["carries"], since_season=2024)["p"] == [75.0]  # no `before`: everything


def test_last_games_count_games_played_without_a_stat_row():
    # Weeks 1-4 he played; nflverse has stat rows only for weeks 1 and 3.
    stats = [row("p", 2026, 1, rushing_yards=40), row("p", 2026, 3, rushing_yards=20)]
    played = {(f"2026_{w:02d}_AAA_BBB", "p") for w in (1, 2, 3, 4)}
    ws = WeeklyStats(stats, played)
    [(games, totals)] = ws.last_games(["rushing_yards"], n=8, since_season=2025, before=(2026, 5)).values()
    assert (games, totals) == (4, [60.0])  # 15 a game, not 30
    [(games, totals)] = ws.last_games(["rushing_yards"], n=2, since_season=2025, before=(2026, 5)).values()
    assert (games, totals) == (2, [20.0])  # weeks 3 and 4


def test_last_games_weight_earlier_seasons():
    ws = WeeklyStats([row("p", 2025, 17, carries=10), row("p", 2026, 1, carries=20)])
    [(games, totals)] = ws.last_games(["carries"], n=8, since_season=2025, from_season=2026, earlier_weight=0.5).values()
    assert (games, totals) == (1.5, [25.0])


def test_played_games_maps_pfr_ids_to_gsis_ids():
    snaps = [{"game_id": "g1", "pfr_player_id": "AbcX00", "offense_snaps": 30},
             {"game_id": "g2", "pfr_player_id": "AbcX00", "offense_snaps": 0},
             {"game_id": "g1", "pfr_player_id": "Nobody", "offense_snaps": 12}]
    roster = [{"pfr_id": "AbcX00", "gsis_id": "00-1"}]
    assert played_games(snaps, roster) == {("g1", "00-1")}


def test_load_played_skips_an_unstarted_season():
    def snaps(season):
        if season == 2027:
            raise ConnectionError("404")
        return [{"game_id": f"{season}_17_AAA_BBB", "pfr_player_id": "X", "offense_snaps": 5}]

    def rosters(season):
        if season == 2027:
            raise ConnectionError("404")
        return [{"pfr_id": "X", "gsis_id": "00-9"}]

    assert weekly.load_played([2026, 2027], snaps, rosters) == {("2026_17_AAA_BBB", "00-9")}
