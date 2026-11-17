# Kalshi fantasy-points markets: how they settle (S5.3)

This note answers how KXNFLFFPTS and KXNFLFFPTSLADDER settle, so the backtest can score our Score Distributions with the same Scoring Rules. It covers the `sleeper` preset, settlement semantics, market shape, and whether nflverse reproduces Sleeper's points.

Research run on 2026-11-16. Sources:
- The Kalshi public API: `/series`, `/markets` and `/historical/markets`. I fetched every market in both series, 2,454 in total.
- The four Kalshi contract PDFs: the `FFPTSLINE` and `FFPTSSCALAR` product certifications and their `contract_terms` versions.
- nflverse weekly player stats, 2026 weeks 1–10, through `nflreadpy 0.1.5`.

Raw responses and the extracted PDF text are cached under `data/kalshi/research/`, which is git-ignored. Scripts:

| Script | What it does |
|---|---|
| `research/scripts/kalshi_ffpts_fetch.py` | Pages through both series on the live and historical endpoints. Writes `ffpts_markets.json`. |
| `research/scripts/kalshi_ffpts_pdftext.py` | Extracts text from the contract PDFs using only the standard library (no pypdf or poppler is installed). |
| `research/scripts/kalshi_ffpts_reproduce.py` | Scores nflverse with the formula and checks the result against every settled market. |
| `research/scripts/kalshi_ffpts_components.py` | Measures how large the Sleeper components outside our Scoring Stats are. |

## TL;DR

- Both series settle on **Sleeper's default full-PPR scoring**. Every market's `rules_secondary` spells out the formula. It is our `ppr` preset with one change: **an interception costs −1, not −2.** Sleeper also counts special-teams TDs (+6) and fumble-recovery TDs (+6), which we have no Scoring Stat for. Together they are worth under 0.03 points per game.
- **KXNFLFFPTS is a binary "Over X" market**: one line per player-game, strictly greater than X. It covers QB, RB, WR, TE, K and D/ST.
- **KXNFLFFPTSLADDER is a scalar market, not a threshold ladder.** YES pays $0.01 per fantasy point, rounded half-up to the cent and capped to $0–$1. Since 2026-10-04 its `expiration_value` field holds Sleeper's exact point total. It covers QB, RB, WR and TE only.
- **nflverse reproduces Sleeper almost exactly:**

  | Check | Agreement |
  |---|---|
  | Exact Sleeper totals (ladder `expiration_value`) | 525/538 (97.6%) |
  | Ladder cent payouts | 658/661 (99.5%) |
  | Over-line results, kickers included | 1,439/1,439 (100%) |

  All 13 exact misses are stat-feed differences, mostly 1–16 yards; none is a formula difference. nflverse's own `fantasy_points_ppr` matches only 91%, because it charges −2 per INT. Don't use it.
- **Markets settle about 30 minutes after the game's expected end**, on Sleeper's figure at that moment. Stat corrections published later are ignored.
- **Coverage:**

  | Series | Weeks | Markets |
  |---|---|---|
  | KXNFLFFPTS | 2026 weeks 1–10 (from 2026-09-09) | 150–210 per week (about 1,775 so far) |
  | KXNFLFFPTSLADDER | 2026 weeks 2–10 (from 2026-09-20) | 60–105 per week (679 so far) |

  Neither series has markets from the 2025 season.

## 1. The scoring formula

**Sources.**
- The primary source is the `rules_secondary` text on every KXNFLFFPTS market. All 1,775 markets carry the same text, except 12 week-2 markets whose kicker table had "50+ yards: +5".
- Both contracts name Sleeper as the Source Agency. Both say the figure is "Points Per Reception (PPR) scoring, single-quarterback (non-Superflex)". The series metadata says "settled using Sleeper's default PPR scoring system". The ladder's `rules_secondary` says "Sleeper's weekly standard Points Per Reception (PPR) fantasy-point total".
- I did not fetch Sleeper's own scoring-settings page separately. Kalshi's published table matches Sleeper's well-known defaults (`pass_int` −1, `fum_lost` −2, `st_td` and `fum_rec_td` +6).
- The 97.6% exact reproduction below confirms the table empirically. That includes 38 of 41 games with an INT, 20 of 22 games with a 2-pt conversion, and 43 of 47 games with a fumble lost. The remaining misses are the yardage differences listed in §4.

**Confidence: high** for every component that appears in the sample. Special-teams TDs and fumble-recovery TDs did not occur in the sample, so those two rely on the rules text alone.

### Offensive players (QB/RB/WR/TE)

