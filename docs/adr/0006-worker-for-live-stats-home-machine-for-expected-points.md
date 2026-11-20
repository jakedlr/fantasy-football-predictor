# A Cloudflare Worker refreshes live stats; the home machine pushes Expected Points

The Leaderboard needs live Points So Far every 5 minutes and Expected Points from `ffp`, on free hosting (research/leaderboard-hosting.md).

- **Worker on a 5-minute timer.** It reads Sleeper's weekly stats (one response for every player) and ESPN's scoreboard (NFL Week, dates, Game Status), scores them (ADR 0005), merges the latest Expected Points, and writes one JSON to KV only when it changed. A Pages Function serves it at `/api/leaderboard`, cached 60 s. Browsers never call ESPN.
- **Home machine for `ffp`.** DraftKings and FanDuel refuse datacenter IPs (prizepicks-optimizer ADR 0001), and a model run takes seconds per player, far over the Worker's 10 ms of CPU. So `ffp` runs in Docker on the user's machine, every 30 minutes on game days and before each kickoff window, and pushes Expected Points to KV with a token that can only edit KV (`site/schema/expected-points.schema.json`).
- **Degrades, doesn't stop.** If the machine is off, live points keep updating; players without a fresh push use ESPN's projections, marked as such.

- **Sleeper for live stats, ESPN for the schedule.** The S9.1 live test (research/leaderboard-live-spike.md) found ESPN's per-Game summary reaches 559 KB, and a Sunday has up to 9 Live Games: about 5 MB to parse per run against the free plan's 10 ms of CPU. Sleeper's one response is about 0.9 KB per player, was as fresh (within a minute), and its kicker distance buckets break at 40, 50 and 60, the same edges as every Leaderboard Scoring Rules option. ESPN's summaries stay the fallback for one Game at a time.
- **Free only.** The project spends no money, so the Workers Paid plan is not a fallback. If a full Sunday still doesn't fit 10 ms, the free answers are: read the scoreboard less often (it changes only at kickoffs and Finals), and split the stats across the free plan's cron triggers (up to 5), each writing its own KV key only when it changes.

## Considered Options

- **ESPN summaries for every Live Game:** rejected after the live test: too much to parse within a free Worker's CPU.
- **Workers Paid ($5 a month), 30 s of CPU:** rejected: the project spends no money.

- **Redeploying Pages with new data:** rejected. Every 5 minutes is about 8,640 builds a month; the free plan allows 500.
- **GitHub Actions on a schedule:** rejected for refreshes. Its 5-minute schedules ran 3–16 minutes late in practice, runs get dropped, and its runners are datacenter IPs too. It does CI/CD only.
- **Cloudflare Tunnel to the home machine:** rejected. The site would go down with the machine.

## Consequences

- Two moving parts, with separate alerts: the Worker failing 3 runs in a row, and no push for 2 hours on a game day.
- Depends on two undocumented APIs. Both answered Workers in the live test; ESPN refused two single minutes, so a refused minute keeps the last good data. Sleeper's data provider has asked it to hide these endpoints, so ESPN summaries stay as the fallback.
