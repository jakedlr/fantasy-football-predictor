"""Sprint 6: Game Lines, the kicker and D/ST models, and their predictions."""

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from ffp import gamelines
from ffp.dst import MIN_ESS, AnalogTable, Target, analog_table, analog_weights, dst_scores, targets, tilt
from ffp.fair import FairPoint
from ffp.kicker import attempt_means, kicker_scores, make_probability, sample_kicker
from ffp.markets import DST_TDS, TEAM_POINTS, GameLine, MainLine, PropMarket, Rung
from ffp.players import ScheduledGame
from ffp.predict import BookOdds, load_fixture_books, load_fixture_nflverse, predict, resolve_targets
from ffp.scoring import load_rules
from ffp.sources import draftkings, fanduel

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"
BEFORE_MNF = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
ESPN = load_rules(Path(__file__).resolve().parents[1] / "src" / "ffp" / "rules" / "espn-league.json")
IND_HOU = ScheduledGame("2026_11_IND_HOU", 2026, 11, "HOU", "IND", "2026-11-20T01:15:00Z")


def read(name):
    return json.loads((FIXTURES / name).read_text())


# --- parsing (S6.2) -----------------------------------------------------------------


def test_draftkings_game_lines_are_the_main_spread_and_total():
    lines = {line.game_id: line for line in draftkings.parse_game_lines(read("dk_nfl_league_game_lines.json"))}
    assert lines["34118011"] == GameLine("34118011", "draftkings", -12.5, 44.5)  # IND @ HOU: HOU −12.5
    assert len(lines) == 13


def test_fanduel_game_lines_are_the_main_spread_and_total():
    lines = {line.game_id: line for line in fanduel.parse_game_lines(read("fd_nfl_event_popular_tab.json"))}
    assert lines["36305232"] == GameLine("36305232", "fanduel", -11.5, 44.5)
    assert len(fanduel.parse_game_lines(read("fd_nfl_league_game_lines.json"))) == 13


def test_draftkings_team_totals_are_a_main_line_and_rungs():
    by_team = {m.player: m for m in draftkings.parse_team_totals(read("dk_nfl_team_totals.json"))}
    ind = by_team["IND Colts"]
    assert ind.stat == TEAM_POINTS and ind.main_line == MainLine(15.5, -120.0, -105.0, "draftkings")
    assert ind.ladder[0] == Rung(8, -600.0, "draftkings")  # Over 7.5 is "8 or more"
    assert 16 not in [r.threshold for r in ind.ladder]  # the Main Line's own threshold isn't repeated


def test_fanduel_team_totals_are_a_main_line_and_alternate_overs():
    by_team = {m.player: m for m in fanduel.parse_team_totals(read("fd_nfl_event_popular_tab.json"))}
    hou = by_team["Houston Texans"]
    assert hou.main_line == MainLine(28.5, -111.0, -115.0, "fanduel")
    assert hou.ladder[-1] == Rung(48, 1700.0, "fanduel")  # "Over (47.5)"


def test_dst_td_prices_come_from_both_books():
    dk = {m.player: m for m in draftkings.parse_dst_tds(read("dk_nfl_td_scorer.json"))}
    fd = {m.player: m for m in fanduel.parse_dst_tds(read("fd_nfl_event_td_scorer_tab.json"))}
    assert dk["BAL Ravens"].stat == DST_TDS and dk["BAL Ravens"].ladder[0] == Rung(1, 475.0, "draftkings")
    assert fd["Baltimore"].ladder[0] == Rung(1, 700.0, "fanduel")
    # The D/ST is not mistaken for a player in the TD markets.
    assert not any("Defense" in m.player or "D/ST" in m.player
                   for m in fanduel.parse_prop_markets(read("fd_nfl_event_td_scorer_tab.json"), "rush_rec_tds"))


# --- Implied Team Totals --------------------------------------------------------------


