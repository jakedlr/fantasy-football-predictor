# Worker (Sprint 10)

The Leaderboard's cron Worker (ADR 0006). Pure modules here, one per concern, each with a Vitest suite in `site/test/worker/`:

- `scoreboard.ts`: ESPN scoreboard → NFL Week, its dates, and each Game with its Game Status.
- `sleeper.ts`: Sleeper weekly stats rows → `ffp.scoring` stat lines (QB/RB/WR/TE in Sprint 10; K and D/ST in Sprint 11).
- `scoring.ts`: the TypeScript port of `ffp.scoring`, held to `site/contracts/` (ADR 0005).

The cron handler, KV and `/api/leaderboard` join these in Sprint 10's last step.
