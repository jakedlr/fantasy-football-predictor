"""Paced, cached JSON fetching (S2.1). Copied from prizepicks-optimizer's tests/test_sources.py, trimmed."""

import logging
import os
import time

import pytest

from ffp.sources.http import FetchError, Http, redact


class FakeFetch:
    """Serves canned (status, body[, headers]) answers by URL and records every call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        answer = self.responses[url]
        return answer(len([c for c in self.calls if c == url])) if callable(answer) else answer


def no_sleep(seconds):
    pass


# --- cache --------------------------------------------------------------------


def test_a_second_identical_get_within_the_ttl_is_answered_from_the_cache(tmp_path):
    fetch = FakeFetch({"u": (200, b'{"a": 1}')})
    assert Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json("u") == {"a": 1}
    assert Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json("u") == {"a": 1}
    assert fetch.calls == ["u"]


@pytest.mark.parametrize("age, refetched", [(55, False), (65, True)])
def test_the_cache_keeps_a_response_for_one_minute_by_default(tmp_path, age, refetched):
    fetch = FakeFetch({"u": (200, b'{"a": 1}')})
    Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json("u")
    for f in tmp_path.glob("*.json"):
        os.utime(f, (time.time() - age, time.time() - age))
    Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json("u")
    assert len(fetch.calls) == (2 if refetched else 1)


def test_without_the_cache_every_get_is_fetched(tmp_path):
    fetch = FakeFetch({"u": (200, b'{"a": 1}')})
    Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json("u")
    http = Http(tmp_path, use_cache=False, fetch=fetch, sleep=no_sleep)
    http.get_json("u")
    http.get_json("u")
    assert fetch.calls == ["u", "u", "u"]
    assert http.live_requests == 2


def test_live_requests_counts_fetches_not_cache_hits(tmp_path):
    fetch = FakeFetch({"a": (200, b"{}"), "b": (200, b"{}")})
    http = Http(tmp_path, fetch=fetch, sleep=no_sleep)
    http.get_json("a")
    http.get_json("a")
    http.get_json("b")
    assert http.live_requests == 2


def test_the_default_cache_dir_is_dot_cache():
    assert Http().cache_dir.parts[0] == ".cache"


@pytest.mark.parametrize("answer", [(403, b'{"url": "captcha"}'), (500, b"{}"), (200, b"<html>")])
def test_a_failed_or_non_json_answer_is_a_fetch_error_and_is_not_cached(tmp_path, answer):
    http = Http(tmp_path, fetch=FakeFetch({"u": answer}), sleep=no_sleep)
    with pytest.raises(FetchError):
        http.get_json("u")
    assert list(tmp_path.glob("*.json")) == []


def test_a_network_error_is_a_fetch_error(tmp_path):
    def fetch(url):
        raise ConnectionResetError("connection reset by peer")

    with pytest.raises(FetchError, match="connection reset by peer"):
        Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json("u")


# --- pacing -------------------------------------------------------------------


class FakeClock:
    """Time that moves only when a request takes time or the run pauses."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def paced(tmp_path, fetch=None, delay=(1, 1)):
    clock, slept = FakeClock(), []

    def sleep(seconds):
        slept.append(seconds)
        clock.sleep(seconds)

    fetch = fetch or (lambda url: (200, b"{}"))
    return Http(tmp_path, fetch=fetch, sleep=sleep, clock=clock, delay=delay), clock, slept


def test_live_requests_to_one_host_are_paused_between(tmp_path):
    http, _, slept = paced(tmp_path, delay=(3.5, 5))
    http.get_json("https://dk.example/a")
    http.get_json("https://dk.example/b")
    http.get_json("https://dk.example/c")
    assert len(slept) == 2 and all(3.5 <= s <= 5 for s in slept)


def test_the_pause_follows_a_slow_request_rather_than_being_swallowed_by_it(tmp_path):
    clock = None

    def slow(url):
        clock.sleep(2)
        return 200, b"{}"

    http, clock, slept = paced(tmp_path, fetch=slow)
    http.get_json("https://dk.example/a")
    http.get_json("https://dk.example/b")
    assert slept == [1]


def test_time_already_passed_counts_toward_the_pause(tmp_path):
    http, clock, slept = paced(tmp_path, delay=(3, 3))
    http.get_json("https://dk.example/a")
    clock.sleep(2)  # the caller parsed for 2 s
    http.get_json("https://dk.example/b")
    assert slept == [1]


def test_each_host_is_paced_on_its_own(tmp_path):
    slept = []  # time stands still: no host's pause runs down while another's is waited out
    http = Http(tmp_path, fetch=lambda url: (200, b"{}"), sleep=slept.append, clock=FakeClock(), delay=(1, 1))
    for url in ["https://dk.example/a", "https://fd.example/a", "https://dk.example/b", "https://fd.example/b"]:
        http.get_json(url)
    assert slept == [1, 1]  # before each host's second request


def test_cache_hits_are_not_paced(tmp_path):
    http, _, slept = paced(tmp_path)
    http.get_json("https://dk.example/a")
    http.get_json("https://dk.example/a")
    assert slept == []


def test_the_default_pause_is_at_least_3_5_seconds():
    low, high = Http().delay
    assert 3.5 <= low <= high


# --- a host that pushes back ----------------------------------------------------

URL = "https://dk.example/a"


