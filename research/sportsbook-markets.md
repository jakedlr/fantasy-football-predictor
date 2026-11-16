# NFL player prop markets: DraftKings and FanDuel

Probed 2026-11-15 (Sunday evening ET) from this network. Both books answered every request with HTTP 200 JSON. There were no 403/429 responses and no bot challenges. In total I made 20 requests, spaced 2.5 s apart, using Scrapling `Fetcher.get(url, impersonate="chrome", headers={"Accept": "application/json"})`, the same way `~/claude-projects/prizepicks-optimizer/src/ppopt/sources/http.py` does.

The sample game is **LA Chargers @ BAL Ravens (MNF)**, kickoff 2026-11-17T01:15Z (DK) or 01:21Z (FD). DK event id is `34118177` and FD event id is `36275768`. At probe time the SNF game (PIT @ CIN) was live. The following week's games were listed but had **no player props posted** at either book at that time; Thursday's props were up at both books by the Sprint 2 re-check a few hours later (see Gotchas).

Fixtures are in `research/fixtures/`. DK fixtures are trimmed to the MNF event; the `categories` and `subcategories` lists are kept as-is. FD event-tab fixtures are unmodified, since each is one event.

---

## Re-check 2026-11-15 (Sprint 2)

Re-probed at about 00:00 ET Monday 2026-11-16 (21:00 PT Sunday 2026-11-15), after SNF had ended. I made 10 requests, spaced 3.5 s apart, with Scrapling 0.5 `Fetcher.get(url, impersonate="chrome", headers={"Accept": "application/json"})`, the same call as prizepicks-optimizer's `scrapling_fetch`. No cookies, extra headers or proxy were used. Every request returned HTTP 200 `application/json`, with no 403/429 and no bot challenge. I compared each response with its fixture by full JSON key path, with numeric dict keys collapsed, plus the specific fields our parsers read.

| Endpoint | Status | Notes |
|---|---|---|
| DK league `leagues/88808` | works | Lists 14 events (MNF through next MNF). Categories 1000/1001/1342/1003 have unchanged ids and names (1000 still has the trailing space). Every prop subcategory id in the table below is still present except 18958 (see below). Live-only categories (998, 1054, 1680, 1873) are gone because no game was in play. |
| DK subcat 1342/16570 (rec yds Ladder) | works | Key paths identical to `dk_nfl_rec_yds_ladder.json`. 20 markets over 2 events (MNF and Thursday IND @ HOU). `milestoneValue` is an int, there is no `outcomeType`, market tag `MilestoneMarket` is present, and `displayOdds.american` is a string with U+2212. The main-line rung is still included (Derrick Henry 6+ −118 = O/U Over −118). |
| DK subcat 1342/14114 (rec yds O/U) | works | Key paths identical to `dk_nfl_rec_yds_ou.json`. `points` is a float, `outcomeType` is Over/Under, and every selection has `main: true` with tag `MainPointLine`. |
| DK subcat 1003/12438 (TD scorer) | works | Key paths identical to `dk_nfl_td_scorer.json`. Same 3 market types and `outcomeType` values. 14 of 175 selections (D/ST, "No Touchdown Scorer") still lack `participants`. |
| FD `content-managed-page` (nfl) | works | Events are still at `attachments.events{}` with `eventId`, `name` and `openDate`. MNF is still 36275768 and Thursday is still 36305232. |
| FD event-page `receiving-props` | works | Key paths and market types identical to `fd_nfl_event_receiving_tab.json`. |
| FD event-page `rushing-props` | works | Identical to `fd_nfl_event_rushing_tab.json`. |
| FD event-page `passing-props` | works | Identical to `fd_nfl_event_passing_tab.json`. Still no QB INT, attempts or completions markets. |
| FD event-page `td-scorer-props` | works | Identical to `fd_nfl_event_td_scorer_tab.json`. |
| FD event-page `receiving-props`, Thursday 36305232 | works, **now posted** | Was "not posted" at the first probe. Now it has full player markets, and its key paths are identical to the MNF receiving fixture. |

