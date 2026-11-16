# Research: choosing a Stat Distribution family per Scoring Stat

Researched 2026-11-15 by an Opus 5.5 subagent. Follows on from RESEARCH.md Q2 (devig Ladders → isotonic → parametric survival fit). Every number here comes from scripts that were actually run on nflverse data. Scripts and intermediate CSV/JSON files are in the session scratchpad (`distwork/`, `data/`), which is temporary; nothing else was written to the project.

## TL;DR

| Scoring Stat | Family (shape) | Free params on a Ladder | Mean–dispersion relation (fallback) |
|---|---|---|---|
| Receiving yards (WR/TE/RB) | **Two-part**: point mass at 0 (= P(0 receptions)) + **shifted gamma** on nonzero yards (lognormal for RB is equally good) | location m, scale sd (2) | sd_nz ≈ a·m^b, b ≈ 0.62–0.81; overall Var ≈ 20–26 × mean |
| Rushing yards (RB, QB) | Two-part, **shifted gamma** on nonzero yards | 2 | sd_nz ≈ 5.3·m^0.48 (RB), 2.5·m^0.64 (QB) |
| Passing yards | **Normal** on nonzero yards plus a small P(0) for early exits. Skew is ~0, slightly left. | 2 | sd ≈ 75–84, nearly flat in the mean |
| Receptions | **Negative binomial** (overdispersed, var/mean 1.3–1.6) | mean (+ size if ≥3 rungs) | size r = r0·μ^d (WR 0.83·μ^1.85, TE 1.61·μ^1.45, RB 1.53·μ^1.07) |
| Passing TDs | **Binomial(n=11, μ/11)**, slightly *under*dispersed. Poisson is a close second. | 1 | var = μ(1 − μ/11) |
| Interceptions | **Poisson** (NB with r ≈ 7 is marginally better) | 1 | var ≈ 1.1·μ |
| Rush+rec TDs (anytime TD) | **Poisson**, λ = −ln(1 − p_anytime) | 1 | var = μ; P(2+) is matched within 0.5 pp |

Main findings:
1. **Normal is wrong for every yards stat except passing yards.** Its ΔAIC is 1,100–4,500 against the best family, and it puts 7% instead of 5% above P95. Among the right-skewed families (gamma, lognormal, skew-normal) the differences are small, about 0.01 nats/obs. **Shifted gamma** is the safest default. Lognormal gives slightly too thin a top tail (3.8–4.3% above the fitted P95 on test data, against 4.4–4.8% for gamma).
2. Dispersion follows a **variance ∝ mean** law. For yards the index of dispersion Var/mean is nearly constant within a position: WR rec 24–29, TE rec 20–23, RB rec 20–22, RB rush 19–30, QB rush 14–20. So sd ≈ √(φ·μ), and the fitted exponents b are 0.37–0.47 for the full outcome. This is what a compound "N catches × yards per catch" process predicts.
3. Players differ little in dispersion once the mean is known. The persistent player-level sd multiplier has a spread (sd of log) of only **0.05–0.15**. A 1-parameter fit (location only, with scale from the relation) is therefore already accurate. A 2-parameter fit only beats it when the Ladder has ≥3 Rungs *and* the player really departs from the relation by 20% or more.
4. Recommended fit: **2-parameter WLS in logit space, continuity-corrected, plus a Gaussian prior on log sd centred on the relation** (prior sd ≈ 0.15). It does as well as the 1-parameter fit on thin Ladders and as well as the free fit on rich ones. With only a Main Line, solve for the location with the scale from the relation.

---

## Data and method

- **Ground truth:** nflverse weekly player stats (`nflreadpy.load_player_stats`), 2015–2025 REG+POST, positions QB/RB/WR/TE.
  - Games where the player took offensive snaps but recorded no stats are **missing from player_stats**. I added them back from nflverse snap counts (pfr→gsis id map) as all-zero games: 2,845 WR, 4,445 TE, 1,015 RB and 140 QB player-games.
  - This matters: a prop has action once the player takes a snap, so those zeros are real outcomes.
