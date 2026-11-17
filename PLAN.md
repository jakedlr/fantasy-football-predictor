# Plan

Goal for the first stages: `ffp predict INPUT.md` reads player names, one per line. For each QB/RB/WR/TE it prints the Score Percentiles P5/P25/P50/P75/P95 for their next Game, built from DraftKings/FanDuel prop prices.

Vocabulary: [GLOSSARY.md](GLOSSARY.md). Background: [RESEARCH.md](RESEARCH.md) and `research/*.md`. Decisions: `docs/adr/`.

## Settled decisions (planning session, 2026-11-15)

| # | Decision |
|---|---|
| Scoring | Built-in presets `standard`, `half-ppr`, `ppr`, plus custom files `scoring/*.json`, which override bundled ones. Default is the bundled `src/ffp/rules/espn-league.json`: full PPR, 4-pt pass TD, −2 INT, −2 fumble lost. Its D/ST tiers that the league page doesn't show are assumed to be ESPN defaults and flagged as assumptions in the file. |
| Scope | QB/RB/WR/TE only. A K or DST name gets "not supported yet". Return TDs and fumble-recovery TDs are a listed omission. |
| Game | Each player's next scheduled Game, at the prices current at run time. If props aren't posted yet, skip the player with a "rerun closer to kickoff" message. |
| Percentiles | Pₖ means P(points ≤ Pₖ) = k%. Output also includes "chance of N+ points" for a few round thresholds. |
| Live odds | DraftKings + FanDuel site endpoints ([ADR 0001](docs/adr/0001-sportsbook-site-endpoints-for-live-odds.md)). Kalshi is used only for backtesting. Every live run saves its raw responses to `snapshots/`. |
| Devig | Logit-shift (odds-ratio) vig, calibrated on each book's Main Line. Without a Main Line, a fixed per-book/market vig. Fair Probabilities are forced to decrease as the threshold rises (isotonic). One fit uses both books' Rungs, with Main Lines weighted ×3. |
| Stat Distributions | Per `research/stat-distributions.md`:<br>- **Receiving and rushing yards:** point mass at 0 + shifted gamma.<br>- **Passing yards:** normal + a small mass at 0.<br>- **Receptions:** negative binomial.<br>- **Pass TDs:** Binomial(11, μ/11).<br>- **INTs:** Poisson.<br>- **Rush+rec TDs:** Poisson from anytime TD, split into rushing vs receiving by historical share.<br><br>Fits are by weighted least squares in logit space, on mean and ln sd with a prior on ln sd taken from the dispersion relation. Yards fits also fit P(0), with a wide prior (sd 0.5 in logit) centred on its relation to the mean, so a low-volume player's Main Line isn't missed (Sprint 1 review, finding 2). With only a Main Line: sd = 0.93 × the relation and P(0) on its relation. |
| No-market stats | Fumbles lost (Poisson, rate per touch; per sack for QBs) and 2-pt conversions (flat rate) come from nflverse Base Rates. |
| Correlation | Gaussian copula with within-player latent Spearman matrices per position, nflverse 2021–25 (`research/nflverse-data.md`). Pooled matrices sit behind a config switch. Fumbles and 2-pt are sampled independently. Receiving yards and TDs are 0 whenever receptions are 0. Supervised models are a later experiment, adopted only if the backtest calls for one. |
| Players | Canonical identity is the nflverse `gsis_id`. Matching uses the normalized name + position, with team as tiebreak and an alias table. An ambiguous name fails loudly; a `Name (TEAM)` hint resolves it. A player with missing markets is flagged "incomplete: missing X". A player with no markets is skipped. |
| Output | `OUTPUT.md` (table) + `output.json` (percentiles + per-stat detail). Fixed seed, 20,000 Monte Carlo samples. |
| Stack | Python ≥3.11, `src/ffp`, pytest, numpy/scipy, nflreadpy, Scrapling. Tests never touch the network; the fetch function is passed in and recorded fixtures stand in. A live smoke test is marked separately. Useful modules are copied from prizepicks-optimizer together with their tests. |
| Process | One branch per sprint. Commit after each red→green and each refactor. At sprint end: push, open a PR, merge when green. Each sprint has a research wave (subagents), then a build wave (one subagent per isolated module, each in its own git worktree, doing red-green-refactor). I integrate the modules and run the full suite at the end. Every change that alters behaviour, usage or status updates `README.md` in the same commit (see `CLAUDE.md`). |
| Git | Trimmed fixtures are committed. `snapshots/`, `.cache/`, nflverse downloads and outputs are gitignored, since the repo is public. |

