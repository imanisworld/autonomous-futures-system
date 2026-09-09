#!/usr/bin/env python3
"""MES 15m strat_122: TRUE per-leg adverse-slippage execution-realism gate.

Evidence only. No strategy/risk/runtime/config/deployment change. Nothing is tuned.

## The defect this measures

The existing "1/2/3 adverse tick" stress runs do NOT apply slippage to every leg of a
`strat_122` trade:

- **Entry leg, all trades.** `replay/replay_engine.py` does not route strat_212/122 through
  the broker's normal entry-fill machinery. Because the causal resolver only hands back a
  candidate once the watched bar has already shown the entry triggering, the engine calls
  `broker.restore_position(..., entry=decision.setup.entry, ...)` directly.
  `PaperBroker.restore_position()` records that price verbatim — `self._slippage_ticks` is
  never consulted. By contrast the broker's real stop-entry primitive
  (`_activate_pending_stop_entry`) DOES add adverse slippage to the entry and then
  re-validates the bracket against the slipped fill, cancelling with
  `ENTRY_BRACKET_INVALID_AT_FILL` when the slipped entry no longer sits inside stop/target.
- **Exit leg, same-bar pre-resolved trades.** When the armed entry boundary and its opposite
  (stop) boundary are both crossed on the single watched bar, the engine resolves via
  `broker.force_resolve(result, exit_price)` at the exact structural price.
  `force_resolve()` also never applies slippage.

Ordinary (non-pre-resolved) exits DO already get the broker's treatment inside
`resolve_position()`: a stop exit is a market order and is slipped; a target exit is a
resting LIMIT order and fills clean at its price, which is realistic — a limit order fills
at its limit or not at all.

## What this harness does

For each slippage level it monkeypatches the two slippage-free primitives — the same
technique the existing harnesses already use for `DecisionEngine`/`advance_strat_212_122` —
and then re-runs the FULL replay engine. It does not post-process a fixed trade list,
because a slipped entry can change the outcome, the exit timing, the account balance, and
therefore which later signals the risk gates admit. Second-order population change is
measured, not assumed.

Per leg, at N ticks:
1. entry fill = causal entry ± N ticks adverse (LONG +, SHORT −), applied AFTER the causal
   entry price is established, exactly like `_activate_pending_stop_entry`;
2. bracket re-validated against that slipped fill with the primitive's own rule
   (LONG `stop < fill < target`, SHORT `target < fill < stop`); failures become
   `ENTRY_BRACKET_INVALID_AT_FILL` CANCELLED outcomes and never become trades;
3. every exit that currently escapes slippage — the same-bar `force_resolve()` exits — is
   slipped N ticks adverse;
4. gap-aware stop fills, pessimistic same-bar handling, breakeven/runner off, fixed 1 MES,
   and $1.48 round-trip commission are all unchanged.

`--strict-target-exits` additionally slips resting-LIMIT target exits by N ticks. That is
NOT the headline variant (a limit order does not slip), but the review asked for "1 adverse
tick to every exit", so it is reported as a labelled sensitivity.

The `merged_baseline` level (no entry slippage, broker's own 1-tick market-exit slippage)
reproduces the merged 40-trade population exactly and is the diff baseline. An "N ticks per
leg" level moves BOTH the added entry/force_resolve slippage AND the broker's own
`fill_slippage_ticks` to N, so stop exits are stressed at N too rather than staying at 1.
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from execution.broker_interface import Fill  # noqa: E402
from execution.paper_broker import PaperBroker, NextBarOHLC, TICK_SIZE, TICK_VALUE  # noqa: E402
from scripts.mes_122_controlled_one_variable_tests import (  # noqa: E402
    COMMISSION_RT,
    _fixed_one_contract_config,
    _resolved_strat122_rows,
    _run_pass,
    _variant_advance,
)
from scripts.mes_122_fallback_full_engine_proof import CORPUS, INSTRUMENT, STRATEGY  # noqa: E402
from scripts.mes_122_mtm_drawdown import (  # noqa: E402
    STARTING_BALANCE,
    _load_master_bars,
    _parse,
    _spans_weekend,
    _unrealized_dollars,
)

BRACKET_INVALID = "ENTRY_BRACKET_INVALID_AT_FILL"


def _adverse_entry(direction: str, entry: float, slip: float) -> float:
    """Adverse entry fill: a LONG pays up, a SHORT sells down (mirrors
    PaperBroker._activate_pending_stop_entry)."""
    return entry + slip if direction == "LONG" else entry - slip


def _adverse_exit(direction: str, exit_price: float, slip: float) -> float:
    """Adverse exit fill: a LONG sells lower, a SHORT buys back higher."""
    return exit_price - slip if direction == "LONG" else exit_price + slip


def _bracket_valid(direction: str, fill: float, stop: float, target: float) -> bool:
    """PaperBroker._activate_pending_stop_entry's own post-slippage bracket rule."""
    if direction == "LONG":
        return stop < fill < target
    return target < fill < stop


