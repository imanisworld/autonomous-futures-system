#!/usr/bin/env python3
"""Run the Asian-session pre-signal precursor audit.

Research only. Reads an explicit WIN/LOSS cohort plus preserved 5m bars and
writes deterministic feature rows, comparison summary, and input manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from research.asian_precursor_audit import (  # noqa: E402
    build_feature_rows,
    load_bars,
    load_candidates,
    summarize_feature_rows,
    write_json,
    write_jsonl,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True, help="Explicit WIN/LOSS cohort JSONL/JSON")
    parser.add_argument("--bars", required=True, help="Preserved 5m JSONL file or directory")
    parser.add_argument("--instrument", required=True, help="Single instrument for this run")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--bar-timestamp-mode",
        choices=("start", "close"),
        default="start",
        help="Historical bar timestamp convention; Polygon replay uses start timestamps",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidates, candidate_source = load_candidates(args.candidates)
    requested_instrument = args.instrument.strip().upper()
    actual_instruments = sorted({row["instrument"] for row in candidates})
    if actual_instruments != [requested_instrument]:
        parser.error(
            f"candidate instrument set {actual_instruments} does not equal --instrument {requested_instrument}"
        )

    bars = load_bars(args.bars, instrument=requested_instrument)
    features = build_feature_rows(
        candidates,
        bars,
        bar_timestamp_mode=args.bar_timestamp_mode,
    )
    summary = summarize_feature_rows(features)

    feature_path = out_dir / "features.jsonl"
    summary_path = out_dir / "summary.json"
    manifest_path = out_dir / "manifest.json"
    write_jsonl(feature_path, features)
    write_json(summary_path, summary)

    first_bar = bars.timestamps[0].isoformat() if bars.timestamps else None
    last_bar = bars.timestamps[-1].isoformat() if bars.timestamps else None
    manifest = {
        "schema": "asian_precursor_audit_manifest_v1",
        "instrument": requested_instrument,
        "bar_timestamp_mode": args.bar_timestamp_mode,
        "candidate_source": candidate_source,
        "bar_sources": bars.source_files,
        "bar_rows": len(bars.rows),
        "first_bar_ts": first_bar,
        "last_bar_ts": last_bar,
        "feature_rows": len(features),
        "feature_sha256": _sha256(feature_path),
        "summary_sha256": _sha256(summary_path),
    }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "instrument": requested_instrument,
                "candidate_rows": len(candidates),
                "bar_rows": len(bars.rows),
                "feature_rows": len(features),
                "groups": len(summary["groups"]),
                "features_sha256": manifest["feature_sha256"],
                "summary_sha256": manifest["summary_sha256"],
                "out_dir": str(out_dir.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
