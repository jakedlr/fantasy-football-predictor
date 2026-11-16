import json
from datetime import datetime, timedelta, timezone

import pytest

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


def test_manifest_lists_every_saved_response(tmp_path):
    snap = Snapshot(RUN_AT, root=tmp_path)
    fetched = datetime(2026, 11, 15, 17, 4, 9, tzinfo=timezone.utc)
    a = snap.save_response("draftkings", "events", {"a": 1}, url="https://dk.example/events")
    b = snap.save_response("fanduel", "event-123", '{"b": 2}', fetched_at=fetched)
    c = snap.save_response("fanduel", "event-123", b"\x00raw", url="https://fd.example/x")

    manifest = _load(snap.run_dir / "manifest.json")
    assert manifest["run_id"] == "170405Z"
    assert manifest["run_at"] == "2026-11-15T17:04:05+00:00"
    assert manifest["responses"] == [
        {
            "file": a.name,
            "book": "draftkings",
            "name": "events",
            "url": "https://dk.example/events",
            "fetched_at": "2026-11-15T17:04:05+00:00",
            "format": "json",
        },
        {
            "file": b.name,
            "book": "fanduel",
            "name": "event-123",
            "url": None,
            "fetched_at": "2026-11-15T17:04:09+00:00",
            "format": "text",
        },
        {
            "file": c.name,
            "book": "fanduel",
            "name": "event-123",
            "url": "https://fd.example/x",
            "fetched_at": "2026-11-15T17:04:05+00:00",
            "format": "bytes",
        },
    ]
    assert len({a, b, c}) == 3  # the same name twice must not overwrite


def test_str_and_bytes_payloads_are_written_exactly_as_received(tmp_path):
    snap = Snapshot(RUN_AT, root=tmp_path)
    text = '{"b":2,  "unparsed": true}\n'
    blob = b"\x00\xffnot json"
    assert snap.save_response("fanduel", "t", text).read_text() == text
    assert snap.save_response("fanduel", "b", blob).read_bytes() == blob


def test_run_time_must_be_timezone_aware(tmp_path):
    with pytest.raises(ValueError):
        Snapshot(datetime(2026, 11, 15, 17, 4, 5), root=tmp_path)


def test_run_time_is_filed_in_utc(tmp_path):
    eastern = timezone(timedelta(hours=-5))
    snap = Snapshot(datetime(2026, 11, 15, 21, 30, 0, tzinfo=eastern), root=tmp_path)
    assert snap.run_dir == tmp_path / "2026-11-16" / "023000Z"
