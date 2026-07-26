#!/usr/bin/env python3
"""Honest market-entry counterfactual on the 165 PR #346 IOC order attempts.

Evidence orchestration ONLY.  No strategy, replay, broker, risk, config,
deployment, or Pine behavior is changed.  The question this answers:

    PR #346 proved the frozen system loses under honest IOC-limit entries
    (PF 0.753, -$802.28 net).  That result cannot distinguish a STRATEGY
    problem (the signals have no post-signal edge) from an EXECUTION-
    SELECTION problem (passive IOC limits at passed levels fill the
    retracements/losers and miss the runners — the literature's known
    adverse-selection / missed-opportunity mechanism).  The discriminating
    measurement is the fate of the SAME 165 attempts under an honest
    AGGRESSIVE entry: fill at the decision bar's close ± adverse slippage.

Method (isolated, attempt-matched, path-free):

- The attempt population is EXACTLY the 165 rows of the committed
  ``scripts/corrected_ioc_corpus_raw_trades.jsonl`` (PR #346), joined by
  ``paper_order_id`` to the preserved #346 journal logs for the frozen
  order plan (entry/stop/target/rr/strategy/direction) and contracts.
- Entry: the production honest-market path — ``PaperBroker`` with
  ``entry_fill_model="market"`` and ``BracketOrder.force_market_entry=True``
  (paper_broker.py's proof-lane branch: fill at ``market_price`` = the
  decision bar's close ± adverse slippage; NEVER the anchored plan price).
  This is the same fill semantics as the live #259 proof mode.
- Exits: the frozen static bracket at its ordered prices, resolved by the
  production ``PaperBroker.resolve_position`` (pessimistic stop-first
  same-bar, adverse slippage on stop exits, target fills clean), walking
  subsequent corpus bars same-day first and then across day files (the
  post-#339/#342 cross-day carry-forward semantics).  No day in the corpus
  uses a DAY_ONLY strategy for these attempts (asserted).
- Each attempt is simulated in its OWN fresh broker: no account
  compounding, no shared position blocking, and deliberately NO drawdown
  breaker — #346 already measured the system path; this pass measures the
  signal, attempt-matched.  H2 censoring therefore cannot occur here.
- Commission ($1.48 RT) at the analysis layer only.  Slippage sensitivity
  at 1/2/3/4 ticks (1 tick = the #346 canonical posture = primary).

Verdict rules (pre-registered before results were seen):
- The 68-attempt IOC-no-fill cohort at 1-tick slippage is the primary
  discriminator: net-after-commission > 0 AND PF > 1 → the missed
  attempts were profitable (execution-selection component CONFIRMED);
  otherwise the no-fills lose too (strategy-problem evidence).
- The all-165 result at 1-tick slippage is the headline signal-level
  answer: positive → signals carry edge at honest aggressive entry;
  negative → the edge is absent even without any passivity penalty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import subprocess
import sys
from collections import Counter
from dataclasses import replace
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
PR346_RESULT = {
    "label": "PR #346 corrected IOC pass (system path, breaker on)",
    "attempts": 165,
    "fills": 97,
    "win_rate": 0.268,
    "net_after_commission": -802.28,
    "profit_factor_after_commission": 0.753,
}


def _sha256_tree(root: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    paths = sorted(root.rglob("*.jsonl"))
    for path in paths:
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return len(paths), digest.hexdigest()


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
    """Join the committed 165-attempt list to the preserved #346 journals."""
    attempts = [row for row in _json_lines(raw_path)]
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
                f"attempt {order_id} has no TRADE row in the preserved #346 logs — "
                "logs do not match the committed attempt list"
            )
        setup = plan.get("setup") or {}
        for field in ("entry", "stop", "target", "direction", "strategy"):
            if setup.get(field) in (None, ""):
                raise RuntimeError(f"attempt {order_id} missing setup.{field}")
        if setup["direction"] != row["direction"] or setup["strategy"] != row["strategy"]:
            raise RuntimeError(f"attempt {order_id} setup/committed-row mismatch")
        if strategy_is_day_only(setup["strategy"]):
            raise RuntimeError(
                f"attempt {order_id} uses a DAY_ONLY strategy; this script's "
                "resolution loop does not implement the day-only flatten path"
            )
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
    """Per-instrument, date-ordered corpus days with candle caching."""

    def __init__(self, corpus: Path) -> None:
        self._loader = ReplayCandleLoader()
        self._files: dict[str, list[tuple[str, Path]]] = {}
        self._cache: dict[Path, list] = {}
        for instrument in INSTRUMENTS:
            days = []
            for path in sorted((corpus / instrument).glob("*.jsonl")):
                days.append((path.stem.rsplit("_", 1)[-1], path))
            self._files[instrument] = days

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


