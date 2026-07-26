#!/usr/bin/env python3
"""Fresh MNQ 4HR Re-Trigger evidence on corrected replay + IOC execution.

Evidence-only driver. It:
1. rematerializes the existing MNQ 5-minute Polygon replay bars with the
   current Pine/runtime market-condition formula;
2. runs the canonical ReplayEngine with only the canonical #317
   ``strat_4hr_retrigger`` lane enabled in memory;
3. uses the current MNQ IOC tolerance, pessimistic same-bar resolution,
   fixed completed-1H stop, prior-4PM target, and exact day-only exit;
4. reports 1/2/3/4-tick adverse-slippage sensitivity and current repository
   commission at the analysis layer.

It never writes risk_rules.yaml, .env, runtime state, broker configuration,
or the source corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from collections import Counter, deque
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config  # noqa: E402
from execution.mnq_strat_evidence import MNQ_COMMISSION_ROUND_TRIP  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402
from scripts.pine_market_condition import (  # noqa: E402
    atr14_series,
    reconstruct_bar,
    sma_series,
)

EXPECTED_SHA = "69ec77fd33834a437fec77a51249fa1d66030a16"
INSTRUMENT = "MNQ"
STRATEGY = "strat_4hr_retrigger"
SLIPPAGE_TICKS = (2, 1, 3, 4)  # baseline first, then remaining sensitivity
VALID_CONDITIONS = {"DEAD", "CHOPPY", "TRENDING", "RANGE_BOUND"}
PRIOR_MARKET_FILL_NET = 3069.60
# Pure performance bound already established by the canonical detector's
# prior evidence harness: 600 five-minute bars = 50 hours, while the widest
# 4HR reference lookback is <36 hours. It does not alter candidate semantics.
FOUR_HR_HISTORY_BARS = 600


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_hash(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.name.encode())
        digest.update(_sha256(path).encode())
    return digest.hexdigest()


def _read_rows(source: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    paths = sorted(source.glob(f"{INSTRUMENT}_*.jsonl"))
    if not paths:
        raise RuntimeError(f"no {INSTRUMENT} replay files under {source}")
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    row = json.loads(line)
                    row["_source_name"] = path.name
                    rows.append(row)
    rows.sort(key=lambda row: row["timestamp"])
    return paths, rows


def rematerialize_market_condition(source: Path, output: Path) -> dict[str, Any]:
    """Copy source rows while replacing engine-facing condition canonically."""
    paths, rows = _read_rows(source)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    highs = [float(row["high"]) for row in rows]
    lows = [float(row["low"]) for row in rows]
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    atr = atr14_series(highs, lows, closes)
    vol_sma = sma_series(volumes, 20)

    before = Counter()
    after = Counter()
    statuses = Counter()
    by_file: dict[str, list[dict[str, Any]]] = {}
    for index, row in enumerate(rows):
        before[str(row.get("market_condition"))] += 1
        rel_vol = (
            volumes[index] / vol_sma[index]
            if vol_sma[index] not in (None, 0)
            else None
        )
        trend, trend_status, condition, condition_status = reconstruct_bar(
            close=closes[index],
            ema9=row.get("ema_9"),
            ema21=row.get("ema_21"),
            ema55=row.get("ema_55"),
            ema_source="self_computed",
            high=highs[index],
            low=lows[index],
            atr14=atr[index],
            rel_vol=rel_vol,
            volume_is_synthetic=False,
        )
        if condition is not None and condition not in VALID_CONDITIONS:
            raise RuntimeError(f"invalid reconstructed market condition: {condition}")
        source_name = row.pop("_source_name")
        legacy = row.get("market_condition")
        row["legacy_market_condition"] = legacy
        row["market_condition"] = condition
        row["market_condition_status"] = condition_status
        row["reconstructed_trend_direction"] = trend
        row["reconstructed_trend_status"] = trend_status
        row["reconstructed_market_condition"] = condition
        row["reconstructed_market_condition_status"] = condition_status
        row["reconstructed_atr14"] = atr[index]
        row["reconstructed_rel_vol"] = rel_vol
        after[str(condition)] += 1
        statuses[condition_status] += 1
        by_file.setdefault(source_name, []).append(row)

    for source_name, file_rows in by_file.items():
        with (output / source_name).open("w", encoding="utf-8") as handle:
            for row in file_rows:
                handle.write(json.dumps(row, separators=(",", ":")) + "\n")

    output_paths = sorted(output.glob("*.jsonl"))
    if len(output_paths) != len(paths):
        raise RuntimeError(
            f"rematerialization file mismatch: {len(output_paths)} != {len(paths)}"
        )
    invalid_labels = set(after).difference({str(v) for v in VALID_CONDITIONS} | {"None"})
    if invalid_labels:
        raise RuntimeError(f"noncanonical output labels: {sorted(invalid_labels)}")
    return {
        "source_files": len(paths),
        "source_rows": len(rows),
        "source_tree_sha256": _tree_hash(paths),
        "output_tree_sha256": _tree_hash(output_paths),
        "date_range": [
            rows[0]["timestamp"],
            rows[-1]["timestamp"],
        ],
        "condition_before": dict(sorted(before.items())),
        "condition_after": dict(sorted(after.items())),
        "condition_status": dict(sorted(statuses.items())),
        "invalid_condition_labels": sorted(invalid_labels),
    }


def _journal_rows(log_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            rows.extend(json.loads(line) for line in handle if line.strip())
    return rows


def _max_drawdown(pnls: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [trade for trade in trades if trade["result"] in {"WIN", "LOSS", "BREAKEVEN"}]
    net = [float(trade["net_pnl"]) for trade in resolved]
    gross = [float(trade["gross_pnl"]) for trade in resolved]
    wins = [pnl for pnl in net if pnl > 0]
    losses = [pnl for pnl in net if pnl < 0]
    ordered_winners = sorted(wins, reverse=True)
    top3 = sum(ordered_winners[:3])
    gross_profit = sum(wins)
    return {
        "resolved": len(resolved),
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(resolved) - len(wins) - len(losses),
        "win_rate_pct": round(100 * len(wins) / len(resolved), 2) if resolved else None,
        "gross_pnl": round(sum(gross), 2),
        "commission_total": round(MNQ_COMMISSION_ROUND_TRIP * len(resolved), 2),
        "net_pnl": round(sum(net), 2),
        "expectancy_per_fill": round(sum(net) / len(resolved), 2) if resolved else None,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 3)
            if losses
            else ("infinite" if wins else None)
        ),
        "max_drawdown": _max_drawdown(net),
        "largest_loss": round(min(net), 2) if net else None,
        "largest_win": round(max(net), 2) if net else None,
        "top3_winner_pnl": round(top3, 2),
        "top3_share_of_gross_profit_pct": (
            round(100 * top3 / gross_profit, 2) if gross_profit else None
        ),
        "net_after_removing_top3": round(sum(net) - top3, 2),
    }


def analyze(log_dir: Path, *, midpoint: datetime) -> dict[str, Any]:
    rows = _journal_rows(log_dir)
    decisions: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    risk_rejected = 0
    detected: dict[str, dict[str, Any]] = {}
    trigger_gate_distribution: Counter = Counter()
    for row in rows:
        strategy_state = (
            ((row.get("strategy_state") or {}).get("strat_4hr_retrigger") or {})
            .get(INSTRUMENT, {})
        )
        if strategy_state.get("status") == "TRIGGERED":
            entry_time = strategy_state.get("entry_time")
            if entry_time and entry_time not in detected:
                detected[entry_time] = row
                gates = row.get("failed_gates") or ["ADMITTED_NO_FAILED_GATE"]
                trigger_gate_distribution.update(gates)
        order_id = row.get("paper_order_id")
        if row.get("decision") == "RISK_REJECTED":
            setup = row.get("setup") or {}
            if setup.get("strategy") == STRATEGY:
                risk_rejected += 1
        if row.get("decision") == "TRADE" and order_id:
            setup = row.get("setup") or {}
            if setup.get("strategy") == STRATEGY:
                decisions[order_id] = row
        elif row.get("type") == "OUTCOME":
            outcome = row.get("outcome") or {}
            outcome_order_id = outcome.get("paper_order_id")
            if outcome_order_id:
                outcomes[outcome_order_id] = outcome

    trades: list[dict[str, Any]] = []
    cancelled = 0
    for order_id, decision in sorted(
        decisions.items(), key=lambda item: item[1].get("bar_ts", "")
    ):
        outcome = outcomes.get(order_id)
        if outcome and outcome.get("result") == "CANCELLED":
            cancelled += 1
            continue
        setup = decision["setup"]
        if outcome is None:
            trades.append({
                "paper_order_id": order_id,
                "bar_ts": decision.get("bar_ts"),
                "direction": setup["direction"],
                "result": "OPEN",
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
            })
            continue
        gross = float(outcome.get("pnl_dollars") or 0.0)
        trades.append({
            "paper_order_id": order_id,
            "bar_ts": decision.get("bar_ts"),
            "direction": setup["direction"],
            "result": outcome.get("result"),
            "exit_reason": outcome.get("exit_reason"),
            "gross_pnl": round(gross, 2),
            "net_pnl": round(gross - MNQ_COMMISSION_ROUND_TRIP, 2),
        })

    resolved = [t for t in trades if t["result"] in {"WIN", "LOSS", "BREAKEVEN"}]
    first_half = [
        t for t in resolved
        if datetime.fromisoformat(t["bar_ts"].replace("Z", "+00:00")) < midpoint
    ]
    second_half = [t for t in resolved if t not in first_half]
    long_trades = [t for t in resolved if t["direction"] == "LONG"]
    short_trades = [t for t in resolved if t["direction"] == "SHORT"]
    attempts = len(decisions)
    fills = len(trades)
    regime_admitted = sum(
        1 for row in detected.values() if row.get("market_condition") == "TRENDING"
    )
    return {
        "setups_detected": len(detected),
        "regime_admitted_trending": regime_admitted,
        "regime_blocked_non_trending": len(detected) - regime_admitted,
        "trigger_gate_distribution": dict(sorted(trigger_gate_distribution.items())),
        "attempts": attempts,
        "fills": fills,
        "fill_rate_pct": round(100 * fills / attempts, 2) if attempts else None,
        "ioc_cancelled_no_fill": cancelled,
        "risk_rejected": risk_rejected,
        "open": sum(1 for t in trades if t["result"] == "OPEN"),
        "overall": summarize(resolved),
        "first_half": summarize(first_half),
        "second_half": summarize(second_half),
        "long": summarize(long_trades),
        "short": summarize(short_trades),
        "trades": trades,
    }


def classify(variants: dict[str, Any]) -> dict[str, str]:
    baseline = variants["2"]
    overall = baseline["overall"]
    h1 = baseline["first_half"]["net_pnl"]
    h2 = baseline["second_half"]["net_pnl"]
    nets = [variants[str(tick)]["overall"]["net_pnl"] for tick in (1, 2, 3, 4)]
    resolved = overall["resolved"]
    if resolved < 30:
        verdict = "WAIT"
        reason = f"only {resolved} resolved IOC fills (<30 preferred minimum)"
    elif overall["net_pnl"] <= 0:
        verdict = "BROKEN"
        reason = "negative full-sample net P&L under the executable baseline"
    elif h1 <= 0 or h2 <= 0 or min(nets) <= 0 or overall["net_after_removing_top3"] <= 0:
        verdict = "OVERFIT"
        reason = (
            "headline edge fails at least one robustness gate: both halves, "
            "1-4 tick sensitivity, or top-3 removal"
        )
    else:
        verdict = "PROMISING BUT UNPROVEN"
        reason = (
            "positive in both chronological halves, all 1-4 tick variants, "
            "and after removing the top 3 winners; still retrospective only"
        )
    return {"verdict": verdict, "reason": reason}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--corrected", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parent.parent
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    if sha != EXPECTED_SHA:
        raise RuntimeError(f"wrong git SHA: {sha} != {EXPECTED_SHA}")

    risk_path = repo / "risk_rules.yaml"
    risk_hash_before = _sha256(risk_path)
    corpus = rematerialize_market_condition(args.source, args.corrected)
    start = datetime.fromisoformat(corpus["date_range"][0].replace("Z", "+00:00"))
    end = datetime.fromisoformat(corpus["date_range"][1].replace("Z", "+00:00"))
    midpoint = start + (end - start) / 2

    base = load_config()
    if base.entry_tolerance_ticks_by_root.get(INSTRUMENT) != 32.0:
        raise RuntimeError("current MNQ IOC tolerance is not 32 ticks")
    if not base.fill_pessimistic_both_hit:
        raise RuntimeError("pessimistic same-bar resolution is not enabled")
    if MNQ_COMMISSION_ROUND_TRIP != 1.48:
        raise RuntimeError("current MNQ evidence commission is not $1.48")
    if STRATEGY not in base.enabled_concepts:
        raise RuntimeError("canonical 4HR strategy is not enabled on current main")
    if STRATEGY in base.disabled_concepts_per_instrument.get(INSTRUMENT, []):
        raise RuntimeError("canonical 4HR strategy is disabled for MNQ")

    variants: dict[str, Any] = {}
    files = sorted(args.corrected.glob(f"{INSTRUMENT}_*.jsonl"))
    for slip in SLIPPAGE_TICKS:
        slip_logs = args.logs / f"slippage_{slip}"
        if slip_logs.exists():
            shutil.rmtree(slip_logs)
        config = replace(
            base,
            enabled_concepts=[STRATEGY],
            entry_fill_model="ioc_limit",
            fill_slippage_ticks=float(slip),
            fill_pessimistic_both_hit=True,
        )
        engine = ReplayEngine(config=config, log_dir=str(slip_logs))
        engine._four_hr_bars[INSTRUMENT] = deque(maxlen=FOUR_HR_HISTORY_BARS)
        for index, path in enumerate(files, start=1):
            day = path.stem.rsplit("_", 1)[-1]
            engine.run(path, review_date=day)
            if index % 100 == 0 or index == len(files):
                print(f"[{slip}t] {index}/{len(files)}", flush=True)
        variants[str(slip)] = analyze(slip_logs, midpoint=midpoint)
        if slip == 2:
            h1 = variants["2"]["first_half"]["net_pnl"]
            h2 = variants["2"]["second_half"]["net_pnl"]
            if h1 < 0 or h2 < 0:
                print(
                    f"IMMEDIATE ROBUSTNESS FLAG: baseline half negative "
                    f"(H1=${h1:.2f}, H2=${h2:.2f})",
                    flush=True,
                )

    result = {
        "git_sha": sha,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "instrument": INSTRUMENT,
        "strategy": STRATEGY,
        "corpus": corpus,
        "chronological_midpoint_utc": midpoint.isoformat(),
        "assumptions": {
            "canonical_detector": "strategy.four_hr_retrigger.advance_4hr_retrigger (#317)",
            "engine": "replay.replay_engine.ReplayEngine",
            "isolation": "enabled_concepts=[strat_4hr_retrigger] in memory only",
            "replay_4hr_history_window_bars": FOUR_HR_HISTORY_BARS,
            "replay_4hr_history_window_reason": (
                "50 hours; performance bound only, canonical detector maximum "
                "reference lookback is under 36 hours"
            ),
            "entry_fill_model": "ioc_limit",
            "entry_tolerance_ticks": base.entry_tolerance_ticks_by_root[INSTRUMENT],
            "baseline_slippage_ticks": 2,
            "sensitivity_slippage_ticks": [1, 2, 3, 4],
            "pessimistic_both_hit": True,
            "stop": "fixed last completed 1H candle at entry",
            "target": "prior 4PM level",
            "day_only_exit": "exact 15:55 ET bar",
            "commission_round_trip": MNQ_COMMISSION_ROUND_TRIP,
            "commission_source": "execution.mnq_strat_evidence.MNQ_COMMISSION_ROUND_TRIP",
            "prior_requested_1_24_status": "superseded older-study precedent; not used",
        },
        "variants": variants,
        "classification": classify(variants),
        "prior_comparison": {
            "prior_market_fill_net_1_tick": PRIOR_MARKET_FILL_NET,
            "corrected_ioc_net_2_tick": variants["2"]["overall"]["net_pnl"],
            "difference": round(
                variants["2"]["overall"]["net_pnl"] - PRIOR_MARKET_FILL_NET, 2
            ),
            "not_like_for_like_warning": (
                "Prior used market fill, 1 tick, and direct detector study; "
                "fresh result uses corrected condition gate, canonical ReplayEngine, "
                "IOC, and 2-tick baseline."
            ),
        },
        "risk_rules_sha256_before": risk_hash_before,
        "risk_rules_sha256_after": _sha256(risk_path),
    }
    if result["risk_rules_sha256_before"] != result["risk_rules_sha256_after"]:
        raise RuntimeError("risk_rules.yaml changed during evidence run")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "classification": result["classification"],
        "baseline": variants["2"],
        "prior_comparison": result["prior_comparison"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
