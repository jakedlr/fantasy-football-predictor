# NFL player prop markets: DraftKings and FanDuel

Probed 2026-11-15 (Sunday evening ET) from this network. Both books answered every request with HTTP 200 JSON. There were no 403/429 responses and no bot challenges. In total I made 20 requests, spaced 2.5 s apart, using Scrapling `Fetcher.get(url, impersonate="chrome", headers={"Accept": "application/json"})`, the same way `~/claude-projects/prizepicks-optimizer/src/ppopt/sources/http.py` does.

The sample game is **LA Chargers @ BAL Ravens (MNF)**, kickoff 2026-11-17T01:15Z (DK) or 01:21Z (FD). DK event id is `34118177` and FD event id is `36275768`. At probe time the SNF game (PIT @ CIN) was live. The following week's games were listed but had **no player props posted** at either book (see Gotchas).

Fixtures are in `research/fixtures/`. DK fixtures are trimmed to the MNF event; the `categories` and `subcategories` lists are kept as-is. FD event-tab fixtures are unmodified, since each is one event.

---

## DraftKings

### Endpoints (state `nj`, NFL league `88808`)

| Purpose | URL |
|---|---|
| League: events + category/subcategory catalogue | `https://sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/88808` |
| One subcategory, every event in the league | `https://sportsbook-nash.draftkings.com/api/sportscontent/dkusnj/v1/leagues/88808/categories/{categoryId}/subcategories/{subcategoryId}` |

One subcategory request returns that market for **all** events that have it, including live games. Filter by `markets[].eventId`.

### Category / subcategory IDs (from the live league response)

Categories: `1000` "Passing Props " (the trailing space is really in the name), `1001` "Rushing Props", `1342` "Receiving Props", `1003` "TD Scorers".

| Stat | Ladder (milestone) subcat | `marketType.name` | O/U subcat | O/U `marketType.name` |
|---|---|---|---|---|
| Receiving yards | 1342 / **16570** "Receiving Yards" | Receiving Yards Milestones | 1342 / **14114** "Rec Yards O/U" | Receiving Yards O/U |
| Receptions | 1342 / **16821** "Receptions" | Receptions Milestones | 1342 / **14115** "Receptions O/U" | |
| Rushing yards | 1001 / **16571** "Rush Yards" | Rushing Yards Milestones | 1001 / **9514** "Rush Yards O/U" | |
| Rush attempts | 1001 / **16820** "Rush Attempts" | Rushing Attempts Milestones | 1001 / **9518** "Rush Attempts O/U" | |
| Passing yards | 1000 / **16569** "Pass Yards" | Passing Yards Milestones | 1000 / **9524** "Pass Yards O/U" | |
| Passing TDs | 1000 / **16568** "Pass TDs" | Passing Touchdowns Milestones | 1000 / **9525** "Pass TDs O/U" | |
| Completions | 1000 / **16889** "Completions" | Passing Completions Milestones | 1000 / **9522** "Completions O/U" | |
| Pass attempts | 1000 / 16888 "Pass Attempts" | (not fetched) | 1000 / 9517 "Pass Attempts O/U" | |
| Interceptions thrown | 1000 / **16819** "Interceptions" | Interceptions Thrown Milestones | 1000 / **15937** and **18958**, both named "Interceptions O/U" | Interceptions Thrown O/U |
| Rush + Rec yards | 1001 / 16572 "Rush + Rec Yards" | (not fetched) | 1001 / 9523 | |
| Anytime TD, 1st TD, 2+ TDs | 1003 / **12438** "TD Scorer" | `Anytime Touchdown Scorer` (mt 11019), `1st Touchdown Scorer` (11018), `Score 2 or More` (11020) | n/a | |
| 3+ TDs | 1003 / **12422** "3+ TDs" | Score 3 or More | n/a | |

Other TD subcats in 1003: 11818 Last TD Scorer, 12424 Anytime TD - 1st Quarter, 12425 First TD Scorer - 2nd Half, 12451 1st Team TD Scorer, 15960 Player to Not Score, 18744 Either Anytime TD, 18745 Combined TDs, 20382 TD Scorer - Halves Grid, 11938 TD Exactas.

**DK has no separate rushing-TD or receiving-TD market.** No such subcategory exists in any category. Its only TD props are scorer props.

**Interceptions O/U uses two subcategory ids.** At probe time 15937 held MNF and 18958 held the live SNF game. To be safe, match subcategories by name, the way the existing repo does. (Its `books.json` "Interceptions O/U" name lookup maps to whichever id comes last.) Ladder and O/U ids match the October fixture in prizepicks-optimizer, except for the extra 18958.

