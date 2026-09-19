"""Controlled causal trigger-bar audit for the frozen 12HR Miyagi population.

Research only. This does not change the canonical detector, replay engine,
strategy rules, risk rules, or runtime. It compares the existing replay policy
with an alternate policy that makes the documented stop active on the trigger
bar and uses gap-aware stop-market entry semantics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from research.bars_12hr_miyagi_loader import load_5m_day
from research.replay_12hr_miyagi_honest_fill import (
    DAY_ONLY_EXIT_REASON,
    EOD_BAR_MISSING,
    POINT_VALUE,
    ROUND_TRIP_COMMISSION,
    TICK_SIZE,
    TRIGGER_NOT_HIT,
    _metrics,
    recover_entry,
    replay_signal,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_DIR = REPO_ROOT / "docs" / "strategy-rules" / "evidence_12hr_miyagi"
STUDY_START = date(2024, 7, 2)
STUDY_END = date(2026, 6, 26)
MIDPOINT = STUDY_START + (STUDY_END - STUDY_START) / 2
PREREG_COMMIT = "f1e08bd56a31ce5fc97866990228ac4516387c3c"
BASE_COMMIT = "36e73f1981850b66b043d849ce877c15bd1ab3e7"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_signal(candidate: dict) -> dict:
    return {**candidate, "date": date.fromisoformat(candidate["date"])}


ET = ZoneInfo("America/New_York")
_EOD_BAR_START = time(15, 55)
_DAY_CLOSE = time(16, 0)


def _legacy_resolve_exit(signal: dict, day_bars: list, entry_bar: dict) -> dict:
    """Reproduce the superseded pre-fix entry-bar exclusion exactly."""
    close_time = datetime.combine(signal["date"], _DAY_CLOSE, ET)
    direction = signal["direction"]
    entry_is_eod_bar = (
        entry_bar["ts"].timetz().replace(tzinfo=None) == _EOD_BAR_START
    )
    if entry_is_eod_bar:
        eligible = [entry_bar]
    else:
        eligible = sorted(
            (
                bar
                for bar in day_bars
                if bar["ts"] > entry_bar["ts"] and bar["ts"] < close_time
            ),
            key=lambda bar: bar["ts"],
        )

    for bar in eligible:
        stop_hit = (
            bar["low"] <= signal["stop"]
            if direction == "LONG"
            else bar["high"] >= signal["stop"]
        )
        target_hit = (
            bar["high"] >= signal["target"]
            if direction == "LONG"
            else bar["low"] <= signal["target"]
        )
        if stop_hit:
            return {"reason": "STOP", "base_exit": signal["stop"], "bar": bar}
        if target_hit:
            return {"reason": "TARGET", "base_exit": signal["target"], "bar": bar}

    exact_bar = next(
        (
            bar
            for bar in day_bars
            if bar["ts"].timetz().replace(tzinfo=None) == _EOD_BAR_START
        ),
        None,
    )
    if exact_bar is not None:
        return {
            "reason": DAY_ONLY_EXIT_REASON,
            "base_exit": exact_bar["close"],
            "bar": exact_bar,
        }
    return {
        "reason": EOD_BAR_MISSING,
        "base_exit": None,
        "bar": eligible[-1] if eligible else entry_bar,
    }


def legacy_trigger_bar_replay(
    signal: dict,
    day_bars: list,
    *,
    slippage_ticks: float,
) -> dict:
    """Freeze the superseded replay policy for audit provenance only."""
    instrument = signal["instrument"]
    point_value = POINT_VALUE[instrument]
    direction = signal["direction"]
    slip = slippage_ticks * TICK_SIZE
    entry = recover_entry(signal, day_bars)
    base = {
        "date": signal["date"].isoformat(),
        "instrument": instrument,
        "direction": direction,
        "trigger": signal["entry_trigger"],
        "stop": signal["stop"],
        "target": signal["target"],
        "target_2": signal.get("target_2"),
        "slippage_ticks": slippage_ticks,
    }
    if entry is None:
        return {
            **base,
            "filled": False,
            "result": "NO_FILL",
            "exit_reason": TRIGGER_NOT_HIT,
            "entry_bar_ts": None,
            "base_entry_price": None,
            "fill_entry_price": None,
            "base_exit_price": None,
            "fill_exit_price": None,
            "exit_bar_ts": None,
            "gross_pnl": 0.0,
            "slippage_cost": 0.0,
            "commission": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
        }

    entry_bar = entry["bar"]
    trigger = signal["entry_trigger"]
    base_fill = trigger
    fill_price = trigger + slip if direction == "LONG" else trigger - slip
    stop_wrong_side = (
        signal["stop"] >= fill_price
        if direction == "LONG"
        else signal["stop"] <= fill_price
    )
    if stop_wrong_side:
        return {
            **base,
            "filled": False,
            "entry_bar_ts": entry_bar["ts"].isoformat(),
            "result": "CANCELLED",
            "exit_reason": "POST_FILL_INVALID_STOP",
            "base_entry_price": base_fill,
            "fill_entry_price": fill_price,
            "base_exit_price": None,
            "fill_exit_price": None,
            "exit_bar_ts": None,
            "gross_pnl": 0.0,
            "slippage_cost": 0.0,
            "commission": 0.0,
            "total_costs": 0.0,
            "net_pnl": 0.0,
        }

    exit_info = _legacy_resolve_exit(signal, day_bars, entry_bar)
    if exit_info["reason"] == EOD_BAR_MISSING:
        return {
            **base,
            "filled": True,
            "entry_bar_ts": entry_bar["ts"].isoformat(),
            "result": "UNRESOLVED",
            "exit_reason": EOD_BAR_MISSING,
            "base_entry_price": base_fill,
            "fill_entry_price": fill_price,
            "base_exit_price": None,
            "fill_exit_price": None,
            "exit_bar_ts": (
                exit_info["bar"]["ts"].isoformat() if exit_info["bar"] else None
            ),
            "gross_pnl": None,
            "slippage_cost": None,
            "commission": None,
            "total_costs": None,
            "net_pnl": None,
        }

    base_exit = exit_info["base_exit"]
    signed = 1.0 if direction == "LONG" else -1.0
    actual_exit = base_exit - slip if direction == "LONG" else base_exit + slip
    gross_pnl = signed * (base_exit - base_fill) * point_value
    entry_slippage_cost = signed * (fill_price - base_fill) * point_value
    exit_slippage_cost = signed * (base_exit - actual_exit) * point_value
    slippage_cost = entry_slippage_cost + exit_slippage_cost
    total_costs = slippage_cost + ROUND_TRIP_COMMISSION
    net_pnl = gross_pnl - total_costs
    return {
        **base,
        "filled": True,
        "entry_bar_ts": entry_bar["ts"].isoformat(),
        "result": "WIN" if net_pnl > 0 else "LOSS" if net_pnl < 0 else "BREAKEVEN",
        "exit_reason": exit_info["reason"],
        "base_entry_price": base_fill,
        "fill_entry_price": fill_price,
        "base_exit_price": base_exit,
        "fill_exit_price": actual_exit,
        "exit_bar_ts": exit_info["bar"]["ts"].isoformat(),
        "gross_pnl": gross_pnl,
        "slippage_cost": slippage_cost,
        "commission": ROUND_TRIP_COMMISSION,
        "total_costs": total_costs,
        "net_pnl": net_pnl,
    }


def _cancelled(old: dict, *, base_fill: float, fill_price: float) -> dict:
    return {
        **old,
        "filled": False,
        "result": "CANCELLED",
        "exit_reason": "POST_FILL_INVALID_BRACKET",
        "base_entry_price": base_fill,
        "fill_entry_price": fill_price,
        "base_exit_price": None,
        "fill_exit_price": None,
        "exit_bar_ts": None,
        "gross_pnl": 0.0,
        "slippage_cost": 0.0,
        "commission": 0.0,
        "total_costs": 0.0,
        "net_pnl": 0.0,
    }


def causal_trigger_bar_replay(signal: dict, day_bars: list, *, slippage_ticks: float) -> dict:
    """Canonical corrected replay used as the audit's causal branch."""
    return replay_signal(signal, day_bars, slippage_ticks=slippage_ticks)


