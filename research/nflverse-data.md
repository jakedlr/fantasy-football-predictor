# nflverse data for the copula and the no-market Scoring Stats

Answers Q3 follow-ups from `RESEARCH.md`: how to get the data, how names match, what the rank correlations are, base rates for Scoring Stats that have no Prop Market, and what to put in the copula.

Run on 2026-11-15 with `nflreadpy 0.1.5` (polars 1.x, Python 3.14) in a scratch venv. All numbers below come from scripts kept outside the project (scratchpad `nflwork/`: `explore1.py`, `explore2.py`, `names.py`, `names2.py`, `prep.py`, `corr.py`, `within.py`, `base.py`, `misc.py`). Data is nflverse (CC-BY 4.0), so attribute nflverse.

## TL;DR

- `nflreadpy` works. Use `load_player_stats` (weekly stats), `load_rosters` / `load_players` (IDs, names, positions, teams), `load_schedules` (next Game and opponent, plus closing spread and total), and `load_snap_counts` (for usage filters; you have to join it through `pfr_id`). The 2026 season is present through week 10. On 2026-11-15, 12 of the 14 week-10 Games were final and in the stats.
- Every Scoring Stat we need has a column, including fumbles lost split into sack, rushing and receiving, and 2-pt conversions split into passing, rushing and receiving.
- **Name collisions are close to zero.** Among 2026 QB/RB/WR/TE, normalized full names are unique (959 rostered players, 574 with stats). Six collisions pair a skill player with a defender or lineman of the same name (for example Justin Jefferson WR/MIN and LB/CLE). Match on name **plus position (or team)** and the problem goes away.
- **Correlations are stable and tiering is not worth it.** Usage tiers move single coefficients by at most 0.02–0.07, except workhorse RBs (0.14 on RuYd–Rec). Season-to-season changes are of the same size. **The big choice is pooled vs within-player:** pooled correlations are inflated by differences between players, so use within-player-season ranks, which are 0.05–0.12 lower. Then convert to latent Gaussian rho, because TD margins are heavily tied.
- Within-player structure: Rec–ReYd ~0.75 (WR), 0.78 (TE), 0.73 (RB). ReTD–ReYd ~0.32 (WR), 0.21 (TE), 0.25 (RB). PaYd–PaTD 0.39. QB RuYd–RuTD 0.17. RB RuYd–RuTD 0.32. Passing and rushing stats are near independent for QBs, as are rushing and receiving stats for RBs (|rho| <= 0.05). INT is about independent of everything except PaTD (-0.11).
- No-market base rates per qualifying game: fumbles lost QB 0.20, RB 0.063, WR 0.026, TE 0.019. 2-pt conversions: QB passing 0.074, rushing/receiving about 0.015 for every position. Share of non-passing TDs that are rushing: RB 80%, QB 99%, WR 3.3%, TE 2.2%.

---

## 1. Data access

### Exact calls

```python
import nflreadpy as nfl   # returns polars DataFrames; call .to_pandas() if needed

nfl.get_current_season(), nfl.get_current_week()          # -> (2026, 10) on 2026-11-15
ps   = nfl.load_player_stats(list(range(2021, 2027)))     # summary_level="week" is the default
rost = nfl.load_rosters(2026)                             # one row per player-season (latest team/status)
rw   = nfl.load_rosters_weekly(2026)                      # one row per player-week (weeks 1-10 present)
pl_  = nfl.load_players()                                 # all-time player master table (24,869 rows)
sch  = nfl.load_schedules(2026)                           # 272 REG games, future games have null scores
snap = nfl.load_snap_counts(list(range(2021, 2026)))      # PFR snap counts, keyed by pfr_player_id
ids  = nfl.load_ff_playerids()                            # cross-site IDs (sleeper, espn, yahoo, fantasypros...)
```

Other loaders in 0.1.5: `load_pbp`, `load_participation`, `load_nextgen_stats`, `load_ff_opportunity`, `load_depth_charts`, `load_injuries`, `load_team_stats`, `load_pfr_advstats`, `load_ftn_charting`, `load_ff_rankings`, `load_trades`, `load_contracts`, `load_officials`, `load_combine`, `load_draft_picks`, `load_teams`. `seasons=True` loads everything.

### `load_player_stats` (150 columns, one row per player-game)

