# Kalshi as a Book for the Sprint 5 Backtest

Probed 2026-11-16 against `https://api.elections.kalshi.com/trade-api/v2`, with no key. About 1,700 requests were made at ≤ 4/s, and none returned 429. Raw responses are cached under `data/kalshi/research/` (git-ignored). The scripts are in `research/scripts/kalshi_*.py`:

| Script | What it does |
|---|---|
| `kalshi_client.py` | Cached, paced GET. Logs any rate-limit headers. |
| `kalshi_coverage.py` | Pulls every settled market for 32 NFL player series from both `/historical/markets` and `/markets?status=settled`, and writes `data/kalshi/research/markets/<SERIES>.json`. |
| `kalshi_players.py` | Pulls all `football_player` structured targets and maps them to `gsis_id`. Writes `players.json`. |
| `kalshi_coverage_report.py` | Joins events to nflverse Games, then reports coverage (tables below). |
| `kalshi_prices.py` | Pre-kickoff candle sample, spreads and volume. Writes `price_sample.json`. |
| `kalshi_minute_vs_hour.py` | Checks hourly-close against minute-close at kickoff. |

**Bottom line.** The plain series (`KXNFLRECYDS`, `KXNFLREC`, `KXNFLRSHYDS`, `KXNFLRSHATT`, `KXNFLPASSYDS`, `KXNFLPASSTDS`, `KXNFLPASSINT`, `KXNFLTD`) are true X+ Ladders: one binary market per Rung, settled on official stats. They cover Weeks 5–22 of 2025 and Weeks 1–10 of 2026, about 84k usable pre-kickoff Rungs in all. In 2026, books are two-sided on 99% of Rungs with a median spread of 1¢. 2025 is thinner, with 5–7¢ spreads. Outcomes must come from nflverse, because the plain markets settle only yes/no.

---

## 1. Market semantics and which series to use

### Ticker anatomy
`KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90` breaks down as:

| Part | Example | Meaning |
|---|---|---|
| Series | `KXNFLRECYDS` | The stat. |
| Event | `26NOV15PITCIN` | One Game. The date is the **ET game date** (SNF Nov 15 kicks off 01:20Z Nov 16). The teams are concatenated, normally away then home. Kalshi uses `LAR` for nflverse `LA`. Match on the date plus the team set, since the order and the 2–3 letter codes are ambiguous. |
| Player | `CINJCHASE1` | Team, then `structured_target.details.abbreviation` (first initial, LASTNAME, jersey). |
| Threshold | `90` | Means **90+**. `floor_strike` = 89.5 and `strike_type` = `greater`. `rules_primary`: "If Ja'Marr Chase records 90+ receiving yards …". |

There is no separate event per player. One event per series per Game holds every player's Rungs; for example, `KXNFLRECYDS-26NOV15PITCIN` has 150 markets. Group by `custom_strike.football_player`.

### Player identity
`market.custom_strike.football_player` is a UUID. Look it up with `GET /structured_targets/{uuid}`, or page through `GET /structured_targets?type=football_player&page_size=2000` (17 pages, which include NCAAF players). That returns `name`, `details.position`, `details.jersey`, `details.team_id` and `source_id`. **`source_id` equals nflverse `sportradar_id`**, so it joins to `load_rosters(2025|2026).sportradar_id` → `gsis_id`. Checked on Germie Bernard (`d37b698e…` → `00-0041489`).

Mapped share of distinct players: plain stat series 99–100% (RECYDS 369/371, REC 355/357, RSHYDS 184/185, all QB series 100%). The TD series map 90–93%; the gap is mostly D/ST and "No TD" entries (position `N/A`). The market `title` and `yes_sub_title` also carry the display name.

### Series and variants (settled, regular season and postseason)

