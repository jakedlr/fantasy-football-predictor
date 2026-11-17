# Kalshi Backtest (S5.2)

Run on 2026-11-16 over every settled Kalshi player-game from September 2025 to 2026 Week 10: **7,165 player-games** (434 Games, 580 players), 7,128 scored. Reproduce with:

```bash
python -m ffp.sources.kalshi pull --root data/kalshi --since 2025-09-01
python scripts/backtest_kalshi.py --root data/kalshi --jobs 16
```

## After the approved recalibrations (S5.4–S5.7)

All four proposals below were approved and applied, then the full Backtest was rerun on the same 7,165 player-games. The tables at the end are from that rerun; the sections between are the original results that led to the changes.

| Player-games | n | P5–P95 (90%) | P25–P75 (50%) | above P95 | bias |
|---|---|---|---|---|---|
| all, before | 7,128 | 80.1% | 42.4% | 17.5% | +2.24 |
| **all, after** | 7,128 | **91.3%** | **54.6%** | 4.9% | −0.24 |
| every core stat priced, 2026 (unchanged inputs) | 2,092 | 90.7% (was 91.0%) | 51.7% | 5.0% | +0.03 |
| gaps filled by Estimated Stats, 2025 | 2,931 | 91.0% | 55.2% | 4.8% | −0.30 |
| gaps filled by Estimated Stats, 2026 | 835 | 94.9% | 66.1% | 3.1% | −0.80 |
| still incomplete (nothing to estimate from) | 181 | 81–82% | 36–60% | 17–21% | +1.2 to +3.7 |

- **Estimated Stats (S5.4)** close almost all of the gap. 3,766 of the 3,947 formerly incomplete player-games now have every core stat. Their ranges are, if anything, slightly too wide (P25–P75 covers 55–66%): an estimate is less certain than a market. Estimated receiving yards run about 4 yards high for WRs and TEs; estimated INTs, pass TDs and RB stats are unbiased.
- **Rushing yards (S5.6)**: shift 3 for RB/WR/TE and 7 for QB, and the P(0) relation refitted on the 1,098 RB and 551 QB player-games the market priced (`params/market_calibration.json`). The RB rushing-yards PIT now passes (p = 0.55 in 2026, was 0.007); model P(0) for RBs is 1.7% against 2.1% observed (was 4.7%). Fantasy-point coverage is unchanged, which meets the acceptance rule. Low-volume RBs (mean under 20 yards) still get too much mass below zero (7.8% against 1.6%), on 127 player-games.
- **Interceptions (S5.7)**: pinned on the 1+ price. Fitted P(1+) averages 0.241 against Kalshi's 0.250 and 0.247 observed (was 0.264); reliability improved from 0.0021 to 0.0012.
- **κ (S5.5)**, from the rerun's free fits: RB rushing 0.84, QB passing 0.86, WR receiving 0.94, TE receiving 0.99, QB rushing 1.01, RB receiving 1.07; 0.93 elsewhere. It only affects players priced with a Main Line alone, which Kalshi's Ladders never are, so the Backtest can't show its effect.
- The model still matches Kalshi's fantasy-points markets: log loss 0.673 against 0.674, MAE 6.22 against 6.26.

## Summary

1. **With every core stat priced, the Score Distributions are calibrated.** P5–P95 covers 92.2% of 2025 outcomes and 91.0% of 2026 outcomes (target 90%); P25–P75 covers 50.2% and 51.6% (target 50%). The PIT histograms are flat.
2. **The copula is what makes them calibrated.** Simulating the same Stat Distributions independently covers only 79% and 37%: the intervals come out too narrow.
3. **The model matches Kalshi's own fantasy-points markets.** Log loss on the Over lines is 0.674 for us and 0.675 for the market; mean absolute error on expected points is 6.26 for us and 6.30 for the market.
4. **"Incomplete" players are under-predicted.** A missing stat counts as 0 points, so actual scores run 1.5 points (2026) to 5 points (2025) above the model's mean, and 13% (2026) to 33% (2025) of outcomes land above P95, where 5% should. This is the largest error found.
5. **Each Stat Distribution is calibrated except low-volume rushing yards**, where the model puts too much mass at and below zero.
6. **The fits lose nothing against the prices they are fitted to**: Brier scores of our fitted P(Y ≥ k) and of the raw Kalshi mids agree to the fourth decimal.

## Data

- Kalshi listed 95,226 markets in the twelve series; 82,573 had a kickoff quote fetched; **69,766 Rungs**, 991 fantasy-points lines and 575 fantasy-points means passed the price rule (both sides quoted, spread ≤ $0.10, opened before kickoff, player played and mapped to nflverse).
- The largest drops: receiving-yards Rungs with a wide spread (2,459) or opened in-game (1,572); receptions Rungs with a one-sided book (2,052); TD Rungs with a one-sided book (1,247) or an unmapped player, mostly D/ST (318 in 2026, 947 in 2025).
- Scored player-games: 2025 4,062; 2026 3,066. By position: QB 831, RB 1,774, WR 2,904, TE 1,619.
- 37 player-games were excluded as did-not-play. 398 were scored with an all-zero outcome: the player had offensive snaps but no nflverse stat row.
- **Most of 2025 is incomplete by construction.** Kalshi listed no receptions before Week 8, no passing TDs before Week 12, and no carries or interceptions at all in 2025. 2,973 of the 4,062 player-games from 2025 are flagged "incomplete", against 974 of 3,066 in 2026.

## Headline: fantasy points (Sleeper scoring)

| Player-games | Model | n | P5–P95 (90%) | P25–P75 (50%) | below P5 | above P95 | bias (actual − mean) |
|---|---|---|---|---|---|---|---|
| 2026, every core stat | copula | 2,092 | **91.0%** | **51.6%** | 3.9% | 5.1% | +0.05 |
| 2026, every core stat | independent | 2,092 | 79.2% | 37.3% | 10.1% | 10.7% | +0.07 |
| 2025, every core stat | copula | 1,089 | **92.2%** | **50.2%** | 3.3% | 4.5% | −0.45 |
| 2025, every core stat | independent | 1,089 | 78.8% | 37.8% | 12.8% | 8.4% | −0.41 |
| 2026, incomplete | copula | 974 | 86.3% | 51.5% | 0.6% | 13.0% | +1.46 |
| 2025, incomplete | copula | 2,973 | 66.0% | 30.1% | 1.5% | 32.5% | +5.02 |

- 2026 is fully out of sample: no parameter was fitted on it. 2025 overlaps the seasons the correlation matrices and Base Rates were fitted on (2021–2025), and its complete player-games score the same as 2026's.
- By position in 2026 (all player-games, incomplete included): QB 87.4%, RB 88.9%, TE 89.2%, WR 90.6%.
- **Model vs market.** On 855 Over lines (KXNFLFFPTS), log loss is 0.676 for the model, 0.678 for the independent baseline and 0.674 for Kalshi's mid. On 574 expected-points contracts (KXNFLFFPTSLADDER), MAE is 6.22 for the model and 6.26 for the market, with a bias of −0.18 against the market's −0.54.

## Per-stat calibration

Randomized PIT, χ² test of uniformity over 10 bins; "mean p" is the average fitted P(Y ≥ k) over Kalshi's Rungs.

| Stat | n | PIT χ² p | bias | mean p: ours / Kalshi mid / observed |
|---|---|---|---|---|
| Touchdowns | 7,072 | 0.24 | 0.00 | 0.165 / 0.163 / 0.164 |
| Passing yards | 681 | 0.43 | +2.1 yd | 0.392 / 0.389 / 0.395 |
| Receiving yards | 3,457 | 0.58 | −0.5 yd | 0.344 / 0.345 / 0.340 |
| Receptions | 2,980 | 0.36 | −0.10 | 0.344 / 0.347 / 0.329 |
| Passing TDs | 491 | 0.94 | 0.00 | 0.369 / 0.368 / 0.373 |
| Interceptions | 299 | 0.44 | −0.05 | 0.264 / 0.250 / 0.247 |
| Carries | 396 | 0.82 | −0.31 | 0.491 / 0.492 / 0.482 |
| Rushing yards | 1,666 | **0.02** | −0.4 yd | 0.358 / 0.358 / 0.344 |

