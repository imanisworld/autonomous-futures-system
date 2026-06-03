"""
Terminal diagnostics for the RiskSentinel webhook service.

Usage:
    python3 scripts/doctor.py
    python3 scripts/doctor.py --json
    python3 scripts/doctor.py --strict
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from webhook.app import _diagnostics_payload  # noqa: E402


_STATUS_LABELS = {
    "ok": "OK",
    "info": "INFO",
    "warn": "WARN",
    "error": "FAIL",
}


def _print_human(payload: dict) -> None:
    overall = str(payload.get("overall_status", "unknown")).upper()
    checked_at = payload.get("checked_at", "unknown")
    print(f"RiskSentinel doctor: {overall}")
    print(f"Checked at: {checked_at}")
    print()

    for item in payload.get("items", []):
        status = _STATUS_LABELS.get(str(item.get("status", "")), "????")
        component = item.get("component", "Unknown")
        message = item.get("message", "")
        print(f"{status:<4} {component}: {message}")
        next_step = item.get("next_step")
        if next_step:
            print(f"     next: {next_step}")

    top_issue = payload.get("top_issue")
    if top_issue:
        print()
        print(f"Top issue: {top_issue.get('component')} - {top_issue.get('message')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run local RiskSentinel diagnostics.")
    parser.add_argument("--json", action="store_true", help="Print raw diagnostics JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on warnings as well as errors.",
    )
    args = parser.parse_args(argv)

    payload = _diagnostics_payload(date.today())
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _print_human(payload)

    overall = payload.get("overall_status")
    if overall == "error":
        return 1
    if args.strict and overall == "warn":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
