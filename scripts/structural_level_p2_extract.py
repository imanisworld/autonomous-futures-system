#!/usr/bin/env python3
"""P2-X — read-only extractor: replay journals → candidates.jsonl + outcomes.sealed.jsonl.

Structural-level prereg v1.3 / P2 spec §3.2–§3.3. Splits every replay decision row's
``shadow_candidates`` into a candidate file (NO outcome field) and a sealed outcome file
keyed by the resolver's ``candidate_key``; writes ``manifest.json`` with hashes and the
fail-closed integrity report (duplicates, family census vs matrix, asian ORB-fade = 0,
ORB NOT_AVAILABLE days, roll + gap ledgers from the corpus MANIFEST).

``--integrity-only`` runs every check and writes candidates + manifest but never writes
the outcomes file (use for parity/smoke runs that are not the authorized regeneration).
``--determinism-log-dir B`` compares the shadow_candidates bytes of two runs (spec §3.3).

Imports nothing from webhook/, execution/, broker/. Never opens an outcome.

Usage:
    python3 scripts/structural_level_p2_extract.py --log-dir logs/replay_p2/MNQ \
        --corpus-root data/replay_polygon_v2 --out-dir <dir> [--integrity-only]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.structural_level_p2 import (  # noqa: E402
    INSTRUMENTS,
    determinism_check,
    extract,
    parse_dt,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", required=True, help="replay run log dir (journal_*.jsonl)")
    ap.add_argument("--corpus-root", required=True, help="corpus root holding <INST>/ day files + MANIFEST.json")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--instruments", default=",".join(INSTRUMENTS))
    ap.add_argument("--end-ts-exclusive", default=None, help="drop rows with bar_ts at/after this ISO ts")
    ap.add_argument("--integrity-only", action="store_true", help="never write outcomes.sealed.jsonl")
    ap.add_argument("--determinism-log-dir", default=None, help="second run of the same day file(s) to byte-compare")
    args = ap.parse_args(argv)

    insts = [x.strip().upper() for x in args.instruments.split(",") if x.strip()]
    end_ts = parse_dt(args.end_ts_exclusive) if args.end_ts_exclusive else None
    manifest = extract(args.log_dir, args.corpus_root, args.out_dir, instruments=insts,
                       integrity_only=args.integrity_only, end_ts_exclusive=end_ts)
    if args.determinism_log_dir:
        det = determinism_check(args.log_dir, args.determinism_log_dir)
        manifest["integrity"]["determinism"] = det
        if not det["identical"]:
            manifest["integrity"]["problems"].append(
                f"determinism: {det['rows_differ']} rows differ between runs (spec §3.3) — run not accepted")
            manifest["integrity"]["status"] = "BLOCKED"
        with open(Path(args.out_dir) / "manifest.json", "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=1)
            fh.write("\n")
    integ = manifest["integrity"]
    print(f"[p2x] rows={integ['decision_rows']} candidates={integ['candidate_rows_seen']} "
          f"unique_keys={integ['unique_candidate_keys']} status={integ['status']}")
    for fam, c in integ["census_by_family_by_instrument"].items():
        print(f"[p2x]   {fam:40s} {c}")
    for p in integ["problems"]:
        print(f"[p2x] PROBLEM: {p}")
    if manifest.get("outcomes_sealed_file"):
        print(f"[p2x] outcomes SEALED sha256={manifest['outcomes_sealed_file']['sha256']}")
    return 0 if integ["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
