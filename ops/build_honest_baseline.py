"""Read-only honest filled-trade baseline across full MNQ+MES journal history.

Reuses the trusted TRADE<->OUTCOME pairing and outcome classification from
``ops.proof_30_mnq`` (the same logic already verified live against the box),
then applies the operator-ruled overrides recorded in
``docs/proof-operator-overrides.md`` (2026-07-07 full-history phantom-clear
audit) so every reconciler-touched row lands in its true resolved bucket
instead of sitting forever in the ambiguous "needs manual review" catch-all.

This does not edit any journal or change any running proof tool. It is a
read-only report generator over a journal directory (local copy or a
mirror of the box's ``logs/`` dir) for building the honest baseline the
operator asked for after the override rulings landed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from ops.proof_30_mnq import (
    ResolvedTrade,
    classify_outcome,
    pair_resolved_trades,
    read_journal_entries,
)

INSTRUMENTS = ("MNQ", "MES")

# The no-fill taxonomy (PR #167) was deployed to the box at this release;
# OUTCOME rows before this timestamp cannot carry no_fill_reason/order_type
# fields even though the underlying journal_logger code now supports them.
NO_FILL_TAXONOMY_DEPLOY_TS = "2026-07-07T18:35:33+00:00"  # release 3c7a6b044cc8

# Operator rulings from docs/proof-operator-overrides.md, keyed by the exact
# TRADE row timestamp (not date/strategy guesswork) so an override can never
# silently apply to the wrong trade. All 21 phantom-cleared rows found by the
# 2026-07-07 audit are covered: 1 broker-verified win, 5 reconstructed
# losses, 14 confirmed genuine no-fills, 1 permanently unresolved/excluded.
OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("MES", "2026-07-06T14:45:15.094051+00:00"): {
        "category": "filled_win_loss", "result": "WIN", "pnl_dollars": 60.60,
        "note": "Broker-verified erased win (2026-07-06 incident, #146 fix)",
    },
    ("MES", "2026-06-08T15:00:10.819898+00:00"): {
        "category": "filled_win_loss", "result": "LOSS", "pnl_dollars": -28.75,
        "note": "2026-07-07 audit: reconstructed erased loss",
    },
    ("MES", "2026-06-09T10:15:08.886190+00:00"): {
        "category": "filled_win_loss", "result": "LOSS", "pnl_dollars": -21.25,
        "note": "2026-07-07 audit: reconstructed erased loss",
    },
    ("MES", "2026-06-18T11:15:11.148937+00:00"): {
        "category": "filled_win_loss", "result": "LOSS", "pnl_dollars": -56.25,
        "note": "2026-07-07 audit: reconstructed erased loss, independently re-verified via a second (Polygon) data source",
    },
    ("MES", "2026-06-29T10:45:11.809779+00:00"): {
        "category": "filled_win_loss", "result": "LOSS", "pnl_dollars": -35.00,
        "note": "2026-07-07 audit: reconstructed erased loss",
    },
    ("MES", "2026-06-30T10:15:02.294122+00:00"): {
        "category": "filled_win_loss", "result": "LOSS", "pnl_dollars": -35.00,
        "note": "2026-07-07 audit: reconstructed erased loss",
    },
    ("MNQ", "2026-07-01T16:00:09.147412+00:00"): {
        "category": "unresolved_excluded", "result": None, "pnl_dollars": None,
        "note": "2026-07-07 audit: erased fill, W/L genuinely indeterminate (bounded +$19.50/-$14.50); excluded from tally, not guessed",
    },
    # --- 14 confirmed genuine no-fills: reconciler cleared them, but the
    # decision-bar close was beyond the live IOC cap, so CANCELLED $0 is the
    # honest outcome. Move out of "reconciler_touched" into "cancelled_nofill".
    ("MES", "2026-07-02T14:00:25.111596+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (07-02, pre-existing solved case)"},
    ("MNQ", "2026-06-11T07:30:01.755299+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MNQ", "2026-06-15T08:15:01.803380+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MNQ", "2026-06-15T14:30:01.215320+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MNQ", "2026-06-16T08:15:03.323562+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-06-15T20:00:03.503167+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-06-16T03:45:12.227190+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-06-16T11:15:15.103041+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-06-16T14:30:03.279682+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-06-16T15:00:05.347594+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-06-19T02:15:10.929403+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-07-01T06:15:20.637915+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-07-01T07:15:19.450249+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    ("MES", "2026-07-01T13:45:21.035583+00:00"): {"category": "cancelled_nofill", "note": "confirmed genuine no-fill (2026-07-07 audit)"},
    # --- Found while building THIS baseline, NOT part of the original
    # 21-row audit. The original audit scanned journals from 2026-06-08
    # onward and filtered exit_reason for "phantom cleared"/"auto-reconcile"
    # specifically. These two predate that window (06-05) or use different
    # phrasing ("manual-reconcile") that the substring filter missed, even
    # though ops.proof_30_mnq.classify_outcome's broader RECONCILER_MARKERS
    # ("reconcile" substring) catches both. Both explicitly say
    # "User-authorized"/"user-authorized" in exit_reason (a known manual
    # intervention, not the silent pre-#146 bug), and both are confirmed
    # unmarketable by the same IOC-cap method used throughout the audit
    # (MNQ: cap 30193.25 vs close 30080.75, 112.5pt overshoot; MES: cap
    # 7552.5 vs close 7539.0, 13.5pt overshoot) -- genuine no-fills, not
    # erased real trades.
    ("MNQ", "2026-06-05T07:15:00.331773+00:00"): {
        "category": "cancelled_nofill",
        "note": "found building the 2026-07-07 baseline (outside the original 21-row audit's window/filter); confirmed genuine no-fill via IOC-cap check, 112.5pt overshoot",
    },
    ("MES", "2026-06-19T03:30:12.674488+00:00"): {
        "category": "cancelled_nofill",
        "note": "found building the 2026-07-07 baseline (outside the original 21-row audit's window/filter); confirmed genuine no-fill via IOC-cap check, 13.5pt overshoot",
    },
}

# 21 from the original phantom-clear audit + 2 found while building this
# baseline (see notes above) that the original audit's window/filter missed.
EXPECTED_OVERRIDE_COUNT = 23


def _apply_override(summary: dict[str, Any], trade: ResolvedTrade) -> dict[str, Any]:
    key = (str(trade.trade.get("instrument")), trade.trade_ts)
    override = OVERRIDES.get(key)
    if override is None:
        return summary
    summary["category"] = override["category"]
    if override.get("result") is not None:
        summary["result"] = override["result"]
    if override.get("pnl_dollars") is not None:
        summary["pnl_dollars"] = override["pnl_dollars"]
    summary["override_note"] = override["note"]
    summary["override_applied"] = True
    return summary


def build_instrument_baseline(entries: list[dict[str, Any]], instrument: str) -> dict[str, Any]:
    resolved, unmatched_outcomes = pair_resolved_trades(
        entries, instrument=instrument, limit=1_000_000,
    )
    rows = []
    for trade in resolved:
        summary = trade.to_summary()
        summary.setdefault("override_applied", False)
        rows.append(_apply_override(summary, trade))

    filled = [r for r in rows if r["category"] == "filled_win_loss"]
    wins = [r for r in filled if r["result"] == "WIN"]
    losses = [r for r in filled if r["result"] == "LOSS"]
    breakeven = [r for r in rows if r["category"] == "breakeven"]
    cancelled = [r for r in rows if r["category"] == "cancelled_nofill"]
    unresolved = [r for r in rows if r["category"] == "unresolved_excluded"]
    still_unclassified = [r for r in rows if r["category"] == "reconciler_touched"]
    other = [r for r in rows if r["category"] == "other"]

    net_pnl = round(sum(float(r["pnl_dollars"] or 0.0) for r in filled), 2)

    by_strategy: dict[str, dict[str, Any]] = {}
    for r in filled:
        bucket = by_strategy.setdefault(
            r.get("strategy") or "unknown", {"wins": 0, "losses": 0, "pnl_dollars": 0.0}
        )
        if r["result"] == "WIN":
            bucket["wins"] += 1
        elif r["result"] == "LOSS":
            bucket["losses"] += 1
        bucket["pnl_dollars"] = round(bucket["pnl_dollars"] + float(r["pnl_dollars"] or 0.0), 2)

    def is_post_taxonomy(row: dict[str, Any]) -> bool:
        return str(row.get("outcome_ts") or "") >= NO_FILL_TAXONOMY_DEPLOY_TS

    return {
        "instrument": instrument,
        "total_resolved_pairs": len(rows),
        "filled_wl_count": len(filled),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl_dollars": net_pnl,
        "breakeven_count": len(breakeven),
        "cancelled_nofill_count": len(cancelled),
        "unresolved_excluded_count": len(unresolved),
        "still_unclassified_reconciler_touched_count": len(still_unclassified),
        "other_category_count": len(other),
        "unmatched_outcomes_count": len(unmatched_outcomes),
        "no_fill_taxonomy_deploy_ts": NO_FILL_TAXONOMY_DEPLOY_TS,
        "pre_taxonomy_filled_count": sum(1 for r in filled if not is_post_taxonomy(r)),
        "post_taxonomy_filled_count": sum(1 for r in filled if is_post_taxonomy(r)),
        "pre_taxonomy_cancelled_count": sum(1 for r in cancelled if not is_post_taxonomy(r)),
        "post_taxonomy_cancelled_count": sum(1 for r in cancelled if is_post_taxonomy(r)),
        "by_strategy": by_strategy,
        "trades": rows,
        "unmatched_outcomes": unmatched_outcomes,
        "still_unclassified_reconciler_touched": still_unclassified,
        "other_category_rows": other,
    }


def build_baseline(journal_dir: Path) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    per_instrument = {inst: build_instrument_baseline(entries, inst) for inst in INSTRUMENTS}
    applied = sum(
        1
        for inst_report in per_instrument.values()
        for row in inst_report["trades"]
        if row.get("override_applied")
    )
    return {
        "journal_dir": str(journal_dir),
        "override_rows_defined": len(OVERRIDES),
        "override_rows_applied": applied,
        "override_count_matches_audit": applied == EXPECTED_OVERRIDE_COUNT,
        "instruments": per_instrument,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=None, help="write JSON report here instead of stdout")
    args = parser.parse_args()

    report = build_baseline(args.journal_dir)
    output = json.dumps(report, indent=2, default=str)
    if args.out:
        args.out.write_text(output, encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
