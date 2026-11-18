"""End-to-end: `ffp predict INPUT.md --fixtures research/fixtures`, replaying DK + FD and nflverse fixtures."""

import json
import re
from pathlib import Path

import pytest

from ffp.cli import main

FIXTURES = Path(__file__).parent.parent / "research" / "fixtures"
BEFORE_MNF = "2026-11-16T12:00:00Z"  # the fixtures' Game, LAC @ BAL, kicks off 2026-11-17T01:15Z


def run_predict(tmp_path: Path, names: list[str], *options: str) -> tuple[str, dict]:
    """One CLI run on the fixtures, in one process unless `options` set --jobs: a test's monkeypatches
    don't reach worker processes (the parallel path has its own tests)."""
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("\n".join(names) + "\n")
    jobs = [] if "--jobs" in options else ["--jobs", "1"]
    exit_code = main(
        ["predict", str(input_md), "--fixtures", str(FIXTURES), "--now", BEFORE_MNF,
         "--output-dir", str(tmp_path), *jobs, *options]
    )
    assert exit_code == 0
    return (tmp_path / "OUTPUT.md").read_text(), json.loads((tmp_path / "output.json").read_text())


def test_predicts_receiving_yards_score_percentiles_for_a_wr(tmp_path):
    markdown, data = run_predict(tmp_path, ["Zay Flowers"])

    [zay] = data["players"]
    assert zay["player"] == "Zay Flowers"
    assert zay["team"] == "BAL"
    assert zay["game"] == "LAC @ BAL"
    assert zay["position"] == "WR"
    assert zay["stats"]["receiving_yards"]["books"] == ["draftkings", "fanduel"]
    p = zay["percentiles"]
    assert p["P5"] < p["P25"] < p["P50"] < p["P75"] < p["P95"]
    # Full PPR: ~5.5 receptions (1 pt each) + ~72 yds (0.1/yd) + ~0.3 TDs (6 pts) is ~14.5 points.
    assert 11 < p["P50"] < 18
    assert "Zay Flowers" in markdown


def test_a_te_with_every_market_has_no_flags_and_every_stat(tmp_path):
    markdown, data = run_predict(tmp_path, ["Mark Andrews"])
    [andrews] = data["players"]
    assert andrews["position"] == "TE"
    assert andrews["flags"] == []
    assert {"receptions", "receiving_yards", "receiving_tds", "rushing_tds"} <= set(andrews["stats"])
    assert "Rushing yards count as 0 for a player no book prices them for" in markdown  # under Assumptions


def test_the_correlation_option_is_accepted(tmp_path):
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    for kind in ("within", "pooled"):
        assert main(["predict", str(input_md), "--fixtures", str(FIXTURES), "--now", BEFORE_MNF,
                     "--correlation", kind, "--output-dir", str(tmp_path)]) == 0


def test_blank_lines_are_ignored_and_unknown_players_are_skipped(tmp_path):
    _, data = run_predict(tmp_path, ["", "Not A Player", "  ", "Zay Flowers"])
    assert [r["player"] for r in data["players"]] == ["Zay Flowers"]
    [skip] = data["skipped"]
    assert skip["player"] == "Not A Player"
    assert skip["reason"].startswith('no QB/RB/WR/TE/K named "Not A Player"')


def test_markdown_list_and_heading_markers_are_not_part_of_names(tmp_path):
    # Review finding: "- Zay Flowers" was read as the name "- Zay Flowers".
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("# Players\n- Zay Flowers\n* Tre Harris\n1. Mark Andrews\nQuentin Johnston\n")
    from ffp.predict import read_input

    assert read_input(input_md) == ["Zay Flowers", "Tre Harris", "Mark Andrews", "Quentin Johnston"]


def test_a_failed_fit_is_flagged_and_every_player_is_still_written(tmp_path, monkeypatch):
    # Review finding: one unfittable player aborted the whole run with no output.
    import ffp.predict
    from ffp.distributions import FitError

    real_fit = ffp.predict.fit_receiving_yards

    def fit_or_fail(points, position, **kwargs):
        if position == "TE":  # Mark Andrews
            raise FitError("out of reach")
        return real_fit(points, position, **kwargs)

    monkeypatch.setattr(ffp.predict, "fit_receiving_yards", fit_or_fail)
    _, data = run_predict(tmp_path, ["Mark Andrews", "Zay Flowers"])
    assert [r["player"] for r in data["players"]] == ["Mark Andrews", "Zay Flowers"]
    [andrews, zay] = data["players"]
    assert "estimated: receiving yards (could not fit its market)" in andrews["flags"]
    assert zay["flags"] == []