Keys and identity: `player_id` (= gsis_id, e.g. `00-0036900`), `player_name` (abbreviated, `J.Chase`), `player_display_name` (`Ja'Marr Chase`), `position`, `position_group`, `season`, `week`, `season_type` (`REG`/`POST`), `game_id` (`2026_10_PIT_CIN`), `team`, `opponent_team`.

Scoring Stat columns:

| Scoring Stat | Column(s) |
|---|---|
| Passing yards / TDs / INTs | `passing_yards`, `passing_tds`, `passing_interceptions` |
| Pass volume | `completions`, `attempts`, `sacks_suffered` |
| Rushing | `carries`, `rushing_yards`, `rushing_tds` |
| Receiving | `receptions`, `targets`, `receiving_yards`, `receiving_tds` |
| Fumbles lost | `sack_fumbles_lost`, `rushing_fumbles_lost`, `receiving_fumbles_lost`, and `fumbles_lost_total` (also `*_fumbles` for fumbles regardless of who recovered) |
| 2-pt conversions | `passing_2pt_conversions`, `rushing_2pt_conversions`, `receiving_2pt_conversions` |
| Return/ST TDs | `special_teams_tds`, `punt_return_yards`, `kickoff_return_yards` |
| Usage shares | `target_share`, `air_yards_share`, `wopr` |
| Precomputed | `fantasy_points`, `fantasy_points_ppr` (handy for checking our scorer) |

`fumbles_lost_total` is larger than the sum of the three split columns: 1,323 vs 1,177 over 2021–25 skill-player games. The extra fumbles mostly come from returns (64% of the 36 qualifying games with a gap had return activity). Use `fumbles_lost_total` if the league counts all fumbles lost, which most do.

Rows exist only for players who recorded a stat. 238 of 15,664 skill-player games with >=50% offensive snaps (2021–25) have no row, mostly blocking TEs with zero targets. Treat a missing row as a zero line when you have snap data. Defensive players and kickers are in the same table, so filter by `position`.

### Seasons and 2026

| season | REG rows | POST rows | max week |
|---|---|---|---|
| 2020 | 16,774 | 828 | 17 REG |
| 2021–2024 | ~18,100 each | ~850 | 18 REG |
| 2025 | 18,540 | 882 | 18 REG / 22 |
| 2026 | 10,306 | — | 10 (partial: 12 of 14 games) |

1999 loads too (16,839 rows), so player stats go back to 1999. `load_rosters_weekly(2026)` has weeks 1–10.

### Rosters / players (IDs, names, positions, teams)

- `load_rosters(2026)` has 36 columns, one row per gsis_id (3,073 rows): `gsis_id, full_name, first_name, last_name, football_name, position, depth_chart_position, team, status, jersey_number, birth_date, years_exp, rookie_year, entry_year, ngs_position, week, game_type`, plus IDs `espn_id, sportradar_id, yahoo_id, rotowire_id, pff_id, pfr_id, fantasy_data_id, sleeper_id, esb_id, gsis_it_id, smart_id`. `status` values: ACT 1682, DEV 551, CUT 430, RES 334, INA 50, RET 22, other 4. Filter `status == "ACT"` for active players.
- `load_players()` has 39 columns: `gsis_id, display_name, common_first_name, first_name, last_name, short_name, football_name, suffix, position, position_group, latest_team, status, last_season, rookie_season, pfr_id, espn_id, nfl_id, pff_id, otc_id, smart_id, esb_id, ...`. The `pfr_id` → `gsis_id` mapping has no duplicates. It joins snap counts to stats, leaving 152 of 132,617 snap rows unmatched.
- 23 players' `load_rosters` team differs from the team in their latest 2026 stats row (trades and cuts). For "current team", take `team` from the latest `load_rosters_weekly` week, not from the last stats row.
- `load_ff_playerids()` (12,541 rows) maps gsis_id to `sleeper_id, espn_id, yahoo_id, fantasypros_id, mfl_id, cbs_id, ...` and has a `merge_name` field. Useful if a sportsbook or fantasy site gives you its own ID.

### Schedules (next Game and opponent)

`load_schedules(2026)` has 46 columns: `game_id, season, game_type, week, gameday, weekday, gametime, away_team, home_team, away_score, home_score, result, total, overtime, spread_line, total_line, away_moneyline, home_moneyline, away_spread_odds, home_spread_odds, under_odds, over_odds, div_game, roof, surface, temp, wind, away_qb_id/home_qb_id, away_qb_name/home_qb_name, away_rest, home_rest, stadium, referee, espn, pfr, gsis, ...`. `spread_line` is positive when the home team is favoured. Future Games have null scores, which gives the next-Game lookup:

```python
nxt = (sch.filter(((pl.col("home_team")==team) | (pl.col("away_team")==team))
                  & pl.col("home_score").is_null())
          .sort(["gameday", "gametime"]).head(1))
# CIN on 2026-11-15 -> 2026_10_PIT_CIN (Sun 20:20, spread 4.5, total 49.5)
```

Team codes in the stats match the schedule codes (`LA` for the Rams, `LV`, etc.). The `game_id` format `{season}_{week:02}_{away}_{home}` is the same in stats and schedules.

## 2. Name matching

How names appear (2026 skill players):

- `player_display_name` / `rosters.full_name` / `players.display_name` keep suffixes and punctuation: `Marvin Harrison Jr.`, `Kenneth Walker III`, `Deebo Samuel Sr.`, `Ja'Marr Chase`, `De'Von Achane`, `D'Andre Swift`, `Amon-Ra St. Brown`, `Jaxon Smith-Njigba`. Counts among 959 rostered skill players: 48 with suffixes, 19 with apostrophes, 42 with periods, 15 with hyphens, 0 with non-ASCII characters.
- Initials are inconsistent across players: `A.J. Brown`, `T.J. Hockenson` and `E.J. Williams Jr.` have periods, while `AJ Dillon`, `AJ Barner` and `DK Metcalf` don't. Books do the same thing in their own way, so strip periods on both sides.
- `first_name` is the legal name (`Arthur` Brown, `DeKaylin` Metcalf, `Rod` Godwin, `Zonovan` Knight, `Chigoziem` Okonkwo). `football_name` is usually the common name, but it isn't reliable: `Devon` for De'Von Achane, `Chigoziem` for Chig Okonkwo, `A.J.` for AJ Dillon, and null for some players. **Use `display_name`/`full_name`**, which already holds the common name (`Bam Knight`, `Chig Okonkwo`, `Gabe Davis`, `Tank Dell`, `Josh Palmer`, `Marquise Brown`; books may say "Hollywood Brown").
- `players.suffix` is mostly null (about 165 non-null, with junk like `Williams`), so parse suffixes out of the display name.
- `player_name` (`J.Chase`) collides: 17 abbreviated names map to 2–3 different 2026 skill players (`J.Daniels`, `D.Moore`, `K.Williams`, `T.Etienne`, `J.Love`, ...). Never match on it.

Normalizer tested: NFKD → ASCII, lowercase, drop `. ' ’ \``, hyphen → space, drop trailing `jr|sr|ii|iii|iv|v`, squeeze whitespace.

Collisions after normalizing:

| Set | Players | Collisions |
|---|---|---|
| 2026 rosters, QB/RB/WR/TE, all statuses | 959 | **0** |
| 2026 rosters, QB/RB/WR/TE, status ACT | 501 | **0** |
| 2026 player_stats, QB/RB/WR/TE | 574 | **0** |
| 2021–2026 player_stats, QB/RB/WR/TE | 1,234 | 1 (Josh Johnson QB vs RB) |
| 2026 rosters, all positions | 3,073 | 11, of which 6 involve a skill player: Justin Jefferson (WR MIN / LB CLE), DeVonta Smith (WR PHI / DB CAR), Cam Miller (QB / DB), Michael Carter (RB / DB, "II"), Devin Neal (RB / DB), Brandon Johnson (WR / DB) |

Recommendation: key on `gsis_id`, and match book names by (normalized name, position in {QB,RB,WR,TE}). Use team as a tiebreaker. Keep a small hand-made alias table for nicknames books use that nflverse doesn't (e.g. "Hollywood Brown", "Kenneth Walker" without III, "Chig"/"Chigoziem"). Log every book name that doesn't match.

## 3. Correlations

### Sample and filters

2021–2025, `season_type == "REG"`, positions from `player_stats.position`. Snap share comes from `load_snap_counts().offense_pct`, joined via `players.pfr_id`.

| Pos | Filter ("meaningful usage") | Player-games |
|---|---|---|
| QB | `attempts >= 15` | 2,709 (of 3,279) |
| RB | `offense_pct >= 0.30` | 4,229 (of 7,687) |
| WR | `offense_pct >= 0.50` | 7,130 (of 12,298) |
| TE | `offense_pct >= 0.50` | 3,172 (of 6,112) |

