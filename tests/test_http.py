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


# --- Sprint 7: each Book paced on its own, several requests in flight ---------------

import json  # noqa: E402
import threading  # noqa: E402

from ffp.sources.http import BOOK_PACING, Cancelled, Pacing, PushedBack  # noqa: E402


class Network:
    """A fake network that notes when each request starts (by `clock`) and how many are in flight at once.

    `status(url, n)` answers the url's n-th request; it is decided as the request starts, one request at a
    time. The first `together` requests wait for each other, so they are in flight at once however the
    threads are scheduled. `before_answer(status)`, if given, is called before each answer goes back."""

    def __init__(self, seconds=0.05, status=None, clock=time.monotonic, together=0, before_answer=None):
        self.seconds, self.status, self.clock = seconds, status or (lambda url, n: (200, {})), clock
        self.together = threading.Barrier(together) if together else None
        self.before_answer = before_answer
        self.lock, self.in_flight, self.peak, self.starts = threading.Lock(), 0, 0, []

    def __call__(self, url):
        with self.lock:
            self.in_flight += 1
            self.peak = max(self.peak, self.in_flight)
            self.starts.append((self.clock(), url))
            n = sum(u == url for _, u in self.starts)
            sent = len(self.starts)
            status, headers = self.status(url, n)
        if self.together is not None and sent <= self.together.parties:
            self.together.wait(timeout=5)
        if self.before_answer is not None:
            self.before_answer(status)
        time.sleep(self.seconds)
        with self.lock:
            self.in_flight -= 1
        return status, json.dumps({"url": url}).encode(), headers


class HeldTime:
    """Injected time for a Book's threads around a pushback. It stands still until the Book has backed off
    (logged "backing off"), then moves only when a request waits its turn. Every wait first waits for the
    back-off, so a request that reserved its start before the pushback cannot slip out ahead of it, however
    the threads are scheduled, and a slow machine changes no time the test sees."""

    def __init__(self):
        self.now, self.lock, self.backed_off = 0.0, threading.Lock(), threading.Event()
        self.waits, self.waited = 0, threading.Condition(self.lock)

    def __call__(self):
        with self.lock:
            return self.now

    def sleep(self, seconds):
        with self.lock:
            self.waits += 1
            self.waited.notify_all()
        assert self.backed_off.wait(timeout=5), "a request waited, but the Book never backed off"
        with self.lock:
            self.now += seconds

    def until_waiting(self, n):
        """Blocks until `n` waits have begun."""
        with self.waited:
            assert self.waited.wait_for(lambda: self.waits >= n, timeout=5), f"{n} requests never waited"

    def after_back_off(self, status):
        """A Network's `before_answer`: every answer but a pushback waits for the Book to back off."""
        if status not in (403, 429):
            assert self.backed_off.wait(timeout=5), "the Book never backed off"


@pytest.fixture
def held():
    time_ = HeldTime()

    class BackedOff(logging.Handler):
        def emit(self, record):
            if "backing off" in record.getMessage():
                time_.backed_off.set()

    handler, logger = BackedOff(), logging.getLogger("ffp.sources.http")
    logger.addHandler(handler)
    yield time_
    logger.removeHandler(handler)


URLS = [f"https://dk.example/{i}" for i in range(9)]
FAST = Pacing(in_flight=3, min_gap=0.0)


def test_each_book_is_paced_at_3_in_flight_0_3_s_apart():
    assert BOOK_PACING == {"draftkings": Pacing(3, 0.3), "fanduel": Pacing(3, 0.3)}


def test_a_paced_book_keeps_at_most_in_flight_requests_going_and_answers_in_order(tmp_path):
    net = Network(together=3)  # the first three meet in flight, however late a thread starts
    with Http(tmp_path, fetch=net).for_book("draftkings", FAST) as dk:
        got = dk.get_many(URLS)
    assert got == [{"url": u} for u in URLS]
    assert net.peak == 3


