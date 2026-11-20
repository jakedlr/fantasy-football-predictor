# Worker (Sprints 10–11)

The Leaderboard's cron Worker (ADR 0006), `ffp-leaderboard-cron`, configured in `site/worker/wrangler.toml`. Each module has a Vitest suite in `site/test/worker/`.

Pure modules, no fetch, KV or clock:

- `scoreboard.ts`: ESPN scoreboard → NFL Week, its dates, and each Game with its Game Status.
- `sleeper.ts`: Sleeper weekly stats rows → `ffp.scoring` stat lines (QB/RB/WR/TE in Sprint 10; K and D/ST in Sprint 11).
- `scoring.ts`: the TypeScript port of `ffp.scoring`, held to `site/contracts/` (ADR 0005).
- `leaderboard.ts`: scoreboard + Sleeper players + KV `expected` and `espn` → the Leaderboard JSON (`site/schema/leaderboard.schema.json`). Players are the union of Sleeper's rows and the expectations, joined to their Game by team; a team with no Game (bye) is left out. A player is listed once he has recorded a stat (a non-empty Sleeper stat line) or has Expected Points ≥ 1 under PPR. Expected Points come from `expected` (expectedSource `ffp`), else `espn` (`espn`); a store for another NFL Week is ignored. Week Points: Expected Points when Upcoming; when Live, the Projected Total (each Scoring Stat's current value + pregame mean × `fractionLeft`, then scored, so D/ST tiers and kick distances stay right), or Points So Far without an expectation; Points So Far when Final. `fractionLeft` is (clock seconds + 900 × quarters still to start) / 3600: halftime 0.5, overtime 0. Every option, to 2 decimals. `out` stays false: nothing in the inputs says a player was ruled out.

The joins, with fetch, KV and the clock passed in:

- `kv.ts`: the KV slice, the binding (`LEADERBOARD`), the keys, and how the stored Leaderboard text begins. The Pages Function uses it too.
- `refresh.ts`: the two runs. Each fetches one source (S10.1: both in one run don't fit 10 ms of CPU), with the User-Agent `ffp-leaderboard/0.1 (non-commercial; …)` and a 10 s timeout. A failed run logs and writes nothing, so the last good data stays.
  - **Scoreboard run** (`2-59/5 * * * *`): ESPN's scoreboard → KV `scoreboard`, written only when it changed.
  - **Stats run** (`*/5 * * * *`): KV `scoreboard`, `expected` and `espn` + Sleeper's weekly stats (`season_type=post` in the postseason) → KV `leaderboard`. A store that isn't the shape `kv.ts` describes is ignored with a log, a malformed player entry dropped with a log; neither fails the run. An unchanged store is parsed once per isolate. Written when anything but `updatedAt` changed, with a new `updatedAt`; an unchanged Leaderboard is rewritten once its `updatedAt` is 10 minutes old, so the page's "Updated … ago" means the last successful refresh.
  - KV writes: at most 288 + 288 = 576 a day (both keys change every run while a Game is Live), against the free plan's 1,000. The arithmetic is in `refresh.ts`.
- `index.ts`: the Worker's `scheduled` handler, dispatching on `controller.cron`. A failed run throws after keeping the last good data, so Cloudflare's cron log shows it.

`site/functions/api/leaderboard.ts` serves KV `leaderboard` at `/api/leaderboard`: `Cache-Control: public, max-age=60`, an ETag (the Leaderboard's `updatedAt`), 304 on `If-None-Match`, and 503 with a small JSON error before the Worker's first write.

Tests run in plain Vitest with a Map-backed KV and recorded responses (`site/test/helpers/fakes.ts`). `@cloudflare/vitest-pool-workers` needs vitest 4 and the site pins vitest 5, so it isn't used. Deploying is `npm run deploy:worker` and `npm run deploy:pages` (root README, "Deploy").

CPU (`npx vitest run test/worker/cpu.bench.test.ts --silent=false`, which prints each phase): on Node, a generated full Sunday mid-slate of 2,000 Sleeper rows (1.6 MB) with 1,000 expectations in each store (326 KB each) takes about 10 ms warm with unchanged stores: parsing Sleeper's JSON about 4.5, converting 0.5, building the Leaderboard about 2 and serializing and comparing about 1.5 (1,700 players listed, 577 KB). A changed store adds about 3 ms the run it changes. At a recorded full week's 694 rows it is about 4.7 ms (7.5 with changed stores). Cloudflare is slower than Node; Sunday's `wrangler tail` is the real check.