### JSON shape (subcategory response)

Top-level keys: `sports, leagues, events, markets, selections, categories, subcategories, subscriptionPartials`. The response is flat and normalised:

- `events[]`: `id`, `name` ("LA Chargers @ BAL Ravens"), `startEventDate` (ISO, 7-digit fraction, `Z`), `status` (`NOT_STARTED`/`STARTED`), `participants[]` (teams with `venueRole` Home/Away and `metadata.shortName` "BAL").
- `markets[]`: `id`, `eventId`, `name` ("Ja'Marr Chase Receiving Yards"), `subcategoryId`, `marketType.{id,name}`, `tags` (ladders carry `"MilestoneMarket"`), and `statistics.live` on in-play markets.
- `selections[]` (join to a market on `marketId`):
  - `label`: ladder `"70+"`, O/U `"Over"`/`"Under"`, scorer = player name
  - `milestoneValue` (int): **ladders only**. This is the threshold N, meaning "N or more".
  - `points` (float) + `outcomeType` (`Over`/`Under`) + `main: true` + tag `MainPointLine`: **O/U only**
  - `outcomeType` on scorer props: `"Anytime Scorer"`, `"First Scorer"`, `"To Score 2 Or More"`, `"To Score 3 Or More"`
  - `displayOdds.american` is a **string that uses U+2212 "−"** (e.g. `"−120"`, `"+390"`). `trueOdds` is decimal.
  - `participants[0].name` is the player. `participants[0].venueRole` is `HomePlayer`/`AwayPlayer`, and you map it to the event's Home/Away team to get the player's team. **There is no team abbreviation on the player.** `participants[0].metadata.sdid` is a stable player id.

JSON paths:
- Player: `selections[].participants[0].name`
- Threshold: `selections[].milestoneValue` (ladder) or `selections[].points` (O/U)
- Odds: `selections[].displayOdds.american`
- Game: `markets[].eventId` → `events[].id`
- Start time: `events[].startEventDate`
- Team: `selections[].participants[0].venueRole` + `events[].participants[]`

### Ladders

- **One-sided**: every ladder selection has no `outcomeType`, and there are no Unders.
- Thresholds are **integers meaning N+** (`"70+"`, `milestoneValue: 70`), so P(X ≥ N). This equals the "Over N−0.5" of an O/U.
- The ladder **includes the main-line rung** with odds identical to the O/U Over. Example: Derrick Henry rec yds O/U 5.5 has Over −118, and his ladder rung "6+" is also −118. That rung is often a non-round number sitting among the round rungs ("201+", "217+").
- Tag `MostBalancedOdds` marks the rung nearest even money.
- Rung spacing: yards 5/10 (receiving/rushing) and 10 (passing), widening at the top; receptions 1; attempts and completions 2; TDs and INTs 1.

### Sample parsed rows (MNF)

| player | stat | threshold | odds |
|---|---|---|---|
| Matthew Hibner | rec_yds ladder | 6+ / 10+ / 15+ / 20+ / 30+ / 60+ | −117 / +138 / +235 / +361 / +710 / +3400 |
| Tre Harris | receptions ladder | 2+ / 3+ / 4+ / 5+ / 9+ | −830 / −254 / −104 / +207 / +2400 |
| Justin Herbert | rush_yds ladder | 10+ / 22+ / 25+ / 80+ | −409 / −113 / +113 / +3600 |
| Justin Herbert | pass_yds ladder | 150+ / 200+ / 201+ / 250+ / 370+ | −455 / −115 / −113 / +281 / +2900 |
| Jacoby Brissett | pass_tds ladder | 1+ / 2+ / 3+ / 4+ | −419 / +143 / +585 / +2000 |
| Justin Herbert | ints ladder | 1+ / 2+ / 3+ | −165 / +285 / +970 |
| Justin Herbert | ints O/U | 0.5 | Over −165 / Under +129 |
| Zay Flowers | rec_yds O/U | 71.5 | Over −113 / Under −111 |
| Derrick Henry | anytime TD / 1st TD / 2+ / 3+ | – | −150 / +360 / +390 / +1700 |

### Coverage for the MNF game (players with a market)

| Market | Players (home BAL / away LAC) | Rungs per player |
|---|---|---|
| Rec yds ladder | 13 (8 / 5) | 8–19 |
| Rec yds O/U | 13 | 2 |
| Receptions ladder | 13 (8 / 5) | 3–10 |
| Rush yds ladder | 8 (4 / 4) | 7–18 |
| Rush attempts ladder | 4 | 4–8 |
| Pass yds ladder | 2 | 24–25 |
| Pass TDs ladder | 2 | 4 |
| Completions ladder | 2 | 9 |
| INT ladder | 2 | 2–3 |
| INT O/U | 2 | 2 |
| Anytime TD | 26 players + 2 D/ST | 1 |
| 1st TD | 26 + 2 D/ST + "No Touchdown Scorer" | 1 |
| 2+ TDs | 26 + 2 D/ST | 1 |
| 3+ TDs | 17 + D/ST | 1 |

