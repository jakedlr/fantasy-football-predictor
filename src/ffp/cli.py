import argparse
import math
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from ffp import predict as pipeline
from ffp.markets import parse_utc
from ffp.params import CORRELATION_KINDS
from ffp import weekly
from ffp.estimates import load_recent_games
from ffp.players import load_roster, load_schedule
from ffp.td_share import load_td_history
from ffp.report import write_json, write_markdown
from ffp.scoring import DEFAULT_RULES, load_rules

log = logging.getLogger("ffp")


def _points(text: str) -> float:
    """A --chance threshold: a finite, non-negative number of points."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a number of points: {text!r}") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(f"must be a finite number of points, 0 or more: {text!r}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ffp", description="NFL fantasy Score Distributions from prop prices"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    predict = commands.add_parser("predict", help="Score Percentiles for players in INPUT.md")
    predict.add_argument("input", help="file with one player name per line")
    predict.add_argument(
        "--fixtures",
        type=Path,
        help="replay recorded sportsbook responses and nflverse CSVs from this directory "
        "instead of fetching live odds",
    )
    predict.add_argument(
        "--now",
        type=parse_utc,
        help="predict as if it were this time (ISO 8601, UTC unless an offset is given); default: now",
    )
    predict.add_argument(
        "--correlation",
        choices=CORRELATION_KINDS,
        help="latent correlation matrix for the copula: within-player-season (default) or pooled",
    )
    predict.add_argument(
        "--scoring", default=DEFAULT_RULES, help=f"Scoring Rules preset or file (default {DEFAULT_RULES})"
    )
    predict.add_argument(
        "--chance",
        type=_points,
        action="append",
        default=[],
        metavar="N",
        help="also report each player's chance of N or more points; repeat for several numbers",
    )
    predict.add_argument(
        "--output-dir", type=Path, default=Path("."), help="where OUTPUT.md and output.json go"
    )
    predict.add_argument(
        "--snapshots",
        type=Path,
        default=Path("snapshots"),
        help="where live runs save their raw responses and Fair Probabilities (default snapshots/)",
    )
    predict.add_argument("--no-snapshot", action="store_true", help="don't save a snapshot of a live run")
    return parser


BOOK_LABELS = {"draftkings": "DraftKings", "fanduel": "FanDuel"}


def timing_line(books: dict, seconds: float) -> str:
    """The run's odds timing in one line (Http.timing; Sprint 7), e.g. "Odds in 12.3 s: DraftKings 23 live
    requests in 9.8 s, FanDuel 30 in 12.1 s (+2 cached)"."""
    parts = []
    for i, (book, t) in enumerate(books.items()):
        notes = [f"+{t['cached']} cached"] if t["cached"] else []
        notes += ["rate-limited"] if t["rate_limited"] else ["slowed"] if t.get("slowed") else []
        notes += ["left out"] if t["failed"] else []
        what = "live requests " if i == 0 else ""
        parts.append(f"{BOOK_LABELS.get(book, book)} {t['requests']} {what}in {t['seconds']:.1f} s"
                     + (f" ({', '.join(notes)})" if notes else ""))
    return f"Odds in {seconds:.1f} s: " + ", ".join(parts)


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except KeyboardInterrupt:  # Ctrl-C: the Books were stopped where they were (Sprint 7)
        print("cancelled", file=sys.stderr)
        return 130


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
    rules = load_rules(args.scoring)
    names = pipeline.read_input(args.input)
    now = args.now or datetime.now(timezone.utc)
    season = pipeline.nfl_season(now)

    if args.fixtures:
        roster, schedule = pipeline.load_fixture_nflverse(args.fixtures, season)
    else:
        roster, schedule = load_roster(season), load_schedule(season)
    targets, skipped = pipeline.resolve_targets(names, roster, schedule, now)

    snapshot = None
    positions = {t.player.position for t in targets}
    if args.fixtures:
        books = pipeline.load_fixture_books(args.fixtures)
    elif targets:
        from ffp.sources.http import Http

        # Filed under the real fetch time, never --now: snapshots are price history (ADR 0001).
        snapshot = None if args.no_snapshot else _open_snapshot(args.snapshots, datetime.now(timezone.utc))
        http, started = Http(), time.monotonic()
        books = pipeline.load_live_books(
            http, list({t.game.game_id: t.game for t in targets}.values()), now,
            on_response=_response_saver(snapshot),
            stats=pipeline.market_stats_for(positions),
            kickers="K" in positions,
            dst_games=list({t.game.game_id: t.game for t in targets if t.player.position == pipeline.DST}.values()),
            kicker_ladders="K" in positions and snapshot is not None,  # kept for a later Backtest, not used
        )
        seconds = round(time.monotonic() - started, 2)
        if http.timing:
            print(timing_line(http.timing, seconds))
            if snapshot is not None:
                snapshot.save_timing(http.timing, seconds)
        if not books:
            print("error: could not get odds from DraftKings or FanDuel; see the warnings above", file=sys.stderr)
            return 1
    else:
        books = []  # nobody to price: don't touch the sportsbooks
    # Each player's TD history splits his TDs and 2-pt, and his recent games feed his Estimated
    # Stats. Fetched only once odds are in hand (a failed odds fetch costs no nflverse download);
    # fixture runs stay offline (position shares, estimates from the other priced stats only).
    # Kickers and D/ST use neither, so a run of only those skips the download.
    live = bool(books) and not args.fixtures and bool(positions & set(pipeline.PLAYER_POSITIONS))
    td_history, recent_games = {}, {}
    if live:
        stats = weekly.memoized(weekly.nflreadpy_player_stats)  # one download, both loaders
        td_history = load_td_history(season, fetch=stats)
        recent_games = load_recent_games(season, fetch=stats, played=_played(season))

    results, unfitted = pipeline.predict(
        targets, books, rules, on_fair_points=_fair_points_saver(snapshot), correlation=args.correlation,
        td_history=td_history, chance_thresholds=args.chance, recent_games=recent_games,
    )
    order = {name: i for i, name in reversed(list(enumerate(names)))}
    skipped = sorted(skipped + unfitted, key=lambda s: order.get(s.name, len(names)))

    generated_at = datetime.now(timezone.utc)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    assumptions = [*rules.assumptions, *pipeline.MODEL_ASSUMPTIONS]
    common = dict(rules_name=rules.name, generated_at=generated_at, assumptions=assumptions)
    write_markdown(results, skipped, path=args.output_dir / "OUTPUT.md", **common)
    write_json(results, skipped, path=args.output_dir / "output.json", **common)
    where = f"; snapshot in {snapshot.run_dir}" if snapshot else ""
    print(f"Wrote {args.output_dir / 'OUTPUT.md'}: {len(results)} players, {len(skipped)} skipped{where}")
    return 0