Snap share is used for RB/WR/TE instead of targets or touches because filtering on a realized outcome (e.g. targets >= 3) cuts off the lower tail of the very stats we're correlating. `attempts >= 15` for QBs does the same thing, but mildly; it removes relief and injury-exit games. The filter matters most for QB (max change 0.25 vs unfiltered) and RB (0.41). For WR and TE it barely matters (0.08 / 0.05).

Abbreviations: PaYd/PaTD/INT passing; RuYd/RuTD rushing; Rec/ReYd/ReTD receiving; Att pass attempts, Car carries, Tgt targets.

### 3a. Pooled Spearman (player-games pooled across players)

**QB** (n = 2,709)

| |PaYd|PaTD|INT|RuYd|RuTD|Att|Car|
|---|---|---|---|---|---|---|---|
|**PaYd**|1|0.49|0.03|-0.06|-0.01|0.65|-0.04|
|**PaTD**|0.49|1|-0.10|-0.04|-0.07|0.18|-0.02|
|**INT**|0.03|-0.10|1|0.00|-0.03|0.20|-0.05|
|**RuYd**|-0.06|-0.04|0.00|1|0.30|-0.03|0.75|
|**RuTD**|-0.01|-0.07|-0.03|0.30|1|-0.10|0.35|

**RB** (n = 4,229)

| |RuYd|RuTD|Rec|ReYd|ReTD|Car|Tgt|
|---|---|---|---|---|---|---|---|
|**RuYd**|1|0.41|0.02|0.03|0.07|0.84|0.00|
|**RuTD**|0.41|1|-0.02|0.02|-0.02|0.38|-0.03|
|**Rec**|0.02|-0.02|1|0.80|0.23|0.04|0.92|
|**ReYd**|0.03|0.02|0.80|1|0.28|0.05|0.73|
|**ReTD**|0.07|-0.02|0.23|0.28|1|0.05|0.21|

**WR** (n = 7,130)

| |Rec|ReYd|ReTD|RuYd|Tgt|
|---|---|---|---|---|---|
|**Rec**|1|0.83|0.34|0.06|0.85|
|**ReYd**|0.83|1|0.39|0.04|0.71|
|**ReTD**|0.34|0.39|1|-0.00|0.28|
|**RuYd**|0.06|0.04|-0.00|1|0.04|

**TE** (n = 3,172)

| |Rec|ReYd|ReTD|RuYd|Tgt|
|---|---|---|---|---|---|
|**Rec**|1|0.86|0.28|0.00|0.89|
|**ReYd**|0.86|1|0.27|0.01|0.78|
|**ReTD**|0.28|0.27|1|-0.00|0.23|
|**RuYd**|0.00|0.01|-0.00|1|0.02|

Stability by season: max |rho_season − rho_pooled| is QB 0.05–0.10 (n ≈ 540/season), RB 0.04–0.07, WR 0.02–0.06, TE 0.02–0.06. There is no trend, so five seasons can be pooled.

### 3b. Within-player-season Spearman (use these)

The copula is applied *after* each player's marginals have been set from his own prop lines, so the dependence it needs is the dependence **given the player**. Pooled correlations also include differences between players: good players have more of everything. That shows up in the WR tiers below, where every tier's Rec–ReYd (0.78–0.82) is below the pooled 0.83. To estimate within-player correlation, rank each stat inside each player-season (players with >=6 qualifying games), centre the ranks, and correlate the pooled centred ranks.

**QB** (2,383 player-games, 67 QBs; max |pooled − within| = 0.12)

| |PaYd|PaTD|INT|RuYd|RuTD|
|---|---|---|---|---|---|
|**PaYd**|1|0.39|0.03|-0.00|0.01|
|**PaTD**|0.39|1|-0.11|-0.04|-0.10|
|**INT**|0.03|-0.11|1|0.02|-0.03|
|**RuYd**|-0.00|-0.04|0.02|1|0.17|
|**RuTD**|0.01|-0.10|-0.03|0.17|1|

**RB** (3,684 player-games, 123 RBs; max diff 0.09)

| |RuYd|RuTD|Rec|ReYd|ReTD|
|---|---|---|---|---|---|
|**RuYd**|1|0.32|-0.03|-0.00|0.05|
|**RuTD**|0.32|1|-0.03|0.02|-0.02|
|**Rec**|-0.03|-0.03|1|0.73|0.18|
|**ReYd**|-0.00|0.02|0.73|1|0.25|
|**ReTD**|0.05|-0.02|0.18|0.25|1|

