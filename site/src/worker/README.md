# Worker (Sprint 10)

The Leaderboard's cron Worker (ADR 0006), `ffp-leaderboard-cron`, configured in `site/worker/wrangler.toml`. Each module has a Vitest suite in `site/test/worker/`.

Pure modules, no fetch, KV or clock:

- `scoreboard.ts`: ESPN scoreboard → NFL Week, its dates, and each Game with its Game Status.
- `sleeper.ts`: Sleeper weekly stats rows → `ffp.scoring` stat lines: QB/RB/WR/TE; kickers by Sleeper's FG distance buckets; D/ST, with Points Allowed rebuilt from Sleeper's `pts_allow` and the opponent's DEF row (the key map and its checks are in research/leaderboard-live-spike.md, "K and D/ST from Sleeper").
- `scoring.ts`: the TypeScript port of `ffp.scoring`, held to `site/contracts/` (ADR 0005).
- `leaderboard.ts`: scoreboard + Sleeper players → the Leaderboard JSON (`site/schema/leaderboard.schema.json`). Players join their Game by team; a team with no Game (bye) is left out. Points So Far under every Scoring Rules option, to 2 decimals. Until Sprint 11 adds Expected Points, Week Points are Points So Far for Live and Final Games, and 0 (Points So Far null) for Upcoming Games; `expected` and `expectedSource` are null.

The joins, with fetch, KV and the clock passed in:

- `kv.ts`: the KV slice, the binding (`LEADERBOARD`), the keys, and how the stored Leaderboard text begins. The Pages Function uses it too.
- `refresh.ts`: the two runs. Each fetches one source (S10.1: both in one run don't fit 10 ms of CPU), with the User-Agent `ffp-leaderboard/0.1 (non-commercial; …)` and a 10 s timeout. A failed run logs and writes nothing, so the last good data stays.
  - **Scoreboard run** (`2-59/5 * * * *`): ESPN's scoreboard → KV `scoreboard`, written only when it changed.
  - **Stats run** (`*/5 * * * *`): KV `scoreboard` + Sleeper's weekly stats (`season_type=post` in the postseason) → KV `leaderboard`. Written when anything but `updatedAt` changed, with a new `updatedAt`; an unchanged Leaderboard is rewritten once its `updatedAt` is 10 minutes old, so the page's "Updated … ago" means the last successful refresh.
  - KV writes: at most 288 + 288 = 576 a day (both keys change every run while a Game is Live), against the free plan's 1,000. The arithmetic is in `refresh.ts`.
- `index.ts`: the Worker's `scheduled` handler, dispatching on `controller.cron`. A failed run throws after keeping the last good data, so Cloudflare's cron log shows it.

`site/functions/api/leaderboard.ts` serves KV `leaderboard` at `/api/leaderboard`: `Cache-Control: public, max-age=60`, an ETag (the Leaderboard's `updatedAt`), 304 on `If-None-Match`, and 503 with a small JSON error before the Worker's first write. `site/functions/api/expected-points.ts` takes the signed Expected Points push and merges it into KV `expected` and `idmap` (root README); its pure parts are in `site/src/push/`.

Tests run in plain Vitest with a Map-backed KV and recorded responses (`site/test/helpers/fakes.ts`). `@cloudflare/vitest-pool-workers` needs vitest 4 and the site pins vitest 5, so it isn't used. Deploying is `npm run deploy:worker` and `npm run deploy:pages` (root README, "Deploy").

CPU: on Node, the stats run on a full week of Sleeper rows (693 KB, about 500 listed players) takes about 6–8 ms, of which parsing Sleeper's JSON is 4–5 ms; converting, scoring four rule sets, serializing and comparing add about 2.5 ms. Sunday's live recording re-checks it on Cloudflare.
