# Leaderboard data sources: player list and live/final per-player stats

Researched 2026-11-18 (2026 season, week 11). The question: where can a scheduled job, running every 5 minutes on GitHub Actions or a Cloudflare Worker, get the player list and live or final per-player stats for free, with no API key? The stats have to cover full PPR plus our custom K (FG distance) and D/ST rules.

Method:
- A quick web search found the relevant repositories. One subagent each then examined pseudo-r/Public-ESPN-API, cwendt94/espn-api, zuplo/espn-openapi (with the nntrn and akeaswaran gists), the Sleeper API wrappers and nflverse.
- Endpoints were also called directly with curl. Samples are in the session scratchpad and were not committed.
- **No game was in progress during the research** (week 11 opens Thu 2026-11-19 20:15 ET). So no source's live-update behaviour was seen first-hand. See [Open risks](#open-risks).

## Comparison

| Source | Key / auth | Live in-game player stats | Per-kick FG distance | D/ST stats | Game status | Week + date span | Player IDs | Stability / ToS / blocking |
|---|---|---|---|---|---|---|---|---|
| **ESPN site API** (`site.api.espn.com` scoreboard + `summary`) | none | Yes (box score in `summary`, `cache-control: max-age=3–10`) | **Yes**: `drives.previous[].plays[].statYardage` on "Field Goal Good/Missed" plays, made and missed | Build it yourself: score, `totalYards`, sacks, INT, fumbles recovered, return TDs from box score. Safeties, blocks and 2-pt only from plays | Yes: `status.type.state` = `pre`/`in`/`post` | Yes: `week.number`, `leagues[0].calendar[].entries[]` start/end | ESPN athlete ID (numeric string) | Unofficial and undocumented. No published limit. Akamai 403s reported Aug 2026 from shared/cloud IPs. Date-range params blocked Sep 2026. ESPN ToS forbids automated use. pseudo-r docs repo was taken down |
| **ESPN core API** (`sports.core.api.espn.com/v2/...`) | none | Yes in principle, but `max-age=900` (up to 15 min stale) | Yes, plus kicker athlete ID in `participants[]` | Yes, ready-made: `pointsAllowed`, `yardsAllowed`, `sacks`, `safeties`, `kicksBlocked`, return TDs | Yes | Yes: `/seasons/{y}/types/2/weeks/{n}` | ESPN athlete ID | Same as site API. Per-player stats need hundreds of calls per poll, which isn't feasible |
| **ESPN Fantasy API** (`lm-api-reads.fantasy.espn.com`, as used by cwendt94/espn-api) | none for `leaguedefaults` / players / schedule | Very likely (ESPN's own matchup pages use it). Keyed per game by `externalId`, `max-age=5`. Not observed live | Buckets only (<40, 40–49, 50+, 60+) plus total yards | Yes, ready-made stat IDs (120 PA, 127 YA, 99 sacks, 95 INT, 96 FR, 98 safety, 97 blocks, 101–105 TDs) | **No** (only `statsOfficial`) | `currentScoringPeriod.id`. Schedule via `proTeamSchedules_wl` | ESPN ID. D/ST = `-16000 - proTeamId` | Domain moved in Apr 2024 (old host gives 403). No reports of datacenter blocking. Same ESPN ToS |
| **Sleeper** (`api.sleeper.com/stats`, `/projections`, `/schedule`; `api.sleeper.app/v1/state`) | none | Sleeper says stats update during live games. CDN cache 300 s (stats) to 600 s (schedule), so expect 5–15 min lag. Not observed live | Buckets: `fgm_20_29` … `fgm_60p`, `fgmiss_*`, `fgm_yds`, `fgm_lng` | Yes, ready-made: `pts_allow`, `yds_allow`, `sack`, `int`, `fum_rec`, `safe`, `blk_kick`, `def_td`, `st_td`, `def_kr_td`, `def_pr_td`, `def_2pt` | `/schedule` gives `pre_game`/`complete` (`in_game` not seen). Stats rows have `status: null` | `/v1/state/nfl` → `week`, `season_type`. Schedule has dates but not kickoff times | Sleeper ID. DEF = team abbr. Has `sportradar_id` (nearly complete). `gsis_id`/`espn_id` are sparse | Documented API is free for non-commercial use, limit under 1000 calls/min. Stats/projections endpoints are undocumented: their stats provider asked Sleeper to remove them from the docs in 2021. Cloudflare-fronted |
| **nflverse** (nflverse-data releases, nfldata `games.csv`) | none | **No live data.** pbp/stats are built 1–6 h after game windows | Yes, post-game: `fg_made_list` / `fg_missed_list` ("24;43") | Post-game: `stats_team_week` (no PA/YA; derive from scores/pbp) | No status column; scores fill in about 5–20 min after the final whistle | `games.csv` `week`, `gameday`, `gametime`; `get_current_week()` | **gsis_id**; crosswalks to ESPN/Sleeper | Open data, GitHub-hosted, very stable. Release downloads don't count toward the API rate limit |
| NFL.com / GameCenter feeds | — | Dead: `api.nfl.com` gives 401 without a token, `feeds.nfl.com` gives 403, the legacy `static.nfl.com` feed doesn't respond | — | — | — | — | gsis | nflgame-era feeds were shut off years ago |
| Yahoo Fantasy API | OAuth app required | — | — | — | — | — | — | Not keyless, so out of scope |
| Paid/free tiers (SportsDataIO, balldontlie, MySportsFeeds, API-Ninjas) | key required | Free tiers are scrambled data (SportsDataIO), 5 req/min without stats (balldontlie), or final scores only (API-Ninjas). MySportsFeeds' free terms were not confirmed | — | — | — | — | — | Fails the "no key" requirement |

## Recommendation

**Primary: the ESPN site API, polling one scoreboard call plus one `summary` per live game.**

- It is the only keyless source that has all of these in one place:
  - a real pre/in/post status for each game;
  - low-latency live box scores;
  - per-kick FG distances, both made and missed.
- Expected volume is about 900 calls a week:
  - 5-minute polls during game windows, with only in-progress and just-finished games refetched;
  - the final `summary` of each game cached once;
  - the scoreboard alone every 30 min outside game windows.
- Our engine needs these inputs. ESPN gives them as follows:
  - **Offense:** `boxscore.players[].statistics[]` passing / rushing / receiving / fumbles. 2-pt conversions are not in the box score, so parse them from plays (`pointAfterAttempt` / play text).
  - **K:** distances from FG plays in `drives.previous[]`. Assign each kick to the kicking team's kicker. ESPN's site `summary` plays carry no athlete IDs. Match on team plus the box score `kicking` row, or fetch the core play's `participants[]` for the rare game with two kickers. XP from `extraPointsMade/extraPointAttempts`.
  - **D/ST:**
    - Points allowed is the opponent's score. Adjust it to whatever our rules exclude, such as return TDs scored against the offense.
    - Yards allowed is the opponent's `totalYards`.
    - Sacks come from the opponent's `sacksYardsLost`.
    - INTs come from team interceptions.
    - Fumble recoveries come from `fumbles.fumblesRecovered`.
    - Defensive TDs, plus kick/punt return TDs, come from the return groups.
    - Safeties and blocked kicks have to be parsed from plays.

**Fallback: Sleeper `api.sleeper.com/stats/nfl/{season}/{week}` (+ `/schedule`), with the ESPN Fantasy `kona_player_info` view as a second fallback.**

- Both deliver pre-aggregated per-player stats, including ready-made D/ST stats and FG distance buckets, on different infrastructure from the site API (Sleeper uses Cloudflare + Sportradar).
- If our K rules need exact distances rather than Sleeper's 10-yard buckets, the fallback is only approximate for kickers.
  - Sleeper buckets are 0–19, 20–29, …, 50–59, 60+.
  - ESPN Fantasy buckets are <40, 40–49, 50+, 60+.
- Sleeper's stats also serve as a cross-check on our ESPN-derived numbers.

**Supporting sources** (not live):
- **Player list:** ESPN (32 × `site.api…/teams/{id}/roster`, daily) or Sleeper `/v1/players/nfl` (14.7 MB, at most once a day).
- **ID mapping to gsis:** nflverse `players.csv` (`gsis_id` ↔ `espn_id`, daily, about 99% coverage of 2026 players). Use `db_playerids.csv` for `sleeper_id` (weekly; only 238/334 2026 rookies have one, so fill gaps by name/team/position).
- **Schedule cross-check and ESPN game IDs:** nflverse `games.csv` (`espn` column filled for all 272 games).
- **Post-game reconciliation:** nflverse `stats_player_week` / `stats_team_week` (available 1–6 h after games, with corrections until midweek).
- **Expected points for not-started games:** our own model. Sleeper `/projections` (Rotowire) or ESPN Fantasy `statSourceId: 1` are free comparisons.

## Endpoints and field paths

### ESPN site API (primary)

| Purpose | URL | Fields |
|---|---|---|
| Current week, games, status | `https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard` | `week.number` (11), `season.{year,type}`, `week.teamsOnBye[]`, `events[].id`, `events[].date`, `events[].status.type.{state,name,completed}` (`pre`/`in`/`post`; `STATUS_SCHEDULED`/`STATUS_FINAL`), `events[].competitions[0].competitors[].{team.abbreviation,team.id,score}` |
| A given week | `…/scoreboard?seasontype=2&week=10&dates=2026` | as above. **Don't use `dates=YYYYMMDD-YYYYMMDD` ranges** (blocked since Sep 2026) |
| Week date span | same response: `leagues[0].calendar[]` where `value=="2"` → `entries[]` | `{label:"Week 11", detail:"Nov 18-24", value:"11", startDate:"2026-11-18T08:00Z", endDate:"2026-11-25T07:59Z"}` (Wed 08:00Z to Wed 07:59Z) |
| Week span (alt) | `https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/seasons/2026/types/2/weeks/11` | `number`, `startDate`, `endDate` |
| Per-game everything | `https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary?event={id}` (≈600 KB; `site.web.api.espn.com` host also works) | see below |

`summary` field paths (example event 401873052, WSH @ NYG, week 10):

- Status: `header.competitions[0].status.type.{state,completed,detail}`
- Player stats: `boxscore.players[i].team.abbreviation` and `boxscore.players[i].statistics[]`, each with `{name, keys[], athletes[]{athlete.{id,displayName}, stats[]}}`. `stats[]` values are strings in the same order as `keys[]`.
  - passing keys: `completions/passingAttempts`, `passingYards`, `passingTouchdowns`, `interceptions`, `sacks-sackYardsLost`, …
  - rushing: `rushingAttempts`, `rushingYards`, `rushingTouchdowns`, `longRushing`
  - receiving: `receptions`, `receivingYards`, `receivingTouchdowns`, `receivingTargets`, …
  - fumbles: `fumbles`, `fumblesLost`, `fumblesRecovered`
  - defensive: `totalTackles`, `sacks`, `defensiveTouchdowns`, …; interceptions: `interceptions`, `interceptionYards`, `interceptionTouchdowns`
  - kickReturns / puntReturns: `…ReturnTouchdowns`
  - kicking: `fieldGoalsMade/fieldGoalAttempts`, `fieldGoalPct`, `longFieldGoalMade`, `extraPointsMade/extraPointAttempts`, `totalKickingPoints`. No per-kick distances here.
- Team stats: `boxscore.teams[i].statistics[]{name,displayValue}` includes `totalYards`, `sacksYardsLost`, `interceptions`, `fumblesLost`, `turnovers`, `defensiveTouchdowns`.
- Plays: `drives.previous[].plays[]{type.{id,text}, text, statYardage, scoringPlay, period, clock, wallclock, modified}`. FG: `type.id` 59 "Field Goal Good" / 60 "Field Goal Missed", where `statYardage` is the distance (e.g. 46, 33). Live game: also `drives.current`. `scoringPlays[]` lists made kicks only.

Kicker athlete ID when needed:
- `https://sports.core.api.espn.com/v2/sports/football/leagues/nfl/events/{id}/competitions/{id}/plays/{playId}`, then `participants[]` where `type=="kicker"`, then `athlete.$ref`. The athlete ID is the last path segment.
- Core `$ref`s sometimes point to `sports.core.api.espn.pvt`; swap `.pvt` for `.com`.

Ready-made D/ST totals (core API, but cached for 15 min):
- URL: `…/events/{id}/competitions/{id}/competitors/{teamId}/statistics`
- Fields: `splits.categories[]`
  - defensive: `pointsAllowed`, `yardsAllowed`, `sacks`, `safeties`, `kicksBlocked`, `defensiveTouchdowns`
  - returning: `kickReturnTouchdowns`, `puntReturnTouchdowns`, `oppFumbleRecoveries`
- Use these for post-game checks, not for the live board.

### Sleeper (fallback)

| Purpose | URL | Fields |
|---|---|---|
| Week/season | `https://api.sleeper.app/v1/state/nfl` | `week`, `display_week`, `season`, `season_type`, `season_start_date` |
| Weekly stats (live) | `https://api.sleeper.com/stats/nfl/2026/11?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF` | list of `{player_id, team, opponent, game_id, date, week, last_modified, updated_at, player{position,first_name,last_name,...}, stats{...}}`. Zero stats are omitted |
| Projections | `https://api.sleeper.com/projections/nfl/2026/11?season_type=regular&position[]=…` | same shape, `category:"proj"`, `company:"rotowire"` |
| Game status | `https://api.sleeper.com/schedule/nfl/regular/2026` | `[{game_id, week, date, home, away, status}]`: `pre_game` / `complete` (and probably `in_game`) |
| Player list | `https://api.sleeper.app/v1/players/nfl` | dict by `player_id` with `sportradar_id`, `espn_id`, `gsis_id`, `yahoo_id` (sparse) |
| Older stats shape | `https://api.sleeper.app/v1/stats/nfl/regular/2026/{week}` | flat `{player_id: {stat: value}}`, no game_id |

Stat keys:
- Skill: `pass_yd`, `pass_td`, `pass_int`, `pass_2pt`, `rush_yd`, `rush_td`, `rush_2pt`, `rec`, `rec_yd`, `rec_td`, `rec_2pt`, `fum_lost`
- K: `fgm`, `fga`, `fgm_0_19`…`fgm_50_59`, `fgm_60p`, `fgmiss_20_29`…`fgmiss_60p`, `fgm_yds`, `fgm_lng`, `xpm`, `xpmiss`
- DEF: `pts_allow`, `yds_allow`, `sack`, `int`, `fum_rec`, `ff`, `safe`, `blk_kick`, `def_td`, `fum_rec_td`, `st_td`, `def_kr_td`, `def_pr_td`, `def_2pt`

### ESPN Fantasy API (second fallback / projections)

- Current week: `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026` → `currentScoringPeriod.id`.
- Stats:
  - URL: `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info&scoringPeriodId=11`
  - Send the header `x-fantasy-filter: {"players":{"limit":500,"offset":0,"sortPercOwned":{"sortPriority":1,"sortAsc":false},"filterStatsForTopScoringPeriodIds":{"value":1,"additionalValue":["0120261"]}}}`. It must include a sort or you get 400 `FILTER_LIMIT_MISSING_SORT`. Page with `offset`; the total is in the `x-fantasy-filter-player-count` header.
  - Response: `players[].player.{id, fullName, defaultPositionId (1 QB,2 RB,3 WR,4 TE,5 K,16 D/ST), proTeamId, stats[]{scoringPeriodId, statSourceId (0 actual/1 proj), statSplitTypeId, externalId (ESPN game id), stats{statId:value}}}`.
- Schedule: `…/seasons/2026?view=proTeamSchedules_wl` → `settings.proTeams[].proGamesByScoringPeriod["11"][0].{id,date,homeProTeamId,awayProTeamId}`.
- Stat IDs are in cwendt94 `espn_api/football/constant.py`:
  - K: 74–82 and 201–203 for distance buckets, 86–88 for PATs.
  - D/ST: 120 PA, 127 YA, 99 sacks, 95 INT, 96 FR, 97 blocks, 98 safeties, 93/94/101–105 TDs.

### nflverse (IDs, schedule, reconciliation)

- Schedule: `https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv`. Check `…/schedules/timestamp.json` first (43 B). Columns: `game_id, season, week, gameday, gametime, away_team, home_team, away_score, home_score, result, espn, gsis, …`.
- `https://github.com/nflverse/nflverse-data/releases/download/players/players.csv` has `gsis_id`, `espn_id`, `latest_team`, `status`.
- `https://github.com/dynastyprocess/data/raw/master/files/db_playerids.csv` has `gsis_id`, `espn_id`, `sleeper_id`, `sportradar_id`, `yahoo_id`, …
- Release `stats_player`, asset `stats_player_week_2026.csv`, has `fg_made_list`, `fg_missed_list` and the `fg_made_*`/`fg_missed_*` buckets. Release `stats_team`, asset `stats_team_week_2026.csv`, has the team D/ST counts.

## Open risks

1. **ESPN blocking from cloud IPs.**
   - In Aug 2026, gist commenters reported Akamai "You don't have permission to access" 403s on `site.api.espn.com` from Google Sheets (shared Google IPs). Their workarounds were the `site.web.api.espn.com` host or a non-browser User-Agent.
   - GitHub Actions runners and Cloudflare Workers egress IPs are the same kind of shared datacenter IP. **Test from both before committing to an architecture.**
   - Keep the Sleeper fallback wired up and switchable automatically.
2. **Unofficial rate limits.** Anecdotes range from "a couple hundred/day" to "about 1,500–2,500/day" before throttling, plus temporary IP bans after runaway loops. Our plan is about 900 calls/week, with a peak Sunday of about 700. That is within the anecdotal range but not guaranteed. Mitigations:
   - fetch only in-progress games;
   - cache finals;
   - back off on 403/429.
3. **Terms of use and takedowns.**
   - ESPN's terms prohibit automated access and redistribution.
   - The main community docs repo, pseudo-r/Public-ESPN-API, went 404 between Oct 28 and Nov 18, 2026. Just before that it added a scope notice referring to an "outstanding objection or takedown request".
   - Sleeper allows non-commercial use of its documented API. The stats endpoints we'd use are undocumented, and their data provider asked for them to be hidden.
   - A public site that republishes raw stats is more exposed than personal use. Publishing only our derived fantasy points, attributing the data, and staying non-commercial reduces but does not remove the risk.
4. **Live behaviour not observed.** No game was live during the research. Before launch, poll one Thursday game and check:
   - ESPN `summary` refresh cadence;
   - whether `drives.current` plays carry `statYardage` live;
   - Sleeper's in-game lag and whether its schedule shows `in_game`;
   - ESPN Fantasy live updates.
5. **D/ST definitions.** "Points allowed" differs by source. Opponent score includes defensive/return TDs scored against the offense, while ESPN's `pointsAllowed` and Sleeper's `pts_allow` may exclude some. Our custom rules need an explicit definition, tested against all three.
6. **Two kickers, 2-pt conversions, safeties and blocked kicks** in ESPN `summary` are only recoverable from play text and types, and the exact `type.text` strings for safeties and blocks weren't seen in a sample. That parsing is brittle; cross-check against Sleeper and nflverse post-game.
7. **ID mapping gaps.**
   - ESPN → gsis via nflverse `players.csv` is about 99% for 2026 players.
   - Sleeper → gsis is weaker: `db_playerids` is weekly, and only 238 of 334 rookies have a `sleeper_id`.
   - Mid-week signings may be unmapped until the next refresh. Fall back to name + team + position.
8. **Schema drift.** ESPN has already removed or changed endpoints in 2026: site v3 scoreboard 404, site v2 `calendar` 404, date-range params. ESPN Fantasy changed host in 2024. Validate the response shape on every poll and alert on failures.

## Sources consulted

Repositories and gists:
- https://github.com/pseudo-r/Public-ESPN-API (now 404). Archived copy: https://web.archive.org/web/20261028011833/https://github.com/pseudo-r/Public-ESPN-API; fork mirror https://github.com/nikki299/Public-ESPN-API (README, docs/sports/football.md, docs/audit-2026-09-30.md, docs/scoreboard-date-ranges.md, PROJECT_SCOPE.md, CHANGELOG.md); sibling repos https://github.com/pseudo-r/Public-NFL-API, https://github.com/pseudo-r/Public-ESPN-Fantasy-API
- https://github.com/cwendt94/espn-api (`espn_api/requests/constant.py`, `espn_requests.py`, `espn_api/football/constant.py`, `player.py`, `box_player.py`). Issues #44, #148, #539, #547, #586, #629, #634.
- https://github.com/zuplo/espn-openapi
- https://gist.github.com/nntrn/ee26cb2a0716de0947a0a4e9a157bc1c (and comments)
- https://gist.github.com/akeaswaran/b48b02f1c94f873c6655e7129910fc3b (and comments)
- https://github.com/SwapnikKatkoori/sleeper-api-wrapper (issues #18, #23)
- https://github.com/lum8rjack/sleeper-go
- https://github.com/nflverse/nflreadpy, https://github.com/nflverse/nflverse-data, https://github.com/nflverse/nflverse-pbp, https://github.com/nflverse/nfldata, https://github.com/nflverse/nflverse-players, https://github.com/nflverse/nflverse-rosters, https://github.com/nflverse/nflfastR (`R/utils.R`)
- https://github.com/dynastyprocess/data
- https://github.com/derek-adair/nflgame (dead NFL GameCenter feeds)

Docs and articles:
- https://docs.sleeper.com/
- https://support.sleeper.com/en/articles/2441282-stat-corrections
- https://zuplo.com/blog/2024/10/01/espn-hidden-api-guide
- https://zuplo.com/blog/2025/05/12/sleeper-api
- https://sportsapis.dev/espn-api
- https://sportsapis.dev/sleeper-api
- https://rdrr.io/github/nflverse/nflreadr/f/vignettes/articles/nflverse_data_schedule.Rmd
- https://www.martin-riedl.de/2018/10/21/nfl-public-api/
- https://sportsdata.io/free-trial
- https://nfl.balldontlie.io/
- https://api-ninjas.com/api/nfl
- https://highlightly.net/blogs/best-nfl-apis-in-2026
- https://community.cloudflare.com/t/workers-dev-returns-http-403-error-1010-for-github-actions-get-request/966282 (generic Cloudflare 1010 bot blocks on datacenter IPs)

Endpoints called directly (HTTP 200 unless noted):
- ESPN `site.api` scoreboard (current and week 10), `summary?event=401873052`
- ESPN core weeks/events/plays/roster/statistics/status
- ESPN Fantasy `lm-api-reads` season/players/kona/schedule
- Sleeper state/players/stats/projections/schedule
- nflverse `games.csv`, `players.csv`, `db_playerids.csv`
- NFL: `api.nfl.com/v1/games` (401), `feeds.nfl.com/feeds-rs/scores.json` (403)