def _simulate_attempt(
    attempt: dict,
    corpus: _CorpusIndex,
    *,
    slippage_ticks: float,
    pessimistic_both_hit: bool,
) -> dict:
    instrument = attempt["instrument"]
    tick = TICK_SIZE[instrument]
    day_paths = corpus.day_sequence(instrument, attempt["date"])
    first_day = corpus.candles(day_paths[0])

    decision_idx = None
    for idx, candle in enumerate(first_day):
        if candle.instrument != instrument:
            continue
        if _norm_ts(candle.timestamp) == attempt["bar_ts"]:
            decision_idx = idx
            break
    if decision_idx is None:
        raise RuntimeError(
            f"decision bar {attempt['bar_ts']} not found in "
            f"{instrument}/{attempt['date']}"
        )
    decision_close = float(first_day[decision_idx].close)

    broker = PaperBroker(
        starting_balance=1500.0,
        slippage_ticks=slippage_ticks,
        pessimistic_both_hit=pessimistic_both_hit,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="market",
    )
    order = BracketOrder(
        instrument=instrument,
        direction=attempt["direction"],
        entry=attempt["plan_entry"],
        stop=attempt["plan_stop"],
        target=attempt["plan_target"],
        rr_ratio=attempt["rr_ratio"],
        strategy=attempt["strategy"],
        contracts=attempt["contracts"],
        force_market_entry=True,
        post_fill_validation_required=False,
    )
    entry_fill = broker.execute_bracket(
        order, market_price=decision_close, paper_order_id=attempt["paper_order_id"]
    )
    if entry_fill.result != "OPEN":
        raise RuntimeError(
            f"honest market entry unexpectedly {entry_fill.result} for "
            f"{attempt['paper_order_id']}"
        )
    expected_entry = decision_close + (
        slippage_ticks * tick if attempt["direction"] == "LONG" else -slippage_ticks * tick
    )
    if abs(entry_fill.entry_price - expected_entry) > 1e-9:
        raise RuntimeError(
            f"fill price {entry_fill.entry_price} != decision close ± slippage "
            f"{expected_entry} for {attempt['paper_order_id']}"
        )

    fill_entry = float(entry_fill.entry_price)
    degenerate_past_target = (
        fill_entry >= attempt["plan_target"]
        if attempt["direction"] == "LONG"
        else fill_entry <= attempt["plan_target"]
    )
    degenerate_past_stop = (
        fill_entry <= attempt["plan_stop"]
        if attempt["direction"] == "LONG"
        else fill_entry >= attempt["plan_stop"]
    )

    fill = None
    bars_seen = 0
    crossed_day = False
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
                crossed_day = day_number > 0
                break
        if fill is not None:
            break

    resolved = fill is not None and fill.result in {"WIN", "LOSS", "BREAKEVEN"}
    pnl_gross = float(fill.pnl_dollars or 0.0) if resolved else 0.0
    return {
        "paper_order_id": attempt["paper_order_id"],
        "date": attempt["date"],
        "bar_ts": attempt["bar_ts"],
        "instrument": instrument,
        "strategy": attempt["strategy"],
        "direction": attempt["direction"],
        "half": _period_label(attempt["date"], HALVES),
        "quarter": _period_label(attempt["date"], QUARTERS),
        "ioc_filled": int(attempt["filled"]),
        "ioc_pnl_after_commission": (
            float(attempt["pnl_after_commission"]) if attempt["filled"] else None
        ),
        "contracts": attempt["contracts"],
        "plan_entry": attempt["plan_entry"],
        "plan_stop": attempt["plan_stop"],
        "plan_target": attempt["plan_target"],
        "decision_close": decision_close,
        "cf_entry_price": fill_entry,
        "entry_degradation_ticks": round(
            (
                (fill_entry - attempt["plan_entry"])
                if attempt["direction"] == "LONG"
                else (attempt["plan_entry"] - fill_entry)
            )
            / tick,
            4,
        ),
        "degenerate_past_target": int(degenerate_past_target),
        "degenerate_past_stop": int(degenerate_past_stop),
        "resolved": int(resolved),
        "open_unresolved": int(not resolved),
        "result": fill.result if resolved else None,
        "exit_reason": fill.exit_reason if resolved else None,
        "exit_price": float(fill.exit_price) if resolved else None,
        "bars_to_resolve": bars_seen if resolved else None,
        "crossed_day": int(crossed_day) if resolved else None,
        "pnl_before_commission": round(pnl_gross, 2),
        "pnl_after_commission": round(
            pnl_gross - COMMISSION_ROUND_TRIP if resolved else 0.0, 2
        ),
    }


