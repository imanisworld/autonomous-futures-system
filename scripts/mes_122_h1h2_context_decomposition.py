#!/usr/bin/env python3
"""Describe why pure MES strat_122 H1 loses while H2 wins.

Evidence only. No strategy/runtime/config/deployment changes.

The study replays pure strat_122 at 1/2/3 adverse ticks with fixed one-contract
sizing, then enriches each resolved trade from the raw replay-corpus bar at the
same timestamp. It reports descriptive splits only; it does not auto-create or
recommend a filter.

Primary dimensions:
- chronological half (H1/H2)
- direction
- system session
- Eastern-time entry window
- market condition
- trend direction / trend strength
- strategy-direction vs trend alignment
- weekday / calendar year

Small cells are kept visible but flagged. A "stable" descriptive cell requires
n >= 5 at 1 tick and the same sign of commission-adjusted P&L across 1/2/3 tick
stress. Stable cells are hypotheses for later preregistered testing, not rules.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.mes_122_controlled_one_variable_tests import (  # noqa: E402
    COMMISSION_RT,
    STRATEGY,
    _fixed_one_contract_config,
    _json_lines,
    _metric_block,
    _run_pass,
    _variant_advance,
)
from scripts.mes_122_fallback_full_engine_proof import CORPUS, INSTRUMENT  # noqa: E402

SLIPS = (1.0, 2.0, 3.0)
ET = ZoneInfo("America/New_York")
DIMENSIONS = (
    "half",
    "direction",
    "session",
    "et_window",
    "market_condition",
    "trend_direction",
    "trend_strength",
    "trend_alignment",
    "weekday",
    "calendar_year",
)


def _parse_ts(value: str) -> datetime:
    text = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(text)


def _ts_key(value: str) -> str:
    dt = _parse_ts(value)
    return dt.isoformat()


def _corpus_index() -> dict[str, dict]:
    root = CORPUS / INSTRUMENT
    files = sorted(root.glob(f"{INSTRUMENT}_*.jsonl"))
    if not files:
        raise RuntimeError(
            f"no corpus files found in {root}; set AFS_122_CORPUS to the "
            "checkout containing replay_corpus_v1_market_condition_fixed"
        )
    out: dict[str, dict] = {}
    for path in files:
        for row in _json_lines(path):
            ts = row.get("timestamp") or row.get("bar_ts") or row.get("time")
            if ts:
                out[_ts_key(str(ts))] = row
    return out


def _pick(row: dict, *keys: str, default: str = "unknown") -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _normalize_dir(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"UP", "BULL", "BULLISH", "LONG"}:
        return "UP"
    if text in {"DOWN", "BEAR", "BEARISH", "SHORT"}:
        return "DOWN"
    if text in {"FLAT", "NEUTRAL", "SIDEWAYS", "NONE", ""}:
        return "NEUTRAL"
    return text


def _alignment(direction: str, trend_direction: str) -> str:
    trade = _normalize_dir(direction)
    trend = _normalize_dir(trend_direction)
    if trend == "NEUTRAL":
        return "neutral"
    if trade == "UP":
        trade = "LONG"
    elif trade == "DOWN":
        trade = "SHORT"
    if trend == "UP":
        trend = "LONG"
    elif trend == "DOWN":
        trend = "SHORT"
    if trade in {"LONG", "SHORT"} and trend in {"LONG", "SHORT"}:
        return "aligned" if trade == trend else "opposed"
    return "unknown"


def _et_window(ts: str) -> str:
    dt = _parse_ts(ts).astimezone(ET)
    mins = dt.hour * 60 + dt.minute
    if mins >= 18 * 60 or mins < 3 * 60:
        return "18:00-03:00"
    if mins < 9 * 60 + 30:
        return "03:00-09:30"
    if mins < 11 * 60:
        return "09:30-11:00"
    if mins < 13 * 60 + 30:
        return "11:00-13:30"
    if mins < 16 * 60:
        return "13:30-16:00"
    return "16:00-18:00"


def _resolved_rows(run: dict[str, Any], corpus: dict[str, dict]) -> list[dict]:
    rows: list[dict] = []
    for bar_ts, decision in sorted(run["decisions"].items()):
        if decision.get("decision") != "TRADE":
            continue
        setup = decision.get("setup") or {}
        if setup.get("strategy") != STRATEGY:
            continue
        order_id = decision.get("paper_order_id")
        outcome = run["outcomes"].get(str(order_id)) if order_id else None
        if not outcome or outcome.get("result") not in {"WIN", "LOSS", "BREAKEVEN"}:
            continue
        raw = corpus.get(_ts_key(bar_ts), {})
        direction = str(setup.get("direction") or "unknown")
        trend_direction = _pick(raw, "trend_direction", "trend", default="unknown")
        try:
            et_dt = _parse_ts(bar_ts).astimezone(ET)
            weekday = et_dt.strftime("%A")
            calendar_year = str(et_dt.year)
        except Exception:
            weekday = "unknown"
            calendar_year = bar_ts[:4]
        rows.append(
            {
                "bar_ts": bar_ts,
                "date": bar_ts[:10],
                "pnl_dollars": float(outcome.get("pnl_dollars") or 0.0),
                "result": outcome.get("result"),
                "direction": direction,
                "session": str(
                    setup.get("session")
                    or raw.get("session")
                    or raw.get("market_session")
                    or "unknown"
                ),
                "et_window": _et_window(bar_ts),
                "market_condition": _pick(
                    raw, "market_condition", "market_state", default="unknown"
                ),
                "trend_direction": trend_direction,
                "trend_strength": _pick(
                    raw, "trend_strength", "trend_strength_label", default="unknown"
                ),
                "trend_alignment": _alignment(direction, trend_direction),
                "weekday": weekday,
                "calendar_year": calendar_year,
                "raw_context": {
                    key: raw.get(key)
                    for key in (
                        "market_condition",
                        "trend_direction",
                        "trend_strength",
                        "session",
                        "market_session",
                        "price_vs_vwap",
                        "vwap",
                        "ema20",
                    )
                    if key in raw
                },
            }
        )
    half = len(rows) // 2
    for i, row in enumerate(rows):
        row["half"] = "H1" if i < half else "H2"
        row["sequence_index"] = i + 1
    return rows


def _group_metrics(rows: list[dict], dimension: str) -> dict[str, dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        groups[str(row.get(dimension, "unknown"))].append(row)
    return {
        key: _metric_block(group)
        for key, group in sorted(groups.items(), key=lambda kv: kv[0])
    }


def _half_mix(rows: list[dict], dimension: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    categories = sorted({str(r.get(dimension, "unknown")) for r in rows})
    for category in categories:
        item: dict[str, Any] = {}
        for half in ("H1", "H2"):
            group = [
                r
                for r in rows
                if r["half"] == half and str(r.get(dimension, "unknown")) == category
            ]
            item[half] = _metric_block(group) if group else {
                "trades": 0,
                "commission_adjusted_net": 0.0,
                "commission_adjusted_pf": None,
            }
        result[category] = item
    return result


def _stable_cells(by_slip: dict[str, dict]) -> dict[str, list[dict]]:
    positive: list[dict] = []
    negative: list[dict] = []
    for dimension in DIMENSIONS[1:]:  # half itself is not a prospective context filter
        categories = set()
        for key in ("slip_1t", "slip_2t", "slip_3t"):
            categories.update(by_slip[key]["groups"][dimension])
        for category in sorted(categories):
            cells = [
                by_slip[key]["groups"][dimension].get(category)
                for key in ("slip_1t", "slip_2t", "slip_3t")
            ]
            if any(cell is None for cell in cells):
                continue
            assert all(cell is not None for cell in cells)
            n = int(cells[0]["trades"])
            if n < 5 or any(int(cell["trades"]) != n for cell in cells):
                continue
            nets = [float(cell["commission_adjusted_net"]) for cell in cells]
            item = {
                "dimension": dimension,
                "category": category,
                "n": n,
                "net_1t": round(nets[0], 2),
                "net_2t": round(nets[1], 2),
                "net_3t": round(nets[2], 2),
            }
            if all(net > 0 for net in nets):
                positive.append(item)
            elif all(net < 0 for net in nets):
                negative.append(item)
    positive.sort(key=lambda x: (x["n"], x["net_3t"]), reverse=True)
    negative.sort(key=lambda x: (x["n"], -x["net_3t"]), reverse=True)
    return {"stable_positive": positive, "stable_negative": negative}


def _top_context(groups: dict[str, dict], *, limit: int = 4) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for dimension in DIMENSIONS[1:]:
        cells = [
            {
                "category": category,
                "n": int(metrics["trades"]),
                "net": float(metrics["commission_adjusted_net"]),
                "pf": metrics["commission_adjusted_pf"],
            }
            for category, metrics in groups[dimension].items()
        ]
        cells.sort(key=lambda x: x["net"], reverse=True)
        out[dimension] = cells[:limit] + list(reversed(cells[-limit:])) if len(cells) > limit else cells
    return out


def run(out_path: Path) -> dict[str, Any]:
    corpus = _corpus_index()
    base = _fixed_one_contract_config(isolated=True)
    base = dataclasses.replace(base, enabled_concepts=[STRATEGY])

    result: dict[str, Any] = {
        "design": {
            "instrument": INSTRUMENT,
            "strategy": STRATEGY,
            "enabled_concepts": [STRATEGY],
            "contracts": 1,
            "slippage_ticks": list(SLIPS),
            "commission_round_trip": COMMISSION_RT,
            "strategy_parameters_changed": False,
            "analysis_only": True,
            "minimum_stable_cell_n": 5,
        },
        "slippage": {},
    }

    rows_by_slip: dict[str, list[dict]] = {}
    with tempfile.TemporaryDirectory(prefix="mes122_h1h2_ctx_") as tmp:
        root = Path(tmp)
        for slip in SLIPS:
            key = f"slip_{int(slip)}t"
            print(f"[{key}] pure strat_122 context decomposition", flush=True)
            cfg = dataclasses.replace(base, fill_slippage_ticks=slip)
            run_data = _run_pass(
                cfg,
                root / key,
                advance_fn=_variant_advance("baseline"),
            )
            rows = _resolved_rows(run_data, corpus)
            rows_by_slip[key] = rows
            result["slippage"][key] = {
                "overall": _metric_block(rows),
                "groups": {dim: _group_metrics(rows, dim) for dim in DIMENSIONS},
                "half_mix": {
                    dim: _half_mix(rows, dim)
                    for dim in DIMENSIONS[1:]
                },
                "unmatched_corpus_rows": [r["bar_ts"] for r in rows if not r["raw_context"]],
                "trade_rows": rows,
            }

    result["stable_cells"] = _stable_cells(result["slippage"])
    result["one_tick_context_summary"] = _top_context(
        result["slippage"]["slip_1t"]["groups"]
    )

    h1 = result["slippage"]["slip_1t"]["groups"]["half"].get("H1", {})
    h2 = result["slippage"]["slip_1t"]["groups"]["half"].get("H2", {})
    result["headline"] = {
        "h1_trades": h1.get("trades"),
        "h1_net": h1.get("commission_adjusted_net"),
        "h2_trades": h2.get("trades"),
        "h2_net": h2.get("commission_adjusted_net"),
        "descriptive_only": True,
        "instruction": "Do not convert a context cell into a strategy filter without a separate preregistered replay test.",
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8")

    console = {
        "headline": result["headline"],
        "overall": {
            key: result["slippage"][key]["overall"]
            for key in ("slip_1t", "slip_2t", "slip_3t")
        },
        "stable_cells": result["stable_cells"],
        "one_tick_context_summary": result["one_tick_context_summary"],
        "one_tick_half_mix": result["slippage"]["slip_1t"]["half_mix"],
    }
    print(json.dumps(console, indent=2))
    print(f"wrote {out_path}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts" / "mes_122_h1h2_context_decomposition_2026-09-08.json",
    )
    args = parser.parse_args()
    run(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