- **Pre-game expectation proxy (μ).** The player's history is used only when it comes *before* the game:
  - trailing 8-game and 4-game means of the stat and of its usage (targets, carries or attempts),
  - season-to-date mean,
  - trailing snap share,
  - implied team total from the nflverse closing spread and total.
  - These are combined by OLS per position and stat, fit leave-one-season-out. Correlation with the outcome is 0.41–0.61, against 0.32–0.60 for a plain trailing mean.
  - The global models were refit on 2015–2021 and scored on 2022–2025 (out of sample).
- **Sample ("prop-eligible" players):**
  - QB: trailing attempts ≥ 20.
  - RB: rushing μ ≥ 25.
  - WR/TE: receiving yards μ ≥ 20.
  - RB receiving: μ ≥ 10. Receptions: μ ≥ 2 (RB ≥ 1.5).
  - TDs: μ ≥ 0.15.
  - That leaves 5.7k–17.6k player-games per segment.
- **Two analyses:**
  - (A) Within ~950–2,900-game buckets (sextiles of μ), fit each family freely by MLE and compare by AIC and by randomized-PIT tail coverage.
  - (B) A global mean-conditional model per family: μ drives the location and the dispersion follows a fitted power law. Scored by test log score and PIT coverage.
- **Likelihoods:** all families are evaluated as **integer pmfs**. Continuous families are discretized, P(Y=y) = F(y+½) − F(y−½), so AIC is comparable with NB and Poisson. For yards, the shapes are compared on the **nonzero part** (zero-truncated likelihood), because the zero spike is handled separately (see below).

## Q1a. Zeros and negatives

| Segment | n | % yards < 0 | % yards = 0 | % zero usage (0 rec / 0 carries / 0 att) | % zero yards *with* usage |
|---|---|---|---|---|---|
| QB pass yds | 5,705 | 0.07 | 2.72 | 2.10 | 0.61 |
| QB rush yds | 3,738 | 6.07 | 9.90 | 7.76 | 2.17 |
| RB rush yds | 8,714 | 0.91 | 2.85 | 2.27 | 0.57 |
| RB rec yds | 9,050 | 2.41 | 19.36 | 18.36 (0 rec) | 1.03 |
| WR rec yds | 17,625 | 0.39 | 10.74 | 10.54 (0 rec) | 0.22 |
| TE rec yds | 5,946 | 0.18 | 8.86 | 8.58 (0 rec) | 0.29 |

The minimum is −13 yards (RB rec). In the 10-bucket table, P(0) falls from 31% to 1% across WR buckets, from 36% to 4% for RB rec, and from 9% to 0% for RB rush.

**How to handle 0 receptions → 0 yards:**
- Receiving yards is a **two-part (hurdle) distribution**: P(Y=0) = π₀, and nonzero yards follow a shifted continuous family, zero-truncated.
- 98–99% of zero-yard games are 0-catch games. Only 19 of 67k games had 0 receptions but nonzero yards, which is an nflverse data artefact.
- So π₀ ≈ P(receptions = 0). Take it from the **receptions** Stat Distribution (NB, below) when a receptions market exists. That links the two markets consistently and gives the Q3 copula its conditional structure (receptions → yards).
- Without a receptions market, use the fitted logistic π₀(μ) = expit(c0 + c1·ln μ) in the table in Q2.
- Yards Ladders start at about 25+ or 40+, so they carry almost no information about π₀. π₀ must come from outside the Ladder.
- NB receptions slightly *under*-predict 0-catch games for high-volume WRs (observed vs fitted P(0) = 4.4% vs 3.2% at μ≈4). The difference is in-game injury exits. A small extra point mass (≈ +1 pp) is defensible.

Negative yards are rare except QB rushing (kneels and scrambles for a loss). Shifting the continuous part by s handles them. The fitted shifts are WR 4, TE 2, RB rush 7, RB rec 8, QB rush 6 (gamma). For safety, use a fixed **s = 10** for rushing and RB receiving and **s = 5** for WR/TE receiving, and floor the pmf at 1e-6 so a −12-yard kneel game cannot break a likelihood. Ladder Rungs never sit near 0, so s has essentially no effect on fitted Fair Probabilities.

## Q1b. Which family? Within-bucket comparison (analysis A)

Sum over 6 μ-buckets of ΔAIC against the best family in each bucket (0 = best everywhere). Yards are compared on the nonzero part.