**WR** (6,364 player-games, 211 WRs; max diff 0.08)

| |Rec|ReYd|ReTD|RuYd|
|---|---|---|---|---|
|**Rec**|1|0.75|0.26|0.02|
|**ReYd**|0.75|1|0.32|0.01|
|**ReTD**|0.26|0.32|1|-0.01|
|**RuYd**|0.02|0.01|-0.01|1|

**TE** (2,713 player-games, 92 TEs; max diff 0.08)

| |Rec|ReYd|ReTD|RuYd|
|---|---|---|---|---|
|**Rec**|1|0.78|0.24|-0.01|
|**ReYd**|0.78|1|0.21|0.00|
|**ReTD**|0.24|0.21|1|-0.01|
|**RuYd**|-0.01|0.00|-0.01|1|

Within-player is a slight *under*-estimate of the right target. Prop lines don't capture all game-level information (late injuries, game script), so some between-game variation remains that the copula should carry. The truth lies between 3a and 3b and is closer to 3b. A backtest (PIT width) can settle it.

### 3c. Latent Gaussian-copula rho (what to put in R)

TD counts are mostly 0 or 1 (P(ReTD >= 1) is about 0.26 for WRs). Ties pull Spearman toward zero, so plugging the observed Spearman, or `2 sin(pi rho_s / 6)`, into a Gaussian copula with discrete margins gives too little dependence. For each pair I solved by bisection for the latent rho that reproduces the 3b Spearman when simulating with the pooled empirical margins (40k draws). All matrices are positive definite (min eigenvalue 0.20–0.49).

**QB** latent R

| |PaYd|PaTD|INT|RuYd|RuTD|
|---|---|---|---|---|---|
|**PaYd**|1|0.42|0.04|0.01|0.03|
|**PaTD**|0.42|1|-0.13|-0.03|-0.17|
|**INT**|0.04|-0.13|1|0.03|-0.06|
|**RuYd**|0.01|-0.03|0.03|1|0.28|
|**RuTD**|0.03|-0.17|-0.06|0.28|1|

**RB** latent R

| |RuYd|RuTD|Rec|ReYd|ReTD|
|---|---|---|---|---|---|
|**RuYd**|1|0.41|-0.03|-0.01|0.10|
|**RuTD**|0.41|1|-0.05|0.03|-0.07|
|**Rec**|-0.03|-0.05|1|0.76|0.33|
|**ReYd**|-0.01|0.03|0.76|1|0.45|
|**ReTD**|0.10|-0.07|0.33|0.45|1|

**WR** latent R

| |Rec|ReYd|ReTD|RuYd|
|---|---|---|---|---|
|**Rec**|1|0.77|0.35|0.05|
|**ReYd**|0.77|1|0.43|-0.00|
|**ReTD**|0.35|0.43|1|-0.03|
|**RuYd**|0.05|-0.00|-0.03|1|

**TE** latent R

| |Rec|ReYd|ReTD|RuYd|
|---|---|---|---|---|
|**Rec**|1|0.80|0.34|-0.03|
|**ReYd**|0.80|1|0.29|-0.01|
|**ReTD**|0.34|0.29|1|-0.05|
|**RuYd**|-0.03|-0.01|-0.05|1|

The TD entries move most (e.g. WR ReYd–ReTD goes from 0.32 Spearman to 0.43 latent). The correction depends on the margin: a player with P(TD) = 0.6 needs a smaller correction than one with 0.15. A v2 could recalibrate per player at run time (bisection on each TD pair is cheap), or tabulate the correction by P(TD >= 1) bucket.

### 3d. Is tiering worth it? Mostly no.

Tiers use **trailing** usage: the mean over the previous 2–4 games of the same season, which is known before the Game. Pooled Spearman within each tier is compared with the position's pooled matrix. The Fisher-z column counts pairs whose difference from pooled exceeds 2 SE.

