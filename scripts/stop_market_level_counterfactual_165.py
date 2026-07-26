#!/usr/bin/env python3
"""Stop-market-at-the-level counterfactual on the 165 PR #346 IOC attempts.

Evidence orchestration ONLY.  No strategy, replay, broker, risk, config,
deployment, or Pine behavior is changed.  Operator-scoped follow-up to
PR #354 (market-entry counterfactual):

    #354 showed the 68 IOC no-fills WERE the better trades (66.2% WR) but
    that chasing them at the 15m decision-bar close (mean 61 ticks past the
    plan level) leaves near-breakeven money, while the 97 IOC fills lose
    identically under any entry.  The remaining question: is the residual
    negativity a BAD SIGNAL or BAD 15-MINUTE REACTION LATENCY?  The
    discriminator is entering AT the planned level with an aggressive
    stop-market style fill and realistic adverse slippage.

Two entry variants over the SAME frozen 165 attempts, plans, stops,
targets, costs and pessimistic exits — nothing else differs:

- LEVEL (primary, zero-latency bound): fill at the planned entry level ±
  adverse slippage, valid only when the decision bar actually traded the
  level (bar low ≤ level ≤ bar high; otherwise NOT_TRIGGERED_INTRABAR,
  reported, no trade).  Bracket validity at fill enforced exactly like the
  production stop-entry activation (stop < fill < target).  The decision
  bar's own range is then handled PESSIMISTICALLY: if the bar's range
  reaches the ordered stop, the attempt books an immediate LOSS at
  stop ± slip (intrabar order is unknowable — adverse outcome assumed,
  consistent with ``fill_pessimistic_both_hit``); a bar range reaching the
  target is NEVER awarded (the favorable extreme may predate the trigger)
  — the position instead carries into the production
  ``PaperBroker.resolve_position`` walk over subsequent bars (same-day
  first, then the post-#339/#342 cross-day carry-forward semantics).  This
  is the upper bound a zero-latency engine could reach, priced honestly.
- ARMED (secondary, shipped-architecture bound): the production
  ``entry_fill_model="stop_market"`` exactly as built — armed at the
  decision bar's close, one-next-bar causal activation (gap through the
  trigger fills at next open ± slip; an intrabar touch fills at the level
  ± slip; otherwise fails closed as CANCELLED), then the same production
  resolution walk.  This is what TODAY's 15m architecture could do with a
  stop order, zero custom semantics.

Commission ($1.48 RT) at the analysis layer; slippage tiers 1/2/3/4 ticks
(1 tick = primary, the #346/#354 canonical posture).

Verdict rules (pre-registered before results were seen), on the LEVEL
variant's full matched population at 1-tick slippage, per operator
direction:
- net after commission ≤ 0 or PF ≤ 1  → strategy logic is the problem;
  stop investigating execution.
- positive at 1 tick but not at 2     → marginal; not a research lane.
- positive (net > 0 AND PF > 1) at BOTH 1 and 2 ticks → 15m reaction
  latency is material; entry timing/execution architecture becomes a
  legitimate research lane.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.day_only_exit import strategy_is_day_only  # noqa: E402
from execution.paper_broker import (  # noqa: E402
    NextBarOHLC,
    PaperBroker,
    TICK_SIZE,
    TICK_VALUE,
)
from replay.candle_loader import ReplayCandleLoader  # noqa: E402

INSTRUMENTS = ("MNQ", "MES")
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
COMMISSION_ROUND_TRIP = 1.48
SLIPPAGE_TIERS = (1.0, 2.0, 3.0, 4.0)
PRIMARY_SLIPPAGE = 1.0
EXPECTED_ATTEMPTS = 165
EXPECTED_IOC_FILLS = 97
EXPECTED_IOC_NO_FILLS = 68
EXPECTED_CORPUS_FILES = 626
PR346_CORPUS_TREE_SHA256 = (
    "4ab5812659910235e8a26e7417f851e0a403855ff75183322e99b0b36970d3d4"
)
REFERENCES = {
    "pr346_ioc_system": {"net_after_commission": -802.28, "pf": 0.753, "wr": 0.268},
    "pr354_market_cf_all165": {
        "net_after_commission": -689.32,
        "pf": 0.861,
        "wr": 0.430,
    },
    "pr354_market_cf_nofill68": {
        "net_after_commission": 118.46,
        "pf": 1.069,
        "wr": 0.662,
    },
}


def _sha256_tree(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.jsonl")):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(sorted(root.rglob("*.jsonl"))), digest.hexdigest()


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=REPO, text=True).strip()


def _json_lines(path: Path) -> Iterable[dict[str, Any]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def _period_label(value: str, periods: dict[str, tuple[str, str]]) -> str:
    for label, (start, end) in periods.items():
        if start <= value <= end:
            return label
    return "OUT_OF_RANGE"


def _norm_ts(value: str) -> str:
    from datetime import datetime

    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()


def _load_attempts(raw_path: Path, logs_root: Path) -> list[dict]:
    """Join the committed 165-attempt list to the preserved #346 journals.

    Identical join contract to scripts/market_entry_counterfactual_165.py
    (PR #354): identity by paper_order_id, full plan from the TRADE row,
    contracts from the OUTCOME row, day-only strategies rejected.
    """
    attempts = list(_json_lines(raw_path))
    if len(attempts) != EXPECTED_ATTEMPTS:
        raise RuntimeError(
            f"expected {EXPECTED_ATTEMPTS} committed attempts, found {len(attempts)}"
        )
    if sum(row["filled"] for row in attempts) != EXPECTED_IOC_FILLS:
        raise RuntimeError("committed attempt list fill-count mismatch vs PR #346")
    if sum(row["cancelled_no_fill"] for row in attempts) != EXPECTED_IOC_NO_FILLS:
        raise RuntimeError("committed attempt list no-fill-count mismatch vs PR #346")

    plans: dict[str, dict] = {}
    contracts_by_id: dict[str, int] = {}
    for instrument in INSTRUMENTS:
        for path in sorted((logs_root / instrument).glob("journal_*.jsonl")):
            for entry in _json_lines(path):
                if entry.get("type") == "OUTCOME":
                    outcome = entry.get("outcome") or {}
                    order_id = outcome.get("paper_order_id")
                    if order_id and outcome.get("contracts"):
                        contracts_by_id[order_id] = int(outcome["contracts"])
                    continue
                if entry.get("decision") != "TRADE":
                    continue
                order_id = entry.get("paper_order_id")
                if order_id:
                    if order_id in plans:
                        raise RuntimeError(f"duplicate TRADE identity {order_id}")
                    plans[order_id] = entry

    joined: list[dict] = []
    for row in attempts:
        order_id = row["paper_order_id"]
        plan = plans.get(order_id)
        if plan is None:
            raise RuntimeError(
                f"attempt {order_id} has no TRADE row in the preserved #346 logs"
            )
        setup = plan.get("setup") or {}
        for field in ("entry", "stop", "target", "direction", "strategy"):
            if setup.get(field) in (None, ""):
                raise RuntimeError(f"attempt {order_id} missing setup.{field}")
        if setup["direction"] != row["direction"] or setup["strategy"] != row["strategy"]:
            raise RuntimeError(f"attempt {order_id} setup/committed-row mismatch")
        if strategy_is_day_only(setup["strategy"]):
            raise RuntimeError(f"attempt {order_id} uses a DAY_ONLY strategy")
        contracts = contracts_by_id.get(order_id)
        if not contracts or contracts < 1:
            raise RuntimeError(f"attempt {order_id} has no journaled contracts")
        joined.append(
            {
                **row,
                "bar_ts": _norm_ts(row["bar_ts"]),
                "plan_entry": float(setup["entry"]),
                "plan_stop": float(setup["stop"]),
                "plan_target": float(setup["target"]),
                "rr_ratio": float(setup.get("rr_ratio") or 0.0),
                "contracts": contracts,
            }
        )
    return joined


class _CorpusIndex:
    def __init__(self, corpus: Path) -> None:
        self._loader = ReplayCandleLoader()
        self._files: dict[str, list[tuple[str, Path]]] = {}
        self._cache: dict[Path, list] = {}
        for instrument in INSTRUMENTS:
            self._files[instrument] = [
                (path.stem.rsplit("_", 1)[-1], path)
                for path in sorted((corpus / instrument).glob("*.jsonl"))
            ]

    def candles(self, path: Path) -> list:
        if path not in self._cache:
            self._cache[path] = self._loader.load_jsonl(path)
            if len(self._cache) > 8:
                self._cache.pop(next(iter(self._cache)))
        return self._cache[path]

    def day_sequence(self, instrument: str, date: str) -> list[Path]:
        days = self._files[instrument]
        started = [path for day, path in days if day >= date]
        if not started or not any(day == date for day, _ in days):
            raise RuntimeError(f"corpus day {instrument}/{date} not found")
        return started


def _fresh_broker(slippage_ticks: float, entry_fill_model: str) -> PaperBroker:
    return PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model=entry_fill_model,
    )


def _walk_resolution(
    broker: PaperBroker,
    corpus: _CorpusIndex,
    instrument: str,
    day_paths: list[Path],
    decision_idx: int,
):
    """Production resolve_position walk: rest of decision day, then later
    day files (cross-day carry semantics).  Returns (fill, bars, crossed)."""
    bars_seen = 0
    for day_number, path in enumerate(day_paths):
        candles = corpus.candles(path)
        start = decision_idx + 1 if day_number == 0 else 0
        for fc in candles[start:]:
            if fc.instrument != instrument:
                continue
            bars_seen += 1
            fill = broker.resolve_position(
                NextBarOHLC(open=fc.open, high=fc.high, low=fc.low)
            )
            if fill is not None:
                return fill, bars_seen, day_number > 0
    return None, bars_seen, False


def _base_row(attempt: dict) -> dict:
    return {
        "paper_order_id": attempt["paper_order_id"],
        "date": attempt["date"],
        "bar_ts": attempt["bar_ts"],
        "instrument": attempt["instrument"],
        "strategy": attempt["strategy"],
        "direction": attempt["direction"],
        "half": _period_label(attempt["date"], HALVES),
        "quarter": _period_label(attempt["date"], QUARTERS),
        "ioc_filled": int(attempt["filled"]),
        "contracts": attempt["contracts"],
        "plan_entry": attempt["plan_entry"],
        "plan_stop": attempt["plan_stop"],
        "plan_target": attempt["plan_target"],
    }


def _finalize_row(
    row: dict,
    *,
    triggered: bool,
    no_trade_reason: Optional[str],
    entry_price: Optional[float],
    fill: Optional[Any],
    bars: Optional[int],
    crossed: bool,
    immediate: Optional[dict] = None,
) -> dict:
    if immediate is not None:
        resolved = True
        result = immediate["result"]
        exit_reason = immediate["exit_reason"]
        exit_price = immediate["exit_price"]
        pnl_gross = immediate["pnl_dollars"]
    elif fill is not None and fill.result in {"WIN", "LOSS", "BREAKEVEN"}:
        resolved = True
        result = fill.result
        exit_reason = fill.exit_reason
        exit_price = float(fill.exit_price)
        pnl_gross = float(fill.pnl_dollars or 0.0)
    else:
        resolved = False
        result = None
        exit_reason = None
        exit_price = None
        pnl_gross = 0.0
    row.update(
        {
            "triggered": int(triggered),
            "no_trade_reason": no_trade_reason,
            "cf_entry_price": entry_price,
            "resolved": int(resolved),
            "open_unresolved": int(triggered and not resolved),
            "result": result,
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "bars_to_resolve": bars if resolved else None,
            "crossed_day": int(crossed) if resolved else None,
            "decision_bar_immediate_stop": int(bool(immediate)),
            "pnl_before_commission": round(pnl_gross, 2),
            "pnl_after_commission": round(
                pnl_gross - COMMISSION_ROUND_TRIP if resolved else 0.0, 2
            ),
        }
    )
    return row


def _simulate_level(
    attempt: dict, corpus: _CorpusIndex, *, slippage_ticks: float
) -> dict:
    """LEVEL variant: zero-latency at-level fill, pessimistic decision bar."""
    instrument = attempt["instrument"]
    tick = TICK_SIZE[instrument]
    tick_value = TICK_VALUE[instrument]
    day_paths = corpus.day_sequence(instrument, attempt["date"])
    candles = corpus.candles(day_paths[0])
    decision_idx = next(
        (
            idx
            for idx, candle in enumerate(candles)
            if candle.instrument == instrument
            and _norm_ts(candle.timestamp) == attempt["bar_ts"]
        ),
        None,
    )
    if decision_idx is None:
        raise RuntimeError(
            f"decision bar {attempt['bar_ts']} not found for {instrument}"
        )
    bar = candles[decision_idx]
    row = _base_row(attempt)
    row["decision_close"] = float(bar.close)

    level = attempt["plan_entry"]
    direction = attempt["direction"]
    slip = slippage_ticks * tick
    if not (float(bar.low) <= level <= float(bar.high)):
        return _finalize_row(
            row,
            triggered=False,
            no_trade_reason="NOT_TRIGGERED_INTRABAR",
            entry_price=None,
            fill=None,
            bars=None,
            crossed=False,
        )
    fill_entry = level + slip if direction == "LONG" else level - slip
    bracket_ok = (
        attempt["plan_stop"] < fill_entry < attempt["plan_target"]
        if direction == "LONG"
        else attempt["plan_target"] < fill_entry < attempt["plan_stop"]
    )
    if not bracket_ok:
        return _finalize_row(
            row,
            triggered=False,
            no_trade_reason="ENTRY_BRACKET_INVALID_AT_FILL",
            entry_price=None,
            fill=None,
            bars=None,
            crossed=False,
        )

    stop_range_hit = (
        float(bar.low) <= attempt["plan_stop"]
        if direction == "LONG"
        else float(bar.high) >= attempt["plan_stop"]
    )
    if stop_range_hit:
        # Pessimistic: the adverse extreme is assumed to follow the trigger.
        exit_price = (
            attempt["plan_stop"] - slip
            if direction == "LONG"
            else attempt["plan_stop"] + slip
        )
        ticks = (
            (exit_price - fill_entry) if direction == "LONG" else (fill_entry - exit_price)
        ) / tick
        return _finalize_row(
            row,
            triggered=True,
            no_trade_reason=None,
            entry_price=fill_entry,
            fill=None,
            bars=0,
            crossed=False,
            immediate={
                "result": "LOSS",
                "exit_reason": "STOP_HIT",
                "exit_price": exit_price,
                "pnl_dollars": ticks * tick_value * attempt["contracts"],
            },
        )

    broker = _fresh_broker(slippage_ticks, "market")
    broker.restore_position(
        instrument=instrument,
        direction=direction,
        entry=fill_entry,
        stop=attempt["plan_stop"],
        target=attempt["plan_target"],
        contracts=attempt["contracts"],
        paper_order_id=attempt["paper_order_id"],
    )
    fill, bars, crossed = _walk_resolution(
        broker, corpus, instrument, day_paths, decision_idx
    )
    return _finalize_row(
        row,
        triggered=True,
        no_trade_reason=None,
        entry_price=fill_entry,
        fill=fill,
        bars=bars,
        crossed=crossed,
    )


def _simulate_armed(
    attempt: dict, corpus: _CorpusIndex, *, slippage_ticks: float
) -> dict:
    """ARMED variant: the shipped one-next-bar stop_market model, unmodified."""
    instrument = attempt["instrument"]
    day_paths = corpus.day_sequence(instrument, attempt["date"])
    candles = corpus.candles(day_paths[0])
    decision_idx = next(
        (
            idx
            for idx, candle in enumerate(candles)
            if candle.instrument == instrument
            and _norm_ts(candle.timestamp) == attempt["bar_ts"]
        ),
        None,
    )
    if decision_idx is None:
        raise RuntimeError(
            f"decision bar {attempt['bar_ts']} not found for {instrument}"
        )
    row = _base_row(attempt)
    row["decision_close"] = float(candles[decision_idx].close)

    broker = _fresh_broker(slippage_ticks, "stop_market")
    order = BracketOrder(
        instrument=instrument,
        direction=attempt["direction"],
        entry=attempt["plan_entry"],
        stop=attempt["plan_stop"],
        target=attempt["plan_target"],
        rr_ratio=attempt["rr_ratio"],
        strategy=attempt["strategy"],
        contracts=attempt["contracts"],
        post_fill_validation_required=False,
    )
    pending = broker.execute_bracket(
        order, paper_order_id=attempt["paper_order_id"]
    )
    if pending.result != "PENDING":
        raise RuntimeError(
            f"stop_market arm unexpectedly {pending.result} for "
            f"{attempt['paper_order_id']}"
        )
    fill, bars, crossed = _walk_resolution(
        broker, corpus, instrument, day_paths, decision_idx
    )
    if fill is None and broker.has_pending_entry():
        fill = broker.cancel_pending_entry("ENTRY_NO_NEXT_BAR")
    if fill is not None and fill.result == "CANCELLED":
        return _finalize_row(
            row,
            triggered=False,
            no_trade_reason=fill.exit_reason or fill.no_fill_reason,
            entry_price=None,
            fill=None,
            bars=None,
            crossed=False,
        )
    entry_price = None
    position = broker.get_position()
    if fill is not None:
        entry_price = float(fill.entry_price)
    elif position is not None:
        entry_price = float(position.entry_price)
    return _finalize_row(
        row,
        triggered=True,
        no_trade_reason=None,
        entry_price=entry_price,
        fill=fill,
        bars=bars,
        crossed=crossed,
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and math.isinf(value):
        return "inf"
    return value


def _profit_factor(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


def _stats(rows: list[dict]) -> dict:
    resolved_rows = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved_rows]
    net = [row["pnl_after_commission"] for row in resolved_rows]
    wins = sum(row["result"] == "WIN" for row in resolved_rows)
    return {
        "attempts": len(rows),
        "triggered": sum(row["triggered"] for row in rows),
        "not_triggered": sum(1 for row in rows if not row["triggered"]),
        "resolved": len(resolved_rows),
        "open_unresolved": sum(row["open_unresolved"] for row in rows),
        "wins": wins,
        "losses": sum(row["result"] == "LOSS" for row in resolved_rows),
        "breakeven": sum(row["result"] == "BREAKEVEN" for row in resolved_rows),
        "decision_bar_immediate_stops": sum(
            row["decision_bar_immediate_stop"] for row in rows
        ),
        "win_rate": round(wins / len(resolved_rows), 6) if resolved_rows else None,
        "net_before_commission": round(sum(gross), 2),
        "net_after_commission": round(sum(net), 2),
        "expectancy_after_commission": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "profit_factor_after_commission": _profit_factor(net),
        "largest_win_after_commission": round(max(net), 2) if net else None,
        "largest_loss_after_commission": round(min(net), 2) if net else None,
        "crossed_day_resolutions": sum(row["crossed_day"] or 0 for row in resolved_rows),
    }


def _group(rows: list[dict], field: str, labels: Iterable[str] | None = None) -> dict:
    keys = list(labels or sorted({str(row[field]) for row in rows}))
    return {
        key: _stats([row for row in rows if str(row[field]) == key]) for key in keys
    }


def _verify_independently(rows: list[dict], stats: dict) -> None:
    """Recompute every resolved row's P&L from raw prices; fail on mismatch.
    Result class is never inferred from P&L sign (see PR #354's note on
    degenerate entries)."""
    resolved = [r for r in rows if r["result"] in {"WIN", "LOSS", "BREAKEVEN"}]
    total = 0.0
    win_count = 0
    for r in resolved:
        tick = TICK_SIZE[r["instrument"]]
        tick_value = TICK_VALUE[r["instrument"]]
        if r["direction"] == "LONG":
            ticks = (r["exit_price"] - r["cf_entry_price"]) / tick
        else:
            ticks = (r["cf_entry_price"] - r["exit_price"]) / tick
        dollars = ticks * tick_value * r["contracts"]
        if abs(dollars - r["pnl_before_commission"]) > 0.011:
            raise RuntimeError(
                f"independent price-arithmetic mismatch for {r['paper_order_id']}: "
                f"{dollars:.2f} vs {r['pnl_before_commission']:.2f}"
            )
        total += r["pnl_before_commission"] - COMMISSION_ROUND_TRIP
        if r["result"] == "WIN":
            win_count += 1
    if abs(round(total, 2) - stats["net_after_commission"]) > 0.011:
        raise RuntimeError("independent net mismatch")
    if resolved and abs(win_count / len(resolved) - (stats["win_rate"] or 0.0)) > 1e-6:
        raise RuntimeError("independent win-rate mismatch")


def _fmt_money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    return "∞" if isinstance(value, float) and math.isinf(value) else f"{value:.3f}"


def _table(title: str, blocks: dict[str, dict]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Scope | Attempts | Triggered | Resolved | Open | Imm. stop | WR | Net gross | Net after $1.48 RT | Exp net | PF net |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        lines.append(
            f"| {label} | {row['attempts']} | {row['triggered']} | {row['resolved']} | "
            f"{row['open_unresolved']} | {row['decision_bar_immediate_stops']} | "
            f"{_fmt_rate(row['win_rate'])} | {_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | "
            f"{_fmt_money(row['expectancy_after_commission'])} | "
            f"{_fmt_pf(row['profit_factor_after_commission'])} |"
        )
    lines.append("")
    return lines


def _render_report(results: dict) -> str:
    level1 = results["variants"]["LEVEL"]["tiers"]["slippage_1"]
    armed1 = results["variants"]["ARMED"]["tiers"]["slippage_1"]
    lv = level1["cohorts"]["ALL_165"]
    lines = [
        "# Stop-market-at-the-level counterfactual on the 165 corrected-IOC attempts",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Corpus: `{results['meta']['corpus_tree_sha256']}` "
        f"({results['meta']['corpus_files']} files — byte-identical to PR #346's corpus)",
        f"Attempt population: the {EXPECTED_ATTEMPTS} committed PR #346 attempts "
        "(same join contract as PR #354, 165/165 by `paper_order_id`).",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Question and posture",
        "",
        "- #346: IOC-limit system loses (PF 0.753). #354: honest market entry at "
        "the 15m decision-bar close still loses overall (PF 0.861); the 68 missed "
        "attempts were the better trades but the ~61-tick chase leaves ~breakeven.",
        "- Remaining question (operator-scoped): bad signal, or bad 15-minute "
        "reaction latency? Discriminator: enter AT the planned level, aggressive "
        "stop-market fill, realistic adverse slippage. Same frozen signals, "
        "stops, targets, costs, pessimistic exits. Nothing else changes.",
        "- **LEVEL** (primary): zero-latency bound — fill at level ± slip, valid "
        "only if the decision bar traded the level; decision-bar range handled "
        "pessimistically (range touching the ordered stop books an immediate "
        "LOSS; a range touching the target is never awarded — the position must "
        "prove out on later bars via the production resolver).",
        "- **ARMED** (secondary): the shipped `entry_fill_model=\"stop_market\"` "
        "exactly as built — armed at decision close, one-next-bar causal "
        "activation (gap → next open ± slip; touch → level ± slip; else fails "
        "closed), production resolution. What today's architecture could do.",
        f"- ${COMMISSION_ROUND_TRIP:.2f} RT commission, analysis layer; slippage "
        "tiers 1/2/3/4 ticks (1 = primary). Isolated per-attempt brokers "
        "(no breaker — #346 owns the system path). All 165 attempts are H1.",
        "- Evidence orchestration only: zero strategy/replay/broker/risk/config/"
        "deployment/Pine edits.",
        "",
        "## Pre-registered decision rule (operator's)",
        "",
        "- LEVEL all-165 net ≤ 0 or PF ≤ 1 at 1 tick → strategy logic is the "
        "problem; stop investigating execution.",
        "- Positive at 1 tick only → marginal; not a research lane.",
        "- Net > 0 AND PF > 1 at both 1 and 2 ticks → latency is material; entry "
        "timing/execution architecture becomes a legitimate research lane.",
        "",
    ]
    lines += _table(
        "LEVEL variant (zero-latency bound) — cohorts at 1 tick",
        {
            "ALL 165": lv,
            "IOC_NO_FILL 68": level1["cohorts"]["IOC_NO_FILL_68"],
            "IOC_FILLED 97": level1["cohorts"]["IOC_FILLED_97"],
        },
    )
    lines += _table(
        "ARMED variant (shipped stop_market) — cohorts at 1 tick",
        {
            "ALL 165": armed1["cohorts"]["ALL_165"],
            "IOC_NO_FILL 68": armed1["cohorts"]["IOC_NO_FILL_68"],
            "IOC_FILLED 97": armed1["cohorts"]["IOC_FILLED_97"],
        },
    )
    lines += _table("LEVEL by strategy (1 tick)", level1["breakdowns"]["strategy"])
    lines += _table("LEVEL by instrument (1 tick)", level1["breakdowns"]["instrument"])
    lines += _table(
        "LEVEL no-fill cohort by strategy (1 tick)",
        level1["breakdowns"]["nofill_strategy"],
    )
    lines += [
        "## Slippage sensitivity (net after commission / PF)",
        "",
        "| Slippage | LEVEL all-165 | LEVEL no-fill-68 | ARMED all-165 |",
        "|---|---:|---:|---:|",
    ]
    for tier in SLIPPAGE_TIERS:
        lv_t = results["variants"]["LEVEL"]["tiers"][f"slippage_{tier:g}"]["cohorts"]
        ar_t = results["variants"]["ARMED"]["tiers"][f"slippage_{tier:g}"]["cohorts"]
        lines.append(
            f"| {tier:g} tick | "
            f"{_fmt_money(lv_t['ALL_165']['net_after_commission'])} / "
            f"{_fmt_pf(lv_t['ALL_165']['profit_factor_after_commission'])} | "
            f"{_fmt_money(lv_t['IOC_NO_FILL_68']['net_after_commission'])} / "
            f"{_fmt_pf(lv_t['IOC_NO_FILL_68']['profit_factor_after_commission'])} | "
            f"{_fmt_money(ar_t['ALL_165']['net_after_commission'])} / "
            f"{_fmt_pf(ar_t['ALL_165']['profit_factor_after_commission'])} |"
        )
    lines += [
        "",
        "## Comparison ladder (all matched populations, 1 tick, net after commission)",
        "",
        "| Pass | Entry | Net | PF | WR |",
        "|---|---|---:|---:|---:|",
        "| #346 (97 fills, system) | IOC limit at level | $-802.28 | 0.753 | 26.8% |",
        "| #354 all-165 | market at decision close | $-689.32 | 0.861 | 43.0% |",
        (
            f"| LEVEL all-165 | at-level, zero latency | "
            f"{_fmt_money(lv['net_after_commission'])} | "
            f"{_fmt_pf(lv['profit_factor_after_commission'])} | "
            f"{_fmt_rate(lv['win_rate'])} |"
        ),
        (
            f"| ARMED all-165 | shipped stop_market | "
            f"{_fmt_money(armed1['cohorts']['ALL_165']['net_after_commission'])} | "
            f"{_fmt_pf(armed1['cohorts']['ALL_165']['profit_factor_after_commission'])} | "
            f"{_fmt_rate(armed1['cohorts']['ALL_165']['win_rate'])} |"
        ),
        "",
        "## Audit and limitations",
        "",
        f"- LEVEL: {lv['triggered']}/165 triggered intrabar "
        f"({lv['not_triggered']} not triggered / bracket-invalid, reported not "
        f"traded); {lv['decision_bar_immediate_stops']} pessimistic decision-bar "
        f"immediate stops; {lv['open_unresolved']} unresolved at corpus end; "
        f"{lv['crossed_day_resolutions']} cross-day resolutions.",
        "- LEVEL entry is a retroactive zero-latency BOUND, not an implementable "
        "order: it assumes reaction at the exact level during the decision bar. "
        "Its pessimistic decision-bar rule (stop-range → immediate loss; "
        "target-range never awarded same-bar) biases it conservative.",
        "- ARMED is fully causal and implementable today, but arms only at the "
        "decision-bar close (one-next-bar model as shipped) — it still carries "
        "the 15m latency and gap-fills runaways at the next open.",
        "- Every resolved row's P&L independently recomputed from raw prices; "
        "join, corpus hash, and cohort splits asserted as in PR #354.",
        "- All 165 attempts are H1 (inherits #346's breaker censoring); replay-"
        "scale dollars; historical evidence, not live-fill proof.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/stop_market_level_counterfactual_165.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --logs /private/tmp/corrected_ioc_corpus_logs \\",
        "  --raw-attempts scripts/corrected_ioc_corpus_raw_trades.jsonl \\",
        "  --out scripts/stop_market_level_counterfactual_165_results.json \\",
        "  --raw scripts/stop_market_level_counterfactual_165_raw.jsonl \\",
        "  --report docs/stop-market-level-counterfactual-165-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--logs", required=True, type=Path)
    parser.add_argument(
        "--raw-attempts",
        type=Path,
        default=REPO / "scripts/corrected_ioc_corpus_raw_trades.jsonl",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts/stop_market_level_counterfactual_165_results.json",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=REPO / "scripts/stop_market_level_counterfactual_165_raw.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/stop-market-level-counterfactual-165-2026-07-26.md",
    )
    args = parser.parse_args()

    config = load_config()
    if config.fill_slippage_ticks != PRIMARY_SLIPPAGE:
        raise RuntimeError("canonical fill_slippage_ticks is not 1.0")
    if not config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")

    corpus_files, corpus_hash = _sha256_tree(args.corpus)
    if corpus_files != EXPECTED_CORPUS_FILES:
        raise RuntimeError(
            f"expected {EXPECTED_CORPUS_FILES} corpus files, found {corpus_files}"
        )
    if corpus_hash != PR346_CORPUS_TREE_SHA256:
        raise RuntimeError("corpus tree hash differs from PR #346's documented corpus")

    attempts = _load_attempts(args.raw_attempts, args.logs)
    corpus = _CorpusIndex(args.corpus)

    variants: dict[str, dict] = {}
    primary_rows: dict[str, list[dict]] = {}
    for variant, simulate in (("LEVEL", _simulate_level), ("ARMED", _simulate_armed)):
        tiers: dict[str, dict] = {}
        for tier in SLIPPAGE_TIERS:
            rows = [
                simulate(attempt, corpus, slippage_ticks=tier) for attempt in attempts
            ]
            all_stats = _stats(rows)
            _verify_independently(rows, all_stats)
            nofill_rows = [row for row in rows if not row["ioc_filled"]]
            filled_rows = [row for row in rows if row["ioc_filled"]]
            if len(nofill_rows) != EXPECTED_IOC_NO_FILLS:
                raise RuntimeError("no-fill cohort size drifted")
            tiers[f"slippage_{tier:g}"] = {
                "cohorts": {
                    "ALL_165": all_stats,
                    "IOC_NO_FILL_68": _stats(nofill_rows),
                    "IOC_FILLED_97": _stats(filled_rows),
                },
                "breakdowns": {
                    "instrument": _group(rows, "instrument", INSTRUMENTS),
                    "strategy": _group(rows, "strategy"),
                    "direction": _group(rows, "direction", ("LONG", "SHORT")),
                    "half": _group(rows, "half", HALVES),
                    "quarter": _group(rows, "quarter", QUARTERS),
                    "nofill_strategy": _group(nofill_rows, "strategy"),
                },
                "no_trade_reasons": dict(
                    sorted(
                        (
                            (reason, sum(1 for r in rows if r["no_trade_reason"] == reason))
                            for reason in {
                                r["no_trade_reason"]
                                for r in rows
                                if r["no_trade_reason"]
                            }
                        ),
                        key=lambda item: -item[1],
                    )
                ),
            }
            if tier == PRIMARY_SLIPPAGE:
                primary_rows[variant] = rows
        variants[variant] = {"tiers": tiers}

    def _positive(block: dict) -> bool:
        return (
            (block["net_after_commission"] or 0) > 0
            and block["profit_factor_after_commission"] is not None
            and not isinstance(block["profit_factor_after_commission"], str)
            and block["profit_factor_after_commission"] > 1
        )

    level_1 = variants["LEVEL"]["tiers"]["slippage_1"]["cohorts"]["ALL_165"]
    level_2 = variants["LEVEL"]["tiers"]["slippage_2"]["cohorts"]["ALL_165"]
    if not _positive(level_1):
        verdict = (
            "STRATEGY LOGIC IS THE PROBLEM — ZERO-LATENCY AT-LEVEL ENTRY IS "
            "STILL NEGATIVE; STOP INVESTIGATING EXECUTION"
        )
    elif not _positive(level_2):
        verdict = (
            "MARGINAL — POSITIVE ONLY AT 1-TICK SLIPPAGE; NOT A RESEARCH LANE "
            "UNDER THE PRE-REGISTERED RULE"
        )
    else:
        verdict = (
            "LATENCY IS MATERIAL — AT-LEVEL ENTRY POSITIVE AT 1 AND 2 TICKS; "
            "ENTRY TIMING/EXECUTION ARCHITECTURE IS A LEGITIMATE RESEARCH LANE"
        )

    results = {
        "meta": {
            "main_sha": _git("rev-parse", "HEAD"),
            "range": list(FULL_RANGE),
            "corpus": str(args.corpus),
            "corpus_files": corpus_files,
            "corpus_tree_sha256": corpus_hash,
            "pr346_corpus_tree_sha256": PR346_CORPUS_TREE_SHA256,
            "attempt_source": str(args.raw_attempts),
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "slippage_tiers": list(SLIPPAGE_TIERS),
            "primary_slippage": PRIMARY_SLIPPAGE,
            "variants": {
                "LEVEL": (
                    "zero-latency bound: fill at plan level ± adverse slip iff "
                    "decision bar traded the level; pessimistic decision-bar "
                    "handling (stop-range → immediate LOSS at stop ± slip; "
                    "target-range never awarded same-bar); production "
                    "resolve_position walk afterwards"
                ),
                "ARMED": (
                    "shipped PaperBroker entry_fill_model='stop_market', "
                    "unmodified: armed at decision close, one-next-bar causal "
                    "activation, fails closed, production resolution"
                ),
            },
        },
        "verdict": verdict,
        "references": REFERENCES,
        "variants": variants,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(_json_safe(results), indent=2, allow_nan=False) + "\n")
    with args.raw.open("w", encoding="utf-8") as handle:
        for variant in ("LEVEL", "ARMED"):
            for row in sorted(
                primary_rows[variant],
                key=lambda item: (item["date"], item["bar_ts"], item["instrument"]),
            ):
                handle.write(
                    json.dumps({"variant": variant, **row}, sort_keys=True) + "\n"
                )
    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(
        json.dumps(
            _json_safe(
                {
                    "verdict": verdict,
                    "LEVEL_all_165_1tick": level_1,
                    "LEVEL_nofill_68_1tick": variants["LEVEL"]["tiers"]["slippage_1"][
                        "cohorts"
                    ]["IOC_NO_FILL_68"],
                    "ARMED_all_165_1tick": variants["ARMED"]["tiers"]["slippage_1"][
                        "cohorts"
                    ]["ALL_165"],
                }
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
