import argparse
from datetime import datetime, timezone
from pathlib import Path

from ffp import predict as pipeline
from ffp.report import write_json, write_markdown
from ffp.scoring import DEFAULT_RULES, load_rules


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
        required=True,
        help="directory of recorded sportsbook responses (live odds arrive in Sprint 2)",
    )
    predict.add_argument(
        "--scoring", default=DEFAULT_RULES, help=f"Scoring Rules preset or file (default {DEFAULT_RULES})"
    )
    predict.add_argument(
        "--output-dir", type=Path, default=Path("."), help="where OUTPUT.md and output.json go"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    rules = load_rules(args.scoring)
    names = pipeline.read_input(args.input)
    markets, games = pipeline.load_fixture_markets(args.fixtures)
    results, skipped = pipeline.predict(names, markets, games, rules)

    generated_at = datetime.now(timezone.utc)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    common = dict(rules_name=rules.name, generated_at=generated_at, assumptions=rules.assumptions)
    write_markdown(results, skipped, path=args.output_dir / "OUTPUT.md", **common)
    write_json(results, skipped, path=args.output_dir / "output.json", **common)
    print(f"Wrote {args.output_dir / 'OUTPUT.md'}: {len(results)} players, {len(skipped)} skipped")
    return 0
