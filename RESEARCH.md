# Research: Fantasy Football Score Distributions from Player Prop Odds

Researched 2026-11-15 by three Opus 5.5 subagents (low effort). The Q2 and Q3 agents each started their own subagents to shallow-clone and read GitHub repos. Repos were read only; nothing was run.

## Summary

1. **Coverage (Q1):** props cover most fantasy points, but not all of them.
   - Covered for nearly every starter: passing, rushing and receiving yards, receptions, pass TDs, anytime TD, and INTs (usually just O/U 0.5).
   - No market: fumbles lost, player 2-pt conversions, and FG-by-distance. Use base rates for these.
   - Kicker and DST have only partial coverage. Build them mostly from game and team totals.
   - Fringe players (handcuffs, WR4/TE2) may have only an anytime TD price. They need a fallback model such as team total × usage share.
2. **Devigging (Q2):** a one-sided X+ price can't be devigged on its own, so you have to borrow the vig from somewhere.
   - Calibrate a logit-shift (odds-ratio) vig on the player's two-sided main O/U line, or use a sharp book or a historical prior for that book and market.
   - Apply it to every rung, force the rungs to decrease with yardage (isotonic regression), then fit a parametric survival curve.
3. **Correlation and percentiles (Q3):**
   - Use the devigged distributions as each stat's marginal.
   - Join them with a Gaussian or t copula, with rank correlations estimated from nflverse weekly stats by position and role. For receptions → receiving yards, a conditional model is better.
   - Run Monte Carlo, score each sample, and read the percentiles off the samples.
   - Validate with PIT histograms and CRPS.
   - nflverse provides all the historical stats needed for free. Sportsbook prop history costs money, but Kalshi's public API gives free historical NFL prop ladders from Oct 2025 on. See **Q4: Doing this for $0**.

### Caveats on reliability
- **Q1 is mostly unverified.** Only one claim was checked against a source: The Odds API's `_alternate` market keys. Book-by-book offerings, coverage depth, line timing and pricing are from the model's general knowledge. The "about 95% of points covered" figure is a rough estimate. Check a live FanDuel/DraftKings board and The Odds API's markets page before relying on any of this.
- In Q2 and Q3 the method descriptions are standard and the repo notes come from reading the code. Hold percentages and vendor history start dates came from web articles and should be spot-checked.
- No open-source repo found does this exact pipeline (prop ladders → stat distributions → fantasy distribution). The closest template is chanzer0/NFL-DFS-Tools.

---

## Q1: Do props cover every fantasy scoring event and every fantasy-relevant player?

Short answer: they don't cover everything, but they come close. Props cover the events behind about 95% of skill-position fantasy points, for nearly every player who gets started. The gaps are fumbles, 2-pt conversions, and most kicker and DST components. Those need simple models or base rates.

Verification note: I confirmed only The Odds API's `_alternate` key pattern with a source (links below). Everything else here, including book-by-book offerings, player depth, timing, and vendor pricing, comes from general knowledge of the US market and has not been checked against live boards or vendor pages.

### Coverage table