| Segment | normal | skew-normal | gamma+shift | lognormal+shift | Weibull+shift | NB on yards+shift |
|---|---|---|---|---|---|---|
| QB pass yds | **15** | 21 | 26 | 26 | 89 | 1,069 |
| QB rush yds | 1,142 | 49 | 71 | **21** | 239 | 77 |
| RB rush yds | 2,307 | 108 | 78 | **63** | 252 | 115 |
| RB rec yds | 2,954 | 264 | 246 | **22** | 802 | 282 |
| WR rec yds | 4,528 | **130** | 164 | 217 | 448 | 231 |
| TE rec yds | 1,668 | 59 | **48** | 109 | 106 | 51 |

| Segment | Poisson | NB | Binomial(n free) |
|---|---|---|---|
| QB pass TD | 67 | 80 | **4** |
| QB INT | 22 | **3** | 44 |
| RB rec | 809 | **0** | 1,060 |
| WR rec | 1,033 | **0** | 1,546 |
| TE rec | 321 | **0** | 500 |
| RB rush+rec TD | 12 | **5** | 28 |
| WR rush+rec TD | 10 | 19 | 9 |
| TE rush+rec TD | **0** | 12 | 11 |

Tail coverage within buckets (mean share of observations below the fitted P5 / above the fitted P95, target 5% / 5%):
- **Normal** on rec/rush yards: 0.0–0.5% below P5 and 7.0–7.3% above P95. It gets both tails wrong.
- **Skew-normal, gamma and lognormal:** all within about 1 pp of the targets.
  - Lognormal has the lightest top tail: 0.4–0.9% above P99 against a 1% target.
  - Skew-normal has the heaviest: 1.3–2.0% above P99.
- **Receptions:** Poisson puts 8–9% above P95 and 8–9% below P5. NB is within 1 pp.

### Analysis B: global mean-conditional models, out of sample (test 2022–2025)

Test log score per player-game (higher is better) and PIT tail shares. The dispersion relation is fitted on 2015–2021.

| Segment | normal | gamma | lognormal | skew-normal | best family: share < P5 / > P95 / > P99 |
|---|---|---|---|---|---|
| QB pass yds | −5.696 | −5.696 | −5.696 | −5.696 | normal: 5.5 / 3.1 / 0.6% |
| QB rush yds | −4.196 | −4.078 | −4.081 | **−4.071** | gamma: 5.4 / 4.7 / 0.9% |
| RB rush yds | −4.892 | **−4.783** | −4.793 | −4.795 | gamma: 3.9 / 4.8 / 0.8% |
| RB rec yds | −3.908 | −3.781 | **−3.772** | −3.781 | gamma: 3.2 / 4.7 / 1.0% |
| WR rec yds | −4.624 | **−4.505** | −4.514 | −4.507 | gamma: 5.0 / 4.4 / 0.7% |
| TE rec yds | −4.554 | **−4.443** | −4.453 | −4.445 | gamma: 4.4 / 4.5 / 0.6% |

| Segment | Poisson | NB (r = r0·μ^d) | Binomial(n) | Notes |
|---|---|---|---|---|
| QB pass TD | −1.4220 | −1.4238 | **−1.4170** (n=11) | var/mean 0.75–1.06, falling with μ |
| QB INT | −1.0823 | **−1.0803** | −1.0827 | var/mean 1.03–1.22 |
| RB rec | −1.9325 | **−1.9097** | −1.9389 | Poisson > P95: 7.7%; NB: 3.8% |
| WR rec | −2.1819 | **−2.1501** | −2.1922 | Poisson > P95: 7.6%; NB: 4.6% |
| TE rec | −2.1632 | **−2.1327** | −2.1734 | NB still 6.3% > P95, so TE upper tail is heavy |
| RB/WR/TE TD | −0.788 / −0.654 / −0.631 | same | same | Poisson is enough; fitted P(2+) is within 0.5 pp of observed in every bucket |

**Readings:**
- **Passing yards are symmetric.** The skew-normal α is −0.5, so the skew is slightly to the left from in-game exits. Gamma and lognormal fits run off to an infinite shift, i.e. they become normal.
- **Passing TDs are underdispersed** relative to Poisson. This is why Binomial(11, μ/11) wins. Poisson is acceptable: it over-states P(4+) a little.
- **Receptions are overdispersed relative to Poisson**, even though receptions given targets is binomial (underdispersed). The extra variance comes from target volume, so "binomial(targets, p)" with unknown targets collapses to an NB-type mixture. Binomial with a fixed n is the worst count model.

