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
- **The Hosting decision doesn't stand as written:** a free Worker can't parse every Live Game's ESPN summary within 10 ms of CPU. It needs either the Workers Paid plan, or Sleeper's single response as the live stats source. The user chose Sleeper on the free plan (2026-11-20; no money is spent on this project): see ADR 0006. A full Sunday is recorded on 2026-11-22 to measure its size and CPU.

## Full-week CPU on Cloudflare (S10.1, 2026-11-20)

A throwaway Worker (`ffp-cpu-probe`, deleted afterwards) fetched Sleeper's completed Week 10 (all six positions: 693 KB, 694 rows, the size of a full Sunday), ESPN's current scoreboard (236 KB, 13 Games), or both. It parsed them and summed a rough PPR line per row. CPU time per request, from `wrangler tail`:

| Run | CPU (ms) | Wall (ms) |
|---|---|---|
| Sleeper only (3 runs) | 4, 5, 5 | 11–13 |
| Scoreboard only (3 runs) | 2, 2, 3 | 30–37 |
| Both in one run (10 runs) | 6, 6, 10, 10, 12, 12, 13, 13, 18, 20 | 30–137 |

- **Both sources in one run don't fit 10 ms reliably:** the median is 12 ms and the worst is 20 ms. Every run succeeded, because Cloudflare doesn't cut a Worker off exactly at 10 ms, but that slack isn't promised.
- **Each source on its own fits,** with room for scoring: Sleeper 4–5 ms, the scoreboard 2–3 ms.
- **So, applying ADR 0006's free answer:**
  - The scoreboard gets its own cron trigger and KV key, written only when Game Statuses, scores or the clock change.
  - The stats run reads that small key instead of ESPN's 236 KB.
- **Still to do:**
  - Sunday's recording (`snapshots/live-sunday/`) re-checks this with a live mid-slate response.
  - The joined Worker's own CPU (scoring under four rule sets, building the Leaderboard JSON) is measured when it's built.

## The deployed cron Worker (2026-11-20, after Sprint 10 went live)

Measured with `wrangler tail --config site/worker/wrangler.toml` over 10:22–10:33 UTC. It was Thursday after the week's first Game, so Sleeper listed 46 players:

| Cron | Runs | CPU (ms) | Wall (ms) |
|---|---|---|---|
| Stats (`*/5`) | 2 (one "unchanged", one "written") | 3, 3 | 395–620 |
| Scoreboard (`2-59/5`) | 2 (both "unchanged") | 3, 4 | 183–205 |

A full Sunday has about 15 times as many rows. The S10.1 probe put a full week's Sleeper parse at 4–5 ms, so the stats run should land around 7–9 ms or more: scoring under four Scoring Rules and building the Leaderboard JSON also grow with rows. That is close to the 10 ms limit. Check it with `wrangler tail` during Sunday's Games.

## K and D/ST from Sleeper (S11.1, 2026-11-20)

Sleeper's completed 2026 Week 10 for K and DEF (63 rows: 35 K, 28 DEF; trimmed to `site/test/fixtures/sleeper/stats-week10-k-dst.json`) and its season-to-date totals were checked against Sleeper's own `pts_std` and against nflverse play-by-play as ffp builds D/ST lines (`scripts/build_params.py dst_games`, saved beside the fixture as `nflverse-week10-dst.json`). The scripts were throwaway; the checks that matter are tests in `site/test/worker/sleeper-k-dst.test.ts`.

**Kicker** (`sleeper.ts` `SLEEPER_KICKER_KEYS`):

| Sleeper | ffp | Evidence |
|---|---|---|
| `fgm_0_19`, `fgm_20_29`, `fgm_30_39`, `fgm_40_49`, `fgm_50_59`, `fgm_60p` | the same bucket keys (`scoreKicker` scores each at its bucket's min) | they sum to `fgm` on every row; nflverse FG distances agree for 28 of 28 teams |
| `xpm` | `pat_made` | nflverse 28 of 28 |
| `xpmiss` | `pat_missed` | equals `xpa − xpm` on every row; nflverse 28 of 28 |
| `fgmiss` | `fg_missed` | equals `fga − fgm` on every row (a blocked FG is a miss) |

Not read: `fgm`, `fgm_yds`, `fgm_lng`, `fgm_50p`, `fgmiss_*`, `fga`, `xpa`. Sleeper's own `pts_std` (ppr's kicker tiers, 3/4/5, plus −1 per PAT missed) matches 27 of 28 Week 10 kickers; the other was given 1 point for a forced fumble on a return (`st_ff`), which ffp has no kicker stat for. Over the season, the 4 kickers with a 60+ FG score 1 point less in Sleeper's `pts_std` than under ffp's `sleeper` preset, which gives 60+ yards 6 points: Sleeper's stats endpoint scores 50+ as 5.

**D/ST** (`SLEEPER_DST_KEYS` and `dstStatLine`):

