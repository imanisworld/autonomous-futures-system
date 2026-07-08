"""Read-only marketability audit of plain (non-reconciler-touched) CANCELLED rows.

The 2026-07-07 phantom-clear audit and the honest-baseline rebuild
(ops/build_honest_baseline.py) only re-verified CANCELLED rows that were
*reconciler-touched* (exit_reason matched a reconcile/phantom/naked/
auto-flatten marker). The much larger set of "plain" CANCELLED rows --
produced by the normal IOC-limit-expired path, never touched by the
reconciler -- have never been independently checked. This script applies
the same IOC-cap marketability arithmetic used throughout that audit to
every plain CANCELLED row, to answer one question: is there any row where
the decision-bar close was actually within the live IOC cap (i.e. should
have been marketable) but got logged CANCELLED anyway? That would be a
previously-unknown bug class, distinct from the reconciler bug already
fixed by #146.

Each row also reports the no-fill taxonomy fields (broker_status_raw,
no_fill_reason, order_type, signal/submit/cancel timestamps), the derived
signal-to-submit latency, and whether it postdates the taxonomy deploy
(2026-07-07T18:35:33Z). A MISLABELED_FILL_SUSPECT row that postdates the
taxonomy deploy but still has no_fill_reason absent or NO_FILL_UNKNOWN is
flagged option_c_recurrence -- the exact signature found in the 2026-06-25
MNQ pdh_reclaim anomaly, which the operator asked to watch for rather than
resolve historically. This does not count suspects as wins/losses and does
not change any trading behavior.

This does not edit any journal or change any running proof tool. Read-only.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from execution.no_fill_taxonomy import NO_FILL_UNKNOWN
from ops.build_honest_baseline import OVERRIDES
from ops.proof_30_mnq import classify_outcome, pair_resolved_trades, parse_proof_ts, read_journal_entries

INSTRUMENTS = ("MNQ", "MES")

# The no-fill taxonomy (PR #167) went live in this release; outcome rows
# resolved before it never had broker_status_raw/no_fill_reason/timestamp
# fields populated, so "the taxonomy should have explained this" only
# applies from here forward. See project_no_fill_taxonomy memory.
TAXONOMY_DEPLOY_TS = "2026-07-07T18:35:33+00:00"

# Empirically validated against every worked case in
# docs/proof-operator-overrides.md (21 cases, all consistent): tick size is
# 0.25 index points for both instruments, so tolerance-in-points =
# tolerance-in-ticks * 0.25. (Note: the audit doc's own prose parenthetical
# "(1.0 pt)" for MES is inconsistent with every one of its worked examples,
# which all use 4.0pt -- trusting the worked arithmetic, not the prose.)
TOLERANCE_POINTS = {"MES": 16 * 0.25, "MNQ": 32 * 0.25}  # MES 4.0pt, MNQ 8.0pt
MARGINAL_THRESHOLD_POINTS = 0.5  # within 2 ticks either side of the cap


def _decision_close(trade: dict[str, Any]) -> float | None:
    context = trade.get("context") or {}
    close = context.get("close")
    return float(close) if close is not None else None


def _marketability(instrument: str, direction: str, entry: float, close: float) -> dict[str, Any]:
    tolerance = TOLERANCE_POINTS[instrument]
    if direction == "LONG":
        cap = entry + tolerance
        marketable = close <= cap
        margin = cap - close  # positive = comfortably unmarketable
    else:
        cap = entry - tolerance
        marketable = close >= cap
        margin = close - cap  # positive = comfortably unmarketable
    return {
        "cap": cap,
        "marketable_per_arithmetic": marketable,
        "margin_points": round(margin, 2),
        "marginal": abs(margin) <= MARGINAL_THRESHOLD_POINTS,
    }


def _signal_to_submit_latency_seconds(outcome_body: dict[str, Any]) -> float | None:
    signal_ts = parse_proof_ts(outcome_body.get("signal_timestamp"))
    submit_ts = parse_proof_ts(outcome_body.get("submit_timestamp"))
    if signal_ts is None or submit_ts is None:
        return None
    return (submit_ts - signal_ts).total_seconds()


def _is_post_taxonomy(outcome_ts: str) -> bool:
    ts = parse_proof_ts(outcome_ts)
    boundary = parse_proof_ts(TAXONOMY_DEPLOY_TS)
    if ts is None or boundary is None:
        return False
    return ts >= boundary


def audit_instrument(entries: list[dict[str, Any]], instrument: str) -> dict[str, Any]:
    resolved, _ = pair_resolved_trades(entries, instrument=instrument, limit=1_000_000)
    plain_cancelled = []
    for trade in resolved:
        category = classify_outcome(trade.outcome_body)
        if category != "cancelled_nofill":
            continue
        key = (instrument, trade.trade_ts)
        if key in OVERRIDES:
            continue  # already independently reconstructed by the phantom-clear audit
        plain_cancelled.append(trade)

    rows = []
    for trade in plain_cancelled:
        setup = trade.setup
        entry = setup.get("entry")
        direction = setup.get("direction")
        close = _decision_close(trade.trade)
        post_taxonomy = _is_post_taxonomy(trade.outcome_ts)
        no_fill_reason = trade.outcome_body.get("no_fill_reason")
        row: dict[str, Any] = {
            "trade_ts": trade.trade_ts,
            "outcome_ts": trade.outcome_ts,
            "strategy": setup.get("strategy"),
            "direction": direction,
            "entry": entry,
            "decision_close": close,
            "exit_reason": trade.outcome_body.get("exit_reason"),
            "order_ids_present": bool(trade.trade.get("order_ids") or trade.outcome.get("order_ids")),
            "no_fill_reason": no_fill_reason,
            "order_type": trade.outcome_body.get("order_type"),
            "broker_status_raw": trade.outcome_body.get("broker_status_raw"),
            "signal_timestamp": trade.outcome_body.get("signal_timestamp"),
            "submit_timestamp": trade.outcome_body.get("submit_timestamp"),
            "cancel_timestamp": trade.outcome_body.get("cancel_timestamp"),
            "seconds_until_cancel": trade.outcome_body.get("seconds_until_cancel"),
            "signal_to_submit_latency_seconds": _signal_to_submit_latency_seconds(trade.outcome_body),
            "post_taxonomy": post_taxonomy,
            "option_c_recurrence": False,
        }
        if entry is None or close is None or direction not in ("LONG", "SHORT"):
            row["classification"] = "DATA_GAP_EXCLUDED"
            row["reason"] = "missing entry, decision-bar close, or direction"
        else:
            mkt = _marketability(instrument, direction, float(entry), close)
            row.update(mkt)
            if mkt["marketable_per_arithmetic"]:
                row["classification"] = "MISLABELED_FILL_SUSPECT"
            elif mkt["marginal"]:
                row["classification"] = "CONFIRMED_NO_FILL_MARGINAL"
            else:
                row["classification"] = "CONFIRMED_NO_FILL"
        # Option C (per the 2026-06-25 MNQ pdh_reclaim anomaly, see
        # project_no_fill_taxonomy memory): a marketable-per-arithmetic close
        # logged as a generic cancel, with the taxonomy fields that should
        # explain a genuine post-taxonomy no-fill either absent entirely or
        # stuck at the catch-all NO_FILL_UNKNOWN bucket. This does not prove
        # a bug on its own -- it flags the exact signature to watch for.
        if (
            row["classification"] == "MISLABELED_FILL_SUSPECT"
            and post_taxonomy
            and (not no_fill_reason or no_fill_reason == NO_FILL_UNKNOWN)
        ):
            row["option_c_recurrence"] = True
        rows.append(row)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row["classification"]] = counts.get(row["classification"], 0) + 1

    return {
        "instrument": instrument,
        "plain_cancelled_total": len(rows),
        "classification_counts": counts,
        "suspect_rows": [r for r in rows if r["classification"] == "MISLABELED_FILL_SUSPECT"],
        "marginal_rows": [r for r in rows if r["classification"] == "CONFIRMED_NO_FILL_MARGINAL"],
        "data_gap_rows": [r for r in rows if r["classification"] == "DATA_GAP_EXCLUDED"],
        "post_taxonomy_total": sum(1 for r in rows if r["post_taxonomy"]),
        "option_c_recurrence_rows": [r for r in rows if r["option_c_recurrence"]],
        "all_rows": rows,
    }


def build_audit(journal_dir: Path) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    return {inst: audit_instrument(entries, inst) for inst in INSTRUMENTS}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    report = build_audit(args.journal_dir)
    output = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
