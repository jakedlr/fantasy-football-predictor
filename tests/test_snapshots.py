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


def test_fair_points_are_keyed_by_player_and_stat(tmp_path):
    snap = Snapshot(RUN_AT, root=tmp_path)
    snap.save_fair_points("A", "receptions", [FairPoint(5, 0.4, 3.0)])
    snap.save_fair_points("A", "receiving_yards", [FairPoint(60, 0.5, 3.0)])
    path = snap.save_fair_points("A", "receptions", [FairPoint(5, 0.45, 3.0)], game_id="9")

    saved = _load(path)["fair_points"]
    assert [(e["player"], e["stat"]) for e in saved] == [
        ("A", "receptions"),
        ("A", "receiving_yards"),
    ]
    assert saved[0]["points"] == [{"threshold": 5, "prob": 0.45, "weight": 3.0}]
    assert saved[0]["game_id"] == "9"


def test_snapshots_dir_is_gitignored():
    from pathlib import Path

    gitignore = Path(__file__).resolve().parents[1] / ".gitignore"
    assert "snapshots/" in gitignore.read_text().splitlines()


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


def test_runs_starting_in_the_same_second_do_not_collide(tmp_path):
    first = Snapshot(RUN_AT, root=tmp_path)
    second = Snapshot(RUN_AT, root=tmp_path)
    third = Snapshot(RUN_AT, root=tmp_path)
    assert first.run_dir.name == "170405Z"
    assert second.run_dir.name == "170405Z-2"
    assert third.run_dir.name == "170405Z-3"
    first.save_response("draftkings", "events", {"run": 1})
    second.save_response("draftkings", "events", {"run": 2})
    assert _load(second.run_dir / "manifest.json")["run_id"] == "170405Z-2"
    assert len(_load(first.run_dir / "manifest.json")["responses"]) == 1


@pytest.mark.parametrize(
    "book, name",
    [
        ("fanduel", "../../escape"),
        ("fanduel", "/etc/passwd"),
        ("fanduel", "a/b\\c"),
        ("../..", "x"),
        ("fanduel", ".."),
        ("fanduel", ""),
    ],
)
def test_names_cannot_escape_the_run_directory(tmp_path, book, name):
    snap = Snapshot(RUN_AT, root=tmp_path)
    path = snap.save_response(book, name, {"ok": True})
    assert path.parent == snap.run_dir
    assert path.resolve().parent == snap.run_dir.resolve()
    assert _load(path) == {"ok": True}
    entry = _load(snap.run_dir / "manifest.json")["responses"][0]
    assert entry["file"] == path.name
    assert entry["name"] == name  # the original name is kept in the manifest


def test_secret_query_params_are_stripped_from_stored_urls(tmp_path):
    snap = Snapshot(RUN_AT, root=tmp_path)
    url = (
        "https://sbapi.example.com/api/event-page"
        "?_ak=FhMFpcPWXMeyZxOx&eventId=123&AK=x&apikey=y&api_key=z&key=k&Token=t&tab=all"
    )
    snap.save_response("fanduel", "event-123", {"ok": True}, url=url)

    stored = _load(snap.run_dir / "manifest.json")["responses"][0]["url"]
    assert stored == "https://sbapi.example.com/api/event-page?eventId=123&tab=all"
    for path in snap.run_dir.iterdir():
        assert "FhMFpcPWXMeyZxOx" not in path.read_text()


def test_a_crash_mid_write_leaves_earlier_files_valid(tmp_path, monkeypatch):
    import ffp.snapshots

    snap = Snapshot(RUN_AT, root=tmp_path)
    first = snap.save_response("draftkings", "events", {"n": 1})
    snap.save_fair_points("A", "receptions", [FairPoint(5, 0.4, 3.0)])
    before = {p.name: p.read_bytes() for p in snap.run_dir.iterdir()}

    def crash(src, dst):
        raise OSError("disk died mid-run")

    monkeypatch.setattr(ffp.snapshots.os, "replace", crash)
    with pytest.raises(OSError):
        snap.save_response("draftkings", "events", {"n": 2})
    with pytest.raises(OSError):
        snap.save_fair_points("B", "receptions", [FairPoint(3, 0.6, 3.0)])

    after = {p.name: p.read_bytes() for p in snap.run_dir.iterdir()}
    assert after == before  # nothing half-written, no temp files left behind
    assert _load(snap.run_dir / "manifest.json")["responses"][0]["file"] == first.name
    assert [e["player"] for e in _load(snap.run_dir / "fair_points.json")["fair_points"]] == ["A"]
