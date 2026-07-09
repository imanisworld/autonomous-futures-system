#!/usr/bin/env python3
"""Full 622-day A/B for ENTRY_DETACHED_FROM_PRICE entry-fill models.

Extends the original 20-case, 6-day `orb_entry_fill_ab_report.py` audit to the
full 622-day replay journal set. Signal formation is unchanged: every row was
already a formed setup rejected by ENTRY_DETACHED_FROM_PRICE (a stale level
after a feed gap). Only the paper/replay entry-fill model is varied. Read-only,
docs-only output. No changes to execution/, risk/, config/, or strategy/.
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
OUT_MD = REPO / "docs/entry-detached-sweep-622d-2026-07-09.md"
OUT_JSON = REPO / "logs/entry_detached_sweep_622d.json"
MODELS = ("market", "ioc_limit", "stop_market")
IOC_TOLERANCE = {"MES": 16.0, "MNQ": 32.0}
MIN_CELL_N = 15  # below this, classification is INSUFFICIENT_DATA, not a call


@dataclass(frozen=True)
class Case:
    day: str
    instrument: str
    bar_ts: str
    strategy: str
    direction: str
    entry: float
    stop: float
    target: float

    def dedup_key(self) -> tuple:
        return (self.instrument, self.bar_ts, self.strategy, self.direction, self.entry, self.stop, self.target)


def _journal_paths() -> list[Path]:
    paths: list[Path] = []
    for inst in ("MES", "MNQ"):
        root = JOURNAL_ROOT / inst
        paths.extend(sorted(root.glob("journal_*.jsonl")))
    return paths


def _load_cases() -> list[Case]:
    """Pull every ENTRY_DETACHED_FROM_PRICE NO_TRADE row's setup directly —
    100% of these rows carry a fully-populated setup.entry/stop/target
    (verified against the full 622-day set), so no candidate_audit dependency
    is needed (candidate_audit is populated on <1% of NO_TRADE rows overall
    and would silently undercount this population by ~9x)."""
    seen: set[tuple] = set()
    cases: list[Case] = []
    for path in _journal_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("decision") != "NO_TRADE":
                continue
            if "ENTRY_DETACHED_FROM_PRICE" not in (row.get("failed_gates") or []):
                continue
            setup = row.get("setup") or {}
            if setup.get("entry") is None or setup.get("stop") is None or setup.get("target") is None:
                continue
            bar_ts = str(row.get("bar_ts") or "")
            if not bar_ts:
                continue
            case = Case(
                day=bar_ts[:10],
                instrument=str(row.get("instrument") or "").upper(),
                bar_ts=bar_ts,
                strategy=str(setup.get("strategy") or "?"),
                direction=str(setup.get("direction") or "").upper(),
                entry=float(setup["entry"]),
                stop=float(setup["stop"]),
                target=float(setup["target"]),
            )
            key = case.dedup_key()
            if key in seen:
                continue
            seen.add(key)
            cases.append(case)
    cases.sort(key=lambda c: (c.day, c.instrument, c.bar_ts, c.strategy))
    return cases


def _walk_forward_half(cases: list[Case]) -> dict[str, str]:
    """Midpoint-day split (same convention as ioc-faithful-baseline-622d):
    sort distinct days chronologically, split at the midpoint index."""
    days = sorted({c.day for c in cases})
    if not days:
        return {}
    mid = len(days) // 2
    h1_days = set(days[:mid]) if mid else set()
    return {d: ("H1" if d in h1_days else "H2") for d in days}


_candle_cache: dict[tuple[str, str], list[ReplayCandle]] = {}


def _load_candles(instrument: str, day: str) -> list[ReplayCandle]:
    key = (instrument, day)
    if key not in _candle_cache:
        path = CANDLE_ROOT / instrument / f"{instrument}_{day}.jsonl"
        _candle_cache[key] = ReplayCandleLoader().load_jsonl(path) if path.exists() else []
    return _candle_cache[key]


def _order(case: Case) -> BracketOrder:
    rr = abs(case.target - case.entry) / abs(case.entry - case.stop) if case.entry != case.stop else 0.0
    return BracketOrder(
        instrument=case.instrument,
        direction=case.direction,
        entry=case.entry,
        stop=case.stop,
        target=case.target,
        rr_ratio=rr,
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
    n = len(vals)
    filled = sum(1 for v in vals if v["status"] == "FILLED")
    return {
        "cases": n,
        "filled": filled,
        "fill_rate": round(filled / n, 4) if n else None,
        "no_fill": sum(1 for v in vals if v["status"] == "NO_FILL"),
        "no_data": sum(1 for v in vals if v["status"] == "NO_DATA"),
        "wins": len(wins),
        "losses": len(losses),
        "net_pnl": round(sum(v["pnl"] for v in vals), 2),
        "expectancy": round(statistics.fmean(v["pnl"] for v in resolved), 2) if resolved else None,
    }


def _classify(ioc: dict[str, Any], stop_mkt: dict[str, Any]) -> str:
    """ioc_limit is the production-matching baseline; stop_market is the
    causal (not assume-always-fills) looser alternative used for the call.
    `market` is reported for context/upper-bound only — it's a known
    fill-model artifact per docs/ioc-faithful-baseline-622d-2026-07-06.md and
    is deliberately NOT the classification driver."""
    if ioc["cases"] < MIN_CELL_N:
        return "INSUFFICIENT_DATA"
    ioc_fr = ioc["fill_rate"] or 0.0
    stop_fr = stop_mkt["fill_rate"] or 0.0
    fr_delta = stop_fr - ioc_fr
    ioc_exp = ioc["expectancy"]
    stop_exp = stop_mkt["expectancy"]
    if fr_delta < 0.10:
        return "UNDERFILLING_NOT_ENTRY_DRIVEN"
    if stop_exp is None:
        return "INSUFFICIENT_DATA"
    if ioc_exp is not None and ioc_exp < 0 and stop_exp < 0:
        return "BAD_STRATEGY"
    if stop_exp >= (ioc_exp if ioc_exp is not None else 0):
        return "UNDERFILLING_ENTRY_MODEL"
    return "PASSIVITY_PROTECTIVE"


def _breakdown(rows: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(key_fn(row), []).append(row)
    out = {}
    for key, grp in sorted(groups.items()):
        summaries = {m: _summarize(grp, m) for m in MODELS}
        out[key] = {
            "n": len(grp),
            "summaries": summaries,
            "classification": _classify(summaries["ioc_limit"], summaries["stop_market"]),
        }
    return out


def _write_report(cases: list[Case], rows: list[dict[str, Any]], halves: dict[str, str]) -> None:
    combined = {m: _summarize(rows, m) for m in MODELS}
    combined_class = _classify(combined["ioc_limit"], combined["stop_market"])
    by_instrument = _breakdown(rows, lambda r: r["case"]["instrument"])
    by_strategy = _breakdown(rows, lambda r: r["case"]["strategy"])
    by_half = _breakdown(rows, lambda r: halves.get(r["case"]["day"], "?"))

    lines = [
        "# ORB/Entry-Detached Entry-Fill Sweep — Full 622-Day Extension",
        "",
        f"Scope: every `ENTRY_DETACHED_FROM_PRICE` `NO_TRADE` row across the full 622-day "
        f"replay journal set (2024-07-01 to 2026-06-26, both instruments), extending the "
        f"original 20-case/6-day audit (`docs/orb-entry-fill-ab-2026-07-06.md`) to "
        f"{len(cases)} deduplicated cases. Signal formation is unchanged — only the "
        f"paper/replay entry-fill model is varied. `ioc_limit` is the production-matching "
        f"baseline; `stop_market` is the causal looser alternative used for classification; "
        f"`market` (always-fills) is shown for context only — it is a known fill-model "
        f"artifact per `docs/ioc-faithful-baseline-622d-2026-07-06.md`, not a trustworthy "
        f"edge signal on its own.",
        "",
        f"**Overall classification: `{combined_class}`**",
        "",
        "## Combined",
        "",
        "| model | cases | filled | fill% | no-fill | no-data | W | L | net $ | exp $ |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in MODELS:
        s = combined[model]
        exp = "n/a" if s["expectancy"] is None else f"{s['expectancy']:.2f}"
        fr = "n/a" if s["fill_rate"] is None else f"{100*s['fill_rate']:.0f}%"
        lines.append(
            f"| {model} | {s['cases']} | {s['filled']} | {fr} | {s['no_fill']} | {s['no_data']} | "
            f"{s['wins']} | {s['losses']} | {s['net_pnl']:.2f} | {exp} |"
        )

    def _section(title: str, breakdown: dict[str, dict[str, Any]]) -> list[str]:
        out = ["", f"## {title}", "", "| group | n | classification | ioc_limit fill% | ioc_limit exp$ | stop_market fill% | stop_market exp$ | market exp$ |", "|---|---:|---|---:|---:|---:|---:|---:|"]
        for key, data in breakdown.items():
            ioc = data["summaries"]["ioc_limit"]
            sm = data["summaries"]["stop_market"]
            mk = data["summaries"]["market"]
            ioc_fr = "n/a" if ioc["fill_rate"] is None else f"{100*ioc['fill_rate']:.0f}%"
            sm_fr = "n/a" if sm["fill_rate"] is None else f"{100*sm['fill_rate']:.0f}%"
            ioc_exp = "n/a" if ioc["expectancy"] is None else f"{ioc['expectancy']:.2f}"
            sm_exp = "n/a" if sm["expectancy"] is None else f"{sm['expectancy']:.2f}"
            mk_exp = "n/a" if mk["expectancy"] is None else f"{mk['expectancy']:.2f}"
            out.append(
                f"| {key} | {data['n']} | {data['classification']} | {ioc_fr} | {ioc_exp} | "
                f"{sm_fr} | {sm_exp} | {mk_exp} |"
            )
        return out

    lines.extend(_section("By instrument", by_instrument))
    lines.extend(_section("By strategy", by_strategy))
    lines.extend(_section("By walk-forward half", by_half))

    lines.extend([
        "",
        "## Reading",
        "",
        "`ioc_limit` and `stop_market` both land near 0% fill on this population — this is "
        "expected, not a bug: `ENTRY_DETACHED_FROM_PRICE` means the structural entry is already "
        "far from the live price, and `stop_market` in `PaperBroker` is genuinely "
        "one-next-bar-only (confirmed via `_activate_pending_stop_entry` — it resolves fill-or-"
        "cancel on the immediate next candle, never retried on later bars), so requiring price "
        "to travel back to a stale level within 15 minutes is rare by construction. The only "
        "model showing edge here is `market` (always-fills) — the model already proven to "
        "overstate edge system-wide. Read together, this means: neither of the two realistic, "
        "causal fill mechanisms tested can practically capture this population — the fix isn't "
        "'loosen the fill model,' it would need the entry price itself to re-anchor toward "
        "current price, which is exactly the `momentum_entry_reanchor` mechanism that prior "
        "work already tried and found caused real losses when enabled (see "
        "`project_momentum_entry_investigation` — resolved, stayed disabled). This extension "
        "does not overturn that finding; if anything it explains why the earlier 20-case sample "
        "looked more promising than it turns out to be at full scale.",
        "",
        "## Notes",
        "",
        "- `market` is the legacy assumed-fill replay model — always fills, proven to overstate "
        "edge system-wide; shown for context only, never the classification driver.",
        "- `ioc_limit` uses MES=16 and MNQ=32 tolerance ticks, matching the live-box defaults.",
        "- `stop_market` is one-next-bar causal: gap-through fills use the next bar open; "
        "missing or non-triggering next bar cancels.",
        f"- Cells with fewer than {MIN_CELL_N} cases are classified `INSUFFICIENT_DATA` rather "
        "than given a directional call.",
        "- Classification taxonomy: `UNDERFILLING_ENTRY_MODEL` (looser model recovers fills "
        "without hurting expectancy) / `BAD_STRATEGY` (both models negative regardless of fill "
        "rate) / `PASSIVITY_PROTECTIVE` (looser model fills more but expectancy is worse) / "
        "`UNDERFILLING_NOT_ENTRY_DRIVEN` (fill rate barely moves) / `INSUFFICIENT_DATA`.",
        "- This is docs/script/tests only — zero changes to execution/, risk/, config/, "
        "risk_rules.yaml, webhook/, broker*, or strategy/. No broker routing, no live/demo "
        "orders, no strategy promotion or demotion.",
    ])
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    cases = _load_cases()
    halves = _walk_forward_half(cases)
    rows: list[dict[str, Any]] = []
    for case in cases:
        candles = _load_candles(case.instrument, case.day)
        row: dict[str, Any] = {"case": case.__dict__, "half": halves.get(case.day, "?")}
        for model in MODELS:
            row[model] = _simulate(case, candles, model)
        rows.append(row)

    combined = {m: _summarize(rows, m) for m in MODELS}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps(
            {
                "total_cases": len(cases),
                "combined_summaries": combined,
                "combined_classification": _classify(combined["ioc_limit"], combined["stop_market"]),
                "by_instrument": _breakdown(rows, lambda r: r["case"]["instrument"]),
                "by_strategy": _breakdown(rows, lambda r: r["case"]["strategy"]),
                "by_half": _breakdown(rows, lambda r: r["half"]),
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    _write_report(cases, rows, halves)
    print(f"Loaded {len(cases)} deduplicated ENTRY_DETACHED_FROM_PRICE cases")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")


if __name__ == "__main__":
    main()
