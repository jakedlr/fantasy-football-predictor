# Live leaderboard: expected and projected points

Research for a public live NFL fantasy leaderboard for the current week (full PPR, custom K and D/ST). Each player shows one of three numbers:

- **Final:** actual points, once his Game is final.
- **In progress:** a projected final total.
- **Not started:** expected points.

Written 2026-11-18. Part A is about what `ffp` can do today. Part B covers external sources and methods. File references are to `main` at `cb87daf`.

## Summary

- **Not started:** use the **mean of `ffp`'s Score Distribution**. It is already computed (`ScoreDistribution.mean`) but not reported. For players with no props, fall back to **ESPN's public weekly projections**, rescored under our rules.
- **In progress:** `actual so far + pregame expected stats × fraction of the Game remaining`, rescored **per stat** under our Scoring Rules. This is what ESPN (reportedly) and Ottoneu do. Take the fraction from the game clock, not wall-clock time. A game-state multiplier and a player-usage update are later refinements.
- **What `ffp` can't do today:**
  - It refuses to predict a Game that has kicked off. Books pull pregame props at kickoff, and `ffp` drops started Games.
  - So the leaderboard must **freeze each player's last pregame expectation before kickoff** and store it.
  - It also has no mode to enumerate every player with props on the slate. It predicts only the names in INPUT.md.
- **Actual points live** need a live stats feed. nflverse is hours behind. The candidates are Sleeper's stats endpoint and ESPN. Both are undocumented, and Sleeper's mid-game freshness is unverified.

---

## Part A. What `ffp` does today

### A1. Player resolution and IDs

- **Canonical key: nflverse `gsis_id`.**
  - `Player(gsis_id, name, position, team)`: `src/ffp/players.py:52-59`.
  - D/STs get the synthetic id `DST-<team>`: `players.py:191-193`.
- **Roster.**
  - Source: `nflreadpy.load_rosters_weekly(season)` (`players.py:467-470`).
  - Each player's latest weekly row gives his current team, so traded players resolve correctly. CUT and RET are dropped: `players.py:74-105`.
- **Name matching.**
  - `normalize_name` does an ASCII fold, lowercases, strips punctuation and drops Jr/Sr/II–V: `src/ffp/names.py:10-25`.
  - A small alias table follows (`ALIASES`, `players.py:41-49`; `name_key`, `players.py:115-118`).
  - Only QB/RB/WR/TE/K count. Ambiguity needs a `Name (TEAM)` hint: `players.py:145-178`.
- **Book names to players.** A book's `PropMarket.player` is the name string as the book spells it (`src/ffp/markets.py:71-81`). It is matched to the target by `name_key` equality inside the target's Game (`src/ffp/predict.py:835-847`). Books carry no player ids.
- **Leaderboard implication.** IDs map across sources via crosswalks:
  - Sleeper's `/v1/players/nfl` carries `gsis_id`, `espn_id` and others.
  - dynastyprocess `db_playerids.csv` and nflverse `ff_playerids` do the same.
  - So `gsis_id` can stay the leaderboard's key.

### A2. Can `ffp` enumerate every player with props on a slate?

**Not today.**

- The CLI reads names from INPUT.md (`src/ffp/cli.py:161`, `predict.py:163-178`), resolves them (`predict.py:189-213`) and fetches odds only for those targets' Games (`cli.py:188-195`).
- The data to enumerate is mostly fetched already:
  - **DraftKings** returns every listed Game's players in one set of subcategory responses per stat (`predict.py:305-308`, `_draftkings_book` at `predict.py:369-428`).
  - **FanDuel** needs one request per Game and tab, and only the wanted Games are fetched (`predict.py:430-468`).
  - So `BookOdds.markets` holds every priced player in those Games.
- **What an `ffp predict --slate` mode would need:**
  1. Pass all of the week's unstarted scheduled Games to `load_live_books`.
  2. Collect the distinct `(market.player, market.team)` pairs.
  3. Resolve each pair to a roster entry using `name_key` and the book's team. `PropMarket.team` is set when known (`markets.py:81`), which resolves most same-name ambiguities.
  4. Add K and D/ST targets for every team: 2 per team, from Game Lines only.
- Unresolvable book names (new aliases) should be logged rather than fatal.

### A3. Full-slate run time (estimate)

Measured baseline (README.md:11, 114; PLAN.md:243-246, 292-306):

- A fresh 9-player lineup across 7 Games takes 16–19 s.
- Odds take about 9 s: DraftKings 22 requests, FanDuel 30.
- Live prediction takes 11.1 s in one process and 6.5 s with workers. That is about 1.2 s per player serially.

