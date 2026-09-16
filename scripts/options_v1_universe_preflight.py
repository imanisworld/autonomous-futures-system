"""Read-only preflight for the proposed broader V1 options universe.

This script validates the derived universe only.  It does not change the live
watchlist, scanner configuration, database, alerts, risk state, or services.

    python scripts/options_v1_universe_preflight.py
    python scripts/options_v1_universe_preflight.py --json

Provider/cycle capacity is a separate read-only proof implemented by
``scripts/options_v1_capacity_preflight.py``; this universe check alone is not
activation authority.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.v1_universe import (  # noqa: E402
    DEFAULT_SOURCE,
    EXCLUDED_SYMBOLS,
    SYMBOL_ALIASES,
    load_candidate_universe,
    ticker_list,
)


def build_report() -> dict:
    entries = load_candidate_universe()
    tickers = ticker_list(entries)
    sectors = Counter(row.sector for row in entries)
    return {
        "status": "PASS",
        "source": str(DEFAULT_SOURCE.relative_to(ROOT)),
        "source_policy": "historical research corpus remains unchanged",
        "candidate_count": len(tickers),
        "aliases": dict(SYMBOL_ALIASES),
        "excluded": dict(EXCLUDED_SYMBOLS),
        "sectors": dict(sorted(sectors.items())),
        "tickers": list(tickers),
        "live_watchlist_changed": False,
        "capacity_proof": "separate: scripts/options_v1_capacity_preflight.py",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit the full machine-readable report")
    args = parser.parse_args(argv)
    try:
        report = build_report()
    except Exception as exc:  # fail closed: this is a preflight, not a repair path
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(
            f"PASS: {report['candidate_count']} proposed V1 symbols; "
            f"aliases={report['aliases']} excluded={list(report['excluded'])}; "
            "live watchlist unchanged; capacity proof still separate"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
