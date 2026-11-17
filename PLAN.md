# Plan

Goal for the first stages: `ffp predict INPUT.md` reads player names, one per line. For each QB/RB/WR/TE it prints the Score Percentiles P5/P25/P50/P75/P95 for their next Game, built from DraftKings/FanDuel prop prices.

Vocabulary: [GLOSSARY.md](GLOSSARY.md). Background: [RESEARCH.md](RESEARCH.md) and `research/*.md`. Decisions: `docs/adr/`.

## Settled decisions (planning session, 2026-11-15)

| # | Decision |
|---|---|
| Scoring | Built-in presets `standard`, `half-ppr`, `ppr`, plus custom files `scoring/*.json`, which override bundled ones. Default is the bundled `src/ffp/rules/espn-league.json`: full PPR, 4-pt pass TD, −2 INT, −2 fumble lost. Its D/ST tiers that the league page doesn't show are assumed to be ESPN defaults and flagged as assumptions in the file. |
| Scope | QB/RB/WR/TE from their Prop Markets; K and D/ST from the Game Lines and team markets (Sprint 6). Return TDs and fumble-recovery TDs scored by offensive players are a listed omission. |
| Game | Each player's next scheduled Game, at the prices current at run time. If props aren't posted yet, skip the player with a "rerun closer to kickoff" message. |
| Percentiles | Pₖ means P(points ≤ Pₖ) = k%. `--chance N` adds the chance of N or more points for a number the user supplies (the fixed round-threshold table was removed in Sprint 5). |
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

**Build wave:** 3 subagents in two steps: the Kalshi source (S5.1) and the `sleeper` preset + outcomes + metrics (S5.3) in parallel, then the backtest runner (S5.2) on their interfaces. The full history pull (~35k requests, resumable) runs after S5.1 merges.

Done when `research/backtest-kalshi.md` reports calibration per stat, position and fantasy points, with proposed recalibrations listed for approval.

- **S5.1 Kalshi fetcher.** Settled markets and the last pre-kickoff candle into git-ignored `data/kalshi/`; each Kalshi Ladder becomes a Prop Market whose Fair Probability is the bid/ask mid (an exchange charges no vig).
- **S5.2 Backtest runner** (`scripts/backtest_kalshi.py`): the full pipeline on Kalshi's Ladders per Game, scored against nflverse outcomes. Per stat: PIT histograms, P(≥k) reliability, CRPS. Headline: fantasy points under Sleeper scoring (2026 only: Kalshi's fantasy-points markets start in Week 1), P5–P95 calibration, and model vs market: our P(points > X) against each KXNFLFFPTS line, and our mean against the KXNFLFFPTSLADDER price (a scalar contract, $0.01 per point). Every metric also for an independent-stats baseline (no copula).
- **S5.3 `sleeper` Scoring Rules preset** (Sleeper default full PPR, −1 per INT; research/kalshi-fantasy-points.md), nflverse outcomes per player-game, and the shared metric functions.
- **Findings change nothing automatically.** Each proposed recalibration (dispersion relations, κ, rushing shift, gamma vs lognormal, TD vig prior, conditional receptions→yards, workhorse override) is brought to the user for approval.

### Sprint 5 follow-up (approved 2026-11-16, done; results in research/backtest-kalshi.md)

From `research/backtest-kalshi.md`'s proposals:

- **S5.4 Estimated Stats.** A player with at least one priced core stat gets his missing core stats estimated (method chosen by hiding priced stats in the Backtest: from his other priced stats, from his recent nflverse games, or a blend) and flagged "estimated: <stat> (no market)". "incomplete" stays only where no estimate is possible. A player with no Prop Markets is still skipped.
- **S5.5 κ per stat and position** for Main-Line-only fits, from the Backtest's market-implied sd ratios, in place of 0.93 everywhere.
- **S5.6 Rushing yards near zero.** Rebuild the rushing params with a smaller shift (5 and 7 tried) and a P(0) relation fitted on higher-volume players; adopt the best only if the RB rushing-yards PIT passes and fantasy-point coverage is no worse.
- **S5.7 Interceptions** anchored on the 1+ price.
- **S5.8 Chance of a chosen threshold.** The fixed "chance of N+ points" table (5, 10, ... 30) is removed; `--chance N` reports the chance of N or more points for numbers the user supplies.

