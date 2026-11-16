"""S2.7 live smoke test: `ffp predict` against the real sportsbooks and nflverse.

Not in the default run (marked live); run with `pytest -m live tests/test_live_smoke.py`.
It makes a few dozen paced requests at most: DraftKings' league and two
subcategories, FanDuel's league page, and one FanDuel event tab per distinct Game.
Outputs and the snapshot go to a temp directory, never the repo.
"""

import json

import pytest

from ffp.cli import main

# Established WR/TEs spread over many teams, so at least one has a Game with props posted.
PLAYERS = [
    "Ja'Marr Chase", "Zay Flowers", "Puka Nacua", "Amon-Ra St. Brown", "Justin Jefferson",
    "CeeDee Lamb", "Malik Nabers", "Brock Bowers", "Trey McBride", "Nico Collins",
    "Drake London", "Garrett Wilson", "Jaxon Smith-Njigba", "Ladd McConkey",
]


@pytest.mark.live
def test_at_least_one_real_player_gets_percentiles(tmp_path):
    input_md = tmp_path / "INPUT.md"
    input_md.write_text("\n".join(PLAYERS) + "\n")
    snapshots = tmp_path / "snapshots"

    assert main(["predict", str(input_md), "--output-dir", str(tmp_path), "--snapshots", str(snapshots)]) == 0

    data = json.loads((tmp_path / "output.json").read_text())
    assert data["players"], f"no player priced; skipped: {data['skipped']}"
    for player in data["players"]:
        p = player["percentiles"]
        assert p["P5"] <= p["P25"] <= p["P50"] <= p["P75"] <= p["P95"]
    [run_dir] = [d for day in snapshots.iterdir() for d in day.iterdir()]
    assert json.loads((run_dir / "manifest.json").read_text())["responses"]
    assert json.loads((run_dir / "fair_points.json").read_text())["fair_points"]