def test_a_paced_book_starts_requests_at_least_min_gap_apart(tmp_path):
    slept = []  # time stands still, so each wait is how far after the first its request starts
    http = Http(tmp_path, fetch=Network(seconds=0), sleep=slept.append, clock=FakeClock())
    with http.for_book("draftkings", Pacing(3, 0.05)) as dk:
        dk.get_many(URLS[:5])
    assert sorted(slept) == pytest.approx([0.05, 0.1, 0.15, 0.2])


def test_a_book_without_pacing_keeps_one_request_at_a_time_with_the_slow_pause(tmp_path):
    clock, net, slept = FakeClock(), Network(seconds=0), []
    http = Http(tmp_path, fetch=net, sleep=lambda t: (slept.append(t), clock.sleep(t)), clock=clock, delay=(3.5, 5),
                book_pacing={})
    with http.for_book("draftkings") as dk:
        dk.get_many(URLS[:4])
    assert net.peak == 1 and len(slept) == 3 and all(3.5 <= s <= 5 for s in slept)


def test_a_failed_request_in_a_batch_costs_only_its_own_answer(tmp_path):
    # Unlike prizepicks, one failed FanDuel event leaves out only its own Game.
    net = Network(seconds=0, status=lambda url, n: (500, {}) if url == URLS[1] else (200, {}))
    with Http(tmp_path, fetch=net, sleep=no_sleep).for_book("fanduel", FAST) as fd:
        got = fd.get_many(URLS[:3])
    assert got[0] == {"url": URLS[0]} and got[2] == {"url": URLS[2]}
    assert isinstance(got[1], FetchError) and "HTTP 500" in str(got[1])


def test_the_timing_counts_each_books_live_and_cached_requests_and_seconds(tmp_path):
    http = Http(tmp_path, fetch=Network(seconds=0), sleep=no_sleep)
    with http.for_book("draftkings", FAST) as dk:
        dk.get_many(URLS[:3])
    with http.for_book("fanduel", FAST) as fd:
        fd.get_many(URLS[:2] + ["https://fd.example/x"])  # two answered from the cache
    timing = http.timing
    assert list(timing) == ["draftkings", "fanduel"]
    assert (timing["draftkings"]["requests"], timing["draftkings"]["cached"]) == (3, 0)
    assert (timing["fanduel"]["requests"], timing["fanduel"]["cached"]) == (1, 2)
    assert timing["fanduel"]["seconds"] >= 0 and timing["fanduel"]["failed"] is False


# --- a Book that pushes back is rate-limited, then left out -------------------------

def limits_first(url_to_limit, status=429, retry_after=None, times=1):
    """A Book that pushes back on one URL's first `times` requests."""
    def respond(url, n):
        if url == url_to_limit and n <= times:
            return status, {"Retry-After": retry_after} if retry_after is not None else {}
        return 200, {}
    return respond


def limits_the_first_sent(retry_after=None):
    """A Book that rate-limits whichever request reaches it first, and answers every other."""
    sent = []  # Network asks one request at a time

    def respond(url, n):
        sent.append(url)
        return (429, {} if retry_after is None else {"Retry-After": retry_after}) if len(sent) == 1 else (200, {})
    return respond


@pytest.mark.parametrize("asked, delay", [("0.3", (0.01, 0.01)), (None, (0.3, 0.3))])
def test_a_book_that_pushes_back_holds_every_request_until_the_wait_is_over(tmp_path, held, asked, delay):
    # Two requests are in flight alongside the one rate-limited (all three sent at once), and answer only once
    # the Book has backed off.
    net = Network(seconds=0, status=limits_the_first_sent(asked), clock=held, together=3,
                  before_answer=held.after_back_off)
    http = Http(tmp_path, fetch=net, delay=delay, clock=held, sleep=held.sleep)
    with http.for_book("draftkings", FAST) as dk:
        got = dk.get_many(URLS)
    assert got == [{"url": u} for u in URLS]
    assert http.timing["draftkings"]["rate_limited"] is True
    starts = sorted(t for t, _ in net.starts)
    # The three sent before the 429 landed are under way; nothing else goes out during the 0.3 s wait.
    assert starts[:3] == [0, 0, 0] and len(starts) == 10 and starts[3] >= 0.3
    # Then one at a time, pausing after each.
    assert all(b - a >= delay[0] - 1e-9 for a, b in zip(starts[3:], starts[4:]))