def _compact(metrics: dict) -> dict:
    return {
        key: metrics.get(key)
        for key in (
            "n", "fills", "resolved_fills", "wins", "losses", "win_rate",
            "net_pnl", "profit_factor", "max_drawdown", "no_fill", "cancellations",
        )
    }


def _split_metrics(rows: list[dict]) -> dict:
    h1 = [row for row in rows if date.fromisoformat(row["date"]) <= MIDPOINT]
    h2 = [row for row in rows if date.fromisoformat(row["date"]) > MIDPOINT]
    return {
        "overall": _compact(_metrics(rows)),
        "H1": _compact(_metrics(h1)),
        "H2": _compact(_metrics(h2)),
    }


def _entry_bar_flags(signal: dict, day_bars: list) -> dict | None:
    entry = recover_entry(signal, day_bars)
    if entry is None:
        return None
    bar = entry["bar"]
    direction = signal["direction"]
    trigger = float(signal["entry_trigger"])
    stop = float(signal["stop"])
    target = float(signal["target"])
    open_price = float(bar["open"])
    gap = open_price >= trigger if direction == "LONG" else open_price <= trigger
    stop_hit = bar["low"] <= stop if direction == "LONG" else bar["high"] >= stop
    target_hit = bar["high"] >= target if direction == "LONG" else bar["low"] <= target
    return {
        "date": signal["date"].isoformat(),
        "direction": direction,
        "entry_bar_ts": bar["ts"].isoformat(),
        "open": open_price,
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "trigger": trigger,
        "stop": stop,
        "target": target,
        "gap_through": gap,
        "same_bar_stop": stop_hit,
        "same_bar_target": target_hit,
        "same_bar_both": stop_hit and target_hit,
    }