def test_implied_totals_average_the_books():
    totals = gamelines.implied_totals(IND_HOU, [("HOU Texans", "IND Colts", GameLine("1", "draftkings", -12.5, 44.5)),
                                                ("Houston Texans", "Indianapolis Colts", GameLine("2", "fanduel", -11.5, 44.5))])
    assert totals.by_team == {"HOU": 28.25, "IND": 16.25}
    assert totals.sources == ["draftkings", "fanduel"]
    assert totals.expected_margin("HOU") == 12.0


def test_a_book_listing_the_teams_the_other_way_round_is_read_from_its_side():
    totals = gamelines.implied_totals(IND_HOU, [("IND Colts", "HOU Texans", GameLine("1", "draftkings", 12.5, 44.5))])
    assert totals.by_team == {"HOU": 28.5, "IND": 16.0}


def test_nflverse_lines_stand_in_and_say_so():
    game = ScheduledGame("g", 2026, 11, "HOU", "IND", "2026-11-20T01:15:00Z", spread_line=11.5, total_line=44.5)
    totals = gamelines.implied_totals(game, [("HOU Texans", "IND Colts", GameLine("1", "draftkings", None, 44.5))])
    assert totals.by_team == {"HOU": 28.0, "IND": 16.5} and totals.sources == [gamelines.NFLVERSE]
    assert gamelines.implied_totals(IND_HOU, []) is None


def test_team_markets_match_any_spelling_of_the_team():
    markets = [PropMarket("Baltimore", DST_TDS, "1", "fanduel", None, ()),
               PropMarket("BAL Ravens", DST_TDS, "2", "draftkings", None, ()),
               PropMarket("BAL Ravens", TEAM_POINTS, "2", "draftkings", None, ()),
               PropMarket("LA Chargers", DST_TDS, "2", "draftkings", None, ())]
    assert [m.book for m in gamelines.team_markets(markets, "BAL", DST_TDS)] == ["fanduel", "draftkings"]


# --- the kicker model (S6.3) ----------------------------------------------------------


def test_pat_attempts_follow_the_implied_team_total_and_fgs_barely_do():
    low, high = attempt_means(15, -6, 2026), attempt_means(30, 9, 2026)
    assert high[1] > 1.8 * low[1]
    assert abs(high[0] - low[0]) < 0.5


def test_long_kicks_miss_more():
    p = make_probability(np.array([25, 40, 55]), 2026)
    assert p[0] > p[1] > p[2] > 0.5


def test_kicker_samples_are_consistent():
    k = sample_kicker(24, 3, 2026, n=5000, seed=1)
    made = np.sum(~np.isnan(k.fg_made_yards), axis=1)
    assert np.all(made + k.fg_missed == k.fga)
    assert np.all(k.pat_made >= 0) and np.all(k.pat_missed >= 0)
    assert 1.5 < k.fga.mean() < 2.6


def test_a_kicker_scores_more_on_a_high_scoring_team():
    low, _ = kicker_scores(14, -7, 2026, ESPN, n=20000)
    high, _ = kicker_scores(30, 7, 2026, ESPN, n=20000)
    assert 6 < low.mean < high.mean < 11


# --- the D/ST model (S6.4) ------------------------------------------------------------


def test_analog_weights_center_on_the_opponents_implied_total():
    table = analog_table()
    w = analog_weights(17.0, 2026, table)
    assert w.sum() == pytest.approx(1.0)
    assert np.sum(w) ** 2 / np.sum(w * w) >= MIN_ESS
    assert np.sum(w * table.columns["opp_itt"]) == pytest.approx(17.0, abs=1.0)


def test_the_tilt_reproduces_the_market():
    table = analog_table()
    goal = [FairPoint(14, 0.80, 1.0), FairPoint(21, 0.40, 1.0), FairPoint(28, 0.12, 1.0)]
    q = tilt(analog_weights(20.0, 2026, table), targets(table, goal, [FairPoint(1, 0.18, 1.0)]))
    pa = table.columns["points_allowed_all"]
    for p in goal:
        assert np.sum(q * (pa >= p.threshold)) == pytest.approx(p.prob, abs=0.01)
    assert np.sum(q * (table.dst_tds >= 1)) == pytest.approx(0.18, abs=0.01)


