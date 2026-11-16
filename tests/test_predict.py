"""The Sprint 2 pipeline: INPUT.md names -> Players and next Games -> both books' Prop Markets -> fits."""

from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from ffp.markets import Game, MainLine, PropMarket, Rung
from ffp.predict import (
    BookOdds,
    load_fixture_books,
    load_fixture_nflverse,
    predict,
    resolve_targets,
)
from ffp.players import PROPS_NOT_POSTED, ScheduledGame
from ffp.scoring import load_rules

FIXTURES = Path(__file__).resolve().parents[1] / "research" / "fixtures"
BEFORE_MNF = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)  # LAC @ BAL kicks off 01:15Z on the 17th
RULES = load_rules("ppr")


@pytest.fixture(scope="module")
def nflverse():
    return load_fixture_nflverse(FIXTURES, 2026)


@pytest.fixture(scope="module")
def books():
    return load_fixture_books(FIXTURES)


def run(names, nflverse, books, now=BEFORE_MNF, **kwargs):
    roster, schedule = nflverse
    targets, skipped = resolve_targets(names, roster, schedule, now)
    results, unfitted = predict(targets, books, RULES, **kwargs)
    return results, skipped + unfitted


def test_fixture_books_are_draftkings_and_fanduel(books):
    assert [b.book for b in books] == ["draftkings", "fanduel"]
    for book in books:
        assert {m.stat for m in book.markets} == {"receiving_yards", "receptions", "rush_rec_tds"}, book.book


def test_a_wr_is_resolved_to_his_team_position_and_next_game(nflverse, books):
    [zay], skipped = run(["Zay Flowers"], nflverse, books)
    assert skipped == []
    assert (zay.team, zay.position, zay.game) == ("BAL", "WR", "LAC @ BAL")
    assert zay.kickoff == "2026-11-17T01:15:00Z"  # nflverse kickoff, not a book's
    assert not any("position assumed" in f for f in zay.flags)


def test_both_books_fit_one_stat_distribution(nflverse, books):
    # S2.5: DK and FD Rungs at different thresholds go into one fit, Main Lines weighted x3.
    seen = {}
    [zay], _ = run(
        ["Zay Flowers"], nflverse, books,
        on_fair_points=lambda t, stat, pts: seen.update({(t.name, stat): pts}),
    )
    detail = zay.stats["receiving_yards"]
    assert detail.books == ["draftkings", "fanduel"]

    def rungs(book):
        return sum(len(m.ladder) for m in book.markets if m.player == "Zay Flowers" and m.stat == "receiving_yards")

    assert detail.rungs == rungs(books[0]) + rungs(books[1])
    points = seen[("Zay Flowers", "receiving_yards")]
    # DK O/U 71.5 -> Y >= 72 and FD O/U 69.5 -> Y >= 70, each weighted 3 (merged with any Rung there).
    by_threshold = {p.threshold: p.weight for p in points}
    assert by_threshold[72] >= 3 and by_threshold[70] >= 3


def test_an_rb_is_skipped_until_rb_models_arrive(nflverse, books):
    results, skipped = run(["Derrick Henry"], nflverse, books)
    assert results == []
    [henry] = skipped
    assert henry.name == "Derrick Henry"
    assert "RB" in henry.reason and "not supported yet" in henry.reason


def test_an_unknown_name_is_skipped_with_the_resolution_error(nflverse, books):
    results, [skip] = run(["Not A Player"], nflverse, books)
    assert results == []
    assert "Not A Player" in skip.reason


def test_a_player_whose_game_has_no_props_gets_rerun_closer_to_kickoff(nflverse, books):
    # After MNF kicks off, Zay's next Game is week 11 at CAR, which no book in the fixtures lists.
    after_mnf = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)  # past the in-progress window
    results, [skip] = run(["Zay Flowers"], nflverse, books, now=after_mnf)
    assert results == []
    assert skip.reason == PROPS_NOT_POSTED


def test_a_player_in_a_listed_game_without_his_own_market_is_no_markets(nflverse):
    roster, schedule = nflverse
    game = Game("g1", "LA Chargers", "BAL Ravens", "2026-11-17T01:15:00Z", "draftkings", False)
    other = PropMarket("Mark Andrews", "receiving_yards", "g1", "draftkings",
                       MainLine(30.5, -110, -110, "draftkings"), (), "BAL Ravens")
    targets, _ = resolve_targets(["Zay Flowers"], roster, schedule, BEFORE_MNF)
    results, [skip] = predict(targets, [BookOdds("draftkings", [game], [other])], RULES)
    assert "no Prop Markets" in skip.reason