D/ST and "No Touchdown Scorer" selections have **no `participants`**, so skip selections without them. For the live SNF game, the TD scorer subcategory had already been pulled, while the yardage ladders were still listed.

---

## FanDuel

### Endpoints (state `nj`)

| Purpose | URL |
|---|---|
| NFL league page (event list) | `https://sbapi.nj.sportsbook.fanduel.com/api/content-managed-page?page=CUSTOM&customPageId=nfl&_ak=<FD_APP_KEY>&timezone=America/New_York` |
| One tab of one event | `https://sbapi.nj.sportsbook.fanduel.com/api/event-page?_ak=<FD_APP_KEY>&eventId={eventId}&tab={tab-slug}` |

`<FD_APP_KEY>` is the public website app key in `prizepicks-optimizer/src/ppopt/sources/fanduel.py` (`API_KEY`). In the league response, events are at `attachments.events{}`, each with `eventId`, `name` ("Los Angeles Chargers @ Baltimore Ravens", full team names), and `openDate`. Futures and Draft entries have no " @ ".

### Tabs (from the `layout.tabs` and `layout.cards` returned by any event-page call)

The tab slug is the tab title lowercased and hyphenated. Each `layout.cards[id].marketTypes` lists the market types that tab can hold, which makes it a handy discovery tool.

| Tab (slug) | Relevant marketTypes |
|---|---|
| `passing-props` (217) | `PLAYER_X_PASSING_YARDS_HIGH`, `PLAYER_X_PASSING_TOUCHDOWNS_HIGH`, `PLAYER_X_ALT_PASSING_YARDS_HIGH`, `PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH`, `MOST_PASSING_YARDS` |
| `receiving-props` (218) | `PLAYER_X_RECEIVING_YARDS_{LOW,MEDIUM,HIGH}`, `PLAYER_X_RECEPTIONS_{…}`, `PLAYER_X_ALT_RECEIVING_YARDS_{…}`, `PLAYER_X_ALT_RECEPTIONS_{…}`, `PLAYER_X_RUSHING_+_RECEIVING_YARDS`, `PLAYERS_WITH_{10,15,20,30}+_YARDS_RECEPTION` |
| `rushing-props` (219) | `PLAYER_X_RUSHING_YARDS_{…}`, `PLAYER_X_ALT_RUSHING_YARDS_{…}`, `PLAYER_X_RUSHING_+_RECEIVING_YARDS` |
| `td-scorer-props` (220) | `ANY_TIME_TOUCHDOWN_SCORER`, `TO_SCORE_2+_TOUCHDOWNS`, `TO_SCORE_3+_TOUCHDOWNS`, `X_TOUCHDOWN_SCORER_QB` (1st TD), `LAST_TOUCHDOWN_SCORER`, `ANYTIME_{1ST,2ND}_HALF_TD_SCORER`, `X_HOME/AWAY_TOUCHDOWN_SCORER_QB`, `DEFENSIVE_PLAYER_ANY_TIME_TOUCHDOWN_SCORER` |
| `same-game-parlay` (150), `popular` (1) | supersets; SGP also has `PLAYER_X_PASSING_+_RUSHING_YARDS` / `PLAYER_X_ALT_PASSING_+_RUSHING_YARDS` (not fetched) |
| `d/st` (215) | `PLAYER_TO_RECORD_AN_INTERCEPTION` is a **defensive** player INT, not QB INTs thrown |

Key normalisation: `PLAYER_X_ALT_RECEIVING_YARDS_LOW` becomes `PLAYER_ALT_RECEIVING_YARDS` (drop the player letter and the tier suffix). The existing `fanduel.market_key()` already does this. In older data the player letter is `A`/`B` instead of `X` (e.g. `PLAYER_A_INTERCEPTION`).

**Missing on FD for this game:** QB interceptions thrown (no O/U, no ladder), pass attempts, completions, and rush attempts. None of these market types appears in any tab's card config for this event. The October prizepicks-optimizer fixture `fd_event_passing.json` did have `PLAYER_A_INTERCEPTION` / `PLAYER_B_INTERCEPTION` ("Drake Maye - Interception", 2 runners) in the passing tab, so the INT market may get posted closer to kickoff or only for some games. Re-check on game day.