FD tab ids and titles are unchanged (217 Passing Props, 218 Receiving Props, 219 Rushing Props, 220 TD Scorer Props, 215 D/ST, 150 Same Game Parlay™, 1 Popular), and so are the slugs. The app key, the hosts (`sportsbook-nash.draftkings.com`, `sbapi.nj.sportsbook.fanduel.com`), the DK league id 88808 and the `dkusnj` state segment are also unchanged. No header beyond `Accept: application/json` plus Chrome TLS impersonation was needed.

**Shape changes:** none in any field a parser reads. Every endpoint either has key paths identical to its fixture, or the differences are confined to parts the parsers ignore:
- DK league: game-line markets (Moneyline/Spread/Total) carry a new `markets[].blurbId` (a UUID). League-level `selections[].points` and `trueOdds` are sometimes JSON ints (a spread of 3, say), not floats. Player-prop O/U `points` were all floats, but a parser should still coerce with `float()`.
- FD league: some futures runners (college players in a draft or award market) carry `teamAbbr`. Event-tab player runners do **not** have it, so the logo slug is still the only team signal on FD.

**Facts fixed in place below:**
- DK Interceptions O/U subcategory 18958 is no longer in the catalogue now that SNF is over. It held the in-play game's market, and 15937 is the pre-game id. I updated the ID table, the note under it, and Gotcha 9.
- Posting timing: Thursday's props were posted at both books by about 00:00 ET Monday, roughly 92 h before kickoff. Gotcha 1 had said "expect Tuesday–Wednesday at the earliest", and I corrected it there and in the intro.

**Fixtures:** still representative. No `_2026-11-15` fixtures were added, because nothing a parser reads changed. `fd_nfl_event_receiving_tab_not_posted.json` remains a valid example of the not-posted shape (only moneyline, spread and total), even though that specific game has since posted props.

---

## Re-check 2026-11-16 (Sprint 4: QB and RB markets)

Fetched at about 19:47Z Monday 2026-11-16, about 5.5 h before the same MNF kickoff (LAC @ BAL, DK 34118177, FD 36275768). I made 7 requests through the repo's own `Http` client (Scrapling, 3.5–5 s pacing, cache off): the DK league, the 4 DK Main Line subcategories the Sprint 1 probe had not saved, and the FD `passing-props` and `rushing-props` tabs. Every response was HTTP 200 JSON. The DK names were looked up with `live.dk_subcategory_ids`, and every one resolved to a single id, the same one as in the table below.

**New fixtures** (DK, trimmed to MNF event 34118177 in the same way as the others, keeping `categories`/`subcategories`; the IND @ HOU Thursday markets were dropped):

| Fixture | Subcat | `marketType.name` | Players (Main Line) |
|---|---|---|---|
| `dk_nfl_pass_yds_ou.json` | 1000 / 9524 "Pass Yards O/U" | Passing Yards O/U | Herbert 200.5 (−112/−112), Brissett 212.5 (−113/−111) |
| `dk_nfl_pass_tds_ou.json` | 1000 / 9525 "Pass TDs O/U" | Passing Touchdowns O/U | Herbert 0.5 (−252/+194), Brissett 1.5 (+129/−166) |
| `dk_nfl_rush_yds_ou.json` | 1001 / 9514 "Rush Yards O/U" | Rushing Yards O/U | 8: Henry 81.5, Hampton 34.5, Vidal 21.5, Herbert 20.5, Hill 13.5, Mitchell 11.5, Brissett 7.5, Zay Flowers 2.5 |
| `dk_nfl_rush_att_ou.json` | 1001 / 9518 "Rush Attempts O/U" | Rushing Attempts O/U | 5: Henry 19.5 (−101/−126), Hampton 10.5 (−137/+107), Vidal 6.5, Herbert 5.5 (+121/−154), Justice Hill 3.5 |

DK selections in all four have `points` as a float, `outcomeType` Over/Under, `main: true` and the tag `MainPointLine`, with exactly one pair per player. That is the same shape as `dk_nfl_rec_yds_ou.json`. The market `name` is "<Player> Rushing Attempts O/U", and so on.

