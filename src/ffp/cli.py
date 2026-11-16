import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ffp", description="NFL fantasy Score Distributions from prop prices"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    predict = commands.add_parser("predict", help="Score Percentiles for players in INPUT.md")
    predict.add_argument("input", help="file with one player name per line")
    return parser


def main(argv: list[str] | None = None) -> int:
    build_parser().parse_args(argv)
    return 0
