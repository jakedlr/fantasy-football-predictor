import json
from datetime import datetime, timezone

from ffp.fair import FairPoint
from ffp.snapshots import Snapshot

RUN_AT = datetime(2026, 11, 15, 17, 4, 5, tzinfo=timezone.utc)


def _load(path):
    return json.loads(path.read_text())


def test_run_writes_raw_responses_and_fair_probabilities_under_date(tmp_path):
    snap = Snapshot(RUN_AT, root=tmp_path)
    payload = {"events": [{"id": "123", "name": "Bills @ Chiefs"}]}
    raw = snap.save_response("draftkings", "nfl-events", payload)
    fp = snap.save_fair_points(
        "Ja'Marr Chase",
        "receiving_yards",
        [FairPoint(70, 0.5, 3.0), FairPoint(100, 0.22, 1.0)],
        game_id="123",
    )

    run_dir = tmp_path / "2026-11-15" / "170405Z"
    assert snap.run_dir == run_dir
    assert raw.parent == run_dir and fp.parent == run_dir
    assert _load(raw) == payload
    saved = _load(fp)["fair_points"]
    assert saved == [
        {
            "player": "Ja'Marr Chase",
            "stat": "receiving_yards",
            "game_id": "123",
            "points": [
                {"threshold": 70, "prob": 0.5, "weight": 3.0},
                {"threshold": 100, "prob": 0.22, "weight": 1.0},
            ],
        }
    ]
