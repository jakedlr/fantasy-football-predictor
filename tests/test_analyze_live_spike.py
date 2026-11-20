"""scripts/analyze_live_spike.py (S9.1 Live test) on small synthetic manifests."""

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("analyze_live_spike", ROOT / "scripts" / "analyze_live_spike.py")
al = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(al)

T0 = datetime(2026, 11, 20, 1, 15, tzinfo=timezone.utc)


def rec(minute, source, vantage="home", status=200, seconds=0, **kw):
    t = T0 + timedelta(minutes=minute, seconds=seconds)
    row = {"ts": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "vantage": vantage, "source": source, "url": "u",
           "status": status, "latency_ms": 100, "bytes": 10, "state": None, "period": None, "clock": None,
           "plays": None, "stats_hash": None, "error": None, "skipped": None}
    row.update(kw)
    return row


def sb(minute, period, clock, state="in", **kw):
    return rec(minute, "espn-scoreboard", state=state, period=period, clock=clock, **kw)


def write(tmp_path, name, rows):
    path = tmp_path / name
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def minutes(m, s=0):
    return T0 + timedelta(minutes=m, seconds=s)


# ------------------------------------------------------------ loading and status codes


def test_load_reads_several_manifests_and_parses_times(tmp_path):
    a = write(tmp_path, "manifest-home.jsonl", [rec(0, "espn-summary")])
    b = write(tmp_path, "manifest-worker.jsonl", [rec(1, "espn-summary", vantage="worker")])
    rows = al.load([a, b])
    assert [r["vantage"] for r in rows] == ["home", "worker"]
    assert rows[1]["t"] == minutes(1)


def test_status_counts_per_source_and_vantage_including_errors_and_skips():
    rows = [rec(0, "sleeper-stats", vantage="worker", status=403),
            rec(1, "sleeper-stats", vantage="worker", status=None, skipped="backoff"),
            rec(2, "sleeper-stats", vantage="worker", status=429),
            rec(3, "sleeper-stats", vantage="worker", status=None, error="TimeoutError"),
            rec(0, "sleeper-stats", vantage="home", status=200)]
    counts = al.status_counts(al.with_times(rows))
    assert counts[("sleeper-stats", "worker")] == {"403": 1, "429": 1, "skipped": 1, "error": 1}
    assert counts[("sleeper-stats", "home")] == {"200": 1}


# ------------------------------------------------------------ game progress and staleness


def test_progress_is_the_first_sighting_of_each_new_clock_or_play_count_from_any_vantage():
    rows = al.with_times([
        sb(0, 1, "15:00"),                       # baseline, not progress
        sb(1, 1, "15:00"),
        sb(2, 1, "14:20", vantage="worker"),     # worker sees it first
        sb(2, 1, "14:20", seconds=5),
        rec(3, "espn-summary", plays=0, state="in"),  # baseline plays
        rec(4, "espn-summary", plays=2, state="in"),
        sb(5, 0, "0:00", state="pre"),           # pre-game rows never count
    ])
    assert al.progress_times(rows) == [minutes(2), minutes(4)]


def test_hash_changes_are_per_stream_and_ignore_failed_requests():
    rows = al.with_times([
        rec(0, "sleeper-stats", stats_hash="a"),
        rec(1, "sleeper-stats", stats_hash="a"),
        rec(2, "sleeper-stats", status=429),
        rec(3, "sleeper-stats", stats_hash="b"),
        rec(3, "sleeper-stats", vantage="worker", stats_hash="z"),
        rec(4, "sleeper-stats", stats_hash="b"),
        rec(5, "sleeper-stats", stats_hash="c"),
    ])
    assert al.hash_changes(rows, "sleeper-stats", "home") == [minutes(3), minutes(5)]
    assert al.hash_changes(rows, "sleeper-stats", "worker") == []


def test_lag_is_minutes_from_each_progress_to_the_next_hash_change():
    progress = [minutes(2), minutes(3), minutes(10)]
    changes = [minutes(2, 10), minutes(7)]
    lags, unresolved = al.lags(progress, changes)
    assert lags == pytest.approx([10 / 60, 4.0])
    assert unresolved == 1


def test_a_change_seen_just_before_the_progress_in_the_same_poll_counts_as_no_lag():
    lags, _ = al.lags([minutes(2, 5)], [minutes(2, 1)], tolerance_s=30)
    assert lags == [0.0]


def test_final_and_settle_time():
    rows = al.with_times([sb(0, 4, "0:10"), sb(1, 4, "0:00", state="post"), sb(2, 4, "0:00", state="post")])
    final = al.final_time(rows)
    assert final == minutes(1)
    assert al.settle_minutes([minutes(0), minutes(9)], final) == 8.0
    assert al.settle_minutes([minutes(0)], final) == 0.0
    assert al.settle_minutes([], None) is None


def test_report_summarises_each_source_and_vantage(tmp_path):
    rows = [
        sb(0, 1, "15:00", stats_hash="s0"), sb(1, 1, "13:00", stats_hash="s1"), sb(2, 1, "11:00", stats_hash="s2"),
        sb(3, 1, "9:00", state="post", stats_hash="s2"),
        rec(0, "espn-summary", state="in", plays=0, stats_hash="b0"),
        rec(1, "espn-summary", state="in", plays=3, stats_hash="b1"),
        rec(2, "espn-summary", state="in", plays=6, stats_hash="b2"),
        rec(3, "espn-summary", state="post", plays=9, stats_hash="b3"),
        rec(0, "sleeper-stats", stats_hash="x0"),
        rec(1, "sleeper-stats", stats_hash="x0"),
        rec(2, "sleeper-stats", stats_hash="x0"),
        rec(3, "sleeper-stats", stats_hash="x1"),
        rec(0, "espn-summary", vantage="worker", status=403),
        rec(1, "espn-summary", vantage="worker", status=None, skipped="backoff"),
    ]
    path = write(tmp_path, "manifest-home.jsonl", rows)
    text = al.report(al.load([path]))
    assert "espn-summary" in text and "worker" in text and "403: 1" in text
    summary = al.summarise(al.load([path]))
    sleeper = summary[("sleeper-stats", "home")]
    # progress at 1, 2, 3 min; sleeper changes only at 3 -> lags 2, 1, 0
    assert sleeper["lag_median"] == pytest.approx(1.0)
    assert sleeper["lag_max"] == pytest.approx(2.0)
    assert summary[("espn-summary", "home")]["lag_max"] == pytest.approx(0.0)
    assert summary[("espn-summary", "worker")]["statuses"] == {"403": 1, "skipped": 1}


def test_main_prints_the_report_for_the_given_manifests(tmp_path, capsys):
    path = write(tmp_path, "manifest-home.jsonl", [sb(0, 1, "15:00", stats_hash="a")])
    assert al.main([str(path)]) == 0
    assert "espn-scoreboard" in capsys.readouterr().out


def test_main_with_a_directory_reads_every_manifest_in_it(tmp_path, capsys):
    write(tmp_path, "manifest-home.jsonl", [sb(0, 1, "15:00")])
    write(tmp_path, "manifest-worker.jsonl", [sb(0, 1, "15:00", vantage="worker")])
    al.main([str(tmp_path)])
    out = capsys.readouterr().out
    assert "home" in out and "worker" in out
