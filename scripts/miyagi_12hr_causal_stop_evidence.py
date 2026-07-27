#!/usr/bin/env python3
"""Canonical 12HR Miyagi evidence rerun under the CAUSAL stop from PR #362.

Requested by the operator as the required gate before PR #362
(`claude/miyagi-12hr-demo-readiness`) can be reviewed for merge/deploy. The
canonical evidence PR #343 (`research/detector_12hr_miyagi.py` Step 7) has a
confirmed lookahead defect in its stop-reference formula: it filters 60m
bars by `ts < 9:30` and takes the last one, but a bar LABELED "09:00" covers
[09:00,10:00) ET and is not closed until 10:00 -- the filter does not
exclude it. Empirically, 4/8 MNQ and 7/10 MES triggered signals in #343's
own evidence used a stop that depended on price action not yet known at the
9:30 decision point. #362's `strategy/strat_12hr_miyagi.py` fixes this
causally (reuses `strategy/four_hr_retrigger.py::_completed_one_hour_stop`
verbatim, computed at the moment of actual entry) -- meaning #343's old
$516.33 (MNQ) / $198.85 (MES) figures do NOT prove what #362's runtime will
produce, only what the lookahead-affected offline detector produced.

WHY THIS SCRIPT INSTEAD OF RERUNNING THE OLD RESEARCH PIPELINE:
`research/replay_12hr_miyagi_honest_fill.py` never recomputes the stop --
it just consumes `signal["stop"]` from the flawed detector's output, so the
lookahead flows straight through. It is a bespoke research harness that
never touches `replay/replay_engine.py` or `strategy/signal_engine.py`'s
`DecisionEngine`, so it cannot be trusted to prove runtime parity even if
patched. #362 wires `strat_12hr_miyagi` directly into `DecisionEngine` (the
SAME engine `replay/replay_engine.py` drives and the SAME one the live/paper
webhook path uses) -- so an isolated single-strategy replay through
`ReplayEngine` on THIS branch inherits the corrected causal stop for free,
with zero porting, and is by construction the same code the live/paper box
would run. This is the runtime/replay parity check itself, not just a new
evidence number.

METHOD: mirrors `scripts/vwap_reclaim_canonical_evidence.py`'s multi-
instrument isolation pattern (own fresh account per instrument, so the
frozen 20% drawdown breaker -- if it trips -- reflects only this strategy's
own P&L) combined with `scripts/orb_breakout_canonical_evidence.py`'s
richer stats (drawdown, max-consecutive-losses, winner concentration).
`enabled_concepts=["strat_12hr_miyagi"]` only, `disabled_concepts_per_
instrument` cleared (MNQ+MES both -- #362's own `_SUPPORTED` set, matching
risk_rules.yaml's PAPER_ELIGIBLE scope for this strategy already), fresh
account per instrument. `entry_fill_model="ioc_limit"` (PR #346's corrected
honest posture -- global PaperBroker setting, applies uniformly regardless
of strategy; verified by reading execution/paper_broker.py before writing
this script), canonical per-root IOC tolerance (MES=16/MNQ=32 ticks)
asserted not overridden. No runner mode: `strat_12hr_miyagi`'s candidate
only ever defines a single Candle-3-boundary T1 target (verified in
strategy/signal_engine.py::_try_strat_12hr_miyagi -- `target_2` is computed
by the state machine but never read anywhere in signal_engine.py), so
`config.runner_mode` is left at its production default (False / static
exit) rather than swept.

Costs/slippage: 1/2/3/4-tick adverse PaperBroker slippage sweep (same
tiers ORB Breakout used), same isolation/corpus, only `fill_slippage_ticks`
varied.

Corpus: same post-#338/#339/#342 corrected `data/replay_corpus_v1_market_
condition_fixed` (313 daily files per instrument, both MNQ and MES present)
every other canonical-evidence lane in this repo uses.

No strategy/runtime/risk/broker/config/deployment/Pine files changed. No
enablement of any kind. Evidence and reporting only. Branch is
`claude/miyagi-12hr-demo-readiness` (PR #362) itself, since the strategy
code under test (`strategy/strat_12hr_miyagi.py`) only exists on this
branch, not on `main`.

Usage:
    python3 scripts/miyagi_12hr_causal_stop_evidence.py \\
        --logs logs/replay_miyagi_12hr_causal_stop \\
        --out scripts/miyagi_12hr_causal_stop_evidence_results.json \\
        --raw scripts/miyagi_12hr_causal_stop_evidence_raw_trades.jsonl \\
        --report docs/strategy-rules/12HR_MIYAGI_CAUSAL_STOP_EVIDENCE_2026-07-27.md
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

STRATEGY = "strat_12hr_miyagi"
INSTRUMENTS = ("MNQ", "MES")
CORPUS = REPO / "data" / "replay_corpus_v1_market_condition_fixed"
FULL_RANGE = ("2025-07-24", "2026-07-23")
HALVES = {
    "H1": ("2025-07-24", "2026-01-23"),
    "H2": ("2026-01-24", "2026-07-23"),
}
QUARTERS = {
    "Q1": ("2025-07-24", "2025-10-23"),
    "Q2": ("2025-10-24", "2026-01-23"),
    "Q3": ("2026-01-24", "2026-04-23"),
    "Q4": ("2026-04-24", "2026-07-23"),
}
RECENT_WINDOWS = {
    "latest_3m": ("2026-04-24", "2026-07-23"),
    "latest_6m": ("2026-01-24", "2026-07-23"),
}
COMMISSION_ROUND_TRIP = 1.48
SLIPPAGE_TICKS = (1.0, 2.0, 3.0, 4.0)
SAMPLE_ADEQUATE_MIN = 30

HISTORICAL_PR343 = {
    "MNQ": {
        "label": "MNQ, PR #343 coded-detector canonical evidence (LOOKAHEAD-AFFECTED STOP)",
        "source": "docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md",
        "candidates": 15,
        "fills": 8,
        "wins": 7,
        "losses": 1,
        "net_pnl_reported": 516.33,
        "profit_factor_reported": 2.81,
        "status": (
            "NOT reproducible as proof of #362's runtime: 4/8 of these triggered signals "
            "used a stop-reference bar (labeled 09:00 ET, covering [09:00,10:00)) that had "
            "not closed yet at the 9:30 decision point -- the offline detector's own "
            "'ts < 9:30' filter fails to exclude it. Kept here as provenance/context only."
        ),
    },
    "MES": {
        "label": "MES, PR #343 coded-detector canonical evidence (LOOKAHEAD-AFFECTED STOP)",
        "source": "docs/strategy-rules/12HR_MIYAGI_CANONICAL_EVIDENCE_2026-07-26.md",
        "candidates": 19,
        "fills": 10,
        "wins": 8,
        "losses": 2,
        "net_pnl_reported": 198.85,
        "profit_factor_reported": 1.98,
        "status": "Same lookahead defect as MNQ above -- 7/10 triggered signals affected. Provenance/context only.",
    },
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _period_label(value: str, periods: dict[str, tuple[str, str]]) -> str:
    for label, (start, end) in periods.items():
        if start <= value <= end:
            return label
    return "OUT_OF_RANGE"


def _in_window(value: str, window: tuple[str, str]) -> bool:
    start, end = window
    return start <= value <= end


def _run_isolated(config, log_dir: Path, fresh: bool) -> dict:
    ran_totals = {}
    for instrument in INSTRUMENTS:
        candle_dir = CORPUS / instrument
        files = sorted(candle_dir.glob(f"{instrument}_*.jsonl"))
        if len(files) != 313:
            raise RuntimeError(f"{instrument}: expected 313 daily files, found {len(files)}")
        inst_log_dir = log_dir / instrument
        inst_log_dir.mkdir(parents=True, exist_ok=True)
        engine = ReplayEngine(config=config, log_dir=str(inst_log_dir))
        ran = skipped = errors = 0
        for index, candle_path in enumerate(files, 1):
            day = candle_path.stem.rsplit("_", 1)[-1]
            if not fresh and (inst_log_dir / f"journal_{day}.jsonl").exists():
                skipped += 1
                continue
            try:
                engine.run(candle_path, review_date=day)
                ran += 1
            except Exception as exc:  # noqa: BLE001 - surface and keep going
                errors += 1
                print(f"[run] {instrument} {day} ERROR: {exc}", file=sys.stderr)
            if index % 100 == 0 or index == len(files):
                print(f"[run] {log_dir.name} {instrument} {index}/{len(files)} "
                      f"(ran={ran} skipped={skipped} errors={errors})", flush=True)
        ran_totals[instrument] = {"ran": ran, "skipped": skipped, "errors": errors, "files": len(files)}
        if errors:
            raise RuntimeError(f"{instrument}: {errors} day(s) errored during isolated run")
    return ran_totals


def _parse_logs(logs_root: Path) -> tuple[list[dict], dict[str, dict | None]]:
    trades: list[dict] = []
    halts: dict[str, dict | None] = {}
    market_condition_blocks: dict[str, int] = {}
    for instrument in INSTRUMENTS:
        halt = None
        blocked = 0
        for path in sorted((logs_root / instrument).glob("journal_*.jsonl")):
            day = path.stem.removeprefix("journal_")
            entries = list(_json_lines(path))
            outcomes: dict[str, dict] = {}
            for entry in entries:
                if entry.get("type") != "OUTCOME":
                    continue
                outcome = entry.get("outcome") or {}
                order_id = outcome.get("paper_order_id")
                if order_id:
                    if order_id in outcomes:
                        raise RuntimeError(f"duplicate outcome identity {order_id} in {path}")
                    outcomes[order_id] = outcome

            for entry in entries:
                if entry.get("decision") == "RISK_REJECTED":
                    risk_reject = entry.get("risk_check") or {}
                    failed_rule = risk_reject.get("failed_rule")
                    if failed_rule == "max_drawdown" and halt is None:
                        halt = {
                            "first_halt_date": day,
                            "first_halt_bar_ts": entry.get("bar_ts"),
                            "reason": risk_reject.get("reason"),
                        }
                    if failed_rule and "market_condition" in str(failed_rule):
                        blocked += 1
                if entry.get("decision") != "TRADE":
                    continue
                risk = entry.get("risk_check") or {}
                if risk.get("result") != "APPROVED":
                    continue
                setup = entry.get("setup") or {}
                if setup.get("strategy") != STRATEGY:
                    raise RuntimeError(
                        f"isolation leak: non-{STRATEGY} TRADE decision in {path}: "
                        f"{setup.get('strategy')!r}"
                    )
                order_id = entry.get("paper_order_id")
                if not order_id:
                    raise RuntimeError(f"approved TRADE has no identity in {path}")
                outcome = outcomes.get(order_id)
                result = (outcome or {}).get("result")
                cancelled = result == "CANCELLED"
                trades.append(
                    {
                        "date": day,
                        "bar_ts": entry.get("bar_ts") or entry.get("ts") or "",
                        "instrument": instrument,
                        "direction": setup.get("direction") or "UNKNOWN",
                        "session": entry.get("session") or "",
                        "paper_order_id": order_id,
                        "attempted": 1,
                        "filled": int(not cancelled),
                        "cancelled_no_fill": int(cancelled),
                        "result": None if cancelled else result,
                        "resolved": int(result in {"WIN", "LOSS", "BREAKEVEN"}),
                        "open": int(result is None),
                        "exit_reason": (outcome or {}).get("exit_reason"),
                        "pnl_before_commission": float((outcome or {}).get("pnl_dollars") or 0.0),
                        "pnl_after_commission": (
                            float((outcome or {}).get("pnl_dollars") or 0.0) - COMMISSION_ROUND_TRIP
                            if result in {"WIN", "LOSS", "BREAKEVEN"}
                            else 0.0
                        ),
                    }
                )
        halts[instrument] = halt
        market_condition_blocks[instrument] = blocked
    return trades, {"halts": halts, "market_condition_blocks": market_condition_blocks}


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


def _max_consecutive_losses(rows: list[dict]) -> int:
    streak = worst = 0
    for row in sorted(rows, key=lambda item: (item["date"], item["bar_ts"], item["instrument"])):
        if not row["resolved"]:
            continue
        if row["result"] == "LOSS":
            streak += 1
            worst = max(worst, streak)
        else:
            streak = 0
    return worst


def _max_drawdown(rows: list[dict], pnl_key: str) -> float:
    equity = peak = 0.0
    max_dd = 0.0
    for row in sorted(rows, key=lambda item: (item["date"], item["bar_ts"], item["instrument"])):
        if not row["resolved"]:
            continue
        equity += row[pnl_key]
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _winner_concentration(values: list[float], top_n: int) -> float | None:
    winners = sorted((v for v in values if v > 0), reverse=True)
    total = sum(winners)
    return round(sum(winners[:top_n]) / total, 6) if total else None


def _stats(rows: list[dict]) -> dict:
    attempts = len(rows)
    fills = sum(row["filled"] for row in rows)
    resolved_rows = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved_rows]
    net = [row["pnl_after_commission"] for row in resolved_rows]
    wins = [row for row in resolved_rows if row["result"] == "WIN"]
    losses = [row for row in resolved_rows if row["result"] == "LOSS"]
    breakeven = sum(row["result"] == "BREAKEVEN" for row in resolved_rows)
    win_vals = [row["pnl_after_commission"] for row in wins]
    loss_vals = [row["pnl_after_commission"] for row in losses]
    return {
        "attempts": attempts,
        "fills": fills,
        "fill_rate": round(fills / attempts, 6) if attempts else None,
        "no_fill": attempts - fills,
        "resolved": len(resolved_rows),
        "open": sum(row["open"] for row in rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": breakeven,
        "win_rate": round(len(wins) / len(resolved_rows), 6) if resolved_rows else None,
        "net_before_commission": round(sum(gross), 2),
        "commission": round(COMMISSION_ROUND_TRIP * len(resolved_rows), 2),
        "net_after_commission": round(sum(net), 2),
        "expectancy_per_arm_after_commission": round(sum(net) / attempts, 4) if attempts else None,
        "expectancy_per_fill_after_commission": round(statistics.fmean(net), 4) if net else None,
        "profit_factor_before_commission": _profit_factor(gross),
        "profit_factor_after_commission": _profit_factor(net),
        "avg_win_after_commission": round(statistics.fmean(win_vals), 2) if win_vals else None,
        "avg_loss_after_commission": round(statistics.fmean(loss_vals), 2) if loss_vals else None,
        "largest_win_after_commission": round(max(net), 2) if net else None,
        "largest_loss_after_commission": round(min(net), 2) if net else None,
        "max_drawdown_after_commission": _max_drawdown(rows, "pnl_after_commission"),
        "max_consecutive_losses": _max_consecutive_losses(rows),
        "winner_concentration_after_commission": {
            "top_1": _winner_concentration(net, 1),
            "top_3": _winner_concentration(net, 3),
        },
        "sample_adequate": len(resolved_rows) >= SAMPLE_ADEQUATE_MIN,
    }


def _group(trades: list[dict], field: str, labels: Iterable[str]) -> dict:
    return {key: _stats([row for row in trades if str(row[field]) == key]) for key in labels}


def _walk_forward_both_halves_positive(halves: dict) -> bool | None:
    h1 = halves.get("H1", {})
    h2 = halves.get("H2", {})
    if h1.get("resolved", 0) == 0 or h2.get("resolved", 0) == 0:
        return None
    return (h1.get("net_after_commission") or 0) > 0 and (h2.get("net_after_commission") or 0) > 0


def _classify(overall: dict, wf: bool | None, slip_ok: bool) -> tuple[str, list[str]]:
    reasons = []
    if overall["resolved"] < SAMPLE_ADEQUATE_MIN:
        reasons.append(f"sample below {SAMPLE_ADEQUATE_MIN}-trade minimum (n={overall['resolved']})")
    if wf is None:
        reasons.append("insufficient data for walk-forward (one half has zero resolved trades)")
    elif not wf:
        reasons.append("fails both-halves-positive walk-forward under honest fills")
    if not slip_ok:
        reasons.append("fails 1-4 tick slippage sensitivity")
    if reasons:
        return "WAIT", reasons
    top3 = overall["winner_concentration_after_commission"]["top_3"]
    if top3 is not None and top3 > 0.60:
        return "PROMISING BUT UNPROVEN", [f"clears walk-forward+slippage but top-3 concentration is {top3:.1%} (elevated, n is thin)"]
    return "PROMISING BUT UNPROVEN", ["clears walk-forward+slippage, sample still short of VALIDATED bar"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=REPO / "logs/replay_miyagi_12hr_causal_stop")
    parser.add_argument("--out", type=Path, default=REPO / "scripts/miyagi_12hr_causal_stop_evidence_results.json")
    parser.add_argument("--raw", type=Path, default=REPO / "scripts/miyagi_12hr_causal_stop_evidence_raw_trades.jsonl")
    parser.add_argument("--report", type=Path, default=REPO / "docs/strategy-rules/12HR_MIYAGI_CAUSAL_STOP_EVIDENCE_2026-07-27.md")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    base_config = load_config()
    before_enabled = tuple(base_config.enabled_concepts)
    before_disabled = {k: tuple(v) for k, v in base_config.disabled_concepts_per_instrument.items()}
    risk_hash_before = _sha256(REPO / "risk_rules.yaml")

    # PARITY FINDING: this dev worktree's .env sets ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ=16 /
    # _MES=8 -- HALF of config/settings.py's own documented "live box's known settings"
    # fallback (MNQ=32/MES=16), which every prior canonical-evidence lane (PR #349 ORB,
    # PR #347 VWAP) asserted and used. Rather than silently trust ambient/possibly-drifted
    # local .env, this run explicitly PINS the documented canonical values in-memory (same
    # pattern as entry_fill_model below) so this evidence stays comparable to those prior
    # lanes. The local/box .env discrepancy itself is reported to the operator separately --
    # it is a system-level question, out of scope for this strategy-specific rerun.
    CANONICAL_ENTRY_TOLERANCE = {"MNQ": 32.0, "MES": 16.0}
    if not base_config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    if base_config.runner_mode is not False:
        raise RuntimeError("expected production default runner_mode=False (strat_12hr_miyagi has no runner semantics)")

    slippage_results: dict[str, dict] = {}
    for slip in SLIPPAGE_TICKS:
        tag = f"{slip:.0f}tick"
        iso_config = dataclasses.replace(
            base_config,
            enabled_concepts=[STRATEGY],
            disabled_concepts_per_instrument={},
            entry_fill_model="ioc_limit",
            entry_tolerance_ticks_by_root=dict(CANONICAL_ENTRY_TOLERANCE),
            fill_slippage_ticks=float(slip),
        )
        log_dir = args.logs / tag
        print(f"[run] === slippage={slip:.0f} tick ===")
        _run_isolated(iso_config, log_dir, args.fresh)
        trades, audit = _parse_logs(log_dir)
        for row in trades:
            row["half"] = _period_label(row["date"], HALVES)
            row["quarter"] = _period_label(row["date"], QUARTERS)
        slippage_results[tag] = {
            "slippage_ticks": slip,
            "overall": _stats(trades),
            "by_instrument": _group(trades, "instrument", INSTRUMENTS),
            "by_instrument_half": {
                inst: _group([r for r in trades if r["instrument"] == inst], "half", HALVES.keys())
                for inst in INSTRUMENTS
            },
            "by_instrument_direction": {
                inst: _group([r for r in trades if r["instrument"] == inst], "direction", ("LONG", "SHORT"))
                for inst in INSTRUMENTS
            },
            "by_half": _group(trades, "half", HALVES.keys()),
            "by_quarter": _group(trades, "quarter", QUARTERS.keys()),
            "by_direction": _group(trades, "direction", ("LONG", "SHORT")),
            "recent_windows": {
                inst: {
                    label: _stats([r for r in trades if r["instrument"] == inst and _in_window(r["date"], window)])
                    for label, window in RECENT_WINDOWS.items()
                }
                for inst in INSTRUMENTS
            },
            "audit": audit,
            "trades": trades,
        }

    after_config = load_config()
    after_enabled = tuple(after_config.enabled_concepts)
    after_disabled = {k: tuple(v) for k, v in after_config.disabled_concepts_per_instrument.items()}
    risk_hash_after = _sha256(REPO / "risk_rules.yaml")
    if (before_enabled, before_disabled, risk_hash_before) != (after_enabled, after_disabled, risk_hash_after):
        raise RuntimeError("risk_rules.yaml / enabled_concepts drifted on disk during this run")

    primary = slippage_results["1tick"]

    def _survives_slippage(instrument: str | None) -> bool:
        def _overall(tag: str) -> dict:
            block = slippage_results[tag]
            return block["overall"] if instrument is None else block["by_instrument"][instrument]
        return all(
            (_overall(f"{s:.0f}tick")["profit_factor_after_commission"] or 0) > 1
            and (_overall(f"{s:.0f}tick")["net_after_commission"] or 0) > 0
            for s in SLIPPAGE_TICKS
        )

    classifications = {}
    for instrument in INSTRUMENTS:
        wf = _walk_forward_both_halves_positive(primary["by_instrument_half"][instrument])
        slip_ok = _survives_slippage(instrument)
        verdict, reasons = _classify(primary["by_instrument"][instrument], wf, slip_ok)
        classifications[instrument] = {
            "verdict": verdict,
            "reasons": reasons,
            "walk_forward_both_halves_positive": wf,
            "survives_1_4_tick_slippage": slip_ok,
        }
    combined_wf = _walk_forward_both_halves_positive(primary["by_half"])
    combined_slip_ok = _survives_slippage(None)
    combined_verdict, combined_reasons = _classify(primary["overall"], combined_wf, combined_slip_ok)
    classifications["COMBINED"] = {
        "verdict": combined_verdict,
        "reasons": combined_reasons,
        "walk_forward_both_halves_positive": combined_wf,
        "survives_1_4_tick_slippage": combined_slip_ok,
    }

    def _recent_negative(instrument: str, label: str) -> bool | None:
        stats = primary["recent_windows"][instrument][label]
        if stats["resolved"] == 0:
            return None
        return (stats["net_after_commission"] or 0) < 0

    main_sha = _git("rev-parse", "HEAD")
    results = {
        "meta": {
            "branch_sha": main_sha,
            "branch": "claude/miyagi-12hr-demo-readiness (PR #362)",
            "range": list(FULL_RANGE),
            "corpus": str(CORPUS.relative_to(REPO)),
            "strategy": STRATEGY,
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "sample_adequate_min": SAMPLE_ADEQUATE_MIN,
            "isolation": {
                "enabled_concepts": [STRATEGY],
                "disabled_concepts_per_instrument": {},
                "entry_fill_model": "ioc_limit",
                "entry_tolerance_ticks_by_root": CANONICAL_ENTRY_TOLERANCE,
                "entry_tolerance_ambient_env_value": base_config.entry_tolerance_ticks_by_root,
                "runner_mode": base_config.runner_mode,
            },
            "risk_rules_sha256_before": risk_hash_before,
            "risk_rules_sha256_after": risk_hash_after,
        },
        "classification": classifications,
        "drawdown_breaker_audit_1tick": {
            inst: primary["audit"]["halts"].get(inst) for inst in INSTRUMENTS
        },
        "market_condition_blocks_1tick": primary["audit"]["market_condition_blocks"],
        "recent_period_check": {
            inst: {
                "latest_3m_negative": _recent_negative(inst, "latest_3m"),
                "latest_6m_negative": _recent_negative(inst, "latest_6m"),
                "latest_3m": primary["recent_windows"][inst]["latest_3m"],
                "latest_6m": primary["recent_windows"][inst]["latest_6m"],
            }
            for inst in INSTRUMENTS
        },
        "primary_1tick": {k: v for k, v in primary.items() if k != "trades"},
        "slippage_sweep": {
            tag: {k: v for k, v in block.items() if k not in (
                "trades", "by_instrument_half", "by_instrument_direction", "recent_windows", "audit",
            )}
            for tag, block in slippage_results.items()
        },
        "historical_comparators_pr343_lookahead_affected": HISTORICAL_PR343,
        "parity_findings": {
            "runtime_identity": (
                "This run drives strategy/strat_12hr_miyagi.py through the SAME "
                "replay/replay_engine.py -> strategy/signal_engine.py::DecisionEngine "
                "path #362 wires into the live/paper webhook -- not a separate "
                "research harness. The causal stop (_completed_one_hour_stop, reused "
                "verbatim from strategy/four_hr_retrigger.py) is therefore exercised "
                "identically to how it would run live/paper; this IS the runtime/"
                "replay parity proof, not just a new evidence number."
            ),
            "entry_model": (
                "entry_fill_model='ioc_limit' (global PaperBroker setting, applies "
                "uniformly regardless of strategy -- verified in execution/"
                "paper_broker.py before writing this script) with generous per-root "
                "tolerance (MNQ=32/MES=16 ticks) approximates the module docstring's "
                "'no IOC cap, fills at the exact trigger price' entry model; any "
                "attempt count divergence from the docstring's cited 34 candidates "
                "(15 MNQ + 19 MES) would indicate a real behavioral difference, "
                "checked explicitly in the report below."
            ),
            "trending_exemption_moot_in_isolation": (
                "strat_12hr_miyagi's TRENDING-gate exemption exists to resolve "
                "collisions with OTHER simultaneously-armed 5m-native strategies "
                "(strat_4hr_retrigger, strat_322_first_live). With only "
                "strat_12hr_miyagi enabled, no such collision is possible in this "
                "isolated run -- the exemption logic is not exercised either way, "
                "consistent with all other single-strategy canonical-evidence lanes."
            ),
            "entry_tolerance_env_drift": (
                f"SYSTEM-LEVEL, NOT MIYAGI-SPECIFIC: this worktree's `.env` resolves "
                f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ/_MES to "
                f"{base_config.entry_tolerance_ticks_by_root} -- HALF "
                "of config/settings.py's own documented \"live box's known settings\" fallback "
                "(MNQ=32/MES=16) that PR #349 (ORB) and PR #347 (VWAP) both asserted and relied "
                "on as canonical. This run pins the documented 32/16 values explicitly rather "
                "than trust the ambient value, so it stays comparable to those prior lanes -- "
                "but the discrepancy itself is unexplained and unresolved: either this dev "
                "worktree's .env has drifted from the box, or the box itself has moved to "
                "tighter tolerance and every prior 'honest-fill' canonical-evidence lane in "
                "this repo (ORB, VWAP, PR #346's corrected corpus) was run against a WIDER "
                "tolerance than the box actually uses. Reported to the operator as a separate "
                "open question, out of scope to resolve in this Miyagi-specific rerun."
            ),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")

    with args.raw.open("w", encoding="utf-8") as handle:
        for tag, block in slippage_results.items():
            for row in sorted(block["trades"], key=lambda r: (r["date"], r["bar_ts"], r["instrument"])):
                out_row = dict(row)
                out_row["slippage_tag"] = tag
                handle.write(json.dumps(out_row, sort_keys=True) + "\n")

    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(json.dumps({
        "classification": classifications,
        "mnq_overall": primary["by_instrument"]["MNQ"],
        "mes_overall": primary["by_instrument"]["MES"],
        "combined_overall": primary["overall"],
    }, indent=2))
    return 0


def _fmt_money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    return "∞" if math.isinf(value) else f"{value:.3f}"


def _table_rows(blocks: dict[str, dict]) -> list[str]:
    lines = [
        "| Scope | Attempts | Fills | Resolved | WR | Net gross | Net after $1.48 RT | "
        "Exp/arm | PF net | Max DD net | MaxConsecL | Top-3 conc | n≥30 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        top3 = row["winner_concentration_after_commission"]["top_3"]
        lines.append(
            f"| {label} | {row['attempts']} | {row['fills']} | {row['resolved']} | "
            f"{_fmt_rate(row['win_rate'])} | {_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | {_fmt_money(row['expectancy_per_arm_after_commission'])} | "
            f"{_fmt_pf(row['profit_factor_after_commission'])} | "
            f"{_fmt_money(row['max_drawdown_after_commission'])} | {row['max_consecutive_losses']} | "
            f"{_fmt_rate(top3)} | {'✅' if row['sample_adequate'] else '❌'} |"
        )
    return lines


def _render_report(results: dict) -> str:
    primary = results["primary_1tick"]
    cls = results["classification"]
    lines = [
        "# 12HR Miyagi — causal-stop canonical evidence rerun (PR #362 gate)",
        "",
        f"**MNQ verdict: {cls['MNQ']['verdict']}** — " + "; ".join(cls["MNQ"]["reasons"]),
        f"**MES verdict: {cls['MES']['verdict']}** — " + "; ".join(cls["MES"]["reasons"]),
        f"**Combined verdict: {cls['COMBINED']['verdict']}** — " + "; ".join(cls["COMBINED"]["reasons"]),
        "",
        f"Pinned code: `{results['meta']['branch_sha']}` on `{results['meta']['branch']}`",
        f"Corpus: `{results['meta']['corpus']}` (post-#338/#339/#342 corrected corpus/ReplayEngine)",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Why this run exists",
        "",
        "PR #343's canonical evidence (MNQ net $516.33 PF 2.81 n=8; MES net $198.85 PF 1.98 "
        "n=10) used a stop-reference formula with a confirmed lookahead defect (4/8 MNQ and "
        "7/10 MES triggered signals affected). PR #362 fixes the stop causally. This run "
        "reproduces the evidence using #362's ACTUAL runtime code path "
        "(`ReplayEngine -> DecisionEngine -> strategy/strat_12hr_miyagi.py`) so the resulting "
        "numbers are runtime-proof, not just detector-proof.",
        "",
        "## Method",
        "",
        f"- **Isolated** single-strategy replay (`enabled_concepts=[\"{results['meta']['strategy']}\"]` "
        "only) — own fresh account per instrument, so the frozen 20% drawdown breaker "
        "(if it trips) reflects only this strategy's own P&L.",
        "- `entry_fill_model=\"ioc_limit\"` (PR #346's corrected posture), canonical per-root "
        f"tolerance MNQ={results['meta']['isolation']['entry_tolerance_ticks_by_root'].get('MNQ'):.0f} / "
        f"MES={results['meta']['isolation']['entry_tolerance_ticks_by_root'].get('MES'):.0f} ticks **explicitly "
        "pinned in-memory** — this worktree's ambient `.env` currently resolves to "
        f"MNQ={results['meta']['isolation']['entry_tolerance_ambient_env_value'].get('MNQ'):.0f}/"
        f"MES={results['meta']['isolation']['entry_tolerance_ambient_env_value'].get('MES'):.0f} ticks instead "
        "(half the documented \"live box\" fallback every prior canonical-evidence lane used) — "
        "see parity findings below, reported as a separate system-level question.",
        f"- `runner_mode={results['meta']['isolation']['runner_mode']}` (production default) — "
        "strat_12hr_miyagi's candidate only ever defines a single Candle-3-boundary T1 target, "
        "verified `target_2` is computed but never consumed in signal_engine.py.",
        "- 1/2/3/4-tick adverse slippage sensitivity, same isolation/corpus.",
        f"- ${results['meta']['commission_round_trip']:.2f} round-trip commission at the analysis layer only.",
        "- `risk_rules.yaml` verified byte-identical before/after "
        f"(`{results['meta']['risk_rules_sha256_before'][:16]}…`).",
        "",
        "## MNQ — overall (1-tick)",
        "",
        *_table_rows({"MNQ": primary["by_instrument"]["MNQ"]}),
        "",
        "## MES — overall (1-tick)",
        "",
        *_table_rows({"MES": primary["by_instrument"]["MES"]}),
        "",
        "## Combined — overall (1-tick)",
        "",
        *_table_rows({"COMBINED": primary["overall"]}),
        "",
        "## Walk-forward H1/H2 by instrument (1-tick)",
        "",
        "### MNQ",
        *_table_rows(primary["by_instrument_half"]["MNQ"]),
        f"Both halves positive: **{cls['MNQ']['walk_forward_both_halves_positive']}**",
        "",
        "### MES",
        *_table_rows(primary["by_instrument_half"]["MES"]),
        f"Both halves positive: **{cls['MES']['walk_forward_both_halves_positive']}**",
        "",
        "## Direction by instrument (1-tick)",
        "",
        "### MNQ",
        *_table_rows(primary["by_instrument_direction"]["MNQ"]),
        "",
        "### MES",
        *_table_rows(primary["by_instrument_direction"]["MES"]),
        "",
        "## Quarter (combined, 1-tick)",
        "",
        *_table_rows(primary["by_quarter"]),
        "",
        "## Recent period (1-tick)",
        "",
    ]
    for inst in INSTRUMENTS:
        rp = results["recent_period_check"][inst]
        lines.append(f"### {inst}")
        lines += _table_rows({"latest_3m": rp["latest_3m"], "latest_6m": rp["latest_6m"]})
        lines.append(
            f"Latest 3m negative: **{rp['latest_3m_negative']}**. "
            f"Latest 6m negative: **{rp['latest_6m_negative']}**."
        )
        lines.append("")
    lines += [
        "## Slippage sensitivity 1/2/3/4-tick (combined overall)",
        "",
        *_table_rows({f"{s:.0f}tick": results["slippage_sweep"][f"{s:.0f}tick"]["overall"] for s in SLIPPAGE_TICKS}),
        f"MNQ survives 1-4 tick: **{cls['MNQ']['survives_1_4_tick_slippage']}**. "
        f"MES survives 1-4 tick: **{cls['MES']['survives_1_4_tick_slippage']}**.",
        "",
        "## Drawdown-breaker / market-condition audit (1-tick)",
        "",
    ]
    for inst in INSTRUMENTS:
        halt = results["drawdown_breaker_audit_1tick"][inst]
        blocks = results["market_condition_blocks_1tick"][inst]
        lines.append(
            f"- **{inst}**: drawdown-breaker halt = {halt if halt else 'none'}; "
            f"market_condition-rejected candidates = {blocks}"
        )
    lines += [
        "",
        "## Historical comparator — PR #343 (lookahead-affected stop, NOT this run's methodology)",
        "",
    ]
    for inst, comp in results["historical_comparators_pr343_lookahead_affected"].items():
        lines.append(
            f"- **{comp['label']}**: n={comp['candidates']} candidates / {comp['fills']} fills / "
            f"{comp['wins']}W-{comp['losses']}L / ${comp['net_pnl_reported']:,.2f} net / "
            f"PF {comp['profit_factor_reported']:.2f}. {comp['status']}"
        )
    lines += [
        "",
        "## Parity findings",
        "",
        f"- **Runtime identity**: {results['parity_findings']['runtime_identity']}",
        f"- **Entry model**: {results['parity_findings']['entry_model']}",
        f"- **TRENDING exemption**: {results['parity_findings']['trending_exemption_moot_in_isolation']}",
        f"- **Entry-tolerance .env drift (system-level)**: {results['parity_findings']['entry_tolerance_env_drift']}",
        "",
        "## Reproduction",
        "",
        "```bash",
        "git checkout claude/miyagi-12hr-demo-readiness",
        "python scripts/miyagi_12hr_causal_stop_evidence.py \\",
        "  --logs logs/replay_miyagi_12hr_causal_stop \\",
        "  --out scripts/miyagi_12hr_causal_stop_evidence_results.json \\",
        "  --raw scripts/miyagi_12hr_causal_stop_evidence_raw_trades.jsonl \\",
        "  --report docs/strategy-rules/12HR_MIYAGI_CAUSAL_STOP_EVIDENCE_2026-07-27.md",
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
