"""Paced, cached JSON fetching (S2.1). Copied from prizepicks-optimizer's tests/test_sources.py, trimmed."""

import os
import time

import pytest

from ffp.sources.http import FetchError, Http


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
