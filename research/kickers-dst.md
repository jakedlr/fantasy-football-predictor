# Kickers and D/ST (Sprint 6)

Two research agents studied kickers and D/ST on 2026-11-17, looking at markets, nflverse data, candidate models and a backtest plan. This page summarises what they found, what was built, and how the build scored.

Reproduce the backtest with:

```bash
python scripts/backtest_k_dst.py                 # nflverse walk-forward 2016–2025, plus Kalshi 2026
```

## Markets

**Kickers.**
- Only DraftKings offers kicker props, under "Special Teams Props":
  - Kicking Pts O/U plus a 4-Rung Ladder;
  - an FG Made Ladder (1+ to 4+);
  - a PAT Made Ladder (1+ to 3+).
- They post late. On the Tuesday before a Thursday Game, 1 of the 2 kickers was listed.
- DraftKings' "kicking points" probably use flat scoring: 3 per FG and 1 per PAT, with no distance bonus. This is inferred, not confirmed.
- FanDuel offers no kicker props.

**D/ST.**
- Both Books sell each team's points total as a Ladder:
  - DraftKings' "Team Totals" is two-sided at every half point from about 7.5 to 26.5.
  - FanDuel's team-total Main Line plus its "alternate total", which steps by 3 points up to the high 40s.
- Both Books list a D/ST anytime-TD price ("BAL Ravens D/ST", "Baltimore Defense"). The selections carry no player id, so they are matched by label.
- DraftKings' defensive props (sacks, INTs) are posted only near game day and are probably for individual defenders.
- No Book offers team sacks.

**Game Lines.** Both Books' league responses already carry the main spread and total, so they cost no extra request.

**Kalshi.**
- KXNFLFFPTS has fantasy-point lines for kickers (287 in 2026) and D/STs (307), settled under Sleeper scoring.
- Our nflverse scoring reproduces 257 of the 257 kicker settlements checked, and 295 of 298 D/ST settlements.
- The D/ST match requires two Sleeper rules: forced fumbles score +1, and Points Allowed leaves out the opponent's INT and fumble return TDs.

## What drives the points (nflverse 2015–2025)

**Kickers.**
- Game Lines explain only about 3% of a kicker's points. The spread around any prediction is about 4.5 points.
- PAT attempts follow the team's Implied Team Total (R² 0.17).
- FG attempts barely move with it, staying near 2 a game.
- The two substitute for each other: once the lines are accounted for, their correlation is −0.19.
- Kicker skill doesn't persist from one year to the next (correlation −0.03), so every kicker gets the league's make curve.
- Kicks are getting longer: the average attempt went from 38.2 yards in 2015 to 41.1 in 2026, and the share from 50+ yards rose from 15% to 26%.

**D/ST.**
- The opponent's closing Implied Team Total forecasts Points Allowed without bias (slope 0.97). It explains 14% of ESPN D/ST points.
- The spread and team history add about 1 point of R² at most.
- The parts are strongly correlated. Points Allowed and Yards Allowed correlate +0.61, and covariance makes up about half of the variance of D/ST points.
- Drawing the parts independently gives intervals that are far too narrow: the P10–P90 interval covers 72% of outcomes instead of 80%.
- Scores are whole numbers that cluster on football totals: 17 points allowed is 6.7% likely, 18 only 1.1%. Those values sit on either side of ESPN's tier edges, so a smooth distribution would put whole tier points in the wrong place.

## What was built

The decisions are in PLAN.md Sprint 6 and docs/adr/0003.

**Kicker** (`ffp.kicker`, `params/kicker.json`):
- FG attempts ~ Binomial(18) and PAT attempts ~ Binomial(12). Their means come from log-linear fits on the Implied Team Total, the expected margin and the season.
- The two counts are joined by a Gaussian copula with ρ −0.19.
- Each attempt's distance is drawn from the 2024–25 attempts.
- Makes come from the league's logistic make curve, with a season trend.
- PATs are made at the league rate, 0.95.