| Pos | Tier variable | Tier (n) | max abs diff vs pooled | pairs with abs z > 2 |
|---|---|---|---|---|
| WR | trailing target share | <15% (2,329) | 0.02 | 0/6 |
| WR | | 15–22% (1,821) | 0.05 | 3/6 |
| WR | | >=22% (1,947) | 0.06 | 1/6 |
| TE | trailing target share | <12% (1,227) | 0.03 | 1/6 |
| TE | | 12–18% (776) | 0.04 | 1/6 |
| TE | | >=18% (714) | 0.05 | 1/6 |
| RB | trailing carry share | <35% (1,404) | 0.03 | 1/10 |
| RB | | 35–55% (1,165) | 0.07 | 1/10 |
| RB | | >=55% (1,061) | **0.14** | 3/10 |
| QB | trailing attempts | <30 (827) | 0.05 | 0/10 |
| QB | | 30–36 (861) | 0.05 | 0/10 |
| QB | | >=36 (549) | 0.06 | 0/10 |

The WR tier differences are statistically detectable but small, and they mostly reflect the between-player effect from 3b (all tiers < pooled on Rec–ReYd), not a real change in structure. The one real difference is **workhorse RBs** (>=55% carry share): RuYd–Rec is −0.12 vs +0.02 pooled, and RuYd–ReYd is −0.07. Bell-cows trade carries for catches depending on game script. Effect on fantasy points is second order. Recommendation: **one matrix per position from 3b/3c; optionally add a bell-cow RB override for RuYd–Rec/ReYd.** Spend the effort on per-player TD latent correction and on validation, not on tiers.

## 4. Base rates for Scoring Stats with no market

Qualifying games (filters from section 3), 2021–25 REG.

### Fumbles lost and 2-pt conversions, per game

| Pos | n | FL total/g | sack FL | rush FL | rec FL | P(FL>=1) | P(FL>=2) | pass 2pt/g | rush+rec 2pt/g |
|---|---|---|---|---|---|---|---|---|---|
| QB | 2,709 | 0.204 | 0.143 | 0.061 | 0.000 | 0.186 | 0.016 | 0.074 | 0.015 |
| RB | 4,229 | 0.063 | 0.000 | 0.049 | 0.013 | 0.062 | 0.002 | 0.000 | 0.018 |
| WR | 7,130 | 0.026 | 0.000 | 0.001 | 0.021 | 0.026 | 0.000 | 0.000 | 0.016 |
| TE | 3,172 | 0.019 | 0.000 | 0.000 | 0.017 | 0.019 | 0.000 | 0.000 | 0.015 |

Per touch (carries + receptions), pooled: RB 0.0044, WR 0.0065, TE 0.0058 fumbles lost. QB: 0.062 fumbles lost per sack, which is 70% of QB fumbles lost.

### By usage tier (trailing usage; it matters for fumbles, less for 2-pt)

| Pos | Tier | n | FL/g | rush+rec 2pt/g | pass 2pt/g | touches/g |
|---|---|---|---|---|---|---|
| WR | tgt share <15% | 2,329 | 0.018 | 0.011 | | 2.9 |
| WR | 15–22% | 1,821 | 0.026 | 0.020 | | 4.1 |
| WR | >=22% | 1,947 | 0.037 | 0.019 | | 5.5 |
| TE | <12% | 1,227 | 0.016 | 0.007 | | 2.4 |
| TE | 12–18% | 776 | 0.022 | 0.025 | | 3.7 |
| TE | >=18% | 714 | 0.027 | 0.024 | | 4.8 |
| RB | carry share <35% | 1,404 | 0.035 | 0.009 | | 10.1 |
| RB | 35–55% | 1,165 | 0.081 | 0.016 | | 15.9 |
| RB | >=55% | 1,061 | 0.086 | 0.026 | | 18.8 |
| QB | trailing att <30 | 827 | 0.192 | 0.017 | 0.068 | |
| QB | 30–36 | 861 | 0.189 | 0.016 | 0.072 | |
| QB | >=36 | 549 | 0.230 | 0.011 | 0.098 | |

Model fumbles as **Poisson(rate_per_touch × expected touches)** for RB/WR/TE, where expected touches come from the carries/receptions marginals. Fumble rates roughly double from the lowest to the highest tier, close to proportional to touches. For QBs, use rush-attempt fumbles plus about 0.06 × expected sacks. A flat 0.20/g also works, since QB FL barely varies by tier. 2-pt conversions are worth at most about 0.04 expected fantasy points (2 pts × 0.02). A flat position rate is fine: rush+rec 0.016/g, plus QB passing 0.074/g. Optionally scale by implied team total.

