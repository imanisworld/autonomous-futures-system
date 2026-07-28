#!/usr/bin/env python3
"""Generate the System Status & Evidence Snapshot.

    cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m scripts.system_status_snapshot

Read-only + fail-soft, mirroring scripts/health_digest.py: `build_system_status_snapshot`
(ops.system_status_snapshot) does the composition, this script does I/O -- resolving
paths, optionally reading the changed-files list for the change-scope check, and
writing the result atomically. On any generation exception the previous snapshot file
(if one exists) is left untouched and this exits non-zero; it never writes a partial
or blank snapshot and never touches trading configuration, orders, or positions.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from ops.system_status_snapshot import build_system_status_snapshot, write_snapshot_atomic

DEFAULT_OUTPUT = "logs/system_status_snapshot.json"


def _changed_files_vs(root: Path, base_ref: str) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=5.0,
        )
    except Exception:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--risk-rules", default="risk_rules.yaml")
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--output", default=None, help=f"output path (default {DEFAULT_OUTPUT}, relative to repo root)")
    parser.add_argument("--diff-base", default="origin/main", help="base ref for the change-scope test-coverage check")
    parser.add_argument("--no-diff", action="store_true", help="skip the change-scope check (change_scope_test_coverage -> UNKNOWN)")
    parser.add_argument("--print", dest="do_print", action="store_true", help="print the snapshot JSON to stdout instead of writing it")
    args = parser.parse_args(argv)

    root = Path(args.repo_root).resolve()
    output_path = Path(args.output) if args.output else root / DEFAULT_OUTPUT
    if not output_path.is_absolute():
        output_path = root / output_path

    changed_files = None if args.no_diff else _changed_files_vs(root, args.diff_base)

    try:
        snapshot = build_system_status_snapshot(
            repo_root=root,
            risk_rules_path=args.risk_rules,
            log_dir=args.log_dir,
            changed_files=changed_files,
        )
    except Exception as exc:  # noqa: BLE001 -- generation failure must never crash-loop or half-write
        print(f"system_status_snapshot: generation FAILED, previous snapshot (if any) left untouched: {exc}", file=sys.stderr)
        return 1

    if args.do_print:
        import json
        print(json.dumps(snapshot, indent=2, sort_keys=True, default=str))
        return 0

    try:
        write_snapshot_atomic(output_path, snapshot)
    except ValueError as exc:
        print(f"system_status_snapshot: refused to write invalid snapshot, previous snapshot (if any) left untouched: {exc}", file=sys.stderr)
        return 1

    print(f"system_status_snapshot: wrote {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
