"""Polite, cached JSON fetching via Scrapling.

Copied from prizepicks-optimizer's ppopt/sources/http.py and trimmed to what a
one-shot CLI run needs: its Sprint 7 Pacing (several requests in flight per Book,
pushback, stop) came over in this repo's Sprint 7. The low-level fetch is passed
in, so tests never touch the network (PLAN "Stack").
"""

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import NamedTuple
from urllib.parse import unquote_plus, urlsplit, urlunsplit

DEFAULT_CACHE_DIR = Path(".cache") / "http"  # gitignored
REUSE_SECONDS = 60  # how long a response is reused: lines move fast
DELAY = (3.5, 5.0)  # seconds paused after each live request to a host (research/sportsbook-markets.md paced 3.5 s)

log = logging.getLogger(__name__)

# Query parameters that carry app keys or credentials (e.g. FanDuel's _ak, PLAN S2.1).
SECRET_QUERY_PARAMS = frozenset({"_ak", "ak", "apikey", "api_key", "key", "token"})
_SECRET = re.compile(
    r"(?i)([?&](?:" + "|".join(sorted(SECRET_QUERY_PARAMS)) + r")=)[^&#\s'\"<>]*"
)


def redact(text: str) -> str:
    """`text` with the value of every secret query parameter in it replaced by REDACTED."""
    return _SECRET.sub(r"\1REDACTED", text)


def redact_url(url: str) -> str:
    """url without its secret query parameters; everything else is kept as written.

    For stored URLs (snapshots). `redact` is for free text such as log lines and errors.
    """
    parts = urlsplit(url)
    kept = [
        pair
        for pair in parts.query.split("&")
        if pair and unquote_plus(pair.partition("=")[0]).lower() not in SECRET_QUERY_PARAMS
    ]
    return urlunsplit(parts._replace(query="&".join(kept)))

class _Redacting(logging.Filter):
    """Rewrites a log record whose message carries a secret query parameter."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if redact(message) != message:
            record.msg, record.args = redact(message), None
        return True


def _guard(logger: logging.Logger, level: int | None = None) -> None:
    if not any(isinstance(f, _Redacting) for f in logger.filters):
        logger.addFilter(_Redacting())
    if level is not None and logger.getEffectiveLevel() < level:
        logger.setLevel(level)


def quiet_scrapling() -> None:
    """Scrapling logs every request URL, app key included, at INFO: raise it to WARNING and redact what's left."""
    _guard(logging.getLogger("scrapling"), logging.WARNING)


_guard(log)


class FetchError(RuntimeError):
    """A request failed, was blocked, or did not return JSON."""


class PushedBack(FetchError):
    """A Book rate-limited or refused us again after backing off: it is left out for the rest of the run."""


class Cancelled(Exception):
    """The run was stopped (Ctrl-C): no request starts and no wait goes on from here."""


def scrapling_fetch(url: str) -> tuple[int, bytes, dict]:
    """Fetch over TLS-impersonating HTTP with Scrapling's Fetcher. Returns status, body and response headers."""
    from scrapling.fetchers import Fetcher

    quiet_scrapling()  # importing scrapling sets its logger to INFO
    page = Fetcher.get(url, impersonate="chrome", headers={"Accept": "application/json"}, timeout=30)
    return page.status, page.body, dict(page.headers or {})

class Pacing(NamedTuple):
    """How fast a run may ask one Book: at most `in_flight` requests at once, starts `min_gap` seconds apart."""

    in_flight: int
    min_gap: float


# Each Book's Pacing (PLAN Sprint 7): prizepicks-optimizer's values on the same endpoints, 77 requests in 14 s
# with no rate limit. Requests made by no Book (nflverse, Kalshi) keep the slow pause, one at a time per host.
BOOK_PACING = {"draftkings": Pacing(3, 0.3), "fanduel": Pacing(3, 0.3)}

RATE_LIMITED = 429
REFUSED = 403  # blocked (bot protection, or a geo-block off a US connection); sometimes it passes
PUSHED_BACK = (RATE_LIMITED, REFUSED)
MAX_RETRY_AFTER = 10  # seconds; odds a minute old aren't worth waiting for
SLOWED = 2  # a host that pushed back gets this many times the usual pause for the rest of the run


def retry_after(headers: dict) -> float | None:
    """Seconds a Retry-After header asks for (a number or an HTTP date), or None."""
    value = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


