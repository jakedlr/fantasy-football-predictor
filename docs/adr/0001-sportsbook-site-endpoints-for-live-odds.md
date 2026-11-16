# Live odds come from DraftKings/FanDuel site endpoints; Kalshi is used only for backtesting

The project must cost $0. Paid odds APIs (The Odds API paid tiers, OpticOdds, SportsDataIO) were ruled out, and The Odds API's free tier (500 credits/month) allows only about one weekly snapshot of a single book. So live Ladders and Main Lines are read from the JSON endpoints DraftKings' and FanDuel's own websites use, following the approach already proven in the user's prizepicks-optimizer repo. These endpoints are undocumented. They depend on a public FanDuel app key, DraftKings league and subcategory IDs, Chrome-impersonating requests and a US IP. They can change or break without notice, and calling them is a terms-of-service grey area, so requests are paced and kept few.

Sportsbooks expose no past prices, so Kalshi's public archive of settled NFL prop Ladders (Oct 2025 onward, with hourly bid/ask) is used only to backtest calibration. It is never used to produce a live prediction. Every live run also saves its raw responses to `snapshots/`, which builds our own sportsbook history over time.

## Considered Options
- **The Odds API free tier:** too few credits for full Ladders across both books.
- **Kalshi as the live source:** free and two-sided, but thinner player coverage and liquidity than the sportsbooks.
- **Unofficial PrizePicks/Underdog endpoints:** only a single median line per stat, and behind bot protection.