## Module map

```
src/ffp/
  scoring.py         Scoring Rules: presets, custom JSON, stat line → points
  odds.py            American → implied probability; logit-shift devig (copied from ppopt odds.py and extended)
  markets.py         Prop Market / Main Line / Ladder / Rung types
  sources/http.py    paced, cached fetch with a fetch function passed in (copied from ppopt)
  sources/draftkings.py, sources/fanduel.py   JSON → Prop Markets (Ladders + Main Lines + TD scorer)
  fair.py            Prop Markets → Fair Probabilities (devig, merge books, isotonic)
  distributions.py   families + fitting → Stat Distribution
  params/            committed JSON: dispersion relations, correlation matrices, Base Rates
  copula.py          joint sampling of Stat Distributions
  simulate.py        samples × Scoring Rules → Score Distribution → Score Percentiles
  players.py         INPUT.md names → gsis_id, position, team; next Game (nflverse)
  snapshots.py       save raw responses per run
  report.py          OUTPUT.md + output.json
  cli.py             `ffp predict`
scripts/build_params.py   regenerates params/ from nflverse
```

## Sprint 0 — Scaffolding

**Research wave:** none.

**Build wave:** 2 subagents (scoring; odds and names).

Done when `pytest` passes and `ffp --help` runs.

- **S0.1 Project skeleton.** pyproject, `src/ffp`, pytest, `ffp` entry point. First test: `ffp --help` exits 0.
- **S0.2 Scoring Rules.**
  - Tests come first:
    - A known stat line scores the hand-computed points under each preset.
    - It scores the hand-computed points under `espn-league.json` too.
    - An unknown Scoring Stat in a custom file is rejected.
    - D/ST and K rules load but are not used.
- **S0.3 Odds and names, copied in.**
  - Copy `odds.py` and `normalize_name` from ppopt together with their tests.
  - New tests, first:
    - Logit-shift devig of a two-sided Main Line sums to 1.
    - Applying the calibrated c to a Rung lowers its probability.
    - Unicode "−" odds parse.

## Sprint 1 — Walking skeleton: one WR, receiving yards, from fixtures

**Research wave:** none (fixtures exist).

**Build wave:** 3 subagents (DK parser; fair + distributions; simulate + report).

Done when `ffp predict INPUT.md --fixtures research/fixtures` prints P5–P95 for one WR, using receiving yards only.

- **S1.1 DK Ladder parser.** Test: `dk_nfl_rec_yds_ladder.json` + `dk_nfl_rec_yds_ou.json` give the expected Rungs for a named player, with threshold "70+" parsed as Y ≥ 70, and the Main Line. In-progress Games are filtered out.
- **S1.2 Fair Probabilities.** Tests:
  - Probabilities are non-increasing in the threshold after isotonic.
  - The Main-Line-implied median is preserved.
  - A synthetic Ladder with known vig recovers the fair values.
- **S1.3 Receiving-yards Stat Distribution.**
  - Test: fitting to Rungs simulated from a known shifted gamma recovers the mean and sd within tolerance.
  - Test: with only a Main Line, the fit falls back to the dispersion relation.
  - P(0) comes from a logistic of the mean for now; Sprint 3 replaces it with receptions.
- **S1.4 Simulate + percentiles.** Tests:
  - With a fixed seed, percentiles are deterministic.
  - P5 < P25 < P50 < P75 < P95.
  - Points = 0.1 × yards under the ESPN rules.
