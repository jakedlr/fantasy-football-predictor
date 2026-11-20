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
- **Worker** (vantage `worker`): `spike/worker-recorder/`, a throwaway Cloudflare Worker on a one-minute cron. Removed after the Game; it is in the history at 3a1071f.
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

## Worker pre-game check (2026-11-19 09:21 UTC, no Game live)

The recorder Worker was deployed to `ffp-live-spike.<account>.workers.dev` and given a short recording window to check it from Cloudflare's network before the Game:

| Source | Status | Latency | Size | Cache headers |
|---|---|---|---|---|
| `espn-scoreboard` | 200 | 43 ms | 237 KB | `max-age=8` |
| `espn-summary` | 200 | 71 ms | 111 KB | `max-age=3` |
| `sleeper-stats` | 200 | 11 ms | `[]` | `s-maxage=4, stale-while-revalidate=600` |

- **No blocking from Workers' IPs before the Game.** Whether that holds under game-day load is what Thursday's run shows.
- **CPU: 7 ms of the free plan's 10 ms** for that run, which parses only the 237 KB scoreboard. The Leaderboard Worker will parse the scoreboard plus one summary per Live Game, so Sprint 10 must measure CPU per run and keep parsing lean (or split a refresh across runs); this is the tightest limit found so far.
- The first window (09:15–09:18 UTC) recorded nothing: a new Worker's cron did not fire for its first few minutes. It fired every minute after that.
- The check's one record was deleted before the Game window.

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

Thursday Night Football, Week 11, IND @ HOU (ESPN event 401873066), recorded 2026-11-20 00:45–05:45 UTC: 233 polls from the home machine (it stopped 30 minutes after Final), 300 from the Worker (every minute of its window). Final first seen 04:06:59 UTC.

| Source | Vantage | Requests | Statuses | Median latency | Lag median / p90 / max | Settle after Final |
|---|---|---|---|---|---|---|
| ESPN scoreboard | home | 233 | 200 ×233 | 186 ms | 1.2 / 7.2 / 19.2 min | 0 min |
| ESPN scoreboard | Worker | 300 | 200 ×298, 403 ×2 | 62 ms | 1.0 / 7.0 / 19.0 min | 0 min |
| ESPN summary | home | 233 | 200 ×233 | 193 ms | 0.2 / 0.2 / 2.2 min | 24.0 min |
| ESPN summary | Worker | 300 | 200 ×298, 403 ×2 | 42 ms | 0.0 / 0.0 / 2.0 min | 23.8 min |
| Sleeper stats | home | 233 | 200 ×233 | 102 ms | 0.2 / 1.0 / 3.2 min | 6.0 min |
| Sleeper stats | Worker | 300 | 200 ×300 | 10 ms | 0.0 / 0.0 / 1.0 min | 94.8 min* |

Lag: minutes from game progress (a new clock or play count) to the stream's next stats change. The scoreboard's "stats" are only its leaders, so its lag doesn't matter; the summary and Sleeper are the stats sources.

\* The Worker hashed Sleeper's whole body, which kept changing after Final without the stats changing (home hashed the parsed rows: 6 min).

### Blocking

- **From the home machine: none.**
- **From the Worker: two single minutes** (02:06:46 and 04:08:46 UTC) when both ESPN endpoints answered 403 (a ~440-byte body, no `Retry-After`) and Sleeper answered 200. The next minute was 200 both times. It looks like ESPN's bot protection briefly refusing one Cloudflare egress IP, not a block. Sleeper was never refused.

### Staleness

- **ESPN's summary is live:** its box score changed within the same minute as the game clock (median 0.0–0.2 min, max about 2 min).
- **Sleeper is live too:** median 0–0.2 min, p90 1 min. The research's 5–15 minute CDN lag did not show; its cache header was `s-maxage=4, stale-while-revalidate=600` throughout.
- **ESPN kept correcting stats for about 24 minutes after Final;** Sleeper for about 6. Final Games must keep refreshing for at least 30 minutes, and once more the next day.

### Live behaviour notes

- The summary's `header.competitions[0].status` is `in` with `displayClock` and `period` while Live; `drives.current` holds the drive in progress, with `statYardage` per play.
- FGs: plays type 59 (good) and 60 (missed) carry the distance in `statYardage`. **A blocked FG is type 18 ("Blocked Field Goal") with `statYardage` 0;** its distance is only in the text ("62 yard field goal is BLOCKED").
- The box score has no 2-pt conversions; safeties and blocked kicks are play types ("Safety", "Blocked Field Goal").
- Sleeper's rows carry the player (name, position, team), and K and D/ST stats ready-made: `fgm_20_29`-style 10-yard buckets, `xpm`, `fga`; `sack`, `int`, `safe`, `blk_kick`, `yds_allow`, `pts_allow`. Its buckets break at 40, 50 and 60, the same edges as every Leaderboard Scoring Rules option.
- **Sizes:** the scoreboard is about 230 KB; one Game's summary grows from 120 KB before kickoff to **559 KB** at Final; Sleeper is about 0.9 KB per player row (51 rows for this Game).

### CPU

- The Worker's run parsed only the 230 KB scoreboard (plus hashing) and used **7 ms of the free plan's 10 ms** (pre-game check). It never failed a minute during the Game.
- `JSON.parse` on this laptop (Node, warm): 559 KB summary 1.4 ms, 222 KB scoreboard 0.6 ms, 44 KB Sleeper 0.1 ms. A Sunday afternoon has up to 9 Live Games at once, so ESPN summaries alone would be about 5 MB per run, several times the free CPU limit once a Worker's slower, colder parse is counted.

### Fixtures

Trimmed (news, odds, images and the like removed) into `site/test/fixtures/` for Sprint 10:

- `espn/scoreboard-upcoming.json`, `espn/scoreboard-live.json` (Q3 14:12), `espn/scoreboard-final.json`
- `espn/summary-live.json` (Q3 14:12), `espn/summary-final.json` (with the made, missed and blocked FGs and the safety)
- `sleeper/stats-live.json` (Q3 14:12)

The full recordings stay in `snapshots/live-spike/` (gitignored).

## Conclusion

- **Workers can reach both sources.** ESPN refused the Worker twice for one minute each, so the Leaderboard Worker must keep the last good data through a refused minute and alert only after 3 failures in a row, as planned.
- **Both sources are fresh enough for a 5-minute refresh.**
- **The Hosting decision doesn't stand as written:** a free Worker can't parse every Live Game's ESPN summary within 10 ms of CPU. It needs either the Workers Paid plan, or Sleeper's single response as the live stats source. That choice is put to the user before Sprint 10.