## Sprint 5.6 — RB rushing yards (approved 2026-11-17, done; results in research/backtest-kalshi.md)

Two research agents looked into the two open rushing-yards issues from the Backtest (findings in research/backtest-kalshi.md, "Sprint 5.6"). One Backtest run checks both changes.

- **S5.9 Estimated rushing yards.** Three changes, each checked across seasons (fit on one, test on the other):
  - Rushing yards come from the player's expected *rushing* TDs (TDs × his rushing TD share) × rushing yards per rushing TD, instead of total TDs × yards per TD, so receiving TDs no longer turn into rushing yards.
  - An RB's rushing yards from TDs are capped by c·TD^b, a line in logs through priced RBs' fitted means (`estimate_cap` in `market_calibration.json`). A back with a high TD price gets fewer yards per TD.
  - A last-season game counts half a game in the recent-games average.
- **S5.10 Rushing yards as a sum of carries** for RB, WR and TE (docs/adr/0002): an NB number of carries, each drawn from the position's per-carry yards in nflverse play-by-play. QBs keep the hurdle shifted gamma. The Backtest refits the gamma on the same Rungs and reports both.

## Sprint 6 — Kickers and D/ST (approved 2026-11-17, done; results in research/kickers-dst.md)

Research: two agents (kickers; D/ST), reports summarised in research/kickers-dst.md. Decisions (all approved):

| Topic | Decision |
|---|---|
| Scope | Kickers and D/ST in one sprint, sharing the Game Line parsing (spread, total, team totals) that the backlog's game-line fallback will build on. |
| Game-line-only predictions | Allowed for K and D/ST, flagged "from game lines". nflverse's schedule lines are the last fallback. |
| Kicker markets | None in v1: the kicker model runs on the Implied Team Total. A live run with a K that saves a Snapshot fetches DraftKings' kicker subcategories so Snapshots collect them; fitting to them waits for a Backtest on those Snapshots. |
| D/ST markets | Analog Games tilted to the opponent's team-total Ladder and the D/ST anytime-TD price where a Book prices them. The opposing QB's INT line can come later. |
| ESPN points allowed | Excludes the opponent's INT and fumble return TDs, counts special-teams return TDs; yards allowed are net of sack yards. Both listed as assumptions in espn-league.json until confirmed on the league page. |
| Presets | `sleeper` gets Sleeper's K (missed PAT −1) and D/ST (forced fumble +1, its own points-allowed tiers, no yards tiers); `standard`/`half-ppr`/`ppr` get ESPN's defaults; `espn-league` keeps no missed-PAT penalty. |
| Not in v1 (backlog) | Kicker team-tendency term, weather/wind, D/ST pass-rush and turnover features. |
| History | Kickers: recent seasons with a season trend term, rebuilt each season. D/ST: Analog Games from 2015 on, recent seasons weighted more. |
| Backtests | An nflverse walk-forward Backtest (fit on earlier seasons, test each of 2016–2025) for K and D/ST; Kalshi's 2026 K and D/ST fantasy-point lines as a market comparison, with D/ST tickers mapped in the Kalshi pull and wide-spread kicker lines kept as a separate group. |
| Names | "BAL D/ST", "Ravens D/ST", "Baltimore Defense", "BAL DST" resolve to a team's D/ST; kickers by name like any player. |

