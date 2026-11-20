# The Leaderboard scores live stats in TypeScript, held to the Python scorer by shared cases

The Leaderboard's Worker turns Sleeper's live stats (ADR 0006) into Points So Far every 5 minutes, under four Scoring Rules options. `ffp.scoring` already does this in Python, with the tier rules settled in Sprints 6 and the PR 9–11 review.

- **Port, don't call.** The Worker gets a TypeScript port of `score`, `score_kicker` and `score_dst`, reading the same rule data.
- **One source of truth.** `scripts/export_scoring_contract.py` exports the Leaderboard's Scoring Rules and scored cases from `ffp.scoring` to `site/contracts/`. The cases include every Points Allowed, Yards Allowed and FG-distance tier edge (each FG distance as its own one-kick case, so opposite errors can't cancel), fractional offense lines and fractional Points Allowed and Yards Allowed between tiers (a Projected Total lands there), and the map from Sleeper's FG distance buckets to distances, checked so no bucket straddles a tier edge. `tests/test_scoring_contract.py` fails when the committed files drift from Python; the Worker's tests score every case in TypeScript and must match to 1e-6.
- **Rules change in Python only.** A rules change is made in `ffp`, re-exported, and the TypeScript side picks it up as data.

## Considered Options

- **A Python Worker (Pyodide):** rejected. The free plan allows 10 ms of CPU per run (research/leaderboard-hosting.md), and Python Workers' cold start plus numpy don't fit.
- **Score on the home machine and push points:** rejected. Live points would stop whenever the machine is off, and the machine is only meant to supply Expected Points (ADR 0006).
- **Hand-maintained TypeScript rules:** rejected. Two copies of the tier tables would drift silently.

## Consequences

- Scoring logic exists twice; the shared cases are what keeps them equal, so a scorer change needs a new case in the exporter first.
- The TypeScript scorer handles plain numbers only, not Monte Carlo arrays.