| Sleeper | ffp | Evidence |
|---|---|---|
| `sack` | `sacks` | nflverse 28 of 28 |
| `int` | `interceptions` | 28 of 28 |
| `fum_rec` + `def_st_fum_rec` | `fumble_recoveries` | `fum_rec` leaves out special-teams recoveries; with them 27 of 28 (alone 26) |
| `ff` + `def_st_ff` | `forced_fumbles` | likewise; with them 25 of 28 (alone 21) |
| `safe` | `safeties` | 28 of 28; season 30 of 30 |
| `blk_kick` | `blocked_kicks` | 28 of 28; season 30 of 30 |
| `def_td` | `interception_return_tds` (both kinds) | equals nflverse INT + fumble return TDs, 28 of 28; season 30 of 30 |
| `def_st_td` | `kickoff_return_tds` (all three kinds) | equals kickoff + punt + blocked-kick return TDs, 28 of 28; season 30 of 30 |
| `yds_allow` | `yards_allowed` | net of sack yards, as ffp's: 28 of 28; season 30 of 30 |
| `pts_allow` + 6 × the opponent's `def_td` | `points_allowed_all` | see below: 28 of 28 |
| 6 × the opponent's `def_td` | `opponent_defensive_tds_points` | nflverse also counts the try |
| 6 × the opponent's `def_st_td` | `opponent_return_tds_points` | likewise |

- **`pts_allow` is the opponent's points less 6 per INT or fumble return TD it scored.** The try after the TD stays in (Miami's pick-six with its PAT: 24 scored, `pts_allow` 18). That matched 28 of 28 Week 10 teams and 27 of 29 season totals (two teams 2 points off, not traced). Return TDs stay in. So `points_allowed_all` adds back 6 per defensive TD from the opponent's own DEF row (`opponent`), and every Leaderboard option, which leaves defensive TDs out, gets exactly Sleeper's Points Allowed.
- **Gaps.** Sleeper doesn't split INT from fumble return TDs, or kickoff from punt and blocked-kick return TDs. All four Leaderboard options score every kind 6, so the points are exact; a test fails if a rules change breaks that. The try after an opponent's defensive or return TD can't be separated, so a rule leaving those TDs out leaves out 6 per TD where ffp's nflverse lines leave out 7 or 8. Three forced fumbles and one recovery in Week 10 are credited differently by Sleeper's data provider and nflverse (LV, NYJ, SF).
- **Sleeper's own `pts_std`** (ffp's `sleeper` D/ST preset, with Points Allowed tiered on `pts_allow`) matches 27 of 27 Week 10 D/STs that have one, and 32 of 32 season totals, once a special-teams recovery is scored 1 (Sleeper's rule) instead of ffp's 2. `misc_td`, `def_2pt`, `fum_rec_ez_tds` and `td` are not in it and aren't read.
- **Under the four Leaderboard options,** the 25 Week 10 D/STs without a credit dispute score the same from Sleeper's lines as from nflverse's.
- **CPU.** On Node (warm, this laptop), converting a 707-row, 635 KB response (14 copies of the live Game's 46 offense rows plus the 63 real K and DEF rows) takes 0.14 ms, of which the K and DEF rows are about 0.02 ms; `JSON.parse` of the same text takes 1.5 ms.

## ESPN projections (S11.5, 2026-11-20)

The fallback for players without `ffp` Expected Points: ESPN's weekly fantasy projections (research/leaderboard-projections.md B3), measured on Friday of Week 11 (13 Games, 6 teams on bye, Thursday's Game Final).

**Request.** `GET https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId=11`, no cookie, with an `x-fantasy-filter` header: `filterSlotIds` (QB 0, RB 2, WR 4, TE 6, D/ST 16, K 17), `filterStatsForTopScoringPeriodIds` `{value: 1, additionalValue: ["11202611"]}` (so each player carries only this week's projected line and his last actual line), `sortAppliedStatTotal` on `11202611` descending, and `limit`. `leaguedefaults/3` is "FFL PPR Scoring", so each line's `appliedTotal` is ESPN's PPR points.

**Size.** Every player in the response carries prose and ranks the Worker doesn't use: `outlooks` (every week's write-up so far, so it grows through the season), `seasonOutlook`, `draftRanksByRankType` and `rankings`. The `filterRanksFor*` options cut `rankings` from 15 KB a player to 10 bytes; nothing found drops `outlooks`. The views `players_wl` and the `/players` route have no stats; `kona_playercard` is bigger; other view names returned only league settings.

| Request | Bytes | Players | Projected > 0 | Projected ≥ 1 |
|---|---|---|---|---|
| All six positions, limit 600, ranks unfiltered | 7.3 MB | 600 | | |
| All six positions, limit 600 | 2.9 MB | 600 | 300 | 300 |
| QB + TE, limit 140 | 703 KB | 140 | 95 | 86 |
| RB + K + D/ST, limit 170 | 867 KB | 170 | 118 | 111 |
| WR, limit 160 | 886 KB | 160 | 108 | 103 |

About 6.5 KB per projected player, against Sleeper's 0.9 KB. A week with no byes has 32 teams, about 23% more players than Week 11's 26; the limits leave room for that.

**CPU** (Node 24 on this laptop, warm, median of 60–200 runs; `JSON.parse` of the text, then the Worker's own conversion, merge and serialization, as `runEspn` does them):

| Run | JSON.parse | + convert | Whole run |
|---|---|---|---|
| All six positions in one response (2.9 MB) | 4.7–10 ms | | |
| QB + TE (703 KB) | 1.0–1.1 ms | 1.3 ms | 1.8–2.1 ms |
| RB + K + D/ST (867 KB) | 1.2–1.3 ms | 1.9 ms | 2.7 ms |
| WR (886 KB, store then 321 players, 110 KB) | 1.2–1.4 ms | 1.6 ms | 2.8–2.9 ms |

Sleeper's 46 KB fixture parses at 2.3 ms/MB on the same machine; ESPN's prose-heavy text at 1.2–1.6 ms/MB. On Cloudflare the deployed runs used 3–4 ms on about 44 KB of Sleeper data, and the S10.1 probe 4–5 ms on 693 KB, so Cloudflare costs roughly 2–3 times this laptop's warm Node plus a fixed 1–2 ms. That puts the one-response design at 10–25 ms (doesn't fit) and each group at roughly 4–7 ms.

**Chosen:** three groups balanced by bytes, one request per run, on the cron `9,29,49 * * * *` (minutes free of the other two runs): QB + TE at :09, RB + K + D/ST at :29, WR at :49, so each group refreshes hourly. Projections change slowly and freeze at kickoff, so hourly is enough. WR is the largest group and can't be split further by position; if `wrangler tail` shows it near 10 ms, lower its limit (about 130 covers a no-bye week's WRs projected for 1 point or more) or page WRs by offset.

