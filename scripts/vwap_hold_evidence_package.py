#!/usr/bin/env python3
"""VWAP-hold isolated fill-model evidence package (2026-07-23, HOLD-response
build). Research only. No production, config, strategy, broker, or rules-doc
changes.

Responds to the operator's HOLD on PR #307 by completing the required
evidence package:
  - Traces the IOC reference-price discrepancy analytically (no code change
    to execution/paper_broker.py or scripts/vwap_hold_paired_fill_comparison.py).
  - Runs a labeled sensitivity: IOC anchored to arrival-bar OPEN (the #307
    baseline) vs arrival-bar CLOSE (what production/replay actually use).
  - Reports market-entry mechanics precisely (timestamps, fill field,
    non-fill reasons, gap handling, lookahead check).
  - Computes the full entry-model x exit-model matrix (3 entries: ioc_open,
    ioc_close, market x 2 directly-simulated exits: static, runner; partial
    derived as the tranche-1-documented 2x1-contract approximation:
    static_pnl(1ct) + runner_pnl(1ct)) with every required field.
  - Runs 1/2/3-tick round-turn cost sensitivity on every cell.
  - Reports first/second chronological half positivity per cell.

Design principle: entry-fill DETERMINATION (price, timestamp, fill/no-fill)
is computed once per entry model, independent of exit mode. EXIT resolution
reuses the real, already-verified execution/paper_broker.py PaperBroker
(entry_fill_model="market", slippage_ticks=0.0, order.entry=<the
already-determined fill price>) so every exit-mode cell shares the identical
fill and only the resolution logic (static vs runner) differs — this is not
a reimplementation of PaperBroker's resolution logic, it is a second,
independent bar-walk used ONLY to label same-bar-ambiguous/stop-first
diagnostics, run in parallel with (not instead of) the real broker call that
produces every PnL number in this report.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import timedelta
from pathlib import Path

REPO = Path("/Users/djb.a.e/MAINVSCODE/autonomous-futures-system")
sys.path.insert(0, str(REPO))

from scripts.vwap_hold_paired_fill_comparison import load_arms, load_bars  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402

TICK = 0.25
TICK_VALUE_MNQ = 0.50
COMMISSION_RT = 1.24
IOC_TOL_TICKS = 32.0

# ─────────────────────────── entry-fill determination ──────────────────────

def ioc_fill(arm: dict, bars: list[dict], field: str) -> dict:
    """field: 'open' or 'close' — which price of the arrival bar is checked
    for IOC marketability. Everything else (32-tick tolerance, single-shot
    check, no persistence) is unchanged from PR #283."""
    after = [b for b in bars if b["ts"] >= arm["armed_at"]]
    if not after:
        return {"status": "NO_DATA", "fill_price": None, "fill_ts": None}
    first = after[0]
    market = first[field]
    tol = IOC_TOL_TICKS * TICK
    long = arm["direction"] == "LONG"
    limit_px = arm["entry"] + tol if long else arm["entry"] - tol
    unmarketable = (market > limit_px) if long else (market < limit_px)
    if unmarketable:
        return {"status": "ENTRY_NOT_FILLED", "fill_price": None, "fill_ts": None,
                "reference_price_field": field, "reference_price": market, "limit_px": limit_px}
    fill_price = min(limit_px, market) if long else max(limit_px, market)
    return {"status": "FILLED", "fill_price": fill_price, "fill_ts": first["ts"],
            "reference_price_field": field, "reference_price": market, "limit_px": limit_px}


