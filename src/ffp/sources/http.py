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


class Http:
    """GETs JSON, reusing a cached answer for `ttl` seconds. Live requests go one at a time, and each host gets a
    pause of `delay` (a (low, high) range of seconds) after each of its requests ends."""

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
        status, body, *_ = self._paced_fetch(url)
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
            self._next_start[host] = self.clock() + random.uniform(*self.delay)
