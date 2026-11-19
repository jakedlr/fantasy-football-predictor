"""scripts/record_live.py (S9.1 Live test): fake network and clock, never the real thing."""

import gzip
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("record_live", ROOT / "scripts" / "record_live.py")
rl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rl)

EVENT = "401873066"
T0 = datetime(2026, 11, 20, 1, 15, tzinfo=timezone.utc)


def scoreboard(state="in", period=1, clock="15:00", leaders=None, events_extra=()):
    event = {
        "id": EVENT,
        "date": "2026-11-20T01:15Z",
        "status": {"period": period, "displayClock": clock, "type": {"state": state, "name": "STATUS_X"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "score": "7", "team": {"abbreviation": "HOU"}},
                {"homeAway": "away", "score": "3", "team": {"abbreviation": "IND"}},
            ],
            "leaders": leaders or [{"name": "passingYards", "leaders": [{"value": 10}]}],
            "situation": {"lastPlay": {"id": "p9"}},
        }],
    }
    return {"season": {"year": 2026, "type": 2}, "week": {"number": 11}, "events": [event, *events_extra]}


def summary(plays=3, players=None, state="in", period=1, clock="12:00"):
    return {
        "boxscore": {"teams": [], "players": players if players is not None else [{"team": {"abbreviation": "HOU"}}]},
        "header": {"competitions": [{"status": {"period": period, "displayClock": clock, "type": {"state": state}}}]},
        "drives": {
            "previous": [{"plays": [{"id": str(i)} for i in range(plays - 1)]}],
            "current": {"plays": [{"id": "c"}]},
        },
    }


def sleeper(rows=None):
    return rows if rows is not None else [
        {"player_id": "1", "team": "HOU", "stats": {"rec": 1.0}, "last_modified": 1794578916102},
        {"player_id": "2", "team": "IND", "stats": {"pass_yd": 9.0}, "last_modified": 1794578900000},
    ]


def ok(obj, headers=None):
    return (200, json.dumps(obj).encode(), headers or {})


# ------------------------------------------------------------ URLs and extraction


def test_source_urls_cover_the_three_free_sources():
    urls = rl.source_urls(EVENT, season=2026, week=11)
    assert urls["espn-scoreboard"] == "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    assert urls["espn-summary"].endswith(f"/summary?event={EVENT}")
    assert urls["sleeper-stats"].startswith("https://api.sleeper.com/stats/nfl/2026/11?season_type=regular")
    for pos in ("QB", "RB", "WR", "TE", "K", "DEF"):
        assert f"position[]={pos}" in urls["sleeper-stats"]


def test_scoreboard_extraction_reads_the_games_clock_period_and_leaders():
    info = rl.extract("espn-scoreboard", json.dumps(scoreboard(period=2, clock="7:41")).encode(), EVENT)
    assert info["state"] == "in"
    assert info["period"] == 2
    assert info["clock"] == "7:41"
    assert info["score"] == "IND 3 - HOU 7"
    assert info["last_play"] == "p9"
    assert len(info["stats_hash"]) == 16


def test_scoreboard_hash_changes_only_when_the_leaders_change():
    a = rl.extract("espn-scoreboard", json.dumps(scoreboard(clock="7:41")).encode(), EVENT)
    b = rl.extract("espn-scoreboard", json.dumps(scoreboard(clock="7:00")).encode(), EVENT)
    c = rl.extract("espn-scoreboard", json.dumps(
        scoreboard(leaders=[{"name": "passingYards", "leaders": [{"value": 25}]}])).encode(), EVENT)
    assert a["stats_hash"] == b["stats_hash"] != c["stats_hash"]


def test_summary_extraction_counts_plays_including_the_current_drive():
    info = rl.extract("espn-summary", json.dumps(summary(plays=5, period=3, clock="1:02")).encode(), EVENT)
    assert info["plays"] == 5
    assert (info["state"], info["period"], info["clock"]) == ("in", 3, "1:02")


def test_summary_hash_is_of_the_box_score_players_only():
    a = rl.extract("espn-summary", json.dumps(summary(plays=5)).encode(), EVENT)
    b = rl.extract("espn-summary", json.dumps(summary(plays=6)).encode(), EVENT)
    c = rl.extract("espn-summary", json.dumps(summary(players=[{"x": 1}])).encode(), EVENT)
    assert a["stats_hash"] == b["stats_hash"] != c["stats_hash"]


def test_sleeper_hash_ignores_row_order_and_metadata():
    rows = sleeper()
    shuffled = [dict(rows[1], last_modified=1), rows[0]]
    a = rl.extract("sleeper-stats", json.dumps(rows).encode(), EVENT)
    b = rl.extract("sleeper-stats", json.dumps(shuffled).encode(), EVENT)
    assert a["stats_hash"] == b["stats_hash"]
    assert a["rows"] == 2
    assert a["last_modified"] == 1794578916102
    changed = [dict(rows[0], stats={"rec": 2.0}), rows[1]]
    assert rl.extract("sleeper-stats", json.dumps(changed).encode(), EVENT)["stats_hash"] != a["stats_hash"]


