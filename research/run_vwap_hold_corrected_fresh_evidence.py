#!/usr/bin/env python3
"""Fresh corrected-system evidence for canonical MNQ NY VWAP Hold.

Evidence-only orchestration.  Candidate generation calls the current
DecisionEngine implementation on the corrected post-#338 replay corpus.
Execution uses PaperBroker IOC-limit entry and runner resolution.  No runtime,
strategy, risk, broker, config, Pine, or deployment file is modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import deque
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from context.bar_history import BarHistory  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.day_only_exit import classify_result  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402
from replay.candle_loader import ReplayCandleLoader  # noqa: E402
from replay.replay_engine import ReplayEngine, _parse_timestamp  # noqa: E402
from risk.risk_engine import DailyState  # noqa: E402
from strategy.signal_engine import DecisionEngine  # noqa: E402

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
INSTRUMENT = "MNQ"
STRATEGY = "vwap_hold"
START = "2025-07-24"
END = "2026-07-23"
MIDPOINT = "2026-01-24"
COMMISSION_RT = 1.48
IOC_TOLERANCE_TICKS = 32.0
TICK_SIZE = 0.25
TICK_VALUE = 0.50
EXPECTED_SHA = "69ec77fd33834a437fec77a51249fa1d66030a16"


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
    ).strip()


def _tree_hash(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


def _fresh_config():
    """Isolate one strategy without mutating repo configuration.

    DecisionEngine strategy, condition, regime, confluence, schedule, and
    validation gates remain canonical.  Permission is disabled only because
    VWAP Hold is intentionally SHADOW_ONLY while this historical lane measures
    it; RiskEngine is not part of this per-strategy detector/fill study.
    """
    base = load_config()
    return replace(
        base,
        allowed_instruments=[INSTRUMENT],
        allowed_sessions=["new_york"],
        enabled_concepts=[STRATEGY],
        disabled_concepts_per_instrument={},
        strategy_permission_gate_enabled=False,
        strategy_selection_mode="ranked",
    )


def _preflight(corpus: Path) -> dict:
    sha = _git_sha()
    if sha != EXPECTED_SHA:
        raise RuntimeError(f"expected exact main {EXPECTED_SHA}, got {sha}")
    files, digest = _tree_hash(corpus)
    if files != 313:
        raise RuntimeError(f"expected 313 MNQ files, got {files}")

    labels: set[str] = set()
    mismatches = 0
    bars = 0
    for path in sorted(corpus.glob("*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            bars += 1
            condition = row.get("market_condition")
            reconstructed = row.get("reconstructed_market_condition")
            if condition != reconstructed:
                mismatches += 1
            if condition is not None:
                labels.add(str(condition))
    invalid = labels - {"DEAD", "CHOPPY", "TRENDING", "RANGE_BOUND"}
    if invalid or mismatches:
        raise RuntimeError(
            f"market-condition preflight failed: invalid={invalid}, mismatches={mismatches}"
        )
    return {
        "git_sha": sha,
        "corpus_files": files,
        "corpus_tree_sha256": digest,
        "bars": bars,
        "market_condition_labels": sorted(labels),
        "engine_facing_reconstruction_mismatches": mismatches,
        "canonical_strategy_callable": "strategy.signal_engine.DecisionEngine._try_vwap_hold",
    }


def _generate_candidates(corpus: Path, cfg) -> list[dict]:
    engine = ReplayEngine(config=cfg, log_dir="/tmp/vwap_hold_fresh_state_only")
    candidates: list[dict] = []
    window: deque = deque(maxlen=6)
    loader = ReplayCandleLoader()

    for path in sorted(corpus.glob("*.jsonl")):
        candles = loader.load_jsonl(path)
        daily = DailyState(
            date=path.stem.rsplit("_", 1)[-1],
            account_balance=cfg.position_sizing.starting_balance,
        )
        decision_engine = DecisionEngine(config=cfg)
        previous = None
        previous2 = None
        for candle in candles:
            window.append(
                {"ts": candle.timestamp, "close": candle.close}
            )
            state = engine._market_state_from_candle(candle, previous, previous2)
            current_date = _parse_timestamp(candle.timestamp).date()
            live_equivalent_window = [
                row
                for row in window
                if 0
                <= (
                    current_date - _parse_timestamp(row["ts"]).date()
                ).days
                < 3
            ]
            state.window_direction = BarHistory.window_direction(
                live_equivalent_window
            )
            decision = decision_engine.evaluate(state, daily)
            previous2, previous = previous, candle
            if (
                decision.decision != "TRADE"
                or decision.setup is None
                or decision.setup.strategy != STRATEGY
            ):
                continue
            if state.session != "new_york" or decision.setup.direction != "SHORT":
                raise RuntimeError(
                    f"canonical scope violation at {candle.timestamp}: "
                    f"{state.session=} {decision.setup.direction=}"
                )
            bar_open = _parse_timestamp(candle.timestamp)
            candidates.append(
                {
                    "signal_bar_ts": candle.timestamp,
                    "decision_ts": (bar_open + timedelta(minutes=15)).isoformat(),
                    "date": bar_open.date().isoformat(),
                    "session": state.session,
                    "direction": decision.setup.direction,
                    "entry": decision.setup.entry,
                    "stop": decision.setup.stop,
                    "target": decision.setup.target,
                    "market_close": candle.close,
                    "market_condition": decision.market_condition,
                }
            )
    candidates.sort(key=lambda row: row["decision_ts"])
    return candidates


def _load_fine(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        ts = datetime.fromisoformat(
            str(raw["timestamp"]).replace("Z", "+00:00")
        )
        rows.append(
            {
                "ts": ts,
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
            }
        )
    return sorted(rows, key=lambda row: row["ts"])


def _simulate(candidates: list[dict], fine_root: Path, slippage_ticks: int) -> dict:
    rows: list[dict] = []
    fine_cache: dict[str, list[dict]] = {}
    busy_until: datetime | None = None
    candidate_while_open = 0

    for arm in candidates:
        decision_ts = datetime.fromisoformat(arm["decision_ts"])
        if busy_until is not None and decision_ts <= busy_until:
            candidate_while_open += 1
            continue
        day = arm["date"]
        bars = fine_cache.setdefault(
            day, _load_fine(fine_root / f"MNQ_{day}.jsonl")
        )
        if not bars:
            rows.append({**arm, "status": "NO_5M_DATA", "result": None})
            continue

        broker = PaperBroker(
            starting_balance=1500.0,
            slippage_ticks=float(slippage_ticks),
            pessimistic_both_hit=True,
            breakeven_at_1r=False,
            runner_mode=True,
            runner_activation_r=1.0,
            runner_trail_r=0.5,
            entry_fill_model="ioc_limit",
            entry_tolerance_ticks_by_root={INSTRUMENT: IOC_TOLERANCE_TICKS},
        )
        order = BracketOrder(
            instrument=INSTRUMENT,
            direction=arm["direction"],
            entry=arm["entry"],
            stop=arm["stop"],
            target=arm["target"],
            rr_ratio=3.0,
            strategy=STRATEGY,
            contracts=1,
        )
        fill = broker.execute_bracket(
            order, market_price=arm["market_close"]
        )
        if fill.result == "CANCELLED":
            rows.append(
                {
                    **arm,
                    "status": "IOC_CANCELLED",
                    "result": "NO_FILL",
                    "pnl_gross": 0.0,
                    "pnl_net": 0.0,
                }
            )
            continue

        resolved = None
        exit_ts = None
        exit_reason = None
        for bar in bars:
            if bar["ts"] < decision_ts:
                continue
            local = bar["ts"].astimezone(ET)
            if local.hour > 15 or (local.hour == 15 and local.minute > 55):
                break
            resolved = broker.resolve_position(
                NextBarOHLC(
                    open=bar["open"], high=bar["high"], low=bar["low"]
                )
            )
            if resolved is not None:
                exit_ts = bar["ts"]
                exit_reason = resolved.exit_reason
                break
            if local.hour == 15 and local.minute == 55:
                result = classify_result(
                    arm["direction"], broker.get_position().entry_price, bar["close"]
                )
                resolved = broker.force_resolve(result, bar["close"])
                if resolved is not None:
                    resolved.exit_reason = "DAY_ONLY_FLATTEN_1555_ET"
                    exit_ts = bar["ts"]
                    exit_reason = resolved.exit_reason
                break

        if resolved is None:
            rows.append({**arm, "status": "OPEN_NO_1555_BAR", "result": None})
            busy_until = datetime.combine(
                decision_ts.date(), datetime.max.time(), tzinfo=UTC
            )
            continue
        gross = float(resolved.pnl_dollars or 0.0)
        rows.append(
            {
                **arm,
                "status": "FILLED_RESOLVED",
                "fill_price": resolved.entry_price,
                "result": resolved.result,
                "exit_price": resolved.exit_price,
                "exit_reason": exit_reason,
                "exit_ts": exit_ts.isoformat() if exit_ts else None,
                "pnl_gross": round(gross, 2),
                "commission": COMMISSION_RT,
                "pnl_net": round(gross - COMMISSION_RT, 2),
            }
        )
        busy_until = exit_ts
    return {
        "slippage_ticks": slippage_ticks,
        "candidates": len(candidates),
        "candidate_while_position_open": candidate_while_open,
        "attempts": len(rows),
        "rows": rows,
    }


def _max_drawdown(values: list[float]) -> float:
    equity = peak = drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return round(drawdown, 2)


def _stats(rows: list[dict]) -> dict:
    attempts = [row for row in rows if row["status"] != "NO_5M_DATA"]
    filled = [row for row in rows if row["status"] == "FILLED_RESOLVED"]
    net = [row["pnl_net"] for row in filled]
    gross = [row["pnl_gross"] for row in filled]
    winners = sorted(
        (row for row in filled if row["pnl_net"] > 0),
        key=lambda row: row["pnl_net"],
        reverse=True,
    )
    losses = [value for value in net if value < 0]
    wins = [value for value in net if value > 0]
    top3 = sum(row["pnl_net"] for row in winners[:3])
    return {
        "attempts": len(attempts),
        "fills": len(filled),
        "fill_rate": round(len(filled) / len(attempts), 6) if attempts else None,
        "cancelled_no_fill": sum(
            row["status"] == "IOC_CANCELLED" for row in rows
        ),
        "resolved": len(filled),
        "open": sum(row["status"] == "OPEN_NO_1555_BAR" for row in rows),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(filled), 6) if filled else None,
        "gross_pnl": round(sum(gross), 2),
        "commission_total": round(COMMISSION_RT * len(filled), 2),
        "net_pnl": round(sum(net), 2),
        "expectancy_per_fill": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 6)
            if wins and losses
            else math.inf if wins else None
        ),
        "max_drawdown": _max_drawdown(net),
        "largest_loss": round(min(net), 2) if net else None,
        "top_3_winner_contribution": round(top3, 2),
        "top_3_share_of_net": (
            round(top3 / sum(net), 6) if sum(net) > 0 else None
        ),
        "net_after_top_3_removal": round(sum(net) - top3, 2),
    }


def _classification(baseline: dict) -> tuple[str, list[str]]:
    full = baseline["full"]
    h1 = baseline["H1"]
    h2 = baseline["H2"]
    reasons = []
    if full["resolved"] < 15:
        return "WAIT", [f"only {full['resolved']} resolved fills"]
    if full["net_pnl"] <= 0:
        return "BROKEN", ["2-tick net P&L is non-positive"]
    if h1["net_pnl"] <= 0 or h2["net_pnl"] <= 0:
        return "OVERFIT", ["one chronological half is non-positive"]
    if full["net_after_top_3_removal"] <= 0:
        return "PROMISING BUT UNPROVEN", [
            "positive but top-3 removal flips net negative"
        ]
    if full["resolved"] < 30:
        return "WAIT", [f"only {full['resolved']} resolved fills"]
    reasons.extend(
        [
            "both chronological halves positive",
            "positive at 2-tick adverse slippage after current $1.48 commission",
            "top-3 removal remains positive",
            f"{full['resolved']} resolved fills",
        ]
    )
    return "PROMISING BUT UNPROVEN", reasons


def _render(results: dict) -> str:
    base = results["baseline_2_tick"]
    lines = [
        "# VWAP Hold — fresh corrected-system evidence",
        "",
        f"**Classification: {results['classification']}**",
        "",
        f"Git SHA: `{results['meta']['git_sha']}`",
        f"Date range: {START} → {END}",
        "",
        "Fresh candidate generation through the current canonical "
        "`DecisionEngine._try_vwap_hold`, not the locked #345 arm population.",
        "",
        "## Assumptions",
        "",
        "- MNQ, New York session, SHORT only.",
        "- Corrected post-#338 engine-facing market condition.",
        "- IOC-limit marketability at the completed decision bar close; 32-tick cap.",
        "- Runner exit: 1.0R activation / 0.5R trail.",
        "- Pessimistic same-bar resolution; day-only flatten on the 15:55 ET 5m bar.",
        "- Baseline: 2 ticks adverse PaperBroker slippage; $1.48 round-trip commission.",
        "- $1.48 is sourced from current `execution.mnq_strat_evidence`; the older "
        "$1.24 #345 convention is not silently reused.",
        "",
        "## Baseline",
        "",
        "| Scope | Attempts | Fills | Fill rate | WR | Gross | Net | Exp/fill | PF | Max DD | Largest loss | Net after top-3 removal |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label in ("full", "H1", "H2", "SHORT"):
        row = base[label]
        pf = "—" if row["profit_factor"] is None else (
            "∞" if math.isinf(row["profit_factor"]) else f"{row['profit_factor']:.3f}"
        )
        lines.append(
            f"| {label} | {row['attempts']} | {row['fills']} | "
            f"{100 * row['fill_rate']:.1f}% | {100 * row['win_rate']:.1f}% | "
            f"${row['gross_pnl']:,.2f} | ${row['net_pnl']:,.2f} | "
            f"${row['expectancy_per_fill']:,.2f} | {pf} | "
            f"${row['max_drawdown']:,.2f} | ${row['largest_loss']:,.2f} | "
            f"${row['net_after_top_3_removal']:,.2f} |"
        )
    lines += [
        "",
        "## Slippage sensitivity",
        "",
        "| Adverse ticks | Attempts | Fills | Fill rate | WR | Gross | Net | Exp/fill | PF | Both halves positive |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    for ticks, cell in results["sensitivity"].items():
        row = cell["full"]
        pf = "—" if row["profit_factor"] is None else (
            "∞" if math.isinf(row["profit_factor"]) else f"{row['profit_factor']:.3f}"
        )
        both = cell["H1"]["net_pnl"] > 0 and cell["H2"]["net_pnl"] > 0
        lines.append(
            f"| {ticks} | {row['attempts']} | {row['fills']} | "
            f"{100 * row['fill_rate']:.1f}% | {100 * row['win_rate']:.1f}% | "
            f"${row['gross_pnl']:,.2f} | ${row['net_pnl']:,.2f} | "
            f"${row['expectancy_per_fill']:,.2f} | {pf} | {'YES' if both else 'NO'} |"
        )
    lines += [
        "",
        "## #345 sanity comparison",
        "",
        "#345 used a locked 107-arm NY population and reported 55 IOC-close fills "
        "(51.4%), runner net $828.77, PF 3.218 under its older $1.24/2-tick cost "
        "convention. This run regenerates candidates through current corrected code; "
        "differences are evidence, not forced reconciliation.",
        "",
        "## Classification reasoning",
        "",
    ]
    lines.extend(f"- {reason}" for reason in results["classification_reasons"])
    if base["full"]["fill_rate"] < 0.30:
        lines.append("- **IOC starvation flag: fill rate is below 30%.**")
    lines += [
        "",
        "## Scope",
        "",
        "Evidence only. Strategy permission is bypassed in memory solely to measure "
        "the current SHADOW_ONLY strategy; no repository config/risk/demo/runtime "
        "state changed. RiskEngine/account breaker effects are outside this isolated "
        "per-strategy detector/fill study.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--fine-root", required=True, type=Path)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "research/vwap_hold_corrected_fresh_results.json",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/strategy-rules/VWAP_HOLD_CORRECTED_FRESH_EVIDENCE_2026-07-26.md",
    )
    args = parser.parse_args()

    preflight = _preflight(args.corpus)
    cfg = _fresh_config()
    candidates = _generate_candidates(args.corpus, cfg)
    if not candidates:
        raise RuntimeError("fresh canonical detector produced zero candidates")

    simulations = {
        ticks: _simulate(candidates, args.fine_root, ticks)
        for ticks in (1, 2, 3, 4)
    }
    sensitivity = {}
    for ticks, simulation in simulations.items():
        rows = simulation["rows"]
        sensitivity[str(ticks)] = {
            "full": _stats(rows),
            "H1": _stats([row for row in rows if row["date"] < MIDPOINT]),
            "H2": _stats([row for row in rows if row["date"] >= MIDPOINT]),
            "SHORT": _stats(rows),
            "candidate_while_position_open": simulation[
                "candidate_while_position_open"
            ],
        }
    classification, reasons = _classification(sensitivity["2"])
    results = {
        "meta": {
            **preflight,
            "date_range": [START, END],
            "commission_round_trip": COMMISSION_RT,
            "commission_source": "execution.mnq_strat_evidence.MNQ_COMMISSION_ROUND_TRIP",
            "entry_model": "PaperBroker ioc_limit at completed decision-bar close",
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "exit_model": "runner 1.0R activation / 0.5R trail; 15:55 ET day flatten",
            "candidate_generation": "current DecisionEngine, VWAP Hold isolated in memory",
        },
        "candidate_count": len(candidates),
        "classification": classification,
        "classification_reasons": reasons,
        "baseline_2_tick": sensitivity["2"],
        "sensitivity": sensitivity,
        "raw_candidates": candidates,
        "raw_rows_by_slippage": {
            str(ticks): simulation["rows"]
            for ticks, simulation in simulations.items()
        },
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    args.report.write_text(_render(results).rstrip() + "\n")
    print(
        json.dumps(
            {
                "classification": classification,
                "candidates": len(candidates),
                "baseline": sensitivity["2"]["full"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
