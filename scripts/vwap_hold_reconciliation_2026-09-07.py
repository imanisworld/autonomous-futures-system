#!/usr/bin/env python3
"""VWAP Hold reconciliation (2026-09-07). Research only.

Reconciles two honest but non-comparable results:
  * 2026-07-26 NY-only ioc_close cell: 348 replay-APPROVED arms from
    logs/retest_baseline_off (107 NY), 5m Polygon bars, arrival = bar_ts+15m,
    IOC at arrival CLOSE with 32-tick tolerance, zero entry slippage, costs at
    the metrics layer -> NY-only passes walk-forward at every cost tier.
  * 2026-09-07 edge-decomposition audit: the raw `_try_vwap_hold` predicate on
    the 15m condition-fixed corpus, 4,579 ungated hits, no next-bar direction,
    6-8% IOC fill.

Everything here runs on ONE corpus (data/replay_polygon_5m), ONE fill model
(the package's unmodified `ioc_fill(field="close")`), ONE exit engine (the
package's `resolve_via_broker`), and ONE arrival definition (bar_ts + 15 min).
Only the population varies: the replay's own APPROVED rows versus the rows the
same replay REJECTED (with their recorded failed gates), split by session.
A next-bar time-exit direction control (no stop, adverse tick each side) is
added to every population, which is the stage the 2026-07-26 evidence never
had.

No strategy, risk, replay, broker, config, or deployment change.
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.vwap_hold_evidence_package import COMMISSION_RT, cell_metrics, ioc_fill, resolve_via_broker  # noqa: E402
from scripts.vwap_hold_ioc_close_concentration import EXPECTED_SHA256, compute_ioc_close_report  # noqa: E402
from scripts.vwap_hold_paired_fill_comparison import JOURNALS, _parse_dt, fingerprint, load_bars  # noqa: E402

TICK = 0.25
POINT_VALUE = 2.0
CONTROL_COMMISSION = 1.48  # the audit's round-turn convention, kept for comparability with #483
HORIZONS_MIN = (30, 60, 120)
AUDIT_WINDOW = (datetime.fromisoformat("2025-07-24T00:00:00+00:00"), datetime.fromisoformat("2026-06-27T00:00:00+00:00"))
OUT_JSON = REPO / "scripts" / "vwap_hold_reconciliation_2026-09-07.json"


def load_all_rows() -> list[dict]:
    arms = []
    for path in sorted(JOURNALS.glob("journal_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            setup = row.get("setup") or {}
            if setup.get("strategy") != "vwap_hold":
                continue
            ts = _parse_dt(str(row.get("bar_ts") or ""))
            if ts is None:
                continue
            approved = row.get("decision") == "TRADE" and (row.get("risk_check") or {}).get("result") == "APPROVED"
            gates = row.get("failed_gates") or []
            arms.append({
                "bar_ts": str(row.get("bar_ts")),
                "signal_dt": ts,
                "armed_at": ts + timedelta(minutes=15),
                "direction": str(setup["direction"]).upper(),
                "entry": float(setup["entry"]),
                "stop": float(setup["stop"]),
                "target": float(setup["target"]),
                "session": str(row.get("session") or ""),
                "approved": approved,
                "failed_gates": sorted(gates) if isinstance(gates, list) else [str(gates)],
                "market_condition": row.get("market_condition"),
            })
    arms.sort(key=lambda a: a["armed_at"])
    return arms


def direction_control(arm: dict, bars: list[dict]) -> dict | None:
    after = [b for b in bars if b["ts"] >= arm["armed_at"]]
    if not after:
        return None
    arrival = after[0]
    sign = 1.0 if arm["direction"] == "LONG" else -1.0
    entry = arrival["close"] + sign * TICK
    out = {"entry_ts": arrival["ts"].isoformat(), "entry": entry, "horizons": {}}
    for h in HORIZONS_MIN:
        cutoff = arrival["ts"] + timedelta(minutes=h)
        path = [b for b in after if b["ts"] <= cutoff]
        if len(path) < 2:
            out["horizons"][f"{h}m"] = None
            continue
        exit_px = path[-1]["close"] - sign * TICK
        pts = sign * (exit_px - entry)
        out["horizons"][f"{h}m"] = {"pts": pts, "net": pts * POINT_VALUE - CONTROL_COMMISSION}
    last = bars[-1]
    if last["ts"] > arrival["ts"]:
        exit_px = last["close"] - sign * TICK
        pts = sign * (exit_px - entry)
        out["horizons"]["EOD"] = {"pts": pts, "net": pts * POINT_VALUE - CONTROL_COMMISSION}
    else:
        out["horizons"]["EOD"] = None
    return out


def stats(values: list[float]) -> dict:
    n = len(values)
    if n == 0:
        return {"n": 0}
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if n > 1 else 0.0
    t = mean / (sd / math.sqrt(n)) if sd > 0 and n > 1 else None
    return {"n": n, "mean_net": round(mean, 2), "t_stat": round(t, 2) if t is not None else None,
            "share_positive": round(sum(1 for v in values if v > 0) / n, 3), "total_net": round(sum(values), 2)}


def control_summary(arms: list[dict]) -> dict:
    per_h: dict[str, list[float]] = defaultdict(list)
    n_data = 0
    for arm in arms:
        bars = load_bars(arm["armed_at"].date().isoformat())
        res = direction_control(arm, bars)
        if res is None:
            continue
        n_data += 1
        for label, h in res["horizons"].items():
            if h is not None:
                per_h[label].append(h["net"])
    out = {"n_with_data": n_data, "horizons": {label: stats(per_h[label]) for label in [f"{h}m" for h in HORIZONS_MIN] + ["EOD"]}}
    pos = [label for label, s in out["horizons"].items() if s.get("n") and s["mean_net"] > 0]
    out["positive_horizons"] = f"{len(pos)}/4"
    out["best_t_stat"] = max((s["t_stat"] for s in out["horizons"].values() if s.get("t_stat") is not None), default=None)
    return out


def per_trade_static(arms: list[dict], exit_mode: str, cost_ticks: int, *, reference_field: str = "close",
                     same_bar: bool = False) -> list[dict]:
    """Per-trade net P&L under the package's fill + exit functions. same_bar=True
    swaps the arrival bar for the signal bar's own last 5m bar (the audit's
    reference), keeping every other convention identical."""
    cost = COMMISSION_RT + cost_ticks * 0.50
    rows = []
    for arm in arms:
        bars = load_bars(arm["armed_at"].date().isoformat())
        a = arm
        if same_bar:
            a = {**arm, "armed_at": arm["signal_dt"] + timedelta(minutes=10)}
        fr = ioc_fill(a, bars, reference_field)
        if fr["status"] != "FILLED":
            rows.append({**arm, "outcome": "NO_FILL", "pnl_net": None})
            continue
        res = resolve_via_broker(arm, fr["fill_price"], fr["fill_ts"], bars, exit_mode)
        rows.append({**arm, "outcome": res["outcome"], "exit_reason": res.get("exit_reason"),
                     "pnl_net": (res["pnl"] - cost) if res["outcome"] in {"WIN", "LOSS", "BREAKEVEN"} else None})
    return rows


def summarize_trades(rows: list[dict]) -> dict:
    filled = [r for r in rows if r["outcome"] != "NO_FILL"]
    resolved = [r for r in rows if r["pnl_net"] is not None]
    # Marketable IOC fills the broker refused to hold (ENTRY_BRACKET_INVALID_AT_FILL,
    # #508). Reported on their own line, never blended into net.
    invalid_at_fill = sum(1 for r in rows if r.get("exit_reason") == "ENTRY_BRACKET_INVALID_AT_FILL")
    if not resolved:
        return {"armed": len(rows), "filled": len(filled), "resolved": 0, "invalid_at_fill": invalid_at_fill}
    net = [r["pnl_net"] for r in resolved]
    gw = sum(v for v in net if v > 0); gl = -sum(v for v in net if v <= 0)
    dates = [r["signal_dt"] for r in resolved]
    mid_date = sorted(dates)[len(dates) // 2]
    h1_date = sum(r["pnl_net"] for r in resolved if r["signal_dt"] < mid_date)
    mid_n = len(net) // 2
    by_month: dict[str, float] = defaultdict(float)
    for r in resolved:
        by_month[r["bar_ts"][:7]] += r["pnl_net"]
    top3 = sorted(by_month.values(), reverse=True)[:3]
    winners = sorted((v for v in net if v > 0), reverse=True)
    total = sum(net)
    return {
        "invalid_at_fill": invalid_at_fill, "armed": len(rows), "filled": len(filled), "fill_rate": round(len(filled) / len(rows), 3) if rows else None,
        "resolved": len(resolved), "net": round(total, 2), "profit_factor": round(gw / gl, 3) if gl else None,
        "win_rate": round(sum(1 for v in net if v > 0) / len(net), 3),
        "h1_h2_by_trade_count": [round(sum(net[:mid_n]), 2), round(sum(net[mid_n:]), 2)],
        "h1_h2_by_date_median": [round(h1_date, 2), round(total - h1_date, 2)], "date_median": mid_date.date().isoformat(),
        "months": len(by_month), "top3_month_share": round(sum(top3) / total, 3) if total > 0 else None,
        "top5_winner_share": round(sum(winners[:5]) / total, 3) if total > 0 else None,
    }


def population_block(name: str, arms: list[dict], *, cross_check: dict | None = None) -> dict:
    block = {"name": name, "n": len(arms), "sessions": dict(Counter(a["session"] for a in arms)),
             "direction_control": control_summary(arms)}
    if arms:
        rep = compute_ioc_close_report(arms, cross_check=cross_check)
        block["ioc_close_package"] = {"population": rep["population"],
                                      "cells": {ex: {t: {k: rep["cells"][ex][t][k] for k in ("armed", "filled", "resolved", "net_pnl", "profit_factor", "win_rate", "positive_both_halves", "max_drawdown")}
                                                     for t in ("1_tick", "2_tick", "3_tick")} for ex in rep["cells"]}}
        if cross_check is not None:
            block["ioc_close_package"]["cross_check"] = rep["cross_check_against_committed_evidence_package_json"]["verdict"]
        block["static_2tick"] = summarize_trades(per_trade_static(arms, "static", 2))
        block["runner_2tick"] = summarize_trades(per_trade_static(arms, "runner", 2))
        block["static_2tick_same_bar_reference"] = summarize_trades(per_trade_static(arms, "static", 2, same_bar=True))
    return block


def main() -> None:
    rows = load_all_rows()
    approved = [a for a in rows if a["approved"]]
    rejected = [a for a in rows if not a["approved"]]
    fp = fingerprint(approved)
    assert len(approved) == 348 and fp == EXPECTED_SHA256, (len(approved), fp)
    existing = json.loads((REPO / "scripts" / "vwap_hold_evidence_package_results.json").read_text())

    ny = lambda xs: [a for a in xs if a["session"] == "new_york"]
    non_ny = lambda xs: [a for a in xs if a["session"] != "new_york"]
    in_window = lambda xs: [a for a in xs if AUDIT_WINDOW[0] <= a["signal_dt"] < AUDIT_WINDOW[1]]
    pre_window = lambda xs: [a for a in xs if a["signal_dt"] < AUDIT_WINDOW[0]]

    report = {
        "generated": datetime.now().astimezone().isoformat(),
        "population_source": "logs/retest_baseline_off/MNQ",
        "bars": "data/replay_polygon_5m/MNQ (5m Polygon, 2024-07-02 .. 2026-06-26)",
        "fill_model": "package ioc_fill(field='close'): arrival = first 5m bar >= bar_ts+15m, limit-IOC at entry -/+ 32 ticks, zero entry slippage; costs 1.24 + ticks*0.50 at the metrics layer",
        "direction_control": "entry at arrival-bar close +/- 1 adverse tick, exit at the close of the last 5m bar within 30/60/120 min and at the last bar of the day file, no stop, $1.48 round-turn",
        "journal_rows": {"total": len(rows), "approved": len(approved), "rejected": len(rejected),
                         "approved_fingerprint": fp,
                         "rejected_failed_gates": {" + ".join(k): v for k, v in Counter(tuple(a["failed_gates"]) for a in rejected).most_common()}},
        "populations": [],
    }
    blocks = [
        ("approved_all_348", approved, existing),
        ("approved_ny_107", ny(approved), None),
        ("approved_non_ny_241", non_ny(approved), None),
        ("rejected_all", rejected, None),
        ("rejected_ny", ny(rejected), None),
        ("rejected_non_ny", non_ny(rejected), None),
        ("rejected_detached_only", [a for a in rejected if a["failed_gates"] == ["ENTRY_DETACHED_FROM_PRICE"]], None),
        ("approved_ny_audit_window_2025-07-24_to_2026-06-26", in_window(ny(approved)), None),
        ("approved_ny_pre_audit_window", pre_window(ny(approved)), None),
        ("approved_all_audit_window", in_window(approved), None),
    ]
    for name, arms, cc in blocks:
        print(f"[reconcile] {name}: n={len(arms)}", flush=True)
        report["populations"].append(population_block(name, arms, cross_check=cc))

    OUT_JSON.write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(f"wrote {OUT_JSON}")
    for b in report["populations"]:
        dc = b["direction_control"]; s2 = b.get("static_2tick", {}); sb = b.get("static_2tick_same_bar_reference", {})
        print(f"\n{b['name']}: n={b['n']} | control pos {dc.get('positive_horizons')} best t {dc.get('best_t_stat')} | "
              f"60m {dc['horizons']['60m'].get('mean_net')} (t {dc['horizons']['60m'].get('t_stat')}) EOD {dc['horizons']['EOD'].get('mean_net')} (t {dc['horizons']['EOD'].get('t_stat')})")
        if s2:
            print(f"   static 2t: fill {s2.get('fill_rate')} n {s2.get('resolved')} net {s2.get('net')} PF {s2.get('profit_factor')} "
                  f"H1/H2 count {s2.get('h1_h2_by_trade_count')} date {s2.get('h1_h2_by_date_median')} top3mo {s2.get('top3_month_share')} top5win {s2.get('top5_winner_share')}")
            print(f"   same-bar ref: fill {sb.get('fill_rate')} n {sb.get('resolved')} net {sb.get('net')} PF {sb.get('profit_factor')}")
        if "ioc_close_package" in b and "cross_check" in b["ioc_close_package"]:
            print("   cross-check vs committed package JSON:", b["ioc_close_package"]["cross_check"])


if __name__ == "__main__":
    main()
