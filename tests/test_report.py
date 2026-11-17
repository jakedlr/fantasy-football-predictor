from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ffp.report import PlayerResult, SkippedPlayer, StatDetail, chances, write_json, write_markdown
from ffp.simulate import ScoreDistribution

GOLDEN = Path(__file__).resolve().parent / "golden"

GENERATED_AT = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
ASSUMPTIONS = ["Return TDs and fumble-recovery TDs are not modelled yet; they count as 0 points."]

RESULTS = [
    PlayerResult(
        player="Zay Flowers",
        team="BAL",
        game="LAC @ BAL",
        kickoff="2026-11-17T01:15:00Z",
        position="WR",
        percentiles={5: 1.23, 25: 5.51, 50: 9.14, 75: 13.37, 95: 20.71},
        flags=["incomplete: missing receptions"],
        stats={"receiving_yards": StatDetail(mean=58.3, p50=55.0, books=["draftkings"], rungs=7)},
    ),
    PlayerResult(
        player="Ladd McConkey",
        team="LAC",
        game="LAC @ BAL",
        kickoff="2026-11-17T01:15:00Z",
        position="WR",
        percentiles={5: 3.04, 25: 8.86, 50: 13.12, 75: 18.49, 95: 27.93},
        flags=[],
        stats={
            "receptions": StatDetail(mean=5.2, p50=5.0, books=["draftkings", "fanduel"], rungs=9),
            "receiving_yards": StatDetail(mean=61.7, p50=57.0, books=["draftkings", "fanduel"], rungs=12),
        },
    ),
]

SKIPPED = [SkippedPlayer(name="Harrison Butker", reason="not supported yet: K")]


def test_markdown_matches_golden_file(tmp_path):
    out = tmp_path / "OUTPUT.md"
    write_markdown(RESULTS, SKIPPED, "espn-league", GENERATED_AT, out, assumptions=ASSUMPTIONS)
    assert out.read_text() == (GOLDEN / "OUTPUT.md").read_text()


def test_json_matches_golden_file(tmp_path):
    out = tmp_path / "output.json"
    write_json(RESULTS, SKIPPED, "espn-league", GENERATED_AT, out, assumptions=ASSUMPTIONS)
    assert out.read_text() == (GOLDEN / "output.json").read_text()


def test_no_skipped_players_says_none(tmp_path):
    out = tmp_path / "OUTPUT.md"
    write_markdown(RESULTS, [], "espn-league", GENERATED_AT, out)
    assert out.read_text().endswith("## Skipped\n\nNone.\n")
    assert "Assumptions" not in out.read_text()


def test_chances_are_for_the_thresholds_asked_for():
    dist = ScoreDistribution(np.array([0.0, 4.9, 5.0, 12.0, 31.0]))
    assert chances(dist, [5, 12.5]) == {5: 0.6, 12.5: 0.2}
    assert chances(dist, []) == {}


def test_without_thresholds_the_report_has_no_chance_table(tmp_path):
    # S5.8: the fixed 5/10/.../30 table is gone.
    md, js = tmp_path / "OUTPUT.md", tmp_path / "output.json"
    write_markdown(RESULTS, [], "espn-league", GENERATED_AT, md)
    write_json(RESULTS, [], "espn-league", GENERATED_AT, js)
    assert "Chance of" not in md.read_text()
    assert "chance_of_at_least" not in js.read_text()


def test_asked_for_thresholds_get_a_chance_table(tmp_path):
    from dataclasses import replace
    import json

    results = [replace(r, chances={14.5: 0.2168, 20: 0.0612}) for r in RESULTS[:1]]
    md, js = tmp_path / "OUTPUT.md", tmp_path / "output.json"
    write_markdown(results, [], "espn-league", GENERATED_AT, md)
    write_json(results, [], "espn-league", GENERATED_AT, js)
    text = md.read_text()
    assert "## Chance of at least" in text
    assert "| Player | 14.5+ | 20+ |" in text and "| Zay Flowers | 21.7% | 6.1% |" in text
    [player] = json.loads(js.read_text())["players"]
    assert player["chance_of_at_least"] == {"14.5": 0.2168, "20": 0.0612}