| Event | Offered? | Alt ladder (X+)? | Notes |
|---|---|---|---|
| Passing yds | Yes, all major books (FD, DK, MGM, CZR) | Yes (FD/DK milestones 150+…350+) | Starting QBs every game |
| Pass TDs | Yes | Yes (1+, 2+, 3+, 4+) | O/U 1.5 typical, plus milestones |
| Interceptions thrown | Yes (O/U 0.5, often juiced) | Limited (1+, 2+ at some books) | Common for starters |
| Pass attempts / completions | Yes | Yes at some books | Useful as auxiliary |
| Rushing yds | Yes | Yes | RB1/RB2, mobile QBs, some WRs |
| Rush attempts | Yes | Some | |
| Receptions | Yes | Yes (2+…8+) | Key for PPR |
| Receiving yds | Yes | Yes (FD 25+…150+ ladders) | WR1-3, TE1, pass-catching RBs |
| Rush+Rec yds | Yes | Some | Redundant with the above |
| Rush TD / Rec TD separately | Rarely; mostly folded into Anytime TD | No | See TD note below |
| Anytime TD | Yes, very broad (nearly every rostered skill player, incl. backups, plus DST/"no TD") | 2+ TDs and 3+ TDs markets | One-sided (yes only) |
| Fumbles lost | **No** (essentially never) | No | Model with base rate (~0.1–0.2 per game for RBs) |
| 2-pt conversions | **No** player market (game-level "2-pt attempt yes/no" exists at some books) | No | Negligible; use base rate |
| Kicker FGs made | Yes (O/U 1.5 FGs made), plus "kicking points" O/U | Few (FD/DK sometimes 2+, 3+ FGs) | **Distance-by-FG not offered** (only "longest FG" O/U) |
| Kicker XPs | Bundled into kicking points | No | Derive from team TD implied totals |
| DST sacks | Team sacks O/U at some books; player sacks (0.5) common | Rare | |
| DST INTs | Player "to record an INT" at some books; team INT props sparse | No | |
| Fumble recoveries | No | No | Base rate |
| Defensive/return TD | Yes, as "Defense/ST TD" in the anytime-TD list | No | One-sided |
| Safety | Game-level "Will there be a safety" yes/no | No | |
| Points allowed | Indirect: opponent team total (O/U and alt team totals) | Yes, via alt team totals | Strong signal |
| Yards allowed | Not directly (sometimes opponent team total yards) | No | Derive from opp. player yardage props |

### Player coverage
- **Deep coverage:** the starting QB, the top 2 RBs, WR1-3 and TE1 for every game at FD/DK. Big games and primetime often add WR4, TE2 and RB3. Anytime TD goes deepest, listing nearly the whole depth chart.
- **Thin coverage:** backup RBs in committees often get only rush yds and anytime TD. Deep WRs and TEs often get only receptions and receiving yds, with no ladder. Ladders (alt/milestone lines) are fuller for stars.
- **Timing:** the main lines usually post Tuesday to Thursday for Sunday games. Alt lines often come later. Lines are pulled or suspended when injury news hits and reposted once status is clear, so questionable players may have no line until Sunday morning. Primetime and TNF games get the most markets. A few late Sunday games can be sparse early in the week.
- **Implication:** almost every player who gets started in fantasy has at least one yardage line plus anytime TD. Fringe flex players and handcuffs may only have anytime TD, or nothing. For players without a line, you'd need a fallback model, such as team totals times a usage share.

### TD markets
US books mostly offer **Anytime TD** (rush and rec combined), plus 2+ TDs and first/last TD. A separate rush-TD vs rec-TD split is uncommon. For fantasy it barely matters (both score 6), except for QB rushing TDs (QB anytime TD = rushing TD). Anytime TD prices are one-sided with heavy vig of about 20–30%+, so devigging is harder. **Measured 2026-11-16 (Sprint 3 review): not borne out.** DraftKings' "Player Not To Score a TD" (subcat 15960) gives the No side for some players. Six Yes/No pairs (LAC @ BAL) had an overround of 6.4–7.6% and a logit-shift c of 0.155–0.243 (median 0.20), against 0.14 for a −115/−115 line. `fair.TD_PRIOR_C` = 0.20 comes from these. The response is saved as `research/fixtures/dk_nfl_player_not_to_score.json`. Common approaches are a power or Shin method, or calibrating against the player's yes price at sharper books.

### Data access
- **The Odds API** (the-odds-api.com): NFL player props are available through `/events/{id}/odds`. Keys include `player_pass_yds`, `player_reception_yds`, `player_receptions`, `player_rush_yds`, `player_anytime_td`, `player_pass_tds`, `player_pass_interceptions`, `player_field_goals`, `player_kicking_points` and more. The site says milestone (X+) markets use `_alternate` keys, e.g. `player_pass_yds_alternate`. Its alternate table shows `player_pass_attempts_alternate` and `player_pass_completions_alternate`, which supports `player_reception_yds_alternate` existing, but I did not confirm that exact key. Pricing is from memory, not checked against the site: there's a free tier of 500 credits/month, and props cost credits per market per region per event. Paid plans start around $30/month. Historical props have existed since about mid-2023 (snapshots, paid plans only). Sources: https://the-odds-api.com/sports-odds-data/betting-markets.html, https://the-odds-api.com/sports/nfl-odds.html
- **Alternatives** (not looked up): OpticOdds, OddsJam, Unabated, SportsDataIO, SportsGameOdds and OddsPapi. Enterprise vendors with full alt ladders across many books (OpticOdds, OddsJam, SportsDataIO) generally cost hundreds of dollars a month or more. SportsGameOdds has a cheaper or free tier. Unabated is more of a tool than an API.
- **Historical data:** free historical player-prop ladders are rare. Plan to scrape or snapshot your own going forward, or pay for The Odds API's historical snapshots.

