# Fantasy Football Predictor (`ffp`)

`ffp` turns DraftKings and FanDuel player prop prices into a fantasy-point distribution for each player's next NFL game. For every QB, RB, WR, TE, kicker and D/ST you list, it reports Score Percentiles (P5, P25, P50, P75, P95). With `--chance N` it also reports the chance of N or more points for numbers you choose.

How it works: each prop Ladder (for example "70+ receiving yards at −260") is devigged into Fair Probabilities. A Stat Distribution is fitted to those for every stat that scores points. The stats are then sampled together through a Gaussian copula and scored under your league's Scoring Rules, 20,000 Monte Carlo draws per player.

Vocabulary (Score Percentile, Ladder, Rung, Main Line, Usage Stat, ...) is defined in [GLOSSARY.md](GLOSSARY.md).

## Status

Sprints 0–6 are done and merged. Every QB, RB, WR, TE, kicker and D/ST gets a full Score Distribution. Sprint 6 added kickers and D/ST, predicted from the Game Lines (spread, total, team totals); see [research/kickers-dst.md](research/kickers-dst.md). Sprint 5 backtested the model on 7,165 settled Kalshi player-games: P5–P95 covers 91.3% of actual scores (target 90%), and the model matches Kalshi's own fantasy-points markets. Sprint 5.6 reworked RB rushing yards (a sum of carries, and better estimates when unpriced). See [research/backtest-kalshi.md](research/backtest-kalshi.md). Sprint 7 fetches the books concurrently: a fresh 9-player lineup now runs in about 16 s, down from about 4 minutes. Next, planned in [PLAN.md](PLAN.md): Sprint 8 parallelises prediction, the nflverse download and the kicker/D/ST backtest.