**D/ST** (`ffp.dst`, `params/dst_games.json`, 6,056 team-games from 2015–2025):
- Every past team-game is weighted with a Gaussian kernel (1.5 points wide) on the opponent's Implied Team Total. The kernel widens until the effective sample reaches 150 games.
- Older seasons count less: a season's weight halves every 8 seasons.
- Where markets exist, the weights are tilted toward the opponent's team-total Fair Probabilities and the D/ST TD Fair Probability.
- Whole team-games are then drawn by weight and scored under the Scoring Rules.

## Backtest: walk-forward on nflverse, 2016–2025

Each test season is fitted on earlier seasons only:
- the kicker on the 5 seasons before it;
- the D/ST on every season from 2015 up to the one before it.

Each team-game is scored from its closing lines. The baselines are:
- for kickers, the last 2 seasons' kicker points;
- for D/ST, every earlier game weighted equally.

There are 5,522 team-games, 2,000 draws each.

| | Rules | CRPS | PIT χ² p | P5–P95 (90%) | P25–P75 (50%) | bias |
|---|---|---|---|---|---|---|
| Kicker model | ESPN league | **2.561** | 0.27 | 91.6% | 57.0% | +0.05 |
| Kicker baseline | ESPN league | 2.595 | 0.04 | 93.0% | 56.4% | +0.17 |
| Kicker model | Sleeper | **2.591** | 0.03 | 91.6% | 56.7% | +0.03 |
| D/ST model | ESPN league | **3.428** | 0.16 | 91.6% | 54.8% | −0.17 |
| D/ST baseline | ESPN league | 3.660 | 0.08 | 91.4% | 52.8% | −0.16 |
| D/ST model | Sleeper | **3.320** | 0.03 | 92.4% | 57.1% | −0.12 |

- **CRPS:** the D/ST model beats its baseline in every season, by 6% overall. The kicker model beats its baseline in 9 of 10 seasons, by about 1.3%. That is all the kicker's thin signal allows.
- **Bias:** under 0.6 points in every season, for both.
- **The P25–P75 interval covers 55–57%, not 50%.** Fantasy scores here are whole numbers, and a score equal to P25 or P75 counts as covered, so the middle interval looks a little wide. The baselines show the same.

## Backtest: Kalshi's 2026 fantasy-point lines (Sleeper scoring)

These are the model's predictions from nflverse's closing lines and the committed params, with no market tilt. Each line gives P(points > line), scored against the outcome next to Kalshi's mid price. Lines with a spread over 10¢ are kept as their own group ("wide").

| Group | n | observed | mean p: model / Kalshi | log loss: model / Kalshi / coin | Brier: model / Kalshi |
|---|---|---|---|---|---|
| K, priced | 134 | 0.485 | 0.505 / 0.511 | 0.694 / 0.699 / 0.693 | 0.2506 / 0.2529 |
| K, wide | 123 | 0.577 | 0.529 / 0.481 | 0.709 / 0.731 / 0.693 | 0.2577 / 0.2679 |
| D/ST, priced | 169 | 0.527 | 0.479 / 0.426 | 0.692 / 0.709 / 0.693 | 0.2494 / 0.2566 |
| D/ST, wide | 122 | 0.385 | 0.471 / 0.337 | 0.666 / 0.647 / 0.693 | 0.2365 / 0.2274 |

**Kickers.** Neither the model nor Kalshi beats a coin flip at the line. The model is at least as good as Kalshi on both groups and moves with it (correlation 0.83 on priced lines).

**D/ST.**
- On priced lines the model beats Kalshi: Brier 0.249 against 0.257. Kalshi set those lines too high (mean price 0.43 against 0.53 observed).
- On wide lines Kalshi did better, which suggests those lines carry information the Game Lines don't. With 122 lines, this is not conclusive.

## Backlog

- **Fit kickers to DraftKings' FG Made and PAT Made Ladders** once live runs have saved a few weeks of them. Every live run with a K now snapshots them.
- **Tilt D/ST to the opposing QB's INT line** where one is priced.
- **Kicker:** a team-tendency term (data mixed), and weather/wind (data patchy).
- **D/ST:** pass-rush and turnover features (each adds about 1 point of R²).
- **Kalshi's D/ST TD Rungs** (KXNFLTD/KXNFLANYTD) against our P(D/ST TD ≥ 1). The pull now maps D/ST targets but leaves those Rungs out (`dst_rung`).