| Step | 9-player lineup (measured) | Full Sunday slate (estimate, ~14 Games) |
|---|---|---|
| DraftKings requests | 22 | ~25 (cost is per stat, not per Game) |
| FanDuel requests | 30 (7 Games) | ~1 + 14 Games × 4–5 tabs ≈ 60–70 |
| Odds wall time | ~9 s | ~20–25 s at the same Pacing (3 in flight, 0.3 s apart, `src/ffp/sources/http.py:100-109`) |
| Players to fit | 9 | ~250–350 skill players with props, plus 28 K and 28 D/ST |
| Prediction | 6.5 s | ~300 × 1.2 s ≈ 6 min serial. This machine has 20 CPUs, and `default_jobs` leaves a fifth free (`predict.py:607-...`, README `--jobs`), so ~16 workers make it **≈ 25–40 s**. |
| **Total** | 16–19 s | **≈ 1–1.5 min** |

Caveats:

- These are extrapolations. The player count per Game and FanDuel's tabs per Game should be measured with a real slate run.
- More requests per run raise the chance of a 429 or 403. Pushback slows a Book to one request at a time (README.md:117), which could stretch the odds step to minutes.
- For a mean only, the 20,000 Monte Carlo draws (`src/ffp/simulate.py:13`) are unnecessary for skill players (see A4). Fitting, not sampling, probably dominates, but this hasn't been profiled.
- DraftKings lists next Sunday's player props only from about Sunday night or Monday (`research/sportsbook-markets.md:314`). A Thursday-morning slate run gets props for Thursday's Game first.

### A4. The Score Distribution mean

- `ScoreDistribution` holds the 20,000 sampled point totals and has a `mean` property (`src/ffp/simulate.py:41-66`).
- **The mean is not reported.**
  - `PlayerResult` has percentiles, flags, per-stat details and chances, but no mean (`src/ffp/report.py:29-42`).
  - `output.json` writes only those fields (`report.py:115-127`).
  - `_player_result` (`predict.py:883-...`) and `_team_result` (`predict.py:920-958`) hold the `scores` object and could pass `scores.mean` through. That is a small change. Note that P50 is not the mean: Score Distributions are right-skewed.
- **The mean is cheap for QB/RB/WR/TE.**
  - Offensive scoring is linear: `score()` is `Σ points_per_unit × stat` (`src/ffp/scoring.py:263-269`).
  - The copula keeps each stat's marginal, including the no-catch coupling (`simulate.py:114-141`: "Overall, Y then has exactly its own distribution").
  - So E[points] = Σ rule × E[stat], from the fitted marginals and the Base Rate stats (`predict.py:849-865`).
  - Per-stat means are already in `output.json` (`StatDetail.mean`, `report.py:19-27`).
  - The leaderboard needs those **per-stat means** anyway, for in-progress proration (Part C).
- **K and D/ST are nonlinear.** FG distance tiers and Points/Yards Allowed tiers (`scoring.py:286-316`; `src/ffp/rules/espn-league.json`) mean the expected points need the simulated samples. Those are already drawn (`kicker_scores`, `src/ffp/kicker.py:91`; `dst_scores`, `src/ffp/dst.py:141`) and cheap.

### A5. Snapshots and HTTP caching

- **HTTP cache** (`src/ffp/sources/http.py:24-25, 206-248, 292-337`).
  - Every GET is cached to `.cache/http/<sha256(url)>.json` and reused for `REUSE_SECONDS = 60`.
  - A leaderboard re-running more often than every 60 s gets cached odds, which is fine.
  - The cache is keyed by URL and not by Book state, so it does not distinguish pregame from in-play.
- **Snapshots** (`src/ffp/snapshots.py:1-12, 30-60`).
  - Each live run writes `snapshots/<date>/<HHMMSSZ>/` holding `manifest.json`, the raw responses and `fair_points.json`.
  - They are price history for backtests (ADR 0001). They do **not** store the Score Distribution, percentiles or means.
  - Replaying a Snapshot needs the fixture loader, which expects fixed fixture filenames (`predict.py:230-272`), so a Snapshot can't be fed to `--fixtures` as is.
  - So the leaderboard must persist its own pregame expectations: per player and Game, the mean, per-stat means, source and timestamp.

### A6. What happens once a Game kicks off

`ffp` predicts each player's **next** Game and deliberately ignores Games already under way:

- **Schedule side.**
  - `game_in_progress` treats a Game as in progress from kickoff until nflverse posts a score, up to `IN_PROGRESS_WINDOW = 8 h` (`players.py:447-464`).
  - `resolve_targets` skips such players with "has already kicked off; rerun after it ends to predict the next one" (`predict.py:204-220`).
  - After that window, `next_game` picks the following week's Game (`players.py:429-444`).
- **Book side.**
  - DraftKings marks any event not `NOT_STARTED` as started (`src/ffp/sources/draftkings.py:43-44`).
  - FanDuel marks an event started if it is `inPlay` or its kickoff has passed (`src/ffp/sources/fanduel.py:67-90`).
  - Started Games' markets and lines are dropped (`draftkings.py:170, 193, 223`; `fanduel.py:261, 296, 317, 344`), and `_same_game` rejects them (`predict.py:504-519`).
- **The books do run in-play markets.** DraftKings has live-only categories (998, 1054, 1680, 1873) and markets carrying `statistics.live` (`research/sportsbook-markets.md:17, 35, 146`). `ffp` does not parse them.

**Consequence.** For a Game in progress or final, `ffp` gives nothing for this week. The leaderboard must:

1. run `ffp` (slate mode) shortly before each kickoff window (TNF; Sunday early, late and SNF; MNF);
2. freeze each player's latest pregame per-stat means and total mean;
3. use the frozen numbers for proration once the Game starts.

The last run before kickoff is also the best-informed one (inactives are known about 90 min before kickoff), so freezing loses nothing.

**Actual stats are also not live in `ffp`.** It reads nflverse weekly player stats (`src/ffp/weekly.py`, `src/ffp/outcomes.py:40-68`). nflverse updates those within about a day (`research/nflverse-data.md:453`), and its play-by-play about 30 min after each game window. `outcomes.fantasy_points` (`outcomes.py:62-68`) is useful for the final reconciliation of "Final" rows the next day, but not live.

---

## Part B. External research

Six public repositories were studied in depth, each by its own research agent.

### B1. nflverse/nflfastR: game clock, win probability, game state

- **Clock.**
  - In regulation, `game_seconds_remaining = quarter_seconds_remaining + 900 × (4 − qtr)`.
  - In overtime, it is just the seconds left in the overtime period.
  - Source: [helper_add_nflscrapr_mutations.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_nflscrapr_mutations.R) (about lines 296–315).
