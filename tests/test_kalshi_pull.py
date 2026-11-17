"""Kalshi source (S5.1): the network side, against research/fixtures/kalshi/ (recorded 2026-11-16 from
the real API: NYG @ NE 2025-12-01 on the historical endpoints, PIT @ CIN 2026-11-15 on the live ones, a
few players per series, trimmed to the stored fields)."""

import csv
import json
import urllib.parse
from collections import Counter
from pathlib import Path

import pytest

from ffp.sources import fanduel, kalshi
from ffp.sources.http import FetchError
from ffp.sources.kalshi import (
    Cache,
    KalshiHttp,
    NotFound,
    drop_counts,
    load_fantasy_lines,
    load_fantasy_means,
    load_rungs,
    pull,
)

FIXTURES = Path(__file__).parents[1] / "research" / "fixtures" / "kalshi"
RESPONSES = json.loads((FIXTURES / "responses.json").read_text())


def _csv(name, season):
    with open(FIXTURES / name, newline="") as f:
        return [r for r in csv.DictReader(f) if int(r["season"]) == season]


def schedule(season):
    return _csv("schedule.csv", season)


def rosters(season):
    return _csv("rosters.csv", season)


def _without(url, param):
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k != param]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


class FixtureHttp:
    """Answers from the recorded responses; fails the test on any other URL. A live listing's
    min_close_ts (set by incremental re-listing) is ignored."""

    def __init__(self, responses=RESPONSES):
        self.responses = {_without(u, "min_close_ts"): r for u, r in responses.items()}
        self.calls = []

    @property
    def requests(self):
        return len(self.calls)

    def get_json(self, url):
        self.calls.append(url)
        key = _without(url, "min_close_ts")
        if key not in self.responses:
            pytest.fail(f"no recorded response for {url}")
        answer = self.responses[key]
        if answer.get("_status") == 404:
            raise NotFound(url)
        return answer


@pytest.fixture
def pulled(tmp_path):
    http = FixtureHttp()
    report = pull(tmp_path, [2025, 2026], http=http, schedule_fetch=schedule, roster_fetch=rosters)
    return tmp_path, http, report


def test_pull_turns_the_recorded_week_into_rungs_of_both_seasons(pulled):
    root, _, report = pulled
    rungs = load_rungs(root)
    assert report.rungs == len(rungs) > 100
    assert {(r.season, r.game_id) for r in rungs} == {(2025, "2025_13_NYG_NE"), (2026, "2026_10_PIT_CIN")}
    chase = [r for r in rungs if r.player == "Ja'Marr Chase" and r.stat == "receiving_yards"]
    assert chase and {r.gsis_id for r in chase} == {"00-0036900"}
    assert chase[0].kickoff == "2026-11-16T01:20:00Z"  # SNF Nov 15, 8:20 pm ET
    assert [r.threshold for r in chase] == sorted(r.threshold for r in chase)
    assert all(0 < r.bid < r.ask < 1 and r.ask - r.bid <= kalshi.MAX_SPREAD + 1e-9 for r in rungs)


def test_td_rungs_come_from_anytd_and_2td_in_2025_and_the_td_ladder_in_2026(pulled):
    root, _, _ = pulled
    tds = Counter((r.season, r.threshold) for r in load_rungs(root) if r.stat == fanduel.TD_STAT)
    assert tds[(2025, 1)] >= 1 and tds[(2025, 2)] >= 1  # KXNFLANYTD, KXNFL2TD
    assert tds[(2026, 1)] >= 1  # KXNFLTD 1+


def test_fantasy_points_records_are_2026_only(pulled):
    root, _, _ = pulled
    assert load_fantasy_means(root) and {m.season for m in load_fantasy_means(root)} == {2026}
    assert all(0 < m.price < 1 for m in load_fantasy_means(root))
    assert {line.season for line in load_fantasy_lines(root)} <= {2026}


def test_pull_reports_the_drop_reasons(pulled):
    root, _, report = pulled
    assert report.drops == drop_counts(root)
    reasons = {reason for _, reason in report.drops}
    assert {"opened_in_game", "unmapped_player", "wide_spread", "one_sided"} <= reasons
    assert report.drops[("KXNFLTD", "unmapped_player")] == 1  # PIT Steelers D/ST: no gsis_id


def test_historical_markets_cost_one_request_each_and_live_ones_are_batched(pulled):
    _, http, _ = pulled
    paths = Counter(urllib.parse.urlsplit(u).path.split("/trade-api/v2")[1].split("/")[1] for u in http.calls)
    single = [u for u in http.calls if "/historical/markets/" in u]
    batch = [u for u in http.calls if "/markets/candlesticks" in u and "/historical/" not in u]
    assert single and batch
    assert all("period_interval=1&" in u or u.endswith("period_interval=1") or "period_interval=60" in u
               for u in single + batch)
    assert paths["structured_targets"] == 1  # the directory, one page here


def test_live_batches_share_a_kickoff_across_series_up_to_100_tickers(pulled):
    _, http, _ = pulled
    batches = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for u in http.calls
               if "/markets/candlesticks" in u and "/historical/" not in u]
    minute = [q["market_tickers"][0].split(",") for q in batches if q["period_interval"] == ["1"]]
    assert all(len(b) <= kalshi.BATCH for b in minute)
    assert len({t.split("-")[0] for t in minute[0]}) > 1  # several series in one request
    assert {q["end_ts"][0] for q in batches} == {"1794792000"}  # the kickoff


def test_a_second_pull_fetches_no_candles_and_skips_the_historical_listing(pulled):
    root, _, _ = pulled
    again = FixtureHttp()
    report = pull(root, [2025, 2026], http=again, schedule_fetch=schedule, roster_fetch=rosters)
    assert report.quotes_fetched == 0
    assert not [u for u in again.calls if "candlesticks" in u or "/historical/markets" in u]
    assert any("min_close_ts=" in u for u in again.calls)  # the live listing is incremental
    assert report.rungs == len(load_rungs(root))