class Pacer:
    """How fast one Book (or, for requests made by no Book, one host) may be asked: at most `in_flight` requests
    at once, and a gap of `gap()` seconds between them, counted from the last request's start or, with
    `after_end`, from its end. Used as a context manager around each live request. Ported from
    prizepicks-optimizer's Sprint 7."""

    def __init__(self, in_flight: int, gap, clock, sleep, after_end: bool = False, check=lambda: None):
        self.in_flight, self.gap, self.clock, self.sleep, self.after_end = in_flight, gap, clock, sleep, after_end
        self.check = check  # raises Cancelled if the run has stopped; called once a request has waited its turn
        self._cond = threading.Condition()
        self._active = 0
        self._next_start: float | None = None
        self.epoch = 0  # bumped by each slow_down: a request admitted before it waits its turn again

    @classmethod
    def slow(cls, delay, clock, sleep, check=lambda: None) -> "Pacer":
        """One request at a time, pausing `delay` (a (low, high) range of seconds) after each."""
        return cls(1, lambda: random.uniform(*delay), clock, sleep, after_end=True, check=check)

    def __enter__(self):
        while True:
            self.check()
            with self._cond:
                while self._active >= self.in_flight:
                    self._cond.wait(0.1)
                    self.check()
                self._active += 1
                epoch = self.epoch
                now = self.clock()
                start = now if self._next_start is None else max(now, self._next_start)
                if not self.after_end:
                    self._next_start = start + self.gap()
            try:
                if start > now:
                    self.sleep(start - now)
                self.check()
            except BaseException:
                self._release()
                raise
            with self._cond:
                if self.epoch == epoch:
                    return self
            self._release()  # the Book pushed back while this one waited: queue again, under the hold

    def _release(self):
        with self._cond:
            self._active -= 1
            self._cond.notify_all()

    def __exit__(self, *exc):
        with self._cond:
            self._active -= 1
            if self.after_end:  # never earlier than a hold already set
                self._next_start = max(self._next_start or 0.0, self.clock() + self.gap())
            self._cond.notify_all()
        return False

    def slow_down(self, gap, hold: float, epoch: int | None = None) -> bool:
        """The Book pushed back: no request starts until `hold` seconds from now, then one at a time, pausing
        `gap()` seconds after each. With `epoch` (the one the pushed-back request was sent in), only the first
        pushback since then slows the Book; later ones, sent alongside it, return False and just retry."""
        with self._cond:
            if epoch is not None and epoch != self.epoch:
                return False
            self.epoch += 1
            self.in_flight = 1
            self.gap = gap
            self.after_end = True
            until = self.clock() + hold
            self._next_start = max(self._next_start or until, until)
            return True