def _json_safe(value: Any) -> Any:
    """json.dumps(allow_nan=False) rejects inf — a zero-loss cohort's profit
    factor is math.inf; serialize it as the string "inf" instead."""
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


def _winner_concentration(values: list[float], top_n: int) -> Optional[float]:
    winners = sorted((v for v in values if v > 0), reverse=True)
    total = sum(winners)
    return round(sum(winners[:top_n]) / total, 6) if total else None


def _stats(rows: list[dict]) -> dict:
    resolved_rows = [row for row in rows if row["resolved"]]
    gross = [row["pnl_before_commission"] for row in resolved_rows]
    net = [row["pnl_after_commission"] for row in resolved_rows]
    wins = sum(row["result"] == "WIN" for row in resolved_rows)
    losses = sum(row["result"] == "LOSS" for row in resolved_rows)
    return {
        "attempts": len(rows),
        "resolved": len(resolved_rows),
        "open_unresolved": sum(row["open_unresolved"] for row in rows),
        "wins": wins,
        "losses": losses,
        "breakeven": sum(row["result"] == "BREAKEVEN" for row in resolved_rows),
        "win_rate": (
            round(wins / len(resolved_rows), 6) if resolved_rows else None
        ),
        "net_before_commission": round(sum(gross), 2),
        "net_after_commission": round(sum(net), 2),
        "expectancy_after_commission": (
            round(statistics.fmean(net), 4) if net else None
        ),
        "profit_factor_before_commission": _profit_factor(gross),
        "profit_factor_after_commission": _profit_factor(net),
        "largest_win_after_commission": round(max(net), 2) if net else None,
        "largest_loss_after_commission": round(min(net), 2) if net else None,
        "winner_concentration_after_commission": {
            "top_1": _winner_concentration(net, 1),
            "top_3": _winner_concentration(net, 3),
            "top_5": _winner_concentration(net, 5),
        },
        "mean_entry_degradation_ticks": (
            round(statistics.fmean(row["entry_degradation_ticks"] for row in rows), 4)
            if rows
            else None
        ),
        "degenerate_past_target": sum(row["degenerate_past_target"] for row in rows),
        "degenerate_past_stop": sum(row["degenerate_past_stop"] for row in rows),
        "crossed_day_resolutions": sum(
            row["crossed_day"] or 0 for row in resolved_rows
        ),
    }