def run(cache_root: Path) -> dict:
    result = {
        "status": "AUDIT_ONLY",
        "prereg_commit": PREREG_COMMIT,
        "base_commit": BASE_COMMIT,
        "study_range": {"start": STUDY_START.isoformat(), "end": STUDY_END.isoformat()},
        "midpoint": MIDPOINT.isoformat(),
        "source_cache_contract": "data/replay_polygon_5m/<ROOT>/<ROOT>_<DATE>.jsonl",
        "policy_change": {
            "current": "entry trigger bar excluded from stop/T1 resolution",
            "audit": "trigger bar eligible immediately; pessimistic STOP if entry and stop both touched",
            "gap_rule": "pre-armed stop-market: gap-through fills from bar open plus adverse slippage",
        },
        "instruments": {},
    }

    for instrument in ("MNQ", "MES"):
        evidence_path = EVIDENCE_DIR / f"{instrument.lower()}_results_corrected_2026-09-18.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        signals_and_bars = []
        flags = []
        for candidate in evidence["candidates"]:
            signal = _candidate_signal(candidate)
            bars = load_5m_day(cache_root, instrument, signal["date"])
            signals_and_bars.append((signal, bars))
            flag = _entry_bar_flags(signal, bars)
            if flag is not None:
                flags.append(flag)

        sensitivity = {}
        changed_rows = {}
        for slip in (1, 2, 3, 4):
            current_rows = [
                legacy_trigger_bar_replay(signal, bars, slippage_ticks=float(slip))
                for signal, bars in signals_and_bars
            ]
            causal_rows = [
                causal_trigger_bar_replay(signal, bars, slippage_ticks=float(slip))
                for signal, bars in signals_and_bars
            ]
            sensitivity[str(slip)] = {
                "current": _split_metrics(current_rows),
                "causal": _split_metrics(causal_rows),
            }
            changed_rows[str(slip)] = [
                {
                    "date": old["date"],
                    "current": {
                        "result": old["result"],
                        "exit_reason": old["exit_reason"],
                        "net_pnl": old["net_pnl"],
                    },
                    "causal": {
                        "result": new["result"],
                        "exit_reason": new["exit_reason"],
                        "net_pnl": new["net_pnl"],
                    },
                }
                for old, new in zip(current_rows, causal_rows)
                if (
                    old["result"],
                    old["exit_reason"],
                    round(float(old["net_pnl"] or 0.0), 8),
                )
                != (
                    new["result"],
                    new["exit_reason"],
                    round(float(new["net_pnl"] or 0.0), 8),
                )
            ]

        affected = [
            flag for flag in flags
            if flag["same_bar_stop"] or flag["same_bar_target"] or flag["gap_through"]
        ]
        hashes = {}
        for flag in affected:
            source = cache_root / instrument / f"{instrument}_{flag['date']}.jsonl"
            if source.exists():
                hashes[source.name] = _sha256(source)

        result["instruments"][instrument] = {
            "candidate_count": len(evidence["candidates"]),
            "filled_entry_bar_count": len(flags),
            "gap_through_count": sum(bool(x["gap_through"]) for x in flags),
            "same_trigger_bar_stop_count": sum(bool(x["same_bar_stop"]) for x in flags),
            "same_trigger_bar_target_count": sum(bool(x["same_bar_target"]) for x in flags),
            "same_trigger_bar_both_count": sum(bool(x["same_bar_both"]) for x in flags),
            "affected_entry_bars": affected,
            "affected_source_sha256": hashes,
            "slippage_sensitivity": sensitivity,
            "changed_rows": changed_rows,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=REPO_ROOT / "data" / "replay_polygon_5m",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "scripts" / "miyagi_trigger_bar_causal_audit_2026-09-19.json",
    )
    args = parser.parse_args()
    result = run(args.cache_root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(args.out)


if __name__ == "__main__":
    main()
