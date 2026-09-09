#!/usr/bin/env python3
"""Controlled MES strat_122 repair tests.

Evidence only. No runtime/config files are changed.

Design:
- current engine / ReplayEngine / RiskEngine / PaperBroker;
- frozen PR #373 concept/permission posture;
- fixed one-contract sizing to prevent treatment P&L from changing downstream
  contract counts;
- 1 adverse tick, pessimistic same-bar handling, static exit;
- $1.48 round-trip commission applied at analysis layer;
- one strategy variable changed per mechanical treatment.

Mechanical treatments (isolated [strat_212, strat_122] lane):
  baseline          current canonical 1-2-2
  entry_confirm_2t  entry only: one extra tick beyond the current trigger
  stop_wider_4t     stop only: four ticks farther from entry; target unchanged
  target_1_5r       target only: 1.5R instead of 2R; entry/stop unchanged

Filter treatment (frozen #373 production concept list):
  scoped_fallback   existing candidate fallback enabled only on the four
                    pre-registered ENTRY_DETACHED bars from PR #514

The report includes carry-forward state, unresolved/cancelled trades, H1/H2,
monthly P&L, drawdown, commission-adjusted metrics, and collateral changes.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import json
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import replay.replay_engine as replay_module  # noqa: E402
import strategy.signal_engine as signal_module  # noqa: E402
import strategy.strat_212_122 as strat_module  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from strategy.signal_engine import DecisionEngine as BaseDecisionEngine  # noqa: E402
from scripts.mes_122_fallback_full_engine_proof import (  # noqa: E402
    CORPUS,
    INSTRUMENT,
    STRATEGY,
    TARGETS,
    ScopedFallbackDecisionEngine,
    _frozen_373_config,
    _json_lines,
)

COMMISSION_RT = 1.48
VARIANTS = ("baseline", "entry_confirm_2t", "stop_wider_4t", "target_1_5r")


def _fixed_one_contract_config(*, isolated: bool):
    base = _frozen_373_config()
    sizing = dataclasses.replace(
        base.position_sizing,
        enabled=False,
        sizing_rules=[],
    )
    cfg = dataclasses.replace(
        base,
        position_sizing=sizing,
        max_contracts_per_instrument={"MES": 1, "MNQ": 1},
        max_contracts_hard_cap=1,
        win_streak_bonus_after=0,
        win_streak_bonus_contracts=0,
        bonus_trades_after_max=0,
        fill_slippage_ticks=1.0,
        fill_pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        exit_mode="static",
        strategy_fallback_enabled=False,
    )
    if isolated:
        cfg = dataclasses.replace(
            cfg,
            enabled_concepts=["strat_212", "strat_122"],
            disabled_concepts_per_instrument={},
        )
    return cfg


def _variant_advance(name: str) -> Callable[..., tuple[dict, dict | None]]:
    original = strat_module.advance_strat_212_122
    if name == "baseline":
        return original

    def wrapped(**kwargs):
        next_state, candidate = original(**kwargs)
        if next_state.get("status") != "ARMED" or next_state.get("pattern") != STRATEGY:
            return next_state, candidate

        tick = float(kwargs["tick_size"])
        state = dict(next_state)
        direction = state["direction"]

        if name == "entry_confirm_2t":
            # Canonical entry is already one tick beyond the boundary.
            # Move only entry one additional tick farther; stop/target unchanged.
            state["entry_price"] = (
                float(state["entry_price"]) + tick
                if direction == "LONG"
                else float(state["entry_price"]) - tick
            )
        elif name == "stop_wider_4t":
            # Widen only the stop. Target remains at the baseline absolute price.
            state["stop_price"] = (
                float(state["stop_price"]) - (4.0 * tick)
                if direction == "LONG"
                else float(state["stop_price"]) + (4.0 * tick)
            )
        elif name == "target_1_5r":
            # Change only the target from the canonical 2R convention to 1.5R.
            entry = float(state["entry_price"])
            stop = float(state["stop_price"])
            risk = abs(entry - stop)
            state["target_price"] = (
                entry + (1.5 * risk) if direction == "LONG" else entry - (1.5 * risk)
            )
        else:
            raise ValueError(f"unknown variant: {name}")
        return state, candidate

    return wrapped


def _run_pass(
    config,
    log_dir: Path,
    *,
    decision_cls=BaseDecisionEngine,
    advance_fn: Callable[..., tuple[dict, dict | None]] | None = None,
) -> dict[str, Any]:
    candle_dir = CORPUS / INSTRUMENT
    files = sorted(candle_dir.glob(f"{INSTRUMENT}_*.jsonl"))
    if not files:
        raise RuntimeError(
            f"no corpus files found in {candle_dir}; set AFS_122_CORPUS to the "
            "checkout containing replay_corpus_v1_market_condition_fixed"
        )

    log_dir.mkdir(parents=True, exist_ok=True)
    old_decision_cls = replay_module.DecisionEngine
    old_advance = signal_module.advance_strat_212_122
    replay_module.DecisionEngine = decision_cls
    if advance_fn is not None:
        signal_module.advance_strat_212_122 = advance_fn

    decisions: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    carry_history: list[dict[str, Any]] = []
    try:
        engine = ReplayEngine(config=config, log_dir=str(log_dir))
        for i, path in enumerate(files, 1):
            date_hint = path.stem.replace(f"{INSTRUMENT}_", "")
            engine.run(path, review_date=date_hint)

            carried = getattr(engine, "_carried_positions", {}) or {}
            if carried:
                carry_history.append(
                    {
                        "after_date": date_hint,
                        "positions": copy.deepcopy(carried),
                    }
                )
            if i % 50 == 0 or i == len(files):
                print(f"[{log_dir.name}] {i}/{len(files)} days", flush=True)
    finally:
        replay_module.DecisionEngine = old_decision_cls
        signal_module.advance_strat_212_122 = old_advance

    # Read the journals only AFTER every day has been replayed. ReplayEngine
    # writes a carried trade's OUTCOME row into the journal of its SIGNAL date
    # (replay/replay_engine.py, for_date=_carried["journal_date"]) when the
    # trade resolves on a later day file. Reading journal_{date} right after
    # engine.run(date) — inside the loop — therefore missed those late OUTCOME
    # rows and classified the trade TRADE_UNRESOLVED (same defect fixed in
    # scripts/mes_122_fallback_full_engine_proof.py, PR #537).
    for journal_path in sorted(log_dir.glob("journal_*.jsonl")):
        for entry in _json_lines(journal_path):
            if entry.get("bar_ts"):
                decisions[str(entry["bar_ts"])] = entry
            if entry.get("type") == "OUTCOME":
                outcome = entry.get("outcome") or {}
                order_id = outcome.get("paper_order_id")
                if order_id:
                    outcomes[str(order_id)] = outcome

    return {
        "decisions": decisions,
        "outcomes": outcomes,
        "carry_history": carry_history,
    }


def _resolved_strat122_rows(run: dict[str, Any]) -> tuple[list[dict], list[dict], list[dict]]:
    resolved: list[dict] = []
    unresolved: list[dict] = []
    cancelled: list[dict] = []
    for bar_ts, entry in sorted(run["decisions"].items()):
        setup = entry.get("setup") or {}
        if setup.get("strategy") != STRATEGY or entry.get("decision") != "TRADE":
            continue
        order_id = entry.get("paper_order_id")
        outcome = run["outcomes"].get(str(order_id)) if order_id else None
        base = {
            "bar_ts": bar_ts,
            "date": bar_ts[:10],
            "strategy": STRATEGY,
            "direction": setup.get("direction"),
            "entry": setup.get("entry"),
            "stop": setup.get("stop"),
            "target": setup.get("target"),
            "contracts": setup.get("contracts"),
            "paper_order_id": order_id,
        }
        if outcome is None:
            unresolved.append(base)
            continue
        result = outcome.get("result")
        row = {
            **base,
            "result": result,
            "exit_reason": outcome.get("exit_reason"),
            "pnl_dollars": float(outcome.get("pnl_dollars") or 0.0),
        }
        if result == "CANCELLED":
            cancelled.append(row)
        elif result in {"WIN", "LOSS", "BREAKEVEN"}:
            resolved.append(row)
        else:
            unresolved.append(row)
    return resolved, unresolved, cancelled


def _drawdown(pnls: list[float]) -> float:
    eq = peak = dd = 0.0
    for pnl in pnls:
        eq += pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return round(dd, 2)


def _metric_block(rows: list[dict]) -> dict[str, Any]:
    raw = [float(r["pnl_dollars"]) for r in rows]
    net = [p - COMMISSION_RT for p in raw]
    mid = len(rows) // 2

    def pf(values: list[float]):
        wins = sum(v for v in values if v > 0)
        losses = abs(sum(v for v in values if v < 0))
        return round(wins / losses, 6) if losses else None

    by_month: dict[str, list[float]] = defaultdict(list)
    for row, pnl in zip(rows, net):
        by_month[row["date"][:7]].append(pnl)

    return {
        "trades": len(rows),
        "wins": sum(1 for p in raw if p > 0),
        "losses": sum(1 for p in raw if p < 0),
        "breakeven": sum(1 for p in raw if p == 0),
        "raw_net": round(sum(raw), 2),
        "commission_adjusted_net": round(sum(net), 2),
        "raw_pf": pf(raw),
        "commission_adjusted_pf": pf(net),
        "h1_commission_adjusted": round(sum(net[:mid]), 2),
        "h2_commission_adjusted": round(sum(net[mid:]), 2),
        "max_drawdown_commission_adjusted": _drawdown(net),
        "months": {
            month: {
                "trades": len(values),
                "net": round(sum(values), 2),
            }
            for month, values in sorted(by_month.items())
        },
        "trade_dates": [r["date"] for r in rows],
    }


def _compare_months(base: dict, variant: dict) -> dict[str, Any]:
    months = sorted(set(base["months"]) | set(variant["months"]))
    delta = {}
    better = worse = same = 0
    for month in months:
        b = float(base["months"].get(month, {}).get("net", 0.0))
        v = float(variant["months"].get(month, {}).get("net", 0.0))
        d = round(v - b, 2)
        delta[month] = d
        if d > 0:
            better += 1
        elif d < 0:
            worse += 1
        else:
            same += 1
    return {
        "better_months": better,
        "worse_months": worse,
        "same_months": same,
        "monthly_net_delta": delta,
    }


def _trade_signature_map(run: dict[str, Any]) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for bar_ts, entry in run["decisions"].items():
        if entry.get("decision") != "TRADE":
            continue
        setup = entry.get("setup") or {}
        order_id = entry.get("paper_order_id")
        outcome = run["outcomes"].get(str(order_id)) if order_id else None
        out[bar_ts] = (
            setup.get("strategy"),
            setup.get("direction"),
            setup.get("contracts"),
            (outcome or {}).get("result"),
            round(float((outcome or {}).get("pnl_dollars") or 0.0), 6) if outcome else None,
        )
    return out


def _collateral_changes(control: dict[str, Any], treatment: dict[str, Any]) -> list[dict]:
    c = _trade_signature_map(control)
    t = _trade_signature_map(treatment)
    changes = []
    for bar_ts in sorted(set(c) | set(t)):
        if c.get(bar_ts) == t.get(bar_ts):
            continue
        c_strategy = c.get(bar_ts, (None,))[0]
        t_strategy = t.get(bar_ts, (None,))[0]
        if c_strategy != STRATEGY or t_strategy != STRATEGY:
            changes.append({"bar_ts": bar_ts, "control": c.get(bar_ts), "treatment": t.get(bar_ts)})
    return changes


def _summarize_run(run: dict[str, Any]) -> dict[str, Any]:
    resolved, unresolved, cancelled = _resolved_strat122_rows(run)
    metrics = _metric_block(resolved)
    return {
        "metrics": metrics,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "cancelled_count": len(cancelled),
        "cancelled": cancelled,
        "carry_day_count": len(run["carry_history"]),
        "first_carry": run["carry_history"][0] if run["carry_history"] else None,
        "last_carry": run["carry_history"][-1] if run["carry_history"] else None,
    }


def run(out_path: Path) -> dict[str, Any]:
    isolated_cfg = _fixed_one_contract_config(isolated=True)
    production_cfg = _fixed_one_contract_config(isolated=False)

    results: dict[str, Any] = {
        "design": {
            "instrument": INSTRUMENT,
            "strategy": STRATEGY,
            "corpus": str(CORPUS),
            "contracts": 1,
            "slippage_ticks": 1.0,
            "pessimistic_same_bar": True,
            "commission_round_trip": COMMISSION_RT,
            "mechanical_variants": list(VARIANTS),
            "filter_variant": "scoped_fallback",
        },
        "mechanical": {},
        "filter": {},
    }

    with tempfile.TemporaryDirectory(prefix="mes122_controlled_") as tmp:
        root = Path(tmp)

        runs: dict[str, dict[str, Any]] = {}
        for name in VARIANTS:
            print(f"[mechanical] {name}", flush=True)
            runs[name] = _run_pass(
                isolated_cfg,
                root / f"mechanical_{name}",
                advance_fn=_variant_advance(name),
            )
            results["mechanical"][name] = _summarize_run(runs[name])

        baseline_metrics = results["mechanical"]["baseline"]["metrics"]
        for name in VARIANTS[1:]:
            results["mechanical"][name]["vs_baseline_months"] = _compare_months(
                baseline_metrics,
                results["mechanical"][name]["metrics"],
            )
            results["mechanical"][name]["collateral_non_strat122_trade_changes"] = _collateral_changes(
                runs["baseline"], runs[name]
            )

        print("[filter] frozen #373 production control", flush=True)
        filter_control = _run_pass(
            production_cfg,
            root / "filter_control",
            decision_cls=BaseDecisionEngine,
            advance_fn=_variant_advance("baseline"),
        )
        print("[filter] scoped fallback", flush=True)
        filter_treatment = _run_pass(
            production_cfg,
            root / "filter_scoped_fallback",
            decision_cls=ScopedFallbackDecisionEngine,
            advance_fn=_variant_advance("baseline"),
        )
        results["filter"]["control"] = _summarize_run(filter_control)
        results["filter"]["scoped_fallback"] = _summarize_run(filter_treatment)
        results["filter"]["scoped_fallback"]["vs_control_months"] = _compare_months(
            results["filter"]["control"]["metrics"],
            results["filter"]["scoped_fallback"]["metrics"],
        )
        results["filter"]["scoped_fallback"]["collateral_non_strat122_trade_changes"] = _collateral_changes(
            filter_control, filter_treatment
        )

        results["filter"]["target_bars"] = {
            bar_ts: {
                "control": filter_control["decisions"].get(bar_ts),
                "treatment": filter_treatment["decisions"].get(bar_ts),
            }
            for bar_ts in TARGETS
        }

    results["screen"] = {}
    base = results["mechanical"]["baseline"]["metrics"]
    for name in VARIANTS[1:]:
        r = results["mechanical"][name]
        m = r["metrics"]
        month_cmp = r["vs_baseline_months"]
        results["screen"][name] = {
            "net_improved": m["commission_adjusted_net"] > base["commission_adjusted_net"],
            "pf_improved": (
                m["commission_adjusted_pf"] is not None
                and base["commission_adjusted_pf"] is not None
                and m["commission_adjusted_pf"] > base["commission_adjusted_pf"]
            ),
            "h1_positive": m["h1_commission_adjusted"] > 0,
            "h2_positive": m["h2_commission_adjusted"] > 0,
            "more_months_better_than_worse": month_cmp["better_months"] > month_cmp["worse_months"],
            "no_new_collateral_non_strat122_changes": not r["collateral_non_strat122_trade_changes"],
            "research_keep": bool(
                m["commission_adjusted_net"] > base["commission_adjusted_net"]
                and m["h1_commission_adjusted"] > 0
                and m["h2_commission_adjusted"] > 0
                and month_cmp["better_months"] > month_cmp["worse_months"]
            ),
        }

    filt = results["filter"]["scoped_fallback"]
    fbase = results["filter"]["control"]["metrics"]
    fm = filt["metrics"]
    fmonths = filt["vs_control_months"]
    results["screen"]["scoped_fallback"] = {
        "net_improved": fm["commission_adjusted_net"] > fbase["commission_adjusted_net"],
        "pf_improved": (
            fm["commission_adjusted_pf"] is not None
            and fbase["commission_adjusted_pf"] is not None
            and fm["commission_adjusted_pf"] > fbase["commission_adjusted_pf"]
        ),
        "h1_positive": fm["h1_commission_adjusted"] > 0,
        "h2_positive": fm["h2_commission_adjusted"] > 0,
        "more_months_better_than_worse": fmonths["better_months"] > fmonths["worse_months"],
        "collateral_non_strat122_trade_change_count": len(
            filt["collateral_non_strat122_trade_changes"]
        ),
        "research_keep": bool(
            fm["commission_adjusted_net"] > fbase["commission_adjusted_net"]
            and fm["h1_commission_adjusted"] > 0
            and fm["h2_commission_adjusted"] > 0
            and fmonths["better_months"] > fmonths["worse_months"]
        ),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")

    print(json.dumps({
        "mechanical": {
            name: results["mechanical"][name]["metrics"] for name in VARIANTS
        },
        "filter": {
            "control": results["filter"]["control"]["metrics"],
            "scoped_fallback": results["filter"]["scoped_fallback"]["metrics"],
        },
        "screen": results["screen"],
    }, indent=2))
    print(f"wrote {out_path}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts" / "mes_122_controlled_one_variable_results_2026-09-08.json",
    )
    args = parser.parse_args()
    run(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
