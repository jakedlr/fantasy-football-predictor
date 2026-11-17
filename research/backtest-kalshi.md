# Kalshi Backtest (S5.2)

**Status: skeleton.** The tables below fill in once the full Kalshi history pull has finished. To produce them, run:

```bash
python scripts/backtest_kalshi.py --root data/kalshi --jobs 16
```

Then paste `data/backtest/tables.md` into the sections below. The run takes about 1 s per player-game per worker with diagnostics.

## Method

For each (Game, player) with usable Kalshi Rungs (`ffp.sources.kalshi.load_rungs`):

1. **Same pipeline as production.** `to_prop_markets` → `predict.fit_player` with `prior_c={"kalshi": 0}`, so each bid/ask mid is the Fair Probability → `predict.score_player` under the `sleeper` preset. This is the same fit rules, Base Rates, TD split and position copula that `ffp predict` uses, with 4,000 samples and a fixed seed.
2. **Baseline.** The same Stat Distributions are simulated a second time with independent stats (`correlation="none"`) and the same seed.
3. **No look-ahead.**
   - The position comes from the nflverse weekly roster row for that week, or the latest row before it.
   - The TD share comes from `load_td_history(before=(season, week))`.
   - The trailing carry share is the mean over the player's previous 2–4 games of that season.
   - The model parameters are not free of look-ahead. Dispersion relations were fitted on 2015–2021 nflverse data, and the correlation matrices and Base Rates on 2021–2025. **2025 games overlap the correlation and Base Rate seasons.** Results are therefore also shown for 2026 alone, which is fully out of sample.
4. **Outcomes** come from `outcomes.load_outcomes`, keyed by (game_id, gsis_id). A player with no row didn't play: he is counted as `dnp` and excluded.
5. **Metrics** come from `ffp.metrics`:
   - **Per stat:** randomized PIT; CRPS; Brier and reliability of our P(Y ≥ k) at Kalshi's thresholds, next to the raw mid's; the fit residual (RMSE in logit of our S(k) against the mids).
   - **Per player, fantasy points:** P5–P95 and P25–P75 coverage, PIT, CRPS and pinball loss. Our P(points > line) is compared against KXNFLFFPTS, and our mean against KXNFLFFPTSLADDER.

## Data

_(counts and drops: pending the full pull)_

## Headline: fantasy points

_(pending)_

## Per-stat calibration

_(pending)_

## Diagnostics

_(pending)_

## Proposed recalibrations (for approval)

_(pending)_