def run_book(tmp_path, respond, urls=URLS[:1]):
    net = Network(seconds=0, status=respond)
    http = Http(tmp_path, fetch=net, delay=(0, 0), sleep=no_sleep)
    with http.for_book("draftkings", FAST) as dk:
        got = dk.get_many(urls)
    return http, net, got


def test_a_book_that_rate_limits_twice_is_left_out_naming_the_rate_limit(tmp_path):
    with pytest.raises(PushedBack, match=r"^rate-limited: HTTP 429 from \S+, again after backing off$"):
        run_book(tmp_path, limits_first(URLS[0], status=429, times=2))


def test_a_book_that_refuses_twice_is_left_out_as_refused(tmp_path):
    with pytest.raises(PushedBack, match=r"^refused: HTTP 403 from \S+ \(blocked, or off a US connection\)$"):
        run_book(tmp_path, limits_first(URLS[0], status=403, times=2))


def test_a_book_left_out_sends_nothing_more(tmp_path):
    net = Network(seconds=0, status=limits_first(URLS[0], times=2))
    http = Http(tmp_path, fetch=net, delay=(0, 0), sleep=no_sleep)
    with pytest.raises(PushedBack):
        with http.for_book("draftkings", Pacing(1, 0.0)) as dk:
            dk.get_many(URLS[:4])
    with pytest.raises(PushedBack):
        http.for_book("draftkings").get_json(URLS[5])  # later in the same run: refused without a request
    assert [u for _, u in net.starts] == [URLS[0], URLS[0]]
    assert http.timing["draftkings"]["failed"] is True


def test_a_book_that_refused_once_then_answered_was_not_rate_limited(tmp_path):
    http, net, _ = run_book(tmp_path, limits_first(URLS[0], status=403))
    assert len(net.starts) == 2 and http.timing["draftkings"]["rate_limited"] is False


def test_a_slowed_book_stays_slowed_when_handed_a_view_again(tmp_path):
    net = Network(seconds=0, status=limits_first(URLS[0]))
    http = Http(tmp_path, fetch=net, delay=(0, 0), sleep=no_sleep)
    with http.for_book("draftkings", FAST) as dk:
        dk.get_many(URLS[:1])
    with http.for_book("draftkings", FAST) as dk:
        dk.get_many(URLS[1:7])
    assert net.peak == 1


# --- stopping: Ctrl-C ends every wait and every request still to start ------------

def test_a_stopped_http_cuts_a_back_off_wait_short(tmp_path):
    # Real time: the wait being cut is Http's own. Stopped after 0.1 s of a 10 s wait; a slow machine has 5 s.
    net = Network(seconds=0, status=limits_first(URLS[0], retry_after="10"))
    http = Http(tmp_path, fetch=net)
    threading.Timer(0.1, http.stop).start()
    began = time.monotonic()
    with pytest.raises(Cancelled):
        with http.for_book("draftkings", Pacing(1, 0.0)) as dk:
            dk.get_many(URLS[:1])
    assert time.monotonic() - began < 5 and len(net.starts) == 1


def test_a_stopped_http_stops_the_rest_of_a_batch(tmp_path):
    http = None

    def stop_on_first(url, n):
        http.stop()
        return 200, {}

    net = Network(status=stop_on_first)  # stops as the first request goes out, before any has finished
    http = Http(tmp_path, fetch=net)
    with pytest.raises(Cancelled):
        with http.for_book("draftkings", FAST) as dk:
            dk.get_many(URLS)
    assert len(net.starts) <= 3