## Q2. How dispersion scales with the mean

### Full outcome (including zeros), sd = a·mean^b, from 10 μ-deciles

The "proxy" columns bucket on the pre-game μ and give an upper bound, because they include the proxy's own error. The "oracle" columns bucket on *realized* usage (actual targets, carries or attempts), which a book cannot know, and give a lower bound. A sportsbook's implied conditional sd should lie between the two.

| Segment | mean range | a (proxy) | b (proxy) | sd at mid-μ, proxy | sd at mid-μ, oracle | Var/mean (min–median–max) |
|---|---|---|---|---|---|---|
| QB pass yds | 142–284 | 1630 | −0.55 | 79 | 57 | 20–26–88 |
| QB rush yds | 8–47 | 3.83 | 0.53 | 17.3 | 12.8 | 13.5–17.2–19.8 |
| RB rush yds | 25–80 | 8.00 | 0.37 | 34.8 | 22.1 | 19.5–24.1–30.2 |
| RB rec yds | 10–36 | 4.86 | 0.47 | 18.7 | 13.8 | 19.6–20.3–21.8 |
| WR rec yds | 19–80 | 6.51 | 0.44 | 33.5 | 25.4 | 24.4–26.3–28.9 |
| TE rec yds | 22–64 | 5.14 | 0.47 | 27.0 | 19.3 | 19.9–21.4–23.3 |
| QB pass TD | 0.7–2.1 | 0.99 | 0.35 | 1.13 | — | 0.75–0.90–1.06 |
| QB INT | 0.50–0.78 | 1.01 | 0.41 | 0.89 | — | 1.03–1.09–1.22 |
| RB rec | 1.6–4.4 | 1.25 | 0.49 | 1.90 | — | 1.37–1.50–1.67 |
| WR rec | 2.1–6.3 | 1.33 | 0.41 | 2.25 | — | 1.27–1.40–1.55 |
| TE rec | 2.1–5.5 | 1.25 | 0.44 | 2.13 | — | 1.21–1.36–1.50 |
| RB / WR / TE TD | 0.14–0.82 | 0.98 / 0.95 / 0.98 | 0.46 / 0.47 / 0.49 | — | — | ≈1.0 (Poisson) |

The negative b for passing yards comes from low-μ QBs. Those are backups or fragile starters, so the bottom decile has 18% zeros and sd 112. For healthy starters with μ ≥ 190, sd is flat at 75–81.

**How much is the upper bound inflated by proxy error?**
- The persistent part of the proxy error was measured as the within-player-season covariance of residuals. It explains **< 2%** of residual variance in every segment.
- The remaining inflation is transient information that a book has and nflverse lacks: injuries, matchup, the game script implied by the line.
- The proxy's R² is about 0.2. Even if a book reached R² 0.30, the conditional sd would only be about 5–10% smaller.
- **Working assumption: the book-implied sd ≈ 0.92–0.95 × the proxy relation.** Re-estimate a and b from rich devigged Ladders (Kalshi 2025–26) before trusting the main-line-only fallback.

### Two-part model parameters (fit 2015–2021; these are the recommended fallback parameters)

- P(0) = expit(c0 + c1·ln μ).
- Nonzero part: mean m = μ / (1 − π₀) and sd_nz = a·m^b, using the family's shift s.

