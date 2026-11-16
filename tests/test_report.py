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
        chances={5: 0.7804, 10: 0.4562, 15: 0.2013, 20: 0.0612, 25: 0.0133, 30: 0.002},
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
        chances={5: 0.9311, 10: 0.6588, 15: 0.3902, 20: 0.1871, 25: 0.0794, 30: 0.0281},
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


def test_chances_from_a_score_distribution():
    dist = ScoreDistribution(np.array([0.0, 4.9, 5.0, 12.0, 31.0]))
    assert chances(dist) == {5: 0.6, 10: 0.4, 15: 0.2, 20: 0.2, 25: 0.2, 30: 0.2}