def test_an_error_that_is_not_a_fit_failure_stops_the_run(tmp_path, monkeypatch):
    # Review finding: every ValueError (e.g. a position with no model) became a quiet per-player skip.
    import pytest

    import ffp.predict

    # A plain ValueError out of a fit is a bug or bad configuration, not a skip.
    def broken(points, position, **kwargs):
        raise ValueError("no receiving-yards model for position 'XX'")

    monkeypatch.setattr(ffp.predict, "fit_receiving_yards", broken)
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    with pytest.raises(ValueError, match="no receiving-yards model for position 'XX'"):
        main(["predict", str(input_md), "--fixtures", str(FIXTURES), "--now", BEFORE_MNF,
              "--output-dir", str(tmp_path)])
    assert not (tmp_path / "OUTPUT.md").exists()


def test_other_common_markdown_is_not_part_of_names(tmp_path):
    # Review finding: checkboxes, bold, rules, front matter and a lone "-" became player names.
    from ffp.predict import read_input

    input_md = tmp_path / "INPUT.md"
    input_md.write_text(
        "---\ntitle: Week 11\n---\n"
        "- [ ] Zay Flowers\n- [x] Tre Harris\n**Mark Andrews**\n* _Quentin Johnston_\n"
        "---\n***\n-\n1.\n- `Derrick Henry`\n"
    )
    assert read_input(input_md) == [
        "Zay Flowers", "Tre Harris", "Mark Andrews", "Quentin Johnston", "Derrick Henry"
    ]


def test_a_fixture_run_saves_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_predict(tmp_path, ["Zay Flowers"])
    assert not (tmp_path / "snapshots").exists()


def test_a_live_run_snapshots_responses_and_fair_points(tmp_path, monkeypatch):
    # S2.6 wired in: the live path with the network replaced by the fixtures.
    import ffp.cli
    import ffp.predict

    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    monkeypatch.setattr(ffp.cli, "load_td_history", lambda season, **kw: {})
    monkeypatch.setattr(ffp.cli, "load_recent_games", lambda season, **kw: {})
    monkeypatch.setattr(ffp.cli, "_played", lambda season: set())
    seen_games = []

    seen_stats = []

    def fake_live_books(http, games, now, on_response=None, stats=None, kickers=False, dst_games=(),
                        kicker_ladders=False):
        seen_games.extend(games)
        seen_stats.extend(stats)
        on_response("fanduel", "fd_league", "https://example.test/page?_ak=SECRET&x=1", {"ok": True})
        return ffp.predict.load_fixture_books(FIXTURES)

    monkeypatch.setattr(ffp.predict, "load_live_books", fake_live_books)
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\nDerrick Henry\n")
    snapshots = tmp_path / "snaps"
    from datetime import datetime, timezone

    run_started = datetime.now(timezone.utc)
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path),
                 "--snapshots", str(snapshots)]) == 0

    assert [g.game_id for g in seen_games] == ["2026_10_LAC_BAL"]  # both targets' Game, asked for once
    assert "passing_yards" not in seen_stats and "rushing_yards" in seen_stats  # no QB asked for: no passing
    # Finding 1: filed under the real fetch time, not --now, so the price history stays true.
    [run_dir] = (snapshots / run_started.strftime("%Y-%m-%d")).iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["run_at"].startswith(run_started.strftime("%Y-%m-%dT%H"))
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert [r["name"] for r in manifest["responses"]] == ["fd_league"]
    assert "SECRET" not in (run_dir / "manifest.json").read_text()
    fair = json.loads((run_dir / "fair_points.json").read_text())["fair_points"]
    zay = sorted((f["player_id"], f["stat"], f["game_id"]) for f in fair if f["player"] == "Zay Flowers")
    assert zay == [
        ("00-0039064", stat, "2026_10_LAC_BAL")
        for stat in ("receiving_yards", "receptions", "rush_rec_tds", "rushing_yards")
    ]
    assert {f["stat"] for f in fair if f["player"] == "Derrick Henry"} >= {"rushing_yards", "carries"}