| Segment | family | c0 | c1 | a | b | shift s | example: μ → (π₀, m, sd_nz) |
|---|---|---|---|---|---|---|---|
| WR rec yds | gamma | 6.75 | −2.48 | 3.00 | 0.622 | 4.1 | 30 → (.159, 35.7, 27.7); 60 → (.033, 62.0, 39.1); 80 → (.016, 81.3, 46.3) |
| TE rec yds | gamma | 6.99 | −2.71 | 2.33 | 0.678 | 1.8 | 25 → (.148, 29.4, 23.0); 40 → (.046, 41.9, 29.4); 55 → (.020, 56.1, 35.8) |
| RB rec yds | lognormal | 4.24 | −2.02 | 1.62 | 0.808 | 11.7 | 10 → (.398, 16.6, 15.7); 20 → (.140, 23.3, 20.6); 35 → (.050, 36.8, 29.9) |
| RB rec yds | gamma | 4.28 | −2.04 | 1.86 | 0.739 | 8.0 | (alternative; test log score −3.781 against −3.772) |
| RB rush yds | gamma | 9.29 | −3.46 | 5.34 | 0.479 | 7.1 | 35 → (.046, 36.7, 30.0); 55 → (.010, 55.6, 36.7); 80 → (.003, 80.2, 43.7) |
| QB rush yds | gamma | 2.08 | −1.56 | 2.53 | 0.642 | 5.7 | 10 → (.181, 12.2, 12.6); 25 → (.050, 26.3, 20.7); 45 → (.021, 46.0, 29.5) |
| QB pass yds | normal | 15.88 | −3.70 | 514 | −0.340 | — | 200 → (.024, 205, 84); 240 → (.012, 243, 80); 280 → (.007, 282, 76) |

**Count dispersion:**

| Segment | Family | Fitted relation | Values |
|---|---|---|---|
| WR receptions | NB | r = 0.83·μ^1.85 | μ = 3/4/5/6 → r = 6.3/10.8/16.4/22.9 and var/mean 1.47/1.37/1.31/1.26 |
| TE receptions | NB | r = 1.61·μ^1.45 | μ = 3/4/5 → r = 7.9/12.0/16.6 |
| RB receptions | NB | r = 1.53·μ^1.07 | μ = 2/3/4 → r = 3.2/5.0/6.8, var/mean ≈ 1.6 throughout |
| QB INT | NB | r = 7.3·μ^0.19 | r ≈ 7, var/mean ≈ 1.1. Poisson is fine. |
| QB pass TD | Binomial | n = 11 | var = μ(1 − μ/11) |
| TDs | Poisson | var = μ | — |

For receptions, r rising like μ² means var ≈ μ + roughly constant (≈ 1.2–1.6). The excess over Poisson is about the same in absolute terms at every mean. If you prefer a single number, NB with var = μ + 1.4 is a good WR/TE approximation.

### Player-level departures from the relation (method of moments on standardized residuals)

| Segment | sd of log(player sd-multiplier), career | per player-season |
|---|---|---|
| QB pass yds | 0.07 | 0.09 |
| QB rush yds | 0.07 | 0.07 |
| RB rush yds | 0.13 | 0.15 |
| RB rec yds | 0.08 | 0.10 |
| WR rec yds | 0.05 | 0.07 |
| TE rec yds | ≈0 | 0.12 |
| RB / WR / TE receptions | 0.11 / 0.03 / 0.01 | 0.13 / 0.07 / 0.05 |

So once the mean is known, the mean–dispersion relation explains almost all of a player's dispersion. Departures of 25% (multiplier 1.25) are 2–3σ events.

## Q3. How many Rungs pin the parameters, and the fitting objective

**Simulation design (`distwork/sim.py`):**
- The truth is the fitted two-part gamma (or lognormal) model above, at WR μ = 60 and RB rush μ = 65.
- The Main Line is placed at the median, e.g. 52.5, and is weighted 3.
- Rungs: none (Main Line only); 40+/80+; 40+/70+/100+; or 25+/40+/60+/80+/100+/125+.
- Noise on the devigged probabilities, in logit space: rung sd 0.10, Main Line sd 0.04, plus a vig-calibration shift common to all Rungs (sd 0.05). 200 simulations per cell.
- Three fits:
  - **F1:** location only, scale from the relation.
  - **F2:** location and scale free.
  - **F2p:** F2 plus the prior λ(ln sd − ln sd_rel)², with λ = τ²/σ_p² = 0.25.

RMSE of the fitted quantiles, in yards (WR rec yds, truth P5/P50/P95 = 7/53/136):

