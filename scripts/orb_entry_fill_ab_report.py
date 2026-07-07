#!/usr/bin/env python3
"""Compact A/B report for ORB ENTRY_DETACHED_FROM_PRICE entry-fill models.

The report intentionally reuses stored replay journal decisions so signal
formation is unchanged: every row was already a formed ORB setup rejected by
ENTRY_DETACHED_FROM_PRICE. Only the paper/replay entry-fill model is varied.
"""
from __future__ import annotations

import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from replay.candle_loader import ReplayCandle, ReplayCandleLoader


JOURNAL_ROOT = REPO / "logs/replay_622d_market_static"
CANDLE_ROOT = REPO / "data/replay_polygon"
OUT_MD = REPO / "docs/orb-entry-fill-ab-2026-07-06.md"
OUT_JSON = REPO / "logs/orb_entry_fill_ab_2026-07-06.json"
DATES = {"2026-06-24", "2026-06-25", "2026-06-26", "2026-06-29", "2026-06-30", "2026-07-01"}
ORB = {"orb_breakout", "orb_reclaim", "orb_rejection"}
IOC_TOLERANCE = {"MES": 16.0, "MNQ": 32.0}


@dataclass
class Case:
    day: str
    instrument: str
    bar_ts: str
    strategy: str
    direction: str
    entry: float
    stop: float
    target: float
    close: float | None


def _journal_paths() -> list[Path]:
    paths: list[Path] = []
    for inst in ("MES", "MNQ"):
        root = JOURNAL_ROOT / inst
        for day in sorted(DATES):
            path = root / f"journal_{day}.jsonl"
            if path.exists():
                paths.append(path)
    return paths


def _load_cases() -> list[Case]:
    cases: list[Case] = []
    for path in _journal_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            setup = row.get("setup") or {}
            gates = row.get("failed_gates") or []
            strategy = setup.get("strategy")
            if "ENTRY_DETACHED_FROM_PRICE" not in gates or strategy not in ORB:
                continue
            bar_ts = str(row.get("bar_ts") or "")
            if not bar_ts:
                continue
            cases.append(
                Case(
                    day=bar_ts[:10],
                    instrument=str(row.get("instrument") or "").upper(),
                    bar_ts=bar_ts,
                    strategy=str(strategy),
                    direction=str(setup["direction"]).upper(),
                    entry=float(setup["entry"]),
                    stop=float(setup["stop"]),
                    target=float(setup["target"]),
                    close=None,
                )
            )
    cases.sort(key=lambda c: (c.day, c.instrument, c.bar_ts, c.strategy))
    return cases


def _load_candles(instrument: str, day: str) -> list[ReplayCandle]:
    path = CANDLE_ROOT / instrument / f"{instrument}_{day}.jsonl"
    if not path.exists():
        return []
    return ReplayCandleLoader().load_jsonl(path)


def _order(case: Case, entry: float | None = None) -> BracketOrder:
    return BracketOrder(
        instrument=case.instrument,
        direction=case.direction,
        entry=case.entry if entry is None else entry,
        stop=case.stop,
        target=case.target,
        rr_ratio=2.0,
        strategy=case.strategy,
        contracts=1,
    )


def _simulate(case: Case, candles: list[ReplayCandle], model: str) -> dict[str, Any]:
    by_ts = {c.timestamp: i for i, c in enumerate(candles)}
    idx = by_ts.get(case.bar_ts)
    if idx is None:
        return {"status": "NO_DATA", "result": "NO_DATA", "pnl": 0.0, "entry_price": None}

    decision_bar = candles[idx]
    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=1.0,
        pessimistic_both_hit=True,
        entry_fill_model=model,
        entry_tolerance_ticks_by_root=IOC_TOLERANCE,
    )
    try:
        fill = broker.execute_bracket(_order(case), market_price=decision_bar.close)
    except ValueError as exc:
        return {"status": "ERROR", "result": "ERROR", "pnl": 0.0, "reason": str(exc)}
    if fill.result == "CANCELLED":
        return {
            "status": "NO_FILL",
            "result": fill.result,
            "reason": fill.exit_reason,
            "pnl": 0.0,
            "entry_price": fill.entry_price,
        }

    resolved = None
    for fc in candles[idx + 1:]:
        resolved = broker.resolve_position(NextBarOHLC(open=fc.open, high=fc.high, low=fc.low))
        if resolved is not None:
            break
    if resolved is None and broker.has_pending_entry():
        resolved = broker.cancel_pending_entry("ENTRY_NO_NEXT_BAR")
    if resolved is None:
        return {
            "status": "OPEN",
            "result": "OPEN",
            "pnl": 0.0,
            "entry_price": broker.get_position().entry_price if broker.get_position() else None,
        }
    status = "NO_FILL" if resolved.result == "CANCELLED" else "FILLED"
    return {
        "status": status,
        "result": resolved.result,
        "reason": resolved.exit_reason,
        "pnl": float(resolved.pnl_dollars or 0.0),
        "entry_price": resolved.entry_price,
    }


