#!/usr/bin/env python3
"""Canonical ORB Breakout evidence: isolated, honest-fill, static vs runner exit.

Requested by the operator as the next strategy-cell evidence lane after PR
#347 (VWAP Reclaim) closed. See docs/orb-breakout-canonical-evidence-2026-07-26.md
for the full parity-trace + robustness-question writeup this script's output
feeds.

WHY A NEW SCRIPT: the only currently-cited figure
(Strategy_Inventory.md: "+$17.40/trade with market entry + runner (n=60)")
traces exactly to docs/orb-breakout-entry-study-2026-07-11.md:27 -- but that
study (a) used entry_fill_model="market" (not ioc_limit), (b) predates the
#338/#339/#342 corpus/engine corrections, and (c) was built from inputs
scripts/ORB_BREAKOUT_ENTRY_STUDY_EVIDENCE_NOTE.md itself calls "gitignored/
unreproducible". A later market-fill pass (strategy-validation-pass-2026-07-24.md)
found the SAME cited edge is carried almost entirely by LONG+london (SHORT
and NY sessions separately near-breakeven/negative) -- a concentration
finding the current one-line Strategy_Inventory summary omits. The only
POST-correction study (PR #346, corrected-ioc-corpus-evidence-2026-07-26.md)
is combined-book (account-level 20% breaker halted by OTHER strategies'
losses) and static-only: n=20 attempts / 3 resolved -- too thin and
contaminated to answer anything.

METHOD: isolated single-strategy replay (`enabled_concepts=["orb_breakout"]`
only, own fresh account so the frozen 20% breaker -- if it trips -- reflects
only this strategy's own P&L), `entry_fill_model="ioc_limit"` in memory
(matches PR #346's corrected posture), canonical IOC tolerance and
`orb_stop_ticks` (MNQ=48, the deliberately-widened, NOT-yet-honestly-tested
value -- risk_rules.yaml's own comment says the sweep that chose 48 assumed
fills and needs live-shadow verification before trusting) asserted not
overridden. MNQ ONLY: orb_breakout is disabled for MES in production
(risk_rules.yaml, "never the validated cell in the #236/#237/#238 evidence
chain") -- no rule support and no evidence reason to test MES here, per
operator instruction not to expand instruments casually.

EXIT MODES: both `static` (config.runner_mode=False, the fixed 2.2R target)
and `runner` (config.runner_mode=True, runner_activation_r=1.0 /
runner_trail_r=0.5, PR #108's mechanism) are run on the SAME candidate
population -- config.runner_mode (bool) is what replay/replay_engine.py
actually reads to select PaperBroker's exit path; the separate
config.exit_mode string (static/runner_shadow/runner_live) is NOT consumed
anywhere in replay/replay_engine.py or execution/paper_broker.py -- it is a
live-webhook-path concept only, verified by grep before writing this script.

SLIPPAGE: 1/2/3/4-tick adverse (operator asked for 1-4 this time, vs 1-3 for
the VWAP Reclaim lane), same isolation/corpus, only `fill_slippage_ticks`
varied, for EACH exit mode.

No strategy/runtime/risk/broker/config/deployment/Pine files changed. No
enablement. No shared-engine fixes (found one real parity issue -- Pine's
orb_breakout stop offset is a stale hardcoded 8 ticks vs the backend's own
validated 48-tick MNQ config, and the backend's Pine-bracket-override path
has no minimum-stop-distance floor to catch it -- reported in the evidence
doc, NOT fixed here per the operator's "if a shared-engine defect is
discovered: STOP, report, do not fix in this lane" instruction).

Usage:
    python3 scripts/orb_breakout_canonical_evidence.py \
        --logs logs/replay_orb_breakout_canonical \
        --out scripts/orb_breakout_canonical_evidence_results.json \
        --raw scripts/orb_breakout_canonical_evidence_raw_trades.jsonl \
        --report docs/orb-breakout-canonical-evidence-2026-07-26.md
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
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

INSTRUMENT = "MNQ"
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
COMMISSION_ROUND_TRIP = 1.48
SLIPPAGE_TICKS = (1.0, 2.0, 3.0, 4.0)
EXIT_MODES = ("static", "runner")
SAMPLE_ADEQUATE_MIN = 30

HISTORICAL_N60_RUNNER = {
    "label": "MNQ, market-fill, runner exit, unbounded entry, 622d retest_baseline_off arms",
    "source": "docs/orb-breakout-entry-study-2026-07-11.md:27",
    "attempts": 60,
    "resolved": 60,
    "win_rate": 0.583,
    "profit_factor": 1.77,
    "net_pnl_reported": 1043.75,
    "expectancy_reported": 17.40,
    "halves": "H1 +26.64/trade, H2 +8.15/trade (per-doc)",
    "status": (
        "provenance/context only -- market-fill (not ioc_limit), predates #338/#339/#342 "
        "corpus corrections, inputs explicitly documented as gitignored/unreproducible "
        "(scripts/ORB_BREAKOUT_ENTRY_STUDY_EVIDENCE_NOTE.md), and superseded by the "
        "2026-07-24 validation pass finding this SAME cited edge is carried almost "
        "entirely by LONG+london (SHORT/NY separately near-breakeven-or-negative)"
    ),
}
HISTORICAL_N63_STATIC = {
    "label": "MNQ, market-fill, static exit, same 622d arm population as the runner figure above",
    "source": "docs/orb-breakout-entry-study-2026-07-11.md:27",
    "attempts": 63,
    "resolved": 63,
    "win_rate": 0.714,
    "profit_factor": 1.06,
    "net_pnl_reported": 56.50,
    "expectancy_reported": 0.90,
    "halves": "H1 +8.68/trade, H2 -6.64/trade (per-doc) -- fails walk-forward",
    "status": "provenance/context only -- same caveats as the runner figure above",
}
HISTORICAL_N68_VALIDATION_PASS = {
    "label": "MNQ, market-fill (legacy), Corpus v1, 2026-07-24 validation pass",
    "source": "docs/strategy-validation-pass-2026-07-24.md:273-286",
    "attempts": None,
    "resolved": 68,
    "win_rate": 0.544,
    "profit_factor": 2.245,
    "net_pnl_reported": 3656.0,
    "expectancy_reported": 54.0,
    "status": (
        "provenance/context only -- market-fill, predates #338/#339/#342. Materially "
        "concentrated: LONG n=43 PF 3.144 exp $80 vs SHORT n=25 PF 1.151 exp $8; "
        "london n=43 PF 3.227 exp $85 vs new_york n=22 PF 0.953 exp -$2 (net NEGATIVE)"
    ),
}
HISTORICAL_PR346_COMBINED_BOOK = {
    "label": "MNQ+MES combined-book, ioc_limit, PR #346, post-#338/#339/#342",
    "source": "docs/corrected-ioc-corpus-evidence-2026-07-26.md",
    "attempts": 20,
    "resolved": 3,
    "win_rate": 0.333,
    "profit_factor": 0.723,
    "net_pnl_reported": -18.44,
    "expectancy_reported": -6.15,
    "status": (
        "the only POST-correction figure before this pass, but combined-book: the "
        "account-level 20% breaker was halted mostly by OTHER strategies' losses "
        "(2025-09-08 MNQ / 2025-12-11 MES), leaving only n=3 resolved and zero H2 "
        "data for orb_breakout specifically -- not this strategy's own evidence, "
        "exactly the contamination this isolated run corrects for"
    ),
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


def _run_isolated(config, log_dir: Path, fresh: bool) -> dict:
    candle_dir = CORPUS / INSTRUMENT
    files = sorted(candle_dir.glob(f"{INSTRUMENT}_*.jsonl"))
    if len(files) != 313:
        raise RuntimeError(f"{INSTRUMENT}: expected 313 daily files, found {len(files)}")
    log_dir.mkdir(parents=True, exist_ok=True)
    engine = ReplayEngine(config=config, log_dir=str(log_dir))
    ran = skipped = errors = 0
    for index, candle_path in enumerate(files, 1):
        day = candle_path.stem.rsplit("_", 1)[-1]
        if not fresh and (log_dir / f"journal_{day}.jsonl").exists():
            skipped += 1
            continue
        try:
            engine.run(candle_path, review_date=day)
            ran += 1
        except Exception as exc:  # noqa: BLE001 - surface and keep going
            errors += 1
            print(f"[run] {day} ERROR: {exc}", file=sys.stderr)
        if index % 100 == 0 or index == len(files):
            print(f"[run] {log_dir.parent.name}/{log_dir.name} {index}/{len(files)} "
                  f"(ran={ran} skipped={skipped} errors={errors})", flush=True)
    if errors:
        raise RuntimeError(f"{errors} day(s) errored during isolated run ({log_dir})")
    return {"ran": ran, "skipped": skipped, "errors": errors, "files": len(files)}


def _parse_logs(logs_root: Path) -> tuple[list[dict], dict | None]:
    trades: list[dict] = []
    halt: dict | None = None
    for path in sorted(logs_root.glob("journal_*.jsonl")):
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
                if risk_reject.get("failed_rule") == "max_drawdown" and halt is None:
                    halt = {
                        "first_halt_date": day,
                        "first_halt_bar_ts": entry.get("bar_ts"),
                        "reason": risk_reject.get("reason"),
                    }
            if entry.get("decision") != "TRADE":
                continue
            risk = entry.get("risk_check") or {}
            if risk.get("result") != "APPROVED":
                continue
            setup = entry.get("setup") or {}
            if setup.get("strategy") != "orb_breakout":
                raise RuntimeError(
                    f"isolation leak: non-orb_breakout TRADE decision in {path}: "
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
                    "instrument": INSTRUMENT,
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
    return trades, halt


def _profit_factor(values: list[float]) -> float | None:
    wins = sum(v for v in values if v > 0)
    losses = abs(sum(v for v in values if v < 0))
    if losses:
        return round(wins / losses, 6)
    return math.inf if wins else None


def _max_consecutive_losses(rows: list[dict]) -> int:
    streak = worst = 0
    for row in sorted(rows, key=lambda item: (item["date"], item["bar_ts"])):
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
    for row in sorted(rows, key=lambda item: (item["date"], item["bar_ts"])):
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
            "top_5": _winner_concentration(net, 5),
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=REPO / "logs/replay_orb_breakout_canonical")
    parser.add_argument("--out", type=Path, default=REPO / "scripts/orb_breakout_canonical_evidence_results.json")
    parser.add_argument("--raw", type=Path, default=REPO / "scripts/orb_breakout_canonical_evidence_raw_trades.jsonl")
    parser.add_argument("--report", type=Path, default=REPO / "docs/orb-breakout-canonical-evidence-2026-07-26.md")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    base_config = load_config()
    before_enabled = tuple(base_config.enabled_concepts)
    before_disabled = {k: tuple(v) for k, v in base_config.disabled_concepts_per_instrument.items()}
    risk_hash_before = _sha256(REPO / "risk_rules.yaml")

    if base_config.entry_tolerance_ticks_by_root.get("MNQ") != 32.0:
        raise RuntimeError("canonical MNQ IOC tolerance is not 32 ticks")
    if not base_config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")
    if base_config.orb_stop_ticks.get("MNQ") != 48:
        raise RuntimeError(f"canonical MNQ orb_stop_ticks is not 48, got {base_config.orb_stop_ticks.get('MNQ')!r}")
    if base_config.runner_activation_r != 1.0 or base_config.runner_trail_r != 0.5:
        raise RuntimeError(
            f"canonical runner activation/trail is not 1.0/0.5, got "
            f"{base_config.runner_activation_r}/{base_config.runner_trail_r}"
        )

    all_results: dict[str, dict] = {}
    for exit_mode in EXIT_MODES:
        for slip in SLIPPAGE_TICKS:
            tag = f"{exit_mode}_{slip:.0f}tick"
            iso_config = dataclasses.replace(
                base_config,
                enabled_concepts=["orb_breakout"],
                disabled_concepts_per_instrument={},
                entry_fill_model="ioc_limit",
                fill_slippage_ticks=float(slip),
                runner_mode=(exit_mode == "runner"),
            )
            log_dir = args.logs / tag
            print(f"[run] === {tag} ===")
            _run_isolated(iso_config, log_dir, args.fresh)
            trades, halt = _parse_logs(log_dir)
            for row in trades:
                row["half"] = _period_label(row["date"], HALVES)
                row["quarter"] = _period_label(row["date"], QUARTERS)
            all_results[tag] = {
                "exit_mode": exit_mode,
                "slippage_ticks": slip,
                "overall": _stats(trades),
                "by_direction": _group(trades, "direction", ("LONG", "SHORT")),
                "by_session": _group(trades, "session", sorted({r["session"] for r in trades}) or ["none"]),
                "by_half": _group(trades, "half", HALVES.keys()),
                "by_quarter": _group(trades, "quarter", QUARTERS.keys()),
                "drawdown_breaker_halt": halt,
                "trades": trades,
            }

    after_config = load_config()
    after_enabled = tuple(after_config.enabled_concepts)
    after_disabled = {k: tuple(v) for k, v in after_config.disabled_concepts_per_instrument.items()}
    risk_hash_after = _sha256(REPO / "risk_rules.yaml")
    if (before_enabled, before_disabled, risk_hash_before) != (after_enabled, after_disabled, risk_hash_after):
        raise RuntimeError("risk_rules.yaml / enabled_concepts drifted on disk during this run")

    static_1t = all_results["static_1tick"]
    runner_1t = all_results["runner_1tick"]

    def _survives_slippage(exit_mode: str) -> bool:
        return all(
            (all_results[f"{exit_mode}_{s:.0f}tick"]["overall"]["profit_factor_after_commission"] or 0) > 1
            and (all_results[f"{exit_mode}_{s:.0f}tick"]["overall"]["net_after_commission"] or 0) > 0
            for s in SLIPPAGE_TICKS
        )

    static_wf = _walk_forward_both_halves_positive(static_1t["by_half"])
    runner_wf = _walk_forward_both_halves_positive(runner_1t["by_half"])
    static_slip_ok = _survives_slippage("static")
    runner_slip_ok = _survives_slippage("runner")

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
        top5 = overall["winner_concentration_after_commission"]["top_5"]
        concentration_flag = top5 is not None and top5 > 0.60
        if reasons:
            return "WAIT", reasons
        if concentration_flag:
            return "PROMISING BUT UNPROVEN", [f"clears walk-forward+slippage but top-5 concentration is {top5:.1%} (elevated)"]
        return "PROMISING BUT UNPROVEN", ["clears walk-forward+slippage, sample still short of VALIDATED bar"]

    static_verdict, static_reasons = _classify(static_1t["overall"], static_wf, static_slip_ok)
    runner_verdict, runner_reasons = _classify(runner_1t["overall"], runner_wf, runner_slip_ok)

    # Runner-materially-improves-edge check vs pure tail-dependence check.
    runner_vs_static_pf_delta = (
        (runner_1t["overall"]["profit_factor_after_commission"] or 0)
        - (static_1t["overall"]["profit_factor_after_commission"] or 0)
    )
    runner_top5 = runner_1t["overall"]["winner_concentration_after_commission"]["top_5"]
    static_top5 = static_1t["overall"]["winner_concentration_after_commission"]["top_5"]

    main_sha = _git("rev-parse", "HEAD")
    results = {
        "meta": {
            "main_sha": main_sha,
            "range": list(FULL_RANGE),
            "corpus": str(CORPUS.relative_to(REPO)),
            "instrument": INSTRUMENT,
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "sample_adequate_min": SAMPLE_ADEQUATE_MIN,
            "isolation": {
                "enabled_concepts": ["orb_breakout"],
                "disabled_concepts_per_instrument": {},
                "entry_fill_model": "ioc_limit",
                "entry_tolerance_ticks_mnq": base_config.entry_tolerance_ticks_by_root.get("MNQ"),
                "orb_stop_ticks_mnq": base_config.orb_stop_ticks.get("MNQ"),
                "runner_activation_r": base_config.runner_activation_r,
                "runner_trail_r": base_config.runner_trail_r,
            },
            "risk_rules_sha256_before": risk_hash_before,
            "risk_rules_sha256_after": risk_hash_after,
        },
        "drawdown_breaker_audit_1tick": {
            "static": static_1t.get("drawdown_breaker_halt"),
            "runner": runner_1t.get("drawdown_breaker_halt"),
            "note": (
                "isolated account's OWN P&L tripped its OWN 20% breaker if non-null -- this is "
                "orb_breakout's own honest performance halting itself, not combined-book "
                "contamination from other strategies (contrast with PR #346's combined-book halt)"
            ),
        },
        "classification": {
            "static": {"verdict": static_verdict, "reasons": static_reasons},
            "runner": {"verdict": runner_verdict, "reasons": runner_reasons},
        },
        "robustness_answers": {
            "positive_under_honest_fills_1tick": {
                "static": (static_1t["overall"]["net_after_commission"] or 0) > 0,
                "runner": (runner_1t["overall"]["net_after_commission"] or 0) > 0,
            },
            "h2_positive": {
                "static": (static_1t["by_half"]["H2"]["net_after_commission"] or 0) > 0,
                "runner": (runner_1t["by_half"]["H2"]["net_after_commission"] or 0) > 0,
            },
            "survives_1_4_tick_slippage": {"static": static_slip_ok, "runner": runner_slip_ok},
            "top5_winner_concentration": {"static": static_top5, "runner": runner_top5},
            "runner_vs_static_pf_delta": round(runner_vs_static_pf_delta, 4),
            "runner_materially_improves_edge_not_just_tail_dependence": (
                runner_vs_static_pf_delta > 0.1 and (runner_top5 or 1) <= (static_top5 or 0) + 0.05
            ),
            "static_fails_runner_passes": (static_verdict == "WAIT" and runner_verdict != "WAIT"),
            "runner_already_canonical_executable": "NO — production exit_mode default is static (config.runner_mode default False); runner_mode exists and is exercised identically to live by replay, but is not the deployed default",
            "mnq_alone_supports_strategy": {
                "static": static_verdict != "WAIT",
                "runner": runner_verdict != "WAIT",
            },
        },
        "exit_mode_comparison_1tick": {
            "static": {k: v for k, v in static_1t.items() if k != "trades"},
            "runner": {k: v for k, v in runner_1t.items() if k != "trades"},
        },
        "slippage_sweep": {
            tag: {k: v for k, v in block.items() if k not in ("trades", "by_direction", "by_session", "by_half", "by_quarter")}
            for tag, block in all_results.items()
        },
        "full_breakdowns": {
            tag: {k: v for k, v in block.items() if k != "trades"}
            for tag, block in all_results.items()
        },
        "historical_comparators": {
            "n60_runner_2026-07-11": HISTORICAL_N60_RUNNER,
            "n63_static_2026-07-11": HISTORICAL_N63_STATIC,
            "n68_validation_pass_2026-07-24": HISTORICAL_N68_VALIDATION_PASS,
            "pr346_combined_book_2026-07-26": HISTORICAL_PR346_COMBINED_BOOK,
        },
        "parity_findings": {
            "pine_orb_stop_offset_stale": (
                "MATERIAL: tradingview/risksentinel_context.pine:419,427 hardcodes the ORB "
                "stop offset at `tick * 8` for orb_breakout. The Python backend "
                "(strategy/signal_engine.py:1899) reads risk_rules.yaml's "
                "`orb_stop_ticks: {MNQ: 48, MES: 16}` instead -- a deliberate, "
                "risk_rules.yaml-documented widening from the same legacy 8-tick "
                "default, 'validated on replay'. strategy/signal_engine.py:1036-1112 "
                "(_apply_advisory_bracket) will accept Pine's complete bracket and "
                "OVERRIDE the backend's own computed stop whenever Pine agrees on "
                "direction+strategy -- there is no minimum-stop-distance floor, only "
                "structural checks (stop<entry<target, positive values, RR>0). If Pine "
                "ever sends a live orb_breakout alert with a complete bracket, the "
                "stale narrower 8-tick stop would silently replace the wider, "
                "risk-validated 48-tick one. Confirmed this does NOT affect replay "
                "evidence (replay/replay_engine.py:1172 sets state.raw=candle.source, "
                "which is None/absent in this corpus -- verified by sampling -- so "
                "_apply_advisory_bracket's pine_has_bracket check is always False in "
                "replay). This is a live-path-only risk. NOT FIXED in this lane per "
                "instruction -- reported only."
            ),
            "orb_stop_ticks_48_provenance": (
                "risk_rules.yaml's own comment on `orb_stop_ticks: {MNQ: 48}` states the "
                "622-day sweep that chose 48 (over 8/16/32) assumed RUNNER exit ON and "
                "'Replay = fills assumed -> live-shadow before trusting' -- i.e. that "
                "tuning was done under an OPTIMISTIC fill assumption, not honest IOC. "
                "This isolated run is the first honest-fill test of this exact stop "
                "width, for both exit modes."
            ),
            "orb_cross_instrument_contamination": (
                "Checked: DailyState.orb_break_long_played/short_played (which gate "
                "one-fire-per-direction-per-day for orb_breakout) were confirmed "
                "Dict[str,bool] keyed by instrument in current code -- PR #324 fixed "
                "the prior cross-instrument leak 2026-07-24, 3 hours after it was "
                "recorded. Not a live blocker; moot anyway since this run is MNQ-only."
            ),
            "gex_gate_inert_in_replay": (
                "state.gex.gex_regime is None in every sampled corpus bar (checked "
                "MNQ_2025-08-15.jsonl, 0/84 bars non-null) -- _gex_allows_orb() always "
                "returns True (no-op) in this replay corpus. Consistent with GEX being "
                "observe-only/inert in production too (memory: GEX_OBSERVE_ENABLED, "
                "analysis toggle OFF) -- not a live/replay parity contradiction."
            ),
            "mes_scope": (
                "orb_breakout is explicitly disabled for MES in production "
                "(risk_rules.yaml: 'never the validated cell in the #236/#237/#238 "
                "evidence chain'). No rule support, no evidence reason to test it here "
                "-- MNQ only, per operator instruction not to expand instruments "
                "casually."
            ),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")

    with args.raw.open("w", encoding="utf-8") as handle:
        for tag, block in all_results.items():
            for row in sorted(block["trades"], key=lambda r: (r["date"], r["bar_ts"])):
                out_row = dict(row)
                out_row["run_tag"] = tag
                handle.write(json.dumps(out_row, sort_keys=True) + "\n")

    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(json.dumps({
        "static_verdict": static_verdict,
        "runner_verdict": runner_verdict,
        "static_overall": static_1t["overall"],
        "runner_overall": runner_1t["overall"],
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
        "| Scope | Attempts | Fills | NoFill | Resolved | WR | Net gross | Net after $1.48 RT | "
        "Exp/arm | Exp/fill | PF net | Max DD net | MaxConsecL | Top-5 conc | n≥30 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for label, row in blocks.items():
        top5 = row["winner_concentration_after_commission"]["top_5"]
        lines.append(
            f"| {label} | {row['attempts']} | {row['fills']} | {row['no_fill']} | {row['resolved']} | "
            f"{_fmt_rate(row['win_rate'])} | {_fmt_money(row['net_before_commission'])} | "
            f"{_fmt_money(row['net_after_commission'])} | {_fmt_money(row['expectancy_per_arm_after_commission'])} | "
            f"{_fmt_money(row['expectancy_per_fill_after_commission'])} | {_fmt_pf(row['profit_factor_after_commission'])} | "
            f"{_fmt_money(row['max_drawdown_after_commission'])} | {row['max_consecutive_losses']} | "
            f"{_fmt_rate(top5)} | {'✅' if row['sample_adequate'] else '❌'} |"
        )
    return lines


def _render_report(results: dict) -> str:
    static = results["exit_mode_comparison_1tick"]["static"]
    runner = results["exit_mode_comparison_1tick"]["runner"]
    rob = results["robustness_answers"]
    lines = [
        "# ORB Breakout — canonical evidence, isolated honest-fill, static vs runner",
        "",
        f"**Static verdict: {results['classification']['static']['verdict']}** — "
        + "; ".join(results['classification']['static']['reasons']),
        f"**Runner verdict: {results['classification']['runner']['verdict']}** — "
        + "; ".join(results['classification']['runner']['reasons']),
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Corpus: `{results['meta']['corpus']}` (post-#338 corrected market_condition, post-#339/#342 ReplayEngine)",
        f"Instrument: {results['meta']['instrument']} only — see parity findings for why",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Method",
        "",
        "- **Isolated** single-strategy replay (`enabled_concepts=[\"orb_breakout\"]` only) — own fresh "
        "account, so the frozen 20% drawdown breaker (if it trips) reflects only this strategy's own P&L.",
        "- `entry_fill_model=\"ioc_limit\"` in memory (PR #346's corrected posture), canonical MNQ IOC "
        f"tolerance ({results['meta']['isolation']['entry_tolerance_ticks_mnq']:.0f} ticks) and "
        f"`orb_stop_ticks` ({results['meta']['isolation']['orb_stop_ticks_mnq']} ticks) asserted, not overridden.",
        "- Both exit modes on the SAME candidate population: static (fixed 2.2R target) and runner "
        f"(activation={results['meta']['isolation']['runner_activation_r']}R, "
        f"trail={results['meta']['isolation']['runner_trail_r']}R) — `config.runner_mode`, the actual "
        "bool replay reads (verified `config.exit_mode` is a live-webhook-only concept, not consumed by "
        "replay/replay_engine.py or execution/paper_broker.py).",
        "- 1/2/3/4-tick adverse slippage sensitivity, each exit mode, same isolation/corpus.",
        f"- ${COMMISSION_ROUND_TRIP:.2f} round-trip commission at the analysis layer only.",
        "- `risk_rules.yaml` verified byte-identical before/after "
        f"(`{results['meta']['risk_rules_sha256_before'][:16]}…`).",
        "",
        "## Static exit — overall (1-tick)",
        "",
        *_table_rows({"STATIC": static["overall"]}),
        "",
        "## Runner exit — overall (1-tick)",
        "",
        *_table_rows({"RUNNER": runner["overall"]}),
        "",
        "## By direction (1-tick)",
        "",
        "### Static",
        *_table_rows(static["by_direction"]),
        "",
        "### Runner",
        *_table_rows(runner["by_direction"]),
        "",
        "## By session (1-tick)",
        "",
        "### Static",
        *_table_rows(static["by_session"]),
        "",
        "### Runner",
        *_table_rows(runner["by_session"]),
        "",
        "## Walk-forward H1/H2 (1-tick)",
        "",
        "### Static",
        *_table_rows(static["by_half"]),
        f"Both halves positive: **{rob['h2_positive']['static']} (H2) / overall walk-forward "
        f"{'PASS' if 'fails both-halves-positive' not in ' '.join(results['classification']['static']['reasons']) else 'FAIL'}**",
        (
            f"⚠️ **This isolated account's OWN 20% drawdown breaker tripped on its own P&L**: "
            f"{static['drawdown_breaker_halt']['first_halt_date']} "
            f"({static['drawdown_breaker_halt']['reason']}). New order admission stopped from "
            f"that date — H2/Q3/Q4's thin counts are not just \"not enough sample happened to "
            f"exist,\" they are orb_breakout's own honest performance halting its own isolated "
            f"account, well before quarter-end."
            if static.get("drawdown_breaker_halt") else
            "No drawdown-breaker halt on the isolated account during this run."
        ),
        "",
        "### Runner",
        *_table_rows(runner["by_half"]),
        f"Both halves positive: **{rob['h2_positive']['runner']} (H2) / overall walk-forward "
        f"{'PASS' if 'fails both-halves-positive' not in ' '.join(results['classification']['runner']['reasons']) else 'FAIL'}**",
        (
            f"⚠️ **This isolated account's OWN 20% drawdown breaker tripped on its own P&L**: "
            f"{runner['drawdown_breaker_halt']['first_halt_date']} "
            f"({runner['drawdown_breaker_halt']['reason']})."
            if runner.get("drawdown_breaker_halt") else
            "No drawdown-breaker halt on the isolated account during this run."
        ),
        "",
        "## Quarter (1-tick)",
        "",
        "### Static",
        *_table_rows(static["by_quarter"]),
        "",
        "### Runner",
        *_table_rows(runner["by_quarter"]),
        "",
        "## Slippage sensitivity 1/2/3/4-tick (overall)",
        "",
        "### Static",
        *_table_rows({f"{s:.0f}tick": results["slippage_sweep"][f"static_{s:.0f}tick"]["overall"] for s in SLIPPAGE_TICKS}),
        f"Survives 1-4 tick: **{rob['survives_1_4_tick_slippage']['static']}**",
        "",
        "### Runner",
        *_table_rows({f"{s:.0f}tick": results["slippage_sweep"][f"runner_{s:.0f}tick"]["overall"] for s in SLIPPAGE_TICKS}),
        f"Survives 1-4 tick: **{rob['survives_1_4_tick_slippage']['runner']}**",
        "",
        "## Robustness questions (operator's list, answered explicitly)",
        "",
        f"1. Positive under honest fills? Static: **{rob['positive_under_honest_fills_1tick']['static']}**. "
        f"Runner: **{rob['positive_under_honest_fills_1tick']['runner']}**.",
        f"2. H2 positive? Static: **{rob['h2_positive']['static']}**. Runner: **{rob['h2_positive']['runner']}**.",
        f"3. Survives 1-4 tick slippage? Static: **{rob['survives_1_4_tick_slippage']['static']}**. "
        f"Runner: **{rob['survives_1_4_tick_slippage']['runner']}**.",
        f"4. Concentrated in a few trades? Top-5 winner share — static: "
        f"**{_fmt_rate(rob['top5_winner_concentration']['static'])}**, runner: "
        f"**{_fmt_rate(rob['top5_winner_concentration']['runner'])}**.",
        "5. Concentrated in one period? See quarter tables above — check for any single quarter "
        "carrying the whole result.",
        f"6. Does runner materially improve edge, or just increase tail dependence? PF delta "
        f"(runner − static) = **{rob['runner_vs_static_pf_delta']:+.3f}**; "
        f"'materially improves, not just tail dependence' flag: "
        f"**{rob['runner_materially_improves_edge_not_just_tail_dependence']}** (requires PF delta > 0.1 "
        "AND runner's top-5 concentration not meaningfully worse than static's).",
        f"7. Does static fail while runner passes? **{rob['static_fails_runner_passes']}**.",
        f"8. If runner passes, is it already canonical/executable? **{rob['runner_already_canonical_executable']}**",
        "9. Does the result depend on stale combined-book assumptions? No — this run is isolated "
        "single-strategy, own account; see historical comparators below for what the combined-book "
        "(#346) and pre-correction (2026-07-11/07-24) studies showed instead.",
        f"10. Does MNQ alone support the strategy? Static: **{rob['mnq_alone_supports_strategy']['static']}**. "
        f"Runner: **{rob['mnq_alone_supports_strategy']['runner']}**.",
        "11. Any live/replay formula differences? See parity findings below — one material Pine/backend "
        "stop-offset mismatch found (does not affect replay).",
        f"12. Is \"WAIT — gated on runner exit\" still accurate? See classification above — "
        "this pass tests runner directly for the first time under honest fills rather than gating on "
        "its promotion; the wording should be replaced with whatever this pass's classification is.",
        "",
        "## Parity findings",
        "",
        f"- **Pine stop-offset staleness (MATERIAL)**: {results['parity_findings']['pine_orb_stop_offset_stale']}",
        f"- **`orb_stop_ticks=48` provenance**: {results['parity_findings']['orb_stop_ticks_48_provenance']}",
        f"- **Cross-instrument contamination**: {results['parity_findings']['orb_cross_instrument_contamination']}",
        f"- **GEX gate**: {results['parity_findings']['gex_gate_inert_in_replay']}",
        f"- **MES scope**: {results['parity_findings']['mes_scope']}",
        "",
        "## Historical comparators (context only — NOT walk-forward-valid, NOT honest-fill except PR #346)",
        "",
    ]
    for key, comp in results["historical_comparators"].items():
        pf = comp.get("profit_factor")
        pf_str = f"PF {pf:.3f}, " if pf is not None else ""
        lines.append(
            f"- **{comp['label']}** ({comp['source']}): n={comp.get('resolved')}, "
            f"{comp['win_rate']*100:.1f}% WR, {pf_str}${comp['net_pnl_reported']:,.2f} net, "
            f"exp ${comp['expectancy_reported']:.2f}. {comp['status']}"
        )
    lines += [
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/orb_breakout_canonical_evidence.py \\",
        "  --logs logs/replay_orb_breakout_canonical \\",
        "  --out scripts/orb_breakout_canonical_evidence_results.json \\",
        "  --raw scripts/orb_breakout_canonical_evidence_raw_trades.jsonl \\",
        "  --report docs/orb-breakout-canonical-evidence-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