def test_book_games_are_matched_by_teams_not_by_name_or_id(nflverse):
    # FD spells teams out in full and has its own ids and a slightly different kickoff.
    roster, schedule = nflverse
    fd_game = Game("36275768", "Los Angeles Chargers", "Baltimore Ravens", "2026-11-17T01:21:00Z", "fanduel", False)
    market = PropMarket(
        "Zay Flowers", "receiving_yards", "36275768", "fanduel",
        MainLine(69.5, -113, -113, "fanduel"),
        (Rung(50, -250, "fanduel"), Rung(90, 200, "fanduel")),
        "Baltimore Ravens",
    )
    targets, _ = resolve_targets(["Zay Flowers"], roster, schedule, BEFORE_MNF)
    [zay], [] = predict(targets, [BookOdds("fanduel", [fd_game], [market])], RULES)
    assert zay.stats["receiving_yards"].books == ["fanduel"]


def test_a_book_game_that_has_started_is_not_used(nflverse):
    roster, schedule = nflverse
    started = Game("g1", "LA Chargers", "BAL Ravens", "2026-11-17T01:15:00Z", "draftkings", True)
    market = PropMarket("Zay Flowers", "receiving_yards", "g1", "draftkings",
                        MainLine(71.5, -113, -111, "draftkings"), (), "BAL Ravens")
    targets, _ = resolve_targets(["Zay Flowers"], roster, schedule, BEFORE_MNF)
    results, [skip] = predict(targets, [BookOdds("draftkings", [started], [market])], RULES)
    assert results == [] and skip.reason == PROPS_NOT_POSTED


def test_a_book_name_spelled_differently_still_matches(nflverse):
    # A book may drop a suffix or use a nickname the alias table knows.
    roster, schedule = nflverse
    game = Game("g1", "LA Chargers", "BAL Ravens", "2026-11-17T01:15:00Z", "draftkings", False)
    market = PropMarket("Ja'Kobi Lane", "receiving_yards", "g1", "draftkings",
                        MainLine(20.5, -110, -110, "draftkings"), (), "BAL Ravens")
    targets, _ = resolve_targets(["JaKobi Lane"], roster, schedule, BEFORE_MNF)
    [lane], [] = predict(targets, [BookOdds("draftkings", [game], [market])], RULES)
    assert lane.player == "JaKobi Lane"


def test_skips_keep_input_order(nflverse, books):
    _, skipped = run(["Not A Player", "Derrick Henry", "Zay Flowers", "Nobody Else"], nflverse, books)
    assert [s.name for s in skipped] == ["Not A Player", "Derrick Henry", "Nobody Else"]


def test_next_game_is_the_schedule_game(nflverse):
    roster, schedule = nflverse
    [target], _ = resolve_targets(["Tre Harris"], roster, schedule, BEFORE_MNF)
    assert isinstance(target.game, ScheduledGame)
    assert target.game.game_id == "2026_10_LAC_BAL"
    assert target.player.team == "LAC"


# Sprint 2 review findings.


def test_a_player_whose_game_is_in_progress_is_told_so(nflverse, books):
    # Finding 4: he was pointed at next week's Game and told "rerun closer to kickoff".
    during = datetime(2026, 11, 17, 2, 30, tzinfo=timezone.utc)
    results, [skip] = run(["Zay Flowers"], nflverse, books, now=during)
    assert results == []
    assert "already kicked off" in skip.reason and "LAC @ BAL" in skip.reason


class _FakeHttp:
    """Serves the committed fixtures by URL; `fail` names URLs (substrings) that error."""

    def __init__(self, fail=(), garble=()):
        import json as _json

        from ffp.sources import live

        league = _json.loads((FIXTURES / "dk_nfl_league.json").read_text())
        fd_tab = _json.loads((FIXTURES / "fd_nfl_event_receiving_tab.json").read_text())
        self.fail, self.garble, self.live = fail, garble, live
        self.pages = {"dk_league": league, "fd_tab": fd_tab}

    def get_json(self, url):
        import json as _json

        from ffp.sources.http import FetchError

        if any(f in url for f in self.fail):
            raise FetchError(f"refused: {url}")
        if any(g in url for g in self.garble):
            return {"events": [{"id": "1"}], "attachments": {"events": {"1": {"name": "A @ B"}}}}
        if url == self.live.DK_LEAGUE_URL:
            return self.pages["dk_league"]
        if "/subcategories/" in url:
            name = "dk_nfl_rec_yds_ladder.json" if "16570" in url else "dk_nfl_rec_yds_ou.json"
            return _json.loads((FIXTURES / name).read_text())
        if "content-managed-page" in url:
            tab = self.pages["fd_tab"]
            return {"attachments": {"events": tab["attachments"]["events"]}}
        if "event-page" in url:
            return self.pages["fd_tab"]
        raise AssertionError(url)