With these four, every QB/RB stat has a DK Ladder and a DK Main Line fixture: pass yds, pass TDs, INTs (`dk_nfl_ints_ladder` + `dk_nfl_ints_ou`), rush yds, and rush attempts. Note that the Ladder fixtures were recorded on 2026-11-15 and these Main Lines on 2026-11-16, so a few prices moved in between. For example, Keaton Mitchell's ladder has "11+" at −115 (the 2026-11-15 main-line rung), while the new O/U is 11.5 with Over −117. So the raw P(≥12) slightly exceeds P(≥11) across the two files. Henry's ladder still carries the old main-line rung "81+" (−110) against today's 81.5. Justice Hill has a rush-attempts Main Line but no rush-attempts Ladder. Any test that checks monotonicity across a Ladder file and its Main Line file should allow for this.

**FanDuel on game day (≈5.5 h before kickoff):** both tabs had the same market types as `fd_nfl_event_passing_tab.json` / `fd_nfl_event_rushing_tab.json`, with the same players and the same runner formats. Only prices and some end rungs changed (Hampton gained "10+ Yards", Brissett gained "30+ Yards"). So no FD fixture was re-saved. The card config (`layout.cards[].marketTypes`, the same list in every tab) still has **no QB interceptions, rush attempts / carries, pass attempts or completions** market type. The only INT types are `PLAYER_TO_RECORD_AN_INTERCEPTION` and the newly listed `PLAYER_TO_RECORD_2+_INTERCEPTIONS`, and both are **defensive** player markets. So for this game, FD has no INT market and no carries market even at game day. INTs are DK-only (as PLAN S4.1 anticipates), and carries are DK-only.

### QB/RB market reference

| Stat | DK Ladder (subcat, `marketType.name`) | DK Main Line | FD Main Line type | FD Ladder type | FD tab |
|---|---|---|---|---|---|
| Passing yards | 1000/16569 "Pass Yards", Passing Yards Milestones | 1000/9524 "Pass Yards O/U", Passing Yards O/U | `PLAYER_X_PASSING_YARDS_HIGH` | `PLAYER_X_ALT_PASSING_YARDS_HIGH` | `passing-props` |
| Passing TDs | 1000/16568 "Pass TDs", Passing Touchdowns Milestones | 1000/9525 "Pass TDs O/U", Passing Touchdowns O/U | `PLAYER_X_PASSING_TOUCHDOWNS_HIGH` | `PLAYER_X_ALT_PASSING_TOUCHDOWNS_HIGH` | `passing-props` |
| Interceptions thrown | 1000/16819 "Interceptions", Interceptions Thrown Milestones | 1000/15937 "Interceptions O/U", Interceptions Thrown O/U | none | none | (none) |
| Rushing yards | 1001/16571 "Rush Yards", Rushing Yards Milestones | 1001/9514 "Rush Yards O/U", Rushing Yards O/U | `PLAYER_X_RUSHING_YARDS_{LOW,MEDIUM,HIGH}` | `PLAYER_X_ALT_RUSHING_YARDS_{LOW,MEDIUM,HIGH}` | `rushing-props` |
| Rush attempts (carries, Usage Stat) | 1001/16820 "Rush Attempts", Rushing Attempts Milestones | 1001/9518 "Rush Attempts O/U", Rushing Attempts O/U | none | none | (none) |

Key paths are the same as for receiving (see the DK and FD JSON shape sections). DK uses `selections[].milestoneValue` (Ladder) and `points` + `outcomeType` (Main Line). FD uses `runners[].handicap` + `" Over"`/`" Under"` suffix (Main Line) and the threshold parsed from `runnerName` (Ladder, `handicap: 0`).

