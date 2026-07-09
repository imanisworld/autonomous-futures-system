#!/usr/bin/env python3
"""Read-only CLI for box-side CANCELLED / Option C verification."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is part of runtime deps
    load_dotenv = None

from ops.box_cancelled_option_c_proof import (
    DEFAULT_LOCAL_API_BASE,
    build_box_cancelled_option_c_proof,
    print_human,
)
from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR


def main(argv: list[str] | None = None) -> int:
    if load_dotenv is not None and os.getenv("PYTHON_DOTENV_DISABLED") != "1":
        load_dotenv(ROOT / ".env")

    parser = argparse.ArgumentParser(
        description="Read-only box-side proof for post-taxonomy CANCELLED / Option C verification."
    )
    parser.add_argument("--repo-root", type=Path, default=ROOT, help="Repository root to inspect.")
    parser.add_argument(
        "--journal-dir",
        type=Path,
        default=Path(os.getenv("LOG_DIR", str(DEFAULT_JOURNAL_DIR))),
        help="Directory containing journal_*.jsonl. Defaults to LOG_DIR or the VPS runtime logs.",
    )
    parser.add_argument(
        "--api-base",
        default=DEFAULT_LOCAL_API_BASE,
        help="Local API base for /health and /status/broker-account. Use empty string to skip HTTP checks.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    if load_dotenv is not None and os.getenv("PYTHON_DOTENV_DISABLED") != "1":
        load_dotenv(args.repo_root / ".env")

    report = build_box_cancelled_option_c_proof(
        repo_root=args.repo_root,
        log_dir=args.journal_dir,
        api_base=args.api_base.strip() or None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print_human(report)

    if report["verdict"] == "PASS":
        return 0
    if report["verdict"] == "INSPECT":
        return 2
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
