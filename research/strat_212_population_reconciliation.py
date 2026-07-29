"""Reconcile the committed MES strat_212 evidence artifact against its journals.

Research-only, read-only. Proves (or disproves) that the frozen canonical
population reproduces from the on-disk canonical replay journals BEFORE any
loss-anatomy analysis is built on top of it.

Join rule mirrors the authoritative one used by
`scripts/strat_212_122_canonical_evidence_report.py`: identity by
`paper_order_id`, no FIFO fallback. Counting is done per trading date so a
discrepancy localizes to specific days rather than a bare total.

See research/STRAT_212_ANATOMY_PRECONDITION_BLOCKER.md for the finding.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from glob import glob
from pathlib import Path


def reconcile(artifact: Path, logs_root: Path, instrument: str, strategy: str) -> dict:
    rows = [json.loads(l) for l in artifact.read_text().splitlines() if l.strip()]
    pop = [r for r in rows if r.get("instrument") == instrument and r.get("strategy") == strategy]
    artifact_by_date = Counter(r["date"] for r in pop)

    journal_by_date: Counter = Counter()
    risk_rejected: dict[str, list[str]] = {}
    order_ids: set[str] = set()
    day_files = sorted(glob(str(logs_root / instrument / "journal_*.jsonl")))

    for f in day_files:
        day = os.path.basename(f).replace("journal_", "").replace(".jsonl", "")
        recs = [json.loads(l) for l in open(f) if l.strip()]
        for r in recs:
            if r.get("decision") == "TRADE" and (r.get("setup") or {}).get("strategy") == strategy:
                journal_by_date[day] += 1
                if r.get("paper_order_id"):
                    order_ids.add(r["paper_order_id"])
        rejects = [r for r in recs if r.get("decision") == "RISK_REJECTED"]
        if rejects:
            risk_rejected[day] = sorted({(r.get("reason") or "")[:200] for r in rejects})

    all_days = set(artifact_by_date) | set(journal_by_date)
    discrepant = {
        d: {
            "artifact": artifact_by_date.get(d, 0),
            "journal": journal_by_date.get(d, 0),
            "risk_rejected_reasons": risk_rejected.get(d, []),
        }
        for d in sorted(all_days)
        if artifact_by_date.get(d, 0) != journal_by_date.get(d, 0)
    }

    artifact_total = sum(artifact_by_date.values())
    journal_total = sum(journal_by_date.values())
    reconciled = sum(min(artifact_by_date.get(d, 0), journal_by_date.get(d, 0)) for d in all_days)

    return {
        "instrument": instrument,
        "strategy": strategy,
        "artifact_total": artifact_total,
        "journal_total": journal_total,
        "reconciled": reconciled,
        "fully_reconciled": artifact_total == journal_total == reconciled,
        "journal_days": len(day_files),
        "trade_rows_with_order_id": len(order_ids),
        "discrepant_dates": discrepant,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact", default="scripts/strat_212_122_canonical_evidence_raw_trades.jsonl")
    ap.add_argument("--logs-root", default="logs/replay_strat212_122_canonical")
    ap.add_argument("--instrument", default="MES")
    ap.add_argument("--strategy", default="strat_212")
    ap.add_argument("--out", default="research/strat_212_population_reconciliation.json")
    args = ap.parse_args()

    result = reconcile(Path(args.artifact), Path(args.logs_root), args.instrument, args.strategy)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0 if result["fully_reconciled"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
