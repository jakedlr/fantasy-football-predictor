"""S11.2 `ffp slate`: every priced player in the NFL Week's Games that haven't kicked off, plus each
team's K and D/ST, as the Expected Points push (site/schema/expected-points.schema.json)."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ffp import slate
from ffp.cli import main
from ffp.markets import Game, MainLine, PropMarket
from ffp.players import ScheduledGame
from ffp.predict import BookOdds, load_fixture_books, load_fixture_nflverse

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "research" / "fixtures"
SCHEMA = json.loads((ROOT / "site" / "schema" / "expected-points.schema.json").read_text())
EXAMPLE = json.loads((ROOT / "site" / "schema" / "examples" / "expected-points.json").read_text())
BEFORE_MNF = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)  # LAC @ BAL, week 10, kicks off 01:15Z on the 17th


def utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def game(week, away, home, kickoff, season=2026, scored=False):
    return ScheduledGame(f"{season}_{week:02}_{away}_{home}", season, week, home, away, kickoff,
                         *((24, 17) if scored else (None, None)))


SCHEDULE = [
    game(11, "BUF", "HOU", "2026-11-20T01:15:00Z"),  # Thursday
    game(11, "BAL", "CAR", "2026-11-22T18:00:00Z"),
    game(11, "NYJ", "LAC", "2026-11-22T21:05:00Z"),
    game(11, "CHI", "LA", "2026-11-24T01:15:00Z"),  # Monday night
    game(12, "LAC", "BAL", "2026-11-27T01:15:00Z"),
]


# --- the NFL Week ------------------------------------------------------------------


def test_the_week_runs_from_the_wednesday_after_the_last_week_s_last_kickoff():
    # ESPN's weeks start Wednesday 3 AM Eastern (08:00Z in November), as the Leaderboard's do.
    assert slate.current_week(SCHEDULE, utc("2026-11-19T12:00:00Z")).number == 11
    assert slate.current_week(SCHEDULE, utc("2026-11-25T07:59:00Z")).number == 11  # Tuesday: still week 11
    assert slate.current_week(SCHEDULE, utc("2026-11-25T08:00:00Z")).number == 12
    assert slate.current_week(SCHEDULE, utc("2026-09-01T12:00:00Z")).number == 11  # before the first: the first


def test_the_week_s_games_and_those_that_have_not_kicked_off():
    week = slate.current_week(SCHEDULE, utc("2026-11-22T19:00:00Z"))  # Sunday: the early Game is on
    assert [g.game_id for g in week.games] == [g.game_id for g in SCHEDULE[:4]]
    assert [g.game_id for g in week.upcoming(utc("2026-11-22T19:00:00Z"))] == ["2026_11_NYJ_LAC", "2026_11_CHI_LA"]
    assert week.teams == {"BUF", "HOU", "BAL", "CAR", "NYJ", "LAC", "CHI", "LA"}


def test_postseason_weeks_are_numbered_as_espn_and_sleeper_number_them():
    playoffs = [game(18, "BAL", "PIT", "2027-01-10T18:00:00Z"), game(19, "BUF", "HOU", "2027-01-16T21:30:00Z"),
                game(20, "BUF", "KC", "2027-01-23T21:30:00Z"), game(21, "KC", "BAL", "2027-01-31T20:00:00Z"),
                game(22, "BAL", "SF", "2027-02-14T23:30:00Z")]
    assert slate.current_week(playoffs, utc("2027-01-11T12:00:00Z")).label == ("regular", 18)
    assert slate.current_week(playoffs, utc("2027-01-14T12:00:00Z")).label == ("postseason", 1)  # Wild Card
    assert slate.current_week(playoffs, utc("2027-01-28T12:00:00Z")).label == ("postseason", 3)
    # The Super Bowl week starts the Wednesday after the conference games (ESPN: Feb 3), its number 4.
    assert slate.current_week(playoffs, utc("2027-02-04T12:00:00Z")).label == ("postseason", 4)


# --- the players -------------------------------------------------------------------


@pytest.fixture(scope="module")
def nflverse():
    return load_fixture_nflverse(FIXTURES, 2026)


@pytest.fixture(scope="module")
def books():
    return load_fixture_books(FIXTURES)


def fixture_week(nflverse):
    return slate.current_week(nflverse[1], BEFORE_MNF)


def test_every_priced_player_in_the_week_s_games_is_a_target(nflverse, books):
    week = fixture_week(nflverse)
    targets, skipped = slate.slate_targets(week.upcoming(BEFORE_MNF), books, nflverse[0])
    names = {t.player.name for t in targets}
    assert {"Zay Flowers", "Mark Andrews", "Derrick Henry", "Justin Herbert", "Quentin Johnston"} <= names
    assert {t.game.game_id for t in targets} == {"2026_10_LAC_BAL"}
    # Players in IND @ HOU (priced, but not on this schedule) are not.
    assert all(t.player.team in ("BAL", "LAC") for t in targets)
    assert len({t.player.gsis_id for t in targets}) == len(targets)  # once each, though both Books price him


def test_each_team_gets_its_kicker_and_dst(nflverse, books):
    targets, skipped = slate.slate_targets(fixture_week(nflverse).upcoming(BEFORE_MNF), books, nflverse[0])
    team_targets = {(t.player.position, t.player.team): t.player.name for t in targets if t.player.position in ("K", "DST")}
    assert team_targets == {("K", "LAC"): "Cameron Dicker", ("DST", "BAL"): "BAL D/ST", ("DST", "LAC"): "LAC D/ST"}
    assert [(s.name, s.reason) for s in skipped if "kicker" in s.reason] == [("BAL K", slate.NO_KICKER)]


def test_without_an_active_kicker_the_practice_squad_one_kicks(nflverse, books):
    # Week 11, 2026: MIA cut its kicker and had only Daniel Carlson on the practice squad (DEV) midweek.
    from ffp.players import RosterEntry

    roster = [*nflverse[0], RosterEntry("00-0034161", "Daniel Carlson", "K", "BAL", "DEV", 10),
              RosterEntry("00-0000001", "Cut Kicker", "K", "BAL", "RES", 9)]
    targets, skipped = slate.slate_targets(fixture_week(nflverse).upcoming(BEFORE_MNF), books, roster)
    assert ("K", "BAL", "Daniel Carlson") in {(t.player.position, t.player.team, t.player.name) for t in targets}
    assert not [s for s in skipped if s.reason == slate.NO_KICKER]
    # An active one comes first.
    roster.append(RosterEntry("00-0000002", "Active Kicker", "K", "BAL", "ACT", 9))
    targets, _ = slate.slate_targets(fixture_week(nflverse).upcoming(BEFORE_MNF), books, roster)
    assert "Active Kicker" in {t.player.name for t in targets} and "Daniel Carlson" not in {t.player.name for t in targets}


def test_a_book_name_no_roster_has_is_logged_not_fatal(nflverse, caplog):
    week = fixture_week(nflverse)
    dk_game = Game("g1", "LA Chargers", "BAL Ravens", "2026-11-17T01:15:00Z", "draftkings", False)
    markets = [PropMarket(name, "receiving_yards", "g1", "draftkings", MainLine(30.5, -110, -110, "draftkings"), (),
                          team) for name, team in (("Nobody Here", "BAL Ravens"), ("Zay Flowers", "BAL Ravens"))]
    with caplog.at_level(logging.WARNING):
        targets, skipped = slate.slate_targets(week.upcoming(BEFORE_MNF), [BookOdds("draftkings", [dk_game], markets)],
                                               nflverse[0])
    assert "Zay Flowers" in {t.player.name for t in targets}
    assert "Nobody Here" in caplog.text
    assert any(s.name == "Nobody Here" for s in skipped)


def test_a_name_on_both_teams_is_told_apart_by_the_book_s_team(nflverse):
    from ffp.players import RosterEntry

    roster = [*nflverse[0], RosterEntry("00-0099999", "Zay Flowers", "WR", "LAC", "ACT", 10)]
    dk_game = Game("g1", "LA Chargers", "BAL Ravens", "2026-11-17T01:15:00Z", "draftkings", False)
    market = PropMarket("Zay Flowers", "receiving_yards", "g1", "draftkings", MainLine(30.5, -110, -110, "draftkings"),
                        (), "LA Chargers")
    targets, _ = slate.slate_targets(fixture_week(nflverse).upcoming(BEFORE_MNF),
                                     [BookOdds("draftkings", [dk_game], [market])], roster)
    [zay] = [t for t in targets if t.player.name == "Zay Flowers"]
    assert zay.player.gsis_id == "00-0099999"


# --- the push file -----------------------------------------------------------------


def run_slate(tmp_path, *options, now="2026-11-16T12:00:00Z"):
    out = tmp_path / "expected-points.json"
    code = main(["slate", "--fixtures", str(FIXTURES), "--now", now, "--output-dir", str(tmp_path), "--jobs", "1",
                 *options])
    return code, (json.loads(out.read_text()) if out.exists() else None)


def test_the_schema_s_own_example_validates():
    Draft202012Validator(SCHEMA).validate(EXAMPLE)


@pytest.fixture(scope="module")
def slate_run(tmp_path_factory):
    import contextlib
    import io

    where, out = tmp_path_factory.mktemp("slate"), io.StringIO()
    with contextlib.redirect_stdout(out):
        code, data = run_slate(where)
    assert code == 0
    return data, out.getvalue(), where / "expected-points.json"


@pytest.fixture(scope="module")
def push(slate_run):
    return slate_run[0]


def test_the_push_validates_against_the_schema(push):
    errors = sorted(Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER).iter_errors(push),
                    key=str)
    assert errors == []


def test_the_push_is_for_the_week_with_its_run_time(push):
    assert (push["schemaVersion"], push["season"], push["seasonType"], push["week"]) == (1, 2026, "regular", 10)
    assert push["generatedAt"].endswith("Z")
    generated = utc(push["generatedAt"])
    assert generated == BEFORE_MNF  # --now when given; the real clock otherwise


def test_output_file_overrides_the_output_dir(tmp_path):
    out = tmp_path / "sub" / "push.json"
    code = main(["slate", "--fixtures", str(FIXTURES), "--now", "2026-11-17T02:00:00Z", "--output", str(out),
                 "--output-dir", str(tmp_path / "unused")])
    assert code == 0 and json.loads(out.read_text())["players"] == []
    assert not (tmp_path / "unused").exists()


def test_without_now_generated_at_is_the_real_clock(tmp_path, monkeypatch):
    import ffp.slate

    monkeypatch.setattr(ffp.slate, "current_week", lambda schedule, now: ffp.slate.NflWeek(2026, 10, ()))
    monkeypatch.setattr(ffp.slate.pipeline, "load_fixture_nflverse", lambda fixtures, season: ([], []))
    assert main(["slate", "--fixtures", str(FIXTURES), "--output-dir", str(tmp_path)]) == 0
    generated = utc(json.loads((tmp_path / "expected-points.json").read_text())["generatedAt"])
    assert abs((datetime.now(timezone.utc) - generated).total_seconds()) < 60


def test_an_unwritable_output_exits_1(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    code = main(["slate", "--fixtures", str(FIXTURES), "--now", "2026-11-17T02:00:00Z", "--output-dir", str(blocker)])
    assert code == 1


def test_every_player_is_keyed_by_sleeper_id_with_kickoff_means_and_expected_points(push):
    players = {p["name"]: p for p in push["players"]}
    zay = players["Zay Flowers"]
    assert (zay["sleeperId"], zay["gsisId"], zay["team"], zay["position"]) == ("9997", "00-0039064", "BAL", "WR")
    assert zay["kickoff"] == "2026-11-17T01:15:00Z"
    assert zay["expected"]["ppr"] - zay["expected"]["standard"] == pytest.approx(zay["statMeans"]["receptions"], abs=2e-3)
    assert players["Justin Herbert"]["espnId"] == "4038941"  # from Sleeper's list
    k = players["Cameron Dicker"]
    assert k["position"] == "K" and "fgm_40_49" in k["statMeans"]
    dst = players["Ravens D/ST"]
    assert (dst["sleeperId"], dst["gsisId"], dst["espnId"], dst["position"]) == ("BAL", None, None, "D/ST")
    assert "points_allowed_all" in dst["statMeans"]
    assert len(players) == len(push["players"])


def test_players_without_a_sleeper_id_are_left_out_and_reported(slate_run, caplog):
    data, out, _ = slate_run
    names = {p["name"] for p in data["players"]}
    assert "Matthew Hibner" not in names  # on BAL's roster and priced, not in the recorded Sleeper list
    assert f"1 {slate.NO_SLEEPER_ID}" in out


def test_the_id_map_covers_the_week_s_teams(push):
    assert push["idMap"]["5995"] == "4038441"  # Justice Hill, BAL
    assert push["idMap"]["6797"] == "4038941"  # Justin Herbert, LAC
    assert all(k.isdigit() and v.isdigit() for k, v in push["idMap"].items())


def test_the_summary_says_how_many_players_and_why_others_were_skipped(slate_run):
    data, out, path = slate_run
    assert f"Wrote {path}: {len(data['players'])} players in 1 of week 10's 1 Games" in out
    assert f"{path.stat().st_size:,} bytes" in out
    assert f"1 {slate.NO_KICKER}" in out  # the fixture roster has no BAL kicker


def test_once_every_game_has_kicked_off_the_push_has_no_players(tmp_path):
    code, data = run_slate(tmp_path, now="2026-11-17T02:00:00Z")  # MNF is on
    assert code == 0
    assert data["players"] == [] and data["week"] == 10
    Draft202012Validator(SCHEMA).validate(data)


def test_both_books_failing_writes_nothing_and_exits_1(tmp_path, monkeypatch):
    import ffp.slate

    monkeypatch.setattr(ffp.slate, "_load_books", lambda *a, **k: ([], None))
    code, data = run_slate(tmp_path)
    assert code == 1 and data is None


def test_stat_means_are_never_negative():
    # The schema's minimum is 0: a fitted mean a hair below it (rushing yards for a QB who kneels) is 0.
    assert slate.stat_means({"rushing_yards": -0.4, "receptions": 5.25}) == {"rushing_yards": 0.0, "receptions": 5.25}