- **S1.5 Report + CLI.**
  - Golden-file test of `OUTPUT.md` and `output.json` for the fixture INPUT.
  - Exact player names only; Sprint 2 adds matching.

## Sprint 2 — Live odds and real players

**Research wave:** one subagent re-checks the endpoints and fixture freshness. Name collisions were already researched.

**Build wave:** 4 subagents (http + live sources; players + schedule; snapshots; FD parser).

Done when `ffp predict INPUT.md` works live for WR/TE receiving yards.

- **S2.1 HTTP wrapper** (copied from ppopt). Tests:
  - Requests are paced.
  - A 429 triggers backoff.
  - The cache is used.
  - The FD app key never appears in logs.
- **S2.2 FD parser.** Tests:
  - Thresholds are parsed from runner names ("Zay Flowers 70+ Yards").
  - The LOW/MEDIUM/HIGH tier buckets merge.
  - The `fd_nfl_event_receiving_tab_not_posted.json` fixture gives "not posted".
- **S2.3 Player resolution.** Tests:
  - "Ja'Marr Chase" and "AJ Brown"/"A.J. Brown" resolve to the right gsis_id.
  - "Justin Jefferson" resolves to the WR, not the LB.
  - An ambiguous name raises an error.
  - "Name (TEAM)" breaks the tie.
  - A K/DST name gets "not supported".
- **S2.4 Next Game.** Test: from a schedule fixture, the next unscored Game, opponent and kickoff are found. A Game without posted props triggers the skip message.
- **S2.5 Combine books.** Test: DK and FD Rungs at different thresholds fit one Stat Distribution, with Main Lines weighted ×3.
- **S2.6 Snapshots.** Test: a run writes the raw responses and the Fair Probabilities under `snapshots/<date>/`.
- **S2.7 Live smoke test** (marked, not in the default run). Expected: at least one real player gets percentiles.

## Sprint 3 — Full WR/TE Score Distribution with correlation

**Research wave:** turn the research scripts into `scripts/build_params.py` and generate `params/`, i.e. the dispersion relations, the WR/TE latent correlation matrices and the Base Rates.

**Build wave:** 3 subagents (receptions + TD markets; copula; base-rate stats).

Done when WR/TE output includes receptions, TDs, fumbles and 2-pt, all correlated.

- **S3.1 Receptions Ladder → negative binomial.** Test: recovers a known NB from simulated Rungs.
- **S3.2 Anytime/2+/3+ TD → Poisson λ, split rushing vs receiving.** Test: P(2+) is consistent with the 2+ price within tolerance.
- **S3.3 Copula.** Tests:
  - The Spearman correlation of the samples matches the input matrix within ±0.03.
  - Each stat's marginal is unchanged by the copula (KS test).
  - Receiving yards and TDs are 0 whenever receptions are 0.
- **S3.4 Base Rate stats.** Test: the mean fumbles lost equals rate × expected touches.
- **S3.5 Missing-market flags.** Test: a WR with no receptions market is reported as "incomplete: missing receptions".
- **S3.6 Pooled vs within-player switch.** Test: the config selects the matrix.

## Sprint 4 — RB and QB

**Research wave:** check the QB rushing and RB Ladder fixtures and record a rush-attempts (carries) fixture if one is missing. Generate the RB/QB params: rushing-yards relations (RB, QB, and WR/TE for receivers with rushing markets), passing-yards relations, the carries r(μ) relation, and the empirical-Bayes shrinkage (strength and seasons) for each player's rushing TD share.

**Build wave:** 3 subagents (QB passing stats; rushing stats + carries; position matrices + TD split).

Done when a QB, RB, WR and TE each get a full Score Distribution and the suite passes.