def test_a_live_run_with_nobody_to_price_makes_no_requests(tmp_path, monkeypatch):
    import ffp.cli
    import ffp.predict

    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    monkeypatch.setattr(ffp.predict, "load_live_books", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setattr(ffp.cli, "load_td_history", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setattr(ffp.cli, "load_recent_games", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    monkeypatch.setattr(ffp.cli, "_played", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Not A Player\n")
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path),
                 "--snapshots", str(tmp_path / "snaps")]) == 0
    assert not (tmp_path / "snaps").exists()


def test_a_live_run_whose_odds_fail_throws_the_player_history_away(tmp_path, monkeypatch):
    # Sprint 8 (S8.2): the history download overlaps the odds, so it may start; with no odds it is
    # thrown away (even a failing one) and the run stops as before.
    import ffp.cli
    import ffp.predict

    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    monkeypatch.setattr(ffp.predict, "load_live_books", lambda *a, **k: [])
    monkeypatch.setattr(ffp.cli, "load_td_history", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(ffp.cli, "load_recent_games", lambda *a, **k: {})
    monkeypatch.setattr(ffp.cli, "_played", lambda *a, **k: set())
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path), "--no-snapshot"]) == 1


def test_the_player_history_downloads_while_the_odds_are_fetched(tmp_path, monkeypatch):
    # S8.2: the odds can't finish until the history download has started.
    import threading

    import ffp.cli
    import ffp.predict

    started = threading.Event()
    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    monkeypatch.setattr(ffp.cli, "load_td_history", lambda *a, **k: (started.set(), {})[1])
    monkeypatch.setattr(ffp.cli, "load_recent_games", lambda *a, **k: {})
    monkeypatch.setattr(ffp.cli, "_played", lambda *a, **k: set())

    def odds(http, games, now, **kw):
        assert started.wait(5), "the history download did not start alongside the odds"
        return ffp.predict.load_fixture_books(FIXTURES)

    monkeypatch.setattr(ffp.predict, "load_live_books", odds)
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path), "--no-snapshot"]) == 0


def test_the_chance_option_reports_the_thresholds_supplied(tmp_path):
    # S5.8: the user supplies the numbers; without the option there is no chance table.
    markdown, data = run_predict(tmp_path, ["Zay Flowers"])
    assert "Chance of" not in markdown and "chance_of_at_least" not in data["players"][0]
    markdown, data = run_predict(tmp_path, ["Zay Flowers"], "--chance", "14.5", "--chance", "20")
    [zay] = data["players"]
    assert list(zay["chance_of_at_least"]) == ["14.5", "20"]
    assert 0 < zay["chance_of_at_least"]["20"] < zay["chance_of_at_least"]["14.5"] < 1
    assert "| Player | 14.5+ | 20+ |" in markdown


@pytest.mark.parametrize("bad", ["nan", "inf", "-3", "abc"])
def test_a_chance_threshold_must_be_a_finite_non_negative_number(tmp_path, bad, capsys):
    with pytest.raises(SystemExit):
        run_predict(tmp_path, ["Zay Flowers"], "--chance", bad)
    assert "--chance" in capsys.readouterr().err


def test_a_kicker_and_dst_run_fetches_no_player_history(tmp_path, monkeypatch):
    # Review of PRs 9-11: K and D/ST use no nflverse weekly stats, so a run of only those doesn't
    # download them; the kicker Ladders come only when a snapshot will keep them.
    import ffp.cli
    import ffp.predict

    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    calls = []

    def fake_live_books(http, games, now, on_response=None, stats=None, kickers=False, dst_games=(),
                        kicker_ladders=False):
        calls.append((kickers, [g.game_id for g in dst_games], kicker_ladders))
        return ffp.predict.load_fixture_books(FIXTURES)

    monkeypatch.setattr(ffp.predict, "load_live_books", fake_live_books)
    for loader in ("load_td_history", "load_recent_games", "_played"):
        monkeypatch.setattr(ffp.cli, loader, lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Cameron Dicker\nRavens D/ST\n")
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path), "--no-snapshot"]) == 0
    assert calls == [(True, ["2026_10_LAC_BAL"], False)]