class Http:
    """GETs JSON, reusing a cached answer for `ttl` seconds.

    A Book's requests go through its view (`for_book`), paced by its Pacing: several in flight, or without
    one, one at a time with a pause of `delay` (a (low, high) range of seconds) after each. Requests made by no
    Book are paced per host, one at a time with that pause. A Book or host that pushes back (429 or 403) is
    waited out (a 429's Retry-After, else one pause), slowed to one request at a time, and asked once more; a
    Book that pushes back again is left out for the rest of the run (PushedBack). `stop()` (Ctrl-C) ends every
    wait and makes every request still to start raise Cancelled. Safe to share between threads.
    """

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        ttl: float = REUSE_SECONDS,
        use_cache: bool = True,
        delay: tuple[float, float] = DELAY,
        fetch=scrapling_fetch,
        sleep=None,
        clock=time.monotonic,
        book_pacing=None,
    ):
        self.cache_dir = Path(cache_dir)
        self.book_pacing = dict(BOOK_PACING if book_pacing is None else book_pacing)  # {Book: Pacing}
        self.ttl = ttl
        self.use_cache = use_cache
        self.fetch = fetch
        self.delay = delay
        self._stopped = threading.Event()
        self.sleep = sleep or self._wait  # by default a wait ends early when the run stops
        self.clock = clock
        self._lock = threading.Lock()  # Books share this Http from their own threads
        self._live_requests = 0
        self._pacers: dict[str, Pacer] = {}  # {Book, or host for requests made by no Book: its Pacer}
        self._slowed: set[str] = set()  # Books and hosts that pushed back during this run
        self._rate_limited: set[str] = set()  # Books that answered 429
        self._left_out: dict[str, PushedBack] = {}  # Books that pushed back again: refused without a request
        self._by_book: dict[str, Counter] = {}
        self._timing: dict[str, dict] = {}
        self._books: list[str] = []  # in the order they were handed views
        quiet_scrapling()

    @property
    def live_requests(self) -> int:
        """Requests made live so far (fetched, not answered from the cache)."""
        return self._live_requests

    def stop(self) -> None:
        """End the run's fetching: waits end now, and every request from here on raises Cancelled."""
        self._stopped.set()

    def _wait(self, seconds: float) -> None:
        self._stopped.wait(seconds)

    def _check(self) -> None:
        if self._stopped.is_set():
            raise Cancelled()

    @property
    def timing(self) -> dict[str, dict]:
        """{Book: {"requests", "cached", "failed", "rate_limited", "seconds"}} for each Book whose view has
        finished, in the order the views were handed out. `requests` counts live requests, retries included."""
        with self._lock:
            return {book: dict(self._timing[book]) for book in self._books if book in self._timing}

    def for_book(self, book: str, pacing: Pacing | None = None) -> "BookHttp":
        """This Http as `book` uses it: same cache, paced by `pacing` (without one, the Book's own from
        `book_pacing`, else the slow pause), its live requests and time recorded. A Book keeps its first
        Pacer for the run, slowed or not."""
        pacing = pacing if pacing is not None else self.book_pacing.get(book)
        with self._lock:
            if book not in self._books:
                self._books.append(book)
        self._pacer(book, pacing)
        return BookHttp(self, book)

    def _pacer(self, key: str, pacing: Pacing | None = None) -> Pacer:
        with self._lock:
            if key not in self._pacers:
                if pacing is not None:
                    gap = float(pacing.min_gap)
                    self._pacers[key] = Pacer(pacing.in_flight, lambda: gap, self.clock, self.sleep, check=self._check)
                else:
                    self._pacers[key] = Pacer.slow(self.delay, self.clock, self.sleep, self._check)
            return self._pacers[key]

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def get_json(self, url: str):
        """The JSON at `url`, from the cache while it's fresh, else fetched live and cached."""
        return self._get_json(url, "")

    def get_many(self, urls: list[str]) -> list:
        """Each URL's JSON in order, or the FetchError that request failed with, one at a time (a Book's view
        fetches them as a batch: BookHttp.get_many). A stopped run (Cancelled) is raised."""
        out = []
        for url in urls:
            try:
                out.append(self.get_json(url))
            except FetchError as error:
                out.append(error)
        return out

    def _get_json(self, url: str, book: str):
        self._check()
        with self._lock:
            left_out = self._left_out.get(book)
        if left_out is not None:
            raise PushedBack(str(left_out))
        path = self._cache_path(url)
        if self.use_cache and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            log.debug("cached %s", redact(url))
            data = json.loads(path.read_text())
            with self._lock:
                self._by_book.setdefault(book, Counter())["cached"] += 1
            return data

        body = self._fetch_live(url, book)
        try:
            data = json.loads(body)
        except ValueError:
            raise FetchError(f"non-JSON response from {redact(url)}") from None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Write aside, then rename: another run (or thread) reading this response never sees half of it.
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_bytes(body)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return data

    def _fetch_live(self, url: str, book: str) -> bytes:
        """The body of a 200 answer at `url`, fetched live: paced, and backed off once if pushed back."""
        key = book or urlsplit(url).netloc
        pacer = self._pacer(key)
        epoch, (status, body, *rest) = self._send(url, book, pacer)
        if status in PUSHED_BACK:
            try:
                status, body = self._back_off(url, book, key, pacer, epoch, status, rest[0] if rest else {})
            except PushedBack as error:
                if book:
                    with self._lock:
                        self._left_out.setdefault(book, error)
                    raise
                raise FetchError(str(error)) from None  # a host made by no Book: just this request fails
        if status != 200:
            raise FetchError(f"HTTP {status} from {redact(url)}")
        return body if isinstance(body, bytes) else body.encode()

    def _send(self, url: str, book: str, pacer: Pacer) -> tuple[int, tuple]:
        """One live request, in its turn, and the Pacer's epoch it was sent in. None once its Book is left
        out (a retry queued behind the one that failed is not sent)."""
        with pacer:
            epoch = pacer.epoch
            with self._lock:
                left_out = self._left_out.get(book) if book else None
                if left_out is not None:
                    raise PushedBack(str(left_out))
                self._live_requests += 1
                self._by_book.setdefault(book, Counter())["live"] += 1
            log.info("GET %s", redact(url))
            try:
                answer = self.fetch(url)
            except Exception as e:
                # from None: the original error's message may hold the URL, app key included.
                raise FetchError(f"request to {redact(url)} failed: {type(e).__name__}: {redact(str(e))}") from None
        log.info("HTTP %s from %s", answer[0], redact(url))
        return epoch, answer

    def _back_off(self, url, book, key, pacer, epoch, status, headers) -> tuple[int, bytes]:
        """The Book (or host) pushed back: hold all its requests for the wait a 429 asked for (or one pause),
        slow it to one request at a time for the rest of the run, then retry this request once, in turn like
        any other. Requests pushed back alongside it (sent in the same epoch) back the Book off only once.
        Only a 429 is a rate limit. A 429 asking for more than MAX_RETRY_AFTER fails just this request."""
        shown = redact(url)
        if status == RATE_LIMITED and book:
            with self._lock:
                self._rate_limited.add(book)
        # A Book drops from its fast pace to the usual pause; a host already on it doubles it.
        factor = SLOWED if not book else 1
        gap = lambda: random.uniform(*self.delay) * factor  # noqa: E731
        with self._lock:
            self._slowed.add(key)
        wait = retry_after(headers) if status == RATE_LIMITED else None
        if wait is not None and wait > MAX_RETRY_AFTER:
            pacer.slow_down(gap, gap(), epoch)
            raise FetchError(f"rate-limited: HTTP {status} from {shown}, asked to wait {wait:g}s")
        # The hold: what a 429 asked for, and one plain pause at least.
        if pacer.slow_down(gap, max(wait or 0.0, random.uniform(*self.delay)), epoch):
            log.warning("HTTP %s from %s: backing off, then retrying once", status, shown)
        _, (retry, body, *_) = self._send(url, book, pacer)
        if retry == RATE_LIMITED:
            raise PushedBack(f"rate-limited: HTTP {retry} from {shown}, again after backing off")
        if retry == REFUSED:
            raise PushedBack(f"refused: HTTP {retry} from {shown} (blocked, or off a US connection)")
        return retry, body  # anything else is judged like a first answer