def pushes_back(status=429, retry_after=None, times=1):
    """An answer that pushes back on the first `times` requests, then succeeds."""
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    return lambda n: (status, b"{}", headers) if n <= times else (200, b'{"ok": true}', {})


def test_a_429_waits_out_the_retry_after_then_retries_once(tmp_path):
    fetch = FakeFetch({URL: pushes_back(retry_after="5")})
    http, _, slept = paced(tmp_path, fetch=fetch)
    assert http.get_json(URL) == {"ok": True}
    assert slept == [5] and fetch.calls == [URL, URL] and http.live_requests == 2


def test_a_retry_after_date_is_respected(tmp_path):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime

    when = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=8), usegmt=True)
    http, _, slept = paced(tmp_path, fetch=FakeFetch({URL: pushes_back(retry_after=when)}))
    http.get_json(URL)
    assert len(slept) == 1 and 6 <= slept[0] <= 8


def test_a_429_without_retry_after_backs_off_by_a_pause_and_slows_the_host(tmp_path):
    fetch = FakeFetch({URL: pushes_back(), "https://dk.example/b": (200, b"{}")})
    http, _, slept = paced(tmp_path, fetch=fetch, delay=(2, 2))
    http.get_json(URL)
    http.get_json("https://dk.example/b")
    assert slept == [2, 4]  # one pause before the retry; the host's pause is doubled after it


def test_a_403_is_retried_once_after_a_pause(tmp_path):
    fetch = FakeFetch({URL: pushes_back(status=403, retry_after="5")})
    http, _, slept = paced(tmp_path, fetch=fetch)
    assert http.get_json(URL) == {"ok": True}
    assert slept == [1]  # a 403 is not a rate limit: its Retry-After is not honoured


def test_a_host_that_rate_limits_twice_fails_naming_the_rate_limit(tmp_path):
    http, _, _ = paced(tmp_path, fetch=FakeFetch({URL: pushes_back(times=2)}))
    with pytest.raises(FetchError, match=r"^rate-limited: HTTP 429 from \S+, again after backing off$"):
        http.get_json(URL)


def test_a_host_that_refuses_twice_is_blocked(tmp_path):
    http, _, _ = paced(tmp_path, fetch=FakeFetch({URL: pushes_back(status=403, times=2)}))
    with pytest.raises(FetchError, match=r"^refused: HTTP 403 from \S+ \(blocked, or off a US connection\)$"):
        http.get_json(URL)


def test_a_host_asking_for_a_long_wait_fails_at_once(tmp_path):
    fetch = FakeFetch({URL: pushes_back(retry_after="60")})
    http, _, slept = paced(tmp_path, fetch=fetch)
    with pytest.raises(FetchError, match=r"^rate-limited: HTTP 429 from \S+, asked to wait 60s$"):
        http.get_json(URL)
    assert fetch.calls == [URL] and slept == []


# --- the FanDuel app key stays out of logs and errors ---------------------------

KEY = "NotARealAppKey123"
FD_URL = f"https://fd.example/api/event-page?_ak={KEY}&eventId=1&tab=receiving-props"


def logged(caplog):
    return [r.getMessage() for r in caplog.records]


def test_requests_are_logged_without_the_app_key(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    http, _, _ = paced(tmp_path, fetch=FakeFetch({FD_URL: pushes_back(retry_after="1")}))
    http.get_json(FD_URL)
    http.get_json(FD_URL)  # cache hit
    messages = logged(caplog)
    assert len(messages) >= 3 and all(KEY not in m for m in messages)
    assert any("eventId=1&tab=receiving-props" in m for m in messages)


@pytest.mark.parametrize("answer", [(500, b"{}"), (200, b"<html>"), (429, b"{}", {"Retry-After": "60"}),
                                    (403, b"{}")])
def test_fetch_errors_never_carry_the_app_key(tmp_path, caplog, answer):
    caplog.set_level(logging.DEBUG)
    http, _, _ = paced(tmp_path, fetch=FakeFetch({FD_URL: answer}))
    with pytest.raises(FetchError) as raised:
        http.get_json(FD_URL)
    assert KEY not in str(raised.value) and "_ak=" in str(raised.value)
    assert KEY not in repr(raised.value.__cause__) and KEY not in repr(raised.value.__context__)
    assert all(KEY not in m for m in logged(caplog))


def test_a_network_error_naming_the_url_does_not_leak_the_app_key(tmp_path):
    def fetch(url):
        raise ConnectionError(f"Failed to perform, curl: (7) for {url}")

    with pytest.raises(FetchError) as raised:
        Http(tmp_path, fetch=fetch, sleep=no_sleep).get_json(FD_URL)
    assert KEY not in str(raised.value) and "ConnectionError" in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_scrapling_logs_its_request_urls_without_the_app_key(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    Http(tmp_path)  # any Http quiets and redacts the scrapling logger
    scrapling = logging.getLogger("scrapling")
    assert not scrapling.isEnabledFor(logging.INFO)
    scrapling.warning("Fetched (%s) <GET %s>", 200, FD_URL)
    assert logged(caplog) == [f"Fetched (200) <GET {FD_URL.replace(KEY, 'REDACTED')}>"]


def test_redact_hides_secret_query_values_wherever_they_appear():
    assert redact(f"see {FD_URL} and ?key=abc") == (
        "see https://fd.example/api/event-page?_ak=REDACTED&eventId=1&tab=receiving-props and ?key=REDACTED")