| Series | Shape | Threshold semantics | Use for |
|---|---|---|---|
| `KXNFLRECYDS`, `KXNFLREC`, `KXNFLRSHYDS`, `KXNFLRSHATT`, `KXNFLPASSYDS`, `KXNFLPASSTDS`, `KXNFLPASSINT` | **Ladder**: one binary market per Rung | "X+" (`floor_strike` = X−0.5, `strike_type` = `greater`). Older 2025 markets show `strike_type: "structured"` but still have `floor_strike` X−0.5 and the same wording. | **Primary Ladders** |
| `KXNFLTD` (2026, from Wk 1) | Ladder: Rungs 1+, 2+, 3+ (median 2 per player) | "scores at least N touchdowns". Rush + rec TDs; D/ST entries exist. | Anytime TD (1+) and 2+ TDs |
| `KXNFLANYTD`, `KXNFL2TD` (2025 season only, Sep 2025–Feb 2026) | One yes/no market per player | Anytime TD / two or more TDs. ANYTD has a "No Touchdown Scorer" market. | Anytime TD and 2+ TDs in 2025 |
| `…PROT` (RECYDSPROT, RECPROT, RSHYDSPROT, PASSYDSPROT, PASSTDSPROT, TDPROT from 2026 Wk 5; PASSINTPROT from Wk 10) | Same Rungs as plain | X+, "protected": a player who plays in the first half but not after settles at the **pre-kickoff fair price** unless he has already hit the Rung. | Not the primary. It prices a conditional event; useful only as a cross-check. Last-hour volume is 0 on 77% of Rungs. |
| `…LADDER` (REC, RECYDS, RSHYDS) and `KXNFLFFPTSLADDER` (2026 Wk 2/3+) | **One scalar market per player-game** | Yes pays $0.05/reception, $0.0025/yard, or $0.01 per full-PPR point, capped at $1. The price is a scaled **mean**, not a Ladder. | Optional mean check |
| `…ESCALATOR` (REC, RECYDS, RSHYDS) | One scalar market per player-game | Convex payout schedule. | Skip |
| `…PRECISE` (PASSYDS from 2026-10-29; RECYDS and RSHYDS from 2026-11-13) | 1-yard Ladder: 252–602 Rungs per player | X+ at every yard. | Skip for now. Huge request count and only 3 weeks of data. |
| `KXNFLFFPTS` (2026 Wk 1+) | One binary market per player-game | "records **above** 7.4 fantasy points" (Sleeper default full PPR; see the other agent's notes) | A single-line Main-Line-like probability for fantasy points |
| Also present | `KXNFLRRYDS` (rush+rec yds, 22.6% of markets opened in-game), `KXNFLPASSATT`, `KXNFLPASSCOMP` | X+ | Not in our model |
| `KXNFLINT` | Empty (0 settled markets) | | |

Contract terms PDFs: `https://assets.kalshi.com/contract_terms/FOOTBALLENTITYSTAT.pdf` (plain), `NFLENTITYSTATCOVERED.pdf` (PROT), `FOOTBALLENTITYSCALARSTAT.pdf` (LADDER and ESCALATOR), `FFPTSLINE.pdf` and `FFPTSSCALAR.pdf` (fantasy points). The URLs are in `GET /series/{ticker}` → `contract_terms_url`.

---

## 2. Coverage

The source is `kalshi_coverage_report.py`. Events are matched to nflverse Games, and preseason events (Aug 2026, which don't match) are excluded. "pg" means player-game. "In-game %" is the share of markets whose `open_time` ≥ kickoff; they must be dropped. "Fair-price %" is the share with `result: "scalar"`, settled at the pre-game fair price because the player didn't play (`expiration_value: "Cancelled"`).

| Series | First | Weeks | Games | Player-games | Players | Markets | pg/week | Rungs/pg median (p10–p90) | In-game % | Fair-price % | Positions (pg) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| KXNFLRECYDS | 2025 Wk 5 (Oct 6) | 28 | 353 | 3,907 | 371 | 27,268 | 140 | 6 (4–12) | 5.8 | 1.5 | WR 2023, TE 997, RB 887 |
| KXNFLREC | 2025 Wk 8 (Oct 27) | 25 | 311 | 3,444 | 357 | 20,407 | 138 | 5 (4–9) | 4.8 | 0.9 | WR 1754, TE 882, RB 808 |
| KXNFLRSHYDS | 2025 Wk 5 | 28 | 348 | 1,801 | 185 | 13,241 | 64 | 6 (4–12) | 5.8 | 1.3 | RB 1196, QB 588, WR 17 |
| KXNFLRSHATT | 2026 Wk 1 | 10 | 150 | 548 | 111 | 1,884 | 55 | 3 (3–7) | 0 | 0.5 | RB 399, QB 148 |
| KXNFLPASSYDS | 2025 Wk 5 | 28 | 352 | 702 | 68 | 6,434 | 25 | 9 (7–10) | 3.2 | 1.1 | QB |
| KXNFLPASSTDS | 2025 Wk 12 (Nov 20) | 21 | 262 | 507 | 60 | 2,078 | 24 | 4 (4–5) | 0.2 | 0.8 | QB |
| KXNFLPASSINT | 2026 Wk 1 | 10 | 149 | 299 | 51 | 897 | 30 | 3 (3–3) | 0 | 0.3 | QB |
| KXNFLTD | 2026 Wk 1 | 10 | 150 | 3,574 | 560 | 7,497 | 357 | 2 (1–3) | 4.1 | 3.8 | WR 1345, RB 841, TE 784, QB 306, D/ST 298 |
| KXNFLANYTD | 2025 Wk 1 | 22 | 285 | 5,482 | 509 | 5,482 | 249 | 1 | 0 | 2.7 | WR, RB, TE, QB, D/ST/No-TD 915 |
| KXNFL2TD | 2025 Wk 1 | 22 | 279 | 2,163 | 316 | 2,163 | 98 | 1 | 0 | 1.2 | WR 945, RB 660, TE 379, QB 134 |
| KXNFLFFPTS | 2026 Wk 1 | 10 | 149 | 1,757 | 285 | 1,757 | 176 | 1 | 0 | 0 | WR 406, RB 298, QB 283, K 287, D/ST 298 |
| KXNFLFFPTSLADDER | 2026 Wk 2 | 9 | 119 | 671 | 122 | 671 | 75 | 1 | 0 | n/a | WR, RB, QB, TE |
| KXNFLRECYDSPROT | 2026 Wk 5 | 6 | 84 | 1,088 | 294 | 9,580 | 181 | 9 (5–13) | 0 | 4.4 | always a subset of plain |

**Per week.** Pass-catcher coverage was ~140–160 player-games per regular-season week in 2025 and ~180–205 in 2026. Rung counts rose from ~5 per player-game in 2025 to ~8–10 in 2026 (RECYDS: ~800 markets/week in 2025, ~1,500–2,100 in 2026). The postseason (2025 Wks 19–22) tapers with the number of Games. PROT always duplicates plain coverage (100% overlap), so it adds no players.

**Rung grid.** RECYDS uses 5+, 10+, 15+, 20+, 25+, 30+, 40+, … every 10 to ~200, with in-game 5-yard steps. REC uses 1+ … 10+. PASSTDS uses 1+ … 4–5+. PASSINT uses 1+, 2+, 3+.

**Usable pre-kickoff Rungs** (excluding in-game, fair-price and preseason):

| Series | Historical | Live |
|---|---|---|
| RECYDS | 10,170 | 15,089 |
| REC | 7,358 | 11,902 |
| RSHYDS | 4,625 | 7,635 |
| PASSYDS | 3,248 | 2,643 |
| PASSTDS | 812 | 1,227 |
| ANYTD | 5,335 | 0 |
| 2TD | 2,136 | 0 |
| RSHATT | 0 | 1,875 |
| PASSINT | 0 | 894 |
| TD | 0 | 6,863 |
| FFPTS | 0 | 1,757 |
| FFPTSLADDER | 0 | 671 |
| **Total** | **33,684** | **50,556** |

**Gaps.** There are no carries or INT Ladders in 2025. Pass TDs start in 2025 Week 12.

---

## 3. Prices

### Endpoints

| Market source | Endpoint | Field names |
|---|---|---|
| Historical (settled before `/historical/cutoff`, now `2026-09-17T00:00Z`) | `GET /historical/markets/{ticker}/candlesticks?start_ts&end_ts&period_interval={1,60}`. One market per request. `1440` returned nothing. | `yes_bid.close`, `yes_ask.close`, `price.close`, `volume`, `open_interest` |
| Live (settled since the cutoff) | Batch: `GET /markets/candlesticks?market_tickers=<≤100 comma-separated>&start_ts&end_ts&period_interval={1,60,1440}`, which returns `markets[].{market_ticker, candlesticks}`. The single-market form is `GET /series/{series}/markets/{ticker}/candlesticks`. | `yes_bid.close_dollars`, `yes_ask.close_dollars`, `price.close_dollars`, `volume_fp`, `open_interest_fp` |

More than 100 tickers per batch call gives `400 "max markets: 100"`. The batch endpoint returns `{"markets": []}` for historical tickers, and there is no `/historical/markets/candlesticks` batch. Candles are keyed by `end_period_ts`. A candle carries the closing bid and ask even when nothing traded (`price.close` is then null or empty). Prices are strings in dollars.

### In-game trading
Trading continues in-game and is heavy. On `KXNFLRECYDS-26NOV15PITCIN-CINJCHASE1-90`, kickoff was 01:20Z. The hour ending 02:00Z traded 33k contracts and the mid ran from 0.505 to 0.715. Markets also close early once the Rung is hit. New Rungs are listed in-game too: Chase's 185+ … 215+ opened 02:28–03:58Z. So:
- Cut every price at kickoff.
- Drop any market with `open_time` ≥ kickoff (5–6% of yardage markets).

### Spreads, volume and one-sided books at the last candle ending ≤ kickoff
Hourly candles, 24 h window. Live = all pre-kickoff Rungs in 2026 Weeks 4 and 9. Historical = 30 random player-games per series, all from the 2025 season in this sample.

| Series | Source | Rungs | Two-sided | Median spread | p90 spread | Median 24 h volume | Zero 24 h volume |
|---|---|---|---|---|---|---|---|
| RECYDS | live | 3,449 | 99% | $0.01 | $0.03 | 398 | 19% |
| REC | live | 2,570 | 99% | 0.01 | 0.03 | 157 | 28% |
| RSHYDS | live | 1,661 | 99% | 0.01 | 0.03 | 712 | 14% |
| RSHATT | live | 349 | 99% | 0.01 | 0.05 | 340 | 10% |
| PASSYDS | live | 578 | 99% | 0.01 | 0.02 | 1,767 | 5% |
| PASSTDS | live | 266 | 98% | 0.01 | 0.02 | 1,027 | 9% |
| PASSINT | live | 186 | 94% | 0.01 | 0.04 | 357 | 10% |
| TD | live | 1,502 | 81% (3+ Rungs have no bid) | 0.01 | 0.01 | 5,221 | 9% |
| FFPTS | live | 365 | 99% | 0.03 | 0.24 | 609 | 12% |
| RECYDSPROT | live | 1,941 | 97% | 0.05 | 0.07 | 0 | 63% |
| RECYDS | 2025 | 142 | 91% | 0.07 | 0.57 | 50 | 35% |
| REC | 2025 | 138 | 75% | 0.06 | 0.46 | 0 | 58% |
| RSHYDS | 2025 | 156 | 99% | 0.06 | 0.08 | 330 | 21% |
| PASSYDS | 2025 | 253 | 93% | 0.05 | 0.08 | 184 | 20% |
| PASSTDS | 2025 | 120 | 87% | 0.05 | 0.07 | 516 | 18% |
| ANYTD | 2025 | 30 | 93% | 0.02 | 0.17 | 1,096 | 3% |
| 2TD | 2025 | 30 | 60% | 0.04 | 0.08 | 832 | 13% |

By distance from the median (all series; mid bucket):

| Mid bucket | Rungs | Median spread | p90 spread | Median 24 h volume | One-sided |
|---|---|---|---|---|---|
| < 5% | 1,981 | 0.02 | 0.04 | 16 | **18%** |
| 5–15% | 3,599 | 0.02 | 0.06 | 107 | 1% |
| 15–35% | 3,347 | 0.01 | 0.07 | 393 | 1% |
| 35–65% | 3,616 | 0.01 | 0.06 | 981 | 3% |
| 65–85% | 1,617 | 0.01 | 0.06 | 93 | 0% |
| 85–95% | 335 | 0.01 | 0.06 | 56 | 0% |

One-sided books are almost entirely far-tail Rungs: bid 0 with an ask of 1–3¢. In the live 2026 data, the last-hour volume is 0 on 30–45% of yardage and reception Rungs, yet the quotes stay tight (market makers quote without trading).

### Hourly close against kickoff close
Week 9 live, n = 4,280 two-sided Rungs. The hourly candle ending ≤ kickoff is 0 min stale for 1 pm games and 5–30 min stale otherwise. Its mid differs from the mid of the 1-minute candle ending ≤ kickoff by a median of 0.000–0.005 (p90 0.010). Minute candles ending exactly at kickoff exist for nearly all Rungs (median gap 0 min).

### Recommended price rule (S5.1)
1. **Kickoff.** Use nflverse `load_schedules` `gameday` + `gametime` (ET) converted to UTC. Kalshi events carry no kickoff field (see §5).
2. **Rungs.** Take markets in the series with `open_time` < kickoff, and drop `result == "scalar"` (fair-price, DNP).
3. **Fetch.** Request `period_interval=1` over [kickoff − 60 min, kickoff]: live in batches of 100 tickers, historical one ticker at a time. Take the **last candle with `end_period_ts` ≤ kickoff**. If there is none, fall back to `period_interval=60` over [kickoff − 24 h, kickoff], last candle ≤ kickoff, and record the staleness. Either form costs one request per historical market, so prefer minutes.
4. **Fair Probability** = mid = (yes_bid.close + yes_ask.close) / 2. The exchange has no vig. Fees are quadratic in price and small, and are ignored.
5. **One-sided book.**
   - Bid 0 with ask a: treat as an upper bound and use a/2, flagged `one_sided`. Bid b with ask ≥ 1: use (1 + b)/2, flagged.
   - Give flagged Rungs low weight in fits, or drop them. They are almost all < 5% tails. For the PIT and reliability metrics, keep only two-sided Rungs.
6. **Liquidity filter.** Require two-sided and spread ≤ $0.10. That keeps ~99% of 2026 Rungs and drops the 2025 wide tails (p90 0.46–0.57 on REC and RECYDS). Do **not** filter on traded volume: 20–45% of good, tight 2026 Rungs trade nothing in the last hour. Log 24 h volume and open interest as a weight. Optionally drop a Rung with zero 24 h volume **and** spread > $0.05.
7. **Monotonicity.** Enforce P(X ≥ k) non-increasing across a player's Rungs (isotonic) before fitting, as for sportsbook Ladders.

---

## 4. Settlement
- `result` is `"yes"` or `"no"`, or `"scalar"` when the player was active but never played. Scalar markets settle at the pre-game fair price: `settlement_value_dollars` is e.g. `"0.0400"` and `expiration_value` is `"Cancelled"`.
- The plain markets do **not** return the stat value. `expiration_value` is just a label (`"receiving_yards"`, or empty on 2025 markets). A yes or no only brackets the value between Rungs.
- **PROT markets** (2026 Wk 5+) put the actual stat in `expiration_value` (e.g. `"103"`) on ~85% of markets. **LADDER and ESCALATOR markets** do so on ~99% (e.g. `"19"` receiving yards). `KXNFLFFPTSLADDER` gives the Sleeper fantasy total (e.g. `"20.2"`). In every case the value agreed with the yes/no on the Rungs (0 inconsistencies).
- These cover only part of 2026, so **outcomes must come from nflverse** `load_player_stats`. Kalshi's value is an optional cross-check. The plain series settle on "the Governing League" (nfl.com) and the PROT and LADDER series on ESPN; small stat-correction differences against nflverse are possible.
- Rungs that hit close early (`close_time` mid-game); that is harmless here.

## 5. Kickoff times
Kalshi markets carry `occurrence_datetime`, but it is ≈ kickoff + 3 h (SNF Nov 15: 04:20Z for a 01:20Z kickoff) and is null on 2025 markets. `open_time` and `close_time` aren't kickoffs either. **Join to nflverse.** Parse `YYMONDD` + team letters from the event ticker, take nflverse `load_schedules([2025, 2026])` rows with `gameday` equal to that date, and match the team set (aliases `LA`→`LAR`, and `JAC` / `WSH` for safety). Then kickoff = `gameday` + `gametime` in America/New_York, converted to UTC. All regular-season and postseason events matched. Only Aug 2026 preseason events don't.

## 6. Terms of use: **not verified**
- `https://docs.kalshi.com/` says: "By continuing to use or access Kalshi's API, you are agreeing to be bound to our Developer Agreement", which links to `https://kalshi.com/developer-agreement`.
- I could not read that agreement. kalshi.com returned HTTP 429 to the web-fetch agent for `/developer-agreement`, `/regulatory/rulebook` and `/terms`, and a direct curl was blocked by this session's permission policy. **Whether personal, non-commercial research with a local cache is allowed is therefore unconfirmed. Someone should read the Developer Agreement in a browser before S5.1 caches the full history.**
- What the docs do say (`https://docs.kalshi.com/getting_started/rate_limits.md`):
  - Unauthenticated public requests "are subject to separate limits. Authenticate requests where supported and back off on `429`." The numbers are not published.
  - 429s carry no `Retry-After` and have "no penalty or cooldown".
  - "Publicly cacheable responses (`Cache-Control: public`), which include most market data, carry none of these headers." That describes headers only and is not a caching licence.

## 7. Cost of a full pull
- **Rate limits.** No rate-limit headers were seen on any of ~1,700 responses, which is consistent with the docs above. There were no 429s at ≤ 4 req/s.
- **Market lists.** 32 series = 223 requests (1,000 markets per page, cursor-paged). The 12 series we need take ~110. Players take 17 requests.
- **Candles.**
  - Historical: one request per Rung, **33,684**.
  - Live: batches of ≤ 100 tickers within one event, **~1,530**.
- **Total ≈ 35,500 requests.** That is ~2.5 h at 4 req/s or ~1 h at 10 req/s, resumable through the cache. Measured: 1,213 requests ran in ≈ 5.7 min (~3.5 req/s effective).
- **Weekly increment.** About 2–3k new live Rungs means ~150 batch calls.
- **Disk.** Raw candle JSON is ~1–3 KB per market, so ~150 MB uncompressed for the full history. Keep only the kickoff candle per Rung in the processed table.