def _live(http, nflverse):
    from ffp.predict import load_live_books

    _, schedule = nflverse
    game = next(g for g in schedule if g.game_id == "2026_10_LAC_BAL")
    return load_live_books(http, [game], BEFORE_MNF)


def test_live_books_from_fixtures(nflverse):
    books = _live(_FakeHttp(), nflverse)
    assert [b.book for b in books] == ["draftkings", "fanduel"]
    assert all(b.markets for b in books)


def test_a_book_whose_response_does_not_parse_is_left_out(nflverse, caplog):
    # Finding 2: a parse error in one book crashed the whole run.
    books = _live(_FakeHttp(garble=["/subcategories/"]), nflverse)
    assert [b.book for b in books] == ["fanduel"]
    assert "DraftKings" in caplog.text and "left out" in caplog.text


def test_one_failed_fanduel_event_keeps_the_others(nflverse, caplog):
    # Finding 3: one failed FD event page threw away every FD market.
    http = _FakeHttp(fail=["eventId=999"])
    tab = http.pages["fd_tab"]
    [(event_id, event)] = tab["attachments"]["events"].items()
    # The league page also lists a second event for the same Game (as after a re-listing), id 999.
    events = {event_id: event, "999": dict(event, eventId=999)}
    http.pages["fd_tab"] = {**tab, "attachments": {**tab["attachments"], "events": events}}
    books = _live(http, nflverse)
    fd = next(b for b in books if b.book == "fanduel")
    assert fd.markets  # the good event's markets survive
    assert "999" in caplog.text


def test_a_stat_that_fails_to_parse_keeps_the_others_on_its_fanduel_tab(nflverse, caplog, monkeypatch):
    # Review finding 3: yards and receptions share a tab, and a yards parse error lost receptions too.
    from ffp.sources import fanduel

    parse = fanduel.parse_prop_markets

    def broken_yards(payload, stat):
        if stat == "receiving_yards":
            raise KeyError("runners")
        return parse(payload, stat)

    monkeypatch.setattr(fanduel, "parse_prop_markets", broken_yards)
    fd = next(b for b in _live(_FakeHttp(), nflverse) if b.book == "fanduel")
    assert {m.stat for m in fd.markets} == {"receptions"}
    assert "receiving yards" in caplog.text and "KeyError" in caplog.text


# Sprint 3: every WR/TE Scoring Stat, correlated.

SPRINT_3_STATS = {"receptions", "receiving_yards", "receiving_tds", "rushing_tds", "fumbles_lost",
                  "rushing_2pt", "receiving_2pt"}


def _capture_simulation(monkeypatch):
    import ffp.predict

    calls = []
    real = ffp.predict.simulate_scores

    def spy(stat_dists, rules, **kwargs):
        calls.append((dict(stat_dists), kwargs))
        return real(stat_dists, rules, **kwargs)

    monkeypatch.setattr(ffp.predict, "simulate_scores", spy)
    return calls


def test_a_wr_with_every_market_gets_every_scoring_stat(nflverse, books, monkeypatch):
    calls = _capture_simulation(monkeypatch)
    [zay], [] = run(["Zay Flowers"], nflverse, books)
    [(dists, kwargs)] = calls
    assert set(dists) == SPRINT_3_STATS
    assert {"receptions", "receiving_yards", "receiving_tds", "rushing_tds"} <= set(zay.stats)
    assert not any(f.startswith("incomplete") for f in zay.flags)
    assert not any("receiving yards only" in f for f in zay.flags)
    # Receiving yards' P(0) is the receptions' P(0): no catch, no yards (research Q4).
    p_no_catch = 1 - dists["receptions"].sf(np.array([1]))[0]
    assert dists["receiving_yards"].pi0 == pytest.approx(p_no_catch, rel=1e-9)
    # TDs are split by the WR's rushing share; together they keep the anytime price's rate.
    from ffp import params

    share = params.load("base_rates")["td_rushing_share"]["WR"]
    total = dists["rushing_tds"].mean + dists["receiving_tds"].mean
    assert dists["rushing_tds"].mean == pytest.approx(share * total)


