"""Polite, cached JSON fetching via Scrapling.

Copied from prizepicks-optimizer's ppopt/sources/http.py and trimmed to what a
one-shot CLI run needs. The low-level fetch is passed in, so tests never touch
the network (PLAN "Stack").
"""

import hashlib
import json
import os
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CACHE_DIR = Path(".cache") / "http"  # gitignored
REUSE_SECONDS = 60  # how long a response is reused: lines move fast
DELAY = (3.5, 5.0)  # seconds paused after each live request to a host (research/sportsbook-markets.md paced 3.5 s)


class FetchError(RuntimeError):
    """A request failed, was blocked, or did not return JSON."""


def scrapling_fetch(url: str) -> tuple[int, bytes, dict]:
    """Fetch over TLS-impersonating HTTP with Scrapling's Fetcher. Returns status, body and response headers."""
    from scrapling.fetchers import Fetcher

    page = Fetcher.get(url, impersonate="chrome", headers={"Accept": "application/json"}, timeout=30)
    return page.status, page.body, dict(page.headers or {})

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


class Http:
    """GETs JSON, reusing a cached answer for `ttl` seconds. Live requests go one at a time, and each host gets a
    pause of `delay` (a (low, high) range of seconds) after each of its requests ends. A host that pushes back
    (429 or 403) is waited out (a 429's Retry-After, else one pause), slowed, and asked once more."""

    def __init__(
        self,
        cache_dir: str | Path = DEFAULT_CACHE_DIR,
        ttl: float = REUSE_SECONDS,
        use_cache: bool = True,
        delay: tuple[float, float] = DELAY,
        fetch=scrapling_fetch,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.cache_dir = Path(cache_dir)
        self.ttl = ttl
        self.use_cache = use_cache
        self.fetch = fetch
        self.delay = delay
        self.sleep = sleep
        self.clock = clock
        self._live_requests = 0
        self._next_start: dict[str, float] = {}  # {host: earliest clock() its next request may start}
        self._slowed: set[str] = set()  # hosts that pushed back during this run

    @property
    def live_requests(self) -> int:
        """Requests made live so far (fetched, not answered from the cache)."""
        return self._live_requests

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".json")

    def get_json(self, url: str):
        """The JSON at `url`, from the cache while it's fresh, else fetched live and cached."""
        path = self._cache_path(url)
        if self.use_cache and path.exists() and time.time() - path.stat().st_mtime < self.ttl:
            return json.loads(path.read_text())

        body = self._fetch_live(url)
        try:
            data = json.loads(body)
        except ValueError as e:
            raise FetchError(f"non-JSON response from {url}") from e
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Write aside, then rename: another run reading this response never sees half of it.
        tmp = path.with_name(f"{path.stem}.{os.getpid()}.tmp")
        try:
            tmp.write_bytes(body)
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return data

    def _fetch_live(self, url: str) -> bytes:
        """The body of a 200 answer at `url`, fetched live."""
        status, body, *rest = self._paced_fetch(url)
        if status in PUSHED_BACK:
            status, body = self._back_off(url, status, rest[0] if rest else {})
        if status != 200:
            raise FetchError(f"HTTP {status} from {url}")
        return body if isinstance(body, bytes) else body.encode()

    def _paced_fetch(self, url: str) -> tuple:
        """One live request, started no sooner than its host's pause allows."""
        host = urlsplit(url).netloc
        wait = self._next_start.get(host, float("-inf")) - self.clock()
        if wait > 0:
            self.sleep(wait)
        self._live_requests += 1
        try:
            return self.fetch(url)
        except Exception as e:
            raise FetchError(f"request to {url} failed: {e}") from e
        finally:
            self._next_start[host] = self.clock() + self._pause(host)

    def _pause(self, host: str) -> float:
        return random.uniform(*self.delay) * (SLOWED if host in self._slowed else 1)

    def _back_off(self, url: str, status: int, headers: dict) -> tuple[int, bytes]:
        """The host pushed back: hold it for the wait a 429 asked for (or one pause), slow it for the rest of the
        run, then retry this request once. Only a 429 is a rate limit."""
        host = urlsplit(url).netloc
        wait = retry_after(headers) if status == RATE_LIMITED else None
        if wait is not None and wait > MAX_RETRY_AFTER:
            self._slowed.add(host)
            raise FetchError(f"rate-limited: HTTP {status} from {url}, asked to wait {wait:g}s")
        hold = self.clock() + max(wait or 0.0, self._pause(host))
        self._next_start[host] = max(self._next_start.get(host, hold), hold)
        self._slowed.add(host)
        retry, body, *_ = self._paced_fetch(url)
        if retry == RATE_LIMITED:
            raise FetchError(f"rate-limited: HTTP {retry} from {url}, again after backing off")
        if retry == REFUSED:
            raise FetchError(f"refused: HTTP {retry} from {url} (blocked, or off a US connection)")
        return retry, body  # anything else is judged like a first answer
