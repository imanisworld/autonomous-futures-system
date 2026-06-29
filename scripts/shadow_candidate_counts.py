#!/usr/bin/env python3
"""Summarize shadow setup candidates from journal JSONL files."""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journals", nargs="+", help="journal_YYYY-MM-DD.jsonl path(s)")
    args = parser.parse_args()

    counts: collections.Counter[str] = collections.Counter()
    by_instrument: collections.Counter[tuple[str, str]] = collections.Counter()
    by_risk_tier: collections.Counter[tuple[str, str]] = collections.Counter()
    by_size: collections.Counter[tuple[str, str]] = collections.Counter()
    # outcome[strategy] = {result: count}; ticks[strategy] = summed pnl_ticks of filled
    outcome: dict[str, collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    ticks: collections.Counter[str] = collections.Counter()
    rows = 0

    for journal in args.journals:
        path = Path(journal)
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rows += 1
            entry = json.loads(line)
            instrument = str(entry.get("instrument") or "?")
            for candidate in entry.get("shadow_candidates", []):
                strategy = str(candidate.get("strategy") or "?")
                risk_tier = str(candidate.get("risk_tier") or "?")
                size_multiplier = str(candidate.get("size_multiplier") or "?")
                counts[strategy] += 1
                by_instrument[(instrument, strategy)] += 1
                by_risk_tier[(risk_tier, strategy)] += 1
                result = candidate.get("outcome") or {}
                if result:
                    outcome[strategy][str(result.get("result") or "?")] += 1
                    pnl = result.get("pnl_ticks")
                    if pnl is not None:
                        ticks[strategy] += float(pnl)
                by_size[(size_multiplier, strategy)] += 1

    print(f"journal_rows={rows}")
    print("by_strategy")
    for strategy, count in counts.most_common():
        print(f"  {strategy}: {count}")
    print("by_instrument")
    for (instrument, strategy), count in by_instrument.most_common():
        print(f"  {instrument} {strategy}: {count}")
    print("by_risk_tier")
    for (risk_tier, strategy), count in by_risk_tier.most_common():
        print(f"  tier {risk_tier} {strategy}: {count}")
    print("by_size_multiplier")
    for (size_multiplier, strategy), count in by_size.most_common():
        print(f"  {size_multiplier}x {strategy}: {count}")
    print("edge_by_strategy (filled = WIN+LOSS; WR over filled; NO_FILL excluded)")
    header = (
        f"  {'strategy':<32} {'N':>5} {'WIN':>5} {'LOSS':>5} {'OPEN':>5} "
        f"{'NOFILL':>6} {'WR%':>6} {'pnlT':>9}"
    )
    print(header)
    for strategy, _ in counts.most_common():
        o = outcome.get(strategy, collections.Counter())
        wins, losses = o.get("WIN", 0), o.get("LOSS", 0)
        filled = wins + losses
        wr = (wins / filled * 100.0) if filled else 0.0
        print(
            f"  {strategy:<32} {counts[strategy]:>5} {wins:>5} {losses:>5} "
            f"{o.get('OPEN', 0):>5} {o.get('NO_FILL', 0):>6} {wr:>6.1f} "
            f"{ticks.get(strategy, 0.0):>9.1f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
