#!/usr/bin/env python3
"""Read-only report of post-cap candidate eligibility.

The runtime journal is authoritative for capture. This reporter never recreates
strategy/risk logic. It only classifies already-recorded post_cap_eligibility
records and numbers rows that were approved by every local gate except the
daily trade-count cap.

No WIN/LOSS/P&L is produced here.
"""
from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def _parse_ts(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp required")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _row_ts(row: dict) -> datetime:
    for key in ("ts", "timestamp", "bar_ts"):
        if row.get(key):
            return _parse_ts(row[key])
    raise ValueError("post-cap row has no timestamp")


def build_report(
    log_dir: str | Path,
    *,
    requested_date: date | None = None,
    expected_cap: int = 3,
) -> dict:
    root = Path(log_dir)
    paths = (
        [root / f"journal_{requested_date.isoformat()}.jsonl"]
        if requested_date
        else sorted(root.glob("journal_*.jsonl"))
    )

    output_rows: list[dict] = []
    for path in paths:
        if not path.exists():
            continue
        day_text = path.stem.removeprefix("journal_")
        try:
            journal_day = date.fromisoformat(day_text)
        except ValueError:
            continue

        raw: list[dict] = []
        for row in _read_jsonl(path):
            if row.get("decision") != "BLOCKED_MAX_TRADES":
                continue
            if row.get("observed_decision") != "TRADE":
                continue
            if not isinstance(row.get("setup"), dict):
                continue
            block = row.get("execution_block") or {}
            limit = block.get("limit")
            if isinstance(limit, bool):
                raise ValueError(f"{path}: invalid execution cap")
            try:
                limit_int = int(limit)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: missing execution cap") from exc
            if limit_int != expected_cap:
                raise ValueError(
                    f"{path}: cap mismatch {limit_int} != expected {expected_cap}"
                )
            raw.append(row)

        raw.sort(key=_row_ts)
        eligible_index = 0
        for raw_index, row in enumerate(raw, start=1):
            eligibility = row.get("post_cap_eligibility")
            status = "UNVERIFIED"
            failed_rule = None
            shadow_trade_number = None

            if isinstance(eligibility, dict):
                if eligibility.get("status") == "ERROR":
                    status = "ERROR"
                else:
                    risk = eligibility.get("risk_without_daily_cap") or {}
                    failed_rule = risk.get("failed_rule")
                    approved = (
                        eligibility.get("observation_only") is True
                        and eligibility.get("execution_reachable") is False
                        and eligibility.get("eligible_except_daily_cap") is True
                        and risk.get("result") == "APPROVED"
                    )
                    if approved:
                        eligible_index += 1
                        status = "ELIGIBLE_EXCEPT_CAP"
                        shadow_trade_number = expected_cap + eligible_index
                    else:
                        status = "REJECTED_OTHER_GATE"

            setup = row["setup"]
            transformed = (
                eligibility.get("transformed_setup")
                if isinstance(eligibility, dict)
                else None
            )
            output_rows.append(
                {
                    "journal_day": journal_day.isoformat(),
                    "detected_at": _row_ts(row).isoformat(),
                    "raw_post_cap_index": raw_index,
                    "status": status,
                    "shadow_trade_number": shadow_trade_number,
                    "failed_rule": failed_rule,
                    "instrument": row.get("instrument"),
                    "session": row.get("session"),
                    "strategy": setup.get("strategy"),
                    "direction": setup.get("direction"),
                    "raw_setup": {
                        "entry": setup.get("entry"),
                        "stop": setup.get("stop"),
                        "target": setup.get("target"),
                        "rr_ratio": setup.get("rr_ratio"),
                    },
                    "transformed_setup": transformed,
                }
            )

    eligible = [r for r in output_rows if r["status"] == "ELIGIBLE_EXCEPT_CAP"]
    rejected = [r for r in output_rows if r["status"] == "REJECTED_OTHER_GATE"]
    unverified = [r for r in output_rows if r["status"] == "UNVERIFIED"]
    errors = [r for r in output_rows if r["status"] == "ERROR"]
    return {
        "schema_version": 1,
        "read_only": True,
        "execution_authority": False,
        "expected_execution_cap": expected_cap,
        "outcome_scoring_enabled": False,
        "summary": {
            "raw_post_cap_candidates": len(output_rows),
            "eligible_except_cap": len(eligible),
            "rejected_other_gate": len(rejected),
            "unverified": len(unverified),
            "errors": len(errors),
        },
        "rows": output_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--date")
    parser.add_argument("--expected-cap", type=int, default=3)
    parser.add_argument("--output")
    args = parser.parse_args()

    requested = date.fromisoformat(args.date) if args.date else None
    report = build_report(
        args.log_dir,
        requested_date=requested,
        expected_cap=args.expected_cap,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