def test_the_simulation_is_correlated_with_the_chosen_matrix(nflverse, books, monkeypatch):
    # S3.6: the config selects the matrix.
    from ffp import params

    for kind in ("within", "pooled"):
        calls = _capture_simulation(monkeypatch)
        run(["Mark Andrews"], nflverse, books, correlation=kind)
        [(_, kwargs)] = calls
        stats, matrix = kwargs["corr"]
        expected_stats, expected = params.correlation("TE", kind)
        assert list(stats) == expected_stats
        np.testing.assert_array_equal(matrix, expected)


def _only(stat_markets, *, stats):
    """The fixture books with only these stats' markets."""
    return [BookOdds(b.book, b.games, [m for m in b.markets if m.stat in stats]) for b in stat_markets]


def test_a_wr_with_no_receptions_market_is_flagged_incomplete(nflverse, books, monkeypatch):
    # S3.5
    calls = _capture_simulation(monkeypatch)
    [zay], [] = run(["Zay Flowers"], nflverse, _only(books, stats={"receiving_yards", "rush_rec_tds"}))
    assert "incomplete: missing receptions" in zay.flags
    [(dists, _)] = calls
    assert "receptions" not in dists
    # Without receptions, the yards P(0) is fitted from the yards markets as before.
    assert dists["receiving_yards"].pi0 > 0


def test_a_wr_with_no_td_market_is_flagged_incomplete(nflverse, books):
    [zay], [] = run(["Zay Flowers"], nflverse, _only(books, stats={"receiving_yards", "receptions"}))
    assert "incomplete: missing touchdowns" in zay.flags
    assert "receiving_tds" not in zay.stats


def test_a_wr_with_only_a_td_market_is_predicted_and_flagged(nflverse, books):
    [zay], [] = run(["Zay Flowers"], nflverse, _only(books, stats={"rush_rec_tds"}))
    assert "incomplete: missing receptions" in zay.flags
    assert "incomplete: missing receiving yards" in zay.flags


def test_one_failed_fit_flags_that_stat_and_keeps_the_rest(nflverse, books, monkeypatch):
    import ffp.predict
    from ffp.distributions import FitError

    def fail(points, position):
        raise FitError("out of reach")

    monkeypatch.setattr(ffp.predict, "fit_receptions", fail)
    [zay], [] = run(["Zay Flowers"], nflverse, books)
    assert "incomplete: could not fit receptions (out of reach)" in zay.flags
    assert "receiving_yards" in zay.stats


def test_a_player_whose_every_fit_fails_is_skipped(nflverse, books, monkeypatch):
    import ffp.predict
    from ffp.distributions import FitError

    def fail(*args, **kwargs):
        raise FitError("out of reach")

    for name in ("fit_receptions", "fit_receiving_yards", "fit_touchdowns"):
        monkeypatch.setattr(ffp.predict, name, fail)
    results, [skip] = run(["Zay Flowers"], nflverse, books)
    assert results == []
    assert skip.reason.startswith("could not fit any of its markets")


def test_fair_points_are_reported_per_stat(nflverse, books):
    seen = []
    run(["Zay Flowers"], nflverse, books, on_fair_points=lambda t, stat, pts: seen.append(stat))
    assert sorted(seen) == ["receiving_yards", "receptions", "rush_rec_tds"]


def test_td_prices_are_devigged_with_the_td_prior_not_the_two_sided_one(nflverse, books):
    # Review finding 2: anytime/2+/3+ TD prices are one-sided and carry more vig than a −115/−115 line.
    from ffp.fair import TD_PRIOR_C
    from ffp.odds import american_to_implied, apply_logit_shift

    seen = {}
    run(["Zay Flowers"], nflverse, _only(books, stats={"rush_rec_tds"}),
        on_fair_points=lambda t, stat, pts: seen.update({stat: pts}))
    anytime = {p.threshold: p for p in seen["rush_rec_tds"]}[1]
    implied = [american_to_implied(r.odds) for b in books for m in b.markets
               if m.player == "Zay Flowers" and m.stat == "rush_rec_tds" for r in m.ladder if r.threshold == 1]
    expected = sum(apply_logit_shift(q, TD_PRIOR_C) for q in implied) / len(implied)
    assert anytime.prob == pytest.approx(expected, abs=1e-9)
