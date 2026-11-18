# Each Book fetches on its own thread, several requests in flight, and is left out if it pushes back twice

A live run used to ask the Books one request at a time, pausing 3.5–5 s after each, so a 9-player lineup took about 4 minutes. Sprint 7 ports prizepicks-optimizer's answer on the same endpoints ([ADR 0001](0001-sportsbook-site-endpoints-for-live-odds.md); prizepicks' Sprints 6–7).

- **Threads per Book.** DraftKings and FanDuel fetch side by side, each through its own view of one shared, thread-safe `Http` (`for_book`).
- **Pacing per Book.** At most 3 requests in flight, starts at least 0.3 s apart (`BOOK_PACING`). After the league response, a Book's requests go out as one batch (`get_many`), answered in order.
- **Pushback.** A 429 or 403 slows the Book for the rest of the run: every request is held, including those already waiting their turn, and then one goes at a time. It is backed off once, however many requests were pushed back together. The request is retried once, after a 429's `Retry-After` (at most 10 s) or a pause. If the retry is refused too, the Book is **left out** for the run (`PushedBack`): nothing more is sent, and the other Book's odds still count.
- **Failures stay small.** Unlike prizepicks, a failed request in a batch costs only its own answer (one FanDuel event, one stat), since a run predicts a few named players rather than pricing a whole board.
- **Stopping.** Ctrl-C stops both Books at once. The workers are daemon threads (never a thread pool, whose workers are joined at exit), so a request still in flight is abandoned. `Http.stop()` ends every wait.

## Considered Options

- **asyncio:** rejected. Scrapling's fetch is synchronous, and threads keep the fake-fetch tests as they were.
- **More in flight, or no gap:** not tried. 3 and 0.3 s are proven on these endpoints (77 requests in 14 s, no rate limit), and a 9-player lineup's odds now take about 9 s.

## Consequences

- A fresh 9-player lineup runs in about 16 s instead of about 4 minutes (PLAN Sprint 7 notes). The rest of the run is nflverse and prediction, which Sprint 8 looks at.
- Every live run prints its odds timing and keeps it in the Snapshot, so a Book that starts pushing back shows up there first. If one does, lower its Pacing.
- Locks guard `Http`'s counters and Pacers, the Snapshot writer and the response callback. They are not test-driven, since thread races don't reproduce on demand.