- **Rushing yards is the one failure**, and it comes from 2026 RBs (p = 0.007, n = 567): only 6% of outcomes fall in the lowest PIT decile, where 10% should. The model gives too much probability to very low and negative rushing totals (see the shift diagnostic).
- **Where the market was off, the model follows it.** Kalshi's 2025 receptions mids averaged 0.433 against 0.393 observed, and its QB rushing-yards mids 0.290 against 0.247 in 2026. Both are small samples from wide-spread markets, and neither is a fitting error.
- **Interceptions**: the Poisson fit reads 0.264 where the mids say 0.250 and 0.247 happened. The fit residual is the largest of any stat (0.18 logit), from fitting one rate to 1+, 2+ and 3+ prices.

## Diagnostics

**Dispersion (market-implied sd ÷ the relation's sd, free fit, ≥ 4 Rungs).** The Main-Line-only fallback assumes one ratio, κ = 0.93, for every stat.

| Stat, position | n | ratio | p25–p75 |
|---|---|---|---|
| Receiving yards, WR | 1,622 | 0.94 | 0.90–0.98 |
| Receiving yards, TE | 768 | 0.99 | 0.94–1.03 |
| Receiving yards, RB | 622 | 1.06 | 0.96–1.16 |
| Rushing yards, RB | 1,015 | **0.86** | 0.82–0.89 |
| Rushing yards, QB | 431 | 1.03 | 0.92–1.11 |
| Passing yards, QB | 631 | **0.86** | 0.83–0.90 |

- 0.93 is right for WRs. The RB rushing and QB passing relations are about 14% too wide for what the market prices, at every mean.
- With a Ladder the prior barely matters: the free fit and the production fit score the same CRPS on every stat. The ratio only changes players priced with a Main Line alone.

**Rushing yards at and below zero.**

| Position, mean | n | model P(Y < 0) | observed | model P(Y = 0) | observed |
|---|---|---|---|---|---|
| RB, 0–20 yd | 141 | 7.7% | 1.4% | 21.5% | 14.2% |
| RB, 20–40 yd | 290 | 3.0% | 1.7% | 5.1% | 0.7% |
| RB, 40+ yd | 667 | 0.2% | 0.0% | 1.0% | 0.2% |
| QB, 0–20 yd | 339 | 11.4% | 8.0% | 20.8% | 15.6% |
| QB, 20–40 yd | 194 | 2.0% | 3.1% | 4.9% | 3.1% |

- No RB finished at −5 yards or worse; 0.5% of QBs did. The shift of 10 leaves room for outcomes that almost never happen, and the P(0) relation is too high for low-volume RBs.

**Gamma vs lognormal.** Gamma is clearly better for WR receiving yards (PIT p 0.85 against 0.000) and better for TEs. For RB rushing yards lognormal has the flatter PIT (0.52 against 0.03) with the same CRPS. Elsewhere they tie.

**Dependence between stats.** Correlation of the outcomes' PITs, against what the copula produces:

| Pair | n | observed | copula | se |
|---|---|---|---|---|
| WR receptions–receiving yards | 1,504 | 0.79 | 0.76 | 0.03 |
| TE receptions–receiving yards | 751 | 0.82 | 0.78 | 0.04 |
| RB receptions–receiving yards | 666 | 0.70 | 0.70 | 0.04 |
| RB rushing yards–receptions | 661 | −0.05 | −0.04 | 0.04 |
| RB workhorse rushing yards–receptions | 244 | −0.16 | −0.05 | 0.06 |

- The receptions–yards link is reproduced to within about one standard error. Nothing here calls for a conditional receptions→yards model.
- Workhorse RBs show the trade-off research found (−0.16 against the matrix's −0.05, 1.8 standard errors), on 244 player-games.

**Touchdowns.** Kalshi's anytime price is calibrated: 21.5% priced, 21.4% scored (n = 7,020). At 2+ the market says 6.2%, the Poisson model 7.0%, and 6.8% happened. Kalshi's prices are vig-free, so this says nothing about `TD_PRIOR_C`, which devigs DraftKings and FanDuel prices we have no history for.

## Proposed recalibrations

Proposals 1, 2, 3 and 8 were approved and applied (see "After the approved recalibrations"); 4–7 recommend no change.

1. **Fill in a missing core stat instead of counting it as 0.** Evidence: incomplete player-games are under-predicted by 1.5–5 points and 13–33% land above P95. Proposal: when a core stat has no market, use a fallback Stat Distribution (the dispersion relations at a mean taken from the player's other stats or recent nflverse games), keep the "incomplete" flag. Effect: the largest calibration gain available; needs its own design. Until then the flag should be read as "this range is too low".
2. **κ per stat and position** for Main-Line-only fits, in place of 0.93 everywhere: RB rushing yards 0.86, QB passing yards 0.86, WR receiving yards 0.94, TE receiving yards 0.99, RB receiving yards 1.06, QB rushing yards 1.03. Effect: narrower ranges for RBs and QBs priced with a Main Line alone; no change for players with Ladders.
3. **Rushing yards near zero**: refit the RB and QB rushing relations with a smaller shift (research fitted 7 for RBs; no RB in the sample reached −5) and refit the P(0) relation on players the books price. Effect: fixes the one failing PIT and raises P5 slightly for low-volume rushers. Needs a `build_params` rerun and a second backtest to confirm before adopting.
4. **No change: gamma family.** Lognormal is no better overall and worse for WRs. Revisit for RB rushing only if proposal 3 doesn't flatten its PIT.
5. **No change: the copula; no conditional receptions→yards model.** The observed dependence matches the copula's.
6. **No change yet: workhorse-RB override.** The effect is real in direction but 1.8 standard errors on 244 player-games, and fantasy points are already calibrated for RBs. Re-test after the 2026 season.
7. **No change: `TD_PRIOR_C`.** Untestable on vig-free prices. A direct test needs DraftKings/FanDuel prices next to Kalshi's for the same players, which our weekly snapshots will provide.
8. **Minor: interceptions.** The Poisson fit overstates P(1+) by about 1.4 points against the mids. Weighting the 1+ Rung as the anchor would remove it; worth about 0.03 fantasy points per QB.

## Method

For each (Game, player) with usable Kalshi Rungs (`ffp.sources.kalshi.load_rungs`):

1. **Same pipeline as production.** `to_prop_markets` → `predict.fit_player` with `prior_c={"kalshi": 0}`, so each bid/ask mid is the Fair Probability → `predict.score_player` under the `sleeper` preset. This is the same fit rules, Base Rates, TD split and position copula that `ffp predict` uses, with 4,000 samples and a fixed seed.
2. **Baseline.** The same Stat Distributions are simulated a second time with independent stats (`correlation="none"`) and the same seed.
3. **No look-ahead.**
   - The position comes from the nflverse weekly roster row for that week, or the latest row before it.
   - The TD share comes from `load_td_history(before=(season, week))`.
   - The trailing carry share is the mean over the player's previous 2–4 games of that season.
   - The model parameters are not free of look-ahead. Dispersion relations were fitted on 2015–2021 nflverse data, and the correlation matrices and Base Rates on 2021–2025. **2025 games overlap the correlation and Base Rate seasons.** Results are therefore also shown for 2026 alone, which is fully out of sample.
4. **Outcomes** come from `outcomes.load_outcomes`, keyed by (game_id, gsis_id).
   - nflverse player stats have no row for a player who played without recording a stat, such as a blocking TE or a WR who was never targeted. On a 300-player-game trial, 9 of the "missing" rows were players like this; Keon Coleman had 39 offensive snaps. Such a player-game is scored as an all-zero outcome when nflverse snap counts show offensive snaps.
   - A player with neither a stat row nor snaps didn't play: he is counted as `dnp` and excluded.
5. **Metrics** come from `ffp.metrics`:
   - **Per stat:** randomized PIT; CRPS; Brier and reliability of our P(Y ≥ k) at Kalshi's thresholds, next to the raw mid's; the fit residual (RMSE in logit of our S(k) against the mids).
   - **Per player, fantasy points:** P5–P95 and P25–P75 coverage, PIT, CRPS and pinball loss. Our P(points > line) is compared against KXNFLFFPTS, and our mean against KXNFLFFPTSLADDER.

## Full tables (rerun after S5.4–S5.7)

### Counts

Player-games with Kalshi Rungs: 7165 (434 Games, 580 players).

| Season | dnp | ok |
|---|---|---|
| 2025 | 4 | 4062 |
| 2026 | 33 | 3066 |

Scored with an all-zero outcome (offensive snaps, no nflverse stat row): 398.

Scored player-games by position: QB 2025 529, QB 2026 302, RB 2025 1007, RB 2026 767, TE 2025 871, TE 2026 748, WR 2025 1655, WR 2026 1249.

Flags on scored player-games: estimated: receptions ×3139; estimated: receiving yards ×2662; estimated: rushing yards ×956; estimated: interceptions ×529; estimated: passing TDs ×337; incomplete: missing receptions ×178; incomplete: missing receiving yards ×178; estimated: passing yards ×147; estimated: touchdowns ×56; incomplete: missing passing yards ×3; incomplete: missing passing TDs ×3; incomplete: missing interceptions ×3.

### Fantasy points (Sleeper)

| Group | Model | n | P5–P95 cover | P25–P75 cover | < P5 | > P95 | PIT χ² p | CRPS | pinball | bias (y − mean) |
|---|---|---|---|---|---|---|---|---|---|---|
| all | copula | 7128 | 91.3% | 54.6% | 3.7% | 4.9% | 0.000 | 3.27 | 1.321 | -0.24 |
| all | independent | 7128 | 80.9% | 40.5% | 10.2% | 8.9% | 0.000 | 3.34 | 1.364 | -0.21 |
| 2025 | copula | 4062 | 91.2% | 53.6% | 3.9% | 4.8% | 0.020 | 3.45 | 1.393 | -0.30 |
| 2025 | independent | 4062 | 80.4% | 40.0% | 11.2% | 8.4% | 0.000 | 3.52 | 1.437 | -0.28 |
| 2026 | copula | 3066 | 91.5% | 56.0% | 3.5% | 5.1% | 0.000 | 3.03 | 1.225 | -0.15 |
| 2026 | independent | 3066 | 81.5% | 41.3% | 9.0% | 9.5% | 0.000 | 3.10 | 1.268 | -0.12 |
| QB 2025 | copula | 529 | 85.1% | 49.3% | 7.8% | 7.2% | 0.046 | 4.40 | 1.776 | 0.78 |
| QB 2025 | independent | 529 | 80.3% | 42.0% | 9.6% | 10.0% | 0.000 | 4.44 | 1.807 | 0.72 |
| QB 2026 | copula | 302 | 88.1% | 48.7% | 6.3% | 5.6% | 0.385 | 4.25 | 1.712 | 0.09 |
| QB 2026 | independent | 302 | 82.8% | 42.4% | 9.3% | 7.9% | 0.004 | 4.28 | 1.735 | -0.00 |
| RB 2025 | copula | 1007 | 90.3% | 50.7% | 4.6% | 5.2% | 0.213 | 3.52 | 1.419 | -0.40 |
| RB 2025 | independent | 1007 | 82.0% | 43.0% | 10.0% | 7.9% | 0.000 | 3.57 | 1.447 | -0.40 |
| RB 2026 | copula | 767 | 90.5% | 51.4% | 5.0% | 4.6% | 0.745 | 3.02 | 1.221 | -0.11 |
| RB 2026 | independent | 767 | 84.2% | 44.1% | 7.7% | 8.1% | 0.000 | 3.06 | 1.242 | -0.10 |
| TE 2025 | copula | 871 | 94.3% | 58.0% | 1.8% | 3.9% | 0.030 | 2.74 | 1.108 | -0.49 |
| TE 2025 | independent | 871 | 80.9% | 38.0% | 11.5% | 7.6% | 0.000 | 2.82 | 1.153 | -0.44 |
| TE 2026 | copula | 748 | 92.0% | 60.3% | 2.8% | 5.2% | 0.003 | 2.45 | 0.995 | -0.48 |
| TE 2026 | independent | 748 | 81.0% | 38.1% | 9.8% | 9.2% | 0.000 | 2.56 | 1.050 | -0.43 |
| WR 2025 | copula | 1655 | 92.2% | 54.5% | 3.4% | 4.4% | 0.208 | 3.47 | 1.404 | -0.49 |
| WR 2025 | independent | 1655 | 79.2% | 38.5% | 12.1% | 8.7% | 0.000 | 3.56 | 1.462 | -0.44 |
| WR 2026 | copula | 1249 | 92.6% | 58.0% | 2.3% | 5.1% | 0.047 | 3.08 | 1.247 | -0.03 |
| WR 2026 | independent | 1249 | 79.7% | 41.3% | 9.4% | 10.9% | 0.000 | 3.17 | 1.301 | 0.02 |
| 2025 complete | copula | 1089 | 92.3% | 50.1% | 3.3% | 4.4% | 0.399 | 3.66 | 1.478 | -0.47 |
| 2025 complete | independent | 1089 | 78.5% | 37.8% | 13.0% | 8.4% | 0.000 | 3.75 | 1.532 | -0.42 |
| 2025 estimated | copula | 2931 | 91.0% | 55.2% | 4.2% | 4.8% | 0.034 | 3.36 | 1.356 | -0.30 |
| 2025 estimated | independent | 2931 | 81.2% | 40.9% | 10.6% | 8.2% | 0.000 | 3.43 | 1.397 | -0.28 |
| 2025 incomplete | copula | 42 | 78.6% | 35.7% | 0.0% | 21.4% | 0.000 | 4.11 | 1.715 | 3.72 |
| 2025 incomplete | independent | 42 | 76.2% | 31.0% | 0.0% | 23.8% | 0.000 | 4.12 | 1.743 | 3.78 |
| 2026 complete | copula | 2092 | 90.7% | 51.7% | 4.3% | 5.0% | 0.012 | 3.64 | 1.465 | 0.03 |
| 2026 complete | independent | 2092 | 79.2% | 37.3% | 10.3% | 10.6% | 0.000 | 3.72 | 1.516 | 0.05 |
| 2026 estimated | copula | 835 | 94.9% | 66.1% | 2.0% | 3.1% | 0.000 | 1.74 | 0.712 | -0.80 |
| 2026 estimated | independent | 835 | 87.3% | 48.3% | 7.3% | 5.4% | 0.000 | 1.82 | 0.742 | -0.76 |
| 2026 incomplete | copula | 139 | 82.0% | 59.7% | 0.7% | 17.3% | 0.000 | 1.50 | 0.687 | 1.17 |
| 2026 incomplete | independent | 139 | 81.3% | 60.4% | 0.7% | 18.0% | 0.000 | 1.51 | 0.689 | 1.19 |

PIT histograms (% per decile, 0→1):

- all copula: 11 10 11 10 9 9 10 9 10 10
- all independent: 20 11 9 8 7 7 8 8 9 14
- 2025 copula: 11 10 11 10 10 9 10 9 10 9
- 2025 independent: 21 10 9 8 7 7 8 8 9 13
- 2026 copula: 11 10 11 11 9 8 10 9 10 11
- 2026 independent: 20 11 9 8 7 6 8 7 8 15
- QB 2025 copula: 11 7 10 9 9 9 11 9 11 14
- QB 2025 independent: 13 7 11 7 8 8 10 9 11 16
- QB 2026 copula: 11 8 12 12 10 7 9 9 11 12
- QB 2026 independent: 14 9 12 10 9 6 9 7 11 15
- RB 2025 copula: 11 11 11 12 11 8 10 10 9 10
- RB 2025 independent: 18 10 11 9 9 6 9 8 8 13
- RB 2026 copula: 9 12 11 11 9 9 10 10 11 10
- RB 2026 independent: 15 13 9 9 7 8 8 9 9 13
- TE 2025 copula: 12 11 11 10 9 10 10 8 11 7
- TE 2025 independent: 24 12 7 8 6 7 8 6 9 12
- TE 2026 copula: 14 11 11 12 8 8 9 8 9 10
- TE 2026 independent: 27 12 9 7 6 5 7 6 8 13
- WR 2025 copula: 11 11 11 9 9 9 10 10 10 9
- WR 2025 independent: 23 11 8 7 6 8 7 8 8 14
- WR 2026 copula: 11 9 10 11 8 9 10 9 10 12
- WR 2026 independent: 21 11 9 7 6 7 8 7 7 17
- 2025 complete copula: 11 11 12 10 9 10 9 9 10 9
- 2025 complete independent: 22 11 7 8 7 7 7 7 9 14
- 2025 estimated copula: 11 10 11 10 10 9 11 9 10 9
- 2025 estimated independent: 21 10 10 8 7 7 8 8 8 13
- 2025 incomplete copula: 2 2 7 5 2 5 10 10 19 38
- 2025 incomplete independent: 2 5 7 2 5 5 10 10 17 38
- 2026 complete copula: 11 9 11 11 9 8 10 10 11 11
- 2026 complete independent: 19 10 9 8 7 6 8 7 9 16
- 2026 estimated copula: 14 13 10 13 9 9 9 8 8 7
- 2026 estimated independent: 24 14 10 10 7 6 7 6 7 9
- 2026 incomplete copula: 2 3 10 10 6 6 12 5 14 30
- 2026 incomplete independent: 6 10 8 4 1 6 9 13 12 32

### Model vs market on fantasy points

KXNFLFFPTS: P(points > line). Log loss and Brier against the outcome (lower is better).

| Group | n | ours log loss | indep. log loss | market log loss | ours Brier | indep. Brier | market Brier | mean p ours / market / observed |
|---|---|---|---|---|---|---|---|---|
| all | 855 | 0.673 | 0.675 | 0.674 | 0.240 | 0.241 | 0.241 | 0.44 / 0.44 / 0.43 |
| 2026 | 855 | 0.673 | 0.675 | 0.674 | 0.240 | 0.241 | 0.241 | 0.44 / 0.44 / 0.43 |
| QB 2026 | 199 | 0.689 | 0.691 | 0.690 | 0.248 | 0.249 | 0.248 | 0.48 / 0.45 / 0.45 |
| RB 2026 | 224 | 0.654 | 0.656 | 0.653 | 0.230 | 0.230 | 0.230 | 0.44 / 0.43 / 0.43 |
| TE 2026 | 141 | 0.662 | 0.665 | 0.665 | 0.235 | 0.236 | 0.236 | 0.42 / 0.42 / 0.40 |
| WR 2026 | 291 | 0.681 | 0.684 | 0.685 | 0.244 | 0.245 | 0.246 | 0.43 / 0.44 / 0.44 |
| 2026 complete | 839 | 0.673 | 0.676 | 0.675 | 0.240 | 0.241 | 0.241 | 0.44 / 0.44 / 0.44 |
| 2026 estimated | 16 | 0.625 | 0.655 | 0.621 | 0.201 | 0.203 | 0.210 | 0.31 / 0.33 / 0.25 |

KXNFLFFPTSLADDER: expected points (capped 0–100) against actual.

| Group | n | ours MAE | indep. MAE | market MAE | ours bias | market bias |
|---|---|---|---|---|---|---|
| all | 574 | 6.22 | 6.22 | 6.26 | -0.22 | -0.54 |
| 2026 | 574 | 6.22 | 6.22 | 6.26 | -0.22 | -0.54 |
| QB 2026 | 98 | 6.03 | 6.04 | 6.06 | -0.01 | -0.26 |
| RB 2026 | 164 | 6.47 | 6.48 | 6.46 | 0.18 | -0.14 |
| TE 2026 | 87 | 5.46 | 5.44 | 5.61 | -1.40 | -1.63 |
| WR 2026 | 225 | 6.41 | 6.40 | 6.46 | -0.15 | -0.53 |
| 2026 complete | 567 | 6.26 | 6.25 | 6.30 | -0.21 | -0.53 |
| 2026 estimated | 7 | 3.03 | 3.04 | 3.66 | -1.40 | -1.54 |

### Per-stat calibration

PIT χ² p: uniformity of the randomized PIT (10 bins). Rungs: Brier and reliability (lower is better) of our fitted P(Y ≥ k) and of the raw Kalshi mid at Kalshi's own thresholds. Fit residual: RMSE in logit of our S(k) against the mids we fitted to.

| Stat | Group | n | PIT χ² p | PIT % per decile | CRPS | bias | Rungs | Brier ours / mid | reliability ours / mid | mean p ours / mid / observed | fit resid |
|---|---|---|---|---|---|---|---|---|---|---|---|
| TD | all | 7072 | 0.242 | 9 10 10 10 10 10 9 10 10 10 | 0.19 | -0.00 | 10490 | 0.1172 / 0.1172 | 0.0002 / 0.0002 | 0.165 / 0.163 / 0.164 | 0.186 |
| TD | 2025 | 4018 | 0.200 | 10 10 11 10 10 10 9 10 10 10 | 0.20 | -0.01 | 5230 | 0.1370 / 0.1370 | 0.0003 / 0.0003 | 0.198 / 0.195 / 0.194 | 0.144 |
| TD | 2026 | 3054 | 0.868 | 9 10 10 10 10 11 9 10 10 10 | 0.17 | 0.00 | 5260 | 0.0975 / 0.0976 | 0.0002 / 0.0002 | 0.133 / 0.132 / 0.134 | 0.219 |
| TD | QB 2025 | 518 | 0.798 | 11 10 9 9 10 9 10 9 10 12 | 0.16 | 0.02 | 587 | 0.1246 / 0.1245 | 0.0009 / 0.0010 | 0.149 / 0.147 / 0.158 | 0.078 |
| TD | QB 2026 | 301 | 0.815 | 8 12 9 9 11 11 8 11 10 10 | 0.14 | 0.01 | 471 | 0.0872 / 0.0875 | 0.0010 / 0.0011 | 0.106 / 0.106 / 0.110 | 0.222 |
| TD | RB 2025 | 995 | 0.205 | 11 12 11 10 10 10 7 9 10 10 | 0.28 | -0.02 | 1451 | 0.1622 / 0.1620 | 0.0007 / 0.0006 | 0.268 / 0.263 / 0.256 | 0.145 |
| TD | RB 2026 | 763 | 0.542 | 8 11 10 11 9 12 9 9 10 10 | 0.23 | 0.00 | 1546 | 0.1111 / 0.1114 | 0.0007 / 0.0007 | 0.171 / 0.168 / 0.172 | 0.215 |
| TD | TE 2025 | 867 | 0.178 | 9 10 9 12 9 10 9 12 10 10 | 0.16 | -0.00 | 1028 | 0.1154 / 0.1154 | 0.0003 / 0.0003 | 0.153 / 0.152 / 0.149 | 0.116 |
| TD | TE 2026 | 744 | 0.492 | 9 9 11 10 12 8 10 12 9 10 | 0.14 | 0.01 | 1134 | 0.0900 / 0.0900 | 0.0006 / 0.0006 | 0.109 / 0.108 / 0.114 | 0.236 |
| TD | WR 2025 | 1638 | 0.050 | 9 9 13 10 11 11 9 10 10 10 | 0.19 | -0.01 | 2164 | 0.1338 / 0.1338 | 0.0007 / 0.0007 | 0.186 / 0.183 / 0.184 | 0.167 |
| TD | WR 2026 | 1246 | 0.842 | 10 10 9 10 10 11 9 10 11 10 | 0.16 | -0.00 | 2109 | 0.0939 / 0.0939 | 0.0002 / 0.0001 | 0.124 / 0.123 / 0.123 | 0.212 |
| PaYd | all | 681 | 0.485 | 9 11 10 9 9 11 11 11 9 10 | 40.36 | 2.02 | 5329 | 0.1636 / 0.1642 | 0.0001 / 0.0003 | 0.392 / 0.389 / 0.395 | 0.135 |
| PaYd | 2025 | 383 | 0.102 | 7 13 11 8 9 11 13 11 9 9 | 39.54 | 1.80 | 2705 | 0.1665 / 0.1675 | 0.0005 / 0.0006 | 0.376 / 0.374 / 0.373 | 0.156 |
| PaYd | 2026 | 298 | 0.901 | 11 9 9 10 9 11 10 12 9 11 | 41.42 | 2.30 | 2624 | 0.1607 / 0.1607 | 0.0004 / 0.0007 | 0.408 / 0.404 / 0.418 | 0.108 |
| PaYd | QB 2025 | 383 | 0.102 | 7 13 11 8 9 11 13 11 9 9 | 39.54 | 1.80 | 2705 | 0.1665 / 0.1675 | 0.0005 / 0.0006 | 0.376 / 0.374 / 0.373 | 0.156 |
| PaYd | QB 2026 | 298 | 0.901 | 11 9 9 10 9 11 10 12 9 11 | 41.42 | 2.30 | 2624 | 0.1607 / 0.1607 | 0.0004 / 0.0007 | 0.408 / 0.404 / 0.418 | 0.108 |
| RecYd | all | 3457 | 0.600 | 10 10 10 11 11 9 10 10 10 10 | 14.47 | -0.47 | 22246 | 0.1751 / 0.1752 | 0.0002 / 0.0002 | 0.344 / 0.345 / 0.340 | 0.078 |
| RecYd | 2025 | 1617 | 0.436 | 11 11 10 11 10 9 10 9 9 10 | 14.66 | -1.48 | 7356 | 0.2089 / 0.2090 | 0.0007 / 0.0009 | 0.428 / 0.428 / 0.406 | 0.043 |
| RecYd | 2026 | 1840 | 0.553 | 9 10 10 10 11 9 10 10 11 10 | 14.30 | 0.42 | 14890 | 0.1584 / 0.1585 | 0.0001 / 0.0001 | 0.302 / 0.304 / 0.307 | 0.091 |
| RecYd | RB 2025 | 353 | 0.280 | 9 14 9 12 10 10 8 8 10 10 | 9.89 | -1.14 | 1386 | 0.1871 / 0.1874 | 0.0022 / 0.0021 | 0.363 / 0.363 / 0.331 | 0.057 |
| RecYd | RB 2026 | 439 | 0.158 | 5 11 12 10 11 9 10 11 10 11 | 9.99 | 0.92 | 2438 | 0.1507 / 0.1508 | 0.0006 / 0.0009 | 0.245 / 0.245 / 0.243 | 0.098 |
| RecYd | TE 2025 | 403 | 0.678 | 10 10 11 12 11 7 11 9 9 10 | 13.70 | -0.90 | 1879 | 0.2015 / 0.2017 | 0.0014 / 0.0015 | 0.432 / 0.432 / 0.407 | 0.035 |
| RecYd | TE 2026 | 470 | 0.727 | 10 10 10 12 11 8 10 11 9 8 | 12.61 | -1.85 | 3721 | 0.1464 / 0.1467 | 0.0007 / 0.0008 | 0.297 / 0.299 / 0.276 | 0.089 |
| RecYd | WR 2025 | 861 | 0.903 | 11 10 10 10 10 9 11 9 9 10 | 17.06 | -1.90 | 4091 | 0.2196 / 0.2197 | 0.0006 / 0.0009 | 0.448 / 0.449 / 0.431 | 0.042 |
| RecYd | WR 2026 | 931 | 0.479 | 10 9 9 10 11 9 9 10 11 11 | 17.19 | 1.32 | 8731 | 0.1656 / 0.1657 | 0.0005 / 0.0003 | 0.320 / 0.323 / 0.338 | 0.090 |
| RuYd | all | 1666 | 0.838 | 10 11 10 11 10 9 9 10 9 10 | 13.39 | -0.86 | 11202 | 0.1718 / 0.1720 | 0.0003 / 0.0005 | 0.357 / 0.358 / 0.344 | 0.075 |
| RuYd | 2025 | 790 | 0.350 | 12 9 9 11 10 9 9 10 9 11 | 14.65 | -0.87 | 3677 | 0.2087 / 0.2089 | 0.0006 / 0.0006 | 0.430 / 0.430 / 0.422 | 0.048 |
| RuYd | 2026 | 876 | 0.314 | 8 12 11 11 10 9 9 10 10 9 | 12.26 | -0.86 | 7525 | 0.1537 / 0.1539 | 0.0005 / 0.0007 | 0.322 / 0.323 / 0.306 | 0.085 |
| RuYd | QB 2025 | 259 | 0.417 | 14 9 10 10 12 10 10 6 9 10 | 8.57 | -1.92 | 1038 | 0.1807 / 0.1810 | 0.0033 / 0.0036 | 0.364 / 0.364 / 0.332 | 0.064 |
| RuYd | QB 2026 | 292 | 0.179 | 9 12 11 10 13 8 8 11 12 7 | 7.69 | -1.72 | 1941 | 0.1474 / 0.1471 | 0.0026 / 0.0027 | 0.290 / 0.290 / 0.247 | 0.081 |
| RuYd | RB 2025 | 531 | 0.388 | 12 9 8 12 10 9 9 11 9 11 | 17.61 | -0.36 | 2639 | 0.2197 / 0.2199 | 0.0005 / 0.0006 | 0.456 / 0.456 / 0.457 | 0.041 |
| RuYd | RB 2026 | 567 | 0.551 | 8 12 11 11 9 10 11 9 9 10 | 14.79 | -0.47 | 5508 | 0.1569 / 0.1573 | 0.0003 / 0.0004 | 0.336 / 0.338 / 0.329 | 0.086 |
| RuYd | WR 2026 | 17 | 0.301 | 0 12 12 29 12 12 6 0 6 12 | 6.15 | 0.85 | 76 | 0.0821 / 0.0809 | 0.0076 / 0.0055 | 0.139 / 0.139 / 0.092 | 0.123 |
| Rec | all | 2980 | 0.359 | 11 10 10 10 10 11 10 9 10 9 | 1.05 | -0.10 | 16244 | 0.1540 / 0.1541 | 0.0004 / 0.0006 | 0.344 / 0.347 / 0.329 | 0.097 |
| Rec | 2025 | 1150 | 0.020 | 11 12 9 10 10 12 10 9 8 9 | 1.05 | -0.22 | 4741 | 0.1837 / 0.1839 | 0.0016 / 0.0021 | 0.430 / 0.433 / 0.393 | 0.087 |
| Rec | 2026 | 1830 | 0.267 | 11 9 10 9 10 10 10 10 12 9 | 1.05 | -0.03 | 11503 | 0.1417 / 0.1418 | 0.0001 / 0.0003 | 0.309 / 0.311 / 0.302 | 0.101 |
| Rec | RB 2025 | 253 | 0.451 | 12 8 11 8 11 13 10 10 8 8 | 0.92 | -0.18 | 971 | 0.1720 / 0.1725 | 0.0019 / 0.0019 | 0.380 / 0.385 / 0.350 | 0.100 |
| Rec | RB 2026 | 432 | 0.301 | 10 9 8 11 10 11 11 7 13 10 | 0.98 | 0.02 | 2344 | 0.1439 / 0.1440 | 0.0003 / 0.0006 | 0.273 / 0.276 / 0.272 | 0.107 |
| Rec | TE 2025 | 291 | 0.202 | 13 11 9 9 11 13 9 8 8 8 | 1.02 | -0.27 | 1220 | 0.1803 / 0.1802 | 0.0030 / 0.0035 | 0.433 / 0.435 / 0.384 | 0.081 |
| Rec | TE 2026 | 474 | 0.323 | 11 11 11 11 10 9 8 9 13 8 | 1.03 | -0.14 | 3011 | 0.1408 / 0.1409 | 0.0011 / 0.0012 | 0.314 / 0.316 / 0.290 | 0.100 |
| Rec | WR 2025 | 606 | 0.119 | 9 13 8 12 10 11 11 9 8 9 | 1.11 | -0.21 | 2550 | 0.1898 / 0.1899 | 0.0015 / 0.0020 | 0.448 / 0.450 / 0.415 | 0.085 |
| Rec | WR 2026 | 924 | 0.202 | 11 8 11 8 10 10 11 11 11 10 | 1.08 | -0.00 | 6148 | 0.1413 / 0.1413 | 0.0002 / 0.0002 | 0.320 / 0.322 / 0.319 | 0.098 |
| PaTD | all | 491 | 0.940 | 11 9 11 9 11 9 10 10 10 10 | 0.61 | -0.00 | 1934 | 0.1506 / 0.1505 | 0.0004 / 0.0005 | 0.369 / 0.368 / 0.373 | 0.139 |
| PaTD | 2025 | 193 | 0.738 | 10 10 10 9 8 8 13 12 11 8 | 0.62 | 0.00 | 720 | 0.1567 / 0.1578 | 0.0013 / 0.0016 | 0.384 / 0.385 / 0.392 | 0.176 |
| PaTD | 2026 | 298 | 0.627 | 12 8 11 9 12 10 9 8 9 11 | 0.61 | -0.01 | 1214 | 0.1469 / 0.1462 | 0.0008 / 0.0010 | 0.361 / 0.357 / 0.362 | 0.111 |
| PaTD | QB 2025 | 193 | 0.738 | 10 10 10 9 8 8 13 12 11 8 | 0.62 | 0.00 | 720 | 0.1567 / 0.1578 | 0.0013 / 0.0016 | 0.384 / 0.385 / 0.392 | 0.176 |
| PaTD | QB 2026 | 298 | 0.627 | 12 8 11 9 12 10 9 8 9 11 | 0.61 | -0.01 | 1214 | 0.1469 / 0.1462 | 0.0008 / 0.0010 | 0.361 / 0.357 / 0.362 | 0.111 |
| Int | all | 299 | 0.345 | 11 9 10 8 8 10 13 11 11 7 | 0.40 | 0.02 | 825 | 0.1408 / 0.1410 | 0.0012 / 0.0021 | 0.241 / 0.250 / 0.247 | 0.303 |
| Int | 2026 | 299 | 0.345 | 11 9 10 8 8 10 13 11 11 7 | 0.40 | 0.02 | 825 | 0.1408 / 0.1410 | 0.0012 / 0.0021 | 0.241 / 0.250 / 0.247 | 0.303 |
| Int | QB 2026 | 299 | 0.345 | 11 9 10 8 8 10 13 11 11 7 | 0.40 | 0.02 | 825 | 0.1408 / 0.1410 | 0.0012 / 0.0021 | 0.241 / 0.250 / 0.247 | 0.303 |
| Car | all | 396 | 0.815 | 10 9 12 10 10 10 11 11 9 8 | 2.67 | -0.31 | 1092 | 0.2104 / 0.2106 | 0.0006 / 0.0007 | 0.491 / 0.492 / 0.482 | 0.098 |
| Car | 2026 | 396 | 0.815 | 10 9 12 10 10 10 11 11 9 8 | 2.67 | -0.31 | 1092 | 0.2104 / 0.2106 | 0.0006 / 0.0007 | 0.491 / 0.492 / 0.482 | 0.098 |
| Car | RB 2026 | 395 | 0.805 | 9 9 12 10 10 10 11 11 9 8 | 2.68 | -0.31 | 1091 | 0.2106 / 0.2108 | 0.0006 / 0.0007 | 0.491 / 0.492 / 0.482 | 0.098 |
| Car | WR 2026 | 1 | 0.437 | 100 0 0 0 0 0 0 0 0 0 | 0.78 | -1.64 | 1 | 0.0042 / 0.0042 | 0.0042 / 0.0042 | 0.065 / 0.065 / 0.000 | 0.000 |

Reliability diagrams (pooled per stat; bin: mean predicted → observed, n):

- TD ours: 0.05→0.05 (4658), 0.15→0.15 (2437), 0.24→0.24 (1573), 0.34→0.37 (961), 0.45→0.40 (464), 0.55→0.52 (292), 0.64→0.66 (93), 0.73→0.83 (12)
- TD Kalshi mid: 0.05→0.05 (4725), 0.14→0.15 (2427), 0.24→0.24 (1511), 0.34→0.36 (951), 0.44→0.41 (468), 0.54→0.51 (303), 0.64→0.66 (93), 0.73→0.83 (12)
- PaYd ours: 0.06→0.06 (1058), 0.15→0.14 (732), 0.25→0.24 (556), 0.35→0.36 (477), 0.45→0.47 (539), 0.54→0.54 (579), 0.65→0.66 (437), 0.75→0.76 (454), 0.85→0.86 (435), 0.91→0.97 (62)
- PaYd Kalshi mid: 0.06→0.06 (1131), 0.14→0.15 (756), 0.25→0.25 (519), 0.35→0.39 (460), 0.45→0.49 (501), 0.54→0.53 (541), 0.65→0.67 (430), 0.75→0.75 (431), 0.85→0.84 (474), 0.91→0.92 (86)
- RecYd ours: 0.06→0.07 (4219), 0.15→0.16 (3604), 0.25→0.24 (3071), 0.35→0.35 (2596), 0.45→0.44 (2719), 0.54→0.52 (2250), 0.65→0.63 (1713), 0.75→0.73 (1318), 0.84→0.83 (738), 0.91→0.94 (18)
- RecYd Kalshi mid: 0.06→0.07 (4276), 0.14→0.16 (3516), 0.25→0.25 (3017), 0.35→0.34 (2539), 0.45→0.43 (2529), 0.54→0.52 (2392), 0.65→0.63 (1831), 0.75→0.73 (1445), 0.83→0.84 (694), 0.91→0.86 (7)
- RuYd ours: 0.06→0.06 (2041), 0.15→0.14 (1750), 0.25→0.24 (1508), 0.35→0.34 (1249), 0.45→0.44 (1316), 0.55→0.51 (1181), 0.65→0.63 (898), 0.75→0.72 (777), 0.84→0.83 (453), 0.93→0.93 (29)
- RuYd Kalshi mid: 0.06→0.06 (2092), 0.15→0.14 (1724), 0.25→0.24 (1464), 0.35→0.34 (1219), 0.45→0.44 (1218), 0.54→0.50 (1203), 0.65→0.62 (972), 0.74→0.71 (833), 0.83→0.83 (448), 0.93→0.97 (29)
- Rec ours: 0.05→0.04 (3834), 0.15→0.15 (2625), 0.25→0.22 (2020), 0.35→0.33 (1630), 0.45→0.41 (1428), 0.55→0.54 (1260), 0.65→0.63 (1172), 0.75→0.72 (1065), 0.85→0.83 (993), 0.92→0.87 (217)
- Rec Kalshi mid: 0.05→0.05 (4026), 0.15→0.15 (2534), 0.25→0.23 (1880), 0.35→0.32 (1521), 0.45→0.40 (1397), 0.55→0.52 (1223), 0.65→0.62 (1227), 0.75→0.71 (1101), 0.85→0.82 (1113), 0.92→0.87 (222)
- PaTD ours: 0.05→0.04 (488), 0.15→0.18 (302), 0.25→0.25 (175), 0.35→0.35 (167), 0.45→0.47 (170), 0.55→0.58 (103), 0.65→0.63 (111), 0.76→0.74 (195), 0.85→0.84 (199), 0.92→1.00 (24)
- PaTD Kalshi mid: 0.05→0.05 (514), 0.15→0.17 (292), 0.24→0.25 (163), 0.35→0.33 (161), 0.44→0.50 (151), 0.55→0.56 (129), 0.65→0.62 (107), 0.76→0.74 (174), 0.84→0.85 (210), 0.92→0.91 (33)
- Int ours: 0.04→0.03 (247), 0.15→0.14 (253), 0.22→0.23 (26), 0.39→0.45 (11), 0.46→0.50 (159), 0.53→0.56 (123), 0.63→0.33 (6)
- Int Kalshi mid: 0.05→0.03 (243), 0.15→0.14 (232), 0.22→0.16 (49), 0.38→0.46 (13), 0.46→0.51 (154), 0.53→0.56 (127), 0.62→0.29 (7)
- Car ours: 0.09→0.00 (5), 0.16→0.22 (45), 0.26→0.24 (191), 0.34→0.32 (138), 0.46→0.43 (216), 0.53→0.55 (162), 0.66→0.67 (113), 0.74→0.74 (159), 0.84→0.80 (56), 0.92→0.86 (7)
- Car Kalshi mid: 0.08→0.00 (9), 0.16→0.21 (66), 0.25→0.24 (189), 0.34→0.36 (111), 0.46→0.43 (191), 0.53→0.54 (182), 0.66→0.61 (95), 0.74→0.74 (178), 0.84→0.81 (64), 0.92→0.86 (7)

### Estimated Stats against outcomes

A core stat Kalshi did not price, estimated from the player's other stats and recent games.

| Stat | n | PIT χ² p | PIT % per decile | mean estimate | bias (y − mean) | CRPS |
|---|---|---|---|---|---|---|
| passing_interceptions | 529 | 0.612 | 9 9 10 10 10 9 12 10 8 11 | 0.65 | 0.00 | 0.40 |
| passing_interceptions QB | 529 | 0.612 | 9 9 10 10 10 9 12 10 8 11 | 0.65 | 0.00 | 0.40 |
| passing_tds | 337 | 0.095 | 9 10 7 9 10 10 8 10 12 15 | 1.33 | 0.12 | 0.62 |
| passing_tds QB | 337 | 0.095 | 9 10 7 9 10 10 8 10 12 15 | 1.33 | 0.12 | 0.62 |
| passing_yards | 147 | 0.192 | 8 4 10 10 15 13 9 8 12 12 | 197.55 | 17.25 | 49.22 |
| passing_yards QB | 147 | 0.192 | 8 4 10 10 15 13 9 8 12 12 | 197.55 | 17.25 | 49.22 |
| receiving_yards | 2662 | 0.000 | 10 12 11 10 10 10 10 10 7 10 | 21.09 | -2.71 | 9.84 |
| receiving_yards RB | 910 | 0.385 | 8 10 10 9 10 10 11 12 9 11 | 10.04 | 0.27 | 6.35 |
| receiving_yards TE | 712 | 0.002 | 11 14 12 10 11 9 10 10 7 7 | 19.37 | -3.95 | 8.26 |
| receiving_yards WR | 1040 | 0.004 | 11 13 11 10 10 11 9 9 7 10 | 31.93 | -4.47 | 13.98 |
| receptions | 3139 | 0.049 | 11 11 10 10 10 9 10 10 9 9 | 2.17 | -0.21 | 0.85 |
| receptions RB | 1017 | 0.096 | 8 9 9 9 10 9 11 12 10 12 | 1.49 | -0.02 | 0.71 |
| receptions TE | 820 | 0.000 | 12 14 10 12 10 10 10 8 8 7 | 2.11 | -0.31 | 0.80 |
| receptions WR | 1302 | 0.043 | 11 12 11 10 10 9 10 11 9 8 | 2.73 | -0.29 | 0.98 |
| rush_rec_tds | 56 | 0.201 | 11 12 4 4 9 12 12 16 4 16 | 0.27 | 0.04 | 0.20 |
| rush_rec_tds QB | 12 | 0.534 | 17 17 0 8 8 0 25 17 0 8 | 0.17 | -0.17 | 0.03 |
| rush_rec_tds RB | 16 | 0.122 | 6 12 0 0 0 19 19 19 0 25 | 0.40 | 0.16 | 0.34 |
| rush_rec_tds TE | 8 | 0.876 | 12 12 0 0 25 0 12 12 12 12 | 0.21 | 0.04 | 0.13 |
| rush_rec_tds WR | 20 | 0.740 | 10 10 10 5 10 20 0 15 5 15 | 0.24 | 0.06 | 0.22 |
| rushing_yards | 956 | 0.091 | 8 11 12 9 10 12 9 9 10 9 | 25.06 | -2.11 | 9.97 |
| rushing_yards QB | 280 | 0.159 | 9 10 8 6 11 13 9 10 11 13 | 15.31 | 1.48 | 8.07 |
| rushing_yards RB | 676 | 0.025 | 8 11 13 11 10 12 9 9 10 8 | 29.09 | -3.60 | 10.76 |

### Dispersion: sd / relation sd

Free fit: no sd prior, players with ≥ 4 Rungs. Fitted: the production fit (prior λ = 0.44).

| Stat, position, mu | n | free: geo mean | median | p25–p75 | fitted: geo mean |
|---|---|---|---|---|---|
| receiving_yards RB | 622 | 1.065 | 1.053 | 0.96–1.16 | 1.055 |
| receiving_yards RB mu 0–25 | 496 | 1.093 | 1.091 | 0.98–1.19 | – |
| receiving_yards RB mu 25–40 | 92 | 0.976 | 0.989 | 0.94–1.03 | – |
| receiving_yards RB mu 40–55 | 32 | 0.922 | 0.922 | 0.89–0.96 | – |
| receiving_yards RB mu 55–70 | 2 | 0.875 | 0.875 | 0.87–0.88 | – |
| receiving_yards TE | 768 | 0.994 | 0.987 | 0.95–1.03 | 0.993 |
| receiving_yards TE mu 0–25 | 245 | 1.045 | 1.040 | 0.96–1.10 | – |
| receiving_yards TE mu 25–40 | 259 | 0.990 | 0.995 | 0.96–1.02 | – |
| receiving_yards TE mu 40–55 | 190 | 0.967 | 0.966 | 0.94–1.00 | – |
| receiving_yards TE mu 55–70 | 52 | 0.935 | 0.936 | 0.91–0.97 | – |
| receiving_yards TE mu 70+ | 22 | 0.864 | 0.875 | 0.84–0.89 | – |
| receiving_yards WR | 1622 | 0.940 | 0.946 | 0.90–0.98 | 0.948 |
| receiving_yards WR mu 0–25 | 346 | 0.918 | 0.927 | 0.86–0.98 | – |
| receiving_yards WR mu 25–40 | 423 | 0.947 | 0.950 | 0.91–0.98 | – |
| receiving_yards WR mu 40–55 | 370 | 0.961 | 0.963 | 0.93–1.00 | – |
| receiving_yards WR mu 55–70 | 254 | 0.945 | 0.950 | 0.91–0.99 | – |
| receiving_yards WR mu 70+ | 229 | 0.924 | 0.925 | 0.89–0.96 | – |
| rushing_yards QB | 431 | 1.007 | 0.965 | 0.91–1.07 | 0.992 |
| rushing_yards QB mu 0–20 | 235 | 1.094 | 1.057 | 0.98–1.17 | – |
| rushing_yards QB mu 20–40 | 182 | 0.913 | 0.912 | 0.88–0.95 | – |
| rushing_yards QB mu 40–60 | 14 | 0.909 | 0.935 | 0.87–0.94 | – |
| rushing_yards RB | 1015 | 0.844 | 0.840 | 0.81–0.87 | 0.855 |
| rushing_yards RB mu 0–20 | 120 | 0.834 | 0.832 | 0.79–0.88 | – |
| rushing_yards RB mu 20–40 | 268 | 0.860 | 0.858 | 0.82–0.90 | – |
| rushing_yards RB mu 40–60 | 262 | 0.843 | 0.838 | 0.81–0.87 | – |
| rushing_yards RB mu 60–80 | 251 | 0.830 | 0.835 | 0.80–0.85 | – |
| rushing_yards RB mu 80+ | 114 | 0.847 | 0.841 | 0.81–0.87 | – |
| rushing_yards WR | 12 | 1.054 | 1.077 | 1.00–1.14 | 1.045 |
| rushing_yards WR mu 0–20 | 12 | 1.054 | 1.077 | 1.00–1.14 | – |
| passing_yards QB | 631 | 0.863 | 0.862 | 0.83–0.90 | 0.867 |
| passing_yards QB mu 0–200 | 136 | 0.843 | 0.824 | 0.78–0.91 | – |
| passing_yards QB mu 200–230 | 241 | 0.856 | 0.858 | 0.83–0.89 | – |
| passing_yards QB mu 230–260 | 193 | 0.873 | 0.867 | 0.84–0.91 | – |
| passing_yards QB mu 260+ | 61 | 0.909 | 0.920 | 0.86–0.96 | – |

Free fit vs production fit, scored (same player-games):

| Stat, position | n | CRPS free / prod | PIT χ² p free / prod | fit resid free / prod |
|---|---|---|---|---|
| receiving_yards RB | 622 | 10.094 / 10.089 | 0.450 / 0.061 | 0.085 / 0.086 |
| receiving_yards TE | 768 | 13.293 / 13.289 | 0.131 / 0.165 | 0.073 / 0.073 |
| receiving_yards WR | 1622 | 17.491 / 17.491 | 0.733 / 0.670 | 0.075 / 0.073 |
| rushing_yards QB | 431 | 8.595 / 8.594 | 0.268 / 0.439 | 0.071 / 0.073 |
| rushing_yards RB | 1015 | 16.024 / 16.016 | 0.692 / 0.833 | 0.067 / 0.069 |
| rushing_yards WR | 12 | 7.244 / 7.268 | 0.534 / 0.254 | 0.121 / 0.123 |
| passing_yards QB | 631 | 40.992 / 40.981 | 0.898 / 0.723 | 0.134 / 0.134 |

### Rushing yards below and at zero

| Position, mu | n | model P(Y<0) | observed | model P(Y=0) | observed | observed P(Y ≤ −5) |
|---|---|---|---|---|---|---|
| QB mu all | 551 | 7.5% | 6.0% | 12.5% | 10.7% | 0.5% |
| QB mu 0–20 | 334 | 11.4% | 8.1% | 18.4% | 15.9% | 0.6% |
| QB mu 20–40 | 199 | 1.6% | 3.0% | 3.7% | 3.0% | 0.5% |
| QB mu 40–60 | 18 | 0.3% | 0.0% | 2.2% | 0.0% | 0.0% |
| RB mu all | 1098 | 1.3% | 0.6% | 1.7% | 2.1% | 0.0% |
| RB mu 0–20 | 127 | 7.8% | 1.6% | 9.1% | 15.0% | 0.0% |
| RB mu 20–40 | 295 | 1.3% | 1.7% | 1.6% | 1.0% | 0.0% |
| RB mu 40–60 | 277 | 0.1% | 0.0% | 0.4% | 0.4% | 0.0% |
| RB mu 60–80 | 274 | 0.0% | 0.0% | 0.2% | 0.0% | 0.0% |
| RB mu 80+ | 125 | 0.0% | 0.0% | 0.1% | 0.0% | 0.0% |
| WR mu all | 17 | 7.4% | 0.0% | 43.9% | 58.8% | 0.0% |
| WR mu 0–20 | 17 | 7.4% | 0.0% | 43.9% | 58.8% | 0.0% |

### Gamma vs lognormal nonzero part

Same fit (production prior and pi0), only the family changes.

| Stat, position | n | CRPS lognormal / gamma | PIT χ² p lognormal / gamma | fit resid lognormal / gamma | PIT lognormal |
|---|---|---|---|---|---|
| receiving_yards RB | 792 | 9.934 / 9.947 | 0.636 / 0.164 | 0.070 / 0.083 | 11 11 11 10 9 8 9 9 11 10 |
| receiving_yards TE | 873 | 13.138 / 13.110 | 0.075 / 0.353 | 0.101 / 0.070 | 13 11 10 11 9 8 10 11 10 9 |
| receiving_yards WR | 1792 | 17.179 / 17.129 | 0.000 / 0.843 | 0.110 / 0.070 | 14 9 9 9 9 9 10 10 12 10 |
| rushing_yards QB | 551 | 8.114 / 8.107 | 0.041 / 0.545 | 0.076 / 0.077 | 13 13 9 10 11 8 9 9 11 8 |
| rushing_yards RB | 1098 | 16.185 / 16.155 | 0.002 / 0.791 | 0.102 / 0.067 | 14 9 10 10 8 10 9 11 10 10 |
| rushing_yards WR | 17 | 6.092 / 6.147 | 0.625 / 0.301 | 0.102 / 0.114 | 18 12 18 18 12 0 6 6 0 12 |

### Joint calibration: PIT dependence

Normal-score correlation of the outcomes' randomized PITs, against the same statistic for one copula draw per player-game (what the copula expects) and the latent rho. Independence expects 0. ±se ≈ 1/√(n−3).

| Pair | n | observed | copula expects | latent rho | se |
|---|---|---|---|---|---|
| RB all Rec–RecYd | 666 | 0.700 | 0.694 | 0.757 | 0.039 |
| RB workhorse Rec–RecYd | 240 | 0.736 | 0.708 | 0.757 | 0.065 |
| RB non-workhorse Rec–RecYd | 324 | 0.684 | 0.689 | 0.757 | 0.056 |
| RB all RuYd–Rec | 661 | -0.050 | -0.045 | -0.043 | 0.039 |
| RB workhorse RuYd–Rec | 244 | -0.164 | -0.047 | -0.043 | 0.064 |
| RB non-workhorse RuYd–Rec | 317 | 0.032 | -0.049 | -0.043 | 0.056 |
| RB all RuYd–RecYd | 757 | -0.019 | -0.022 | -0.006 | 0.036 |
| RB workhorse RuYd–RecYd | 288 | -0.076 | -0.024 | -0.006 | 0.059 |
| RB non-workhorse RuYd–RecYd | 369 | 0.031 | -0.021 | -0.006 | 0.052 |
| WR all Rec–RecYd | 1504 | 0.793 | 0.759 | 0.773 | 0.026 |
| WR all RuYd–Rec | 17 | -0.063 | 0.028 | 0.045 | 0.267 |
| WR all RuYd–RecYd | 17 | -0.186 | 0.014 | 0.021 | 0.267 |
| TE all Rec–RecYd | 751 | 0.824 | 0.777 | 0.805 | 0.037 |

### TD Rungs: Kalshi mid vs realized

| Rung, group | n | mean mid | observed | ±se | our model | Brier (mid) |
|---|---|---|---|---|---|---|
| 1+ 2025 | 3967 | 23.2% | 22.9% | 0.7% | 23.2% | 0.1572 |
| 1+ 2025 QB | 515 | 15.6% | 15.9% | 1.6% | 15.6% | 0.1241 |
| 1+ 2025 RB | 983 | 33.2% | 31.8% | 1.5% | 33.2% | 0.1905 |
| 1+ 2025 TE | 852 | 17.4% | 16.8% | 1.3% | 17.4% | 0.1283 |
| 1+ 2025 WR | 1617 | 22.5% | 22.8% | 1.0% | 22.5% | 0.1628 |
| 1+ 2026 | 3053 | 19.3% | 19.5% | 0.7% | 19.3% | 0.1367 |
| 1+ 2026 QB | 300 | 14.9% | 15.3% | 2.1% | 14.9% | 0.1203 |
| 1+ 2026 RB | 763 | 27.0% | 27.0% | 1.6% | 27.0% | 0.1607 |
| 1+ 2026 TE | 744 | 14.9% | 15.6% | 1.3% | 14.9% | 0.1202 |
| 1+ 2026 WR | 1246 | 18.3% | 18.3% | 1.1% | 18.3% | 0.1359 |
| 1+ all | 7020 | 21.5% | 21.4% | 0.5% | 21.5% | 0.1483 |
| 2+ 2025 | 1263 | 8.0% | 8.6% | 0.8% | 9.2% | 0.0734 |
| 2+ 2025 QB | 72 | 8.4% | 15.3% | 4.2% | 9.3% | 0.1274 |
| 2+ 2025 RB | 468 | 11.6% | 12.4% | 1.5% | 13.3% | 0.1020 |
| 2+ 2025 TE | 176 | 4.7% | 5.7% | 1.7% | 5.3% | 0.0532 |
| 2+ 2025 WR | 547 | 5.8% | 5.3% | 1.0% | 7.0% | 0.0483 |
| 2+ 2026 | 1809 | 5.0% | 5.5% | 0.5% | 5.4% | 0.0477 |
| 2+ 2026 QB | 161 | 3.0% | 3.7% | 1.5% | 2.9% | 0.0317 |
| 2+ 2026 RB | 532 | 8.6% | 9.8% | 1.3% | 9.2% | 0.0788 |
| 2+ 2026 TE | 363 | 3.2% | 3.6% | 1.0% | 3.3% | 0.0348 |
| 2+ 2026 WR | 753 | 3.9% | 3.9% | 0.7% | 4.2% | 0.0355 |
| 2+ all | 3072 | 6.2% | 6.8% | 0.5% | 7.0% | 0.0583 |
| 3+ 2026 | 397 | 2.8% | 2.5% | 0.8% | 3.2% | 0.0243 |
| 3+ 2026 QB | 10 | 3.2% | 0.0% | 0.0% | 3.9% | 0.0012 |
| 3+ 2026 RB | 250 | 3.3% | 3.2% | 1.1% | 3.9% | 0.0306 |
| 3+ 2026 TE | 27 | 1.6% | 0.0% | 0.0% | 1.3% | 0.0003 |
| 3+ 2026 WR | 110 | 1.9% | 1.8% | 1.3% | 2.0% | 0.0180 |
| 3+ all | 397 | 2.8% | 2.5% | 0.8% | 3.2% | 0.0243 |
| 4+ 2026 | 1 | 1.5% | 0.0% | 0.0% | 2.8% | 0.0002 |
| 4+ 2026 RB | 1 | 1.5% | 0.0% | 0.0% | 2.8% | 0.0002 |
| 4+ all | 1 | 1.5% | 0.0% | 0.0% | 2.8% | 0.0002 |