**No separate rushing-TD or receiving-TD markets on FD either.** It only offers scorer props.

### JSON shape (event-page response)

Top level: `layout` (tabs, cards) and `attachments.{eventTypes, events, competitions, markets}`.

- `attachments.events[eventId]`: `name`, `openDate`, `inPlay`.
- `attachments.markets[marketId]`: `marketId` ("734.197193936"), `eventId`, `marketName` ("Derrick Henry - Alt Receiving Yds"), `marketType`, `marketTime`, `marketStatus` (`OPEN`), `inPlay`, `runners[]`.
- `runners[]`: `selectionId`, `runnerName`, `handicap`, `runnerStatus` (`ACTIVE`), `result.type` (`OVER`/`UNDER` on O/U), `winRunnerOdds.americanDisplayOdds.americanOddsInt` (an **int**), `winRunnerOdds.trueOdds.decimalOdds.decimalOdds`, `logo` (team jersey PNG URL), `jersey.jerseyNumber`, `isPlayerSelection`.

How each market kind is expressed:
- **O/U**: 2 runners `"<Player> Over"` / `"<Player> Under"` with `handicap` = line (69.5).
- **Ladder (ALT_*)**: one market per player. Each runner is one rung, named `"<Player> 70+ Yards"`, `"<Player> 4+ Receptions"`, or `"<Player> 2+ Passing Touchdowns"`, with **`handicap: 0`**. The threshold must be parsed from `runnerName` with a regex like `^(.*) (\d+)\+ `.
- **Scorer markets**: one market for the whole game. Runners are named with the bare player name, plus team defenses ("Baltimore Defense", "LA Chargers Defense").
- **Team**: there is no team field. Derive it from the `runners[].logo` URL slug (`…/nfl/baltimore_ravens_jersey.png`), or match players to a roster.

JSON paths:
- Player: `runnerName` (strip " Over"/" Under"/" N+ …")
- Threshold: `handicap` (O/U) or regex on `runnerName` (ladder)
- Odds: `runners[].winRunnerOdds.americanDisplayOdds.americanOddsInt`
- Game: `markets[].eventId`
- Start time: `attachments.events[id].openDate` or `markets[].marketTime`
- Team: logo slug

### Ladders

- **One-sided**: there are no Under runners.
- Thresholds are **integers meaning N+**, on fixed round rungs. They do *not* necessarily include the main-line rung. Zay Flowers' rec yds O/U is 69.5 (Over −113), and his ladder runs 30+, 40+, …, 70+ (−114), 80+, 90+, 100+, 110+, 125+, 150+.
- Rungs: receiving and rushing yards go in steps of 5 at the low end and 10 in the middle, then 125/150. Passing yards go in steps of 25 (125+, 150+, 175+, 200+…). Receptions and TDs go in steps of 1.

### Sample parsed rows (MNF)

| player | stat | threshold | odds |
|---|---|---|---|
| Zay Flowers | rec_yds ladder | 30+ / 70+ / 100+ / 150+ | −1100 / −114 / +260 / +1100 |
| Zay Flowers | rec_yds O/U | 69.5 | −113 / −113 |
| Derrick Henry | rec_yds ladder (LOW tier) | 5+ / 10+ / 15+ / 20+ | −136 / +162 / +280 / +430 |
| Matthew Hibner | receptions ladder | 1+ / 2+ / 3+ | −192 / +235 / +700 |
| Derrick Henry | rush_yds ladder | 40+ / 50+ / 60+ / 70+ | −1100 / −500 / −300 / −188 |
| Derrick Henry | rush_yds O/U | 80.5 | −113 / −113 |
| Justin Herbert | pass_yds ladder | 125+ / 150+ / 175+ / 200+ | −1000 / −440 / −225 / −118 |
| Jacoby Brissett | pass_tds ladder | 1+ / 2+ / 3+ / 4+ | −400 / +142 / +520 / +1500 |
| Justin Herbert | pass_tds O/U | 0.5 | −255 / +188 |
| Derrick Henry | anytime TD / 2+ / 3+ | – | −140 / +440 / +2200 |

### Coverage for the MNF game (players; BAL / LAC from logo)

| Market | Players |
|---|---|
| Rec yds O/U and ladder | 11 (8 / 3) |
| Receptions O/U and ladder | 11 (8 / 3) |
| Rush + rec yds O/U | 6 (3 / 3) |
| Rush yds O/U and ladder | 8 (4 / 4) |
| Pass yds / pass TDs O/U and ladder | 2 (1 / 1) |
| Anytime TD | 29 runners (27 players + 2 defenses) |
| 2+ TDs | 29 |
| 3+ TDs | 14 |
| QB INTs, attempts, completions, rush attempts | 0 (not offered) |