- **WP model.**
  - An xgboost model on `receive_2h_ko`, `home`, `half_seconds_remaining`, `game_seconds_remaining`, `Diff_Time_Ratio`, `score_differential`, down, distance, yard line and timeouts.
  - `Diff_Time_Ratio = score_differential / exp(−4 × elapsed_share)`, with `elapsed_share = (3600 − game_seconds_remaining)/3600`.
  - Sources: [helper_add_ep_wp.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_ep_wp.R), trained in [data-raw/MODELS.R](https://github.com/nflverse/nflfastR/blob/main/data-raw/MODELS.R).
- **`vegas_wp`.**
  - A Stern (1994) probit on the spread, with variance shrinking as `((t+ε)/3600)^α`, plus monotone xgboost trees on top.
  - Sources: [helper_add_vegas_wp.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_vegas_wp.R), [wp_vegas_model.R](https://github.com/nflverse/nflfastR/blob/main/data-raw/wp_vegas_model.R).
  - Overtime has its own model: [helper_add_ot_wp.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_ot_wp.R).
- **Scoring a live state.**
  - `calculate_win_probability()` and `calculate_expected_points()` score any game state you build ([ep_wp_calculators.R](https://github.com/nflverse/nflfastR/blob/main/R/ep_wp_calculators.R)). They are R only.
  - `nflreadpy.load_pbp()` gives precomputed `wp`, `vegas_wp`, `ep` and `xpass` on historical plays but has no calculator ([load_pbp.py](https://github.com/nflverse/nflreadpy/blob/main/src/nflreadpy/load_pbp.py)).
  - `nfl_data_py` is deprecated ([README](https://github.com/nflverse/nfl_data_py/blob/main/README.md)).
- **Not live.** The nflverse play-by-play pipeline runs daily and about 30 min after each game window ([update_data.yaml](https://github.com/nflverse/nflverse-pbp/blob/main/.github/workflows/update_data.yaml)).
- **Game state moves fantasy volume.** The xpass model uses `wp`, `vegas_wp`, score differential, quarter and half-seconds as inputs ([helper_add_xpass.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_xpass.R)). Background reading:
  - [nflfastR EP/WP/CP models](https://www.opensourcefootball.com/posts/2020-09-28-nflfastr-ep-wp-and-cp-models/)
  - [run/pass tendencies](https://www.opensourcefootball.com/posts/2020-09-07-estimating-runpass-tendencies-with-tidymodels-and-nflfastr/)
- **Use for us:**
  - the clock formula for the fraction remaining;
  - historical play-by-play (via `nflreadpy`) for fitting pace and pass-rate multipliers by score differential or `Diff_Time_Ratio`;
  - a simple logistic on `Diff_Time_Ratio` plus spread as a Python stand-in for WP.

### B2. ffverse/ffopportunity: expected fantasy points

- **Descriptive, not predictive.** It gives the points an average player would score from the opportunities he actually got. Eight per-play xgboost models (rush yards/TD/first down; completion, YAC, TD, first down and INT for passes) run on nflverse play-by-play from 2006–2020.
  - Sources: [README](https://github.com/ffverse/ffopportunity/blob/main/README.md), [ep_predict.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_predict.R), [ep_preprocess.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_preprocess.R).
- **Coverage and scoring.**
  - QB/RB/WR/TE only, **no K or D/ST**.
  - Scoring is hardcoded: full PPR, 4-pt pass TD, 0.04 per pass yard, −2 INT, −2 fumble lost ([ep_summarize.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_summarize.R)). That matches our league's offense, and component `_exp` columns allow rescoring.
- **Data and latency.**
  - Files: `https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.parquet` (plus pbp_pass and pbp_rush).
  - In Python: `nflreadpy.load_ff_opportunity()` ([load_ffverse.py](https://github.com/nflverse/nflreadpy/blob/main/src/nflreadpy/load_ffverse.py)).
  - The update cron runs after each game window ([ep-update-data.yaml](https://github.com/ffverse/ffopportunity/blob/main/.github/workflows/ep-update-data.yaml)). Real latency is hours.
- **Use for us:**
  - A trailing average of `total_fantasy_points_exp` is a cheap, stable **prior** for skill players without props.
  - Not usable live: porting the R preprocessing and features (ep, vegas_wp, xpass) is heavy, and it measures what already happened, not what remains.

### B3. cwendt94/espn-api: ESPN projections and live scoring

Permalinks are at commit [700e15d](https://github.com/cwendt94/espn-api/tree/700e15ddbb91124f031473d2411c8a6285944274).

- **Endpoint.** `lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{yr}/segments/0/...` with the views `mMatchupScore`, `mScoreboard`, `kona_player_info` and `kona_playercard`.
  - Sources: [espn_requests.py](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/requests/espn_requests.py), [league.py](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/league.py).
- **Stats layout.** In `stats[]`, `statSourceId` 0 is actual and 1 is projected; `statSplitTypeId` 1 is the week. Raw stats are under `stats`, and points under `appliedTotal`.
  - Source: [player.py#L67-L117](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/player.py#L67-L117).
  - Stat-id map: [constant.py#L116-L276](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/constant.py#L116-L276). A few ids are marked unsure there.
- **No league or login needed (verified by the agent on 2026-11-18).**
  - Request: `GET .../seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId=12` with an `x-fantasy-filter` header (slot ids, `filterStatsForTopScoringPeriodIds` = `11{yr}{wk}`).
  - It returned week-12 projections as **fractional raw stat lines** for every position:
    - K: FG made by distance band.
    - D/ST: sacks, INT, fumble recoveries, TDs, Points Allowed, Yards Allowed, and Points Allowed bucket values that look like tier probabilities.
  - So it can be rescored under our custom rules.
- **Live projections.**
  - **Team level.** `totalProjectedPointsLive` is updated during Games ([box_score.py#L78-L80](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/box_score.py#L78-L80)), but it needs a real league.
  - **Player level.** The projection entry stays pregame ([issue #61](https://github.com/cwendt94/espn-api/issues/61)).
  - **How ESPN computes it.** A write-up on ESPN's live views says the front end computes **live projected = live points + original projection × fraction of game remaining**, and the ceiling the same way from `appliedTotalCeiling` ([blog post](https://mkdocs-4a2.pages.dev/blog/2024-10-06/espn-fantasy-football-in-spring-boot-live-scoring-and-projected-ceilings/)). It is a secondary source with no code.
  - Also relevant: [issue #571](https://github.com/cwendt94/espn-api/issues/571) on season vs week keys.
- **Use for us.** This is the best free pregame fallback: every position, raw stats, one cookie-free request per position. The endpoint is undocumented and has no published terms, so cache it and fail safe.

### B4. kiernann/fflr: how an existing client shows live projections

- `live_scoring()` computes nothing itself.
  - It reads ESPN's team-level `totalPointsLive` and `totalProjectedPointsLive` from `mScoreboard` ([live.R#L21-L60](https://github.com/kiernann/fflr/blob/master/R/live.R#L21-L60)).
  - "Yet to play" is decided by kickoff time only ([live.R#L61-L102](https://github.com/kiernann/fflr/blob/master/R/live.R#L61-L102)).
  - Player stats use the same `statSourceId`/`statSplitTypeId` keys ([roster.R#L78-L96](https://github.com/kiernann/fflr/blob/master/R/roster.R#L78-L96); key layout comment in [players.R#L296-L298](https://github.com/kiernann/fflr/blob/master/R/players.R#L296-L298)).
- espn-api's `BoxPlayer.game_played` is all-or-nothing: 100 once 3 h have passed since kickoff, else 0 ([box_player.py](https://github.com/cwendt94/espn-api/blob/master/espn_api/football/box_player.py)). That mislabels delayed or long Games.
- **Ottoneu** uses actual + Rotowire projection × fraction of game time remaining, "linearly decreasing", and ignores overtime ([forum thread](https://community.ottoneu.com/t/matchup-projection-updates-as-games-are-played/6141)). Its example: 20-point projection, 10 points after Q1, gives 10 + ¾ × 20 = 25.
- **Underdog** describes in-game projections as full-game targets but does not publish its method ([help article](https://help.underdogfantasy.com/en/articles/10361705-in-game-projections), which now redirects and returned 403).
- No open-source code was found that goes further (game script, usage updates). A [r/fantasyfootballcoding thread](https://axebps-redlib.hf.space/r/fantasyfootballcoding/comments/1elhw48/live_projections_api) asks for a free live projection API and got no answer.
- **Pitfalls these sources show:**
  - wall-clock heuristics mislabel delayed or long Games;
  - nobody handles players who leave injured;
  - overtime is ignored.

### B5. Sleeper projections and stats (smallery/sleeper_fantasy_api, plus the raw endpoints)

- **The wrapper.** [smallery/sleeper_fantasy_api](https://github.com/smallery/sleeper_fantasy_api) is MIT licensed with 2 stars. It only calls the old `v1/projections/nfl/regular/{season}/{week}` route ([projections_endpoint.py](https://github.com/smallery/sleeper_fantasy_api/blob/main/sleeper_api/endpoints/projections_endpoint.py)). Calling the endpoints directly is simpler.
- **Projections** (public, no auth).
  - Endpoint: `https://api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular&position[]=QB&...&position[]=K&position[]=DEF`.
  - Each row has `player_id`, `player{…, injury_status}`, `team`, `opponent`, `game_id` and `stats`.
  - **`company: "rotowire"`**, so these are Rotowire's projections.
  - Stats are raw:
    - skill players: `rec, rec_yd, rush_yd, pass_td, fum_lost, …`
    - K: `fgm_0_19 … fgm_50p`, `xpm`
    - D/ST: `sack, int, def_td, pts_allow, yds_allow`
  - Pre-scored totals are also included (`pts_ppr`, `pts_half_ppr`, `pts_std`).
- **Actual stats.**
  - Endpoint: `https://api.sleeper.com/stats/nfl/{season}/{week}?...`, with `company: "sportradar"`.
  - Fields include per-quarter splits such as `q1_pass_yd` and snaps (`off_snp`).
  - **Untested during a live Game.** Responses carry `cache-control: public, s-maxage=3600`, so the CDN may serve stats up to about an hour old. Test on a Sunday before relying on it for live actuals.
- **IDs.** `https://api.sleeper.app/v1/players/nfl` (about 5 MB) maps `player_id` to `gsis_id`, `espn_id` and others. Also [dynastyprocess db_playerids.csv](https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv).
- **Terms.** [docs.sleeper.com](https://docs.sleeper.com/) says the API is free for **non-commercial** use, under 1,000 calls/min, or you risk an IP block. The projections and stats routes are undocumented.
- **Use for us:** a second fallback projection (Rotowire), injury status, and a candidate live stats feed. Poll from one server-side job; never let browsers call Sleeper.

### B6. FantasyFootballAnalytics/ffanalytics: aggregating free projections

- **Sources.** It scrapes CBS, ESPN, FantasyPros, FantasySharks, FFToday, FleaFlicker, NFL, RTSports, Walterfootball and FanDuel/NumberFire ([scrape_funcs.R](https://github.com/FantasyFootballAnalytics/ffanalytics/blob/master/R/scrape_funcs.R)).
- **Status on 2026-11-18:**
  - **ESPN:** works. The same `kona_player_info` JSON as B3, plus `X-Fantasy-Source: kona`.
  - **CBS:** returns 200, HTML table ([example](https://www.cbssports.com/fantasy/football/stats/RB/2026/12/projections/ppr/)).
  - **NFL.com:** returns 200, HTML table ([example](https://fantasy.nfl.com/research/projections?position=2&statCategory=projectedStats&statSeason=2026&statType=weekProjectedStats&statWeek=12)).
  - **FFToday:** broken (database error).
  - **FantasySharks:** returned an empty CSV.
  - **Yahoo:** no longer publishes projections.
  - **FanDuel/NumberFire:** uses GraphQL and was fixed in Sept 2026; not tested.
- **Aggregation** ([calc_projections.R](https://github.com/FantasyFootballAnalytics/ffanalytics/blob/master/R/calc_projections.R)).
  - Averaging is plain, robust (Hodges–Lehmann) or weighted (ESPN 0.157, FFToday 0.151, CBS 0.145, NFL 0.140).
  - `return_raw_stats=TRUE` averages **stat lines**, which suits rescoring.
  - Scoring objects cover kicker distance bands and D/ST Points Allowed brackets ([scoring_rules.R](https://github.com/FantasyFootballAnalytics/ffanalytics/blob/master/R/scoring_rules.R)).
  - IDs are MFL ids with name, team and position fallback.
- **Maintenance and licence.** It is GPL, R, last committed 2026-09-09, and breaks most seasons. Port ideas, not code.
- **FantasyPros consensus pages** ([example](https://www.fantasypros.com/nfl/projections/rb.php?week=11&scoring=PPR)) embed full stat lines as JSON, but logged-out visitors get 10 rows. Full data is the paid [API](https://www.fantasypros.com/api-data/). [robots.txt](https://www.fantasypros.com/robots.txt) disallows `/api/` and `/ajax/`. Not usable.

---

## Part C. Options

### C1. Expected points for a player whose Game hasn't started

| Option | Coverage | Quality | Cost and risk |
|---|---|---|---|
| **N1. `ffp` Score Distribution mean** (props) | Players with props, plus K and D/ST for every team with Game Lines | Best: market-implied, backtested P5–P95 coverage of 91.3% | Needs a slate mode (A2) and a `mean` field (A4). About 1–1.5 min per slate run. Sportsbook scraping is for personal use (README "Data and licences"), so publishing book-derived numbers publicly is a terms question to settle. |
| **N2. ESPN `kona_player_info` stat lines, rescored** | Every rostered player, all positions incl. K and D/ST | Good, a standard industry projection | One request per position. Undocumented, no published terms. |
| **N3. Sleeper (Rotowire) stat lines, rescored** | Every player, all positions | Good | Non-commercial terms, undocumented routes, 1 h CDN cache |
| **N4. ffopportunity trailing expected points** | QB/RB/WR/TE | Fair: backward-looking, no matchup or injury | Free, one `nflreadpy` call, CC-BY data |
| **N5. Backlog "game-line fallback"** (Implied Team Total × usage share, PLAN.md backlog) | Anyone on a team with Game Lines | Fair to good | Not built yet |
| **N6. Blend** (for example N1 where props exist, else robust average of N2+N3) | All | Best coverage | More moving parts. Label the source per row. |

Notes:

- Players with props are the ones who matter on a leaderboard. Deep backups without props get N2.
- Show the source per row (`props`, `ESPN`, ...) so viewers know what they're looking at.
- A player ruled inactive gets 0. Injury status comes from Sleeper's `injury_status` or the inactive lists.

### C2. Projected final total for a player whose Game is in progress

Let `f` be the fraction of the Game remaining:

- in regulation, `f = game_seconds_remaining / 3600`, using nflfastR's clock formula;
- at halftime, `f = 0.5`;
- in overtime, `f = 0` (or a small constant);
- Final: `f = 0`, and the projection is the actual.

Take `f` from the live game state (quarter and clock), never from wall-clock time since kickoff.

| Option | Formula | Pros | Cons |
|---|---|---|---|
| **P1. Linear proration of points** (ESPN as reported; Ottoneu) | `actual_pts + E[pts] × f` | Trivial; matches what users see elsewhere | Wrong for tiered scoring (D/ST Points Allowed, FG distance); ignores game script and injuries |
| **P2. Linear proration per stat, then rescore** | `stats_now + E[stat] × f`, scored under our Scoring Rules | Same as P1 for offense (linear rules), but **correct for D/ST tiers and kickers**: project Points and Yards Allowed to the end of the Game, then apply the tier. Uses `ffp`'s per-stat means directly. | Needs live per-stat actuals and per-stat pregame means (both planned anyway) |
| **P3. P2 × game-state multiplier** | Remaining volume scaled by pace and pass rate given score differential or WP (fitted offline on nflverse play-by-play, nflfastR `Diff_Time_Ratio` idea) | Trailing teams' QB/WR/TE up and RBs down, leading teams the reverse; captures garbage time | An offline fit and validation needed; a modest gain for a leaderboard |
| **P4. P2 with a usage update** | Shrink the remaining expectation toward the player's observed share so far, e.g. `E[stat]×f × (k + observed_share/expected_share × w)/(k + w)` | Reacts to a role change or a big early workload | Risk of overreacting to noise early in the Game; needs tuning on historical play-by-play |
| **P5. Live sportsbook props** (DraftKings in-play categories) | Remaining = live line's implied mean − current stat | Market-informed, reacts to injuries | Live markets suspend often; heavy scraping during Games; `ffp` doesn't parse them; terms. Not recommended. |

Edge cases for any option:

- **Player out mid-game:** set the remainder to 0 when he is ruled out (live injury feed).
- **Delayed Game:** use the game status, not the kickoff time.
- **Overtime:** clamp `f` at 0, or a small constant.
- **Stat corrections:** reconcile "Final" rows the next day against nflverse (`outcomes.fantasy_points`).

### C3. Live actuals

Options for the live actuals feed:

- **Sleeper `/stats/nfl/{season}/{week}`**: per-stat, all positions. Live freshness is unverified, and the 1 h `s-maxage` is a concern.
- **ESPN**: per-game box scores and the scoreboard (`site.api.espn.com`, which returned 403 to a plain fetch here).
- **A paid feed** (SportsDataIO and similar) if neither proves fresh enough.

nflverse is fine for next-day reconciliation only.

## Recommendation

1. **Not started: N1 with an N2 fallback.**
   - Add a `mean` to `PlayerResult` and `output.json` (A4).
   - Add a slate mode that predicts every player with props plus every team's K and D/ST (A2).
   - Run it on a schedule, for example hourly on game days and before each kickoff window.
   - Fill players without props from ESPN's stat lines rescored under our rules.
   - Store per-stat means with the source and run time, keyed by `gsis_id` and `game_id`.
2. **At kickoff:** freeze the last pregame per-stat means (A6). `ffp` will refuse to predict that Game from then on.
3. **In progress: P2.** Project each stat as `now + pregame mean × f`, with `f` from quarter and clock, then score with `ffp.scoring`. Zero the remainder for players ruled out. Add P3 later if a backtest on nflverse play-by-play shows it beats P2 on halftime-to-final error.
4. **Final:** live actuals, scored under our rules, then reconciled next day from nflverse.
5. **Before building:**
   - Verify a live stats feed's latency on a Sunday (Sleeper's CDN cache; ESPN's box score).
   - Measure a real slate run's time and request count.
   - Settle whether book-derived and ESPN/Sleeper-derived numbers can be shown publicly. Both books' data is personal-use in this repo, and Sleeper's API is non-commercial only.

## Sources

- **nflfastR:** [helper_add_ep_wp.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_ep_wp.R), [helper_add_vegas_wp.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_vegas_wp.R), [helper_add_ot_wp.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_ot_wp.R), [helper_add_nflscrapr_mutations.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_nflscrapr_mutations.R), [helper_add_xpass.R](https://github.com/nflverse/nflfastR/blob/main/R/helper_add_xpass.R), [ep_wp_calculators.R](https://github.com/nflverse/nflfastR/blob/main/R/ep_wp_calculators.R), [utils.R](https://github.com/nflverse/nflfastR/blob/main/R/utils.R), [MODELS.R](https://github.com/nflverse/nflfastR/blob/main/data-raw/MODELS.R), [wp_vegas_model.R](https://github.com/nflverse/nflfastR/blob/main/data-raw/wp_vegas_model.R)
- **nflverse play-by-play cadence:** [update_data.yaml](https://github.com/nflverse/nflverse-pbp/blob/main/.github/workflows/update_data.yaml)
- **nflreadpy:** [load_pbp.py](https://github.com/nflverse/nflreadpy/blob/main/src/nflreadpy/load_pbp.py), [load_ffverse.py](https://github.com/nflverse/nflreadpy/blob/main/src/nflreadpy/load_ffverse.py)
- **nfl_data_py deprecation:** [README](https://github.com/nflverse/nfl_data_py/blob/main/README.md)
- **Open Source Football:** [nflfastR EP/WP/CP models](https://www.opensourcefootball.com/posts/2020-09-28-nflfastr-ep-wp-and-cp-models/), [run/pass tendencies](https://www.opensourcefootball.com/posts/2020-09-07-estimating-runpass-tendencies-with-tidymodels-and-nflfastr/)
- **ffopportunity:** [repo](https://github.com/ffverse/ffopportunity), [README](https://github.com/ffverse/ffopportunity/blob/main/README.md), [ep_predict.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_predict.R), [ep_preprocess.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_preprocess.R), [ep_summarize.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_summarize.R), [ep_load.R](https://github.com/ffverse/ffopportunity/blob/main/R/ep_load.R), [ep-update-data.yaml](https://github.com/ffverse/ffopportunity/blob/main/.github/workflows/ep-update-data.yaml), [nflreadr load_ff_opportunity](https://rdrr.io/cran/nflreadr/man/load_ff_opportunity.html)
- **espn-api:** [repo @700e15d](https://github.com/cwendt94/espn-api/tree/700e15ddbb91124f031473d2411c8a6285944274), [player.py](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/player.py#L67-L117), [box_score.py](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/box_score.py#L52-L80), [constant.py](https://github.com/cwendt94/espn-api/blob/700e15ddbb91124f031473d2411c8a6285944274/espn_api/football/constant.py#L116-L276), [box_player.py](https://github.com/cwendt94/espn-api/blob/master/espn_api/football/box_player.py), [issue #61](https://github.com/cwendt94/espn-api/issues/61), [issue #571](https://github.com/cwendt94/espn-api/issues/571), [Football Intro wiki](https://github.com/cwendt94/espn-api/wiki/Football-Intro)
- **ESPN live projection write-up:** [mkdocs blog post](https://mkdocs-4a2.pages.dev/blog/2024-10-06/espn-fantasy-football-in-spring-boot-live-scoring-and-projected-ceilings/)
- **fflr:** [live.R](https://github.com/kiernann/fflr/blob/master/R/live.R), [roster.R](https://github.com/kiernann/fflr/blob/master/R/roster.R), [players.R](https://github.com/kiernann/fflr/blob/master/R/players.R)
- **Ottoneu and Underdog:** [Ottoneu forum](https://community.ottoneu.com/t/matchup-projection-updates-as-games-are-played/6141), [Underdog in-game projections](https://help.underdogfantasy.com/en/articles/10361705-in-game-projections), [Underdog projection changes](https://help.underdogfantasy.com/en/articles/11099851-settings-projection-changes)
- **Community:** [r/fantasyfootballcoding live projections API thread](https://axebps-redlib.hf.space/r/fantasyfootballcoding/comments/1elhw48/live_projections_api)
- **Sleeper:** [smallery/sleeper_fantasy_api](https://github.com/smallery/sleeper_fantasy_api), [Sleeper API docs](https://docs.sleeper.com/), [dynastyprocess player ids](https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv), [ProjectionsTool.ts (bulk URL pattern)](https://glama.ai/mcp/servers/@justfeltlikerunning/sleeper-fantasy-mcp/blob/cb3145ccd958c76b221fa443c5dbf2f604e0f8a0/src/tools/ProjectionsTool.ts)
- **ffanalytics:** [repo](https://github.com/FantasyFootballAnalytics/ffanalytics), [scrape_funcs.R](https://github.com/FantasyFootballAnalytics/ffanalytics/blob/master/R/scrape_funcs.R), [calc_projections.R](https://github.com/FantasyFootballAnalytics/ffanalytics/blob/master/R/calc_projections.R), [scoring_rules.R](https://github.com/FantasyFootballAnalytics/ffanalytics/blob/master/R/scoring_rules.R)
- **Projection pages:** [CBS projections](https://www.cbssports.com/fantasy/football/stats/RB/2026/12/projections/ppr/), [NFL.com projections](https://fantasy.nfl.com/research/projections?position=2&statCategory=projectedStats&statSeason=2026&statType=weekProjectedStats&statWeek=12)
- **FantasyPros:** [projections](https://www.fantasypros.com/nfl/projections/rb.php?week=11&scoring=PPR), [API](https://www.fantasypros.com/api-data/), [robots.txt](https://www.fantasypros.com/robots.txt)
- **Commercial live data (not evaluated):** [SportsDataIO fantasy API](https://sportsdata.io/fantasy-sports-api)