**Stat ids.** From espn-api's `PLAYER_STATS_MAP` (B3), checked against `appliedTotal` for all 600 players of the 2.9 MB response:

| Leaderboard stat | ESPN id | Note |
|---|---|---|
| passing_yards, passing_tds, passing_interceptions, passing_2pt | 3, 4, 20, 19 | |
| rushing_yards, rushing_tds, rushing_2pt | 24, 25, 26 | 40 is sometimes 2 × 24 or 0 |
| receptions, receiving_yards, receiving_tds, receiving_2pt | 53, 42, 43, 44 | 41 is absent; 61 is sometimes 2 × 42 or 0 |
| fumbles_lost | 72 | |
| pat_made, pat_missed, fg_missed | 86, 88, 85 | ESPN's default doesn't score a missed PAT; ours doesn't either |
| fgm_30_39 | 80 (FG made under 40) | spans Sleeper's 0–19, 20–29 and 30–39; every Scoring Rules option scores 0–39 as one tier (a test checks) |
| fgm_40_49 | 77 | |
| fgm_50_59, fgm_60p | 74 − 201, 201 | 74 is 50+ including 60+ (83 = 80 + 77 + 74); 201 (60+) wasn't given in Week 11, so 60+ FGs count as 50–59, which `espn-league` scores 1 point lower |
| sacks, interceptions, fumble_recoveries, forced_fumbles, safeties, blocked_kicks | 99, 95, 96, 106, 98, 97 | |
| interception_return_tds, fumble_return_tds, kickoff_return_tds, punt_return_tds, blocked_kick_return_tds | 103, 104, 101, 102, 93 | 94 = 103 + 104; 105 = all D/ST TDs |
| points_allowed_all, yards_allowed | 120, 127 | |

- **Offense:** ESPN's PPR rules equal our `ppr` on these stats. Our score matches `appliedTotal` for 458 of 530 players within 0.01; the other 72 differ by exactly 6 × (101 + 102 + 63), offensive return and fumble-recovery TDs, which aren't `ffp` Scoring Stats (largest left over: 0.08, Travis Hunter's defensive stats).
- **K and D/ST:** exact for every player once D/ST tiers are scored as below.
- **D/ST tiers.** ESPN gives the probability of each Points Allowed band (89–92, 121–125: 0, 1–6, 7–13, 14–17, 18–21, 22–27, 28–34, 35–45, 46+) and Yards Allowed band (128–136), and `appliedTotal` is Σ P(band) × the band's points. Every band lies inside one of our tiers in all four options, so Expected Points use the same sum. Scoring the tier of the mean instead (what `scoreLine` does with `statMeans`) overstates a D/ST: the Steelers' mean of 12.5 points allowed is in the 3-point tier, but the expected tier points are 0.9; with yards allowed the gap is 4.5 points. `statMeans` still carry the means, for the Projected Total.

**Joins and freezing.** Thursday's teams (IND, HOU) kept their pregame projections the day after their Game, as B3 found. Players projected for 0 (Out, injured reserve, bye) are left out. The run keeps a stored entry whose Game has kicked off, and takes the fresh one only when nothing is stored.

**Postseason.** ESPN's fantasy game ends with the regular season, so the run does nothing in the postseason; players without a push have no Expected Points then.

**Fixtures:** `site/test/fixtures/espn/projections-{qb-te,rb-k-dst,wr}.json`, the three group responses trimmed to 8–19 players each (the top 4 per position, the Thursday Game's teams, an Out player, a bye), with `outlooks`, `seasonOutlook`, `rankings` and `draftRanksByRankType` removed.
