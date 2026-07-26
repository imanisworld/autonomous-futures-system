#!/usr/bin/env python3
"""Settle the 68 corrected-Corpus IOC no-fills under a market counterfactual.

This is an evidence-only matched-cohort analysis.  It reads the exact no-fill
identities produced by ``corrected_ioc_corpus_evidence.py`` and replaces only
their failed IOC entry with an immediately executable decision-close market
fill.  Signal selection, timestamps, stops, targets, contracts, sessions,
static exits, adverse slippage, and pessimistic same-bar resolution stay fixed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.day_only_exit import (  # noqa: E402
    is_after_eod_close,
    is_exact_eod_bar,
    resolve_paper_eod,
    strategy_is_day_only,
)
from execution.paper_broker import (  # noqa: E402
    NextBarOHLC,
    PaperBroker,
    TICK_SIZE,
)

INSTRUMENTS = ("MNQ", "MES")
EXPECTED_NO_FILLS = 68
COMMISSION_ROUND_TRIP = 1.48
BOOTSTRAP_SAMPLES = 20_000
BOOTSTRAP_SEED = 338342


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _corpus_sha256(corpus: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    paths = sorted(corpus.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(corpus).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


def _parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect_no_fill_cohort(logs: Path, raw_attempts: Path) -> list[dict[str, Any]]:
    """Join exact stored no-fill identities to their original approved setups."""
    expected = {
        row["paper_order_id"]: row
        for row in _json_lines(raw_attempts)
        if row.get("cancelled_no_fill") == 1
    }
    if len(expected) != EXPECTED_NO_FILLS:
        raise RuntimeError(
            f"stored corrected-IOC artifact has {len(expected)} no-fills; "
            f"expected {EXPECTED_NO_FILLS}"
        )

    decisions: dict[str, dict[str, Any]] = {}
    cancellations: dict[str, dict[str, Any]] = {}
    for instrument in INSTRUMENTS:
        for path in sorted((logs / instrument).glob("journal_*.jsonl")):
            for row in _json_lines(path):
                if row.get("decision") == "TRADE" and row.get("paper_order_id"):
                    decisions[row["paper_order_id"]] = row
                outcome = row.get("outcome") or {}
                if (
                    row.get("type") == "OUTCOME"
                    and outcome.get("result") == "CANCELLED"
                    and outcome.get("paper_order_id")
                ):
                    cancellations[outcome["paper_order_id"]] = outcome

    missing_decisions = sorted(set(expected) - set(decisions))
    missing_cancellations = sorted(set(expected) - set(cancellations))
    if missing_decisions or missing_cancellations:
        raise RuntimeError(
            "cannot causally reconstruct exact cohort: "
            f"missing_decisions={missing_decisions}, "
            f"missing_cancellations={missing_cancellations}"
        )

    rows: list[dict[str, Any]] = []
    for order_id, original in expected.items():
        decision = decisions[order_id]
        outcome = cancellations[order_id]
        setup = decision.get("setup") or {}
        risk = decision.get("risk_check") or {}
        if risk.get("result") != "APPROVED":
            raise RuntimeError(f"{order_id}: no-fill decision was not risk approved")
        if outcome.get("exit_reason") != "ENTRY_NOT_FILLED":
            raise RuntimeError(
                f"{order_id}: unexpected cancellation {outcome.get('exit_reason')}"
            )
        for key in ("direction", "entry", "stop", "target", "rr_ratio", "strategy"):
            if setup.get(key) is None:
                raise RuntimeError(f"{order_id}: original setup is missing {key}")
        if (
            original["instrument"] != decision["instrument"]
            or original["bar_ts"] != decision["bar_ts"]
            or original["strategy"] != setup["strategy"]
            or original["direction"] != setup["direction"]
        ):
            raise RuntimeError(f"{order_id}: stored artifact/journal identity mismatch")
        rows.append(
            {
                "paper_order_id": order_id,
                "instrument": decision["instrument"],
                "signal_timestamp": decision["bar_ts"],
                "session": decision.get("session") or "unknown",
                "strategy": setup["strategy"],
                "direction": setup["direction"],
                "planned_entry": float(setup["entry"]),
                "stop": float(setup["stop"]),
                "target": float(setup["target"]),
                "rr_ratio": float(setup["rr_ratio"]),
                "notes": setup.get("notes"),
                "contracts": max(1, int(outcome.get("contracts") or 1)),
                "original_outcome": "CANCELLED",
                "original_exit_reason": "ENTRY_NOT_FILLED",
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            row["signal_timestamp"],
            row["instrument"],
            row["paper_order_id"],
        ),
    )


def load_candles(corpus: Path) -> tuple[dict[str, list[dict]], dict[tuple[str, str], int]]:
    candles: dict[str, list[dict]] = {}
    indices: dict[tuple[str, str], int] = {}
    for instrument in INSTRUMENTS:
        lane: list[dict] = []
        for path in sorted((corpus / instrument).glob("*.jsonl")):
            lane.extend(_json_lines(path))
        lane.sort(key=lambda row: _parse_ts(row["timestamp"]))
        for index, candle in enumerate(lane):
            key = (instrument, candle["timestamp"])
            if key in indices:
                raise RuntimeError(f"duplicate candle identity: {key}")
            indices[key] = index
        candles[instrument] = lane
    return candles, indices


def settle_attempt(
    attempt: dict[str, Any],
    candles: dict[str, list[dict]],
    indices: dict[tuple[str, str], int],
    *,
    slippage_ticks: float,
    pessimistic_both_hit: bool,
    breakeven_at_1r: bool,
    runner_mode: bool,
) -> dict[str, Any]:
    """Resolve one no-fill independently, preserving its original future bars."""
    key = (attempt["instrument"], attempt["signal_timestamp"])
    if key not in indices:
        raise RuntimeError(f"{attempt['paper_order_id']}: decision candle missing")
    lane = candles[attempt["instrument"]]
    signal_index = indices[key]
    signal_bar = lane[signal_index]
    decision_close = float(signal_bar["close"])

    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=pessimistic_both_hit,
        breakeven_at_1r=breakeven_at_1r,
        runner_mode=runner_mode,
        entry_fill_model="market",
    )
    order = BracketOrder(
        instrument=attempt["instrument"],
        direction=attempt["direction"],
        entry=attempt["planned_entry"],
        stop=attempt["stop"],
        target=attempt["target"],
        rr_ratio=attempt["rr_ratio"],
        strategy=attempt["strategy"],
        contracts=attempt["contracts"],
        notes=attempt["notes"],
        # This flag makes the evidence model fill at the decision-bar close
        # rather than granting the stale structural entry.  It is local to this
        # counterfactual BracketOrder and changes no strategy/runtime setting.
        force_market_entry=True,
        post_fill_validation_required=False,
    )
    entry_fill = broker.execute_bracket(
        order,
        market_price=decision_close,
        paper_order_id=attempt["paper_order_id"],
    )
    if entry_fill.result != "OPEN":
        raise RuntimeError(
            f"{attempt['paper_order_id']}: market counterfactual did not open"
        )

    tick = TICK_SIZE[attempt["instrument"]]
    expected_entry = decision_close + (
        slippage_ticks * tick
        if attempt["direction"] == "LONG"
        else -slippage_ticks * tick
    )
    if not math.isclose(entry_fill.entry_price, expected_entry):
        raise RuntimeError(f"{attempt['paper_order_id']}: adverse entry slip mismatch")
    bracket_valid = (
        attempt["stop"] < entry_fill.entry_price < attempt["target"]
        if attempt["direction"] == "LONG"
        else attempt["target"] < entry_fill.entry_price < attempt["stop"]
    )
    if not bracket_valid:
        raise RuntimeError(
            f"{attempt['paper_order_id']}: immediate market fill invalidates "
            "the frozen stop/target bracket"
        )

    fill = None
    both_hit_on_exit_bar = False
    signal_date = _parse_ts(attempt["signal_timestamp"]).date()
    day_only = strategy_is_day_only(attempt["strategy"])
    bars_held = 0
    for future in lane[signal_index + 1 :]:
        future_ts = _parse_ts(future["timestamp"])
        if day_only and future_ts.date() != signal_date:
            break
        if day_only and is_after_eod_close(future["timestamp"]):
            break
        bars_held += 1
        both_hit = (
            float(future["high"]) >= attempt["target"]
            and float(future["low"]) <= attempt["stop"]
            if attempt["direction"] == "LONG"
            else float(future["low"]) <= attempt["target"]
            and float(future["high"]) >= attempt["stop"]
        )
        fill = broker.resolve_position(
            NextBarOHLC(
                open=float(future["open"]),
                high=float(future["high"]),
                low=float(future["low"]),
            )
        )
        if fill is not None:
            both_hit_on_exit_bar = bool(both_hit)
            break
        if day_only and is_exact_eod_bar(
            future["timestamp"], future.get("timeframe")
        ):
            fill = resolve_paper_eod(
                broker,
                {
                    "instrument": attempt["instrument"],
                    "direction": attempt["direction"],
                    "entry": entry_fill.entry_price,
                    "contracts": attempt["contracts"],
                    "strategy": attempt["strategy"],
                    "paper_order_id": attempt["paper_order_id"],
                },
                timestamp=future["timestamp"],
                timeframe=future.get("timeframe"),
                close=float(future["close"]),
            )
            break

    resolved = fill is not None and fill.result in {"WIN", "LOSS", "BREAKEVEN"}
    gross = float(fill.pnl_dollars or 0.0) if resolved else 0.0
    return {
        **attempt,
        "entry_model": "decision_close_market_plus_1_tick_adverse",
        "decision_close": decision_close,
        "counterfactual_entry": entry_fill.entry_price,
        "entry_displacement_points": round(
            entry_fill.entry_price - attempt["planned_entry"], 4
        ),
        "bracket_valid_after_fill": bracket_valid,
        "bars_held": bars_held,
        "both_stop_and_target_hit_on_exit_bar": both_hit_on_exit_bar,
        "resolved": int(resolved),
        "open": int(not resolved),
        "result": fill.result if resolved else "OPEN",
        "exit_price": fill.exit_price if resolved else None,
        "exit_reason": fill.exit_reason if resolved else None,
        "pnl_before_commission": round(gross, 2),
        "commission": COMMISSION_ROUND_TRIP if resolved else 0.0,
        "pnl_after_commission": (
            round(gross - COMMISSION_ROUND_TRIP, 2) if resolved else 0.0
        ),
    }


def _profit_factor(values: list[float]) -> float | str | None:
    gains = sum(value for value in values if value > 0)
    losses = abs(sum(value for value in values if value < 0))
    if losses:
        return round(gains / losses, 6)
    return "Infinity" if gains else None


def _max_drawdown(rows: list[dict[str, Any]]) -> float:
    equity = peak = maximum = 0.0
    for row in sorted(
        rows, key=lambda item: (item["signal_timestamp"], item["instrument"])
    ):
        if not row["resolved"]:
            continue
        equity += row["pnl_after_commission"]
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return round(maximum, 2)


def _bootstrap_mean_ci(values: list[float]) -> list[float] | None:
    if not values:
        return None
    rng = random.Random(BOOTSTRAP_SEED)
    size = len(values)
    means = sorted(
        statistics.fmean(rng.choices(values, k=size))
        for _ in range(BOOTSTRAP_SAMPLES)
    )
    return [
        round(means[int(0.025 * BOOTSTRAP_SAMPLES)], 4),
        round(means[int(0.975 * BOOTSTRAP_SAMPLES) - 1], 4),
    ]


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    resolved = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved]
    net = [row["pnl_after_commission"] for row in resolved]
    wins = sum(row["result"] == "WIN" for row in resolved)
    losses = sum(row["result"] == "LOSS" for row in resolved)
    breakeven = sum(row["result"] == "BREAKEVEN" for row in resolved)
    return {
        "attempts": len(rows),
        "resolved": len(resolved),
        "open": len(rows) - len(resolved),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(wins / len(resolved), 6) if resolved else None,
        "net_before_commission": round(sum(gross), 2),
        "commission": round(COMMISSION_ROUND_TRIP * len(resolved), 2),
        "net_after_commission": round(sum(net), 2),
        "expectancy_after_commission": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "expectancy_95pct_bootstrap_ci": _bootstrap_mean_ci(net),
        "profit_factor_after_commission": _profit_factor(net),
        "max_drawdown_after_commission": _max_drawdown(resolved),
        "largest_win_after_commission": (
            round(max(value for value in net if value > 0), 2)
            if any(value > 0 for value in net)
            else None
        ),
        "largest_loss_after_commission": (
            round(min(value for value in net if value < 0), 2)
            if any(value < 0 for value in net)
            else None
        ),
    }


def grouped(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        buckets[str(row[field])].append(row)
    return {key: stats(buckets[key]) for key in sorted(buckets)}


def decision(overall: dict[str, Any]) -> tuple[str, str]:
    ci = overall["expectancy_95pct_bootstrap_ci"]
    pf = overall["profit_factor_after_commission"]
    if (
        overall["net_after_commission"] > 0
        and pf is not None
        and (pf == "Infinity" or pf > 1)
        and ci is not None
        and ci[0] > 0
    ):
        return (
            "NO-FILL COUNTERFACTUAL MATERIALLY PROFITABLE",
            "IOC execution architecture is rejecting or mis-selecting valid signals; investigate entry mechanics.",
        )
    if (
        overall["net_after_commission"] < 0
        and pf is not None
        and pf != "Infinity"
        and pf < 1
        and ci is not None
        and ci[1] < 0
    ):
        return (
            "NO-FILL COUNTERFACTUAL MATERIALLY NEGATIVE",
            "The rejected cohort also loses after costs; do not engineer around IOC.",
        )
    return (
        "MIXED / NEAR BREAKEVEN AFTER COSTS — WAIT",
        "There is no proof of recoverable edge and no basis to change IOC.",
    )


def _money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _pf(value: Any) -> str:
    if value is None:
        return "—"
    return "∞" if value == "Infinity" else f"{value:.3f}"


def _table(title: str, blocks: dict[str, dict[str, Any]]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Scope | Attempts | Resolved | Open | W-L-BE | WR | Gross | Net after $1.48 RT | Exp net | 95% bootstrap CI | PF net | Max DD |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        ci = row["expectancy_95pct_bootstrap_ci"]
        ci_text = "—" if ci is None else f"{_money(ci[0])} to {_money(ci[1])}"
        lines.append(
            f"| {label} | {row['attempts']} | {row['resolved']} | {row['open']} | "
            f"{row['wins']}-{row['losses']}-{row['breakeven']} | "
            f"{_rate(row['win_rate'])} | {_money(row['net_before_commission'])} | "
            f"{_money(row['net_after_commission'])} | "
            f"{_money(row['expectancy_after_commission'])} | {ci_text} | "
            f"{_pf(row['profit_factor_after_commission'])} | "
            f"{_money(row['max_drawdown_after_commission'])} |"
        )
    lines.append("")
    return lines


def render_report(results: dict[str, Any]) -> str:
    overall = results["overall"]
    lines = [
        "# Corrected IOC no-fill counterfactual settling test",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        results["decision_implication"],
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Cohort source: `{results['meta']['source_results_sha']}` "
        f"({EXPECTED_NO_FILLS} exact cancelled order identities)",
        f"Corpus: `{results['meta']['corpus']}`",
        f"Corpus hash: `{results['meta']['corpus_tree_sha256']}` "
        f"({results['meta']['corpus_files']} files)",
        "",
        "## Test contract",
        "",
        "- Primary population is exactly the 68 IOC no-fills selected while the frozen 20% drawdown breaker was enabled in PR #346.",
        "- Each trade is settled independently so counterfactual P&L cannot retroactively change the matched cohort.",
        "- Original signal identity, timestamp, instrument, strategy, session, direction, contracts, stop, target, and static exit logic are unchanged.",
        "- The sole counterfactual change is entry: decision-bar close plus one tick of adverse market slippage (higher for LONG, lower for SHORT).",
        "- Stop exits receive the same one-tick adverse slippage; targets remain resting-limit fills.",
        "- If one future bar contains both stop and target, the stop wins.",
        f"- ${COMMISSION_ROUND_TRIP:.2f} round-trip commission is deducted at the analysis layer.",
        "- No breaker-off full-year diagnostic was run. No runtime, strategy, risk, broker, config, deployment, or Pine logic was changed.",
        "",
    ]
    lines += _table("Primary result", {"68 NO-FILLS": overall})
    lines += _table("By strategy", results["breakdowns"]["strategy"])
    lines += _table("By instrument", results["breakdowns"]["instrument"])
    lines += _table("By session", results["breakdowns"]["session"])
    lines += _table("By direction", results["breakdowns"]["direction"])
    lines += [
        "## Conditional-selection diagnostic",
        "",
        "This comparison is descriptive, not a causal portfolio rerun.",
        "",
    ]
    lines += _table(
        "Filled versus rejected cohorts",
        {
            "REALIZED IOC FILLS": results["selection_diagnostic"][
                "realized_ioc_fills"
            ],
            "REJECTED → MARKET CF": overall,
            "MECHANICAL UNION (NON-CAUSAL)": results["selection_diagnostic"][
                "mechanical_union_noncausal"
            ],
        },
    )
    lines += [
        "## Interpretation boundary",
        "",
        "- This test estimates the outcome of the signals IOC rejected, conditional on the frozen breaker-on selection path.",
        "- It does not rehabilitate the legacy market-fill Corpus v1 result, because it neither reruns that population nor removes the corrected market-condition and replay semantics.",
        "- It does not prove a live market order would receive exactly the modeled fill. The result is an adverse-slippage historical counterfactual, not live-fill evidence.",
        "- Arithmetic combination with the 97 realized IOC fills is not a causal portfolio replay: filling these trades could suppress later attempts while positions are open and could alter breaker timing.",
        "",
        "## Audit checks",
        "",
        f"- Exact stored no-fill identities joined: `{results['audit']['cohort_count']}`.",
        f"- Unique identities: `{results['audit']['unique_identity_count']}`.",
        f"- Decision candles found: `{results['audit']['decision_candles_found']}`.",
        f"- Frozen brackets valid after market fill: `{results['audit']['valid_brackets']}`.",
        f"- Pessimistic both-hit outcomes: `{results['audit']['pessimistic_both_hit_count']}`.",
        f"- Open at corpus end: `{overall['open']}`.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/ioc_no_fill_counterfactual.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --logs /private/tmp/corrected_ioc_corpus_logs \\",
        "  --source-raw scripts/corrected_ioc_corpus_raw_trades.jsonl \\",
        "  --out scripts/ioc_no_fill_counterfactual_results.json \\",
        "  --raw scripts/ioc_no_fill_counterfactual_trades.jsonl \\",
        "  --report docs/ioc-no-fill-counterfactual-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument(
        "--source-raw",
        type=Path,
        default=REPO / "scripts/corrected_ioc_corpus_raw_trades.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts/ioc_no_fill_counterfactual_results.json",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=REPO / "scripts/ioc_no_fill_counterfactual_trades.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/ioc-no-fill-counterfactual-2026-07-26.md",
    )
    args = parser.parse_args()

    config = load_config()
    if config.fill_slippage_ticks != 1.0:
        raise RuntimeError("canonical fill_slippage_ticks is not 1.0")
    if not config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    if config.breakeven_at_1r:
        raise RuntimeError("canonical static exit unexpectedly enables breakeven")
    if config.runner_mode or config.exit_mode != "static":
        raise RuntimeError("canonical exit is not frozen static mode")

    cohort = collect_no_fill_cohort(args.logs, args.source_raw)
    candles, indices = load_candles(args.corpus)
    corpus_files, corpus_hash = _corpus_sha256(args.corpus)
    if corpus_files != 626:
        raise RuntimeError(f"expected 626 corrected corpus files, found {corpus_files}")
    settled = [
        settle_attempt(
            row,
            candles,
            indices,
            slippage_ticks=config.fill_slippage_ticks,
            pessimistic_both_hit=config.fill_pessimistic_both_hit,
            breakeven_at_1r=config.breakeven_at_1r,
            runner_mode=config.runner_mode,
        )
        for row in cohort
    ]
    realized_rows = []
    for row in _json_lines(args.source_raw):
        if row.get("resolved") != 1:
            continue
        realized_rows.append(
            {
                **row,
                "signal_timestamp": row["bar_ts"],
                "commission": COMMISSION_ROUND_TRIP,
            }
        )
    if len(realized_rows) != 97:
        raise RuntimeError(
            f"stored corrected-IOC artifact has {len(realized_rows)} fills; expected 97"
        )
    overall = stats(settled)
    verdict, implication = decision(overall)
    source_sha = subprocess.check_output(
        ["git", "rev-parse", "69ec77f"], cwd=REPO, text=True
    ).strip()
    results = {
        "meta": {
            "main_sha": _git("rev-parse", "HEAD"),
            "source_results_sha": source_sha,
            "source_raw": str(args.source_raw),
            "source_raw_sha256": _sha256(args.source_raw),
            "corpus": str(args.corpus),
            "corpus_files": corpus_files,
            "corpus_tree_sha256": corpus_hash,
            "logs": str(args.logs),
            "entry_model": "decision_close_market_plus_1_tick_adverse",
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "bootstrap_samples": BOOTSTRAP_SAMPLES,
            "bootstrap_seed": BOOTSTRAP_SEED,
            "breaker_primary": "on; exact cohort selected by PR #346 frozen run",
            "breaker_off_diagnostic_run": False,
        },
        "verdict": verdict,
        "decision_implication": implication,
        "overall": overall,
        "breakdowns": {
            field: grouped(settled, field)
            for field in ("strategy", "instrument", "session", "direction")
        },
        "selection_diagnostic": {
            "realized_ioc_fills": stats(realized_rows),
            # This union answers only the mechanical attribution question.
            # It is not a replay: filled positions could suppress later signals
            # and alter breaker timing.
            "mechanical_union_noncausal": stats(realized_rows + settled),
        },
        "audit": {
            "cohort_count": len(settled),
            "unique_identity_count": len(
                {row["paper_order_id"] for row in settled}
            ),
            "decision_candles_found": sum(
                (row["instrument"], row["signal_timestamp"]) in indices
                for row in settled
            ),
            "valid_brackets": sum(
                row["bracket_valid_after_fill"] for row in settled
            ),
            "pessimistic_both_hit_count": sum(
                row["exit_reason"] == "STOP_HIT"
                and row["both_stop_and_target_hit_on_exit_bar"]
                for row in settled
            ),
        },
    }
    if results["audit"]["cohort_count"] != EXPECTED_NO_FILLS:
        raise RuntimeError("counterfactual cohort changed")
    if results["audit"]["unique_identity_count"] != EXPECTED_NO_FILLS:
        raise RuntimeError("counterfactual identities are not unique")
    if results["audit"]["valid_brackets"] != EXPECTED_NO_FILLS:
        raise RuntimeError("not all frozen brackets remained executable")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")
    with args.raw.open("w", encoding="utf-8") as handle:
        for row in settled:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    args.report.write_text(render_report(results).rstrip() + "\n")
    print(json.dumps({"verdict": verdict, "overall": overall}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