def test_extreme_market_probabilities_are_left_out_of_the_tilt():
    assert targets(analog_table(), [FairPoint(1, 0.999, 1.0), FairPoint(45, 0.01, 1.0)]) == []
    w = np.full(4, 0.25)
    assert tilt(w, []) is w


def test_a_dst_facing_a_weak_offense_scores_more():
    weak = dst_scores(14, 2026, ESPN, n=20000)
    strong = dst_scores(30, 2026, ESPN, n=20000)
    assert weak.scores.mean > strong.scores.mean + 5
    assert weak.sampled["points_allowed"].mean() < 17


def test_dst_rows_are_scored_under_the_rules():
    # Two Analog Games: a shutout with 5 sacks and a 40-point blowout.
    cols = {name: np.zeros(2) for name in analog_table().columns}
    cols.update(season=np.array([2025.0, 2025.0]), opp_itt=np.array([20.0, 20.0]), sacks=np.array([5.0, 0.0]),
                points_allowed_all=np.array([0.0, 40.0]), yards_allowed=np.array([150.0, 480.0]))
    result = dst_scores(20, 2026, ESPN, n=2000, table=AnalogTable(cols))
    assert set(np.unique(result.scores.points)) == {5 + 5 + 3, -3 - 5}


# --- predictions (S6.5) ----------------------------------------------------------------


@pytest.fixture(scope="module")
def nflverse():
    return load_fixture_nflverse(FIXTURES, 2026)


@pytest.fixture(scope="module")
def books():
    return load_fixture_books(FIXTURES)


def run(names, nflverse, books, rules=ESPN):
    roster, schedule = nflverse
    targets_, skipped = resolve_targets(names, roster, schedule, BEFORE_MNF)
    results, unfitted = predict(targets_, books, rules)
    return results, skipped + unfitted


def test_a_kicker_is_predicted_from_game_lines(nflverse, books):
    [dicker], skipped = run(["Cameron Dicker"], nflverse, books)
    assert skipped == []
    assert (dicker.position, dicker.team, dicker.game) == ("K", "LAC", "LAC @ BAL")
    assert dicker.flags == ["from game lines"]
    assert dicker.stats["fg_attempts"].books == ["draftkings", "fanduel"]
    assert 0 <= dicker.percentiles[5] < dicker.percentiles[50] < dicker.percentiles[95] < 30


def test_a_dst_uses_its_td_prices(nflverse, books):
    [ravens], _ = run(["Ravens D/ST"], nflverse, books)
    assert ravens.position == "D/ST" and ravens.team == "BAL"
    assert ravens.flags == []  # both books price the Ravens D/ST to score
    assert ravens.stats["dst_tds"].books == ["draftkings", "fanduel"]
    assert ravens.stats["points_allowed"].books == ["draftkings", "fanduel"]  # no team total: the lines


def test_a_dst_with_no_markets_is_flagged(nflverse, books):
    no_team_markets = [BookOdds(b.book, b.games, b.markets, b.lines, []) for b in books]
    [ravens], _ = run(["BAL D/ST"], nflverse, no_team_markets)
    assert ravens.flags == ["from game lines (no team total or D/ST TD market)"]


def test_nflverse_lines_are_the_last_fallback(nflverse, books):
    no_lines = [BookOdds(b.book, b.games, b.markets) for b in books]
    [dicker], _ = run(["Cameron Dicker"], nflverse, no_lines)
    assert "Game Lines from nflverse's schedule (no Book lists them)" in dicker.flags
    roster, schedule = nflverse
    bare = [g if g.game_id != "2026_10_LAC_BAL" else ScheduledGame(g.game_id, g.season, g.week, g.home, g.away,
                                                                    g.kickoff) for g in schedule]
    [], [skip] = run(["Cameron Dicker"], (roster, bare), no_lines)
    assert skip.reason.startswith("no Game Lines")


def test_rules_without_a_dst_section_skip_the_dst(nflverse, books, tmp_path):
    path = tmp_path / "offense-only.json"
    path.write_text(json.dumps({"name": "offense-only", "offense": {"receptions": 1}}))
    [], [skip] = run(["Ravens D/ST"], nflverse, books, rules=load_rules(path))
    assert "no dst section" in skip.reason


