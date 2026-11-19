# A Cloudflare Worker refreshes live stats; the home machine pushes Expected Points

The Leaderboard needs live Points So Far every 5 minutes and Expected Points from `ffp`, on free hosting (research/leaderboard-hosting.md).

- **Worker on a 5-minute timer.** It reads ESPN's scoreboard and the summaries of Live Games, scores them (ADR 0005), merges the latest Expected Points, and writes one JSON to KV only when it changed. A Pages Function serves it at `/api/leaderboard`, cached 60 s. Browsers never call ESPN.
- **Home machine for `ffp`.** DraftKings and FanDuel refuse datacenter IPs (prizepicks-optimizer ADR 0001), and a model run takes seconds per player, far over the Worker's 10 ms of CPU. So `ffp` runs in Docker on the user's machine, every 30 minutes on game days and before each kickoff window, and pushes Expected Points to KV with a token that can only edit KV (`site/schema/expected-points.schema.json`).
- **Degrades, doesn't stop.** If the machine is off, live points keep updating; players without a fresh push use ESPN's projections, marked as such.

## Considered Options

- **Redeploying Pages with new data:** rejected. Every 5 minutes is about 8,640 builds a month; the free plan allows 500.
- **GitHub Actions on a schedule:** rejected for refreshes. Its 5-minute schedules ran 3–16 minutes late in practice, runs get dropped, and its runners are datacenter IPs too. It does CI/CD only.
- **Cloudflare Tunnel to the home machine:** rejected. The site would go down with the machine.

## Consequences

- Two moving parts, with separate alerts: the Worker failing 3 runs in a row, and no push for 2 hours on a game day.
- Depends on ESPN answering Workers' IPs; Sprint 9's live test checks that before Sprint 10 builds on it.