**Rung spacing and thresholds** (MNF):
- *Pass yds:* DK uses 10s from 150+ to 370–380+, plus the main-line rung (Herbert "201+", Brissett "217+"), with 24–25 rungs. FD uses 25s from 125+ to 325+/350+, with 9–10 rungs.
- *Pass TDs:* both books use 1+…4+ (FD Herbert stops at 3+).
- *INTs:* DK only, 1+ / 2+ (Brissett) or 1+ / 2+ / 3+ (Herbert). The Main Line is always 0.5, so "1+" duplicates the Over.
- *Rush yds:* DK uses steps of 5 to 30 (10+, 15+, 20+, 25+, 30+), then 10s up to 80–190+, plus the main-line rung (Henry "81+", Hampton "32+", Mitchell "11+"). A low-volume player can start below 10 (Zay Flowers "3+", Brissett "6+"). FD's LOW/MEDIUM tiers use 5s from 5+ to 30+, then 10s. Its HIGH tier (Henry) uses 10s from 40+ to 110+, then 125+/150+/175+.
- *Rush attempts:* DK only, in steps of 2 (4+, 6+, 8+ … ; Henry 12+…28+), plus the main-line rung (Henry "19+", Hampton "11+", Vidal "7+"). The Ladders have 4–10 rungs.

**Sample prices (MNF):**

| Player | Stat | DK | FD |
|---|---|---|---|
| Justin Herbert (LAC QB) | pass yds | O/U 200.5 −112/−112; 150+ −455, 200+ −115, 250+ +281, 300+ +850 | O/U 200.5 −113/−113; 150+ −440, 200+ −118, 250+ +270, 300+ +750 |
| Jacoby Brissett (BAL QB) | pass TDs | O/U 1.5 +129/−166; 1+ −419, 3+ +585 | O/U 1.5 +142/−188; 1+ −400, 3+ +520 |
| Justin Herbert | INTs | O/U 0.5 −165/+129; 2+ +285, 3+ +970 | – |
| Derrick Henry (BAL RB) | rush yds | O/U 81.5 −110/−114; 60+ −287, 100+ +191, 150+ +1120 | O/U 80.5 −113/−113; 60+ −300, 100+ +182, 150+ +850 |
| Derrick Henry | rush att | O/U 19.5 −101/−126; 16+ −323, 22+ +216 | – |
| Omarion Hampton (LAC RB) | rush yds | O/U 34.5 −111/−113; 25+ −214, 50+ +302 | O/U 34.5 −113/−113; 25+ −230, 50+ +235 |
| Omarion Hampton | rush att | O/U 10.5 −137/+107; 8+ −468, 14+ +279 | – |