def test_an_interrupted_pull_resumes_where_it_stopped(tmp_path):
    class Dies(FixtureHttp):
        def get_json(self, url):
            if "candlesticks" in url and sum("candlesticks" in u for u in self.calls) >= 20:
                raise FetchError("connection reset")
            return super().get_json(url)

    with pytest.raises(FetchError):
        pull(tmp_path, [2025, 2026], http=Dies(), schedule_fetch=schedule, roster_fetch=rosters)
    kept = len(Cache(tmp_path).quotes())
    assert kept > 0
    resumed = FixtureHttp()
    pull(tmp_path, [2025, 2026], http=resumed, schedule_fetch=schedule, roster_fetch=rosters)
    full = FixtureHttp()
    pull(tmp_path / "fresh", [2025, 2026], http=full, schedule_fetch=schedule, roster_fetch=rosters)
    assert load_rungs(tmp_path) == load_rungs(tmp_path / "fresh")
    count = lambda http: sum("candlesticks" in u for u in http.calls)  # noqa: E731
    assert count(resumed) < count(full)


def test_the_cache_keeps_compact_records_only(pulled):
    root, _, _ = pulled
    text = "".join(p.read_text() for p in root.rglob("*") if p.is_file())
    assert "candlesticks" not in text and "rules_primary" not in text
    assert sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) < 100_000


def test_since_skips_older_games_and_the_historical_listing(tmp_path):
    http = FixtureHttp()
    pull(tmp_path, [2026], http=http, since=kalshi.date(2026, 10, 1), schedule_fetch=schedule,
         roster_fetch=rosters)
    assert not [u for u in http.calls if "/historical/" in u and "cutoff" not in u]
    assert {r.season for r in load_rungs(tmp_path)} == {2026}


def test_a_player_missing_from_the_directory_is_looked_up_once(tmp_path):
    uuid = "a2566caa-57c6-4945-9841-2f0c10aaed8e"
    market = {"ticker": "KXNFLTD-26NOV15PITCIN-NOTD-1", "custom_strike": {"football_player": uuid},
              "floor_strike": 0.5, "open_time": "2026-11-13T15:00:00Z", "settlement_ts": "2026-11-16T04:30:00Z",
              "result": "no", "expiration_value": ""}
    listing = "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXNFLTD&status=settled&limit=1000"
    responses = {
        **{u: r for u, r in RESPONSES.items() if "/historical/cutoff" in u},
        listing: {"markets": [market], "cursor": ""},
        kalshi.api_url("/structured_targets", type="football_player", page_size=2000): {"structured_targets": []},
        kalshi.api_url(f"/structured_targets/{uuid}"): {"_status": 404},
    }
    for _ in range(2):
        http = FixtureHttp(responses)
        pull(tmp_path, [2026], http=http, since=kalshi.date(2026, 10, 1), series=["KXNFLTD"],
             schedule_fetch=schedule, roster_fetch=rosters)
    assert not [u for u in http.calls if "structured_targets" in u]  # the second pull asks nothing
    assert Cache(tmp_path).players()[uuid]["gsis_id"] is None
    assert drop_counts(tmp_path)[("KXNFLTD", "unmapped_player")] == 1


# --- the client --------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def test_the_client_paces_requests_to_four_a_second():
    clock, starts = Clock(), []

    def fetch(url):
        starts.append(clock.now)
        return 200, b"{}", {}

    http = KalshiHttp(fetch=fetch, sleep=clock.sleep, clock=clock)
    for _ in range(5):
        http.get_json("u")
    assert starts == pytest.approx([0, 0.25, 0.5, 0.75, 1.0])
    assert http.requests == 5


def test_the_client_waits_out_a_429_and_retries():
    clock, answers = Clock(), [(429, b"", {}), (429, b"", {}), (200, b'{"ok": 1}', {})]
    http = KalshiHttp(fetch=lambda url: answers.pop(0), sleep=clock.sleep, clock=clock)
    assert http.get_json("u") == {"ok": 1}
    assert 1 in clock.slept and 2 in clock.slept


def test_the_client_raises_not_found_and_fetch_errors():
    with pytest.raises(NotFound):
        KalshiHttp(fetch=lambda url: (404, b"", {}), sleep=lambda s: None).get_json("u")
    with pytest.raises(FetchError):
        KalshiHttp(fetch=lambda url: (500, b"", {}), sleep=lambda s: None).get_json("u")
    with pytest.raises(FetchError):
        KalshiHttp(fetch=lambda url: (429, b"", {}), retries=1, sleep=lambda s: None).get_json("u")


# --- the CLI -----------------------------------------------------------------------------


def test_cli_pull_prints_counts_and_drop_reasons(tmp_path, monkeypatch, capsys):
    real_pull = kalshi.pull
    seen = {}

    def fake_pull(root, seasons, since=None, series=kalshi.SERIES):
        seen.update(root=root, seasons=seasons, since=since)
        return real_pull(root, [2025, 2026], http=FixtureHttp(), since=since, series=series,
                         schedule_fetch=schedule, roster_fetch=rosters)

    monkeypatch.setattr(kalshi, "pull", fake_pull)
    assert kalshi.main(["pull", "--root", str(tmp_path), "--since", "2025-10-01"]) == 0
    out = capsys.readouterr().out
    assert seen["since"] == kalshi.date(2025, 10, 1) and seen["seasons"][0] == 2025
    assert "Rungs" in out and "opened_in_game" in out and "KXNFLRECYDS" in out