@contextmanager
def per_leg_slippage(entry_slip_ticks: float, exit_slip_ticks: float,
                     *, strict_target_exits: bool = False):
    """Patch the two primitives that bypass configured slippage, for the duration of a run.

    `entry_slip_ticks` is applied once per trade to the causal entry; `exit_slip_ticks` is
    applied to same-bar `force_resolve()` exits (ordinary stop/gap exits already get the
    broker's own `_slippage_ticks`, which the caller sets to the same value via config).

    Restores the originals unconditionally, mirroring _run_pass's own monkeypatch discipline.
    """
    orig_restore = PaperBroker.restore_position
    orig_force = PaperBroker.force_resolve
    orig_resolve = PaperBroker.resolve_position
    # replay_engine.py restores a position a SECOND time when it carries across a day
    # boundary (the cross-day carry-forward block), passing the entry price it captured
    # from the broker -- which is already slipped. Slipping again would charge an extra
    # tick per overnight hold, so each order's entry is slipped exactly once.
    slipped_orders: set[str] = set()

    def restore_position(self, instrument, direction, entry, stop, target, contracts=1,
                         *, paper_order_id=None, runner_max_favorable=None):
        tick = TICK_SIZE.get(instrument, 0.25)
        key = str(paper_order_id) if paper_order_id is not None else None
        already = key is not None and key in slipped_orders
        filled = float(entry) if already else _adverse_entry(
            direction, float(entry), entry_slip_ticks * tick
        )
        if key is not None:
            slipped_orders.add(key)
        orig_restore(
            self, instrument, direction, filled, stop, target, contracts,
            paper_order_id=paper_order_id, runner_max_favorable=runner_max_favorable,
        )
        # Bracket re-validation against the slipped fill. A failure must not become a
        # trade; flag it so the next resolution call (or force_resolve) cancels it, which
        # is the path the engine already understands (fill.result == "CANCELLED").
        # A carry-forward restore re-checks an unchanged entry, so it never flips.
        self._slipgate_invalid = not _bracket_valid(direction, filled, float(stop), float(target))
        self._slipgate_requested_entry = float(entry)

    def _cancel_invalid(self) -> Fill:
        pos = self._position
        self._position = None
        self._slipgate_invalid = False
        return Fill(
            instrument=pos.instrument,
            direction=pos.direction,
            contracts=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=None,
            exit_reason=BRACKET_INVALID,
            result="CANCELLED",
            pnl_ticks=0.0,
            pnl_dollars=0.0,
            paper_order_id=self._active_order_id,
        )

    def resolve_position(self, next_bar):
        if getattr(self, "_slipgate_invalid", False) and self._position is not None:
            return _cancel_invalid(self)
        fill = orig_resolve(self, next_bar)
        if (
            strict_target_exits
            and fill is not None
            and fill.exit_reason == "TARGET_HIT"
            and exit_slip_ticks
        ):
            tick = TICK_SIZE.get(fill.instrument, 0.25)
            tick_val = TICK_VALUE.get(fill.instrument, 1.0)
            slipped = _adverse_exit(fill.direction, float(fill.exit_price), exit_slip_ticks * tick)
            if fill.direction == "LONG":
                pnl_ticks = (slipped - fill.entry_price) / tick
            else:
                pnl_ticks = (fill.entry_price - slipped) / tick
            pnl_dollars = pnl_ticks * tick_val * fill.contracts
            self._balance += pnl_dollars - float(fill.pnl_dollars or 0.0)
            fill.exit_price = round(slipped, 4)
            fill.pnl_ticks = round(pnl_ticks, 2)
            fill.pnl_dollars = round(pnl_dollars, 2)
            fill.result = "WIN" if pnl_dollars > 0 else "LOSS" if pnl_dollars < 0 else "BREAKEVEN"
        return fill

    def force_resolve(self, result, exit_price):
        if getattr(self, "_slipgate_invalid", False) and self._position is not None:
            return _cancel_invalid(self)
        if self._position is None:
            return orig_force(self, result, exit_price)
        tick = TICK_SIZE.get(self._position.instrument, 0.25)
        slipped = _adverse_exit(self._position.direction, float(exit_price), exit_slip_ticks * tick)
        fill = orig_force(self, result, slipped)
        # force_resolve books the caller's asserted result; with adverse slippage a
        # scratch can cross zero, so re-derive the result from realised P&L.
        if fill is not None:
            pnl = float(fill.pnl_dollars or 0.0)
            fill.result = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"
        return fill

    PaperBroker.restore_position = restore_position
    PaperBroker.force_resolve = force_resolve
    PaperBroker.resolve_position = resolve_position
    try:
        yield
    finally:
        PaperBroker.restore_position = orig_restore
        PaperBroker.force_resolve = orig_force
        PaperBroker.resolve_position = orig_resolve