### Bottom line for the model
Price QB, RB, WR and TE scores from yardage ladders, reception ladders, anytime TD, pass TDs and INTs. Fill in fumbles and 2-pt conversions with base rates. Model K and DST from game and team totals, with sack/FG props where available. That should cover roughly 95% of fantasy point variance for skill players.

---

## Q2: Devigging one-sided "X+" milestone ladders, then building a CDF

### TL;DR
You cannot devig a one-sided rung by itself. With no opposing price there is no overround to measure. The vig has to come from somewhere else:
1. the two-sided main O/U line for the same player and stat,
2. a sharp book (Pinnacle or Circa) on the same rung, or
3. an assumed hold that varies by market type and rung.

Pick a *vig model* that maps fair p to the book's implied q, calibrate it on the main line, and invert it on every rung. Then fit a monotone parametric survival curve to the devigged rungs.

### Repos reviewed (shallow-cloned and read only; nothing run)

There are few dedicated repos. GitHub searches for "devig", "no vig odds" and "remove vig" return mostly 0-star projects. These three are the canonical implementations.

| Repo | Stars | Methods | One-sided handling | Relevance |
|---|---|---|---|---|
| [martineastwood/penaltyblog](https://github.com/martineastwood/penaltyblog) (`penaltyblog/implied/implied.py`) | 250 | multiplicative, additive, power, Shin, differential margin weighting (Buchdahl), odds ratio (Cheung), logarithmic (logit shift) | None. Assumes a complete set of outcomes. | Clean Python reference for every method. The logit-shift and odds-ratio methods are the ones to reuse. |
| [mberk/shin](https://github.com/mberk/shin) | 105 | Shin only (Rust plus a Python fallback). Two-outcome closed form: z=(S−1)(D²−S)/(S(D²−1)). | None. Requires n≥2. | For two outcomes, Shin is the same as additive. Not very useful for ladders. |
| [opisthokonta/implied](https://github.com/opisthokonta/implied) (R) | 9 (the reference package; CRAN `implied`) | basic, Shin, bb, wpo, odds ratio, power, additive, jsd, goto/ooepc | None, but it has `target_probability` (target sum ≠ 1), which handles non-exclusive markets such as top-N | Has the most complete set of formulas. |

**Formulas from the code** (q = 1/decimal odds, S = Σq):
- multiplicative: p = q/S
- additive: p = q − (S−1)/n
- power: p = q^k, solving for k so that Σp = 1
- odds ratio: p = q/(c + q − c·q), which is the same as odds(q) = c·odds(p)
- logarithmic: p = σ(logit q − c)
- Shin: p = (√(z² + 4(1−z)q²/S) − z)/(2(1−z))
- wpo: fair odds = n·o/(n − margin·o)

### Web findings
- Devig tools say a one-sided market "has no overround to distribute". Any fair number from it is invented unless the other side is priced somewhere ([oddsguy devig calculator](https://oddsguy.ai/tools/devig-calculator), [oddsguy learn](https://oddsguy.ai/learn/devig)).
- Common practice is to devig the two-sided standard line at a market-making book (Circa or Pinnacle) and use it as the baseline for alt lines and milestones. Devigging a single soft book removes its margin but keeps its lean ([SportsGameOdds](https://sportsgameodds.com/blog/removing-the-vig-from-sportsbook-odds), [Establish The Run on boosts](https://establishtherun.com/how-to-approach-odds-boosts/)).
- Crazy Ninja Mike's devigger recommends the conservative "worst-case" method ([guide](https://tennesseev.substack.com/p/a-beginners-guide-part-7-mastering)). For one-way markets, practitioners plug in an assumed margin.
- Hold estimates: player props and alt lines run about 6–15%, against about 4–5% on main spreads and totals. Alt lines carry more vig than main lines ([DeucesCracked](https://www.deucescracked.com/blog/sportsbook-hold-percentage-explained-vig-by-market-2026), [SI alt lines](https://www.si.com/betting/what-are-alt-lines-sports-betting-alternate-lines), [Action Network on futures hold](https://www.actionnetwork.com/education/calculate-remove-hold-from-futures)).
- Favorite-longshot bias: books shade longshot rungs (100+, 125+ yds) the most, measured as a fraction of fair probability. A flat additive or multiplicative vig would overstate the tails. The odds-ratio, logit-shift, power and Shin methods all put more relative vig on longshots by design.

### Recommended approach

**Step 0. Convert prices.** American to implied probability: q = 100/(A+100) for A>0, and q = −A/(−A+100) for A<0. For example, −800 gives 0.889, −420 gives 0.808 and −260 gives 0.722.

**Step 1. Choose a vig model.** Use the logit-shift model, which is the same as odds ratio:

  logit(q_x) = logit(p_x) + c,  so  p_x = σ(logit(q_x) − c).

A single c > 0 means the vig shrinks in absolute terms near p=0 and p=1, and is largest in relative terms on longshots. That matches the favorite-longshot pattern.

**Step 2. Calibrate c from the main two-sided line.** Take the main line L with prices (q_o, q_u). Solve σ(logit q_o − c) + σ(logit q_u − c) = 1 with a 1-D root-find, the same as penaltyblog's `_logarithmic`. This c is the book's per-side vig for this player's market.
- If there is no main line, fall back to a prior c for each book and market type. Estimate it from history, for example the median c across all two-sided rec-yds props at that book.
- Optionally let c grow with distance from the main line, c(x) = c₀ + c₁·|logit p_x|. Fit c₁ across many players by comparing soft-book ladders with Pinnacle or with the main line.

**Step 3. Devig each rung:** p̂_i = σ(logit q_i − c). By construction p̂_i is the fair P(Y ≥ x_i). Also add the main line's devigged over, P(Y > L) = p_o, as an anchor point.

**Step 4. Enforce monotonicity.** The p̂_i must be non-increasing in x. Run isotonic (PAV) regression on (x_i, p̂_i), weighted by 1/Var or simply equally. This step removes stale or misquoted rungs.

**Step 5. Fit a parametric survival function S(x; θ)** by weighted least squares in logit space:

  θ* = argmin Σ_i w_i [logit p̂_i − logit S(x_i − 0.5; θ)]²

Use a continuity correction of 0.5 because "50+" means Y ≥ 50. Good families:
- **Rec/rush yards:** a zero-inflated or shifted **gamma**, or a **lognormal**, or a skew-normal. Yards can be negative, so shift the support (Y+10) or add a point mass for 0 catches.
- **Receptions, TDs:** **negative binomial** or Poisson. For anytime TD, use Poisson with λ = −ln(1 − p_TD).

Weight the main line most heavily (w ≈ 3). It is two-sided, so its probability is the cleanest.

**Optional joint fit.** When there is no main line, estimate c and θ together:

  min over (θ, c) of Σ [logit q_i − (logit S(x_i; θ) + c)]² + λ(c − c_prior)²

c is only weakly identified from one ladder because a shift in c trades off against the distribution's location. The prior term (or a sharp-book anchor) is what pins it down.

**Step 6. Output.** The CDF is F(x) = 1 − S(x; θ*). Sample from it or discretize it to feed the fantasy points calculation, for example 0.1 × yds. Combine stats with a copula or simulation, since yards, receptions and TDs are correlated.

**Sanity checks:**
- The devigged median should be close to the main O/U line.
- Across many games, the calibration of p̂ against outcomes shows whether c needs to depend on the rung.
- When Pinnacle or Circa post the same rung two-sided, prefer their devigged price.

---

## Q3: Handling correlated stats when aggregating prop marginals into fantasy-point distributions

### TL;DR
Treat each devigged prop ladder as a **marginal CDF**, tie the marginals together with a **Gaussian (or t) copula whose rank correlations are estimated from nflverse weekly history by position/role**, Monte Carlo sample ~10k joint stat lines per player, apply the scoring formula to each, and read percentiles off the samples. For the strongest pair (receptions -> receiving yards) a **conditional model** (yards | receptions) is better than a copula. Validate with PIT histograms, CRPS and pinball loss against actual outcomes.

### Repo survey
Open-source work in this area is thin and has few stars. No popular repo builds fantasy distributions from prop ladders.

| Repo | Stars | Approach | Relevance |
|---|---|---|---|
| https://github.com/DimaKudosh/pydfs-lineup-optimizer | 451 | Lineup optimizer (see notes below) | Low/medium |
| https://github.com/nflverse/nfl_data_py | 443 | Python loader for nflverse pbp/weekly data | High (data) |
| https://github.com/nflverse/nflreadr | 117 | R loader for nflverse | High (data) |
| https://github.com/chanzer0/NFL-DFS-Tools | 52 | DFS optimizer + GPP simulator (see notes below) | High (correlation sim) |
| https://github.com/ffverse/ffsimulator | 23 | Season sim by resampling history (see notes below) | Medium |

#### Notes from reading the code (shallow-cloned and read; nothing was run)
- **NFL-DFS-Tools** (all in `src/nfl_gpp_simulator.py`): **this is the closest template for us.**
  - Per-player fantasy-point distributions come from fitted families chosen by AIC/BIC (exgaussian, skew-normal, gamma, lognormal, NB, ZIP...). They are fit per position x projection window, then rescaled to the projected mean and std.
  - Correlation lookup order: custom overrides, then empirical correlations by (pos1, pos2, same team, projection windows) loaded from YAML, then a hardcoded position matrix. Hardcoded values include QB-WR 0.36, QB-TE 0.35, QB-oppQB 0.23, QB-oppDST -0.30, same-team WR-WR 0.00.
  - Sampling: build the correlation matrix for the whole game, repair it to positive definite (eigenvalue clipping), take the Cholesky factor, then reorder each player's samples by rank to match. This is a Gaussian copula by rank reordering, which the code calls Iman-Conover. It clips at mean + 5 SD.
  - Percentiles are not reported, but they can be read off the sample arrays.
  - **Limitation:** correlation is modeled only between players' *total* fantasy points. Within-player stat correlation is not modeled.
- **ffsimulator** (`R/1_outcomes.R`, `R/4_projections.R`):
  - Builds an empirical distribution by resampling historical weekly scores of players with the same (position, ECR rank +/-2).
  - Rank uncertainty is drawn from Normal(ECR, sd/2), and games played is a Bernoulli draw.
  - No correlation at all; percentiles come only from the empirical Monte Carlo draws.
  - The resampling idea is a useful baseline.
- **pydfs-lineup-optimizer:**
  - A MILP optimizer; its randomness is uniform noise between floor and ceiling or +/- a percentage.
  - Stacking is handled by hard rules, not statistics, and it computes no percentiles.
  - Useful only for building lineups downstream.

### Methods
1. **Gaussian copula (Sklar):** draw z ~ MVN(0, R), u = Phi(z), x_k = F_k^-1(u_k), using the prop-implied marginal F_k for each stat. Discrete stats (receptions, TDs) need a quantile inverse on the step CDF. Estimate R from **Spearman rank correlations** of historical stat lines, since the copula parameter is not the Pearson correlation of the outputs (MathWorks: https://www.mathworks.com/help/stats/simulating-dependent-random-variables-using-copulas.html). Use a correction or tune R by simulation for discrete margins (latent-threshold/tetrachoric issue: https://arxiv.org/pdf/2607.16801). A **t-copula** adds tail dependence (boom games where everything hits together).
2. **Iman-Conover / empirical rank copula:** sample each marginal independently, then reorder the samples to match the rank structure of real historical joint stat lines. It is nonparametric and captures nonlinear dependence. Simple, and robust to a small number of stats.
3. **Conditional / hierarchical generative model:** receptions ~ NegBin or binomial(targets, catch rate), yards | rec = sum of per-catch yards (gamma or empirical from pbp), TDs ~ Poisson/Bernoulli with rate tied to yards and red-zone share. Calibrate its parameters so the implied marginals match the devigged ladders. This handles the rec–yds structure most faithfully (yards are structurally 0 when receptions = 0).
4. **Vine copulas** (pyvinecopulib): flexible pair-copulas. Overkill for 4–6 stats per player at the start.
5. **Distributional ML** (NGBoost, LightGBM quantile, or quantile regression on prop lines plus features) predicting fantasy points directly. This skips explicit correlation modeling. Use it as a **benchmark and calibration layer**, not as the primary model, because it needs historical prop odds as features.
6. **Teammates/opponents** (only needed for DFS or game stacks): extend R across players (QB–WR positive, RB–opposing DST negative, WR1–WR2 slightly negative), conditioned on game total and spread. Commercial DFS sims (SaberSim, Stokastic) publish only high-level descriptions. They appear to use correlated simulations built on game environment, with no copula details made public.

### Data
- **nflverse** (nflreadr / nfl_data_py; `nfl_data_py` is archived in favor of `nflreadpy`): play-by-play 1999+, weekly player stats, snap counts, participation/NGS (2016+). It is CC-BY-style open data, so attribute nflverse. Weekly stats give tens of thousands of player-games per position, which is plenty to estimate 5x5 rank-correlation matrices per position and per role tier (e.g. WR by target-share bucket). Conditioning on implied team total or spread is also feasible.
- **Pro Football Reference:** scraping is restricted by its terms and rate limits. Prefer nflverse.
- **Historical player props: no good free bulk source.** Paid options:
  - The Odds API historical event odds, props from about 2023, one call per event: https://the-odds-api.com/historical-odds-data/
  - SportsDataIO Vault: https://sportsdata.io/historical-odds
  - Apify DraftKings props scraper: https://apify.com/datawright/nfl-player-props-scraper
  - OpticOdds and SportsGameOdds
  - Overview of free sources: https://sportsapis.dev/free-sports-datasets

  **Start logging props yourself now.** For backtesting, use paid history for the last 1–3 seasons.

### Recommended pipeline
1. Devig each ladder (per stat), then fit a monotone, smoothed CDF per stat. Use a discrete pmf for receptions and TDs (anytime-TD price -> P(TD>=1), with Poisson extrapolation to 2+).
2. Correlation: from nflverse weekly stats, compute Spearman correlations of {rec, rec_yds, rush_att, rush_yds, TDs, pass_yds, INT} by position x usage tier. Optionally condition on team implied total. Shrink toward the position-level matrix.
3. Sample: Gaussian copula -> inverse marginal CDFs, N = 10–20k per player. Alternatively, use the conditional model for rec -> yds and the copula for the rest.
4. Score each sample (PPR/half/std), then take empirical quantiles. P(points >= k) is directly 1 - ECDF.
5. Validate on held-out weeks:
   - PIT histogram of actual points under the predicted CDF (should be uniform)
   - Reliability of P(>= k) thresholds
   - CRPS (`properscoring` / `scoringrules`) and pinball loss at the 5/25/50/75/95 percentiles
   - Ablation: independent vs copula vs conditional. Independence will show **too narrow** a spread, because positive rec/yds/TD correlation fattens the tails.
6. If PIT is miscalibrated, fit a post-hoc isotonic or quantile-mapping recalibration on the backtest set.

### Sources
- https://the-odds-api.com/historical-odds-data/
- https://sportsdata.io/historical-odds
- https://sportsapis.dev/free-sports-datasets
- https://apify.com/datawright/nfl-player-props-scraper
- https://www.mathworks.com/help/stats/simulating-dependent-random-variables-using-copulas.html
- https://www.sigmaxl.com/DSIM-Menu-InputCorrelations.html
- https://arxiv.org/pdf/2607.16801
- https://github.com/nflverse/nflreadr , https://github.com/nflverse/nfl_data_py

---

## Q4: Doing this for $0

Added 2026-11-15. The constraint is no paid data. Each claim below is marked **verified** (I called the API or downloaded the file myself) or **from docs or search**.

### 1. Kalshi prediction-market props. Best free source (verified)
Kalshi is a CFTC-regulated exchange. Its public REST API (`https://api.elections.kalshi.com/trade-api/v2`) serves market data with **no API key or account needed for reads**. It lists NFL player-prop series that are already structured as X+ ladders:

| Series | Example market | Archived (settled, before ~Sep 16 2026) | Earliest |
|---|---|---|---|
| `KXNFLRECYDS` | "Jaxon Smith-Njigba: 190+ receiving yards" | 11,711 markets (+16.7k settled since) | 2025-10-07 |
| `KXNFLRSHYDS` | "Tyler Allgeier: 10+ rushing yards" | 5,488 | 2025-10-07 |
| `KXNFLPASSYDS` | "Fernando Mendoza: 75+ passing yards" | 3,841 | 2025-10-07 |
| `KXNFLREC` | "Michael Mayer: 1+ receptions" | 8,656 | 2025-10-28 |
| `KXNFLANYTD` | anytime TD (incl. a "no TD" market) | 5,482 | 2025-09-05 |

Other series exist too: `KXNFLPASSATT`, `KXNFLRSHATT`, `KXNFLDSTTD` (D/ST TD), `KXNFLTOTALTD`, ladder variants `KXNFLLADDERREC` and `KXNFLLADDERRSHYDS`, and "protected" variants. I didn't find a passing-TD or INT series in a quick scan; check for one with `GET /series?category=Sports`.

Why it fits this project:
- **Two-sided prices on every rung.** Each X+ market has a yes bid and a yes ask, so the **mid-price is close to a fair probability**. That removes most of the one-sided devig problem from Q2. Exchange fees are small compared with sportsbook vig on alt lines.
- **Free history.** Use `GET /historical/markets?series_ticker=…` for markets settled before the cutoff, which was about 2026-09-16 when I checked (`GET /historical/cutoff`). Use `GET /markets?series_ticker=…&status=settled` for markets settled since. `GET /historical/markets/{ticker}/candlesticks?period_interval=60` returns **hourly yes_bid / yes_ask / last-price candles**. I pulled real hourly bid/ask for a Sep 2026 receiving-yards rung. Markets settle yes or no, so they come with labelled outcomes.
- That gives about **1.2 seasons of free, labelled, two-sided ladders** (Oct 2025 → now). That's enough to backtest calibration, check the copula aggregation, and compare with independent-stats baselines.

Caveats:
- **Liquidity is uneven.** Far rungs often show bid 0.00 / ask 1.00 or wide spreads. Weight rungs by tightness of spread and volume, and drop quotes that are only one side. Isotonic + parametric fit (Q2 steps 4–5) handles the rest.
- **Coverage is thinner than at sportsbooks** for depth players. No kicker or DST yardage markets, apart from a D/ST TD series.
- Prediction-market prices may be **less sharp than** Pinnacle/Circa. Use them as the main source and validate against outcomes.
- I didn't check Kalshi's API terms. Read them before redistributing data, though personal research use is the normal case. Polymarket's public API (`us_ex` / `polymarket` in The Odds API's list) may be a second free exchange source; not checked.

### 2. The Odds API free tier, for live sportsbook ladders (from docs)
- Free "Starter" plan: **500 credits/month**. Event-odds cost = markets × regions per call (≈10 bookmakers = 1 region). The pricing page lists "All betting markets" for Starter. It doesn't say outright that player props are included, so test with one call.
- `_alternate` milestone keys exist: `player_reception_yds_alternate`, `player_rush_yds_alternate`, `player_pass_yds_alternate`, `player_receptions_alternate`.
- Budget: ~16 games × (4 alt markets + 4 main O/U markets) × 1 book ≈ **128 credits per weekly snapshot**, so ~4 snapshots/month. That's one pre-kickoff snapshot per week of FanDuel ladders plus the main lines needed to calibrate the vig. Main lines alone (~64/week) leave room for a second book.
- **Historical odds are paid-only.** Use it only for live snapshots that you log yourself.
- It also has a `us_dfs` region (PrizePicks, Underdog, Pick6, ParlayPlay, Dabble). Pick'em lines are set near the **median**, so they work as an almost vig-free 50% quantile per stat. The Odds API says the odds there are "indicative only". PrizePicks "demons/goblins" show up as `_alternate` markets.

### 3. nflverse closing game lines. Free history to 2008 (verified)
`https://github.com/nflverse/nfldata/raw/master/data/games.csv` has `spread_line`, `total_line`, moneylines and over/under odds for every game. **Complete from 2011**, partial in 2008–2010, and filled for 2026 games played so far. Implied team total = total/2 ± spread/2.

This allows a **prop-free backtest of the aggregation layer**, with no prop data back to 2011:
- Model each player's stat means from team implied total × usage shares (targets, carries, snap share from nflverse).
- Feed those means into the same marginal-distribution + copula + Monte Carlo pipeline.
- Check calibration (PIT, CRPS) over ~15 seasons.

This tests the correlation structure and distribution families on far more data than any prop history would.

It's also the fallback for players without props, and a way to model **kicker and DST** (team totals → points allowed, XP and FG volume).

### 4. Options I'd avoid
- **Scraping FanDuel/DraftKings directly.** Their props render client-side behind rotating tokens and bot detection. Sources say it's hard, it breaks often, and it likely violates their terms.
- **Undocumented PrizePicks/Underdog endpoints.** They're unofficial, can change without notice, and may conflict with the sites' terms. The Odds API's `us_dfs` region is the sanctioned route.
- **Kaggle or GitHub prop datasets.** The search found none with player props. Free archives such as SportsOddsHistory have game lines only.

### Revised $0 plan
1. **Primary market data: Kalshi.** Write a fetcher for the NFL prop series: current ladders, plus historical markets and pre-kickoff candles.
2. **Devig:** mid-price per rung; drop or down-weight wide or one-sided quotes; then isotonic + parametric fit (Q2 steps 4–6). The logit-shift vig model is needed only for sportsbook data.
3. **Correlation:** Spearman matrices from nflverse weekly stats by position, as in Q3.
4. **Backtest:** (a) the aggregation layer on 2011–2025 with game-line-driven means; (b) the full prop-driven pipeline on Kalshi Oct 2025 → now.
5. **Optional cross-check:** a weekly The Odds API free-tier snapshot of FanDuel alt ladders + main lines, logged locally, to compare Kalshi against sportsbook prices from here on.

### Sources
- Kalshi API (called directly): `https://api.elections.kalshi.com/trade-api/v2` — `/series`, `/markets`, `/historical/markets`, `/historical/cutoff`, `/historical/markets/{ticker}/candlesticks`
- Kalshi NFL props news: [Legal Sports Report](https://www.legalsportsreport.com/241859/kalshi-self-certifies-more-nfl-prop-markets/), [CasinoBeats](https://casinobeats.com/2025/09/17/kalshi-self-certifies-new-nfl-player-props-markets/)
- The Odds API: [v4 guide](https://the-odds-api.com/liveapi/guides/v4/), [markets](https://the-odds-api.com/sports-odds-data/betting-markets.html), [bookmakers/regions](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html), [pricing](https://the-odds-api.com/#get-access)
- nflverse games file (downloaded): https://github.com/nflverse/nfldata/raw/master/data/games.csv
- Scraping difficulty / free datasets: [OddsPapi on props](https://oddspapi.io/blog/?p=3871), [sportsapis.dev free datasets](https://sportsapis.dev/free-sports-datasets), [SportsOddsHistory](https://www.sportsoddshistory.com/about-us)

---

## Suggested next steps
1. Write a small Kalshi fetcher. Pull every settled `KXNFLRECYDS` market for one week plus its last pre-kickoff hourly candle, and check spread width and coverage depth.
2. Prototype the devig on one player's Kalshi receiving-yards ladder: mid-price, isotonic, then a gamma or lognormal fit. Compare the median with that week's sportsbook main line.
3. Use nflreadpy to compute Spearman correlation matrices of the stats for each position.
4. Build a copula Monte Carlo for one player. Backtest the aggregation layer on 2011–2025 using game lines, then the full pipeline on Kalshi's 2025–26 history.
