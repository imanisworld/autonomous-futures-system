#!/usr/bin/env python3
"""Read-only external-broker entry-fill calibration from append-only journals.

This measures what the broker actually did; it never edits config, changes a
fill model, calls Tradovate, submits/cancels an order, or recommends a new
slippage setting automatically.

Exact fill calibration is accepted only from confirmed TRADE rows that:
  * carry a broker client_order_id;
  * do NOT carry a PaperBroker paper_order_id; and
  * carry execution_audit.post_fill_validation with requested_entry,
    actual_entry and slippage diagnostics produced by the shared runtime
    post-fill validator.

CANCELLED external attempts are counted separately from fills. Missing audit
fields remain UNKNOWN rather than being reconstructed from later outcomes.

The journal does not prove whether an external broker row came from Tradovate
DEMO or LIVE. --source-mode is therefore a caller declaration for labeling
only; the report always exposes that limitation. Runtime/release provenance
must be supplied separately before this evidence is called DEMO calibration.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


def _paths(inputs: list[Path]) -> list[Path]:
    out: list[Path] = []
    for item in inputs:
        if item.is_dir():
            out.extend(sorted(item.glob("journal_*.jsonl")))
        else:
            out.append(item)
    return list(dict.fromkeys(out))


def _rows(paths: list[Path]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in _paths(paths):
        try:
            with path.open(encoding="utf-8") as handle:
                for line_no, line in enumerate(handle, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError as exc:
                        errors.append(f"{path}:{line_no}: {exc}")
                        continue
                    if not isinstance(row, dict):
                        errors.append(f"{path}:{line_no}: row is not a JSON object")
                        continue
                    row = dict(row)
                    row["_source_path"] = str(path)
                    row["_source_line"] = line_no
                    rows.append(row)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return rows, errors


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _fill_measurement(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("decision") != "TRADE":
        return None
    if row.get("paper_order_id"):
        return None
    client_id = str(row.get("client_order_id") or "").strip()
    if not client_id:
        return None
    audit = row.get("execution_audit") or {}
    post = audit.get("post_fill_validation") if isinstance(audit, dict) else None
    if not isinstance(post, dict):
        return None
    requested = _number(post.get("requested_entry"))
    actual = _number(post.get("actual_entry"))
    slippage = _number(post.get("slippage_ticks"))
    adverse = _number(post.get("adverse_slippage_ticks"))
    if None in (requested, actual, slippage, adverse):
        return None
    setup = row.get("setup") or {}
    return {
        "instrument": str(row.get("instrument") or "").upper(),
        "strategy": setup.get("strategy"),
        "client_order_id": client_id,
        "requested_entry": requested,
        "actual_entry": actual,
        "slippage_ticks": slippage,
        "adverse_slippage_ticks": adverse,
        "post_fill_accepted": post.get("accepted"),
        "failed_checks": post.get("failed_checks") or [],
        "ts": row.get("ts"),
        "source_path": row.get("_source_path"),
        "source_line": row.get("_source_line"),
    }


def _external_trade_without_exact_audit(row: dict[str, Any]) -> bool:
    return (
        row.get("decision") == "TRADE"
        and not row.get("paper_order_id")
        and bool(str(row.get("client_order_id") or "").strip())
        and _fill_measurement(row) is None
    )


def _no_fill_measurement(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("type") != "OUTCOME":
        return None
    outcome = row.get("outcome") or {}
    if not isinstance(outcome, dict) or outcome.get("result") != "CANCELLED":
        return None
    client_id = str(outcome.get("client_order_id") or "").strip()
    if not client_id or outcome.get("paper_order_id"):
        return None
    return {
        "instrument": str(row.get("instrument") or "").upper(),
        "strategy": outcome.get("strategy"),
        "client_order_id": client_id,
        "no_fill_reason": outcome.get("no_fill_reason"),
        "order_type": outcome.get("order_type"),
        "broker_status_raw": outcome.get("broker_status_raw"),
        "seconds_until_cancel": _number(outcome.get("seconds_until_cancel")),
        "requested_entry": _number(outcome.get("requested_entry")),
        "ts": row.get("ts"),
        "source_path": row.get("_source_path"),
        "source_line": row.get("_source_line"),
    }


def _summarize(fills: list[dict[str, Any]], no_fills: list[dict[str, Any]], unknown: int) -> dict[str, Any]:
    adverse = [float(row["adverse_slippage_ticks"]) for row in fills]
    signed = [float(row["slippage_ticks"]) for row in fills]
    classified_attempts = len(fills) + len(no_fills)
    taxonomy = [
        row for row in no_fills
        if row.get("no_fill_reason") not in (None, "", "NO_FILL_UNKNOWN")
        and row.get("order_type") not in (None, "")
    ]
    return {
        "exact_fills": len(fills),
        "external_cancelled_no_fills": len(no_fills),
        "external_confirmed_trades_missing_exact_fill_audit": unknown,
        "classified_attempts": classified_attempts,
        "classified_no_fill_rate": (
            len(no_fills) / classified_attempts if classified_attempts else None
        ),
        "no_fill_taxonomy_coverage": (
            len(taxonomy) / len(no_fills) if no_fills else None
        ),
        "signed_slippage_ticks": {
            "mean": statistics.fmean(signed) if signed else None,
            "median": statistics.median(signed) if signed else None,
            "min": min(signed) if signed else None,
            "max": max(signed) if signed else None,
        },
        "adverse_slippage_ticks": {
            "mean": statistics.fmean(adverse) if adverse else None,
            "median": statistics.median(adverse) if adverse else None,
            "p90_nearest_rank": _nearest_rank(adverse, 0.90),
            "p95_nearest_rank": _nearest_rank(adverse, 0.95),
            "max": max(adverse) if adverse else None,
        },
    }


def audit(paths: list[Path], *, source_mode: str = "unproven") -> dict[str, Any]:
    rows, read_errors = _rows(paths)
    fills: list[dict[str, Any]] = []
    no_fills: list[dict[str, Any]] = []
    unknown_rows: list[dict[str, Any]] = []
    paper_trade_rows = 0

    for row in rows:
        if row.get("decision") == "TRADE" and row.get("paper_order_id"):
            paper_trade_rows += 1
        fill = _fill_measurement(row)
        if fill is not None:
            fills.append(fill)
        elif _external_trade_without_exact_audit(row):
            unknown_rows.append({
                "instrument": str(row.get("instrument") or "").upper(),
                "client_order_id": row.get("client_order_id"),
                "ts": row.get("ts"),
                "source_path": row.get("_source_path"),
                "source_line": row.get("_source_line"),
            })
        no_fill = _no_fill_measurement(row)
        if no_fill is not None:
            no_fills.append(no_fill)

    instruments = sorted(
        {row["instrument"] for row in fills + no_fills + unknown_rows if row.get("instrument")}
    )
    by_instrument: dict[str, Any] = {}
    for instrument in instruments:
        f = [row for row in fills if row["instrument"] == instrument]
        n = [row for row in no_fills if row["instrument"] == instrument]
        u = sum(1 for row in unknown_rows if row["instrument"] == instrument)
        by_instrument[instrument] = _summarize(f, n, u)

    overall = _summarize(fills, no_fills, len(unknown_rows))
    status = (
        "CORRUPT_SOURCE"
        if read_errors
        else "MEASURED"
        if fills or no_fills
        else "INSUFFICIENT"
    )
    return {
        "status": status,
        "source_mode_declared": source_mode,
        "source_mode_independently_proven_by_journal": False,
        "source_mode_limitation": (
            "Journal rows distinguish PaperBroker from external broker identity but do not "
            "prove Tradovate DEMO versus LIVE. Reconcile account mode separately."
        ),
        "descriptive_only": True,
        "automatic_fill_model_or_config_change_authorized": False,
        "sample_threshold_pre_registered": None,
        "input_files": [str(path) for path in _paths(paths)],
        "rows_read": len(rows),
        "read_errors": read_errors,
        "paper_trade_rows_excluded": paper_trade_rows,
        "overall": overall,
        "by_instrument": by_instrument,
        "exact_fill_measurements": fills,
        "external_no_fill_measurements": no_fills,
        "external_trades_missing_exact_fill_audit": unknown_rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument(
        "--source-mode",
        choices=("unproven", "demo", "live"),
        default="unproven",
        help="label only; journal rows cannot independently prove demo vs live",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = audit(args.paths, source_mode=args.source_mode)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] != "CORRUPT_SOURCE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