(Lamar Jackson has no passing or rushing market at either book for this game. Brissett is BAL's QB in both.)

**Parser notes** (tried in a scratch script on the fixtures, without editing `src/`):
- `draftkings.parse_prop_markets(ladder, ou, stat)` handles all five stats unchanged. On these fixtures it gave 2 QBs for pass yds, pass TDs and INTs, 8 players for rush yds and 5 for rush att, each with Main Line, team and deduped Ladder. Only `live.DK_STATS` needs entries, e.g. `DkStat("pass_yds", "Passing Props", "Pass Yards", "Pass Yards O/U")`, `("pass_tds", …, "Pass TDs", "Pass TDs O/U")`, `("ints", …, "Interceptions", "Interceptions O/U")`, `("rush_yds", "Rushing Props", "Rush Yards", "Rush Yards O/U")`, `("rush_att", "Rushing Props", "Rush Attempts", "Rush Attempts O/U")`. Those slugs reproduce the fixture names `dk_nfl_<slug>_{ladder,ou}`. The "Passing Props " trailing space is already handled by `.strip()`.
- `fanduel.parse_prop_markets(tab, stat)` handles pass yds, pass TDs and rush yds with only `MARKET_STEMS` (`PASSING_YARDS`, `PASSING_TOUCHDOWNS`, `RUSHING_YARDS`) and `TABS` (`passing-props`, `rushing-props`) entries. FD ladder runner names are `"<Player> N+ Yards"` (both rushing and passing yards: the name does not say which) and `"<Player> N+ Passing Touchdowns"`. `_RUNG` matches both. Look-alike types normalise to different keys and are not picked up: `PLAYER_X_PASSING_+_RUSHING_YARDS`, `PLAYER_X_RUSHING_+_RECEIVING_YARDS`, and `PLAYER_X_1ST_QUARTER_RUSHING_YARDS`. The tab also holds `MOST_RUSHING_YARDS` / `MOST_PASSING_YARDS`, whose runners are bare player names with `isPlayerSelection` true. Neither `_RUNG` nor `_OVER_UNDER` matches them, so they're ignored.
- FD has **no** INT or carries market, so there is no FD stem to add for `interceptions` or `carries`. These are DK-only, and FD should report them as missing (not an error). Since `fanduel.parse_prop_markets` raises on a stat not in `TABS`, the caller must skip FD for those stats, or `TABS` must allow "no tab".
- Rushing yards can be negative, but no book offers a rung below 3+ or a line below 2.5, so the Ladders say nothing about P(<0).
- The Main Line and its main-line rung: DK INTs O/U 0.5 and pass TDs O/U 0.5 (Herbert) mean the "1+" Rung is the same event and is dropped by `ladder_without_main_line_copy`. That leaves only 2+/3+ Rungs plus the Main Line, which is expected.

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
| Interceptions thrown | 1000 / **16819** "Interceptions" | Interceptions Thrown Milestones | 1000 / **15937** (plus **18958** while SNF was live; gone after it ended), both named "Interceptions O/U" | Interceptions Thrown O/U |
| Rush + Rec yards | 1001 / 16572 "Rush + Rec Yards" | (not fetched) | 1001 / 9523 | |
| Anytime TD, 1st TD, 2+ TDs | 1003 / **12438** "TD Scorer" | `Anytime Touchdown Scorer` (mt 11019), `1st Touchdown Scorer` (11018), `Score 2 or More` (11020) | n/a | |
| 3+ TDs | 1003 / **12422** "3+ TDs" | Score 3 or More | n/a | |

**15960 "Player to Not Score"** (market "Player Not To Score a TD") is the No side of anytime TD, offered for only a few players per game (6 on 2026-11-16). Its Yes/No pairs calibrate the TD vig (`fair.TD_PRIOR_C`); fixture `dk_nfl_player_not_to_score.json`.

Other TD subcats in 1003: 11818 Last TD Scorer, 12424 Anytime TD - 1st Quarter, 12425 First TD Scorer - 2nd Half, 12451 1st Team TD Scorer, 15960 Player to Not Score, 18744 Either Anytime TD, 18745 Combined TDs, 20382 TD Scorer - Halves Grid, 11938 TD Exactas.

**DK has no separate rushing-TD or receiving-TD market.** No such subcategory exists in any category. Its only TD props are scorer props.

**Interceptions O/U used two subcategory ids.** At probe time 15937 held MNF and 18958 held the live SNF game. By the Sprint 2 re-check (SNF over) 18958 had left the catalogue and only 15937 remained, so 18958 looks tied to the in-play game. To be safe, match subcategories by name, the way the existing repo does. (Its `books.json` "Interceptions O/U" name lookup maps to whichever id comes last.) Ladder and O/U ids match the October fixture in prizepicks-optimizer, except for the extra 18958.

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
| Rush yds O/U | 8 | 2 |
| Rush attempts ladder | 4 | 4–10 |
| Rush attempts O/U | 5 (adds Justice Hill) | 2 |
| Pass yds ladder | 2 | 24–25 |
| Pass yds O/U | 2 | 2 |
| Pass TDs ladder | 2 | 4 |
| Pass TDs O/U | 2 | 2 |
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

**Missing on FD for this game:** QB interceptions thrown (no O/U, no ladder), pass attempts, completions, and rush attempts. None of these market types appears in any tab's card config for this event. The October prizepicks-optimizer fixture `fd_event_passing.json` did have `PLAYER_A_INTERCEPTION` / `PLAYER_B_INTERCEPTION` ("Drake Maye - Interception", 2 runners) in the passing tab, so the INT market may get posted closer to kickoff or only for some games. Re-check on game day. **Game-day re-check (2026-11-16, ~5.5 h before kickoff): still none** (see the Sprint 4 re-check above).

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

1. **Posting timing.** About 32 h before MNF, both books had full props for MNF. At the first probe, the Thursday game (IND @ HOU, 4 days out) and all of next Sunday's games were listed in both league feeds, but their props were not posted. DK subcategories covered only the 2 current-week events. FD's Thursday `receiving-props` tab returned only moneyline, spread and total (fixture `fd_nfl_event_receiving_tab_not_posted.json`). By the re-check at about 00:00 ET Monday (~92 h before the Thursday kickoff), **both books had posted Thursday props**. Next Sunday's games were still not in the DK subcategories. So props for the next game appear about Sunday night to Monday; treat an empty result as "not posted", not as an error.
2. **DK subcategory responses include live games.** Filter on `events[].status == "NOT_STARTED"` or on start time.
3. **DK odds strings use U+2212 minus.** FD gives an int.
4. **The DK ladder contains the main-line rung**, which duplicates the O/U Over. The FD ladder uses only round rungs. Dedupe before fitting a distribution.
5. **FD ladders carry the threshold only in `runnerName`.** Their `handicap` is 0.
6. **FD tiers (`_LOW/_MEDIUM/_HIGH`) are a per-player bucket, not alternate lines.** Each player appears in exactly one tier, so strip the suffix. Passing markets only use `_HIGH`.
7. **Neither book gives a team abbreviation on the player.** Use DK `venueRole` (HomePlayer/AwayPlayer) and the FD logo slug.
8. **Name formats differ between books.** Teams: DK "LA Chargers" vs FD "Los Angeles Chargers"; defenses: DK "BAL Ravens D/ST" vs FD "Baltimore Defense". Player names matched exactly for every FD rec-yds player in this sample. DK additionally listed Tre Harris and Quentin Johnston (LAC), which FD lacked.
9. **DK Interceptions O/U can appear under two subcategory ids** (15937, plus 18958 while a game is live). Look them up by name rather than hard-coding ids.
10. **FD does not offer QB interceptions thrown, pass attempts, completions or rush attempts for this game,** and it still didn't ~5.5 h before kickoff (2026-11-16). INTs and carries are DK-only here.
11. **Scrapling logs the full request URL**, FD app key included, at INFO level. Lower the scrapling logger level in any tooling.
12. **DK ladder end rungs are very long odds** (+3400, +4000). This is where the hold is likely heaviest, so be careful weighting tail rungs.

## Fixtures (`research/fixtures/`)

DK (trimmed to MNF event 34118177; `dk_nfl_league.json` keeps the MNF and Thursday events plus the full category/subcategory catalogue):
`dk_nfl_league.json`, `dk_nfl_rec_yds_ladder.json`, `dk_nfl_rec_yds_ou.json`, `dk_nfl_receptions_ladder.json`, `dk_nfl_rush_yds_ladder.json`, `dk_nfl_rush_att_ladder.json`, `dk_nfl_pass_yds_ladder.json`, `dk_nfl_pass_tds_ladder.json`, `dk_nfl_completions_ladder.json`, `dk_nfl_ints_ladder.json`, `dk_nfl_ints_ou.json` (subcat 15937), `dk_nfl_td_scorer.json` (anytime/1st/2+), `dk_nfl_3plus_tds.json`. Added 2026-11-16 (Sprint 4, Main Lines recorded ~5.5 h before kickoff, a day after the Ladders): `dk_nfl_pass_yds_ou.json` (9524), `dk_nfl_pass_tds_ou.json` (9525), `dk_nfl_rush_yds_ou.json` (9514), `dk_nfl_rush_att_ou.json` (9518). Also `dk_nfl_receptions_ou.json` and `dk_nfl_player_not_to_score.json` (see above).

FD (event 36275768, full responses): `fd_nfl_event_passing_tab.json`, `fd_nfl_event_receiving_tab.json`, `fd_nfl_event_rushing_tab.json`, `fd_nfl_event_td_scorer_tab.json`, `fd_nfl_event_receiving_tab_not_posted.json` (Thursday game 36305232). There is also `fd_nfl_league.json`, trimmed to 3 events, with `layout` dropped.

None of the fixtures contains the FD app key. Probe scripts are in `/tmp/claude-1000/-home-jakedlr-claude-projects-fantasy-football-predictor/43976835-63d8-4048-9cd8-7baef42b37d8/scratchpad/probe/`.