| Ladder | Player follows relation: F1 | F2 | F2p | Player sd 1.25× relation: F1 | F2 | F2p |
|---|---|---|---|---|---|---|
| Main Line only | P50 1.2, **P95 2.2** | — | — | P50 1.4, **P95 27.4** | — | — |
| Main + 2 Rungs | P95 2.6 | P95 10.1 | P95 7.4 | P95 26.3 | P95 17.6 | **P95 12.1** |
| Main + 3 Rungs | P95 2.5 | P95 5.8 | P95 5.0 | P95 23.9 | P95 8.7 | **P95 7.6** |
| Main + 6 Rungs | P95 2.5 | P95 3.3 | P95 3.1 | P95 22.8 | P95 5.2 | P95 5.3 |

RB rush yards with the player's sd at 0.8× the relation gives the same pattern. The P95 error is about 20 for F1 at any number of Rungs, against 5.4 / 3.7 / 2.0 for F2 with 2 / 3 / 6 Rungs.

With the wrong family (truth lognormal, fit gamma, or the reverse), the P95 RMSE is 6–8 yards whatever the number of Rungs, because the two families' upper tails differ by about that much at P95. P50 and the mean stay within about 2 yards. **The number of Rungs matters more than the choice between gamma and lognormal.**

**Conclusions:**
- **Location** is pinned by the Main Line alone (P50 error ≈ 1–1.5 yards).
- **Scale** needs **≥ 2 Rungs on either side of the median**, i.e. Main Line + 3 Rungs spread out, e.g. 40+/70+/100+, to beat the relation. Two Rungs give only a noisy scale. The prior is what keeps that case from losing to F1.
- A **third (shape) parameter** (skew-normal α, or a free shift) is not identifiable from realistic Ladders: there are rarely more than 6 usable Rungs, and their logit noise is about 0.1. Fix the shape per stat and position from the tables above.
- Count stats:
  - **Receptions:** NB has 2 parameters. Fix r from r(μ) unless there are ≥3 Rungs, e.g. 3+/5+/7+ around a 4.5 line.
  - **Passing TDs, INTs and anytime TD:** 1-parameter families, so a single price pins them. Extra Rungs (2+ TDs, 3+ TDs) act as a check on the vig model rather than a fit input.

**Recommended objective.** This is the RESEARCH.md Q2 Step 5 objective plus a prior on the scale:

  θ* = argmin over (m, ln sd) of Σᵢ wᵢ [logit p̂ᵢ − logit S(xᵢ; θ)]² / τ² + (ln sd − ln sd_rel(m))² / σ_p²

where:
- **S(xᵢ; θ) = P(Y ≥ xᵢ).** For the continuous part this is the continuity-corrected S(xᵢ − 0.5): Rung "70+" is evaluated at 69.5. Main Line "over 52.5" means P(Y ≥ 53), evaluated at 52.5.
- **π₀ comes from the receptions model or from π₀(μ)**, not from the Ladder.
- **Weights:** Main Line w = 3. Rungs w = 1, or 1/Var of the isotonic-smoothed p̂ if available. Down-weight Rungs with p̂ < 0.05 or > 0.95, where the logit amplifies price-rounding noise.
- **τ ≈ 0.10** (logit noise of a devigged Rung) and **σ_p ≈ 0.15**: the player-level spread above, plus some room for book-specific dispersion. That makes λ = τ²/σ_p² ≈ 0.45. The simulation used 0.25, and the results barely change between 0.25 and 0.5.
- **Optimiser:** Nelder–Mead over (ln m, ln sd), started at m = Main Line and sd = sd_rel(m). Parametrise by mean and sd, not shape and scale, so the prior is natural and the fit is well conditioned.

**Fallback when only a Main Line exists:**
1. Devig the Main Line to get p_over = P(Y ≥ ⌈L⌉).
2. Set sd = κ·sd_rel(m), with κ ≈ 0.93 until it is calibrated on books' Ladders.
3. Solve the 1-D equation S(⌈L⌉ − 0.5; m, sd(m)) = p_over for m with a bracketing root-finder (brentq). π₀ comes from π₀(μ) or from the receptions market.
4. Expected accuracy if the relation holds: P50 ±1.5 yards and P95 ±2–3 yards. If the player's true dispersion is off by 25%, the P95 error grows to about ±25 yards.

When there is no Main Line but there are Rungs (one-sided only), use the RESEARCH.md joint (θ, c) fit with the same σ_p prior on the scale.

## Q4. Recommendation table

