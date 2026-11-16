"""End-to-end: `ffp predict INPUT.md --fixtures research/fixtures`, replaying DK + FD and nflverse fixtures."""

import json
from pathlib import Path

from ffp.cli import main

FIXTURES = Path(__file__).parent.parent / "research" / "fixtures"
BEFORE_MNF = "2026-11-16T12:00:00Z"  # the fixtures' Game, LAC @ BAL, kicks off 2026-11-17T01:15Z


def run_predict(tmp_path: Path, names: list[str]) -> tuple[str, dict]:
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("\n".join(names) + "\n")
    exit_code = main(
        ["predict", str(input_md), "--fixtures", str(FIXTURES), "--now", BEFORE_MNF,
         "--output-dir", str(tmp_path)]
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
    assert "rushing yards are not modelled yet" in markdown  # listed once, under Assumptions


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
    assert skip["reason"].startswith('no QB/RB/WR/TE named "Not A Player"')


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
    assert "incomplete: could not fit receiving yards (out of reach)" in andrews["flags"]
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
    seen_games = []

    def fake_live_books(http, games, now, on_response=None):
        seen_games.extend(games)
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

    assert [g.game_id for g in seen_games] == ["2026_10_LAC_BAL"]  # only the RB-free target's Game
    # Finding 1: filed under the real fetch time, not --now, so the price history stays true.
    [run_dir] = (snapshots / run_started.strftime("%Y-%m-%d")).iterdir()
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["run_at"].startswith(run_started.strftime("%Y-%m-%dT%H"))
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert [r["name"] for r in manifest["responses"]] == ["fd_league"]
    assert "SECRET" not in (run_dir / "manifest.json").read_text()
    fair = json.loads((run_dir / "fair_points.json").read_text())["fair_points"]
    assert sorted((f["player"], f["player_id"], f["stat"], f["game_id"]) for f in fair) == [
        ("Zay Flowers", "00-0039064", stat, "2026_10_LAC_BAL")
        for stat in ("receiving_yards", "receptions", "rush_rec_tds")
    ]


def test_a_live_run_with_nobody_to_price_makes_no_requests(tmp_path, monkeypatch):
    import ffp.cli
    import ffp.predict

    roster, schedule = ffp.predict.load_fixture_nflverse(FIXTURES, 2026)
    monkeypatch.setattr(ffp.cli, "load_roster", lambda season: roster)
    monkeypatch.setattr(ffp.cli, "load_schedule", lambda season: schedule)
    monkeypatch.setattr(ffp.predict, "load_live_books", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fetched")))
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Derrick Henry\n")
    assert main(["predict", str(input_md), "--now", BEFORE_MNF, "--output-dir", str(tmp_path),
                 "--snapshots", str(tmp_path / "snaps")]) == 0
    assert not (tmp_path / "snaps").exists()