| Position | Fitted from Prop Markets | From nflverse Base Rates |
|---|---|---|
| QB | passing yards, pass TDs, INTs, rushing yards, rush+rec TDs | fumbles lost (flat per game), 2-pt (passing and rush/rec) |
| RB | rushing yards, receptions, receiving yards, rush+rec TDs; carries (Usage Stat) | fumbles lost (per touch), 2-pt |
| WR / TE | receptions, receiving yards, rush+rec TDs; rushing yards when a book prices them; carries (Usage Stat) | fumbles lost (per touch), 2-pt |
| K | none: the Game Lines (his team's Implied Team Total and expected margin) | FG and PAT attempts, kick distance and make rate |
| D/ST | the opponent's team total and the D/ST anytime-TD price, when a book prices them; the Game Lines | past team-games (2015 on) with a similar opponent Implied Team Total |

Model choices:

- **Receiving yards, and QB rushing yards:** point mass at 0 plus a discretised, shifted gamma. Receiving yards take their P(0) from P(0 receptions).
- **RB, WR and TE rushing yards:** a sum of carries: a negative-binomial number of carries, each with the position's yards per carry from nflverse play-by-play ([ADR 0002](docs/adr/0002-rushing-yards-as-a-sum-of-carries.md)). A low-volume back then has few negative games and still a long right tail.
- **Passing yards:** point mass at 0 plus a discretised normal.
- **Receptions and carries:** negative binomial.
- **Pass TDs:** Binomial(11, μ/11).
- **INTs:** Poisson.
- **Rush+rec TDs:** Poisson from the anytime/2+/3+ prices. They are split into rushing and receiving TDs by the player's own rushing share: his last 2 seasons plus this season, shrunk toward his volume and position (`ffp.td_share`).
- **Carries** are only used to set expected touches for fumbles lost. With no carries market (FanDuel has none), expected carries are the rushing-yards mean divided by the position's yards per carry.
- **Kickers:** FG and PAT attempts from his team's Implied Team Total and expected margin, each FG's distance from the last two seasons, the league's make rate by distance (with a season trend). No kicker-specific skill or weather ([ADR 0003](docs/adr/0003-kickers-and-dst-from-game-lines.md)).
- **D/ST:** whole past team-games whose opponent had a similar Implied Team Total, reweighted to match the opponent's team-total Ladder and the D/ST TD price where a book prices them, each scored under your rules. This keeps Points Allowed tiers, whole football scores and the correlation between sacks, turnovers and points allowed.
- **Correlation:** Gaussian copula with within-player-season Spearman matrices per position (`--correlation pooled` switches matrices). Receiving yards and TDs are 0 whenever receptions are 0.

A stat the position needs but that has no market, or can't be fitted, becomes an **Estimated Stat**, flagged `estimated: X (no market)`. Its mean comes from the player's other priced stats (for example receptions from his receiving yards; rushing yards from his expected rushing TDs, capped for RBs with a high TD price) blended with his last 8 games from nflverse, where a game last season counts half. Where nothing can estimate it, the stat is flagged `incomplete: missing X` and counts as 0, so that player's range is too low. Fixture runs are offline and estimate from the other priced stats only. The model's other omissions are listed under "Assumptions" in every `OUTPUT.md`.

## Install

You need Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Usage

List players one per line in a Markdown or plain-text file. List markers, checkboxes, headings and front matter are ignored. If a name is ambiguous, add the team, as in `Name (TEAM)`. A kicker is listed by name. A D/ST is listed by its team, as in `Ravens D/ST`, `BAL DST`, `Baltimore Defense` or just `Ravens`.

```markdown
# Week 11
- Justin Herbert
- Derrick Henry
- Zay Flowers
- Mark Andrews
- Cameron Dicker
- Ravens D/ST
```

Run it:

```bash
ffp predict INPUT.md                        # live odds, default league rules
ffp predict INPUT.md --scoring half-ppr     # a preset: standard, half-ppr, ppr, sleeper
ffp predict INPUT.md --scoring my-league    # scoring/my-league.json (see scoring/README.md)
ffp predict INPUT.md --correlation pooled   # pooled instead of within-player correlations
ffp predict INPUT.md --chance 14.5 --chance 20   # also: chance of 14.5+ and of 20+ points
ffp predict INPUT.md --output-dir out/ --no-snapshot
```

Run offline against the recorded responses in `research/fixtures` (TD shares fall back to the position share here):

```bash
ffp predict INPUT.md --fixtures research/fixtures --now 2026-11-16T12:00:00Z
```

Options:

| Option | Meaning |
|---|---|
| `--fixtures DIR` | Replay recorded sportsbook responses and nflverse CSVs instead of fetching anything. |
| `--now TIME` | Predict as if it were this time (ISO 8601, UTC unless an offset is given). |
| `--scoring NAME_OR_FILE` | Scoring Rules. Default is `espn-league` (full PPR, 4-pt pass TD, −2 INT, −2 fumble lost). |
| `--correlation within\|pooled` | Copula matrix. Default is `within`. |
| `--chance N` | Also report each player's chance of N or more points. Repeat for several numbers. |
| `--output-dir DIR` | Where `OUTPUT.md` and `output.json` are written. Default is `.`. |
| `--snapshots DIR` / `--no-snapshot` | Where a live run saves its raw responses and Fair Probabilities (default `snapshots/`, gitignored), or skip saving them. |

Output:

- **`OUTPUT.md`:** a percentile table, flags and assumptions, plus a chance table when `--chance` is given.
- **`output.json`:** the same, plus per-stat detail (mean, median, books used, Rung count). For a kicker the detail is FG attempts, FGs made and PATs made; for a D/ST it is Points Allowed, sacks, interceptions and D/ST TDs.

A kicker is always flagged `from game lines`. A D/ST with no team total or TD price is flagged `from game lines (no team total or D/ST TD market)`. When no book lists the Game's spread and total, both use nflverse's schedule lines and say so.

Players are skipped, with a reason, when:

- the name can't be resolved;
- the position isn't supported;
- the game has already kicked off;
- props aren't posted yet (rerun closer to kickoff);
- no fit succeeds;
- for a kicker or D/ST: no spread and total anywhere yet, or Scoring Rules without a `kicker`/`dst` section.

A live run makes a few network calls:

- DraftKings and FanDuel site endpoints ([ADR 0001](docs/adr/0001-sportsbook-site-endpoints-for-live-odds.md)). Passing markets are fetched only when a QB is listed.
  - A D/ST adds DraftKings' team totals and TD scorer, and FanDuel's "popular" and TD scorer tabs for its Game.
  - A kicker adds four DraftKings kicker subcategories, only when the run saves a snapshot (not with `--no-snapshot`). The model doesn't use them yet; they are kept for a later backtest.
- nflverse, through `nflreadpy`, for rosters, the schedule and weekly player stats (TD history and recent games). A run of only kickers and D/STs skips the weekly player stats.

DraftKings and FanDuel are fetched at the same time, each with its own Pacing: at most 3 requests in flight, started at least 0.3 s apart. A fresh 9-player lineup across 7 Games fetches its odds in about 9 s, and the whole run takes about 16 s. Before Sprint 7 it took about 4 minutes.

- **Timing:** the run prints one line, such as `Odds in 8.8 s: DraftKings 22 live requests in 7.4 s, FanDuel 30 in 8.8 s`. A book that was slowed, rate-limited or left out is marked on that line. The snapshot's `manifest.json` keeps the same numbers under `timing`.
- **Pushback:** a book that answers 429 (rate-limited) or 403 (refused) is slowed. It holds all its requests, including those already waiting their turn, and drops to one at a time for the rest of the run. It waits out a 429's `Retry-After` and retries once. If that fails too, the book is left out with one warning, and the other book's odds still count. A 429 asking for more than 10 s fails only that request.
- **Ctrl-C** stops both books at once and exits with status 130, with no traceback.

## Scoring Rules

The bundled default is `src/ffp/rules/espn-league.json`. Presets `standard`, `half-ppr`, `ppr` and `sleeper` are built in:

- `standard`, `half-ppr` and `ppr` use ESPN's default kicker and D/ST scoring.
- `sleeper` is Sleeper's default full PPR, which Kalshi's fantasy-points markets settle on: `ppr` with −1 per interception instead of −2. It also uses Sleeper's kicker and D/ST rules: missed PAT −1, forced fumble +1, and its own Points Allowed tiers with no Yards Allowed tiers.

A rules file's `kicker` section scores FGs by distance tiers, plus PATs and misses. Its `dst` section scores the D/ST counts and the Points Allowed and Yards Allowed tiers. Tiers are whole-number ranges (`max` inclusive) that must follow on with no gap or overlap: list every tier, including those worth 0 points. Only the last tier may leave `max` open (`null`). `points_allowed_excludes` can leave the opponent's defensive TDs and/or return TDs out of Points Allowed. Custom leagues go in `scoring/<name>.json` and override a bundled file of the same name ([scoring/README.md](scoring/README.md)).

## Development

```bash
pytest                 # full offline suite (tests never touch the network)
pytest -m live         # live smoke tests against the real sportsbooks
```

Model parameters are committed JSON in `src/ffp/params/`:

- dispersion relations, per-carry rushing yards, correlation matrices and Base Rates;
- the kicker model (`kicker.json`, from the `--seasons` window, with a season trend: rebuild every season);
- the D/ST Analog Games (`dst_games.json`, one row per team-game of `--dst-seasons`).

Regenerate them from nflverse. This needs the network, downloads play-by-play for the dispersion and D/ST seasons, and caches to disk. `market_calibration.json` is measured on the Kalshi backtest and kept by hand.

The build also reads two hand-kept inputs: the P(0) overrides in `market_calibration.json`, which the rushing spread fits use, and the rushing shifts in the existing `dispersion.json`. It reads them from `--inputs`, which defaults to the committed `src/ffp/params`, so building to another `--out` gives the same results. A `--inputs` folder without `market_calibration.json` is an error.

```bash
python scripts/build_params.py [--out src/ffp/params] [--inputs src/ffp/params] [--seasons 2021-2025] \
                               [--dispersion-seasons 2015-2021] [--dst-seasons 2015-2025] [--draws 40000]
```

### Backtest data (Sprint 5)

The Kalshi backtest reads settled Kalshi NFL player-prop markets and their last pre-kickoff bid/ask from a git-ignored cache. Fill it, or bring it up to date, with:

```bash
python -m ffp.sources.kalshi pull --root data/kalshi --since 2025-10-01   # prints counts and drop reasons
```

The pull needs the network. It is paced to 4 requests/s and can be resumed: a rerun fetches only what is still missing. A full history pull is about 35k requests, roughly 2.5 h. A weekly top-up takes under a hundred requests. `ffp.sources.kalshi.load_rungs(root)` and its siblings read the cache offline.

Run the backtest on that cache:

```bash
python scripts/backtest_kalshi.py --root data/kalshi --jobs 8   # [--out data/backtest] [--seasons 2025 2026]
                                                               # [--samples 4000] [--seed N] [--limit N] [--no-diagnostics]
```

For each player-game with Kalshi Rungs it runs the production fit and simulation (`predict.fit_player` and `score_player`, with Kalshi's bid/ask mids as the Fair Probabilities and the `sleeper` preset). It uses no look-ahead: the position comes from that week's nflverse roster, and the TD share from history before the Game. Each Score Distribution is simulated twice with the same seed, once through the copula and once with independent stats as the baseline. Both are scored against nflverse outcomes. A player with offensive snaps but no stat row is scored as all zeros. Outcomes, rosters and snap counts come from `nflreadpy`, which needs the network once and then reads its cache.

The run writes three files to `--out` (git-ignored):

- `records.jsonl`: one record per player-game. The run resumes from it and recomputes only records whose inputs changed.
- `summary.json`: the pooled metrics.
- `tables.md`: the tables behind [research/backtest-kalshi.md](research/backtest-kalshi.md).

The metrics are PIT, CRPS and P(≥k) reliability per stat against Kalshi's own mids, plus fantasy-point coverage and the model against KXNFLFFPTS and KXNFLFFPTSLADDER. The recalibration diagnostics cover free-fit dispersion, the rushing shift, gamma vs lognormal, PIT dependence against the copula, and the TD mids. The logic is in `ffp.backtest`.

### Kicker and D/ST backtest (Sprint 6)

```bash
python scripts/backtest_k_dst.py   # [--seasons 2016-2025] [--samples 2000] [--out data/backtest-k-dst] [--kalshi data/kalshi] [--no-kalshi]
```

It runs a walk-forward backtest on nflverse: each test season's kicker and D/ST models are fitted on earlier seasons only and scored on that season's team-games from their closing lines. Each model is compared with a baseline that ignores the Game Lines. It then checks Kalshi's 2026 kicker and D/ST fantasy-point lines against the model and the outcomes. The pull maps Kalshi's D/ST targets to `DST-<team>`, and lines with spreads over 10¢ are kept as their own group. The run writes `summary.json` and `tables.md` to `--out`; results are in [research/kickers-dst.md](research/kickers-dst.md).

### Layout

```
src/ffp/
  cli.py             `ffp predict`
  predict.py         INPUT.md → players and Games → Prop Markets → fits → Score Distributions
  players.py         name → nflverse gsis_id, position, team; next Game
  sources/           DraftKings / FanDuel parsers, live fetch, paced HTTP; kalshi.py: backtest markets
  markets.py, odds.py, fair.py   Prop Market types, devig, Fair Probabilities
  distributions.py   Stat Distribution families and their fits
  base_rates.py      fumbles lost and 2-pt from nflverse Base Rates
  td_share.py        per-player rushing TD share
  estimates.py       Estimated Stats: a missing core stat from other stats and recent games
  gamelines.py       Game Lines → Implied Team Totals; a team's markets
  kicker.py, dst.py  kickers from the Game Lines; D/ST from Analog Games tilted to its markets
  copula.py, simulate.py   correlated sampling → Score Distribution
  scoring.py, rules/ Scoring Rules
  report.py          OUTPUT.md + output.json
  snapshots.py       raw responses saved per live run
  outcomes.py        Backtest outcomes: actual stats per player-game from nflverse
  metrics.py         Backtest metrics: PIT, CRPS, reliability/Brier, coverage, pinball, log loss
  backtest.py        the Kalshi Backtest: per player-game records, summaries, report tables
  params/            committed model parameters (scripts/build_params.py)
scripts/build_params.py   regenerates params/ from nflverse
scripts/backtest_kalshi.py   runs the Kalshi Backtest (ffp.backtest)
scripts/backtest_k_dst.py    walk-forward kicker and D/ST Backtest, plus Kalshi's K and D/ST lines
research/          research notes, recorded fixtures, research scripts
docs/adr/          architecture decisions
```

### Docs

- [PLAN.md](PLAN.md): sprint plan, settled decisions and backlog.
- [RESEARCH.md](RESEARCH.md) and `research/*.md`: background research. `stat-distributions.md` covers the families and fits, `nflverse-data.md` the Base Rates, correlations and TD share, `sportsbook-markets.md` the book endpoints and markets, and `kickers-dst.md` the kicker and D/ST research and backtest.
- [GLOSSARY.md](GLOSSARY.md): the project's vocabulary.

Keep this README current. Any change to behaviour, usage, options, supported positions or status updates it in the same commit (see [CLAUDE.md](CLAUDE.md)).

## Data and licences

Player data is from [nflverse](https://github.com/nflverse) (CC-BY 4.0), accessed with `nflreadpy`. Sportsbook prices are fetched for personal use, and raw snapshots are gitignored. Kalshi's historical market data is pulled and cached (in `data/kalshi/`, gitignored) for personal research, which Kalshi's terms allow.