def _group(rows: list[dict], field: str, labels: Iterable[str] | None = None) -> dict:
    keys = list(labels or sorted({str(row[field]) for row in rows}))
    return {
        key: _stats([row for row in rows if str(row[field]) == key]) for key in keys
    }


def _fmt_money(value: Any) -> str:
    return "—" if value is None else f"${value:,.2f}"


def _fmt_rate(value: Any) -> str:
    return "—" if value is None else f"{100 * value:.1f}%"


def _fmt_pf(value: Any) -> str:
    if value is None:
        return "—"
    return "∞" if math.isinf(value) else f"{value:.3f}"


def _table(title: str, blocks: dict[str, dict]) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Scope | Attempts | Resolved | Open | WR | Net gross | Net after $1.48 RT | Exp net | PF net | Deg. ticks | Past-target | Past-stop |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        lines.append(
            f"| {label} | {row['attempts']} | {row['resolved']} | "
            f"{row['open_unresolved']} | {_fmt_rate(row['win_rate'])} | "
            f"{_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | "
            f"{_fmt_money(row['expectancy_after_commission'])} | "
            f"{_fmt_pf(row['profit_factor_after_commission'])} | "
            f"{row['mean_entry_degradation_ticks'] if row['mean_entry_degradation_ticks'] is not None else '—'} | "
            f"{row['degenerate_past_target']} | {row['degenerate_past_stop']} |"
        )
    lines.append("")
    return lines


def _verify_independently(rows: list[dict], stats: dict) -> None:
    """Recompute every resolved row's P&L from raw prices with separate
    arithmetic (never trusting the broker's own pnl fields), then re-derive the
    headline aggregates; fail loudly on any mismatch.

    NOTE: result class is deliberately NOT inferred from P&L sign — a
    degenerate detached entry (fill already past the ordered stop/target) can
    make a STOP_HIT profitable or a TARGET_HIT negative; class comes from the
    exit reason, exactly as the production broker books it.
    """
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
        raise RuntimeError(
            f"independent net mismatch: {round(total, 2)} vs {stats['net_after_commission']}"
        )
    if resolved and abs(win_count / len(resolved) - (stats["win_rate"] or 0.0)) > 1e-6:
        raise RuntimeError("independent win-rate mismatch")


