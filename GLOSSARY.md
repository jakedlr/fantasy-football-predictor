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

**Fair Probability**:
A probability implied by a price after the book's vig is removed.
_Avoid_: No-vig probability, true odds, devigged odds

**Snapshot**:
The saved raw prices from one run, kept so the run can be backtested later.
_Avoid_: Cache, log

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

**Score Percentile (Pₖ)**:
The fantasy point total x such that the player has a k% chance of scoring x or fewer. P5 is the floor, P95 the ceiling.
_Avoid_: "5% score", chance of N+ points
