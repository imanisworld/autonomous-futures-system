#!/usr/bin/env python3
"""Options first-sight vs trigger-reclaim entry test (docs/prereg-options-trigger-reclaim-entry-2026-09-23.md).

RESEARCH / PAPER EVIDENCE ONLY. Reads a READ-ONLY copy of the options scanner SQLite
store (take it with SQLite's online backup; never read the live file in place).

  reproduce  CONTROL re-derivation vs every stored resolved OPTIONS_PAPER_V1 ACTIVE row,
             plus contract-symbol and entry-ASK lineage. Must PASS before any scoring.
  counts     (default) blind forward counts: eligible episodes, scorable pairs, days,
             RECLAIM entries / NO_ENTRY, blocked reasons, gate status. Never prints P&L.
  look       the single look. Refuses unless reproduce PASSES on the same snapshot, the
             gate (>=50 pairs, >=20 days, >=30 RECLAIM entries) is met, --confirm-single-look
             is given, and the canonical preregistration look receipt does not already exist.

Usage:
    python3 scripts/options_reclaim_entry.py reproduce --db snapshot.sqlite
    python3 scripts/options_reclaim_entry.py counts --db snapshot.sqlite [--out counts.json]
    python3 scripts/options_reclaim_entry.py look --db snapshot.sqlite --confirm-single-look
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research import options_reclaim_entry as oe  # noqa: E402

CANONICAL_LOOK_PATH = REPO / ".evidence" / f"{oe.PREREG_ID}-single-look.json"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("mode", nargs="?", choices=("reproduce", "counts", "look"), default="counts")
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--confirm-single-look", action="store_true")
    a = p.parse_args(argv)
    now = datetime.now(timezone.utc)
    if a.mode == "look":
        if not a.confirm_single_look:
            print("REFUSED: the look happens once; pass --confirm-single-look", file=sys.stderr)
            return 3
        if a.out is not None and a.out.resolve() != CANONICAL_LOOK_PATH.resolve():
            print(f"REFUSED: look output is fixed at {CANONICAL_LOOK_PATH}", file=sys.stderr)
            return 3
        if CANONICAL_LOOK_PATH.exists():
            print(f"REFUSED: canonical single-look receipt already exists at {CANONICAL_LOOK_PATH}", file=sys.stderr)
            return 3
    conn = oe.connect_readonly(a.db)
    eps = oe.load_episodes(conn)
    source = {"db": str(a.db), "sha256": _sha(a.db),
              "max_scan_timestamp": conn.execute("select max(timestamp) from scans").fetchone()[0]}
    repro = oe.reproduction(eps, oe.contract_symbols_by_row(conn))
    if a.mode == "reproduce":
        out = {"source": source, **repro}
        text = json.dumps(out, indent=2, default=str)
        if a.out:
            a.out.write_text(text + "\n")
        print(json.dumps({"verdict": repro["verdict"], "matched": repro["matched"], "total": repro["total"],
                          "fixture_ids": repro["fixture_ids"]}))
        return 0 if repro["verdict"] == "PASS" else 2
    if repro["verdict"] != "PASS":
        print(f"BLOCKED: reproduction {repro['matched']}/{repro['total']} — exact lineage not proven", file=sys.stderr)
        return 2
    pairs = oe.evaluate(eps)
    if a.mode == "counts":
        rep = oe.counts_report(pairs, as_of=now, source=source)
        rep["reproduction"] = {"verdict": repro["verdict"], "rows": repro["total"]}
        text = json.dumps(rep, indent=2, sort_keys=True, default=str)
        if a.out:
            a.out.write_text(text + "\n")
        print(text)
        return 0
    # Check the frozen gate without exposing economics, then atomically reserve
    # the canonical receipt before computing the one allowed look.
    blind = oe.counts_report(pairs, as_of=now, source=source)
    if blind["status"] != "READY_FOR_SINGLE_LOOK":
        print("REFUSED: scoring gate not reached", file=sys.stderr)
        return 3
    CANONICAL_LOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with CANONICAL_LOOK_PATH.open("x") as fh:
            fh.write(json.dumps({"prereg": oe.PREREG_ID, "status": "LOOK_RESERVED",
                                 "reserved_at": now.isoformat()}) + "\n")
    except FileExistsError:
        print(f"REFUSED: canonical single-look receipt already exists at {CANONICAL_LOOK_PATH}", file=sys.stderr)
        return 3
    try:
        rep = oe.look_report(pairs, as_of=now)
        rep["source"] = source
        CANONICAL_LOOK_PATH.write_text(json.dumps(rep, indent=2, sort_keys=True, default=str) + "\n")
    except Exception:
        # Fail closed: the reservation remains, preventing an unrecorded second look.
        raise
    print(json.dumps({"verdict": rep["verdict"], "criteria": rep["criteria"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