def _live_cli(tmp_path, monkeypatch, fake_live_books):
    import ffp.cli
    import ffp.predict

    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    for loader in ("load_td_history", "load_recent_games"):
        monkeypatch.setattr(ffp.cli, loader, lambda season, **kw: {})
    monkeypatch.setattr(ffp.cli, "_played", lambda season: set())
    monkeypatch.setattr(ffp.predict, "load_live_books", fake_live_books)
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    return ["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path),
            "--snapshots", str(tmp_path / "snaps")]


def test_a_live_run_prints_and_snapshots_its_timing(tmp_path, monkeypatch, capsys):
    import ffp.predict

    def fake_live_books(http, games, now, **kw):
        with http.for_book("draftkings"):
            pass
        with http.for_book("fanduel"):
            pass
        return ffp.predict.load_fixture_books(FIXTURES)

    assert main(_live_cli(tmp_path, monkeypatch, fake_live_books)) == 0
    out = capsys.readouterr().out
    assert re.search(r"Odds in [\d.]+ s: DraftKings 0 live requests in [\d.]+ s, FanDuel 0 in [\d.]+ s", out)
    [run_dir] = (tmp_path / "snaps").glob("*/*")
    timing = json.loads((run_dir / "manifest.json").read_text())["timing"]
    assert list(timing["books"]) == ["draftkings", "fanduel"]


def test_ctrl_c_during_a_live_run_ends_it_quietly(tmp_path, monkeypatch, capsys):
    def fake_live_books(http, games, now, **kw):
        raise KeyboardInterrupt

    assert main(_live_cli(tmp_path, monkeypatch, fake_live_books)) == 130
    err = capsys.readouterr().err
    assert "cancelled" in err and "Traceback" not in err
    assert not (tmp_path / "OUTPUT.md").exists()


def test_the_timing_line_names_a_book_left_out_or_rate_limited():
    from ffp.cli import timing_line

    timing = {
        "draftkings": {"requests": 4, "cached": 0, "failed": True, "rate_limited": True, "seconds": 3.0},
        "fanduel": {"requests": 30, "cached": 2, "failed": False, "rate_limited": False, "seconds": 11.94},
    }
    assert timing_line(timing, 12.04) == ("Odds in 12.0 s: DraftKings 4 live requests in 3.0 s (rate-limited, "
                                          "left out), FanDuel 30 in 11.9 s (+2 cached)")


@pytest.mark.parametrize("extra, jobs", [([], "auto"), (["--jobs", "1"], 1), (["--jobs", "3"], 3)])
def test_jobs_sets_the_prediction_worker_processes(tmp_path, monkeypatch, extra, jobs):
    # Sprint 8: by default one worker per player, a fifth of the cores left free; --jobs 1 stays in one process.
    import ffp.predict

    seen = {}
    real = ffp.predict.predict

    def spy(targets, books, rules, **kw):
        seen["jobs"], seen["n"] = kw.get("jobs"), len(targets)
        return real(targets, books, rules, **{**kw, "jobs": 1})

    monkeypatch.setattr(ffp.predict, "predict", spy)
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\nDerrick Henry\n")
    assert main(["predict", str(input_md), "--fixtures", str(FIXTURES), "--now", BEFORE_MNF,
                 "--output-dir", str(tmp_path), *extra]) == 0
    # Auto is None: predict sizes it by the players with markets to fit (ffp.predict.default_jobs).
    assert seen["jobs"] == (None if jobs == "auto" else jobs)


def test_jobs_must_be_a_whole_number_of_one_or_more(tmp_path, capsys):
    with pytest.raises(SystemExit):
        main(["predict", "x.md", "--jobs", "0"])
    assert "--jobs" in capsys.readouterr().err


def test_a_failed_odds_run_lets_the_history_download_finish_before_exiting(tmp_path, monkeypatch):
    # Review finding 3: the process exited with the download (and its cache write) half done.
    import time as _time

    import ffp.cli
    import ffp.predict

    done = []
    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    monkeypatch.setattr(ffp.predict, "load_live_books", lambda *a, **k: [])
    monkeypatch.setattr(ffp.cli, "_player_history", lambda season: (_time.sleep(0.3), done.append(1), ({}, {}))[2])
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path), "--no-snapshot"]) == 1
    assert done == [1]