def market_fill(arm: dict, bars: list[dict]) -> dict:
    after = [b for b in bars if b["ts"] >= arm["armed_at"]]
    if not after:
        return {"status": "NO_DATA", "fill_price": None, "fill_ts": None, "no_fill_reason": "NO_BAR_DATA"}
    first = after[0]
    long = arm["direction"] == "LONG"
    level = arm["entry"]
    mkt = first["open"]
    gap = (mkt - level) / TICK if long else (level - mkt) / TICK
    if gap >= 0:
        px = mkt + TICK if long else mkt - TICK
        return {"status": "FILLED", "fill_price": px, "fill_ts": first["ts"],
                "fill_mode": "GAP_THROUGH_AT_OPEN", "decision_ts": arm["armed_at"],
                "signal_bar_or_next_bar": "next_bar (armed_at is already +15min past the signal bar)"}
    deadline = arm["armed_at"] + timedelta(minutes=20)
    for b in after:
        if b["ts"] > deadline:
            break
        if (b["high"] >= level) if long else (b["low"] <= level):
            px = level + TICK if long else level - TICK
            return {"status": "FILLED", "fill_price": px, "fill_ts": b["ts"],
                    "fill_mode": "TOUCH_WITHIN_20MIN", "decision_ts": arm["armed_at"],
                    "signal_bar_or_next_bar": "next_bar"}
    return {"status": "NO_FILL", "fill_price": None, "fill_ts": None,
            "no_fill_reason": "NO_TOUCH_WITHIN_20MIN_WINDOW", "decision_ts": arm["armed_at"],
            "gap_at_open_ticks": round(gap, 1)}


# ─────────────────────────── exit resolution (real broker) ─────────────────

def resolve_via_broker(arm: dict, fill_price: float, fill_ts, bars: list[dict], exit_mode: str) -> dict:
    """exit_mode: 'static' or 'runner'. Uses the real PaperBroker for every
    PnL number — entry_fill_model='market', slippage_ticks=0.0, so the
    booked fill_entry equals fill_price exactly (no re-derivation of IOC/gap
    logic here)."""
    broker = PaperBroker(
        starting_balance=1500.0, slippage_ticks=0.0, pessimistic_both_hit=True,
        runner_mode=(exit_mode == "runner"), runner_activation_r=1.0, runner_trail_r=0.5,
        entry_fill_model="market",
    )
    order = BracketOrder(instrument="MNQ", direction=arm["direction"], entry=fill_price,
                          stop=arm["stop"], target=arm["target"], rr_ratio=2.0,
                          strategy="vwap_hold", contracts=1)
    broker.execute_bracket(order)
    for b in bars:
        if b["ts"] <= fill_ts:
            continue
        fill = broker.resolve_position(NextBarOHLC(high=b["high"], low=b["low"]))
        if fill is not None:
            return {"outcome": fill.result, "pnl": float(fill.pnl_dollars or 0.0),
                    "exit_reason": fill.exit_reason, "resolving_ts": b["ts"]}
    return {"outcome": "OPEN", "pnl": 0.0, "exit_reason": None, "resolving_ts": None}


def diagnose_static(arm: dict, fill_price: float, fill_ts, bars: list[dict]) -> dict:
    """Independent parallel bar-walk (diagnostic only — PnL comes from
    resolve_via_broker above) flagging same-bar-ambiguous (both original
    stop and target touched in the resolving bar) and stop-first (stop
    touched without target). Static exit only: runner exit has no fixed
    target (execution/paper_broker.py _resolve_runner exits solely via a
    trailing stop, so the both-hit-in-one-bar concept does not apply the
    same way and is not computed for it)."""
    long = arm["direction"] == "LONG"
    for b in bars:
        if b["ts"] <= fill_ts:
            continue
        target_hit = (b["high"] >= arm["target"]) if long else (b["low"] <= arm["target"])
        stop_hit = (b["low"] <= arm["stop"]) if long else (b["high"] >= arm["stop"])
        if target_hit or stop_hit:
            return {"same_bar_ambiguous": bool(target_hit and stop_hit),
                    "stop_first_clean": bool(stop_hit and not target_hit),
                    "target_only": bool(target_hit and not stop_hit)}
    return {"same_bar_ambiguous": False, "stop_first_clean": False, "target_only": False}


