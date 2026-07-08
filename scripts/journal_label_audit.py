#!/usr/bin/env python3
"""Read-only CLI for journal decision/risk/outcome label consistency."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.journal_label_audit import build_audit, format_report, report_to_json
from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only audit for journal decision/risk/outcome label consistency."
    )
    parser.add_argument("paths", nargs="*", type=Path, help="Specific journal JSONL files to scan.")
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=DEFAULT_JOURNAL_DIR,
        help="Directory containing journal_*.jsonl files when no explicit paths are provided.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = build_audit(paths=args.paths or None, journal_dir=args.journal_dir)
    if args.json:
        print(report_to_json(report))
    else:
        print(format_report(report))
    return 1 if report["summary"]["issues_by_severity"].get("error") else 0


if __name__ == "__main__":
    raise SystemExit(main())