def _isolated_cfg(exit_slip_ticks: float):
    """The exact config behind the merged 40-trade population, with the broker's own
    exit slippage set to the level under test.

    `fill_slippage_ticks` is what `resolve_position()` uses for ordinary market exits
    (stop, gap, runner). The merged 40-trade baseline pins it at 1.0; a genuine "N ticks
    per leg" run has to move it to N as well, otherwise the entry leg would be stressed
    at N ticks while every stop exit stayed at 1.
    """
    return dataclasses.replace(
        _fixed_one_contract_config(isolated=True),
        enabled_concepts=[STRATEGY],
        fill_slippage_ticks=float(exit_slip_ticks),
    )


def _decision_census(run: dict[str, Any]) -> dict[str, int]:
    census: dict[str, int] = {}
    for _, entry in run["decisions"].items():
        setup = entry.get("setup") or {}
        if setup.get("strategy") != STRATEGY:
            continue
        key = str(entry.get("decision"))
        census[key] = census.get(key, 0) + 1
    return census


def _metrics(rows: list[dict]) -> dict[str, Any]:
    pnls = [float(r["pnl_dollars"]) - COMMISSION_RT for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    mid = len(pnls) // 2
    eq = peak = dd = 0.0
    for p in pnls:
        eq += p
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {
        "trades": len(rows),
        "wins": len([r for r in rows if r["result"] == "WIN"]),
        "losses": len([r for r in rows if r["result"] == "LOSS"]),
        "breakeven": len([r for r in rows if r["result"] == "BREAKEVEN"]),
        "net_after_commission": round(sum(pnls), 2),
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss else None,
        "h1_after_commission": round(sum(pnls[:mid]), 2),
        "h2_after_commission": round(sum(pnls[mid:]), 2),
        "closed_trade_max_drawdown": round(dd, 2),
    }


def _mtm_walk(run: dict[str, Any], rows: list[dict], bars: list[dict],
              ts_index: dict[str, int], entry_slip_ticks: float,
              added_exit_slip_ticks: float, broker_exit_slip_ticks: float,
              strict_target_exits: bool) -> dict[str, Any]:
    """Bar-by-bar MTM equity curve for this variant, under the same patched primitives."""
    tick = TICK_SIZE[INSTRUMENT]
    tick_val = TICK_VALUE[INSTRUMENT]
    balance = peak = STARTING_BALANCE
    max_dd = 0.0
    max_dd_point: dict[str, Any] | None = None
    worst_mae = 0.0
    worst_mae_info: dict[str, Any] | None = None
    longest_hold = 0.0
    longest_info: dict[str, Any] | None = None
    overnight = weekend = 0
    mismatches: list[dict] = []

    with per_leg_slippage(entry_slip_ticks, added_exit_slip_ticks,
                          strict_target_exits=strict_target_exits):
        broker = PaperBroker(
            starting_balance=STARTING_BALANCE, slippage_ticks=broker_exit_slip_ticks,
            pessimistic_both_hit=True, breakeven_at_1r=False, runner_mode=False,
            entry_fill_model="market",
        )
        for i, trade in enumerate(rows):
            bar_ts = trade["bar_ts"]
            direction = trade["direction"]
            entry_req = float(trade["entry"])
            entry_filled = _adverse_entry(direction, entry_req, entry_slip_ticks * tick)
            outcome = run["outcomes"].get(str(trade["paper_order_id"])) or {}
            pre_resolved = (
                (outcome.get("execution_audit") or {}).get("source")
                == "strat_212_122_same_bar_resolution"
            )
            idx = ts_index.get(bar_ts)
            if idx is None:
                raise RuntimeError(f"signal bar {bar_ts} missing from corpus")

            before = balance
            raw_pnl = round(float(trade["pnl_dollars"]), 2)
            mae = 0.0
            mae_ts = None

            if pre_resolved:
                bar = bars[idx]
                adverse = bar["low"] if direction == "LONG" else bar["high"]
                mae = min(_unrealized_dollars(direction, entry_filled, adverse, tick, tick_val),
                          raw_pnl, 0.0)
                mae_ts = bar_ts
                exit_ts = bar_ts
            else:
                broker.restore_position(
                    instrument=INSTRUMENT, direction=direction, entry=entry_req,
                    stop=float(trade["stop"]), target=float(trade["target"]),
                    contracts=1, paper_order_id=f"SLIP-{i}",
                )
                fill = None
                exit_bar = None
                j = idx + 1
                while j < len(bars):
                    bar = bars[j]
                    adverse = bar["low"] if direction == "LONG" else bar["high"]
                    adv = _unrealized_dollars(direction, entry_filled, adverse, tick, tick_val)
                    if adv < mae:
                        mae, mae_ts = adv, bar["timestamp"]
                    fill = broker.resolve_position(
                        NextBarOHLC(open=bar["open"], high=bar["high"], low=bar["low"])
                    )
                    if fill is not None:
                        exit_bar = bar
                        break
                    close_unreal = _unrealized_dollars(direction, entry_filled, bar["close"], tick, tick_val)
                    eq = before + close_unreal
                    peak = max(peak, eq)
                    if peak - eq > max_dd:
                        max_dd = peak - eq
                        max_dd_point = {"ts": bar["timestamp"], "equity": round(eq, 2),
                                        "peak": round(peak, 2), "drawdown": round(peak - eq, 2),
                                        "trade_date": bar_ts[:10], "kind": "mark"}
                    j += 1
                if fill is None or exit_bar is None:
                    raise RuntimeError(f"trade {bar_ts} never resolved in the corpus walk")
                if round(fill.pnl_dollars, 2) != raw_pnl or fill.result != trade["result"]:
                    mismatches.append({
                        "bar_ts": bar_ts, "engine": [trade["result"], raw_pnl],
                        "walk": [fill.result, round(fill.pnl_dollars, 2)],
                    })
                exit_ts = exit_bar["timestamp"]

            balance = round(before + raw_pnl - COMMISSION_RT, 2)
            peak = max(peak, balance)
            if peak - balance > max_dd:
                max_dd = peak - balance
                max_dd_point = {"ts": exit_ts, "equity": balance, "peak": round(peak, 2),
                                "drawdown": round(peak - balance, 2),
                                "trade_date": bar_ts[:10], "kind": "close"}
            if mae < worst_mae:
                worst_mae = mae
                worst_mae_info = {"trade_date": bar_ts[:10], "direction": direction,
                                  "mae_dollars": round(mae, 2), "mae_ts": mae_ts}
            entry_dt, exit_dt = _parse(bar_ts), _parse(exit_ts)
            hold = round((exit_dt - entry_dt).total_seconds() / 3600.0, 2)
            if exit_dt.date() != entry_dt.date():
                overnight += 1
                if _spans_weekend(entry_dt.date(), exit_dt.date()):
                    weekend += 1
            if hold > longest_hold:
                longest_hold = hold
                longest_info = {"trade_date": bar_ts[:10], "hold_hours": hold, "exit_ts": exit_ts}

    return {
        "max_mtm_drawdown": round(max_dd, 2),
        "max_mtm_drawdown_point": max_dd_point,
        "worst_per_trade_mae": worst_mae_info,
        "longest_hold": longest_info,
        "overnight_holds": overnight,
        "weekend_holds": weekend,
        "final_balance": balance,
        "walk_vs_engine_mismatches": mismatches,
    }


def run_level(entry_slip_ticks: float, added_exit_slip_ticks: float,
              broker_exit_slip_ticks: float, bars, ts_index, *,
              label: str, strict_target_exits: bool) -> dict[str, Any]:
    """`added_exit_slip_ticks` is the slippage this harness ADDS to the same-bar
    `force_resolve()` exits that currently get none. `broker_exit_slip_ticks` is the
    broker's own `fill_slippage_ticks`, which already covers ordinary market (stop/gap)
    exits. The merged baseline is (0, 0, 1.0); an N-per-leg level is (N, N, N)."""
    print(f"[{label}] replaying 313 days (entry {entry_slip_ticks}t / "
          f"same-bar exit +{added_exit_slip_ticks}t / broker exit {broker_exit_slip_ticks}t)",
          flush=True)
    with per_leg_slippage(entry_slip_ticks, added_exit_slip_ticks,
                          strict_target_exits=strict_target_exits):
        with tempfile.TemporaryDirectory(prefix=f"mes122_slip_{label}_") as tmp:
            run = _run_pass(_isolated_cfg(broker_exit_slip_ticks), Path(tmp) / label,
                            advance_fn=_variant_advance("baseline"))

    resolved, unresolved, cancelled = _resolved_strat122_rows(run)
    resolved = sorted(resolved, key=lambda r: r["bar_ts"])
    bracket_invalid = [c for c in cancelled if c.get("exit_reason") == BRACKET_INVALID]
    other_cancelled = [c for c in cancelled if c.get("exit_reason") != BRACKET_INVALID]

    result = {
        "entry_slippage_ticks": entry_slip_ticks,
        "added_same_bar_exit_slippage_ticks": added_exit_slip_ticks,
        "broker_market_exit_slippage_ticks": broker_exit_slip_ticks,
        "strict_target_exits": strict_target_exits,
        "decision_census": _decision_census(run),
        "candidates_trade_decisions": len(resolved) + len(unresolved) + len(cancelled),
        "fills": len(resolved),
        "bracket_invalid_at_fill": len(bracket_invalid),
        "other_cancelled": len(other_cancelled),
        "unresolved_at_corpus_end": len(unresolved),
        "metrics": _metrics(resolved),
        "mtm": _mtm_walk(run, resolved, bars, ts_index, entry_slip_ticks,
                         added_exit_slip_ticks, broker_exit_slip_ticks, strict_target_exits),
        "rows": [
            {"bar_ts": r["bar_ts"], "direction": r["direction"], "entry_requested": r["entry"],
             "stop": r["stop"], "target": r["target"], "result": r["result"],
             "exit_reason": r["exit_reason"], "pnl_dollars": round(float(r["pnl_dollars"]), 2)}
            for r in resolved
        ],
        "bracket_invalid_rows": [
            {"bar_ts": c["bar_ts"], "direction": c["direction"], "entry_requested": c["entry"],
             "stop": c["stop"], "target": c["target"]}
            for c in bracket_invalid
        ],
    }
    m = result["metrics"]
    mism = result["mtm"]["walk_vs_engine_mismatches"]
    print(f"[{label}] fills={result['fills']} invalid={result['bracket_invalid_at_fill']} "
          f"{m['wins']}W/{m['losses']}L net={m['net_after_commission']} pf={m['profit_factor']} "
          f"mtmDD={result['mtm']['max_mtm_drawdown']}"
          + (f"  ⚠ walk/engine mismatches: {len(mism)}" if mism else ""), flush=True)
    return result


def _diff_vs_baseline(base: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    b = {r["bar_ts"]: r for r in base["rows"]}
    v = {r["bar_ts"]: r for r in variant["rows"]}
    disappeared, changed, appeared = [], [], []
    invalid_ts = {r["bar_ts"] for r in variant["bracket_invalid_rows"]}
    for ts in sorted(set(b) - set(v)):
        disappeared.append({
            "bar_ts": ts, "baseline": b[ts],
            "why": "ENTRY_BRACKET_INVALID_AT_FILL" if ts in invalid_ts else "no longer a resolved trade",
        })
    for ts in sorted(set(v) - set(b)):
        appeared.append({"bar_ts": ts, "variant": v[ts]})
    for ts in sorted(set(b) & set(v)):
        if (b[ts]["result"] != v[ts]["result"]
                or b[ts]["pnl_dollars"] != v[ts]["pnl_dollars"]
                or b[ts]["exit_reason"] != v[ts]["exit_reason"]):
            changed.append({
                "bar_ts": ts,
                "baseline": {k: b[ts][k] for k in ("result", "exit_reason", "pnl_dollars")},
                "variant": {k: v[ts][k] for k in ("result", "exit_reason", "pnl_dollars")},
                "delta_dollars": round(v[ts]["pnl_dollars"] - b[ts]["pnl_dollars"], 2),
            })
    return {"disappeared": disappeared, "appeared": appeared, "changed": changed,
            "counts": {"disappeared": len(disappeared), "appeared": len(appeared),
                       "changed": len(changed)}}


def main(out_path: Path, strict: bool) -> dict[str, Any]:
    bars = _load_master_bars()
    ts_index = {b["timestamp"]: i for i, b in enumerate(bars)}

    levels = {}
    # Diff baseline = the merged 40-trade population exactly as published (PR #547):
    # NO added entry slippage, NO added same-bar exit slippage, and the broker's own
    # 1-tick slippage on ordinary market exits.
    baseline = run_level(0, 0, 1.0, bars, ts_index, label="merged_baseline",
                         strict_target_exits=False)
    levels["merged_baseline"] = baseline
    bm = baseline["metrics"]
    expected = {"trades": 40, "wins": 11, "losses": 29, "net_after_commission": 90.80,
                "closed_trade_max_drawdown": 219.11}
    actual = {k: bm[k] for k in expected}
    if actual != expected or baseline["mtm"]["max_mtm_drawdown"] != 219.11:
        raise RuntimeError(
            "the control must reproduce the merged PR #547 population exactly.\n"
            f"  expected {expected} + mtmDD 219.11\n"
            f"  got      {actual} + mtmDD {baseline['mtm']['max_mtm_drawdown']}"
        )
    for n in (1, 2, 3):
        levels[f"{n}t_per_leg"] = run_level(n, n, n, bars, ts_index, label=f"{n}t_per_leg",
                                            strict_target_exits=False)
    if strict:
        for n in (1, 2, 3):
            levels[f"{n}t_per_leg_strict_target_exits"] = run_level(
                n, n, n, bars, ts_index, label=f"{n}t_strict", strict_target_exits=True
            )

    report = {
        "design": {
            "instrument": INSTRUMENT, "strategy": STRATEGY, "corpus": str(CORPUS),
            "contracts": 1, "starting_balance": STARTING_BALANCE,
            "commission_round_trip": COMMISSION_RT,
            "pessimistic_same_bar": True, "gap_aware_stop_fills": True,
            "breakeven_at_1r": False, "runner_mode": False,
            "entry_slippage": "N ticks adverse applied to the causal entry, then the bracket "
                              "re-validated with PaperBroker._activate_pending_stop_entry's own rule",
            "exit_slippage": "N ticks adverse on same-bar force_resolve() exits; ordinary stop "
                             "exits keep the broker's existing market-order slippage; resting "
                             "LIMIT target exits fill clean unless strict_target_exits",
            "strategy_parameters_changed": False,
            "evidence_only": True,
        },
        "levels": levels,
        "diffs_vs_merged_baseline": {
            key: _diff_vs_baseline(baseline, lv)
            for key, lv in levels.items() if key != "merged_baseline"
        },
    }
    out_path.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path,
                    default=REPO / "scripts" / "mes_122_per_leg_slippage_gate_2026-09-09.json")
    ap.add_argument("--strict-target-exits", action="store_true",
                    help="also run 1/2/3t variants that slip resting-LIMIT target exits")
    args = ap.parse_args()
    rep = main(args.out, args.strict_target_exits)
    print("\n=== SUMMARY ===")
    for key, lv in rep["levels"].items():
        m, mt = lv["metrics"], lv["mtm"]
        print(f"{key:26s} cand={lv['candidates_trade_decisions']:3d} fills={lv['fills']:3d} "
              f"invalid={lv['bracket_invalid_at_fill']:2d} {m['wins']:2d}W/{m['losses']:2d}L "
              f"net={m['net_after_commission']:9.2f} pf={m['profit_factor']} "
              f"H1={m['h1_after_commission']:8.2f} H2={m['h2_after_commission']:8.2f} "
              f"closedDD={m['closed_trade_max_drawdown']:7.2f} mtmDD={mt['max_mtm_drawdown']:7.2f}")
    print(f"\nwrote {args.out}")
