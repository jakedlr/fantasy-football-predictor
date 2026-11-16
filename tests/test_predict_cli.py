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


def test_markdown_list_and_heading_markers_are_not_part_of_names(tmp_path):
    # Review finding: "- Zay Flowers" was read as the name "- Zay Flowers".
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("# Players\n- Zay Flowers\n* Tre Harris\n1. Mark Andrews\nQuentin Johnston\n")
    from ffp.predict import read_input

    assert read_input(input_md) == ["Zay Flowers", "Tre Harris", "Mark Andrews", "Quentin Johnston"]


def test_a_player_whose_fit_fails_is_skipped_and_the_rest_are_still_written(tmp_path, monkeypatch):
    # Review finding: one unfittable player aborted the whole run with no output.
    import ffp.predict
    from ffp.distributions import FitError

    real_fit = ffp.predict.fit_receiving_yards

    def fit_or_fail(points, position):
        if points[0].threshold == 6:  # Derrick Henry's Main Line 5.5 -> Y >= 6 is his lowest point
            raise FitError("out of reach")
        return real_fit(points, position)

    monkeypatch.setattr(ffp.predict, "fit_receiving_yards", fit_or_fail)
    _, data = run_predict(tmp_path, ["Derrick Henry", "Zay Flowers"])
    assert [r["player"] for r in data["players"]] == ["Zay Flowers"]
    assert data["skipped"] == [
        {"player": "Derrick Henry", "reason": "could not fit receiving yards: out of reach"}
    ]


def test_an_error_that_is_not_a_fit_failure_stops_the_run(tmp_path, monkeypatch):
    # Review finding: every ValueError (e.g. a position with no model) became a quiet per-player skip.
    import pytest

    import ffp.predict

    monkeypatch.setattr(ffp.predict, "SPRINT_1_POSITION", "RB")
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("Zay Flowers\n")
    with pytest.raises(ValueError, match="no receiving-yards model for position 'RB'"):
        main(["predict", str(input_md), "--fixtures", str(FIXTURES), "--output-dir", str(tmp_path)])
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
