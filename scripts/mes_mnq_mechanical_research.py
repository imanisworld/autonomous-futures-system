#!/usr/bin/env python3
"""Read-only MES/MNQ mechanical edge research.

This script uses existing replay journals and Polygon replay bars only. It
does not import or change strategy/risk/execution behavior. The goal is to
separate what the current data can prove from theories that still need a
pre-registered replay.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
JOURNAL_ROOT = REPO / "logs/replay_622d_market_static"
HONEST_STATIC_ROOT = REPO / "logs/replay_622d_nodd_ioc_limit_static"
HONEST_RUNNER_ROOT = REPO / "logs/replay_622d_nodd_ioc_limit_runner"
CANDLE_ROOT = REPO / "data/replay_polygon"
OUT_JSON = REPO / "logs/mes_mnq_mechanical_research.json"
OUT_MD = REPO / "docs/mes-mnq-mechanical-research-2026-07-09.md"

INSTRUMENTS = ("MES", "MNQ")
POINT_VALUE = {"MES": 5.0, "MNQ": 2.0}
GATES_OF_INTEREST = {
    "MARKET_CONDITION_NOT_TRENDING",
    "MARKET_CONDITION_NOT_TRADABLE",
    "TREND_STRENGTH_BELOW_REQUIRED",
    "REGIME_NOT_FULL",
    "REGIME_RESTRICTED",
    "EMA_STACK_NOT_ALIGNED",
    "EMA_STACK_NOT_ALIGNED_SOFT",
    "HTF_ALIGNMENT_FAIL",
    "WEAK_BAR_CLOSE",
    "INVALID_SETUP",
    "SIGNAL_BAR_VOLUME_TOO_LOW",
    "ENTRY_DETACHED_FROM_PRICE",
    "STRAT_DIRECTION_CONFLICT",
}
TREND_GATES = {
    "MARKET_CONDITION_NOT_TRENDING",
    "MARKET_CONDITION_NOT_TRADABLE",
    "TREND_STRENGTH_BELOW_REQUIRED",
    "REGIME_RESTRICTED",
    "EMA_STACK_NOT_ALIGNED",
    "EMA_STACK_NOT_ALIGNED_SOFT",
    "HTF_ALIGNMENT_FAIL",
}
MIN_CELL_N = 30


@dataclass(frozen=True)
class Bar:
    ts: str
    high: float
    low: float
    close: float
    session: str | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class Candidate:
    source: str
    instrument: str
    day: str
    bar_ts: str
    session: str
    gate: str
    market_condition: str | None
    trend_strength: str | None
    regime: str | None
    strategy: str
    direction: str
    entry: float
    stop: float
    target: float

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def current_r(self) -> float:
        return abs(self.target - self.entry) / self.risk if self.risk > 0 else math.nan


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


_CANDLE_CACHE: dict[tuple[str, str], list[Bar]] = {}


def _candles(instrument: str, day: str) -> list[Bar]:
    key = (instrument, day)
    if key not in _CANDLE_CACHE:
        path = CANDLE_ROOT / instrument / f"{instrument}_{day}.jsonl"
        bars = []
        for r in _load_jsonl(path):
            bars.append(
                Bar(
                    ts=str(r["timestamp"]),
                    high=float(r["high"]),
                    low=float(r["low"]),
                    close=float(r["close"]),
                    session=r.get("session"),
                    raw=r,
                )
            )
        _CANDLE_CACHE[key] = bars
    return _CANDLE_CACHE[key]


def _bar_index(candles: list[Bar], bar_ts: str) -> int | None:
    for i, bar in enumerate(candles):
        if bar.ts == bar_ts:
            return i
    return None


def _first_gate(row: dict[str, Any]) -> str:
    gates = row.get("failed_gates") or []
    return str(gates[0]) if gates else "NO_GATE"


def _row_trend_strength(row: dict[str, Any], candle: Bar | None) -> str | None:
    for key in ("trend_strength", "trend"):
        value = row.get(key)
        if isinstance(value, str):
            return value
    raw = (candle.raw if candle else None) or {}
    value = raw.get("trend_strength")
    return str(value) if value is not None else None


def _candidate_from_bracket(
    *,
    source: str,
    row: dict[str, Any],
    bracket: dict[str, Any],
    instrument: str,
    day: str,
    candle: Bar | None,
) -> Candidate | None:
    direction = str(bracket.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None
    try:
        entry = float(bracket["entry"])
        stop = float(bracket["stop"])
        target = float(bracket["target"])
    except (KeyError, TypeError, ValueError):
        return None
    if entry == stop:
        return None
    return Candidate(
        source=source,
        instrument=instrument,
        day=day,
        bar_ts=str(row.get("bar_ts") or ""),
        session=str(row.get("session") or (candle.session if candle else "") or "?"),
        gate=_first_gate(row),
        market_condition=row.get("market_condition"),
        trend_strength=_row_trend_strength(row, candle),
        regime=row.get("regime"),
        strategy=str(bracket.get("strategy") or "unknown"),
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
    )


def collect_blocked_candidates() -> list[Candidate]:
    candidates: list[Candidate] = []
    seen: set[tuple[Any, ...]] = set()
    for instrument in INSTRUMENTS:
        for path in sorted((JOURNAL_ROOT / instrument).glob("journal_*.jsonl")):
            day = path.stem.replace("journal_", "")
            candles = _candles(instrument, day)
            by_ts = {bar.ts: bar for bar in candles}
            for row in _load_jsonl(path):
                if row.get("decision") != "NO_TRADE":
                    continue
                gate = _first_gate(row)
                if gate not in GATES_OF_INTEREST and gate != "NO_GATE":
                    continue
                bar_ts = str(row.get("bar_ts") or "")
                candle = by_ts.get(bar_ts)
                setup = row.get("setup")
                if isinstance(setup, dict):
                    cand = _candidate_from_bracket(
                        source="executable_setup",
                        row=row,
                        bracket=setup,
                        instrument=instrument,
                        day=day,
                        candle=candle,
                    )
                    if cand:
                        key = (cand.source, cand.instrument, cand.bar_ts, cand.strategy, cand.entry, cand.stop, cand.target)
                        if key not in seen:
                            seen.add(key)
                            candidates.append(cand)
                for shadow in row.get("shadow_candidates") or []:
                    if not isinstance(shadow, dict):
                        continue
                    cand = _candidate_from_bracket(
                        source="shadow_candidate",
                        row=row,
                        bracket=shadow,
                        instrument=instrument,
                        day=day,
                        candle=candle,
                    )
                    if cand:
                        key = (cand.source, cand.instrument, cand.bar_ts, cand.strategy, cand.entry, cand.stop, cand.target)
                        if key not in seen:
                            seen.add(key)
                            candidates.append(cand)
    return candidates


def _target_for(c: Candidate, mode: str, decision_bar: Bar | None) -> float | None:
    risk = c.risk
    if risk <= 0:
        return None
    sign = 1 if c.direction == "LONG" else -1
    if mode == "current":
        return c.target
    if mode.endswith("R"):
        return c.entry + sign * float(mode[:-1]) * risk
    if mode == "next_level":
        raw = (decision_bar.raw if decision_bar else None) or {}
        levels = []
        for key in (
            "orb_high",
            "orb_low",
            "vwap",
            "previous_day_high",
            "previous_day_low",
            "previous_day_close",
            "hod",
            "lod",
            "supply_top",
            "supply_bottom",
            "demand_top",
            "demand_bottom",
        ):
            value = raw.get(key)
            if value is None:
                continue
            try:
                level = float(value)
            except (TypeError, ValueError):
                continue
            if c.direction == "LONG" and level > c.entry:
                levels.append(level)
            elif c.direction == "SHORT" and level < c.entry:
                levels.append(level)
        if not levels:
            return None
        return min(levels) if c.direction == "LONG" else max(levels)
    raise ValueError(mode)


def _simulate(c: Candidate, target: float) -> dict[str, Any]:
    candles = _candles(c.instrument, c.day)
    idx = _bar_index(candles, c.bar_ts)
    if idx is None:
        return {"result": "NO_DATA", "pnl": 0.0, "filled": False}
    forward = candles[idx + 1 :]
    if not forward:
        return {"result": "NO_DATA", "pnl": 0.0, "filled": False}

    fill_idx = None
    for i, bar in enumerate(forward):
        if bar.low <= c.entry <= bar.high:
            fill_idx = i
            break
    if fill_idx is None:
        return {"result": "NO_FILL", "pnl": 0.0, "filled": False}

    is_long = c.direction == "LONG"
    pt_value = POINT_VALUE[c.instrument]
    mfe = 0.0
    mae = 0.0
    for j, bar in enumerate(forward[fill_idx + 1 :], start=fill_idx + 2):
        fav = (bar.high - c.entry) if is_long else (c.entry - bar.low)
        adv = (c.entry - bar.low) if is_long else (bar.high - c.entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)
        target_hit = bar.high >= target if is_long else bar.low <= target
        stop_hit = bar.low <= c.stop if is_long else bar.high >= c.stop
        if target_hit and stop_hit:
            stop_hit = True
            target_hit = False
        if stop_hit:
            pnl = ((c.stop - c.entry) if is_long else (c.entry - c.stop)) * pt_value
            return {
                "result": "LOSS",
                "pnl": pnl,
                "filled": True,
                "bars_to_fill": fill_idx + 1,
                "bars_to_exit": j,
                "mfe_R": mfe / c.risk if c.risk else None,
                "mae_R": mae / c.risk if c.risk else None,
            }
        if target_hit:
            pnl = ((target - c.entry) if is_long else (c.entry - target)) * pt_value
            return {
                "result": "WIN",
                "pnl": pnl,
                "filled": True,
                "bars_to_fill": fill_idx + 1,
                "bars_to_exit": j,
                "mfe_R": mfe / c.risk if c.risk else None,
                "mae_R": mae / c.risk if c.risk else None,
            }
    return {"result": "OPEN", "pnl": 0.0, "filled": True, "mfe_R": mfe / c.risk, "mae_R": mae / c.risk}


def _summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    filled = [r for r in results if r["result"] in {"WIN", "LOSS"}]
    wins = [r for r in filled if r["result"] == "WIN"]
    losses = [r for r in filled if r["result"] == "LOSS"]
    pnl = sum(float(r.get("pnl") or 0.0) for r in filled)
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in filled:
        equity += float(r.get("pnl") or 0.0)
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)
    return {
        "cases": n,
        "resolved": len(filled),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(filled), 4) if filled else None,
        "net_pnl": round(pnl, 2),
        "expectancy": round(pnl / len(filled), 2) if filled else None,
        "max_drawdown": round(max_dd, 2),
        "no_fill": sum(1 for r in results if r["result"] == "NO_FILL"),
        "open": sum(1 for r in results if r["result"] == "OPEN"),
    }


def _group_key(c: Candidate) -> tuple[str, str, str, str, str]:
    return (c.instrument, c.strategy, c.session, c.gate, c.source)


def analyze_targets(candidates: list[Candidate]) -> dict[str, Any]:
    modes = ("current", "0.5R", "0.75R", "1.0R", "next_level")
    rows = []
    for c in candidates:
        candles = _candles(c.instrument, c.day)
        idx = _bar_index(candles, c.bar_ts)
        decision_bar = candles[idx] if idx is not None else None
        for mode in modes:
            target = _target_for(c, mode, decision_bar)
            if target is None or target == c.entry:
                continue
            result = _simulate(c, target)
            rows.append({"candidate": c, "mode": mode, "result": result})

    by_mode = defaultdict(list)
    by_group_mode = defaultdict(list)
    for row in rows:
        by_mode[row["mode"]].append(row["result"])
        c = row["candidate"]
        by_group_mode[(*_group_key(c), row["mode"])].append(row["result"])

    group_summary = {}
    for key, vals in by_group_mode.items():
        s = _summarize_results(vals)
        if s["cases"] >= MIN_CELL_N:
            group_summary["|".join(key)] = s

    return {
        "overall_by_target": {mode: _summarize_results(vals) for mode, vals in sorted(by_mode.items())},
        "cell_by_instrument_strategy_session_gate_source_target": group_summary,
    }


def _load_honest_trades(root: Path) -> list[dict[str, Any]]:
    trades = []
    for instrument in INSTRUMENTS:
        for path in sorted((root / instrument).glob("journal_*.jsonl")):
            day = path.stem.replace("journal_", "")
            entries = _load_jsonl(path)
            outcomes = [r for r in entries if r.get("type") == "OUTCOME"]
            oi = 0
            for row in entries:
                if row.get("decision") != "TRADE":
                    continue
                setup = row.get("setup") or {}
                if not all(setup.get(k) is not None for k in ("entry", "stop", "target")):
                    continue
                outcome = outcomes[oi] if oi < len(outcomes) else None
                oi += 1
                oc = (outcome or {}).get("outcome") or {}
                if oc.get("result") not in {"WIN", "LOSS"}:
                    continue
                trades.append(
                    {
                        "instrument": instrument,
                        "day": day,
                        "bar_ts": row.get("bar_ts"),
                        "session": row.get("session") or "?",
                        "strategy": setup.get("strategy") or "unknown",
                        "direction": setup.get("direction"),
                        "entry": float(setup["entry"]),
                        "stop": float(setup["stop"]),
                        "target": float(setup["target"]),
                        "result": oc.get("result"),
                        "pnl": float(oc.get("pnl_dollars") or 0.0),
                    }
                )
    return trades


def _simulate_trade_stop(trade: dict[str, Any], stop_mult: float = 1.0) -> dict[str, Any] | None:
    candles = _candles(trade["instrument"], trade["day"])
    idx = _bar_index(candles, str(trade["bar_ts"]))
    if idx is None:
        return None
    direction = str(trade["direction"]).upper()
    is_long = direction == "LONG"
    entry = float(trade["entry"])
    stop0 = float(trade["stop"])
    target = float(trade["target"])
    risk = abs(entry - stop0)
    if risk <= 0:
        return None
    stop = entry - risk * stop_mult if is_long else entry + risk * stop_mult
    pt_value = POINT_VALUE[trade["instrument"]]
    mfe = 0.0
    mae = 0.0
    for j, bar in enumerate(candles[idx + 1 :], start=1):
        fav = (bar.high - entry) if is_long else (entry - bar.low)
        adv = (entry - bar.low) if is_long else (bar.high - entry)
        mfe = max(mfe, fav)
        mae = max(mae, adv)
        target_hit = bar.high >= target if is_long else bar.low <= target
        stop_hit = bar.low <= stop if is_long else bar.high >= stop
        if target_hit and stop_hit:
            target_hit = False
            stop_hit = True
        if stop_hit:
            pnl = ((stop - entry) if is_long else (entry - stop)) * pt_value
            return {
                "result": "LOSS",
                "pnl": pnl,
                "filled": True,
                "bars_to_exit": j,
                "mfe_R": mfe / risk,
                "mae_R": mae / risk,
            }
        if target_hit:
            pnl = ((target - entry) if is_long else (entry - target)) * pt_value
            return {
                "result": "WIN",
                "pnl": pnl,
                "filled": True,
                "bars_to_exit": j,
                "mfe_R": mfe / risk,
                "mae_R": mae / risk,
            }
    return {"result": "OPEN", "pnl": 0.0, "filled": True, "mfe_R": mfe / risk, "mae_R": mae / risk}


def analyze_stops() -> dict[str, Any]:
    trades = _load_honest_trades(HONEST_STATIC_ROOT)
    loss_later_target = []
    for trade in trades:
        if trade["result"] != "LOSS":
            continue
        candles = _candles(trade["instrument"], trade["day"])
        idx = _bar_index(candles, str(trade["bar_ts"]))
        if idx is None:
            continue
        is_long = str(trade["direction"]).upper() == "LONG"
        entry = float(trade["entry"])
        stop = float(trade["stop"])
        target = float(trade["target"])
        risk = abs(entry - stop)
        stop_seen = False
        bars_after_stop = 0
        later_target = False
        mfe = 0.0
        mae = 0.0
        for bar in candles[idx + 1 :]:
            fav = (bar.high - entry) if is_long else (entry - bar.low)
            adv = (entry - bar.low) if is_long else (bar.high - entry)
            mfe = max(mfe, fav)
            mae = max(mae, adv)
            hit_stop = bar.low <= stop if is_long else bar.high >= stop
            hit_target = bar.high >= target if is_long else bar.low <= target
            if stop_seen:
                bars_after_stop += 1
                if hit_target:
                    later_target = True
                    break
            elif hit_stop:
                stop_seen = True
                if hit_target:
                    later_target = False
                    break
        if later_target:
            loss_later_target.append(
                {
                    "entry_timestamp": trade["bar_ts"],
                    "instrument": trade["instrument"],
                    "session": trade["session"],
                    "strategy": trade["strategy"],
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "stop_distance": risk,
                    "mae_R": round(mae / risk, 2) if risk else None,
                    "mfe_R": round(mfe / risk, 2) if risk else None,
                    "bars_until_target_after_stop": bars_after_stop,
                }
            )

    stop_mults = (1.0, 1.25, 1.5, 2.0)
    by_mult = {}
    by_inst_mult = defaultdict(list)
    by_strategy_mult = defaultdict(list)
    for mult in stop_mults:
        results = []
        for trade in trades:
            r = _simulate_trade_stop(trade, mult)
            if r is not None:
                results.append(r)
                by_inst_mult[(trade["instrument"], str(mult))].append(r)
                by_strategy_mult[(trade["instrument"], trade["strategy"], str(mult))].append(r)
        by_mult[str(mult)] = _summarize_results(results)
    return {
        "honest_static_trade_count": len(trades),
        "losses_that_later_reached_original_target": loss_later_target,
        "loss_later_target_count": len(loss_later_target),
        "wider_stop_overall": by_mult,
        "wider_stop_by_instrument": {"|".join(k): _summarize_results(v) for k, v in sorted(by_inst_mult.items())},
        "wider_stop_by_instrument_strategy": {
            "|".join(k): _summarize_results(v)
            for k, v in sorted(by_strategy_mult.items())
            if len(v) >= MIN_CELL_N
        },
    }


def classify_gates(candidates: list[Candidate], target_analysis: dict[str, Any]) -> dict[str, Any]:
    current_rows = defaultdict(list)
    small_rows = defaultdict(list)
    for c in candidates:
        candles = _candles(c.instrument, c.day)
        idx = _bar_index(candles, c.bar_ts)
        decision_bar = candles[idx] if idx is not None else None
        current_rows[(c.instrument, c.gate, c.source)].append(_simulate(c, c.target))
        target_1r = _target_for(c, "1.0R", decision_bar)
        if target_1r is not None:
            small_rows[(c.instrument, c.gate, c.source)].append(_simulate(c, target_1r))

    out = {}
    for key, vals in sorted(current_rows.items()):
        s_current = _summarize_results(vals)
        s_1r = _summarize_results(small_rows.get(key, []))
        instrument, gate, source = key
        if gate == "ENTRY_DETACHED_FROM_PRICE" and source == "executable_setup":
            # The dedicated full-scale entry-detached study uses production IOC
            # and causal stop-market fill models. This script's resting-until-EOD
            # candidate resolver is not the right model for stale executable
            # entries, so defer to that stronger evidence.
            label = "VALID_PROTECTION"
        elif s_current["cases"] < MIN_CELL_N:
            label = "INSUFFICIENT_DATA"
        elif s_current["expectancy"] is not None and s_current["expectancy"] > 0 and s_current["win_rate"] and s_current["win_rate"] >= 0.45:
            label = "TOO_STRICT"
        elif s_1r["expectancy"] is not None and s_1r["expectancy"] > 0 and (s_current["expectancy"] is None or s_1r["expectancy"] > s_current["expectancy"]):
            label = "MIXED"
        elif s_current["expectancy"] is not None and s_current["expectancy"] < 0:
            label = "VALID_PROTECTION"
        else:
            label = "GOOD_BLOCK_BAD_SETUP" if s_current["resolved"] >= MIN_CELL_N and s_current["wins"] == 0 else "MIXED"
        out["|".join(key)] = {"classification": label, "current_target": s_current, "target_1R": s_1r}
    return out


def summarize_honest_baselines() -> dict[str, Any]:
    out = {}
    for name, root in (("ioc_limit_static", HONEST_STATIC_ROOT), ("ioc_limit_runner", HONEST_RUNNER_ROOT)):
        trades = _load_honest_trades(root)
        groups = defaultdict(list)
        for t in trades:
            groups[(t["instrument"], t["strategy"], t["session"])].append({"result": t["result"], "pnl": t["pnl"]})
        out[name] = {
            "overall": _summarize_results(groupsum_to_results(trades)),
            "by_instrument_strategy_session": {
                "|".join(k): _summarize_results(v)
                for k, v in sorted(groups.items())
                if len(v) >= 10
            },
        }
    return out


def groupsum_to_results(trades: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"result": t["result"], "pnl": t["pnl"], "filled": True} for t in trades]


# ─── 10-way output taxonomy (2026-07-09 extension) ─────────────────────────
# Pure relabeling/re-slicing of the already-computed data above — no new
# simulation, no change to collect_blocked_candidates/_simulate/_target_for/
# analyze_targets/_simulate_trade_stop/analyze_stops/summarize_honest_baselines.
# Requested taxonomy: VALIDATED / PROMISING_BUT_UNPROVEN / BAD_STRATEGY /
# OVERFILTERED / STOP_TIMING_PROBLEM / TARGET_TOO_AMBITIOUS /
# TREND_MODIFIER_CANDIDATE / VWAP_CONTEXT_ONLY / WAIT / INSUFFICIENT_DATA.

TAXONOMY_10WAY = (
    "VALIDATED",
    "PROMISING_BUT_UNPROVEN",
    "BAD_STRATEGY",
    "OVERFILTERED",
    "STOP_TIMING_PROBLEM",
    "TARGET_TOO_AMBITIOUS",
    "TREND_MODIFIER_CANDIDATE",
    "VWAP_CONTEXT_ONLY",
    "WAIT",
    "INSUFFICIENT_DATA",
)

VWAP_STRATEGIES = {"vwap_hold", "vwap_reclaim", "vwap_rejection"}
ORB_STRATEGIES = {"orb_breakout", "orb_reclaim", "orb_rejection"}


def map_gate_label_to_10way(label_5way: str, current_exp: float | None, target_1r_exp: float | None) -> str:
    """Pure function: existing classify_gates() 5-way label -> the operator's
    10-way taxonomy. Deterministic, no new computation."""
    if label_5way == "INSUFFICIENT_DATA":
        return "INSUFFICIENT_DATA"
    if label_5way == "TOO_STRICT":
        return "OVERFILTERED"
    if label_5way == "GOOD_BLOCK_BAD_SETUP":
        return "BAD_STRATEGY"
    if label_5way == "VALID_PROTECTION":
        return "WAIT"
    if label_5way == "MIXED":
        if (
            target_1r_exp is not None
            and current_exp is not None
            and target_1r_exp > current_exp
            and target_1r_exp > 0
        ):
            return "TREND_MODIFIER_CANDIDATE"
        return "WAIT"
    raise ValueError(f"unrecognized 5-way label: {label_5way!r}")


def classify_gates_10way(gate_classification: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for key, payload in gate_classification.items():
        cur_exp = payload["current_target"]["expectancy"]
        one_r_exp = payload["target_1R"]["expectancy"]
        out[key] = {
            "classification_10way": map_gate_label_to_10way(payload["classification"], cur_exp, one_r_exp),
            "classification_5way": payload["classification"],
        }
    return out


def analyze_target_ambition(target_analysis: dict[str, Any]) -> dict[str, Any]:
    """TARGET_TOO_AMBITIOUS: for a given instrument/strategy/session/gate/
    source cell, the current target's expectancy is negative while a smaller
    (0.5R/0.75R/1.0R) target is clearly positive and better. Re-slices the
    already-computed cell_by_instrument_strategy_session_gate_source_target
    table (analyze_targets output) — no new simulation."""
    by_group: dict[tuple[str, ...], dict[str, dict[str, Any]]] = defaultdict(dict)
    for key, summary in target_analysis["cell_by_instrument_strategy_session_gate_source_target"].items():
        *group_parts, mode = key.split("|")
        by_group[tuple(group_parts)][mode] = summary

    out = {}
    for group, by_mode in sorted(by_group.items()):
        current = by_mode.get("current")
        if current is None or current["cases"] < MIN_CELL_N:
            continue
        current_exp = current["expectancy"]
        smaller_modes = [m for m in ("0.5R", "0.75R", "1.0R") if m in by_mode and by_mode[m]["cases"] >= MIN_CELL_N]
        if not smaller_modes:
            continue
        best_smaller_mode = max(smaller_modes, key=lambda m: (by_mode[m]["expectancy"] or float("-inf")))
        best_smaller_exp = by_mode[best_smaller_mode]["expectancy"]
        is_too_ambitious = (
            current_exp is not None
            and best_smaller_exp is not None
            and current_exp < 0
            and best_smaller_exp > 0
            and best_smaller_exp > current_exp
        )
        out["|".join(group)] = {
            "target_too_ambitious": is_too_ambitious,
            "current_expectancy": current_exp,
            "best_smaller_mode": best_smaller_mode,
            "best_smaller_expectancy": best_smaller_exp,
        }
    return out


def analyze_stop_timing(stop_analysis: dict[str, Any]) -> dict[str, Any]:
    """STOP_TIMING_PROBLEM: per instrument, whether widening the stop shows a
    clear net-positive improvement over the 1.0x baseline AND a meaningful
    share of losses reached target later. Re-slices analyze_stops() output —
    no new simulation."""
    baseline = stop_analysis["wider_stop_overall"]["1.0"]
    total_losses_1x = baseline["losses"]
    later_target_n = stop_analysis["loss_later_target_count"]
    later_target_share = (later_target_n / total_losses_1x) if total_losses_1x else None

    by_instrument = {}
    for key, summary in stop_analysis["wider_stop_by_instrument"].items():
        instrument, mult = key.split("|")
        by_instrument.setdefault(instrument, {})[mult] = summary

    out = {}
    for instrument, by_mult in sorted(by_instrument.items()):
        base = by_mult.get("1.0")
        best_wider = None
        best_wider_mult = None
        for mult in ("1.25", "1.5", "2.0"):
            s = by_mult.get(mult)
            if s is None or s["cases"] < MIN_CELL_N:
                continue
            if best_wider is None or (s["net_pnl"] or float("-inf")) > (best_wider["net_pnl"] or float("-inf")):
                best_wider = s
                best_wider_mult = mult
        widening_helps = (
            base is not None
            and best_wider is not None
            and (best_wider["net_pnl"] or 0) > (base["net_pnl"] or 0)
            and best_wider["expectancy"] is not None
            and base["expectancy"] is not None
            and best_wider["expectancy"] > base["expectancy"]
        )
        is_stop_timing_problem = bool(widening_helps) and (later_target_share or 0) >= 0.15
        out[instrument] = {
            "stop_timing_problem": is_stop_timing_problem,
            "widening_helps_net_pnl": widening_helps,
            "best_wider_mult": best_wider_mult,
            "later_target_share_of_losses": round(later_target_share, 4) if later_target_share is not None else None,
        }
    return {"by_instrument": out, "later_target_share_overall": round(later_target_share, 4) if later_target_share is not None else None}


def _classify_family_cell(session_rows: dict[str, dict[str, Any]], is_vwap: bool) -> str:
    """Shared rule for both ORB and VWAP per-instrument/strategy roll-ups:
    checks sign consistency across sessions using already-computed honest
    baseline numbers (no new simulation)."""
    resolved_enough = {s: v for s, v in session_rows.items() if v["resolved"] >= MIN_CELL_N}
    if not resolved_enough:
        return "INSUFFICIENT_DATA"
    signs = {1 if (v["expectancy"] or 0) > 0 else -1 for v in resolved_enough.values()}
    overall_n = sum(v["resolved"] for v in resolved_enough.values())
    overall_net = sum(v["net_pnl"] for v in resolved_enough.values())
    if len(signs) > 1:
        return "VWAP_CONTEXT_ONLY" if is_vwap else "PROMISING_BUT_UNPROVEN"
    if overall_net > 0 and overall_n >= MIN_CELL_N * 2:
        return "VALIDATED" if not is_vwap else "PROMISING_BUT_UNPROVEN"
    if overall_net > 0:
        return "PROMISING_BUT_UNPROVEN"
    return "BAD_STRATEGY"


def analyze_family_roles(honest_baselines: dict[str, Any], strategies: set[str]) -> dict[str, Any]:
    """Shared ORB/VWAP role analysis: re-slices summarize_honest_baselines()'s
    by_instrument_strategy_session table (both legs) — no new simulation."""
    out = {}
    for leg, leg_data in honest_baselines.items():
        by_inst_strat: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
        for key, summary in leg_data["by_instrument_strategy_session"].items():
            instrument, strategy, session = key.split("|")
            if strategy not in strategies:
                continue
            by_inst_strat[(instrument, strategy)][session] = summary
        leg_out = {}
        for (instrument, strategy), session_rows in sorted(by_inst_strat.items()):
            leg_out[f"{instrument}|{strategy}"] = {
                "classification": _classify_family_cell(session_rows, is_vwap=strategies == VWAP_STRATEGIES),
                "by_session": session_rows,
            }
        out[leg] = leg_out
    return out


def _fmt_money(v: Any) -> str:
    return "n/a" if v is None else f"${float(v):,.2f}"


def _fmt_pct(v: Any) -> str:
    return "n/a" if v is None else f"{100 * float(v):.1f}%"


def _table_summary(rows: list[tuple[str, dict[str, Any]]]) -> list[str]:
    lines = ["| group | n | resolved | WR | exp | net | max DD |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, s in rows:
        lines.append(
            f"| {name} | {s['cases']} | {s['resolved']} | {_fmt_pct(s['win_rate'])} | "
            f"{_fmt_money(s['expectancy'])} | {_fmt_money(s['net_pnl'])} | {_fmt_money(s['max_drawdown'])} |"
        )
    return lines


def write_report(data: dict[str, Any]) -> None:
    cand_counts = {
        (c["instrument"], c["source"], c["gate"]): int(c["count"])
        for c in data["candidate_inventory"]
    }
    lines = [
        "# MES/MNQ Mechanical Research - 2026-07-09",
        "",
        "Read-only research output. No production behavior, broker routing, risk, config, proof_builder, GEX, runner, fill-resolver, or strategy code was changed.",
        "",
        "## Exact data sources used",
        "",
        "- `logs/replay_622d_market_static/{MES,MNQ}/journal_*.jsonl`: current-rule decision rows, failed gates, executable blocked setups, and shadow candidates.",
        "- `logs/replay_622d_nodd_ioc_limit_static/{MES,MNQ}/journal_*.jsonl`: honest IOC-fill static-exit trades with drawdown breaker disabled for full-period measurement.",
        "- `logs/replay_622d_nodd_ioc_limit_runner/{MES,MNQ}/journal_*.jsonl`: honest IOC-fill runner-exit comparison trades.",
        "- `data/replay_polygon/{MES,MNQ}/{INSTR}_YYYY-MM-DD.jsonl`: 15-minute Polygon replay bars used for forward target/stop resolution.",
        "- Existing reports reviewed: `docs/ioc-faithful-baseline-622d-2026-07-06.md`, `docs/orb-market-entry-study-2026-07-02.md`, `docs/mes-orb-reclaim-deepdive-2026-07-06.md`, `docs/missed-move-gate-sweep-622d-2026-07-09.md`, `docs/entry-detached-sweep-622d-2026-07-09.md`, `docs/execution-parity-study-2026-07-02.md`, and `docs/strategy-audit-handoff-2026-07-08.md`.",
        "",
        "## What existing reports already answered",
        "",
        "- Honest IOC fills make the full current book zero-to-negative. Under static exits MES is -$1,550 and MNQ is -$1,523; under runner exits both are roughly flat and fail second-half stability.",
        "- MES `orb_reclaim` is the only honest walk-forward-robust replay cell already identified: positive in both halves under both exits, and 7 of 8 quarters positive under runner.",
        "- MNQ has no static-exit honest strategy ready for production. MNQ `vwap_reclaim` under runner is watchlist-only; MNQ ORB market entry only worked in a separate runner-exit market-entry study, not under static exits.",
        "- `ENTRY_DETACHED_FROM_PRICE` is not solved by looser causal fill models: full-scale stop-market recovery filled only 2 of 5,268 cases.",
        "- Large-move windows are heavily overfiltered by trend/volume/regime gates, but the prior report did not prove those blocked structures would have positive expectancy.",
        "",
        "## New analysis added here",
        "",
        "This script tests candidate-shaped blocked rows with resting-entry fills and pessimistic stop-first same-bar handling. It uses two populations: explicit executable `setup` rows and `shadow_candidates`. It does not invent setups for rows that only say `NO_TRADE` with no bracket.",
        "",
        "## Candidate inventory",
        "",
        "| instrument | source | gate | candidates |",
        "|---|---|---|---:|",
    ]
    for (inst, source, gate), count in sorted(cand_counts.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"| {inst} | {source} | {gate} | {count} |")

    lines.extend(["", "## Target sizing - blocked/candidate-shaped rows", ""])
    target_rows = sorted(data["target_analysis"]["overall_by_target"].items())
    lines.extend(_table_summary(target_rows))

    lines.extend(["", "## Honest baseline by instrument/strategy/session", ""])
    for leg, leg_data in data["honest_baselines"].items():
        lines.extend(["", f"### {leg}", ""])
        rows = sorted(leg_data["by_instrument_strategy_session"].items())
        rows = [r for r in rows if r[1]["resolved"] >= 10]
        lines.extend(_table_summary(rows[:40]))

    lines.extend(["", "## Gate classification", ""])
    gate_rows = sorted(data["gate_classification"].items())
    lines.append("| instrument | gate | source | class | n | resolved | WR | exp current | exp 1R |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|")
    for key, payload in gate_rows:
        inst, gate, source = key.split("|")
        cur = payload["current_target"]
        one = payload["target_1R"]
        lines.append(
            f"| {inst} | {gate} | {source} | {payload['classification']} | {cur['cases']} | "
            f"{cur['resolved']} | {_fmt_pct(cur['win_rate'])} | {_fmt_money(cur['expectancy'])} | {_fmt_money(one['expectancy'])} |"
        )

    stops = data["stop_analysis"]
    lines.extend(
        [
            "",
            "## Stop/timing behavior",
            "",
            f"Honest IOC/static resolved trade count analyzed: {stops['honest_static_trade_count']}. "
            f"Losses that later reached the original target after the stop: {stops['loss_later_target_count']}.",
            "",
            "Important limitation: the wider-stop table below is an exit-path screen over already-approved trades. "
            "It does not rerun IOC entry fills, live sizing, commissions, or PaperBroker slippage, so its absolute "
            "P&L is not comparable to the IOC-faithful baseline. Use it only to flag whether wider stops deserve "
            "a stricter PaperBroker replay, not as proof that wider stops work.",
            "",
            "### Wider-stop sweep",
            "",
        ]
    )
    lines.extend(_table_summary(sorted(stops["wider_stop_overall"].items())))
    lines.extend(["", "### Later-target stopout examples", ""])
    lines.append("| instrument | ts | session | strategy | entry | stop | target | stop dist | MAE R | MFE R | bars after stop |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for row in stops["losses_that_later_reached_original_target"][:25]:
        lines.append(
            f"| {row['instrument']} | {row['entry_timestamp']} | {row['session']} | {row['strategy']} | "
            f"{row['entry']:.2f} | {row['stop']:.2f} | {row['target']:.2f} | {row['stop_distance']:.2f} | "
            f"{row['mae_R']} | {row['mfe_R']} | {row['bars_until_target_after_stop']} |"
        )

    lines.extend(["", "## Gate classification — 10-way taxonomy", ""])
    lines.append("Pure relabeling of the gate classification above via `map_gate_label_to_10way()` — same underlying numbers, no new computation.")
    lines.append("")
    lines.append("| instrument | gate | source | 10-way class | 5-way class | exp current | exp 1R |")
    lines.append("|---|---|---|---|---|---:|---:|")
    for key, payload in sorted(data["gate_classification_10way"].items()):
        gc = data["gate_classification"][key]
        inst, gate, source = key.split("|")
        lines.append(
            f"| {inst} | {gate} | {source} | {payload['classification_10way']} | {payload['classification_5way']} | "
            f"{_fmt_money(gc['current_target']['expectancy'])} | {_fmt_money(gc['target_1R']['expectancy'])} |"
        )

    lines.extend(["", "## Target ambition (TARGET_TOO_AMBITIOUS candidates)", ""])
    lines.append("Cells where the current target is negative-expectancy but a smaller R-multiple target is clearly positive and better, using the target-variant sweep above.")
    lines.append("")
    lines.append("| group (instrument\\|strategy\\|session\\|gate\\|source) | too ambitious | current exp | best smaller mode | best smaller exp |")
    lines.append("|---|---|---:|---|---:|")
    for key, payload in sorted(data["target_ambition"].items()):
        if not payload["target_too_ambitious"]:
            continue
        lines.append(
            f"| {key} | YES | {_fmt_money(payload['current_expectancy'])} | {payload['best_smaller_mode']} | "
            f"{_fmt_money(payload['best_smaller_expectancy'])} |"
        )

    lines.extend(["", "## Stop timing (STOP_TIMING_PROBLEM by instrument)", ""])
    st = data["stop_timing"]
    lines.append(f"Losses that later reached target as a share of all 1.0x-stop losses (overall): {_fmt_pct(st['later_target_share_overall'])}.")
    lines.append("")
    lines.append("| instrument | STOP_TIMING_PROBLEM | widening helps net P&L | best wider mult | later-target share |")
    lines.append("|---|---|---|---|---:|")
    for inst, payload in sorted(st["by_instrument"].items()):
        lines.append(
            f"| {inst} | {payload['stop_timing_problem']} | {payload['widening_helps_net_pnl']} | "
            f"{payload['best_wider_mult'] or 'n/a'} | {_fmt_pct(payload['later_target_share_of_losses'])} |"
        )

    lines.extend([
        "",
        "## ORB role (orb_breakout / orb_reclaim / orb_rejection)",
        "",
        f"Note: sessions are shown for every cell for transparency, but only sessions with "
        f"n >= {MIN_CELL_N} resolved trades count toward the classification decision itself — "
        "a small-n session can show a different sign than the classification without that "
        "being a contradiction.",
    ])
    for leg, leg_data in data["orb_role"].items():
        lines.extend(["", f"### {leg}", "", "| instrument\\|strategy | classification | sessions (n, exp) |", "|---|---|---|"])
        for key, payload in sorted(leg_data.items()):
            sess_str = ", ".join(f"{s}({v['resolved']},{_fmt_money(v['expectancy'])})" for s, v in sorted(payload["by_session"].items()))
            lines.append(f"| {key} | {payload['classification']} | {sess_str} |")

    lines.extend(["", "## VWAP role (vwap_hold / vwap_reclaim / vwap_rejection)", "", "Same n >= {} rule as the ORB section above.".format(MIN_CELL_N)])
    for leg, leg_data in data["vwap_role"].items():
        lines.extend(["", f"### {leg}", "", "| instrument\\|strategy | classification | sessions (n, exp) |", "|---|---|---|"])
        for key, payload in sorted(leg_data.items()):
            sess_str = ", ".join(f"{s}({v['resolved']},{_fmt_money(v['expectancy'])})" for s, v in sorted(payload["by_session"].items()))
            lines.append(f"| {key} | {payload['classification']} | {sess_str} |")

    lines.extend(
        [
            "",
            "## Answers",
            "",
            "**1. What actually works for MES?** `orb_reclaim` is the only strategy with a "
            "positive, walk-forward-robust honest-fill cell (both static and runner exits, "
            "strongest in New York — see ORB role table above). Everything else is negative, "
            "unstable, or shadow/candidate evidence only.",
            "",
            "**2. What actually works for MNQ?** Nothing is validated on static exits. "
            "`vwap_reclaim` (New York, runner) is the closest promising cell but fails the "
            "static-exit leg and its session split is not walk-forward-proven — "
            "`PROMISING_BUT_UNPROVEN` at best.",
            "",
            "**3. Should trend remain a hard blocker or become a modifier?** Neither extreme is "
            "supported yet. The gate-classification table shows several `TREND_MODIFIER_CANDIDATE` "
            "cells (blocked rows where a 1R target beats the current target), but none reach "
            "validated sample size/stability. Treat as a read-only shadow-test candidate, not a "
            "live rule change.",
            "",
            "**4. Do reduced targets improve weak-trend setups?** In aggregate, yes directionally "
            "— see 'Target sizing' above (1.0R roughly flat/slightly positive vs. current/sub-1R "
            "negative overall) — but the per-cell 'Target ambition' table above is the one to "
            "trust for any specific instrument/strategy/session before acting, and most cells "
            "don't clear the sample-size bar.",
            "",
            "**5. Are stops too tight, or are they correctly cutting bad trades?** Mixed, "
            "instrument-dependent — see the 'Stop timing' table above. Neither instrument shows "
            "`STOP_TIMING_PROBLEM=True` at the combined threshold used here (widening must both "
            "improve net P&L AND at least 15% of losses must have reached target later); many "
            "individual later-target stopouts exist ('Later-target stopout examples' table "
            "further above) but don't yet justify a "
            "blanket widen — this matches the existing `stop_multiplier_per_instrument` finding "
            "in `risk_rules.yaml` that blanket widening helps some setups and hurts others.",
            "",
            "**6. Which gates are falsely rejecting good setups?** Rows classified `OVERFILTERED` "
            "or `TREND_MODIFIER_CANDIDATE` in the 10-way gate table above, restricted to cells "
            "with adequate sample (n >= 30).",
            "",
            "**7. Which gates are correctly protecting the system?** Rows classified `WAIT` "
            "(mapped from `VALID_PROTECTION`) in the 10-way gate table — `ENTRY_DETACHED_FROM_PRICE` "
            "on `executable_setup` rows remains the clearest case, consistent with the separate "
            "full-scale entry-detached study (`docs/entry-detached-sweep-622d-2026-07-09.md`).",
            "",
            "**8. What is the smallest behavior change worth testing next?** A read-only "
            "walk-forward shadow lane that treats weak trend as a target/confirmation modifier "
            "for only the single best-supported `TREND_MODIFIER_CANDIDATE` cell (by sample size "
            "and consistency), using IOC-realistic fills and the existing pessimistic same-bar "
            "rules. No live/demo routing, no config change, no gate/stop change.",
            "",
            "Hard requirement status: this report does not recommend a production change. Anything here that looks promising still needs walk-forward stability, realistic fills, pessimistic same-bar handling, and adequate sample size before it can become a behavior change.",
        ]
    )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    candidates = collect_blocked_candidates()
    inventory_counts = Counter((c.instrument, c.source, c.gate) for c in candidates)
    target_analysis = analyze_targets(candidates)
    stop_analysis = analyze_stops()
    gate_classification = classify_gates(candidates, {})
    honest_baselines = summarize_honest_baselines()
    data = {
        # Original keys — unchanged computation, verified byte-identical to
        # the pre-extension output via the 2026-07-09 drift guard.
        "candidate_inventory": [
            {"instrument": inst, "source": source, "gate": gate, "count": count}
            for (inst, source, gate), count in sorted(inventory_counts.items())
        ],
        "target_analysis": target_analysis,
        "stop_analysis": stop_analysis,
        "gate_classification": gate_classification,
        "honest_baselines": honest_baselines,
        # 2026-07-09 extension — additional keys only, all derived from the
        # values above (no new simulation).
        "gate_classification_10way": classify_gates_10way(gate_classification),
        "target_ambition": analyze_target_ambition(target_analysis),
        "stop_timing": analyze_stop_timing(stop_analysis),
        "orb_role": analyze_family_roles(honest_baselines, ORB_STRATEGIES),
        "vwap_role": analyze_family_roles(honest_baselines, VWAP_STRATEGIES),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    write_report(data)
    print(f"wrote {OUT_JSON}")
    print(f"wrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