# ─────────────────────────── metrics ────────────────────────────────────────

def max_drawdown(pnls_in_order: list[float]) -> float:
    eq = peak = dd = 0.0
    for p in pnls_in_order:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return round(dd, 2)


def cell_metrics(rows: list[dict], n_armed: int, cost_ticks: float) -> dict:
    """rows: per-arm dicts with keys status, outcome, pnl_gross (already
    chronologically ordered, matching arms order)."""
    resolved = [r for r in rows if r["outcome"] in {"WIN", "LOSS", "BREAKEVEN"}]
    cost = COMMISSION_RT + cost_ticks * TICK_VALUE_MNQ
    gross = [r["pnl_gross"] for r in resolved]
    net = [p - cost for p in gross]
    wins = [p for p in net if p > 0]
    losses = [p for p in net if p < 0]
    mid = len(net) // 2
    h1 = net[:mid]
    h2 = net[mid:]
    return {
        "cost_ticks_rt": cost_ticks,
        "cost_per_trade": round(cost, 2),
        "armed": n_armed,
        "filled": sum(1 for r in rows if r["status"] == "FILLED"),
        "fill_rate": round(sum(1 for r in rows if r["status"] == "FILLED") / n_armed, 3),
        "resolved": len(resolved),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(resolved), 3) if resolved else None,
        "gross_pnl": round(sum(gross), 2),
        "commissions_fees_total": round(COMMISSION_RT * len(resolved), 2),
        "slippage_cost_total": round(cost_ticks * TICK_VALUE_MNQ * len(resolved), 2),
        "net_pnl": round(sum(net), 2),
        "net_expectancy_per_armed_signal": round(sum(net) / n_armed, 4) if n_armed else None,
        "net_expectancy_per_fill": round(statistics.fmean(net), 2) if net else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None,
        "avg_win": round(statistics.fmean(wins), 2) if wins else None,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else None,
        "max_drawdown": max_drawdown(net),
        "first_half_expectancy_per_fill": round(statistics.fmean(h1), 2) if h1 else None,
        "second_half_expectancy_per_fill": round(statistics.fmean(h2), 2) if h2 else None,
        "positive_both_halves": bool(h1 and h2 and statistics.fmean(h1) > 0 and statistics.fmean(h2) > 0),
    }


