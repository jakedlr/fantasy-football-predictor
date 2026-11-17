"""Pull every settled market for the NFL player-prop series (historical + live
endpoints) and summarise coverage. Raw pages cached by kalshi_client; a compact
per-series market table is written to data/kalshi/research/markets/<SERIES>.json.

Usage: .venv/bin/python research/scripts/kalshi_coverage.py [SERIES ...]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from kalshi_client import CACHE, STATS, paged  # noqa: E402

SERIES = """KXNFLREC KXNFLRECYDS KXNFLRSHYDS KXNFLRSHATT KXNFLPASSYDS KXNFLPASSTDS
KXNFLPASSINT KXNFLINT KXNFLANYTD KXNFL2TD KXNFLTD KXNFLFFPTS KXNFLFFPTSLADDER
KXNFLLADDERREC KXNFLLADDERRECYDS KXNFLLADDERRSHYDS KXNFLESCALATORREC
KXNFLESCALATORRECYDS KXNFLESCALATORRSHYDS KXNFLRECYDSPRECISE KXNFLRSHYDSPRECISE
KXNFLPASSYDSPRECISE KXNFLRECPROT KXNFLRECYDSPROT KXNFLRSHYDSPROT KXNFLPASSYDSPROT
KXNFLPASSTDSPROT KXNFLPASSINTPROT KXNFLTDPROT KXNFLRRYDS KXNFLPASSATT KXNFLPASSCOMP""".split()

KEEP = ["ticker", "event_ticker", "title", "yes_sub_title", "floor_strike", "cap_strike",
        "strike_type", "custom_strike", "market_type", "open_time", "close_time",
        "occurrence_datetime", "expected_expiration_time", "settlement_ts", "result",
        "settlement_value_dollars", "expiration_value", "volume_fp", "open_interest_fp",
        "last_price_dollars", "status", "rules_primary"]


def pull(series: str) -> list[dict]:
    hist = paged("/historical/markets", "markets", {"series_ticker": series, "limit": 1000})
    live = paged("/markets", "markets", {"series_ticker": series, "status": "settled", "limit": 1000})
    rows = {}
    for src, ms in (("historical", hist), ("live", live)):
        for m in ms:
            r = {k: m.get(k) for k in KEEP}
            r["source"] = src
            rows[m["ticker"]] = r
    out = sorted(rows.values(), key=lambda r: r["ticker"])
    d = CACHE / "markets"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{series}.json").write_text(json.dumps(out))
    return out


if __name__ == "__main__":
    for s in sys.argv[1:] or SERIES:
        ms = pull(s)
        print(s, len(ms), "markets", STATS)
