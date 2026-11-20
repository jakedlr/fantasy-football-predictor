# Fantasy Football Predictor

Turns sportsbook player prop prices into a probability distribution of each NFL player's fantasy points for their next game.

## Scoring

**Scoring Stat**:
A player statistic that changes fantasy points, such as receptions, receiving yards, rushing TDs or interceptions thrown.
_Avoid_: Event, scoring event, category

**Usage Stat**:
A player statistic that scores no fantasy points but shapes the distribution of a Scoring Stat, such as carries.
_Avoid_: Volume stat, usage market

**Scoring Rules**:
The points awarded per unit of each Scoring Stat. A named preset (standard, half-PPR, full PPR) or a Custom Scoring Rules set for a specific league.
_Avoid_: Scoring format, scoring system, settings

**Custom Scoring Rules**:
Scoring Rules defined for one specific league instead of a standard preset.
_Avoid_: League settings

**D/ST**:
A team's defense and special teams, scored as one fantasy player.
_Avoid_: DST, DEF, defense (alone)

**Points Allowed**:
The opponent's points a D/ST is charged with under the Scoring Rules: all of them, or without the touchdowns the opponent's own defense and returners scored, depending on the league.
_Avoid_: PA, opponent score

**Yards Allowed**:
The opponent's net offensive yards (rushing plus passing, sacks subtracted) a D/ST is charged with.
_Avoid_: YA, total yards

**Base Rate**:
The historical per-game frequency of a Scoring Stat that no book offers, such as fumbles lost or 2-point conversions.
_Avoid_: Default rate, prior

**Estimated Stat**:
A Scoring Stat with no Prop Market for this player in this Game, whose Stat Distribution is estimated from his other stats or recent games.
_Avoid_: Fallback stat, imputed stat, filled stat

## Markets

**Game**:
One scheduled NFL game. A sportsbook's "event" means a Game.
_Avoid_: Event, matchup, fixture

**Book**:
A sportsbook or prediction exchange that prices Prop Markets: DraftKings, FanDuel or Kalshi. An exchange's prices carry no vig.
_Avoid_: Sportsbook (when an exchange is meant too), site, operator

**Prop Market**:
A book's offer on one player's Scoring Stat or Usage Stat in one Game.
_Avoid_: Prop, bet, market

**Main Line**:
The two-sided over/under price in a Prop Market.
_Avoid_: O/U, standard line

**Ladder**:
The set of one-sided "X or more" prices in a Prop Market.
_Avoid_: Alt lines, milestones

**Rung**:
One threshold and its price within a Ladder, e.g. "70+ receiving yards at −260".
_Avoid_: Milestone, alt line, step

**Game Line**:
A Book's price on a whole Game or one team in it, not a player: the spread, the total, a team total.
_Avoid_: Game odds, team prop

**Implied Team Total**:
A team's expected points in a Game, from the Game Lines: half the total plus or minus half the spread, or the team-total Main Line.
_Avoid_: ITT (in prose), team projection

**Fair Probability**:
A probability implied by a price after the book's vig is removed.
_Avoid_: No-vig probability, true odds, devigged odds

**Snapshot**:
The saved raw prices from one run, kept so the run can be backtested later.
_Avoid_: Cache, log

**Pacing**:
How fast a run may send requests to one Book: how many at once and how far apart their starts are. Each Book is paced on its own. A Book that pushes back is *slowed*: it falls back to one request at a time for the rest of the run. Pushback by HTTP 429 is a rate limit (*rate-limited*); a 403 is a refusal (*refused*).
_Avoid_: Throttle, rate, politeness

**Backtest**:
Scoring past Score Distributions against what the players actually scored, to check calibration.
_Avoid_: Validation, evaluation

## Distributions

**Stat Distribution**:
The probability distribution of one Scoring Stat for one player in one Game.
_Avoid_: Event distribution, marginal

**Score Distribution**:
The probability distribution of one player's total fantasy points in one Game under given Scoring Rules.
_Avoid_: Odds distribution, point distribution, projection

**Analog Game**:
A past team-game whose Game Lines resemble the one being predicted, drawn whole (every D/ST stat from the same game) to build a D/ST Score Distribution.
_Avoid_: Neighbour, comparable, comp

**Score Percentile (Pₖ)**:
The fantasy point total x such that the player has a k% chance of scoring x or fewer. P5 is the floor, P95 the ceiling.
_Avoid_: "5% score", chance of N+ points

## Leaderboard

**Leaderboard**:
The public ranking of every listed player's Week Points for the current NFL Week, filterable by position.
_Avoid_: Rankings, standings, board

**NFL Week**:
One numbered week of the NFL season, bounded by ESPN's week start and end times (Wednesday to Wednesday), shown as the dates of its first and last Game.
_Avoid_: Slate, round, gameweek

**Game Status**:
Where a player's Game stands as of the last update: Upcoming (not kicked off), Live (in progress) or Final (finished).
_Avoid_: Game state, phase, not started / in progress / completed

**Expected Points**:
The mean of a player's Score Distribution for a Game that has not kicked off, frozen at kickoff.
_Avoid_: Projection, projected points, EV

**Points So Far**:
The fantasy points a player has actually scored in a Live or Final Game under the chosen Scoring Rules.
_Avoid_: Actual points, live points, current score

**Projected Total**:
A Live player's Points So Far plus the share of his Expected Points still to play.
_Avoid_: Live projection, final projection

**Week Points**:
The single number the Leaderboard ranks by: Points So Far when Final, the Projected Total when Live, Expected Points when Upcoming.
_Avoid_: Score, total, fantasy points (alone)