At these sizes, a −2 per fumble lost moves P5 for QBs (P(FL >= 1) = 19%) and has little effect elsewhere. Sample them as independent Poisson/Bernoulli. For QBs, a weak link to pass attempts or sacks is possible, but it isn't worth modelling yet.

### Rushing vs receiving split of TDs (for splitting an anytime-TD price)

| Pos | n | rush TDs | rec TDs | rushing share | P(rush or rec TD >= 1) | P(2+) | P(both kinds) |
|---|---|---|---|---|---|---|---|
| QB | 2,709 | 490 | 6 | 98.8% | 0.157 | 0.025 | 0.0004 |
| RB | 4,229 | 1,631 | 405 | **80.1%** | 0.376 | 0.088 | 0.026 |
| WR | 7,130 | 73 | 2,123 | 3.3% | 0.264 | 0.040 | 0.003 |
| TE | 3,172 | 17 | 742 | 2.2% | 0.205 | 0.032 | 0.0003 |

RB split by trailing carry share: 76% rushing (<35%), 81% (35–55%), 82% (>=55%). It varies much more by player: among RB player-seasons with >=5 TDs, the rushing share has P10/P25/P50/P75/P90 of 0.55/0.70/0.83/1.0/1.0. So for RBs, split an anytime-TD probability using the player's own prop ratio, e.g. the ratio of rushing-yards to receiving-yards lines or a rush-TD market if one exists. Fall back to a shrunk player history and finally to 0.80. For WR/TE, treat the anytime TD as receiving (97–98%). For QBs, anytime TD means rushing TD.

## 5. Recommendations for the copula

1. **Stats per position (copula dimensions):**
   - QB: PaYd, PaTD, INT, RuYd, RuTD. The 5×5 matrix is almost block-diagonal (passing block ⟂ rushing block; |rho| <= 0.04 across blocks except RuTD–PaTD at −0.17 latent, which is goal-line substitution). Keep the full 5×5; it costs nothing.
   - RB: RuYd, RuTD, Rec, ReYd, ReTD. Also nearly block-diagonal (rushing ⟂ receiving).
   - WR/TE: Rec, ReYd, ReTD, plus RuYd only if a rushing market exists. Its correlations are about 0, so sampling it independently is fine.
   - Carries and targets are not Scoring Stats, but if the prop markets exist (rush attempts, sometimes receptions only), they are the best anchors for the volume part of the model: Car–RuYd 0.84, Tgt–Rec 0.85–0.92.
   - Fumbles lost and 2-pt: outside the copula, sampled independently from section 4's rates scaled by expected volume.
2. **Matrix to use:** the within-player latent R (3c) per position. Pool 2021–2025 (refresh yearly; adding 2026 weeks as they finish is fine). No usage tiers, except an optional bell-cow RB override (RuYd–Rec ≈ −0.12). Repair to PD if any edits are made (eigenvalue clip). The tables above are already PD.
3. **Discrete margins:**
   - Receptions and TDs are integer-valued. Invert with the step-CDF quantile `x = min{k : F(k) >= u}`.
   - Correlations for TD pairs must be latent (3c), not observed Spearman, or the joint tails come out too thin. The correction depends on P(TD) (more ties need more correction). Recalibrating per player by bisection at run time is cheap.
   - Yards given receptions = 0 must be 0. A Gaussian copula will sometimes draw Rec = 0 with ReYd > 0 (and negative yards with Rec > 0 can occur legitimately). Either post-process (if Rec == 0, set ReYd = 0 and ReTD = 0) or use the conditional model from RESEARCH.md for Rec→ReYd. Rec = 0 games are rare for WR1s but common for TE2s and RB2s, so check how often it fires.
   - Yards marginals are integer-valued but can be treated as continuous.
   - QB rushing TDs and INTs are mostly 0/1. Same tie issue.
4. **Sample sizes:** pooled per position, 2,709 QB, 4,229 RB, 7,130 WR and 3,172 TE player-games. Within-player: 67 QBs / 123 RBs / 211 WRs / 92 TEs with >=6 games in a season. With n >= 2,400, the SE of each rho is about 0.02. Season-to-season noise (0.02–0.10) is larger than sampling error, so differences below about 0.05 aren't worth acting on.
5. **Validation hook:** the pooled (3a) vs within (3b) choice is the biggest modelling lever found here, at up to 0.12 per pair. Make it a config switch and let the PIT/CRPS backtest decide.
