# Fantasy Football Predictor (`ffp`)

`ffp` turns DraftKings and FanDuel player prop prices into a fantasy-point distribution for each player's next NFL game. For every QB, RB, WR and TE you list, it reports Score Percentiles (P5, P25, P50, P75, P95) and the chance of reaching 5, 10, 15 or more points.

How it works: each prop Ladder (for example "70+ receiving yards at −260") is devigged into Fair Probabilities. A Stat Distribution is fitted to those for every stat that scores points. The stats are then sampled together through a Gaussian copula and scored under your league's Scoring Rules, 20,000 Monte Carlo draws per player.

Vocabulary (Score Percentile, Ladder, Rung, Main Line, Usage Stat, ...) is defined in [GLOSSARY.md](GLOSSARY.md).

## Status

Sprints 0–4 are done and merged. Every QB, RB, WR and TE gets a full Score Distribution. Kickers and D/ST are not supported yet. Sprint 5 backtested the model on 7,165 settled Kalshi player-games: with every core stat priced, P5–P95 covers 91–92% of actual scores (target 90%) and the model matches Kalshi's own fantasy-points markets; players flagged "incomplete" are under-predicted. See [research/backtest-kalshi.md](research/backtest-kalshi.md); its proposed recalibrations are awaiting approval.

| Position | Fitted from Prop Markets | From nflverse Base Rates |
|---|---|---|
| QB | passing yards, pass TDs, INTs, rushing yards, rush+rec TDs | fumbles lost (flat per game), 2-pt (passing and rush/rec) |
| RB | rushing yards, receptions, receiving yards, rush+rec TDs; carries (Usage Stat) | fumbles lost (per touch), 2-pt |
| WR / TE | receptions, receiving yards, rush+rec TDs; rushing yards when a book prices them; carries (Usage Stat) | fumbles lost (per touch), 2-pt |

Model choices:

- **Receiving and rushing yards:** point mass at 0 plus a discretised, shifted gamma. Receiving yards take their P(0) from P(0 receptions).
- **Passing yards:** point mass at 0 plus a discretised normal.
- **Receptions and carries:** negative binomial.
- **Pass TDs:** Binomial(11, μ/11).
- **INTs:** Poisson.
- **Rush+rec TDs:** Poisson from the anytime/2+/3+ prices. They are split into rushing and receiving TDs by the player's own rushing share: his last 2 seasons plus this season, shrunk toward his volume and position (`ffp.td_share`).
- **Carries** are only used to set expected touches for fumbles lost. With no carries market (FanDuel has none), expected carries are the rushing-yards mean divided by the position's yards per carry.
- **Correlation:** Gaussian copula with within-player-season Spearman matrices per position (`--correlation pooled` switches matrices). Receiving yards and TDs are 0 whenever receptions are 0.

A stat the position needs but that has no market or can't be fitted is flagged `incomplete: missing X`. The model's other omissions are listed under "Assumptions" in every `OUTPUT.md`.

## Install

You need Python 3.11 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Usage

List players one per line in a Markdown or plain-text file. List markers, checkboxes, headings and front matter are ignored. If a name is ambiguous, add the team, as in `Name (TEAM)`.

```markdown
# Week 11
- Justin Herbert
- Derrick Henry
- Zay Flowers
- Mark Andrews
```

Run it:

```bash
ffp predict INPUT.md                        # live odds, default league rules
ffp predict INPUT.md --scoring half-ppr     # a preset: standard, half-ppr, ppr, sleeper
ffp predict INPUT.md --scoring my-league    # scoring/my-league.json (see scoring/README.md)
ffp predict INPUT.md --correlation pooled   # pooled instead of within-player correlations
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
| `--output-dir DIR` | Where `OUTPUT.md` and `output.json` are written. Default is `.`. |
| `--snapshots DIR` / `--no-snapshot` | Where a live run saves its raw responses and Fair Probabilities (default `snapshots/`, gitignored), or skip saving them. |

Output:

- **`OUTPUT.md`:** a percentile table, chance-of-N+ points, flags and assumptions.
- **`output.json`:** the same, plus per-stat detail (mean, median, books used, Rung count).

Players are skipped, with a reason, when:

- the name can't be resolved;
- the position isn't supported;
- the game has already kicked off;
- props aren't posted yet (rerun closer to kickoff);
- no fit succeeds.

A live run makes a few network calls:

- DraftKings and FanDuel site endpoints ([ADR 0001](docs/adr/0001-sportsbook-site-endpoints-for-live-odds.md)). Passing markets are fetched only when a QB is listed.
- nflverse, through `nflreadpy`, for rosters, the schedule and weekly player stats (TD history).

## Scoring Rules

The bundled default is `src/ffp/rules/espn-league.json`. Presets `standard`, `half-ppr`, `ppr` and `sleeper` are built in (`sleeper` is Sleeper's default full PPR, which Kalshi's fantasy-points markets settle on: `ppr` with −1 per interception instead of −2). Custom leagues go in `scoring/<name>.json` and override a bundled file of the same name ([scoring/README.md](scoring/README.md)).

## Development

```bash
pytest                 # full offline suite (tests never touch the network)
pytest -m live         # live smoke tests against the real sportsbooks
```

Model parameters are committed JSON in `src/ffp/params/`: dispersion relations, correlation matrices and Base Rates. Regenerate them from nflverse (this needs the network and caches to disk):

```bash
python scripts/build_params.py [--out src/ffp/params] [--seasons 2021-2025] \
                               [--dispersion-seasons 2015-2021] [--draws 40000]
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
research/          research notes, recorded fixtures, research scripts
docs/adr/          architecture decisions
```

### Docs

- [PLAN.md](PLAN.md): sprint plan, settled decisions and backlog.
- [RESEARCH.md](RESEARCH.md) and `research/*.md`: background research. `stat-distributions.md` covers the families and fits, `nflverse-data.md` the Base Rates, correlations and TD share, and `sportsbook-markets.md` the book endpoints and markets.
- [GLOSSARY.md](GLOSSARY.md): the project's vocabulary.

Keep this README current. Any change to behaviour, usage, options, supported positions or status updates it in the same commit (see [CLAUDE.md](CLAUDE.md)).

## Data and licences

Player data is from [nflverse](https://github.com/nflverse) (CC-BY 4.0), accessed with `nflreadpy`. Sportsbook prices are fetched for personal use, and raw snapshots are gitignored. Kalshi's historical market data is pulled and cached (in `data/kalshi/`, gitignored) for personal research, which Kalshi's terms allow.