class _TeamHttp:
    """Serves the Sprint 6 fixtures for IND @ HOU and records what was asked."""

    def __init__(self):
        from ffp.sources import live

        self.live, self.urls = live, []

    def get_json(self, url):
        self.urls.append(url)
        if url == self.live.DK_LEAGUE_URL:
            return read("dk_nfl_league_game_lines.json")
        if "/subcategories/16719" in url:
            return read("dk_nfl_team_totals.json")
        if "/subcategories/" in url:
            return {"events": [], "markets": [], "selections": []}
        if "content-managed-page" in url:
            return read("fd_nfl_league_game_lines.json")
        if "tab=popular" in url:
            return read("fd_nfl_event_popular_tab.json")
        if "event-page" in url:
            return {"attachments": {"events": {}, "markets": {}}}
        raise AssertionError(url)


def test_live_books_fetch_game_lines_team_totals_and_kicker_markets():
    from ffp.predict import load_live_books

    http = _TeamHttp()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    books = load_live_books(http, [IND_HOU], now, stats=[], kickers=True, dst_games=[IND_HOU], kicker_ladders=True)
    dk, fd = books
    assert GameLine("34118011", "draftkings", -12.5, 44.5) in dk.lines
    assert {m.player for m in dk.team_markets} == {"IND Colts", "HOU Texans"}
    assert {m.player for m in fd.team_markets} == {"Indianapolis Colts", "Houston Texans"}
    asked = " ".join(http.urls)
    for subcategory in ("17131", "17133", "17132", "17062"):  # FG Made, PAT Made, Kicking Pts (+ O/U)
        assert f"/subcategories/{subcategory}" in asked
    assert asked.count("eventId=36305232") == 2  # FanDuel: the popular and TD scorer tabs, this Game only

    targets_ = [t for t in resolve_targets(["Texans D/ST"], [], [IND_HOU], now)[0]]
    [texans], _ = predict(targets_, books, ESPN)
    assert texans.stats["points_allowed"].books == ["draftkings", "fanduel"]  # the Colts' team total
    assert texans.stats["points_allowed"].mean == pytest.approx(16, abs=1.5)


def test_only_dst_games_and_snapshot_runs_cost_extra_requests():
    # Review of PRs 9-11: FanDuel's popular and TD tabs came for every Game, and the kicker
    # Ladders (snapshot-only) on every run with a K.
    from ffp.predict import load_live_books

    http = _TeamHttp()
    now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
    dk, fd = load_live_books(http, [IND_HOU], now, stats=[], kickers=True)
    asked = " ".join(http.urls)
    assert "tab=popular" not in asked and not fd.team_markets and not dk.team_markets
    assert "/subcategories/17131" not in asked
    assert GameLine("34118011", "draftkings", -12.5, 44.5) in dk.lines  # a kicker still gets the Game Lines


def test_draftkings_kicker_ladders_parse_like_any_prop_market():
    # Snapshotted for a later Backtest (PLAN Sprint 6): the existing parser reads them.
    [fg] = draftkings.parse_prop_markets(read("dk_nfl_kicker_17131.json"), None, "fg_made")
    assert fg.player == "Spencer Shrader" and [r.threshold for r in fg.ladder] == [1, 2, 3, 4]
    [pts] = draftkings.parse_prop_markets(read("dk_nfl_kicker_17132.json"), read("dk_nfl_kicker_17062.json"),
                                          "kicking_points")
    assert pts.main_line.line == 5.5 and [r.threshold for r in pts.ladder] == [4, 8, 10]


def test_fixture_books_carry_the_team_totals():
    # Review of PRs 9-11: fixture runs left the team totals out, so the D/ST tilt went untested offline.
    from ffp.markets import TEAM_POINTS
    from ffp.predict import load_fixture_books

    dk, fd = load_fixture_books(FIXTURES)
    assert any(m.stat == TEAM_POINTS for m in dk.team_markets)
    assert any(m.stat == TEAM_POINTS for m in fd.team_markets)