class BookHttp:
    """One Book's view of the run's Http, used as a context manager around that Book's fetching."""

    def __init__(self, http: Http, book: str):
        self.http, self.book = http, book

    def get_json(self, url: str):
        return self.http._get_json(url, self.book)

    def get_many(self, urls: list[str]) -> list:
        """Every URL's response, in order, fetched as fast as the Book's Pacing allows. A request that fails is
        answered by its FetchError, so one failed Game costs only its own answer; the Book being left out
        (PushedBack) or the run stopping (Cancelled) stops the requests not yet started and is raised once the
        ones in flight have landed."""
        if not urls:
            return []
        abort = threading.Event()

        def one(url):
            if abort.is_set():
                raise Cancelled()
            try:
                return self.get_json(url)
            except FetchError as error:
                if isinstance(error, PushedBack):
                    abort.set()
                    raise
                return error
            except BaseException:
                abort.set()
                raise

        results: list = [None] * len(urls)
        errors: list = [None] * len(urls)
        todo = iter(range(len(urls)))
        lock = threading.Lock()

        def worker():
            while True:
                with lock:
                    i = next(todo, None)
                if i is None:
                    return
                try:
                    results[i] = one(urls[i])
                except BaseException as error:  # noqa: BLE001 - raised below, in the caller's thread
                    errors[i] = error

        # Daemon threads, not a pool: Python joins pool workers at exit, so Ctrl-C would wait out a request
        # in flight (up to Scrapling's 30 s timeout). A daemon worker is abandoned with the run instead.
        workers = [threading.Thread(target=worker, daemon=True)
                   for _ in range(min(len(urls), self.http._pacer(self.book).in_flight))]
        for w in workers:
            w.start()
        for w in workers:
            while w.is_alive():
                w.join(0.1)
        raised = [e for e in errors if e is not None]
        for kind in (PushedBack, Cancelled):  # the reason the batch stopped, not a request it then skipped
            first = next((e for e in raised if isinstance(e, kind)), None)
            if first is not None:
                raise first
        if raised:
            raise raised[0]
        return results

    def __enter__(self):
        self.started = self.http.clock()
        return self

    def __exit__(self, exc_type, *exc):
        with self.http._lock:
            counts = self.http._by_book.get(self.book, Counter())
            self.http._timing[self.book] = {
                "requests": counts["live"], "cached": counts["cached"],
                "failed": exc_type is not None or self.book in self.http._left_out,
                "rate_limited": self.book in self.http._rate_limited,
                "slowed": self.book in self.http._slowed,  # pushed back (429 or 403): one at a time since
                "seconds": round(self.http.clock() - self.started, 2),
            }
        return False
