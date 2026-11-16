"""End-to-end: `ffp predict INPUT.md --fixtures research/fixtures` (Sprint 1 walking skeleton)."""

import json
from pathlib import Path

from ffp.cli import main

FIXTURES = Path(__file__).parent.parent / "research" / "fixtures"


def run_predict(tmp_path: Path, names: list[str]) -> tuple[str, dict]:
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("\n".join(names) + "\n")
    exit_code = main(
        ["predict", str(input_md), "--fixtures", str(FIXTURES), "--output-dir", str(tmp_path)]
    )
    assert exit_code == 0
    return (tmp_path / "OUTPUT.md").read_text(), json.loads((tmp_path / "output.json").read_text())


def test_predicts_receiving_yards_score_percentiles_for_a_wr(tmp_path):
    markdown, data = run_predict(tmp_path, ["Zay Flowers"])

    [zay] = data["players"]
    assert zay["player"] == "Zay Flowers"
    assert zay["team"] == "BAL Ravens"
    assert zay["game"] == "LA Chargers @ BAL Ravens"
    p = zay["percentiles"]
    assert p["P5"] < p["P25"] < p["P50"] < p["P75"] < p["P95"]
    # Main Line 71.5 yds is near the median, so P50 is about 7 points under 0.1/yd.
    assert 5.5 < p["P50"] < 8.5
    assert "Zay Flowers" in markdown


def test_sprint_1_flags_its_simplifications(tmp_path):
    _, data = run_predict(tmp_path, ["Zay Flowers"])
    flags = data["players"][0]["flags"]
    assert any("position assumed WR" in f for f in flags)
    assert any("receiving yards only" in f for f in flags)


def test_blank_lines_are_ignored_and_unknown_players_are_skipped(tmp_path):
    _, data = run_predict(tmp_path, ["", "Not A Player", "  ", "Zay Flowers"])
    assert [r["player"] for r in data["players"]] == ["Zay Flowers"]
    assert data["skipped"] == [
        {"player": "Not A Player", "reason": "no Prop Markets found for this player"}
    ]