- **S4.1 Passing yards** (normal + mass at 0), **pass TDs** (Binomial(11, μ/11)), **INTs** (Poisson). DK only for INTs if FD has none.
- **S4.2 Rushing yards** (point mass at 0 + shifted gamma, with a shift for negatives) for RBs and QBs, and for WR/TE when a rushing market exists (the "counts as 0" assumption stays only for those without one). **Carries** are a Usage Stat: fetched, fitted as a negative binomial, used only for their mean in expected touches (fumbles), never simulated, and they don't set rushing-yards P(0). With no carries market (FanDuel has none), expected carries come from the rushing-yards mean over the position's yards-per-carry Base Rate; with neither market, fumbles lost uses the per-game Base Rate, unflagged (PR #7 review).
- **S4.3 Position matrices** for QB and RB (one per position, no workhorse override), and the **per-player rushing TD share** for all four positions (research/nflverse-data.md section 6): volume share v = (ρr·C + κ·m)/(ρr·C + ρt·T + κ) from the player's carries C and targets T, then share = (R + k·v)/(N + k) from his rushing TDs R of N rush+rec TDs; κ = 2, k = 30; window this season to date + the previous 2 seasons. It splits both TDs and 2-pt conversions. History comes from nflverse weekly player stats fetched at runtime; no history, or nflverse unreachable (logged), gives the position share m.
- ~~S4.4 QB fumbles from sacks.~~ Dropped (Sprint 4 planning): no book offers a QB sacks-taken market, and research/nflverse-data.md finds QB fumbles lost barely vary by usage, so the flat per-game rate stays.

## Sprint 5 — Kalshi backtest

**Research wave:** Kalshi coverage per stat, Oct 2025 → now (settled games, players, Ladders; `/historical` before the 2026-09-17 cutoff and the live endpoints after), bid/ask spreads and which pre-kickoff price to use; Kalshi's API terms for personal research use; the Sleeper scoring that Kalshi's fantasy-points contracts settle on.

**Build wave:** 3 subagents (Kalshi fetcher + cache; backtest runner; `sleeper` preset + scoring metrics).

Done when `research/backtest-kalshi.md` reports calibration per stat, position and fantasy points, with proposed recalibrations listed for approval.

- **S5.1 Kalshi fetcher.** Settled markets and the last pre-kickoff candle into git-ignored `data/kalshi/`; each Kalshi Ladder becomes a Prop Market whose Fair Probability is the bid/ask mid (an exchange charges no vig).
- **S5.2 Backtest runner** (`scripts/backtest_kalshi.py`): the full pipeline on Kalshi's Ladders per Game, scored against nflverse outcomes. Per stat: PIT histograms, P(≥k) reliability, CRPS. Headline: fantasy points under Sleeper scoring, P5–P95 calibration, and our percentiles against Kalshi's own fantasy-points Ladder. Every metric also for an independent-stats baseline (no copula).
- **S5.3 `sleeper` Scoring Rules preset** matching Kalshi's contract terms, and the shared metric functions.
- **Findings change nothing automatically.** Each proposed recalibration (dispersion relations, κ, rushing shift, gamma vs lognormal, TD vig prior, conditional receptions→yards, workhorse override) is brought to the user for approval.

## Backlog (Sprint 5 chosen after Sprint 4; kickers and D/ST likely next)

- **Game-line fallback** for Games without props: spread/total → implied team total × usage share. It doubles as the 2011–2025 backtest of the aggregation layer.
- **Kicker and D/ST** from team totals and opponent totals (likely Sprint 6).
- **Kalshi as a live Book** next to DraftKings and FanDuel (no key, no vig, deep Ladders).
- **Conditional receptions→yards model** or a supervised alternative, adopted only if the backtest shows the copula is miscalibrated.
- **Backtest on our own `snapshots/`** once enough weeks have accumulated.
- **QB fumbles from sacks** (S4.4, dropped from Sprint 4): needs an expected-sacks estimate, e.g. pass attempts × a sack-rate Base Rate.
- **Workhorse-RB correlation override** (≥55% carry share: RuYd–Rec −0.12 vs +0.02 pooled), adopted only if the backtest shows it matters.
