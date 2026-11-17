#!/usr/bin/env python
"""Backtest the model on Kalshi's Ladders against nflverse outcomes (PLAN S5.2).

    python scripts/backtest_kalshi.py [--root data/kalshi] [--out data/backtest] [--seasons 2025 2026]
                                      [--samples 4000] [--seed N] [--jobs N] [--limit N] [--no-diagnostics]

Reads Kalshi's cache (python -m ffp.sources.kalshi pull) and nflverse weekly stats and rosters
(nflreadpy, cached). Writes, under --out: records.jsonl (one record per player-game; the run
resumes from it), summary.json and tables.md (the tables of research/backtest-kalshi.md).
The logic is in ffp.backtest.
"""

import sys

from ffp.backtest import main

if __name__ == "__main__":
    sys.exit(main())