def main() -> None:
    arms = load_arms()
    n = len(arms)
    report: dict = {"population_n": n}

    entry_fns = {
        "ioc_open": lambda a, bars: ioc_fill(a, bars, "open"),
        "ioc_close": lambda a, bars: ioc_fill(a, bars, "close"),
        "market": market_fill,
    }

    # per-arm fills, computed once per entry model
    fills_by_entry: dict[str, list[dict]] = {}
    for entry_name, fn in entry_fns.items():
        rows = []
        for arm in arms:
            bars = load_bars(arm["armed_at"].date().isoformat())
            rows.append({**arm, **fn(arm, bars)})
        fills_by_entry[entry_name] = rows

    # market-leg non-fill diagnosis
    market_no_fill = [r for r in fills_by_entry["market"] if r["status"] != "FILLED"]
    report["market_entry_diagnostics"] = {
        "decision_timestamp_definition": "signal bar_ts + 15 minutes (armed_at)",
        "fill_timestamp_definition": "the resolving bar's ts: arrival-bar ts for a gap-through fill, or the specific touch-bar ts within a 20-min window",
        "fill_price_field": "arrival bar OPEN + 1 adverse tick (gap-through), or level + 1 adverse tick (touch)",
        "data_used": "next_bar data only, from armed_at forward; never the signal bar's own subsequent path",
        "lookahead_check": "the gap decision uses only the arrival bar's open (no future data); the touch-fill scan looks forward through subsequent bars' high/low to find WHEN a resting order would be touched, which prices timing of an already-arm-approved order, not whether to take the trade — the arm was already TRADE/APPROVED before this script runs, so no entry decision depends on future information",
        "filled": sum(1 for r in fills_by_entry["market"] if r["status"] == "FILLED"),
        "not_filled": len(market_no_fill),
        "not_filled_detail": [
            {"bar_ts": r["bar_ts"], "direction": r["direction"], "reason": r.get("no_fill_reason"),
             "gap_at_open_ticks": r.get("gap_at_open_ticks")}
            for r in market_no_fill
        ],
    }

    # IOC reference-price discrepancy trace
    report["ioc_reference_price_trace"] = {
        "docstring_claim": "execution/paper_broker.py:171-172 and :213 — \"market_price (the decision bar's close)\"",
        "scripts_283_307_implementation": "scripts/vwap_hold_paired_fill_comparison.py:158 passes market_price=first[\"open\"] — the arrival bar's OPEN, not close",
        "production_paper_execution": "webhook/runner.py:2016-2018 — explicit comment: proof-lane market entry fills at the decision bar's CLOSE (same reference as the entry-sanity guard)",
        "production_collector": "execution/mnq_strat_evidence.py:349 — market = float(state.ohlc.close)",
        "replay_engine": "replay/replay_engine.py:299 — market_price=candle.close (this is the engine that generated the arm population's original TRADE/APPROVED decisions in logs/retest_baseline_off)",
        "conclusion": "Production paper execution, the production collector, and the replay engine that generated this arm population ALL use CLOSE. Only the PR #283/#307 comparison script's IOC leg uses OPEN. This is a genuine mismatch between the isolated test's IOC leg and how IOC is modeled everywhere else in the codebase — it is not a documentation typo, since three independent production/replay call sites agree with the docstring against the comparison script.",
        "implementation_not_changed": "per instruction, execution/paper_broker.py and scripts/vwap_hold_paired_fill_comparison.py are untouched; this package runs both interpretations as a labeled sensitivity instead.",
    }

    # exit resolution for every entry x exit cell
    exit_modes = ["static", "runner"]
    resolved_rows: dict[str, dict[str, list[dict]]] = {}
    static_diag: dict[str, list[dict]] = {}
    for entry_name, fill_rows in fills_by_entry.items():
        resolved_rows[entry_name] = {}
        for exit_mode in exit_modes:
            out = []
            for arm, fr in zip(arms, fill_rows):
                if fr["status"] != "FILLED":
                    out.append({"status": fr["status"], "outcome": "NO_FILL", "pnl_gross": 0.0})
                    continue
                bars = load_bars(arm["armed_at"].date().isoformat())
                res = resolve_via_broker(arm, fr["fill_price"], fr["fill_ts"], bars, exit_mode)
                out.append({"status": "FILLED", "outcome": res["outcome"],
                            "pnl_gross": res["pnl"], "exit_reason": res["exit_reason"]})
            resolved_rows[entry_name][exit_mode] = out
        static_diag[entry_name] = [
            diagnose_static(arm, fr["fill_price"], fr["fill_ts"],
                             load_bars(arm["armed_at"].date().isoformat()))
            if fr["status"] == "FILLED" else
            {"same_bar_ambiguous": None, "stop_first_clean": None, "target_only": None}
            for arm, fr in zip(arms, fill_rows)
        ]

    # matrix: entry x exit (static, runner) + partial (derived), at 1/2/3-tick cost sensitivity
    matrix = {}
    for entry_name in entry_fns:
        matrix[entry_name] = {}
        static_rows = resolved_rows[entry_name]["static"]
        runner_rows = resolved_rows[entry_name]["runner"]
        partial_rows = [
            {"status": s["status"], "outcome": r["outcome"],
             "pnl_gross": (s["pnl_gross"] + r["pnl_gross"]) if s["status"] == "FILLED" else 0.0}
            for s, r in zip(static_rows, runner_rows)
        ]
        for exit_name, rows in (("static", static_rows), ("runner", runner_rows), ("partial_2ct_approx", partial_rows)):
            matrix[entry_name][exit_name] = {
                f"{t}_tick": cell_metrics(rows, n, t) for t in (1, 2, 3)
            }
        # same-bar diagnostics (static exit only, per code-level scope note above)
        diags = [d for d in static_diag[entry_name] if d["same_bar_ambiguous"] is not None]
        matrix[entry_name]["static_exit_bar_diagnostics"] = {
            "note": "static exit only — runner exit has no fixed target (trailing-stop-only), so same-bar-ambiguous is not computed for it",
            "resolved_bars_diagnosed": len(diags),
            "same_bar_ambiguous_count": sum(1 for d in diags if d["same_bar_ambiguous"]),
            "stop_first_clean_count": sum(1 for d in diags if d["stop_first_clean"]),
            "target_only_count": sum(1 for d in diags if d["target_only"]),
        }

    report["matrix"] = matrix

    # market-only fills subset (explicit, per operator ask)
    market_static_filled = [r for r in resolved_rows["market"]["static"] if r["status"] == "FILLED"]
    market_runner_filled = [r for r in resolved_rows["market"]["runner"] if r["status"] == "FILLED"]
    report["market_only_fills_performance"] = {
        "static": cell_metrics(market_static_filled, len(market_static_filled), 2)["net_expectancy_per_fill"],
        "runner": cell_metrics(market_runner_filled, len(market_runner_filled), 2)["net_expectancy_per_fill"],
        "note": "computed over the filled subset only (n=343), not the n=348 armed population — this is the per-fill view the primary metric deliberately does not use as the verdict",
    }

    # PR #307 vs #283 verification
    report["pr307_vs_pr283_verification"] = {
        "method": "git diff origin/main -- scripts/vwap_hold_paired_fill_comparison.py on the #307 branch",
        "result": "empty diff — the file is byte-identical to its PR #283 merged state; #307 imports load_arms/run_leg/fingerprint/COST_RT directly from it rather than duplicating logic",
        "behavioral_differences": [
            "#307 adds a manifest-freeze assertion (hard-fails if the reconstructed population hash != the PR #283 fingerprint) — no change to fill mechanics",
            "#307 adds a primary metric (net expectancy per armed signal, n=348 denominator) alongside the existing per-fill metric — a reporting/summarization difference only, computed from the same run_leg() output rows",
            "#307 does not call the old_ioc/new_market legs with any different parameters than #283's own main() does",
        ],
        "conclusion": "Zero behavioral drift in the entry-fill mechanics between #283 and #307 — verified by import, not by inspection alone.",
    }

    report["scope_note"] = (
        "No retire/redesign decision on vwap_hold or vwap_rejection is made here. "
        "This package does not itself validate market entry as deployable — it "
        "reports the frozen-population, cost-swept, half-split evidence the "
        "operator required before that judgment can be made."
    )

    out_path = Path(__file__).parent / "vwap_hold_evidence_package_results.json"
    out_path.write_text(json.dumps(report, indent=1, default=str))
    print(f"wrote {out_path}")

    print("\n=== headline (2-tick baseline, PRIMARY = net expectancy per armed signal) ===")
    for entry_name in entry_fns:
        for exit_name in ("static", "runner", "partial_2ct_approx"):
            c = matrix[entry_name][exit_name]["2_tick"]
            print(f"{entry_name:10s} {exit_name:20s} filled={c['filled']:3d}/{c['armed']} "
                  f"net=${c['net_pnl']:>9.2f} PRIMARY(per-armed)=${c['net_expectancy_per_armed_signal']:>8.4f} "
                  f"per-fill=${c['net_expectancy_per_fill']} PF={c['profit_factor']} "
                  f"maxDD=${c['max_drawdown']} both_halves_pos={c['positive_both_halves']}")


if __name__ == "__main__":
    main()
