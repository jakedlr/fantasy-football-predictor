# Leaderboard live test: staleness and blocking of the free stats sources (S9.1)

Status: **method and tools ready; Results to be filled in after the Game.**

The Game: Thursday Night Football, NFL Week 11, **IND @ HOU**, ESPN event `401873066`, kickoff **2026-11-20 01:15 UTC** (Thu 8:15 PM ET). Event id and kickoff are from the ESPN scoreboard on 2026-11-19.

## Questions

1. **Staleness.** How many minutes after the Game moves do ESPN's summary box score and Sleeper's stats show it? Does Sleeper lag by its CDN cache (5–15 min was the guess in [leaderboard-stats-sources.md](leaderboard-stats-sources.md))?
2. **Blocking.** Do ESPN or Sleeper answer 403 or 429 to a Cloudflare Worker (shared datacenter IPs), compared with the home machine? If ESPN blocks Workers, the Hosting decision in PLAN.md is reopened before Sprint 10.
3. Things seen only in a live Game (open risk 4 of the sources research):
   - whether `drives.current` plays carry `statYardage` while Live;
   - the summary's `cache-control` and how often its box score changes;
   - whether Sleeper's stats rows or `/schedule` show the Game as in progress;
   - what the summary's `header.competitions[0].status` holds while Live (period and clock or not).

## Method

Two recorders poll the same three URLs once a minute from 30 minutes before kickoff until 30 minutes after the Game is Final:

| Source | URL |
|---|---|
| `espn-scoreboard` | `https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard` |
| `espn-summary` | `https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event=401873066` |
| `sleeper-stats` | `https://api.sleeper.com/stats/nfl/2026/11?season_type=regular&position[]=QB&…&position[]=DEF` |

Both send the User-Agent `ffp-live-spike/0.1 (non-commercial fantasy research; one request per source per minute)`. A 403 or 429 backs that source off for 1, 2, 4, ... polls (up to 16).

- **Home** (vantage `home`): `scripts/record_live.py` on the user's machine.
  - It saves every body gzipped at `snapshots/live-spike/<source>/<UTC time>.json.gz`.
  - It writes one line per request to `snapshots/live-spike/manifest-home.jsonl`: time, URL, status, latency, bytes, game state/period/clock, score, last play id, play count, Sleeper rows and `last_modified`, cache headers (`cache-control`, `age`, `cf-cache-status`, `retry-after`), and a hash of the player stats.
  - The player-stats hash covers: the scoreboard's `leaders` for the Game; the summary's `boxscore.players`; Sleeper's `(player_id, stats)` pairs.
- **Worker** (vantage `worker`): `spike/worker-recorder/`, a throwaway Cloudflare Worker on a one-minute cron.
  - It writes the same manifest lines to KV, without bodies. To stay under the 10 ms CPU limit it hashes the raw box-score text and Sleeper's raw text, so its hashes are not comparable with home's.
  - `/dump` returns its lines as JSONL.

`scripts/analyze_live_spike.py` reads both manifests and reports, per source and vantage:

- **Statuses:** counts of each HTTP status, network errors and polls skipped while backing off; median latency.
- **Game progress:** the first sighting, from either vantage, of a new (period, clock) on the scoreboard while the Game is Live, or of a new play count in the summary.
- **Lag:** for each game progress event, the minutes until that stream's player-stats hash next changes (a change up to 30 s earlier, in the same poll, counts as 0). Median, 90th percentile and max. Plays that change no stat (penalties, kneels, timeouts) inflate the tail, so read the median. With one-minute polls, the resolution is about 1 minute.
- **No change:** progress events with no later stats change.
- **Settle:** minutes from the first Final sighting to the stream's last stats change (late corrections).

Not covered: the ESPN Fantasy API (`lm-api-reads`), the second fallback. Add it to the recorders if Sleeper turns out to be unusable.

## Smoke run (2026-11-19 08:06 UTC, no Game live)

Three polls 20 s apart from the home machine (WSL2), `scripts/record_live.py --interval 20 --max-minutes 1`:

| Source | Statuses | Latency | Size | Headers |
|---|---|---|---|---|
| `espn-scoreboard` | 200 ×3 | ~155 ms | 236 KB | `cache-control: max-age=2` |
| `espn-summary` | 200 ×3 | ~183 ms | 111 KB (pre-game) | `cache-control: max-age=6` |
| `sleeper-stats` | 200 ×3 | ~100 ms | 2 B (`[]`, no Week 11 stats yet) | `cache-control: public, s-maxage=4, stale-while-revalidate=600`; `cf-cache-status: UPDATING`, `age: 36` |

Sleeper's `s-maxage` was 4 s, not the 300 s seen for Week 10's stats on 2026-11-18. Its edge cache may be short while a week is current. `stale-while-revalidate=600` could still let it serve answers up to 10 minutes old.

The Worker ran locally once (`wrangler dev --test-scheduled`, which egresses from this machine, not Cloudflare). All three sources answered 200. It has not been deployed: that needs the Cloudflare account (S9.2, or the manual steps in `spike/worker-recorder/README.md`).

## How to run it on Thursday

Times are UTC. 00:45 UTC Friday is 7:45 PM ET Thursday.

1. **Worker, any time before 00:45 UTC:** follow "Deploy" in [spike/worker-recorder/README.md](../spike/worker-recorder/README.md): `npm install`, `npx wrangler login`, `npx wrangler kv namespace create ffp-live-spike`, paste the id into `wrangler.toml`, then `npx wrangler deploy`.
2. **Home machine:** keep it awake (WSL stops when Windows sleeps). From a checkout of this branch, start the recorder in tmux or with nohup. It waits until the start time:

   ```bash
   mkdir -p snapshots/live-spike
   nohup python3 scripts/record_live.py --event 401873066 --start 2026-11-20T00:45:00Z \
       --after-final 30 --max-minutes 330 > snapshots/live-spike.log 2>&1 &
   tail -f snapshots/live-spike.log
   ```

   It logs one line a minute with each source's status and the game clock. It stops 30 minutes after the scoreboard shows Final, or at 06:15 UTC at the latest.
3. **During the Game:** glance at `tail -f snapshots/live-spike.log` and at `curl -s https://ffp-live-spike.<subdomain>.workers.dev/dump | tail -3`.
4. **After the Game:**

   ```bash
   curl -s https://ffp-live-spike.<subdomain>.workers.dev/dump > snapshots/live-spike/manifest-worker.jsonl
   python3 scripts/analyze_live_spike.py snapshots/live-spike
   ```

   Then remove the Worker ("Remove" in its README) and fill in the Results below.

## Results

### Blocking (status codes per source and vantage)

_To fill in: the analyzer's statuses column for home and worker, any Retry-After seen, when blocks started._

### Staleness (lag behind the game clock)

_To fill in: lag median / p90 / max and "no change" per source and vantage; settle after Final._

### Live behaviour notes

_To fill in: `drives.current` `statYardage`; summary header status while Live; Sleeper rows or schedule while Live; cache headers seen while Live._

### Fixtures

_To fill in: which saved bodies were trimmed into test fixtures for Sprint 10, and where they live._

## Conclusion

_To fill in: does the Hosting decision (Worker polls ESPN, Sleeper as fallback) stand?_
