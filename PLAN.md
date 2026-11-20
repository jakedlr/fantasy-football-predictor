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
| Process | One branch per sprint. Commit after each red→green and each refactor. At sprint end: push and open a PR. Each PR is reviewed by subagents, not the user; Claude merges once the review is clean and CI is green (see `CLAUDE.md`, since 2026-11-20). Each sprint has a research wave (subagents), then a build wave (one subagent per isolated module, each in its own git worktree, doing red-green-refactor). I integrate the modules and run the full suite at the end. Every change that alters behaviour, usage or status updates `README.md` in the same commit (see `CLAUDE.md`). |
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

## Sprint 7 — Concurrent fetching (approved 2026-11-17, done)

A live run spends nearly all its time waiting on the Books. Every request goes one at a time, DraftKings then FanDuel, with a 3.5–5 s pause after each: about 4.6 s per request, from the HTTP cache's file times. A full lineup is roughly 60 requests, about 4–5 minutes. The CPU side is small: a fixture run of 8 players takes 4.7 s with imports. This sprint ports what prizepicks-optimizer's Sprints 6–7 proved on the same endpoints, where 77 requests went from 89.8 s to 14 s with no rate-limit warning. Terms are in GLOSSARY.md (*Pacing*).

| Topic | Decision |
|---|---|
| Scope | Live fetching only. Parallel prediction, nflverse alongside the odds, and a parallel kicker/D/ST Backtest are Sprint 8. |
| Stories | One sprint, two stories on one branch. S7.1: the Books fetch side by side. S7.2: several requests in flight per Book. Each records its own live timing. |
| Pacing | Each Book: at most 3 requests in flight, starts at least 0.3 s apart (prizepicks' values). One constant per Book in `ffp.sources.http`. Requests that aren't to a Book (nflverse, Kalshi) keep today's slow pacing. |
| Pushback | Ported from prizepicks Sprint 7, review fixes included. On a 429 or 403 the Book holds all its requests, drops to one at a time for the rest of the run, honours `Retry-After` up to 10 s and retries once. If that fails, the Book is left out with a warning: "rate-limited" for a 429, "refused" for a 403. The other Book carries on. Waits end early when the run stops. |
| Measurement | One timing line on the console (live requests and seconds per Book), with the same numbers in the Snapshot manifest. OUTPUT.md is unchanged. |
| Ctrl-C | Stops both Books within about a second, with no traceback from the worker threads: in-flight requests are abandoned and pending Pacing waits end. |
| Process | Done criteria approved first; branch `sprint-7`. Every behaviour test-first, against a fake clock and a fake fetch. Thread-safety plumbing is written without tests (races don't reproduce on demand) and listed in the notes. README in the same commit; PR, `/code-review`, findings fixed test-first, merge when green. |

**Done criteria**
- [x] A baseline live run is timed before any change and noted here: fresh, a 9-player lineup across at least 6 Games.
- [x] S7.1: DraftKings and FanDuel fetch at the same time, each paced on its own. A failing Book still leaves the other's markets.
- [x] S7.2: a Book never has more than 3 requests in flight, nor starts two less than 0.3 s apart. The requests after each league response go to `Http` as one batch, and the answers come back in order.
- [x] Pushback as above: hold, slow down, one retry, then a warning naming the rate limit or the refusal.
- [x] Ctrl-C stops both Books within about a second, with no worker traceback.
- [x] The timing line is printed and saved in the Snapshot manifest.
- [x] The Snapshot writer, the request counters and the HTTP cache writes are safe across threads: a lock, and cache files written aside then renamed.
- [x] A fresh 9-player lineup across at least 6 Games runs in under 30 s with no rate-limit warning. The live timing is noted here against the baseline.

**Notes**
- Lineup for every timing, fresh each time: Allen, Henry, Gibbs, St. Brown, Lamb, Chase, Kelce, Dicker and the Ravens D/ST, across 7 Games of week 11, on 2026-11-18 around 05:25 UTC.
  - **Baseline:** 3 min 57 s for the whole run, 53 sportsbook responses (DraftKings 23, FanDuel 30).
  - **S7.1** (side by side, each Book still one request at a time with the 3.5–5 s pause): 2 min 17 s. Odds took 128.1 s: DraftKings 22 requests in 98.3 s, FanDuel 30 in 128.1 s.
  - **S7.2** (3 in flight, 0.3 s apart): **16.3 s**. Odds took 8.8 s: DraftKings 22 in 7.4 s, FanDuel 30 in 8.8 s, with no warning. The other 7.5 s is nflverse and prediction, which Sprint 8 looks at.
- The S7.2 predictions match the baseline's apart from price moves between the runs: 7 of 9 players' percentiles are identical, two moved by at most 1 point, and every flag matches.
- DraftKings made 22 requests, not 23: its batch fetches each URL once, and the anytime-TD Ladder and the D/ST TD scorer are the same subcategory.
- Unlike prizepicks, a failed request in a batch costs only its own answer (one FanDuel event, one stat). Only a Book being left out (`PushedBack`) or the run stopping (`Cancelled`) stops a batch.
- Once a Book is left out, retries still queued behind the failed one are not sent. A test caught this: the 3 requests in flight when the first 429 landed would each have retried.
- Requests made by no Book (nflverse through `Http`, Kalshi) keep the slow per-host pause and the doubled pause after pushback, as before.
- Written without tests, since thread races don't reproduce on demand:
  - the lock around `Http`'s counters and Pacers;
  - the lock in the Snapshot writer, and in `_once`, which serialises `on_response`;
  - cache temp files named by process and thread.
- Ctrl-C: the Books run on daemon threads and the main thread joins in 0.1 s steps, so an interrupt lands at once. `Http.stop()` ends every Pacing wait, and a request still in flight is abandoned with its thread.

**Review:** done 2026-11-18. `/code-review` (medium) found ten issues. Nine were fixed, test-first where testable; one needed no change:
- Batch workers are daemon threads, not a pool. Python joins pool workers at exit, so Ctrl-C would have waited out a request in flight, up to Scrapling's 30 s timeout.
- A hold now stops requests already waiting their turn too. Before, those that had reserved a start went out during it. Each `slow_down` bumps the Pacer's epoch, and a request admitted earlier queues again.
- Requests pushed back together back the Book off once: one warning, one hold.
- A 429 asking for more than 10 s fails only that request again, instead of leaving the Book out.
- A merged DraftKings Ladder keeps the part that answered in the Snapshot, even when another part failed.
- The timing marks a Book slowed by a 403 (`slowed`). GLOSSARY now says a 403 is a refusal and a 429 a rate limit.
- `fetch_draftkings` and `fetch_fanduel_events` are built on the batch helpers, and the unused `fetch_dk_subcategory` is gone.
- Production code has no test-only paths. Test fakes are wrapped in a real `Http` (`tests/conftest.py: over_http`), and a plain `Http` has its own one-at-a-time `get_many`.
- ADR 0004 records the decision.
- No change: a DraftKings league whose Games don't parse leaves the Book out with one warning. Its markets can't be matched to players without Games; before, every stat failed the same way, with a warning each.
- Live again after the fixes, fresh: 16.9 s; odds 8.8 s (DraftKings 22 in 6.7 s, FanDuel 30 in 8.8 s), no warnings.

## Sprint 8 — Parallel prediction and data (approved 2026-11-17, done)

Three small stories. Same process as Sprint 7.

| Topic | Decision |
|---|---|
| S8.1 Parallel prediction | A spike first: threads against processes on a 9-player fixture lineup. Starting processes alone costs about 1–2 s of imports, against 4.7 s of prediction. The story ships only if it saves at least 30% of the prediction time; otherwise it is dropped and the measurement noted here. |
| S8.2 nflverse alongside the odds | The nflverse download starts with the odds fetch and is thrown away if both Books fail. That costs one wasted download in a rare case, and replaces today's rule that a run whose odds fail downloads nothing. Only for a run whose input has a QB, RB, WR or TE; kicker- and D/ST-only runs still download nothing. |
| S8.3 Parallel kicker/D/ST Backtest | `scripts/backtest_k_dst.py --jobs N`, like the Kalshi Backtest's (default 1). |
| Same results | Parallel output is identical to a single-threaded run. S8.1: OUTPUT.md and output.json, apart from the timestamp. S8.3: `summary.json`. Every player and team-game keeps its own fixed seed, whatever order the workers finish in. |

**Done criteria**
- [x] S8.1: spike timings noted here; shipped only at a saving of 30% or more, with identical output.
- [x] S8.2: the nflverse download overlaps the odds fetch; a run whose odds fail discards it; a K/D/ST-only run makes none.
- [x] S8.3: `--jobs N` gives the same `summary.json` as `--jobs 1`, and its run time is noted here.

**Notes**
- **S8.1 spike** (9-player fixture lineup, 20 cores; prediction alone):

  | Way | Time | Output vs serial |
  |---|---|---|
  | Serial | 6.0 s | — |
  | Threads, 4 / 9 | 10.7 / 11.9 s | identical |
  | Processes (fork), 4 / 9 | 2.4 / 1.8 s | identical |
  | Processes (forkserver), 4 / 9 | 3.0 / 1.8 s | identical |

  Threads are slower than serial: the model's Python holds the GIL. Processes save 70%, so S8.1 shipped. It first used forkserver; the review moved every pool to spawn (see below).
- S8.1 design:
  - `predict(jobs=N)` with the CLI's `--jobs N`. The default is one worker per player, leaving a fifth of the cores free; one player always runs in process.
  - Each worker gets the Books and the rules once, through its initializer. Each task carries the player's own TD history and recent games.
  - A player's prediction returns its result, its Fair Points and its warnings. The main process saves and logs them in target order, so a parallel run's Snapshot and console read exactly like a one-process run's.
  - `ScoringRules` now pickles: its read-only `offense` table goes as a dict and comes back read-only.
- Fixture CLI run, same lineup: 7.9 s with `--jobs 1`, 4.8 s by default; output.json identical.
- **S8.2:** the history download (TD history, recent games, snap counts, rosters) runs on a daemon thread from the moment the targets are known. With nflreadpy's disk cache warm it takes about 1.2 s, now hidden behind the odds' ~9 s.
- **Live, S8.1 + S8.2:** fresh, the Sprint 7 lineup without a Snapshot, main and this branch alternated three times on the same machine. Main took 23.9, 23.0 and 22.9 s; this branch 19.1, 18.5 and 17.8 s. That is about 4.8 s (21%) faster. The machine was slower than during Sprint 7's runs: main's 23 s here was 16.9 s then.
- In that live run, prediction took 6.5 s with workers against 11.1 s in one process; live markets are fuller than the fixtures'.
- **S8.3:** test seasons run in worker processes, each sent the data once. 10 seasons: 60.8 s with `--jobs 1`, 33.3 s with `--jobs 8`. The `summary.json` is byte-identical, and the same as the committed run's.
- Tests: CLI tests that monkeypatch the model run with `--jobs 1`, since patches don't reach worker processes; the parallel path has its own identical-output tests. Test fakes use no-gap Pacing (`Http(book_pacing=...)`), which brought the suite back from 4 to about 2 minutes.
- `ffp/__main__.py` now has a `__main__` guard. multiprocessing already skips a package's `__main__`, but worker processes now rely on it, so the guard makes it explicit.

**Review:** done 2026-11-18. `/code-review` (medium) found ten issues, all fixed test-first except where noted:
- **One place starts workers** (`ffp.workers`), for prediction and both Backtests:
  - spawn on every platform: it reads the current sys.path, and Windows has no forkserver. A forkserver started by an earlier pool kept a sys.path without `scripts/`, so the D/ST Backtest's workers could fail depending on test order.
  - Workers ignore SIGINT, so a Ctrl-C prints no worker tracebacks. The main process cancels the queue and terminates the workers at once.
  - The main process waits for results in 0.1 s steps (`in_order`). The full suite showed why: a SIGINT can land on another thread, such as an abandoned daemon thread, and a main thread blocked without a timeout saw it only 30 s later.
  - The worker count comes from the CPUs this process may use (`os.process_cpu_count`), not every CPU on the machine.
- **Prediction:**
  - Worker results are replayed as each player lands, in target order, so a crash keeps the Fair Points and warnings of the players before it. A worker's error comes back with what it logged before raising.
  - Only players with markets to fit get workers; D/STs, kickers and skips run in process. `--jobs` unset means "size it by those players".
  - The one-process path no longer touches logging; only workers collect records, at every level, and the main process's logging filters them on replay.
- **Smaller fixes:**
  - When both Books fail, the run waits for the history download to finish its disk-cache writes before exiting, rather than killing it mid-write.
  - `ScoringRules` pickles every field generically, so a new field can't be silently dropped.
  - `tests/test_predict.py` uses `conftest.over_http`, not a copy of it.
- Re-checked after the fixes:
  - the D/ST Backtest's `summary.json` at `--jobs 8` is identical to `--jobs 1`;
  - the fixture CLI output is identical with and without workers (14.3 s vs 7.1 s on a busier machine);
  - a live run took 19.8 s, with prediction 5.1 s.
  
  Two alternating live A/B pairs were unusable: main and this branch both ran 31–108 s, while DraftKings or the network was slow.

## Leaderboard site — settled decisions (planning session, 2026-11-18)

A public, one-page Leaderboard of every player's Week Points for the current NFL Week, at `ffp-leaderboard.pages.dev`. Terms are in GLOSSARY.md (*Leaderboard*). Research: [research/leaderboard-stats-sources.md](research/leaderboard-stats-sources.md), [research/leaderboard-hosting.md](research/leaderboard-hosting.md), [research/leaderboard-projections.md](research/leaderboard-projections.md).

| Topic | Decision |
|---|---|
| Scoring | A Scoring Rules toggle: `ppr` (default), `half-ppr`, `standard`, `espn-league`. |
| Players | QB, RB, WR, TE, K, D/ST on teams that play this NFL Week; a player is listed once he has recorded a stat or has Expected Points ≥ 1. Bye-week teams are left out. A player ruled Out or Inactive before kickoff is dropped; one ruled out after the list was built shows 0 with an "Out" note. Filters: All, QB, RB, WR, TE, FLEX (RB/WR/TE), K, D/ST. |
| Ranking | By Week Points: Points So Far when Final, Projected Total when Live (with Points So Far beside it), Expected Points when Upcoming. |
| NFL Week | ESPN's week start and end (Wednesday to Wednesday) decide the current NFL Week; the header shows the week number and the dates of its first and last Game, in the viewer's timezone. |
| Search | Name and team (abbreviation or nickname), as you type, combined with the position filter. |
| Table | Rank, Player, Team, Pos, Opponent, Game Status, Week Points, Points So Far. Sorted by Week Points; any header sorts. First 50 rows, then "Show more". Works at phone width. |
| Time | "Updated 1:05 PM EST · 3 min ago" in the browser's timezone (`Intl.DateTimeFormat`, `Intl.RelativeTimeFormat`); no picker. A "data delayed" banner when data is more than 15 minutes old. |
| Live stats | Sleeper's weekly stats endpoint: one call per refresh for every player, with K distance buckets and D/ST stats ready-made. ESPN's scoreboard gives the NFL Week, its dates and each Game Status. ESPN's per-Game summary is the fallback. Response shapes are checked on every poll; a refused minute keeps the last good data. Final Games keep refreshing 30 minutes after Final and once the next day (ESPN corrected stats for 24 minutes after Final in the live test). Changed 2026-11-20 after S9.1 (ADR 0006). |
| Cost | Free only: no paid plan, not even as a fallback. |
| Expected Points | `ffp` on the user's machine in Docker (the prizepicks scheduler pattern): every 30 minutes on game days plus about 10 minutes before each kickoff window, sent to `/api/expected-points` signed with a shared secret, checked against the schema before it is saved (ADR 0006). Players are keyed by Sleeper id, mapped on the home machine from Sleeper's player list. Frozen at kickoff. Players without props, or when the machine is off, use ESPN's projections rescored under our Scoring Rules, marked "ESPN projection". |
| Projected Total | Per Scoring Stat: current value + pregame mean × fraction of the Game left (from quarter and clock; overtime counts as nothing left), then scored, so D/ST tiers and kick distances stay right. A player injured mid-game gets no remainder only once ESPN marks him out (a known limitation). |
| Hosting | A free Cloudflare Worker on a 5-minute timer writes one small JSON to KV only when it changes (or, unchanged, once its update time is 10 minutes old, so "Updated … ago" means the last successful refresh; at most 576 KV writes a day of the free 1,000, S10.4) and serves it at `/api/leaderboard` through a Pages Function. The page polls every 5 minutes while visible and a Game is Live or about to kick off; otherwise it waits for the next kickoff or for the tab to be shown again (every Function call counts toward the free 100,000 requests a day). Changed 2026-11-20 in the PR 17 review; it was every 60 s. Redeploying Pages and GitHub Actions timers can't meet 5 minutes (500 builds a month; timers run 3–16 minutes late). |
| Scoring code | Ported to TypeScript for the Worker, kept in step with shared cases generated from the Python scorer (ADR 0005). |
| Exposure | Public, non-commercial, no ads or betting links, a credit line and disclaimer. Browsers never call ESPN. The stats source sits behind one swappable interface. |
| Alerts | ntfy when the Worker fails 3 runs in a row, or no `ffp` push has arrived for 2 hours on a game day. |
| Stack | Plain TypeScript in `site/`; Vitest for the page and the Worker (the Worker's handlers take fetch, KV and the clock as arguments and are tested with fakes: `@cloudflare/vitest-pool-workers` needs vitest 4, the site pins 5; changed in S10.4); pytest for `ffp`. A Playwright smoke test. |
| CI/CD | GitHub Actions: on every PR, pytest, Vitest, type-check and the setup wizard's self-test, each workflow only when its files change (lint is in the backlog); on merge to `main`, `wrangler pages deploy` and `wrangler deploy` with `CLOUDFLARE_API_TOKEN` (Pages and Workers edit only) and `CLOUDFLARE_ACCOUNT_ID`. Cloudflare's Git integration stays off. |
| Setup | `scripts/setup_cloudflare.sh`, an interactive wizard that walks the user through the account token, KV namespace, Pages project, GitHub secrets and the home push secret, checking each step. |
| Design | taste-skill proposes 2–3 directions as throwaway prototypes on recorded data; the user picks one. **Chosen 2026-11-20: B, "Broadcast"** (dark-first, like a TV score bug: a strip of every Game that filters to one Game's players, a yellow underline for the current selection, Live rows with a red edge and dot, rows sliding to their new rank when Scoring Rules change, tabular mono figures). Two changes: C's banked bar in the Week Points cell (how much is Points So Far), and a light theme designed with the same care as the dark one. Emil Kowalski's skills and the Laws of UX are the review checklist (familiar table, few position chips, 44 px targets, filter under 100 ms, Live rows stand out, motion only where it means something, none under reduced motion). |
| Process | As for `ffp`: one branch and PR per sprint, red→green→refactor. Contracts (the JSON schema, the shared scoring cases) are fixed first on the sprint branch; then one subagent per isolated piece in its own worktree; the main thread reviews, merges and runs the full suite. PRs are reviewed by subagents and merged by Claude (`CLAUDE.md`, "Pull requests"). |

## Sprint 9 — Leaderboard foundation

- **S9.1 Live test** (Thursday 2026-11-19): record ESPN's scoreboard and summaries, and Sleeper's stats, every minute during the Game, from a throwaway Worker and from the user's machine. Measure staleness against the game clock and any blocking (403/429). Findings in `research/leaderboard-live-spike.md`; trimmed responses become fixtures. If ESPN blocks Workers, the Hosting decision is reopened before Sprint 10. **Done 2026-11-20** (results in research/leaderboard-live-spike.md): both sources answered Workers (ESPN refused two single minutes), both were fresh within a minute, ESPN kept correcting stats about 24 minutes after Final. A free Worker can't parse every Live Game's ESPN summary (up to 559 KB each) within 10 ms of CPU, so live stats moved to Sleeper on the free plan (ADR 0006). The recorder Worker and its KV namespace were deleted; trimmed responses are in `site/test/fixtures/`.
- **S9.2 Cloudflare setup wizard** (`scripts/setup_cloudflare.sh`), run by the user. Written; it creates the Pages project and KV namespaces (`ffp-leaderboard`, `ffp-leaderboard_preview`), the CI token, the home push secret, the GitHub secrets `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `LEADERBOARD_PUSH_SECRET` and `NTFY_TOPIC`, and the gitignored `site/.cloudflare-ids.env` and `.env.leaderboard`.
- **S9.3 Contracts:** `site/schema/leaderboard.schema.json`; shared scoring cases generated from `ffp.scoring`; ADR 0005 (TypeScript scoring port) and ADR 0006 (Worker for live stats, home machine for Expected Points).
- **S9.4 CI:** the PR workflows (`ci-python.yml`, `ci-site.yml`), red first.
- **PR 17 review fixes:** players keyed by Sleeper id; one-kick FG cases, fractional D/ST cases and Sleeper's FG buckets in the scoring contract; the Expected Points push signed with a shared secret instead of a KV token; polling every 5 minutes; path-filtered CI with the wizard's self-test.

## Sprint 10 — Walking skeleton: Points So Far on the live site

First: measure the Worker's CPU on a full-Sunday Sleeper response and scoreboard (recorded 2026-11-22, `snapshots/live-sunday/`). If it doesn't fit 10 ms, apply the free answers in ADR 0006 before building on it. **S10.1 done 2026-11-20** on a completed full week (research/leaderboard-live-spike.md): Sleeper alone 4–5 ms, the scoreboard alone 2–3 ms, both in one run 6–20 ms. So the scoreboard gets its own cron trigger and KV key, and the stats run reads that key. Sunday's recording re-checks it live.

Parallel pieces: the Worker's ESPN scoreboard client (Game Status, NFL Week); Sleeper stats to QB/RB/WR/TE stat lines plus the TypeScript scoring port; the page (design direction chosen, then the table, week header, chips, search, last updated). Joined: timer → KV → `/api/leaderboard` → page; deploy on merge, handing `LEADERBOARD_PUSH_SECRET` to the site with `wrangler pages secret put`.

**S10.5 the page, built 2026-11-20:** direction B with C's banked bar and a light theme of its own (the score strip stays a dark ticker band; the yellow selection line becomes a highlighter on white). Static `site/public/` with self-hosted fonts; the script is TypeScript in `site/src/page/`, bundled by esbuild (`npm run build`, run by the deploy job; `public/app.js` is gitignored). Pure, tested modules for filters, ranking, sorts, time, the delayed threshold, the poll schedule and the banked bar. It also took Sprint 12's sorting, "Show more", phone layout, delayed banner and credit line, so Sprint 12 reviews them rather than builds them. Page decisions: ties share a rank (1, 1, 3); an Upcoming player with no Expected Points shows "—" for rank and Week Points (Out shows 0.0); "ESPN projection" shows only while the Game isn't Final; search matches the start of any word of the name, team abbreviation, city or nickname; the chosen Scoring Rules are remembered in the browser; a Game still Upcoming 3 hours after kickoff (postponed) stops the polling; the longest wait is 6 hours.

**Sprint 10 done 2026-11-20** (PRs #18, #19). The site went live on the merge of #18. The first deploy hit error 10007 for a brand-new Worker; re-running the job fixed it, as in S9.1. On Cloudflare, each cron run used 3–4 ms of CPU, but that was a Thursday-only week of 46 players; Sunday's recording re-checks it at full size, where it may approach 10 ms. Sleeper lists a player only once his Game has stats, so Upcoming players appear with Sprint 11's Expected Points.

## Sprint 11 — Complete Week Points

Parallel pieces: K stats (Sleeper's distance buckets) and D/ST stats; `ffp --slate` with the mean in its output, frozen at kickoff, the Docker scheduler and the signed push to `/api/expected-points`; ESPN projections as the fallback, with the Sleeper↔ESPN id map sent in the push (Sleeper's rows carry no ESPN id); the Projected Total per Scoring Stat.

**S11.0 Contracts** (main thread, first on `sprint-11`):
- `site/schema/expected-points.schema.json`: the statMeans keys per position (K FGs by Sleeper's `fgm_*` buckets; D/ST counts plus Points and Yards Allowed), Expected Points under all four Scoring Rules options, `seasonType`, and `idMap` (Sleeper id → ESPN id for every QB/RB/WR/TE/K on a team that plays).
- `site/contracts/push-signature.json`: the signing scheme and a test vector, checked by both the signer and the Function.
- `site/src/worker/kv.ts`: the KV keys `expected`, `idmap` and `espn`, their shapes, and how a push is merged (frozen at kickoff).
- `scoreKicker` scores Sleeper bucket counts, fractional ones included.

Stories, one worktree each:
- **S11.1 K and D/ST from Sleeper** (Worker): kicker and D/ST stat lines from Sleeper's rows, checked against Sleeper's own points and nflverse.
- **S11.2 `ffp slate`** (Python): every player with props in the Games that haven't kicked off, plus every team's K and D/ST. It reports the mean and per-Scoring-Stat means, scores Expected Points under the four options, maps Sleeper ids, and writes the push JSON.
- **S11.3 The push and its scheduler** (Python and Docker): signs and sends the push. It runs every 30 minutes on game days and about 10 minutes before each kickoff window.
- **S11.4 `/api/expected-points`** (Pages Function): checks the signature, the time and the schema, merges into KV `expected` and `idmap`. The deploy job hands `LEADERBOARD_PUSH_SECRET` to Pages. **Built:** the schema is precompiled with Ajv standalone (`npm run build:validator`): @cfworker/json-schema took about 40 ms on a 1,000-player push, against about 1 ms. 409 for a push older than the stored one, 429 for one under 5 minutes after it, and `idmap` written at most hourly, so no sender can spend more than 312 KV writes a day. An ESPN id two Sleeper ids claim is left out of `idmap`.
- **S11.5 ESPN projection fallback** (Worker): a third cron trigger. It reads ESPN's weekly projections, rescores them, joins them through `idmap` and writes KV `espn` only when it changes. First it measures whether the response fits 10 ms of CPU.
- **S11.6 Week Points** (Worker): the stats run merges `expected` and `espn` into the Leaderboard. Upcoming players are listed from their Expected Points (≥ 1). Live players get the Projected Total per stat, using the fraction of the Game left from quarter and clock. The full-Sunday CPU is re-measured. Built 2026-11-20: "recorded a stat" means a non-empty Sleeper stat line (zeros count); a row with only `gms_active`-style keys is listed only with Expected Points ≥ 1 under PPR (in the Thursday recording, 30 of 46 offensive rows were such backups). `out` stays false: no input carries an in-game Out flag. CPU in `site/src/worker/README.md`.

## Sprint 12 — Finish

Design review (Emil's skills, Laws of UX) of the page built in S10.5 (sorting, "Show more", phone layout, the delayed banner, credit line and disclaimer are in), ntfy alerts, a Playwright smoke test, README.

## Backlog

- **Kickers from DraftKings' FG Made and PAT Made Ladders**, once live runs have saved a few weeks of them (every live run with a K that saves a Snapshot keeps them): does fitting attempts to them beat the Game Lines alone?
- **D/ST tilt to the opposing QB's INT line** where a Book prices it.
- **Kicker team-tendency term and weather/wind; D/ST pass-rush and turnover features** (Sprint 6 research: each adds about 1 point of R² or less, or the data is mixed or patchy).
- **Kalshi's D/ST TD Rungs** (KXNFLTD/KXNFLANYTD) against the model's P(D/ST TD ≥ 1): the pull maps D/ST targets but leaves their Rungs out (`dst_rung`; their KXNFLFFPTSLADDER markets `dst_mean`).

- **`sleeper` preset 60+ yard FG:** `src/ffp/scoring.py` gives 6 points; Sleeper's stats endpoint (`pts_std`) gives 5 to all four 60+ yard FGs this season (found in S11.1). Check Sleeper's league defaults and fix the preset if they agree, since the Kalshi checks use it.
- **Lint in CI** (ruff for `ffp`, a TypeScript linter for `site/`): promised for Sprint 10, not done.
- **Game-line fallback** for Games without props: spread/total → implied team total × usage share. It doubles as the 2011–2025 backtest of the aggregation layer.
- **Kalshi as a live Book** next to DraftKings and FanDuel (no key, no vig, deep Ladders).
- **Backtest on our own `snapshots/`** once enough weeks have accumulated.
- **QB fumbles from sacks** (S4.4, dropped from Sprint 4): needs an expected-sacks estimate, e.g. pass attempts × a sack-rate Base Rate.
- **Workhorse-RB correlation override** (≥55% carry share): the Kalshi Backtest saw −0.16 against the matrix's −0.05, 1.8 se on 244 player-games; re-test after the 2026 season.
- **Estimated RB rushing yards** still fail the PIT after Sprint 5.6 (p = 0.011, n = 676): bias −1.5 yards overall, −4.9 at estimated means of 40+; estimated low-volume backs go below zero 5.2% of the time (priced ones 1.6%), so a per-carry distribution for backups may help.
- **Kalshi underprices low-volume RB upside:** on RBs with a rushing-yards mean under 20, 40+ is priced at 8.4% and happens 19.1% of the time (n = 115; 30+ and 50+ likewise). No distribution can fix this. A market-side correction needs validating on one season against the other, and 2025 has only 23 such games.
- **TD vig prior** for DraftKings/FanDuel: test against Kalshi's prices for the same players once our snapshots cover enough weeks.
