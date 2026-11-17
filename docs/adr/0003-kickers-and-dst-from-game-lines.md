# Kickers come from the Game Lines alone; a D/ST is drawn from Analog Games tilted to its markets

QB/RB/WR/TE Score Distributions are fitted from each player's own Prop Markets. Kickers and D/ST can't be predicted the same way.

**Kickers.** Only DraftKings prices kickers. It posts them late and probably scores "kicking points" without distance bonuses. Meanwhile the Game Lines barely predict a kicker: they explain about 3% of his points (research/kickers-dst.md). So a kicker's Score Distribution comes from his team's Implied Team Total and expected margin alone:
- FG and PAT attempts from fits on those two, joined by a copula;
- each kick's distance from the last two seasons' attempts;
- makes from the league's make curve, with no kicker-specific skill, since skill doesn't persist from year to year.

DraftKings' kicker Ladders are saved in every live Snapshot but not used, until a Backtest on those Snapshots shows they help.

**D/ST.** A D/ST's points add up a dozen correlated parts through non-linear tiers. Its Score Distribution is therefore built by resampling whole past team-games (Analog Games), not by modelling each part:
- each past team-game is weighted by how close its opponent's Implied Team Total was to this one;
- the weights are tilted toward the opponent's team-total Ladder and the D/ST anytime-TD price when a Book prices them;
- every drawn game is scored under the league's own Scoring Rules.

Whole football scores, tier edges, each league's Points Allowed definition, and the correlations between parts all come with the data. That includes the covariance that makes up half of the variance.

## Considered Options

- **Fit kickers to DraftKings' kicker Ladders:** not yet. They exist only on DraftKings, they are posted late, and they are probably scored differently from our leagues. Calibrating the kicker model to Kalshi's kicker lines made it worse.
- **Model each D/ST part and join them with a copula:** rejected for now. The parts are discrete, partly structural (a pick-six is both an INT and a TD), and highly correlated. Drawn independently, the 80% interval covers 72%.
- **Fit a distribution directly to a D/ST fantasy-points line:** no sportsbook offers one, and the tiers differ between leagues.

## Consequences

- Kickers and D/ST get a prediction whenever the Game has a spread and total, even with no prop posted. As a last fallback, nflverse's schedule lines are used and flagged.
- Kickers' ranges differ little from one kicker to the next. That is the honest result, and the report says so in its assumptions.
- The D/ST table (`params/dst_games.json`) and the kicker parameters (`params/kicker.json`) are rebuilt by `scripts/build_params.py`. The kicker model extrapolates a season trend, so it should be rebuilt every season.
- The D/ST tilt targets the opponent's **total** points, because team totals include its defensive and return TDs. The Scoring Rules then subtract whatever the league leaves out of Points Allowed.