| Component | Sleeper points | Our Scoring Stat | Our `ppr` preset |
|---|---|---|---|
| Passing yard | +0.04 (1 per 25) | `passing_yards` | 0.04 |
| Passing TD | +4 | `passing_tds` | 4 |
| Interception thrown | **−1** | `passing_interceptions` | **−2** |
| Passing 2-pt conversion | +2 | `passing_2pt` | 2 |
| Rushing yard | +0.1 | `rushing_yards` | 0.1 |
| Rushing TD | +6 | `rushing_tds` | 6 |
| Rushing 2-pt conversion | +2 | `rushing_2pt` | 2 |
| Reception | +1 (full PPR) | `receptions` | 1 |
| Receiving yard | +0.1 | `receiving_yards` | 0.1 |
| Receiving TD | +6 | `receiving_tds` | 6 |
| Receiving 2-pt conversion | +2 | `receiving_2pt` | 2 |
| Fumble lost | −2 (a fumble that isn't lost scores 0) | `fumbles_lost` | −2 |
| Special-teams TD (kick or punt return, blocked kick) | +6 | none | — |
| Fumble-recovery TD | +6 | none | — |
| Special-teams forced fumble / fumble recovery | +1 / +1 (listed under D/ST) | none | — |

**Not counted:**
- No yardage bonuses.
- No points for first downs.
- No points for return yards.
- No penalty for sacks taken (a sack only costs passing yards through Sleeper's net passing yards, which matches nflverse's `passing_yards`).
- No penalty for a fumble that isn't lost.

**Kickers.** FG made 0–39 yards +3, 40–49 +4, 50–59 +5, 60+ +6. PAT made +1. Missed FG −1, missed PAT −1.

**D/ST.**
- TD +6, sack +1, INT +2, fumble recovery +2, forced fumble +1, safety +2, blocked kick +2.
- Points allowed: 0 → +10, 1–6 → +7, 7–13 → +4, 14–20 → +1, 21–27 → 0, 28–34 → −1, 35+ → −4.

Kickers and D/ST are listed here for completeness; we don't model them yet.

### The `sleeper` preset

The `offense` dict, keyed by `SCORING_STATS`:

```python
"sleeper": {
    "passing_yards": 0.04,
    "passing_tds": 4,
    "passing_interceptions": -1,
    "passing_2pt": 2,
    "rushing_yards": 0.1,
    "rushing_tds": 6,
    "rushing_2pt": 2,
    "receptions": 1,
    "receiving_yards": 0.1,
    "receiving_tds": 6,
    "receiving_2pt": 2,
    "fumbles_lost": -2,
}
```

In `scoring.py` terms this is `{**_STANDARD_OFFENSE, "receptions": 1, "passing_interceptions": -1}`.

Our `fumbles_lost` already equals `sack_fumbles_lost + rushing_fumbles_lost + receiving_fumbles_lost`, which is the fumble-lost total Sleeper scores in the sample. Sleeper's points have at most 2 decimals; no other rounding is applied before settlement.

## 2. Settlement semantics and timing

### KXNFLFFPTS ("Pro Football Player Fantasy Points")

- Contract `FFPTSLINE`. Binary market, `strike_type: "greater"`.
- `rules_primary`: "If <player> records **above** X fantasy points in the Full game (including overtime)…". The contract defines "above" as strictly greater than X, compared at Sleeper's published decimals with no extra rounding by Kalshi.
- Six settled markets had nflverse points exactly equal to X. All six settled NO, which confirms the strict inequality.

### KXNFLFFPTSLADDER ("Fantasy Ladder")

- Contract `FFPTSSCALAR`. `custom_strike.scalar_payout_type: "capped_cent"`.
- YES pays $0.01 × points, rounded to the cent with $0.005 rounded up, and clamped to $0.00–$1.00. NO pays $1 minus that.
- Contract examples: 31.4 → $0.31 and 32.5 → $0.33. Negative points pay YES $0.
- The API reports `result: "scalar"`. `settlement_value_dollars` is the YES payout. `expiration_value` holds Sleeper's exact points from 2026-10-04 on; for weeks 2–4 it holds the dollar payout, or nothing.
- Despite the name, these are not "X+" threshold rungs. Each player-game has exactly one market.

### Player who doesn't play

- Under both contracts, a player Sleeper assigns no figure (bye, inactive, did not play, delisted) is deemed to have scored 0.0.
- All 30 settled markets in my sample with no nflverse stat line settled NO, or at 0.

### Timing and corrections

- The contract value is "the most recent figures published by the Source Agency as of the Expiration time … any correction, restatement, or retraction published after the Expiration time is disregarded". Kalshi may override a figure it judges to be a manifest error, under Rule 7.1.
- Contract latest expiration: seven days after the week's final game, 10:00 AM ET.
- In practice markets expire early. `settlement_ts` minus `occurrence_datetime` (the game's expected end) has these values:

  | Series | Median | Range |
  |---|---|---|
  | Ladders | 0.5 h | −0.1 h to 1.8 h |
  | Over lines | 0.5 h | −7.6 h to 2.3 h |

  The negative Over-line values are lines that had already been crossed during the game.
- So the figure that settles a market is Sleeper's live total about 30 minutes after the final whistle, not the version after Tuesday or Thursday stat corrections.
- For the backtest, nflverse later in the week is the right outcome source for *our* calibration, but it can differ from what Kalshi paid by a yard or two (§4).

## 3. Market shape

| | KXNFLFFPTS | KXNFLFFPTSLADDER |
|---|---|---|
| First game | 2026-09-09 (week 1) | 2026-09-20 (week 2) |
| Settled through | 2026-11-15 (week 10); week 11 is open | same |
| Markets so far | 1,775, i.e. 1,775 player-games | 679, i.e. 679 player-games |
| Per week | 150–210 | 60–105 (13 in week 2) |
| Per Game | about 8–17 players (median about 12) | about 4–8 players |
| Positions | QB, RB, WR, TE, K (about 16%), D/ST (about 17%) | QB, RB, WR, TE |
| Thresholds | one "Over X" line per player-game. X is set at 0.1 precision (e.g. 10.9, 14.7, 10) and works as the market's median line | none (scalar) |
| 2025 season | none (the historical endpoint holds only week-1 2026 duplicates) | none |

### Ticker anatomy

```
KXNFLFFPTS-26NOV19INDHOU-INDTWARREN84-10P9
  series   -  date + AWAY HOME  -  TEAM + first initial + LASTNAME + jersey  -  strike (P = decimal point)
```

Ladder tickers are the same without the strike suffix, for example `KXNFLFFPTSLADDER-26NOV19INDHOU-INDTWARREN84`.

**Teams.**
- Kalshi uses `JAC` and `LAR` where nflverse uses `JAX` and `LA`.
- Split the event's team pair into two valid codes first. A naive prefix match turns `GBTB` + `GBTKRAFT85` into team `GBT`.
- D/ST markets use the player segment `<TEAM><TEAM>DST`, for example `INDINDDST`.

**Players.**
- The player's name is in `title` ("Tyler Warren: Over 10.9 fantasy points" or "Tyler Warren fantasy scalar ladder") and in `yes_sub_title`.
- `custom_strike.football_player` is a Kalshi player UUID, so it can serve as a stable key.
- Matching on normalised full name + team + week (from `load_schedules` gameday) worked for every player who played.

## 4. Can nflverse reproduce Sleeper's points?

`kalshi_ffpts_reproduce.py` scores each nflverse row with the formula above, adding the kicker formula for kickers. It skips D/ST.

| Check | Sample | Agree |
|---|---|---|
| Ladder exact points (`expiration_value`, from 2026-10-04) | 538 | **525 (97.6%)**: QB 79/87, RB 149/151, WR 216/218, TE 83/84 |
| Ladder cents (round-half-up(points) = payout) | 661 | 658 (99.5%) |
| Over-line result (YES ⇔ points > X) | 1,439 | **1,439 (100%)**: QB 280, RB 294, WR 398, TE 183, K 284 |
| Same as the first row, but with only our 12 Scoring Stats | 538 | 527: identical, because no ST or fumble-recovery TD occurred in the sample |
| Same as the first row, but with nflverse `fantasy_points_ppr` | 538 | 489 (91%): its INT is −2 |

### The 13 exact-value misses (Sleeper minus nflverse)

| Difference | Cases | Likely cause |
|---|---|---|
| ±0.04 to ±0.1 | 8 | 1–2 yards apart |
| −0.64 | Daniels, week 9 | 16 passing yards |
| −0.7 | Dowdle, week 10 | 7 yards |
| +0.32 | L. Jackson, week 4 | 8 passing yards |
| +1.8 | Z. Flowers, week 4 | 18 receiving yards |
| −2.0 | D. Watson, week 5 | nflverse has 1 fumble lost of 3 fumbles; Sleeper apparently charged 2 |

None of these is systematic, and none points to a missing component. The biggest ones are yardage differences between Sleeper's settlement-time feed and nflverse's play-by-play.

**Matching coverage.** 30 settled non-D/ST markets had no nflverse row. Every one is a player who didn't play, and every one settled NO or at 0. 298 D/ST markets were skipped.

## 5. Mapping onto `SCORING_STATS`

- Every Sleeper offensive component maps to a Scoring Stat except special-teams TDs and fumble-recovery TDs (+6 each) and the special-teams forced fumble and fumble recovery (+1 each).
- **Size of the gap.** Over 2023–2026 nflverse regular-season games (21,040 QB/RB/WR/TE player-games):

  | Position | Special-teams TD | Fumble-recovery TD |
  |---|---|---|
  | WR | 36 games, 0.025 pts/game | 3 games, 0.002 pts/game |
  | RB | 8 games, 0.009 pts/game | 2 games, 0.002 pts/game |
  | TE | 1 game, 0.001 pts/game | 2 games, 0.003 pts/game |
  | QB | 0 | 0 |

  This is negligible for the Score Distribution except for dedicated returners, who rarely get markets.
- **Other fumbles lost.** nflverse `fumbles_lost_total` sometimes exceeds the sum of the three per-play-type columns (87 WR games out of 8,814, mostly return fumbles). The sample has no decisive case for whether Sleeper charges these −2. Our `fumbles_lost` should stay the three-column sum, which matched every fumble-lost case except the Watson game.
- **What the backtest needs.**
  - Add the `sleeper` preset above.
  - Score outcomes from nflverse with the same 12 columns.
  - When comparing to Kalshi payouts, remember that Kalshi paid on Sleeper's live figure. Expect about 2% of player-games to differ by a few tenths of a point.
  - Kickers (284 Over lines) and D/ST (298 Over lines) are present in KXNFLFFPTS but out of scope until the model covers them.