def _render_report(results: dict) -> str:
    primary = results["tiers"][f"slippage_{PRIMARY_SLIPPAGE:g}"]
    overall = primary["cohorts"]["ALL_165"]
    nofill = primary["cohorts"]["IOC_NO_FILL_68"]
    filled = primary["cohorts"]["IOC_FILLED_97"]
    paired = primary["paired_filled_comparison"]
    lines = [
        "# Market-entry counterfactual on the 165 corrected-IOC attempts",
        "",
        f"**Verdict: {results['verdict']}**",
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Corpus: `{results['meta']['corpus_tree_sha256']}` "
        f"({results['meta']['corpus_files']} files — byte-identical to PR #346's corpus)",
        f"Attempt population: the {EXPECTED_ATTEMPTS} committed PR #346 order attempts "
        f"({EXPECTED_IOC_FILLS} IOC-filled / {EXPECTED_IOC_NO_FILLS} IOC-no-fill), "
        "joined 165/165 to the preserved #346 journals by `paper_order_id`.",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Question and posture",
        "",
        "- PR #346 (system path, IOC-limit entries, breaker on) → PF 0.753, "
        "-$802.28: cannot separate a strategy problem from execution selection.",
        "- This pass: SAME frozen order plans, honest AGGRESSIVE entry — fill at "
        "the decision bar's close ± adverse slippage via the production "
        "`force_market_entry` branch of `PaperBroker` (the live #259 proof-mode "
        "semantics). Stop/target stay at ordered prices; pessimistic stop-first "
        "same-bar resolution; stop exits pay adverse slippage; target fills clean.",
        "- Isolated per-attempt simulation (fresh broker each attempt): no account "
        "path, no breaker — deliberate, because #346 already measured the system "
        "path and its breaker censored H2 to zero attempts. Attempt-matched "
        "means H1-weighted by construction (all 165 attempts are H1).",
        f"- ${COMMISSION_ROUND_TRIP:.2f} round-trip commission at the analysis "
        "layer; slippage sensitivity at 1/2/3/4 ticks (1 tick = primary, the "
        "#346 canonical posture).",
        "- Evidence orchestration only: zero strategy/replay/broker/risk/config/"
        "deployment/Pine edits.",
        "",
        "## The discriminating answer (1-tick slippage)",
        "",
        "| Cohort | Attempts | Resolved | WR | Net after commission | PF | Verdict input |",
        "|---|---:|---:|---:|---:|---:|---|",
        (
            f"| The 68 IOC no-fills | {nofill['attempts']} | {nofill['resolved']} | "
            f"{_fmt_rate(nofill['win_rate'])} | {_fmt_money(nofill['net_after_commission'])} | "
            f"{_fmt_pf(nofill['profit_factor_after_commission'])} | primary discriminator |"
        ),
        (
            f"| The 97 IOC fills | {filled['attempts']} | {filled['resolved']} | "
            f"{_fmt_rate(filled['win_rate'])} | {_fmt_money(filled['net_after_commission'])} | "
            f"{_fmt_pf(filled['profit_factor_after_commission'])} | "
            f"vs {_fmt_money(paired['ioc_net_after_commission'])} IOC actual (same 97) |"
        ),
        (
            f"| All 165 | {overall['attempts']} | {overall['resolved']} | "
            f"{_fmt_rate(overall['win_rate'])} | {_fmt_money(overall['net_after_commission'])} | "
            f"{_fmt_pf(overall['profit_factor_after_commission'])} | signal-level headline |"
        ),
        "",
        "### Paired comparison on the 97 IOC-filled attempts",
        "",
        f"- IOC actual (PR #346, same 97 rows): {_fmt_money(paired['ioc_net_after_commission'])}",
        f"- Market-entry counterfactual, same 97: {_fmt_money(paired['cf_net_after_commission'])}",
        f"- Mean per-attempt delta (CF − IOC): {_fmt_money(paired['mean_delta_after_commission'])}",
        f"- Attempts where CF result class differs from IOC: {paired['result_class_changes']}",
        "",
    ]
    lines += _table(
        "Cohorts (1-tick slippage)",
        {
            "ALL 165": overall,
            "IOC_NO_FILL 68": nofill,
            "IOC_FILLED 97": filled,
        },
    )
    lines += _table("By instrument (all 165, 1-tick)", primary["breakdowns"]["instrument"])
    lines += _table("By strategy (all 165, 1-tick)", primary["breakdowns"]["strategy"])
    lines += _table("By direction (all 165, 1-tick)", primary["breakdowns"]["direction"])
    lines += _table(
        "No-fill cohort by strategy (1-tick)", primary["breakdowns"]["nofill_strategy"]
    )
    lines += [
        "## Slippage sensitivity (all 165 / no-fill 68, net after commission)",
        "",
        "| Slippage | All-165 net | All-165 PF | No-fill-68 net | No-fill-68 PF |",
        "|---|---:|---:|---:|---:|",
    ]
    for tier in SLIPPAGE_TIERS:
        block = results["tiers"][f"slippage_{tier:g}"]["cohorts"]
        lines.append(
            f"| {tier:g} tick | "
            f"{_fmt_money(block['ALL_165']['net_after_commission'])} | "
            f"{_fmt_pf(block['ALL_165']['profit_factor_after_commission'])} | "
            f"{_fmt_money(block['IOC_NO_FILL_68']['net_after_commission'])} | "
            f"{_fmt_pf(block['IOC_NO_FILL_68']['profit_factor_after_commission'])} |"
        )
    lines += [
        "",
        "## Audit and limitations",
        "",
        f"- Join integrity: {EXPECTED_ATTEMPTS}/{EXPECTED_ATTEMPTS} attempts matched "
        "to preserved #346 journal TRADE rows by identity; fill/no-fill split "
        "re-verified 97/68; every simulated fill price re-asserted equal to "
        "decision close ± slippage.",
        f"- Degenerate brackets at entry (fill already past target / past stop): "
        f"{overall['degenerate_past_target']} / {overall['degenerate_past_stop']} "
        "of 165 — resolved mechanically by the production broker (pessimistic).",
        f"- Cross-day resolutions: {overall['crossed_day_resolutions']} "
        f"(post-#339/#342 carry-forward semantics); unresolved at corpus end: "
        f"{overall['open_unresolved']}.",
        "- Isolated per-attempt design measures the SIGNAL, not the account path: "
        "no compounding, no breaker, no position blocking. It cannot say what a "
        "market-entry SYSTEM would have done H2 (the IOC system halted in H1; "
        "a market-entry system would have generated a different attempt set — "
        "that requires a full-corpus system replay, out of scope here).",
        "- All 165 attempts are H1 by construction (the #346 breaker halted both "
        "instruments before H2), so this pass inherits that censoring and says "
        "nothing about H2.",
        "- Entry detachment moves realized R:R away from plan R:R (stop farther, "
        "target nearer for late fills); mean adverse entry degradation is "
        f"{overall['mean_entry_degradation_ticks']} ticks (all-165, 1-tick tier).",
        "- Dollar magnitudes are replay-scale. Historical evidence, not live-fill proof.",
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/market_entry_counterfactual_165.py \\",
        "  --corpus data/replay_corpus_v1_market_condition_fixed \\",
        "  --logs /private/tmp/corrected_ioc_corpus_logs \\",
        "  --raw-attempts scripts/corrected_ioc_corpus_raw_trades.jsonl \\",
        "  --out scripts/market_entry_counterfactual_165_results.json \\",
        "  --raw scripts/market_entry_counterfactual_165_raw.jsonl \\",
        "  --report docs/market-entry-counterfactual-165-2026-07-26.md",
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
        default=REPO / "scripts/market_entry_counterfactual_165_results.json",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        default=REPO / "scripts/market_entry_counterfactual_165_raw.jsonl",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=REPO / "docs/market-entry-counterfactual-165-2026-07-26.md",
    )
    args = parser.parse_args()

    config = load_config()
    if config.fill_slippage_ticks != PRIMARY_SLIPPAGE:
        raise RuntimeError("canonical fill_slippage_ticks is not 1.0")
    if not config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    # The counterfactual entry model is applied per-order (force_market_entry)
    # on a per-attempt PaperBroker; nothing global is mutated.  `replace` is
    # imported to make that explicit if a future edit needs a config override.
    _ = replace

    corpus_files, corpus_hash = _sha256_tree(args.corpus)
    if corpus_files != EXPECTED_CORPUS_FILES:
        raise RuntimeError(
            f"expected {EXPECTED_CORPUS_FILES} corpus files, found {corpus_files}"
        )
    if corpus_hash != PR346_CORPUS_TREE_SHA256:
        raise RuntimeError(
            "corpus tree hash differs from PR #346's documented corpus — "
            "refusing to run an attempt-matched counterfactual on different data"
        )

    attempts = _load_attempts(args.raw_attempts, args.logs)
    corpus = _CorpusIndex(args.corpus)

    tiers: dict[str, dict] = {}
    primary_rows: list[dict] = []
    for tier in SLIPPAGE_TIERS:
        rows = [
            _simulate_attempt(
                attempt,
                corpus,
                slippage_ticks=tier,
                pessimistic_both_hit=config.fill_pessimistic_both_hit,
            )
            for attempt in attempts
        ]
        all_stats = _stats(rows)
        _verify_independently(rows, all_stats)
        nofill_rows = [row for row in rows if not row["ioc_filled"]]
        filled_rows = [row for row in rows if row["ioc_filled"]]
        if len(nofill_rows) != EXPECTED_IOC_NO_FILLS:
            raise RuntimeError("no-fill cohort size drifted")
        paired_deltas = []
        result_class_changes = 0
        for row in filled_rows:
            ioc = row["ioc_pnl_after_commission"]
            if ioc is None:
                raise RuntimeError("filled-cohort row missing IOC actual pnl")
            if row["resolved"]:
                paired_deltas.append(row["pnl_after_commission"] - ioc)
                ioc_class = "WIN" if ioc > 0 else "LOSS_OR_BE"
                cf_class = "WIN" if row["pnl_after_commission"] > 0 else "LOSS_OR_BE"
                if ioc_class != cf_class:
                    result_class_changes += 1
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
            "paired_filled_comparison": {
                "ioc_net_after_commission": round(
                    sum(row["ioc_pnl_after_commission"] for row in filled_rows), 2
                ),
                "cf_net_after_commission": _stats(filled_rows)[
                    "net_after_commission"
                ],
                "mean_delta_after_commission": (
                    round(statistics.fmean(paired_deltas), 4)
                    if paired_deltas
                    else None
                ),
                "result_class_changes": result_class_changes,
            },
        }
        if tier == PRIMARY_SLIPPAGE:
            primary_rows = rows

    primary = tiers[f"slippage_{PRIMARY_SLIPPAGE:g}"]
    nofill = primary["cohorts"]["IOC_NO_FILL_68"]
    overall = primary["cohorts"]["ALL_165"]
    nofill_positive = (
        (nofill["net_after_commission"] or 0) > 0
        and nofill["profit_factor_after_commission"] is not None
        and nofill["profit_factor_after_commission"] > 1
    )
    all_positive = (
        (overall["net_after_commission"] or 0) > 0
        and overall["profit_factor_after_commission"] is not None
        and overall["profit_factor_after_commission"] > 1
    )
    if nofill_positive and all_positive:
        verdict = (
            "EXECUTION-SELECTION CONFIRMED — MISSED ATTEMPTS AND FULL POPULATION "
            "PROFITABLE AT HONEST MARKET ENTRY"
        )
    elif nofill_positive:
        verdict = (
            "MIXED — MISSED ATTEMPTS PROFITABLE AT HONEST MARKET ENTRY BUT FULL "
            "POPULATION STILL NEGATIVE"
        )
    elif all_positive:
        verdict = (
            "MIXED — FULL POPULATION POSITIVE BUT THE MISSED-ATTEMPT COHORT IS NOT"
        )
    else:
        verdict = (
            "STRATEGY PROBLEM CONFIRMED — SIGNALS FAIL EVEN AT HONEST MARKET ENTRY"
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
            "entry_model": (
                "PaperBroker entry_fill_model='market' + BracketOrder."
                "force_market_entry=True: fill at decision bar close ± adverse "
                "slippage (live #259 proof-mode semantics); pessimistic "
                "stop-first same-bar resolution; stop exits pay adverse "
                "slippage; target fills clean; isolated per-attempt broker."
            ),
        },
        "verdict": verdict,
        "pr346_reference": PR346_RESULT,
        "tiers": tiers,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(_json_safe(results), indent=2, allow_nan=False) + "\n"
    )
    with args.raw.open("w", encoding="utf-8") as handle:
        for row in sorted(
            primary_rows,
            key=lambda item: (item["date"], item["bar_ts"], item["instrument"]),
        ):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(
        json.dumps(
            {
                "verdict": verdict,
                "all_165": overall,
                "ioc_no_fill_68": nofill,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