- **S6.1 Scoring.** Kicker and D/ST scoring from the `kicker`/`dst` sections of Scoring Rules; presets as above.
- **S6.2 Game Lines.** Spread and total from both Books' league responses, team-total Ladders (DraftKings "Team Totals", FanDuel team totals and alternate team totals), D/ST anytime-TD prices; nflverse schedule lines as the fallback.
- **S6.3 Kicker model** (`ffp.kicker`, `params/kicker.json`): FG and PAT attempts from the Implied Team Total (binomial GLMs, joined by a Gaussian copula), FG distance from recent seasons, make probability by distance with a season trend, PAT make rate.
- **S6.4 D/ST model** (`ffp.dst`, `params/dst_games.json`): kernel-weighted Analog Games by the opponent's Implied Team Total, resampled whole, tilted to the markets, scored under any Scoring Rules.
- **S6.5 Players and routing.** K and D/ST resolve and get a Score Distribution in OUTPUT.md.
- **S6.6 Backtests.** `scripts/backtest_k_dst.py` (nflverse walk-forward) and a K and D/ST cut against Kalshi's fantasy-point lines.

### Review of PRs 9–11 (approved 2026-11-17, done)

- Scoring tiers must follow on with no gap or overlap (a skipped tier used to score as the tier below); a value above a closed last tier scores 0.
- The interceptions fit pins on a Main Line chosen by how many books post it, not by merged weight (Rungs merged onto a 2+ point could outweigh the 1+ Main Line).
- `dst_games`: a TD's try is the scoring team's next try play, not the next row (a timeout in between dropped it). 30 of 6,056 team-games changed; the Backtest barely moved (D/ST CRPS 3.428 → 3.429).
- Fixture runs load the team totals; Kalshi's D/ST KXNFLFFPTSLADDER markets drop as `dst_mean`, and wide lines kept on request are not counted as drops; a D/ST hint naming another team is an error.
- Fewer requests: FanDuel's D/ST tabs only for Games with a D/ST; DraftKings' kicker Ladders only when a Snapshot is saved; no nflverse weekly stats for a K/D/ST-only run, and only the columns used otherwise.

## Backlog

- **Kickers from DraftKings' FG Made and PAT Made Ladders**, once live runs have saved a few weeks of them (every live run with a K that saves a Snapshot keeps them): does fitting attempts to them beat the Game Lines alone?
- **D/ST tilt to the opposing QB's INT line** where a Book prices it.
- **Kicker team-tendency term and weather/wind; D/ST pass-rush and turnover features** (Sprint 6 research: each adds about 1 point of R² or less, or the data is mixed or patchy).
- **Kalshi's D/ST TD Rungs** (KXNFLTD/KXNFLANYTD) against the model's P(D/ST TD ≥ 1): the pull maps D/ST targets but leaves their Rungs out (`dst_rung`; their KXNFLFFPTSLADDER markets `dst_mean`).

- **Game-line fallback** for Games without props: spread/total → implied team total × usage share. It doubles as the 2011–2025 backtest of the aggregation layer.
- **Kalshi as a live Book** next to DraftKings and FanDuel (no key, no vig, deep Ladders).
- **Backtest on our own `snapshots/`** once enough weeks have accumulated.
- **QB fumbles from sacks** (S4.4, dropped from Sprint 4): needs an expected-sacks estimate, e.g. pass attempts × a sack-rate Base Rate.
- **Workhorse-RB correlation override** (≥55% carry share): the Kalshi Backtest saw −0.16 against the matrix's −0.05, 1.8 se on 244 player-games; re-test after the 2026 season.
- **Estimated RB rushing yards** still fail the PIT after Sprint 5.6 (p = 0.011, n = 676): bias −1.5 yards overall, −4.9 at estimated means of 40+; estimated low-volume backs go below zero 5.2% of the time (priced ones 1.6%), so a per-carry distribution for backups may help.
- **Kalshi underprices low-volume RB upside:** on RBs with a rushing-yards mean under 20, 40+ is priced at 8.4% and happens 19.1% of the time (n = 115; 30+ and 50+ likewise). No distribution can fix this. A market-side correction needs validating on one season against the other, and 2025 has only 23 such games.
- **TD vig prior** for DraftKings/FanDuel: test against Kalshi's prices for the same players once our snapshots cover enough weeks.