| Scoring Stat | Position(s) | Family | Parameters fitted from Ladder | Fixed / external | Fallback (Main Line only) |
|---|---|---|---|---|---|
| Receiving yards | WR, TE | π₀ point mass + zero-truncated **gamma**(mean m, sd), shift s = 5 | m, ln sd (prior centred on 3.00·m^0.62 for WR, 2.33·m^0.68 for TE, σ_p 0.15) | π₀ = P(rec = 0) from the NB receptions model, else expit(6.75 − 2.48 ln μ) (WR) or expit(6.99 − 2.71 ln μ) (TE) | Root-find m with sd = 0.93·sd_rel(m) |
| Receiving yards | RB | π₀ + zero-truncated **lognormal** (gamma is equivalent), shift s = 10 | m, ln sd (prior 1.62·m^0.81) | π₀ from NB receptions, else expit(4.24 − 2.02 ln μ); π₀ is large (15–40%) | Same |
| Rushing yards | RB | π₀ + zero-truncated **gamma**, s = 10 | m, ln sd (prior 5.34·m^0.48) | π₀ = expit(9.29 − 3.46 ln μ) (≤ 5%) | Same |
| Rushing yards | QB | π₀ + zero-truncated **gamma**, s = 10 (skew-normal is marginally better; a heavy kneel/negative tail) | m, ln sd (prior 2.53·m^0.64) | π₀ = expit(2.08 − 1.56 ln μ) | Same |
| Passing yards | QB | π₀ + **normal**(m, sd) | m, ln sd (prior: sd 75–84, i.e. 514·m^−0.34) | π₀ = expit(15.9 − 3.70 ln μ) (1–2% for starters) | Root-find m with sd ≈ 78 × 0.93 |
| Receptions | WR, TE, RB | **Negative binomial**(μ, r) | μ; r only if ≥ 3 Rungs | r = 0.83·μ^1.85 (WR), 1.61·μ^1.45 (TE), 1.53·μ^1.07 (RB). Optionally add +1 pp to P(0) for high-volume WRs. | Solve μ from the Main Line with r(μ) |
| Passing TDs | QB | **Binomial(11, μ/11)** (Poisson acceptable) | μ (1 parameter); extra Rungs over-determine it, so use them to check the vig | n = 11 | One price pins μ |
| Interceptions | QB | **Poisson** (or NB with r = 7) | λ from the 0.5 line: λ = −ln P(0 INT) | — | Same, 1 parameter |
| Rush + rec TDs (anytime) | RB, WR, TE | **Poisson** | λ = −ln(1 − p_anytime) | P(2+) follows from λ, matched within 0.5 pp | Same |

Treat rush TD and rec TD as a single Poisson for the anytime market. Split λ by the player's historical rush:rec TD share when Scoring Rules weight them differently (they usually do not).

## Caveats

- **The expectation proxy is not the book's line.** Shapes and dispersion are measured conditional on an nflverse-only forecast with R² ≈ 0.2. The proxy-error share that persists within a player-season is under 2%, but the transient part is not measurable here. The dispersion relations are therefore upper bounds, about 5–10% too wide; the realized-usage bracket gives a hard lower bound. Calibrate the relations, κ, and the gamma-versus-lognormal choice on devigged Kalshi or sportsbook Ladders with outcomes. Kalshi ladders from Oct 2025 are free (see RESEARCH.md Q4). That is the single most valuable validation step.
- **Bucket mixtures bias towards heavier tails.** Each bucket pools players whose true means differ, which fattens the upper tail and favours skew-normal and gamma over lognormal. Against the book's own conditional, lognormal's lighter tail might fit better than it does here.
- **The sample** is starters and prop-eligible players defined by my thresholds, not by which players books actually priced.
- **Missing games:** zero-stat games were recovered via snap counts (2015+) and an id map. A few may still be missing where pfr↔gsis ids do not map.
- **Snaps and voids:** if a player is inactive the prop voids, so those games are excluded here, which is correct. In-game injury exits are included, which is also correct (the prop stands). They are the source of the passing-yards left tail and the extra 0-catch mass.
- **Fitted shifts are small** (2–8 yards). The recommended fixed shifts (5 and 10) slightly change the far-left tail but not any Ladder-relevant probability.