def _summarize(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    vals = [r[model] for r in rows]
    resolved = [v for v in vals if v["result"] in {"WIN", "LOSS", "BREAKEVEN"}]
    wins = [v["pnl"] for v in resolved if v["result"] == "WIN"]
    losses = [v["pnl"] for v in resolved if v["result"] == "LOSS"]
    return {
        "cases": len(vals),
        "filled": sum(1 for v in vals if v["status"] == "FILLED"),
        "no_fill": sum(1 for v in vals if v["status"] == "NO_FILL"),
        "no_data": sum(1 for v in vals if v["status"] == "NO_DATA"),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(sum(v["pnl"] for v in vals), 2),
        "expectancy": round(statistics.fmean(v["pnl"] for v in resolved), 2) if resolved else None,
    }


def _write_report(rows: list[dict[str, Any]], summaries: dict[str, dict[str, Any]]) -> None:
    lines = [
        "# ORB Entry-Fill A/B — Workstream A Phase 1",
        "",
        "Scope: stored ORB formed setups rejected by `ENTRY_DETACHED_FROM_PRICE` in the June 24 to July 1 audit window, replayed with signal formation unchanged. Local replay candles are available through 2026-06-26, so later audit/thread cases are identified as out of local replay coverage rather than inferred.",
        "",
        "| model | cases | filled | no-fill | no-data | W | L | net $ | exp $ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in ("market", "ioc_limit", "stop_market"):
        s = summaries[model]
        exp = "n/a" if s["expectancy"] is None else f"{s['expectancy']:.2f}"
        lines.append(
            f"| {model} | {s['cases']} | {s['filled']} | {s['no_fill']} | {s['no_data']} | "
            f"{s['wins']} | {s['losses']} | {s['net_pnl']:.2f} | {exp} |"
        )
    lines.extend([
        "",
        "## Cases",
        "",
        "| ts | inst | setup | market | ioc_limit | stop_market |",
        "|---|---|---|---|---|---|",
    ])
    for row in rows:
        case = row["case"]
        cells = []
        for model in ("market", "ioc_limit", "stop_market"):
            v = row[model]
            reason = f"/{v['reason']}" if v.get("reason") and v["result"] == "CANCELLED" else ""
            cells.append(f"{v['result']}{reason} {v['pnl']:+.2f}")
        lines.append(
            f"| {case['bar_ts']} | {case['instrument']} | {case['strategy']} {case['direction']} @ {case['entry']} | "
            f"{cells[0]} | {cells[1]} | {cells[2]} |"
        )
    lines.extend([
        "",
        "Notes:",
        "- `market` is the legacy assumed-fill replay model.",
        "- `ioc_limit` uses MES=16 and MNQ=32 tolerance ticks, matching the live-box defaults.",
        "- `stop_market` is one-next-bar causal: gap-through fills use the next bar open; missing or non-triggering next bar cancels.",
        "- Coverage caveat: the June 29 to July 1 audit rows and the July 2 missed-items thread are part of the review scope, but this repo snapshot only has local ORB detached replay journals/candles through June 26. Those later rows are therefore not inferred into the A/B totals.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    cache: dict[tuple[str, str], list[ReplayCandle]] = {}
    rows: list[dict[str, Any]] = []
    for case in _load_cases():
        candles = cache.setdefault((case.instrument, case.day), _load_candles(case.instrument, case.day))
        row = {"case": case.__dict__}
        for model in ("market", "ioc_limit", "stop_market"):
            row[model] = _simulate(case, candles, model)
        rows.append(row)
    summaries = {model: _summarize(rows, model) for model in ("market", "ioc_limit", "stop_market")}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"summaries": summaries, "rows": rows}, indent=2), encoding="utf-8")
    _write_report(rows, summaries)
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