FD's LAC receiving coverage was thin: 3 players against 5 at DK. FD was missing Tre Harris and Quentin Johnston, although both had FD TD-scorer odds.

---

## Existing repo parsers: still working

I ran the unmodified `prizepicks-optimizer` parsers in-process against the new payloads:

- `draftkings.parse_markets` on Rec Yds O/U returned 13 markets (e.g. Zay Flowers 71.5, −113/−111). On Interceptions O/U (15937) it returned 2. On a ladder payload it correctly returns 0, because ladders have no `outcomeType`/`points`.
- `fanduel.parse_event_markets` returned rec_yds 11, receptions 11, rush_yds 8, pass_yds 2, and pass_tds 2. For the same mapping keys, the `ALT_` ladders are not picked up, because `market_key` keeps the `ALT_` prefix.

---

## Gotchas

1. **Posting timing.** About 32 h before MNF, both books had full props for MNF. The Thursday game (IND @ HOU, 4 days out) and all of next Sunday's games were listed in both league feeds, but their props were not posted. DK subcategories covered only the 2 current-week events. FD's Thursday `receiving-props` tab returned only moneyline, spread and total (fixture `fd_nfl_event_receiving_tab_not_posted.json`). Expect to fetch Tuesday–Wednesday at the earliest, and treat an empty result as "not posted", not as an error.
2. **DK subcategory responses include live games.** Filter on `events[].status == "NOT_STARTED"` or on start time.
3. **DK odds strings use U+2212 minus.** FD gives an int.
4. **The DK ladder contains the main-line rung**, which duplicates the O/U Over. The FD ladder uses only round rungs. Dedupe before fitting a distribution.
5. **FD ladders carry the threshold only in `runnerName`.** Their `handicap` is 0.
6. **FD tiers (`_LOW/_MEDIUM/_HIGH`) are a per-player bucket, not alternate lines.** Each player appears in exactly one tier, so strip the suffix. Passing markets only use `_HIGH`.
7. **Neither book gives a team abbreviation on the player.** Use DK `venueRole` (HomePlayer/AwayPlayer) and the FD logo slug.
8. **Name formats differ between books.** Teams: DK "LA Chargers" vs FD "Los Angeles Chargers"; defenses: DK "BAL Ravens D/ST" vs FD "Baltimore Defense". Player names matched exactly for every FD rec-yds player in this sample. DK additionally listed Tre Harris and Quentin Johnston (LAC), which FD lacked.
9. **DK Interceptions O/U appears under two subcategory ids** (15937, 18958). Fetch both, or look them up by name.
10. **FD does not offer QB interceptions thrown, pass attempts, completions or rush attempts for this game.** Re-check closer to kickoff.
11. **Scrapling logs the full request URL**, FD app key included, at INFO level. Lower the scrapling logger level in any tooling.
12. **DK ladder end rungs are very long odds** (+3400, +4000). This is where the hold is likely heaviest, so be careful weighting tail rungs.

## Fixtures (`research/fixtures/`)

DK (trimmed to MNF event 34118177; `dk_nfl_league.json` keeps the MNF and Thursday events plus the full category/subcategory catalogue):
`dk_nfl_league.json`, `dk_nfl_rec_yds_ladder.json`, `dk_nfl_rec_yds_ou.json`, `dk_nfl_receptions_ladder.json`, `dk_nfl_rush_yds_ladder.json`, `dk_nfl_rush_att_ladder.json`, `dk_nfl_pass_yds_ladder.json`, `dk_nfl_pass_tds_ladder.json`, `dk_nfl_completions_ladder.json`, `dk_nfl_ints_ladder.json`, `dk_nfl_ints_ou.json` (subcat 15937), `dk_nfl_td_scorer.json` (anytime/1st/2+), `dk_nfl_3plus_tds.json`.

FD (event 36275768, full responses): `fd_nfl_event_passing_tab.json`, `fd_nfl_event_receiving_tab.json`, `fd_nfl_event_rushing_tab.json`, `fd_nfl_event_td_scorer_tab.json`, `fd_nfl_event_receiving_tab_not_posted.json` (Thursday game 36305232). There is also `fd_nfl_league.json`, trimmed to 3 events, with `layout` dropped.

None of the fixtures contains the FD app key. Probe scripts are in `/tmp/claude-1000/-home-jakedlr-claude-projects-fantasy-football-predictor/43976835-63d8-4048-9cd8-7baef42b37d8/scratchpad/probe/`.
