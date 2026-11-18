"""Shared test helpers."""

import json
import tempfile


def over_http(fake):
    """A real ffp Http (Book views, Pacing, batches) serving `fake.get_json(url)`; a FetchError from the
    fake answers HTTP 500. No pauses, no cache reuse: production code needs no test-only path."""
    from ffp.sources.http import BOOK_PACING, FetchError, Http, Pacing

    def fetch(url):
        try:
            return 200, json.dumps(fake.get_json(url)).encode(), {}
        except FetchError:
            return 500, b"", {}

    no_gap = {book: Pacing(p.in_flight, 0.0) for book, p in BOOK_PACING.items()}  # same in flight, no wait
    return Http(tempfile.mkdtemp(prefix="ffp-http-"), fetch=fetch, use_cache=False, delay=(0, 0),
                book_pacing=no_gap)