def test_an_unparseable_body_is_reported_not_raised():
    info = rl.extract("espn-summary", b"<html>Access Denied</html>", EVENT)
    assert info["stats_hash"] is None
    assert "parse" in info["error"]


def test_pick_event_takes_the_earliest_game_not_yet_final():
    sb = scoreboard(state="post")
    later = {"id": "2", "date": "2026-11-22T18:00Z", "status": {"type": {"state": "pre"}}}
    sooner = {"id": "3", "date": "2026-11-21T18:00Z", "status": {"type": {"state": "pre"}}}
    sb["events"] += [later, sooner]
    assert rl.pick_event(sb) == "3"


# ------------------------------------------------------------ polling


class FakeClock:
    def __init__(self, start):
        self.t = start
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += timedelta(seconds=seconds)


class FakeFetch:
    """Answers by source, from a function of the poll number; advances the clock by the latency."""

    def __init__(self, clock, answers, latency=0.25):
        self.clock, self.answers, self.latency = clock, answers, latency
        self.calls = []

    def __call__(self, url, headers=None):
        self.calls.append((url, headers))
        self.clock.t += timedelta(seconds=self.latency)
        source = rl.source_of(url)
        n = len([u for u, _ in self.calls if rl.source_of(u) == source])
        answer = self.answers[source]
        answer = answer(n) if callable(answer) else answer
        if isinstance(answer, Exception):
            raise answer
        return answer


def manifest(out):
    return [json.loads(line) for line in (out / "manifest-home.jsonl").read_text().splitlines()]


def test_poll_once_saves_gzipped_bodies_and_one_manifest_line_per_request(tmp_path):
    clock = FakeClock(T0)
    fetch = FakeFetch(clock, {
        "espn-scoreboard": ok(scoreboard(period=2, clock="7:41")),
        "espn-summary": ok(summary(plays=4), {"Cache-Control": "max-age=1"}),
        "sleeper-stats": ok(sleeper(), {"age": "161", "cf-cache-status": "HIT"}),
    })
    rec = rl.Recorder(tmp_path, EVENT, season=2026, week=11, vantage="home", fetch=fetch, now=clock.now)
    rows = rec.poll_once()
    assert [r["source"] for r in rows] == ["espn-scoreboard", "espn-summary", "sleeper-stats"]
    lines = manifest(tmp_path)
    assert lines == rows
    sb = lines[0]
    assert sb["vantage"] == "home"
    assert sb["status"] == 200
    assert sb["latency_ms"] == 250
    assert sb["bytes"] == len(json.dumps(scoreboard(period=2, clock="7:41")).encode())
    assert (sb["period"], sb["clock"]) == (2, "7:41")
    assert sb["ts"] == "2026-11-20T01:15:00Z"
    saved = tmp_path / sb["file"]
    assert saved.parent.name == "espn-scoreboard" and saved.name.endswith(".json.gz")
    assert json.loads(gzip.decompress(saved.read_bytes()))["week"]["number"] == 11
    assert lines[1]["cache_control"] == "max-age=1"
    assert lines[2]["age"] == "161" and lines[2]["cf_cache_status"] == "HIT"


def test_requests_carry_a_polite_user_agent_without_personal_details(tmp_path):
    clock = FakeClock(T0)
    fetch = FakeFetch(clock, {s: ok(scoreboard()) for s in rl.SOURCES})
    rl.Recorder(tmp_path, EVENT, 2026, 11, "home", fetch=fetch, now=clock.now).poll_once()
    agents = {h["User-Agent"] for _, h in fetch.calls}
    assert agents == {rl.USER_AGENT}
    assert "@" not in rl.USER_AGENT and "ffp" in rl.USER_AGENT


def test_a_network_error_is_logged_in_the_manifest_with_no_status(tmp_path):
    clock = FakeClock(T0)
    fetch = FakeFetch(clock, {
        "espn-scoreboard": ok(scoreboard()),
        "espn-summary": OSError("timed out"),
        "sleeper-stats": ok(sleeper()),
    })
    rl.Recorder(tmp_path, EVENT, 2026, 11, "home", fetch=fetch, now=clock.now).poll_once()
    row = manifest(tmp_path)[1]
    assert row["status"] is None
    assert "timed out" in row["error"]
    assert row["file"] is None