def _played(season: int) -> set[tuple[str, str]]:
    """Games played this season and last (snap counts), so recent games count games with no stat row."""
    try:
        return weekly.load_played([season - 1, season])
    except Exception as error:  # noqa: BLE001 - without it, averages count only games with stats
        log.warning("snap counts unavailable (%s); recent games count only games with a stat", error)
        return set()


# A snapshot is history for later backtests: failing to write one is reported, never fatal.


def _open_snapshot(root: Path, now: datetime):
    from ffp.snapshots import Snapshot

    try:
        return Snapshot(now, root=root)
    except OSError as error:
        log.warning("no snapshot for this run: %s", error)
        return None


def _response_saver(snapshot):
    if snapshot is None:
        return None

    def save(book: str, name: str, url: str, payload) -> None:
        try:
            snapshot.save_response(book, name, payload, url=url)
        except OSError as error:
            log.warning("snapshot: could not save %s %s: %s", book, name, error)

    return save


def _fair_points_saver(snapshot):
    if snapshot is None:
        return None

    def save(target: pipeline.Target, stat: str, points) -> None:
        try:
            snapshot.save_fair_points(
                target.player.name,
                stat,
                points,
                game_id=target.game.game_id,
                player_id=target.player.gsis_id,
            )
        except OSError as error:
            log.warning("snapshot: could not save Fair Probabilities for %s: %s", target.name, error)

    return save
