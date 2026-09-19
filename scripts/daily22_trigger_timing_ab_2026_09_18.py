#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from context import daily_22_swing_collector as lane
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from scripts.edge_decomposition_audit import load_bars
from strategy.strat_classifier import TWO_DOWN, TWO_UP, StratBar, classify_bar

ET = ZoneInfo("America/New_York")
PREREG_COMMIT = "cd4c207a03f818e5f0fd3988294dca83343a62a8"
BASE_COMMIT = "3fbd20c7a28494320ef1fc4f080ec0de0229a3c4"
ACTIVATION_COMMIT = "9caaaa3e2fbe5cb6ff20941229e3498794bcb6de"
EXPECTED_TREE = "7f09a7f82ee282e892f5db9b86be3130e6a5c1210d86828a56b9666bb06afd35"
EXPECTED_BASELINE = {"fills": 34, "net": 13885.18, "pf": 2.02, "max_dd_pct": 25.15}
TICK = lane.TICK
def tree_sha(root: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(root.glob("MNQ_*.jsonl")):
        h.update(path.name.encode())
        h.update(b"\0")
        h.update(path.read_bytes())
    return h.hexdigest()


def legacy_trading_day(ts: datetime) -> date | None:
    local = ts.astimezone(ET)
    clock = local.time().replace(tzinfo=None)
    if time(17, 0) <= clock < time(18, 0):
        return None
    if clock >= time(18, 0):
        return local.date() + timedelta(days=1)
    return local.date()


def current_trading_day(ts: datetime) -> date | None:
    return lane._trading_day(ts)


def payload(row: dict) -> SimpleNamespace:
    data = {k: v for k, v in row.items() if not k.startswith("_")}
    data["ticker"] = "MNQ1!"
    data["timestamp"] = row["timestamp"]
    return SimpleNamespace(**data)
def session_map(bars, day_fn) -> dict[date, dict]:
    out: dict[date, dict] = {}
    for idx, row in enumerate(bars.rows):
        day = day_fn(row["_dt"])
        if day is None:
            continue
        try:
            o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
        except (KeyError, TypeError, ValueError):
            continue
        bucket = out.setdefault(day, {
            "open": o, "high": h, "low": l, "close": c,
            "count": 0, "bars": [],
        })
        bucket["high"] = max(float(bucket["high"]), h)
        bucket["low"] = min(float(bucket["low"]), l)
        bucket["close"] = c
        bucket["count"] += 1
        bucket["bars"].append((idx, row))
    return out


def structural_events(bars, day_fn) -> list[dict]:
    sessions = session_map(bars, day_fn)
    days = sorted(sessions)
    complete = [d for d in days if sessions[d]["count"] >= lane.MIN_COMPLETE_SESSION_BARS]
    events: list[dict] = []
    for day in days:
        prior = [d for d in complete if d < day]
        if len(prior) < 2:
            continue
        two_back, previous = sessions[prior[-2]], sessions[prior[-1]]
        prev_type = classify_bar(
            StratBar(high=float(previous["high"]), low=float(previous["low"])),
            StratBar(high=float(two_back["high"]), low=float(two_back["low"])),
        )
        if prev_type not in (TWO_UP, TWO_DOWN):
            continue
        first = None
        for idx, row in sessions[day]["bars"]:
            up = float(row["high"]) > float(previous["high"])
            down = float(row["low"]) < float(previous["low"])
            if up or down:
                first = (idx, row, up, down)
                break
        if first is None:
            continue
        idx, row, up, down = first
        if up and down:
            events.append({"day": day.isoformat(), "bar_idx": idx, "status": "AMBIGUOUS"})
            continue
        cur_type = TWO_UP if up else TWO_DOWN
        if cur_type != prev_type:
            events.append({"day": day.isoformat(), "bar_idx": idx, "status": "REVERSAL"})
            continue
        direction = "LONG" if cur_type == TWO_UP else "SHORT"
        if direction == "LONG":
            entry = float(previous["high"]) + TICK
            stop = float(previous["low"]) - TICK
            target = entry + 2.0 * (entry - stop)
        else:
            entry = float(previous["low"]) - TICK
            stop = float(previous["high"]) + TICK
            target = entry - 2.0 * (stop - entry)
        events.append({
            "day": day.isoformat(), "bar_idx": idx, "status": "CONTINUATION",
            "direction": direction, "planned_entry": entry, "stop": stop, "target": target,
            "previous_high": float(previous["high"]), "previous_low": float(previous["low"]),
            "previous_type": prev_type, "trigger_bar_ts": row["timestamp"],
        })
    return events


def context_result(row: dict, direction: str) -> tuple[bool, str, dict]:
    return lane._context_gate(payload(row), direction)


def fill_rr(cand: dict, fill: float) -> float:
    return lane._actual_rr(cand, fill)


def risk_dollars(cand: dict, fill: float) -> float:
    return abs(fill - float(cand["stop"])) * lane.POINT_VALUE
def make_order(cand: dict) -> BracketOrder:
    return BracketOrder(
        instrument="MNQ", direction=cand["direction"],
        entry=float(cand["planned_entry"]), stop=float(cand["stop"]),
        target=float(cand["target"]), rr_ratio=2.0,
        strategy=lane.STRATEGY, contracts=1,
    )


def _net(fill) -> float:
    return round(float(fill.pnl_dollars or 0.0) - lane.COMMISSION_ROUND_TRIP, 2)


def resolve_model_a(cand: dict, bars, balance: float) -> dict:
    idx = cand["bar_idx"]
    row = bars.rows[idx]
    broker = lane._broker(balance)
    opened = broker.execute_bracket(make_order(cand), market_price=float(row["close"]))
    if opened.result != "OPEN":
        return {"status": "NO_FILL", "reason": opened.exit_reason, "exit_idx": idx}
    entry = float(opened.entry_price)
    both = 0
    for j in range(idx + 1, len(bars.rows)):
        bar = bars.rows[j]
        if cand["direction"] == "LONG":
            both += int(float(bar["low"]) <= cand["stop"] and float(bar["high"]) >= cand["target"])
        else:
            both += int(float(bar["high"]) >= cand["stop"] and float(bar["low"]) <= cand["target"])
        fill = broker.resolve_position(NextBarOHLC(high=float(bar["high"]), low=float(bar["low"])))
        if fill is not None:
            return {
                "status": "RESOLVED", "entry": entry, "exit_idx": j,
                "exit_bar_ts": bar["timestamp"], "result": fill.result,
                "exit_reason": fill.exit_reason, "net": _net(fill),
                "same_bar_both_stop_target": both,
            }
    return {"status": "OPEN", "entry": entry, "exit_idx": len(bars.rows) - 1}


def resolve_model_b(cand: dict, bars, entry_slip_ticks: float) -> dict:
    idx = cand["bar_idx"]
    if idx <= 0:
        return {"status": "NO_FILL", "reason": "NO_PRIOR_BAR", "exit_idx": idx}
    trigger = bars.rows[idx]
    planned = float(cand["planned_entry"])
    open_px = float(trigger["open"])
    if cand["direction"] == "LONG":
        fill_entry = (open_px if open_px >= planned else planned) + entry_slip_ticks * TICK
    else:
        fill_entry = (open_px if open_px <= planned else planned) - entry_slip_ticks * TICK
    valid = (
        float(cand["stop"]) < fill_entry < float(cand["target"])
        if cand["direction"] == "LONG"
        else float(cand["target"]) < fill_entry < float(cand["stop"])
    )
    if not valid:
        return {"status": "NO_FILL", "reason": "ENTRY_BRACKET_INVALID_AT_FILL",
                "exit_idx": idx, "entry": fill_entry}
    rr = fill_rr(cand, fill_entry)
    risk = risk_dollars(cand, fill_entry)
    if rr < lane.MIN_ACTUAL_RR:
        return {"status": "NO_FILL", "reason": "ACTUAL_RR_BELOW_2",
                "exit_idx": idx, "entry": fill_entry}
    if risk > lane.MAX_PLANNED_RISK_DOLLARS:
        return {"status": "NO_FILL", "reason": "PLANNED_RISK_ABOVE_1750",
                "exit_idx": idx, "entry": fill_entry}

    broker = PaperBroker(
        starting_balance=100_000.0, slippage_ticks=lane.SLIPPAGE_TICKS,
        pessimistic_both_hit=True, breakeven_at_1r=False, runner_mode=False,
        entry_fill_model="market",
    )
    broker.restore_position(
        instrument="MNQ", direction=cand["direction"], entry=fill_entry,
        stop=float(cand["stop"]), target=float(cand["target"]), contracts=1,
    )
    both_on_trigger = int(
        (float(trigger["low"]) <= cand["stop"] and float(trigger["high"]) >= cand["target"])
        if cand["direction"] == "LONG"
        else (float(trigger["high"]) >= cand["stop"] and float(trigger["low"]) <= cand["target"])
    )
    fill = broker.resolve_position(NextBarOHLC(
        open=float(trigger["open"]), high=float(trigger["high"]), low=float(trigger["low"])
    ))
    if fill is not None:
        return {
            "status": "RESOLVED", "entry": fill_entry, "exit_idx": idx,
            "exit_bar_ts": trigger["timestamp"], "result": fill.result,
            "exit_reason": fill.exit_reason, "net": _net(fill),
            "same_trigger_bar": True, "same_bar_both_stop_target": both_on_trigger,
        }
    for j in range(idx + 1, len(bars.rows)):
        bar = bars.rows[j]
        fill = broker.resolve_position(NextBarOHLC(
            open=float(bar["open"]), high=float(bar["high"]), low=float(bar["low"])
        ))
        if fill is not None:
            return {
                "status": "RESOLVED", "entry": fill_entry, "exit_idx": j,
                "exit_bar_ts": bar["timestamp"], "result": fill.result,
                "exit_reason": fill.exit_reason, "net": _net(fill),
                "same_trigger_bar": False, "same_bar_both_stop_target": both_on_trigger,
            }
    return {"status": "OPEN", "entry": fill_entry, "exit_idx": len(bars.rows) - 1}


def run_model(events: list[dict], bars, mode: str, entry_slip_ticks: float = 1.0) -> list[dict]:
    rows: list[dict] = []
    busy_until = -1
    balance = lane.STARTING_BALANCE
    peak = balance
    halted = False
    for cand in [e for e in events if e.get("status") == "CONTINUATION"]:
        row = dict(cand)
        idx = cand["bar_idx"]
        if idx <= busy_until:
            row.update(disposition="SKIPPED_POSITION_OPEN")
            rows.append(row)
            continue
        if halted:
            row.update(disposition="BLOCKED_MAX_DRAWDOWN")
            rows.append(row)
            continue
        ctx_idx = idx if mode == "A" else idx - 1
        if ctx_idx < 0:
            row.update(disposition="REJECTED_CONTEXT", reason="NO_PRIOR_CONTEXT_BAR")
            rows.append(row)
            continue
        ok, reason, audit = context_result(bars.rows[ctx_idx], cand["direction"])
        row["context"] = audit
        row["context_bar_ts"] = bars.rows[ctx_idx]["timestamp"]
        if not ok:
            row.update(disposition="REJECTED_CONTEXT", reason=reason)
            rows.append(row)
            continue
        if mode == "A":
            expected, reason = lane._expected_ioc_fill(cand, float(bars.rows[idx]["close"]))
            if expected is None:
                row.update(disposition="CANCELLED", reason=reason)
                rows.append(row)
                continue
            rr = fill_rr(cand, expected)
            risk = risk_dollars(cand, expected)
            if rr < lane.MIN_ACTUAL_RR:
                row.update(disposition="REJECTED_RISK", reason="ACTUAL_RR_BELOW_2",
                           expected_fill=expected, actual_rr=rr)
                rows.append(row)
                continue
            if risk > lane.MAX_PLANNED_RISK_DOLLARS:
                row.update(disposition="REJECTED_RISK", reason="PLANNED_RISK_ABOVE_1750",
                           expected_fill=expected, planned_risk_dollars=risk)
                rows.append(row)
                continue
            result = resolve_model_a(cand, bars, balance)
        else:
            result = resolve_model_b(cand, bars, entry_slip_ticks)
        row["resolution"] = result
        if result["status"] == "NO_FILL":
            row.update(disposition="CANCELLED", reason=result.get("reason"))
            rows.append(row)
            continue
        row["disposition"] = result["status"]
        if result["status"] in {"RESOLVED", "OPEN"}:
            busy_until = result["exit_idx"]
        if result["status"] == "RESOLVED":
            balance = round(balance + float(result["net"]), 2)
            peak = max(peak, balance)
            dd = max(0.0, (peak - balance) / peak) if peak else 1.0
            halted = dd >= lane.MAX_DRAWDOWN
            row.update(balance=balance, peak=peak, drawdown=dd)
        rows.append(row)
    return rows
def pf(nets: list[float]) -> float | None:
    wins = sum(x for x in nets if x > 0)
    losses = abs(sum(x for x in nets if x < 0))
    return math.inf if wins > 0 and losses == 0 else (wins / losses if losses else None)


def summary(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r.get("resolution", {}).get("status") == "RESOLVED"]
    nets = [float(r["resolution"]["net"]) for r in resolved]
    n = len(nets)
    split = (n + 1) // 2
    balances = [lane.STARTING_BALANCE]
    peak = lane.STARTING_BALANCE
    max_dd = 0.0
    for x in nets:
        balances.append(balances[-1] + x)
        peak = max(peak, balances[-1])
        max_dd = max(max_dd, (peak - balances[-1]) / peak if peak else 1.0)
    by_year = defaultdict(float)
    by_month = defaultdict(float)
    for r in resolved:
        net = float(r["resolution"]["net"])
        by_year[r["day"][:4]] += net
        by_month[r["day"][:7]] += net
    positive = sum(v for v in by_month.values() if v > 0)
    top3 = sum(sorted((v for v in by_month.values() if v > 0), reverse=True)[:3])
    p = pf(nets)
    return {
        "structural_continuations": len(rows),
        "resolved": n,
        "fills": sum(r.get("resolution", {}).get("status") in {"RESOLVED", "OPEN"} for r in rows),
        "wins": sum(x > 0 for x in nets), "losses": sum(x < 0 for x in nets),
        "net": round(sum(nets), 2),
        "pf": None if p is None else ("inf" if math.isinf(p) else round(p, 4)),
        "h1_net": round(sum(nets[:split]), 2), "h2_net": round(sum(nets[split:]), 2),
        "both_halves_positive": bool(
            nets[:split] and nets[split:] and sum(nets[:split]) > 0 and sum(nets[split:]) > 0
        ),
        "by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "max_dd_pct": round(max_dd * 100.0, 4),
        "top3_positive_month_concentration": round(top3 / positive, 4) if positive > 0 else None,
        "dispositions": dict(Counter(r.get("disposition") for r in rows)),
        "reasons": dict(Counter(r.get("reason") for r in rows if r.get("reason"))),
        "same_trigger_bar_resolutions": sum(
            bool(r.get("resolution", {}).get("same_trigger_bar")) for r in resolved
        ),
        "same_trigger_bar_both_stop_target": sum(
            int(r.get("resolution", {}).get("same_bar_both_stop_target", 0)) for r in resolved
        ),
    }


def gate0_check(s: dict) -> tuple[bool, list[str]]:
    problems = []
    if s["fills"] != EXPECTED_BASELINE["fills"]:
        problems.append(f"fills {s['fills']} != {EXPECTED_BASELINE['fills']}")
    if abs(s["net"] - EXPECTED_BASELINE["net"]) > 0.02:
        problems.append(f"net {s['net']} != {EXPECTED_BASELINE['net']}")
    pf_val = s["pf"] if isinstance(s["pf"], (int, float)) else math.inf
    if not math.isfinite(pf_val) or abs(float(pf_val) - EXPECTED_BASELINE["pf"]) > 0.02:
        problems.append(f"pf {s['pf']} != {EXPECTED_BASELINE['pf']}")
    if abs(s["max_dd_pct"] - EXPECTED_BASELINE["max_dd_pct"]) > 0.05:
        problems.append(f"max_dd_pct {s['max_dd_pct']} != {EXPECTED_BASELINE['max_dd_pct']}")
    if not s["both_halves_positive"]:
        problems.append("chronological halves not both positive")
    for year in ("2024", "2025", "2026"):
        if s["by_year"].get(year, 0.0) <= 0:
            problems.append(f"{year} not positive")
    return not problems, problems


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    vals = sorted(values)
    return vals[min(len(vals) - 1, max(0, math.ceil(q * len(vals)) - 1))]


def detachment(events: list[dict], bars) -> dict:
    rows = []
    for cand in [e for e in events if e.get("status") == "CONTINUATION"]:
        bar = bars.rows[cand["bar_idx"]]
        planned = float(cand["planned_entry"])
        close = float(bar["close"])
        signed = (
            (close - planned) / TICK
            if cand["direction"] == "LONG"
            else (planned - close) / TICK
        )
        adverse = max(0.0, signed)
        ok, reason, _ = context_result(bar, cand["direction"])
        expected, fill_reason = lane._expected_ioc_fill(cand, close)
        rr = fill_rr(cand, expected) if expected is not None else None
        rows.append({
            "day": cand["day"], "direction": cand["direction"],
            "trigger_bar_ts": cand["trigger_bar_ts"], "planned_entry": planned,
            "open": float(bar["open"]), "high": float(bar["high"]),
            "low": float(bar["low"]), "close": close,
            "signed_close_detachment_ticks": round(signed, 3),
            "adverse_detachment_ticks": round(adverse, 3),
            "absolute_detachment_ticks": round(abs(signed), 3),
            "gap_through": (
                float(bar["open"]) >= planned
                if cand["direction"] == "LONG"
                else float(bar["open"]) <= planned
            ),
            "trigger_context_ok": ok, "trigger_context_reason": reason,
            "ioc_fill_reason": fill_reason, "ioc_expected_fill": expected,
            "ioc_actual_rr": rr,
        })

    def stats(sub):
        absvals = [r["absolute_detachment_ticks"] for r in sub]
        adv = [r["adverse_detachment_ticks"] for r in sub]
        return {
            "n": len(sub),
            "median_abs": round(statistics.median(absvals), 3) if absvals else None,
            "p90_abs": round(percentile(absvals, .9), 3) if absvals else None,
            "max_abs": round(max(absvals), 3) if absvals else None,
            "median_adverse": round(statistics.median(adv), 3) if adv else None,
            "p90_adverse": round(percentile(adv, .9), 3) if adv else None,
            "max_adverse": round(max(adv), 3) if adv else None,
            "adverse_over_8": sum(v > lane.IOC_TOLERANCE_TICKS for v in adv),
            "ioc_cancel": sum(r["ioc_expected_fill"] is None for r in sub),
            "rr_below_2_after_close": sum(
                r["ioc_actual_rr"] is not None and r["ioc_actual_rr"] < lane.MIN_ACTUAL_RR
                for r in sub
            ),
        }
    return {
        "all": stats(rows),
        "trigger_context_approved": stats([r for r in rows if r["trigger_context_ok"]]),
        "rows": rows,
    }


def population_diff(legacy: list[dict], current: list[dict]) -> dict:
    def key(e):
        return (
            e.get("day"), e.get("status"), e.get("direction"),
            round(float(e.get("planned_entry", 0.0)), 4),
        )
    a, b = {key(e): e for e in legacy}, {key(e): e for e in current}
    return {
        "legacy_events": len(legacy), "current_events": len(current),
        "legacy_continuations": sum(e.get("status") == "CONTINUATION" for e in legacy),
        "current_continuations": sum(e.get("status") == "CONTINUATION" for e in current),
        "legacy_only": [a[k] for k in sorted(a.keys() - b.keys())],
        "current_only": [b[k] for k in sorted(b.keys() - a.keys())],
    }


def render_md(result: dict) -> str:
    g = result["gate0"]
    lines = [
        "# MNQ Daily 2-2 Trigger Timing A/B — 2026-09-18", "",
        "## Gate 0 — activation baseline reproduction", "",
        f"- pass: **{g['pass']}**",
        f"- problems: {g['problems']}",
        f"- fills: {g['summary']['fills']}",
        f"- net USD: {g['summary']['net']:,.2f}",
        f"- PF: {g['summary']['pf']}",
        f"- max DD: {g['summary']['max_dd_pct']}%", "",
    ]
    if not g["pass"]:
        lines += [
            "**BASELINE PROVENANCE MISMATCH / HOLD.**", "",
            "Per preregistration, no timing-model result is interpreted.", "",
        ]
        return "\n".join(lines)
    lines += [
        "## Current-identity population", "",
        f"- continuations: {result['population_diff']['current_continuations']}",
        f"- legacy-only rows: {len(result['population_diff']['legacy_only'])}",
        f"- current-only rows: {len(result['population_diff']['current_only'])}", "",
        "## Detachment", "",
        f"- all: {result['detachment']['all']}",
        f"- trigger-close context-approved: {result['detachment']['trigger_context_approved']}", "",
        "## Model A — completed-close IOC", "",
        f"{result['model_a']['summary']}", "",
        "## Model B — prior-bar context + pre-armed touch", "",
    ]
    for key, val in result["model_b"].items():
        lines.append(f"- {key}: {val['summary']}")
    lines += ["", "## Safety", "", "Audit only. No runtime/deployment authority."]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()

    mnq = args.data_root / "MNQ"
    observed_tree = tree_sha(mnq)
    if observed_tree != EXPECTED_TREE:
        raise SystemExit(f"corpus hash mismatch {observed_tree} != {EXPECTED_TREE}")
    bars = load_bars(args.data_root, "MNQ")
    legacy = structural_events(bars, legacy_trading_day)
    gate0_rows = run_model(legacy, bars, "A")
    gate0_summary = summary(gate0_rows)
    passed, problems = gate0_check(gate0_summary)
    result = {
        "prereg_commit": PREREG_COMMIT,
        "base_commit": BASE_COMMIT,
        "activation_commit": ACTIVATION_COMMIT,
        "corpus_tree_sha256": observed_tree,
        "corpus_files": len(list(mnq.glob("MNQ_*.jsonl"))),
        "gate0": {"pass": passed, "problems": problems, "summary": gate0_summary},
    }
    if passed:
        current = structural_events(bars, current_trading_day)
        a_rows = run_model(current, bars, "A")
        model_b = {}
        for slip in (1.0, 2.0, 3.0):
            b_rows = run_model(current, bars, "B", entry_slip_ticks=slip)
            model_b[f"{int(slip)}tick"] = {
                "summary": summary(b_rows),
                "rows": b_rows,
            }
        result.update(
            population_diff=population_diff(legacy, current),
            detachment=detachment(current, bars),
            model_a={"summary": summary(a_rows), "rows": a_rows},
            model_b=model_b,
        )
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2, default=str) + "\n")
    args.md_out.write_text(render_md(result))
    print(json.dumps({"gate0": result["gate0"], "models_run": passed}, indent=2))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