@pytest.mark.parametrize("status", [403, 429])
def test_a_block_backs_that_source_off_for_doubling_numbers_of_polls(tmp_path, status, capsys):
    clock = FakeClock(T0)
    blocked = (status, b"<html>Access Denied</html>", {})
    fetch = FakeFetch(clock, {
        "espn-scoreboard": ok(scoreboard()),
        "espn-summary": ok(summary()),
        "sleeper-stats": blocked,
    })
    rec = rl.Recorder(tmp_path, EVENT, 2026, 11, "home", fetch=fetch, now=clock.now)
    for _ in range(6):
        rec.poll_once()
    sleeper_rows = [r for r in manifest(tmp_path) if r["source"] == "sleeper-stats"]
    # poll 1 blocked -> skip 1; poll 3 blocked -> skip 2; poll 6 blocked
    assert [r["status"] for r in sleeper_rows] == [status, None, status, None, None, status]
    assert [r.get("skipped") for r in sleeper_rows] == [None, "backoff", None, "backoff", "backoff", None]
    assert f"HTTP {status}" in capsys.readouterr().err
    assert len([1 for u, _ in fetch.calls if rl.source_of(u) == "espn-summary"]) == 6


def test_a_success_after_a_block_resets_the_backoff(tmp_path):
    clock = FakeClock(T0)
    answers = {1: (429, b"", {}), 3: ok(sleeper())}
    fetch = FakeFetch(clock, {
        "espn-scoreboard": ok(scoreboard()),
        "espn-summary": ok(summary()),
        "sleeper-stats": lambda n: answers.get(n, ok(sleeper())),
    })
    rec = rl.Recorder(tmp_path, EVENT, 2026, 11, "home", fetch=fetch, now=clock.now)
    for _ in range(4):
        rec.poll_once()
    statuses = [r["status"] for r in manifest(tmp_path) if r["source"] == "sleeper-stats"]
    assert statuses == [429, None, 200, 200]


# ------------------------------------------------------------ the run loop


def run(tmp_path, answers, start=T0, **kw):
    clock = FakeClock(T0 - timedelta(minutes=5))
    fetch = FakeFetch(clock, answers)
    rec = rl.Recorder(tmp_path, EVENT, 2026, 11, "home", fetch=fetch, now=clock.now)
    kw.setdefault("max_minutes", 300)
    polls = rl.run(rec, start=start, now=clock.now, sleep=clock.sleep, **kw)
    return polls, clock


def test_run_waits_for_the_start_time_then_polls_every_interval_until_final_plus_tail(tmp_path):
    def sb(n):
        return ok(scoreboard(state="post" if n >= 3 else "in"))

    polls, clock = run(tmp_path, {"espn-scoreboard": sb, "espn-summary": ok(summary()), "sleeper-stats": ok(sleeper())},
                       interval=60, after_final_minutes=2)
    # Final seen on poll 3 at T0+2min; tail keeps polling through T0+4min.
    assert polls == 5
    ts = [r["ts"] for r in manifest(tmp_path) if r["source"] == "espn-scoreboard"]
    assert ts == [f"2026-11-20T01:{15 + i}:00Z" for i in range(5)]
    assert clock.sleeps[0] == pytest.approx(300)


def test_run_stops_at_the_max_duration_even_if_the_game_never_ends(tmp_path):
    answers = {"espn-scoreboard": ok(scoreboard()), "espn-summary": ok(summary()), "sleeper-stats": ok(sleeper())}
    polls, _ = run(tmp_path, answers, interval=60, max_minutes=10)
    assert polls == 10


def test_a_start_time_in_the_past_starts_at_once(tmp_path):
    answers = {"espn-scoreboard": ok(scoreboard(state="post")), "espn-summary": ok(summary()),
               "sleeper-stats": ok(sleeper())}
    polls, clock = run(tmp_path, answers, start=T0 - timedelta(hours=1), interval=60, after_final_minutes=0)
    assert polls == 1
    assert clock.sleeps == []


def test_main_parses_arguments_and_uses_the_injected_network(tmp_path):
    clock = FakeClock(T0)
    fetch = FakeFetch(clock, {"espn-scoreboard": ok(scoreboard(state="post")), "espn-summary": ok(summary()),
                              "sleeper-stats": ok(sleeper())})
    code = rl.main(["--out", str(tmp_path), "--event", EVENT, "--after-final", "0", "--vantage", "home"],
                   fetch=fetch, now=clock.now, sleep=clock.sleep)
    assert code == 0
    assert len(manifest(tmp_path)) == 3
    assert "week=11" not in fetch.calls[-1][0] and "/2026/11?" in fetch.calls[-1][0]


def test_main_picks_the_event_and_week_from_the_scoreboard_when_not_given(tmp_path):
    clock = FakeClock(T0)
    fetch = FakeFetch(clock, {"espn-scoreboard": ok(scoreboard(state="pre")), "espn-summary": ok(summary()),
                              "sleeper-stats": ok(sleeper())})
    rl.main(["--out", str(tmp_path), "--max-minutes", "1"], fetch=fetch, now=clock.now, sleep=clock.sleep)
    urls = [u for u, _ in fetch.calls]
    assert any(u.endswith(f"event={EVENT}") for u in urls)
