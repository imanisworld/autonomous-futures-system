#!/usr/bin/env python3
"""Do any committed replay runs actually contain an invalid-at-fill bracket?

Research only. Surveys the replay journals for positions whose recorded fill
price sits outside its own stop/target, by pairing each `OUTCOME` row with the
`TRADE` decision that preceded it in the same journal file. This measures the
blast radius of adding the `ENTRY_BRACKET_INVALID_AT_FILL` guard to
`PaperBroker`'s `ioc_limit` path — i.e. which existing evidence would move.

**Input dependency:** this reads `logs/`, which is gitignored (`.gitignore:10`),
so it does NOT run from a fresh clone. Its output is committed as
`scripts/ioc_limit_guard_replay_log_survey_2026-09-08.json` so the result stays
auditable without the logs. The companion
`scripts/ioc_limit_bracket_guard_readacross.py` has no such dependency.

No strategy, risk, replay, broker, config or deployment change.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
from collections import Counter
from pathlib import Path

TICK = 0.25
DEFAULT_ROOTS = ("logs/replay_622d*", "logs/retest_baseline_off")


def bracket_valid_at_fill(direction: str, fill: float, stop: float, target: float) -> bool:
    if direction == "LONG":
        return stop < fill < target
    return target < fill < stop


def survey(root: str) -> dict:
    """Pair OUTCOME rows with the preceding TRADE setup in the same file."""
    files = sorted(glob.glob(os.path.join(root, "*", "journal_*.jsonl")))
    paired = orphan = invalid = 0
    detachment: list[float] = []
    pnl_all = pnl_invalid = 0.0
    invalid_exits: Counter = Counter()

    for path in files:
        pending: dict | None = None
        with open(path) as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("decision") == "TRADE" and row.get("setup"):
                    pending = row["setup"]
                    continue
                if row.get("type") != "OUTCOME":
                    continue
                outcome = row.get("outcome") or {}
                fill = outcome.get("entry_price")
                if fill is None:
                    continue
                if pending is None:
                    # An outcome we cannot attribute is reported, never assumed benign.
                    orphan += 1
                    continue
                paired += 1
                pnl = float(outcome.get("pnl_dollars") or 0.0)
                pnl_all += pnl
                detachment.append(abs(float(fill) - pending["entry"]) / TICK)
                if not bracket_valid_at_fill(
                    pending["direction"], float(fill), pending["stop"], pending["target"]
                ):
                    invalid += 1
                    pnl_invalid += pnl
                    invalid_exits[outcome.get("exit_reason")] += 1
                pending = None

    return {
        "root": root,
        "journal_files": len(files),
        "paired_trades": paired,
        "unattributable_outcomes": orphan,
        "invalid_at_fill": invalid,
        "pnl_all": round(pnl_all, 2),
        "pnl_invalid_at_fill": round(pnl_invalid, 2),
        "invalid_exit_reasons": dict(invalid_exits),
        # Guards against a vacuous zero: if fills never deviate from the plan
        # level, the invalid count is trivially 0 and says nothing.
        "detachment_ticks": {
            "median": round(statistics.median(detachment), 2) if detachment else None,
            "max": round(max(detachment), 2) if detachment else None,
            "nonzero": sum(1 for value in detachment if value > 0),
            "count": len(detachment),
        },
    }


def build_report(patterns: tuple[str, ...] = DEFAULT_ROOTS) -> dict:
    roots = sorted({path for pattern in patterns for path in glob.glob(pattern)
                    if os.path.isdir(path)})
    surveys = [survey(root) for root in roots]
    return {
        "question": (
            "Would adding ENTRY_BRACKET_INVALID_AT_FILL to PaperBroker's ioc_limit "
            "path change any recorded replay result?"
        ),
        "input_dependency": "logs/ is gitignored; this does not run from a fresh clone",
        "roots_surveyed": len(surveys),
        "total_paired_trades": sum(item["paired_trades"] for item in surveys),
        "total_invalid_at_fill": sum(item["invalid_at_fill"] for item in surveys),
        "total_unattributable_outcomes": sum(item["unattributable_outcomes"] for item in surveys),
        "surveys": surveys,
        "interpretation": [
            "A zero here is only meaningful alongside a nonzero detachment count: "
            "fills must actually deviate from the plan level for the check to bite.",
            "Detachment capping at the configured entry tolerance is the signature of "
            "ENTRY_DETACHED_FROM_PRICE (strategy/signal_engine.py:1517) rejecting "
            "far-detached candidates before the broker ever sees them.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", action="append", dest="roots")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report(tuple(args.roots) if args.roots else DEFAULT_ROOTS)
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end="")