def test_a_stopped_http_ends_the_slow_pause_early(tmp_path):
    # Real time, as above: stopped after 0.1 s of a 10 s pause.
    http = Http(tmp_path, fetch=Network(seconds=0), delay=(10, 10))
    http.get_json("https://kalshi.example/a")
    threading.Timer(0.1, http.stop).start()
    began = time.monotonic()
    with pytest.raises(Cancelled):
        http.get_json("https://kalshi.example/b")
    assert time.monotonic() - began < 5


# --- review of PR #15 ------------------------------------------------------------

def test_batch_requests_run_on_daemon_threads(tmp_path):
    # Finding 1: pool workers are joined at exit, so Ctrl-C hung up to 30 s on a request in flight.
    daemons = []

    def fetch(url):
        daemons.append(threading.current_thread().daemon)
        return 200, b"{}", {}

    with Http(tmp_path, fetch=fetch, sleep=no_sleep).for_book("fanduel", FAST) as fd:
        fd.get_many(URLS[:4])
    assert daemons and all(daemons)


def test_requests_already_waiting_their_turn_also_wait_out_a_hold(tmp_path, held):
    # Finding 2: B and C had reserved starts before A's 429 and went out during the hold. Whichever request
    # the threads send first is A: the Book rate-limits it, answering once B and C are waiting their turn.
    net = Network(seconds=0, status=limits_the_first_sent(retry_after="0.5"), clock=held,
                  before_answer=lambda status: held.until_waiting(2))
    http = Http(tmp_path, fetch=net, delay=(0.01, 0.01), clock=held, sleep=held.sleep)
    with http.for_book("draftkings", Pacing(3, 0.1)) as dk:
        dk.get_many(URLS[:3])
    first, *others = sorted(t for t, _ in net.starts)
    assert first == 0 and len(others) == 3 and all(t >= 0.5 for t in others)  # B, C and A's retry


def test_a_long_retry_after_fails_only_that_request(tmp_path):
    # Finding 3: one 429 asking 15 s left the whole Book out without a retry.
    net = Network(seconds=0, status=limits_first(URLS[0], retry_after="15"))
    http = Http(tmp_path, fetch=net, delay=(0, 0), sleep=no_sleep)
    with http.for_book("draftkings", FAST) as dk:
        got = dk.get_many(URLS[:3])
    assert isinstance(got[0], FetchError) and not isinstance(got[0], PushedBack)
    assert "asked to wait 15s" in str(got[0]) and got[1:] == [{"url": u} for u in URLS[1:3]]
    assert http.timing["draftkings"]["rate_limited"] is True and http.timing["draftkings"]["failed"] is False


def test_simultaneous_pushbacks_back_the_book_off_once(tmp_path, caplog):
    # Finding 4: three 403s in flight each held, warned and retried on their own.
    net = Network(seconds=0.02, status=lambda url, n: (403, {}) if n == 1 and url in URLS[:3] else (200, {}),
                  together=3)  # all three in flight at once, however the threads are scheduled
    http = Http(tmp_path, fetch=net, delay=(0.01, 0.01))
    with http.for_book("fanduel", FAST) as fd:
        got = fd.get_many(URLS[:3])
    assert got == [{"url": u} for u in URLS[:3]]
    assert sum("backing off" in r.getMessage() for r in caplog.records) == 1


def test_a_book_slowed_by_a_refusal_is_timed_as_slowed(tmp_path):
    # Finding 9: a 403 slows the Book for the run; the timing said nothing.
    http, _, _ = run_book(tmp_path, limits_first(URLS[0], status=403))
    t = http.timing["draftkings"]
    assert (t["slowed"], t["rate_limited"]) == (True, False)


def test_a_book_is_paced_by_its_own_pacing_unless_told_otherwise(tmp_path):
    http = Http(tmp_path, fetch=Network(seconds=0.05), sleep=no_sleep)
    with http.for_book("fanduel") as fd:  # BOOK_PACING's 3 in flight
        fd.get_many(URLS[:6])
    assert http._pacer("fanduel").in_flight == 3
