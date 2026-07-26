#!/usr/bin/env python3
"""ORB Reclaim — isolated honest-fill root-cause audit.

AMENDMENT (operator HOLD, same day): the first pass of this PR derived its
London-vs-New-York breakdown by FILTERING a single all-session isolated
account's trades by session after the fact -- which does not stop a London
loss from tripping the SAME account's breaker and censoring later New York
evidence. The operator required a stronger test: four genuinely independent
session-isolated canonical lanes (MNQ London, MNQ New York, MES London, MES
New York), each its OWN fresh account with the NORMAL 20% breaker, where a
bar outside the lane's session is never even evaluated by the strategy (not
merely excluded from the report) -- see SESSION_LANES / the
`allowed_sessions=[session]` override below, which makes
`_enforce_schedule and state.session not in self.config.allowed_sessions`
(signal_engine.py:294) return NO_TRADE for every off-session bar BEFORE any
orb_reclaim candidate is ever generated, so an off-session bar can carry
zero risk/drawdown consequence for that lane's account. The operator also
ruled the NON-CANONICAL breaker-off diagnostic (see BREAKER below) out of
the authorized validation path entirely: it is NOT rerun in this amendment,
its already-produced figures are kept ONLY as explicitly-labeled
out-of-scope diagnostic provenance, and no classification or
Strategy_Inventory.md claim may depend on it. The instrument-level
all-session canonical runs (MNQ/MES, breaker ON) are kept as context, not
superseded -- the session-isolated lanes are the new authoritative session
test. The Pine-vs-Python trend_dir gap remains an unresolved, unfixed parity
blocker -- this amendment does not choose a canonical side.

ORIGINAL SCOPE, requested by the operator directly following PR #349 (ORB
Breakout). PR
#346's corrected combined-book run showed orb_reclaim carrying 73.3% of the
book's total loss (net -$588.28, 86 resolved, 29.1% WR, PF 0.803) -- but
every one of those 86 resolved trades falls inside H1 only, because the
SHARED account's 20% drawdown breaker (tripped mostly by OTHER strategies'
losses: MNQ 2025-09-08, MES 2025-12-11) halted new orders before H2 began.
That number is valid COMBINED-BOOK ATTRIBUTION (confirmed independently
against PR #351's decomposition, docs/corpus-v1-loss-attribution-2026-07-26.md,
same figures to the cent) but not yet a standalone orb_reclaim verdict.

METHOD: isolated single-strategy replay (`enabled_concepts=["orb_reclaim"]`
only, own fresh account per instrument so a drawdown-breaker halt -- if any
-- reflects only orb_reclaim's own P&L), `entry_fill_model="ioc_limit"`
canonical posture, MNQ and MES run SEPARATELY (never combined into one
account), combined numbers reported only as a post-hoc aggregate of the two
independent runs. 1/2/3/4-tick adverse slippage sweep on the canonical
ioc_limit config. A second fill model ("market", the pre-#150 default) is
also run per instrument at 1-tick, joined to the ioc_limit run by
(date, bar_ts) candidate identity, to classify market-vs-IOC outcome
transitions -- NOT as an alternative canonical result, context only.

BREAKER: canonical runs keep the 20% breaker ON (matches production). If it
trips, a NON-CANONICAL breaker-off diagnostic variant is also run per
instrument (max_drawdown_percent=0) SOLELY to show what evidence the breaker
censored -- clearly labeled, never used for the classification verdict.

SOURCE-OF-TRUTH TRACE (before any scoring, see docs report for full text):
- Predicate: state.orb.status=="reclaimed_high" AND state.vwap.price_vs_vwap
  =="above" AND GEX gate. LONG only -- orb_reclaim has no SHORT branch in
  either Python (_try_orb_reclaim, signal_engine.py:1963) or Pine
  (risksentinel_context.pine:433-439). All-LONG in the evidence is a
  STRUCTURAL property of the strategy, not breaker censorship.
- Entry/stop/target: entry=orb_h+2tick; stop=max(orb_l-4tick,
  entry-MAX_ORB_STOP_TICKS*tick) [MNQ=80t/20pt, MES=40t/10pt, a Python class
  constant, NOT risk_rules.yaml-driven -- so no config-drift exposure];
  target=entry+2.5xrisk. Pine's own `max_stop_ticks` ternary (MNQ=80/MES=40)
  and stop/target formulas are an EXACT structural match -- confirmed by
  direct read, no discrepancy (contrast with orb_breakout's stale-8-tick
  finding in PR #349, which does NOT apply to orb_reclaim).
- Session eligibility: risk_rules.yaml `sessions.allowed` = [asian, london,
  new_york], no per-strategy session restriction. (The `allowed_sessions:
  [new_york]` at risk_rules.yaml:505 belongs to the unrelated
  `options_trading` block -- verified by reading the surrounding YAML, not a
  futures-strategy gate. Ruled out as a false lead before writing this up.)
- MATERIAL FINDING 1 (reported, not fixed): Pine's own orb_reclaim branch
  requires `trend_dir=="UP"` (its independent EMA-stack recompute) before
  ever sending a live alert. Python's `_try_orb_reclaim` has NO trend check
  at all -- confirmed by direct read of signal_engine.py:1963-1995. The
  2026-07-24 Pine Parity Audit's Finding 3 lists 6 strategies with a
  Python-side `state.trend.direction` hard gate (orb_breakout, vwap_reclaim,
  vwap_hold, vwap_rejection, pdh_reclaim, pdl_reclaim) -- orb_reclaim is NOT
  among them. Consequence: replay (which never runs Pine's cascade, only the
  Python predicate) evaluates every bar where orb.status=="reclaimed_high"
  and price>vwap regardless of trend direction, while LIVE only ever gets a
  chance to fire when Pine's own trend_dir=="UP" gate ALSO passes. Replay's
  orb_reclaim candidate population is therefore a strict superset of what
  live could ever produce -- this isolated evidence may include bars that
  would never generate a live alert. Not fixed here (shared-code change);
  reported as a real, previously-unitemized-for-this-strategy parity gap.
- MATERIAL FINDING 2 (reported, not fixed): risk_rules.yaml promotes MES
  orb_reclaim to "the ONLY strategy with validated positive expectancy under
  honest fills, both walk-forward halves, both exit modes" (comment at
  risk_rules.yaml:422-425, citing docs/ioc-faithful-baseline-622d-2026-07-06.md,
  PR #150) and disables every other MES strategy on that basis. That 622-day
  study predates PR #338 (market_condition parity fix), #339/#342 (replay
  engine cross-day carry-forward fixes), and #346 (corrected corpus) -- i.e.
  it ran on an engine with since-fixed defects. PR #346's own newer corrected
  combined-book run shows MES orb_reclaim net -$441.00 (75 resolved, 29.3%
  WR, PF 0.831) -- directly in tension with the 622d study's claim on THIS
  narrower window. This isolated run is the first honest-fill test of MES
  orb_reclaim under the POST-correction engine/corpus, and speaks directly
  to whether that "sole active MES proof lane" promotion still holds.

No strategy/runtime/risk/broker/config/deployment/Pine files changed. No
enablement, no tuning, no session/instrument expansion beyond what the
operator's brief requested (MNQ + MES, both explicitly in scope here).

Usage:
    python3 scripts/orb_reclaim_isolated_root_cause_audit.py \
        --logs logs/replay_orb_reclaim_isolated \
        --out scripts/orb_reclaim_isolated_root_cause_audit_results.json \
        --raw scripts/orb_reclaim_isolated_root_cause_audit_raw_trades.jsonl \
        --report docs/orb-reclaim-isolated-root-cause-audit-2026-07-26.md
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
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from config.settings import load_config  # noqa: E402
from replay.replay_engine import ReplayEngine  # noqa: E402

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
COMMISSION_ROUND_TRIP = 1.48
SLIPPAGE_TICKS = (1.0, 2.0, 3.0, 4.0)
SAMPLE_ADEQUATE_MIN = 30
SESSIONS = ("asian", "london", "new_york")
SESSION_LANES = (
    ("MNQ", "london"),
    ("MNQ", "new_york"),
    ("MES", "london"),
    ("MES", "new_york"),
)

HISTORICAL_PR346_COMBINED_BOOK = {
    "label": "MNQ+MES combined-book, ioc_limit, PR #346 (post-#338/#339/#342), full H1, breaker-truncated",
    "source": "docs/corrected-ioc-corpus-evidence-2026-07-26.md, docs/corpus-v1-loss-attribution-2026-07-26.md",
    "attempts": 131,
    "resolved": 86,
    "win_rate": 0.291,
    "profit_factor": 0.803,
    "net_pnl_reported": -588.28,
    "share_of_book_loss": 0.733,
    "by_instrument": {"MES": {"resolved": 75, "net": -441.00}, "MNQ": {"resolved": 11, "net": -147.28}},
    "by_session": {"london": -521.96, "new_york": -41.88, "asian": -24.44},
    "status": (
        "combined-book attribution, valid for what it is, but the SHARED account's own "
        "20% breaker (mostly tripped by OTHER strategies) halted new orders before H2 -- "
        "every one of the 86 resolved trades is H1-only. Not a standalone orb_reclaim "
        "verdict; exactly the contamination this isolated run corrects for."
    ),
}
HISTORICAL_IOC_622D_MES_ORB_RECLAIM = {
    "label": "MES orb_reclaim, ioc_limit, 622-day Polygon set, runner exit, pre-#338/#339/#342 engine",
    "source": "docs/ioc-faithful-baseline-622d-2026-07-06.md, docs/mes-orb-reclaim-deepdive-2026-07-06.md",
    "attempts": None,
    "resolved": 191,
    "win_rate": 0.555,
    "profit_factor": None,
    "net_pnl_reported": 2419.0,
    "expectancy_reported": 12.67,
    "status": (
        "the ONLY basis cited in risk_rules.yaml for promoting MES orb_reclaim to sole "
        "active MES proof lane. Positive both halves under 95% CI [+0.20,+25.13] at the "
        "time -- but ran on the engine BEFORE #338 (market_condition parity fix) and "
        "#339/#342 (replay cross-day carry-forward fixes). This isolated run is the first "
        "honest-fill re-test of MES orb_reclaim under the corrected engine/corpus."
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


def _run_isolated(instrument: str, config, log_dir: Path, fresh: bool) -> dict:
    candle_dir = CORPUS / instrument
    files = sorted(candle_dir.glob(f"{instrument}_*.jsonl"))
    if len(files) != 313:
        raise RuntimeError(f"{instrument}: expected 313 daily files, found {len(files)}")
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
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[run] {day} ERROR: {exc}", file=sys.stderr)
        if index % 100 == 0 or index == len(files):
            print(f"[run] {log_dir.parent.name}/{log_dir.name} {index}/{len(files)} "
                  f"(ran={ran} skipped={skipped} errors={errors})", flush=True)
    if errors:
        raise RuntimeError(f"{errors} day(s) errored during isolated run ({log_dir})")
    return {"ran": ran, "skipped": skipped, "errors": errors, "files": len(files)}


def _parse_logs(logs_root: Path, instrument: str) -> tuple[list[dict], dict | None]:
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
            if setup.get("strategy") != "orb_reclaim":
                raise RuntimeError(
                    f"isolation leak: non-orb_reclaim TRADE decision in {path}: "
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
    if (overall["net_after_commission"] or 0) <= 0 and not reasons:
        reasons.append("net non-positive under honest fills")
    top5 = overall["winner_concentration_after_commission"]["top_5"]
    concentration_flag = top5 is not None and top5 > 0.60
    if reasons:
        return "WAIT", reasons
    if concentration_flag:
        return "PROMISING BUT UNPROVEN", [f"clears walk-forward+slippage but top-5 concentration is {top5:.1%} (elevated)"]
    return "PROMISING BUT UNPROVEN", ["clears walk-forward+slippage, sample still short of VALIDATED bar"]


def _match_ioc_vs_market(ioc_trades: list[dict], market_trades: list[dict]) -> dict:
    """Classify IOC-vs-market outcome transitions, joined by (date, bar_ts) candidate identity."""
    def key(row: dict) -> tuple:
        return (row["date"], row["bar_ts"])

    market_by_key = {key(r): r for r in market_trades}
    ioc_by_key = {key(r): r for r in ioc_trades}
    all_keys = set(market_by_key) | set(ioc_by_key)

    def outcome(row: dict | None) -> str:
        if row is None:
            return "no_candidate"
        if not row["filled"]:
            return "no_fill"
        if row["result"] == "WIN":
            return "win"
        if row["result"] == "LOSS":
            return "loss"
        if row["result"] == "BREAKEVEN":
            return "breakeven"
        return "open"

    buckets: Counter = Counter()
    for k in all_keys:
        m_outcome = outcome(market_by_key.get(k))
        i_outcome = outcome(ioc_by_key.get(k))
        if m_outcome == "no_candidate" or i_outcome == "no_candidate":
            continue
        bucket = f"market_{m_outcome}__ioc_{i_outcome}"
        buckets[bucket] += 1
    return dict(sorted(buckets.items(), key=lambda kv: -kv[1]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs", type=Path, default=REPO / "logs/replay_orb_reclaim_isolated")
    parser.add_argument("--out", type=Path, default=REPO / "scripts/orb_reclaim_isolated_root_cause_audit_results.json")
    parser.add_argument("--raw", type=Path, default=REPO / "scripts/orb_reclaim_isolated_root_cause_audit_raw_trades.jsonl")
    parser.add_argument("--report", type=Path, default=REPO / "docs/orb-reclaim-isolated-root-cause-audit-2026-07-26.md")
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()

    base_config = load_config()
    before_enabled = tuple(base_config.enabled_concepts)
    before_disabled = {k: tuple(v) for k, v in base_config.disabled_concepts_per_instrument.items()}
    risk_hash_before = _sha256(REPO / "risk_rules.yaml")

    if base_config.entry_tolerance_ticks_by_root.get("MNQ") != 32.0:
        raise RuntimeError("canonical MNQ IOC tolerance is not 32 ticks")
    if base_config.entry_tolerance_ticks_by_root.get("MES") != 16.0:
        raise RuntimeError("canonical MES IOC tolerance is not 16 ticks")
    if not base_config.fill_pessimistic_both_hit:
        raise RuntimeError("canonical pessimistic same-bar handling is disabled")

    all_results: dict[str, dict] = {}
    breaker_diagnostics: dict[str, dict] = {}

    for instrument in INSTRUMENTS:
        # Canonical ioc_limit sweep (1-4 tick), breaker ON.
        for slip in SLIPPAGE_TICKS:
            tag = f"{instrument}_ioc_{slip:.0f}tick"
            iso_config = dataclasses.replace(
                base_config,
                enabled_concepts=["orb_reclaim"],
                disabled_concepts_per_instrument={},
                entry_fill_model="ioc_limit",
                fill_slippage_ticks=float(slip),
            )
            log_dir = args.logs / tag
            print(f"[run] === {tag} ===")
            _run_isolated(instrument, iso_config, log_dir, args.fresh)
            trades, halt = _parse_logs(log_dir, instrument)
            for row in trades:
                row["half"] = _period_label(row["date"], HALVES)
                row["quarter"] = _period_label(row["date"], QUARTERS)
            all_results[tag] = {
                "instrument": instrument,
                "fill_model": "ioc_limit",
                "slippage_ticks": slip,
                "breaker": "canonical_on",
                "overall": _stats(trades),
                "by_direction": _group(trades, "direction", sorted({r["direction"] for r in trades}) or ["LONG"]),
                "by_session": _group(trades, "session", SESSIONS),
                "by_half": _group(trades, "half", HALVES.keys()),
                "by_quarter": _group(trades, "quarter", QUARTERS.keys()),
                "drawdown_breaker_halt": halt,
                "trades": trades,
            }

        # Diagnostic market-fill run (1-tick only) for the IOC-vs-market comparison.
        tag_market = f"{instrument}_market_1tick"
        market_config = dataclasses.replace(
            base_config,
            enabled_concepts=["orb_reclaim"],
            disabled_concepts_per_instrument={},
            entry_fill_model="market",
            fill_slippage_ticks=1.0,
        )
        log_dir = args.logs / tag_market
        print(f"[run] === {tag_market} (diagnostic, market-fill comparator) ===")
        _run_isolated(instrument, market_config, log_dir, args.fresh)
        market_trades, market_halt = _parse_logs(log_dir, instrument)
        for row in market_trades:
            row["half"] = _period_label(row["date"], HALVES)
            row["quarter"] = _period_label(row["date"], QUARTERS)
        all_results[tag_market] = {
            "instrument": instrument,
            "fill_model": "market",
            "slippage_ticks": 1.0,
            "breaker": "canonical_on",
            "overall": _stats(market_trades),
            "by_direction": _group(market_trades, "direction", sorted({r["direction"] for r in market_trades}) or ["LONG"]),
            "by_session": _group(market_trades, "session", SESSIONS),
            "by_half": _group(market_trades, "half", HALVES.keys()),
            "by_quarter": _group(market_trades, "quarter", QUARTERS.keys()),
            "drawdown_breaker_halt": market_halt,
            "trades": market_trades,
        }

        ioc_1t_key = f"{instrument}_ioc_1tick"
        breaker_diagnostics[instrument] = {
            "canonical_halt": all_results[ioc_1t_key]["drawdown_breaker_halt"],
        }
        # Non-canonical breaker-off diagnostic ONLY if the canonical run halted.
        if all_results[ioc_1t_key]["drawdown_breaker_halt"] is not None:
            tag_off = f"{instrument}_ioc_1tick_breaker_off_DIAGNOSTIC"
            off_config = dataclasses.replace(
                base_config,
                enabled_concepts=["orb_reclaim"],
                disabled_concepts_per_instrument={},
                entry_fill_model="ioc_limit",
                fill_slippage_ticks=1.0,
                max_drawdown_percent=0.0,
            )
            log_dir = args.logs / tag_off
            print(f"[run] === {tag_off} (NON-CANONICAL, breaker disabled to reveal censored evidence) ===")
            _run_isolated(instrument, off_config, log_dir, args.fresh)
            off_trades, off_halt = _parse_logs(log_dir, instrument)
            for row in off_trades:
                row["half"] = _period_label(row["date"], HALVES)
                row["quarter"] = _period_label(row["date"], QUARTERS)
            all_results[tag_off] = {
                "instrument": instrument,
                "fill_model": "ioc_limit",
                "slippage_ticks": 1.0,
                "breaker": "NON_CANONICAL_OFF",
                "overall": _stats(off_trades),
                "by_direction": _group(off_trades, "direction", sorted({r["direction"] for r in off_trades}) or ["LONG"]),
                "by_session": _group(off_trades, "session", SESSIONS),
                "by_half": _group(off_trades, "half", HALVES.keys()),
                "by_quarter": _group(off_trades, "quarter", QUARTERS.keys()),
                "drawdown_breaker_halt": off_halt,
                "trades": off_trades,
            }
            breaker_diagnostics[instrument]["breaker_off_diagnostic_overall"] = all_results[tag_off]["overall"]
            breaker_diagnostics[instrument]["breaker_off_diagnostic_by_half"] = all_results[tag_off]["by_half"]
        else:
            breaker_diagnostics[instrument]["breaker_off_diagnostic_overall"] = None

    # ── Session-isolated canonical lanes (operator HOLD amendment) ──────────
    # Genuinely independent: allowed_sessions=[session] makes the session gate
    # (signal_engine.py:294) return NO_TRADE for every off-session bar BEFORE
    # any orb_reclaim candidate is generated -- an off-session bar carries
    # zero risk/drawdown consequence for this lane's account. This is NOT a
    # post-hoc filter of the all-session run; it is an independently-run,
    # independently-breakered account per (instrument, session). Breaker ON
    # (canonical, matches production) -- no breaker-off variant here, out of
    # scope per the operator's amendment.
    session_lane_results: dict[str, dict] = {}
    for instrument, session in SESSION_LANES:
        for slip in SLIPPAGE_TICKS:
            tag = f"{instrument}_{session}_ioc_{slip:.0f}tick"
            iso_config = dataclasses.replace(
                base_config,
                enabled_concepts=["orb_reclaim"],
                disabled_concepts_per_instrument={},
                entry_fill_model="ioc_limit",
                fill_slippage_ticks=float(slip),
                allowed_sessions=[session],
            )
            log_dir = args.logs / tag
            print(f"[run] === {tag} (session-isolated lane) ===")
            _run_isolated(instrument, iso_config, log_dir, args.fresh)
            trades, halt = _parse_logs(log_dir, instrument)
            for row in trades:
                row["half"] = _period_label(row["date"], HALVES)
                row["quarter"] = _period_label(row["date"], QUARTERS)
            off_session_leak = [r for r in trades if r["session"] != session]
            if off_session_leak:
                raise RuntimeError(
                    f"session isolation leak: {tag} produced trade(s) outside "
                    f"'{session}': {off_session_leak[:3]!r}"
                )
            session_lane_results[tag] = {
                "instrument": instrument,
                "session": session,
                "fill_model": "ioc_limit",
                "slippage_ticks": slip,
                "breaker": "canonical_on",
                "overall": _stats(trades),
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

    # Per-instrument canonical (1-tick) verdicts.
    verdicts: dict[str, dict] = {}
    for instrument in INSTRUMENTS:
        block_1t = all_results[f"{instrument}_ioc_1tick"]
        wf = _walk_forward_both_halves_positive(block_1t["by_half"])
        slip_ok = all(
            (all_results[f"{instrument}_ioc_{s:.0f}tick"]["overall"]["profit_factor_after_commission"] or 0) > 1
            and (all_results[f"{instrument}_ioc_{s:.0f}tick"]["overall"]["net_after_commission"] or 0) > 0
            for s in SLIPPAGE_TICKS
        )
        verdict, reasons = _classify(block_1t["overall"], wf, slip_ok)
        verdicts[instrument] = {
            "verdict": verdict,
            "reasons": reasons,
            "walk_forward_both_halves_positive": wf,
            "survives_1_4_tick_slippage": slip_ok,
        }

    # Session-isolated lane verdicts (1-tick canonical, breaker ON) -- the
    # authoritative clean London-vs-New-York test.
    session_lane_verdicts: dict[str, dict] = {}
    for instrument, session in SESSION_LANES:
        key = f"{instrument}_{session}"
        block_1t = session_lane_results[f"{key}_ioc_1tick"]
        wf = _walk_forward_both_halves_positive(block_1t["by_half"])
        slip_ok = all(
            (session_lane_results[f"{key}_ioc_{s:.0f}tick"]["overall"]["profit_factor_after_commission"] or 0) > 1
            and (session_lane_results[f"{key}_ioc_{s:.0f}tick"]["overall"]["net_after_commission"] or 0) > 0
            for s in SLIPPAGE_TICKS
        )
        verdict, reasons = _classify(block_1t["overall"], wf, slip_ok)
        session_lane_verdicts[key] = {
            "instrument": instrument,
            "session": session,
            "verdict": verdict,
            "reasons": reasons,
            "walk_forward_both_halves_positive": wf,
            "survives_1_4_tick_slippage": slip_ok,
            "drawdown_breaker_halt": block_1t["drawdown_breaker_halt"],
        }

    # Combined (post-hoc aggregate of the two independent isolated runs) at 1-tick canonical.
    combined_trades = (
        all_results["MNQ_ioc_1tick"]["trades"] + all_results["MES_ioc_1tick"]["trades"]
    )
    combined_overall = _stats(combined_trades)
    combined_by_session = _group(combined_trades, "session", SESSIONS)
    combined_by_half = _group(combined_trades, "half", HALVES.keys())

    # IOC vs market comparison, per instrument.
    ioc_vs_market = {
        instrument: _match_ioc_vs_market(
            all_results[f"{instrument}_ioc_1tick"]["trades"],
            all_results[f"{instrument}_market_1tick"]["trades"],
        )
        for instrument in INSTRUMENTS
    }

    main_sha = _git("rev-parse", "HEAD")
    results = {
        "meta": {
            "main_sha": main_sha,
            "range": list(FULL_RANGE),
            "corpus": str(CORPUS.relative_to(REPO)),
            "instruments": list(INSTRUMENTS),
            "commission_round_trip": COMMISSION_ROUND_TRIP,
            "sample_adequate_min": SAMPLE_ADEQUATE_MIN,
            "isolation": {
                "enabled_concepts": ["orb_reclaim"],
                "disabled_concepts_per_instrument": {},
                "entry_fill_model_canonical": "ioc_limit",
                "entry_tolerance_ticks_mnq": base_config.entry_tolerance_ticks_by_root.get("MNQ"),
                "entry_tolerance_ticks_mes": base_config.entry_tolerance_ticks_by_root.get("MES"),
                "max_orb_stop_ticks_mnq": 80,
                "max_orb_stop_ticks_mes": 40,
            },
            "risk_rules_sha256_before": risk_hash_before,
            "risk_rules_sha256_after": risk_hash_after,
        },
        "verdicts": verdicts,
        "session_isolated_lanes": {
            "note": (
                "AUTHORITATIVE clean London-vs-New-York test (operator HOLD amendment). Each "
                "lane is an INDEPENDENT account with allowed_sessions=[session] -- off-session "
                "bars never generate a candidate (signal_engine.py:294 returns NO_TRADE before "
                "_try_orb_reclaim is ever called), so one session cannot censor another's "
                "evidence. Breaker ON (canonical, matches production). Supersedes the earlier "
                "by-session breakdown of the all-session run below, which was a post-hoc filter "
                "of a single shared-breaker account and could not rule out cross-session "
                "censorship."
            ),
            "verdicts": session_lane_verdicts,
            "lanes_1tick": {
                key: {k: v for k, v in session_lane_results[f"{key}_ioc_1tick"].items() if k != "trades"}
                for instrument, session in SESSION_LANES
                for key in [f"{instrument}_{session}"]
            },
            "slippage_sweep": {
                tag: {k: v for k, v in block.items() if k != "trades"}
                for tag, block in session_lane_results.items()
            },
        },
        "breaker_off_diagnostics_OUT_OF_SCOPE_NOT_FOR_CLASSIFICATION": {
            "note": (
                "NON-CANONICAL. Ruled outside the authorized validation path by the operator's "
                "HOLD amendment. Preserved ONLY as diagnostic provenance (what evidence the "
                "20% breaker censored) for auditability -- MUST NOT be used to determine "
                "classification, Master Table fields, or any 'genuinely negative' / 'near-flat' "
                "style conclusion. Not rerun in this amendment; figures are exactly as first "
                "produced."
            ),
            "data": breaker_diagnostics,
        },
        "combined_reporting_aggregate_1tick": {
            "overall": combined_overall,
            "by_session": combined_by_session,
            "by_half": combined_by_half,
            "note": (
                "post-hoc SUM of two INDEPENDENT isolated single-instrument runs, NOT a "
                "shared-account replay -- no combined breaker, no cross-instrument "
                "contamination. Reporting aggregate only, per operator instruction."
            ),
        },
        "ioc_vs_market_comparison_1tick": ioc_vs_market,
        "per_instrument_canonical_1tick": {
            instrument: {k: v for k, v in all_results[f"{instrument}_ioc_1tick"].items() if k != "trades"}
            for instrument in INSTRUMENTS
        },
        "slippage_sweep": {
            tag: {k: v for k, v in block.items() if k not in ("trades", "by_direction", "by_session", "by_half", "by_quarter")}
            for tag, block in all_results.items()
            if block["fill_model"] == "ioc_limit" and block["breaker"] == "canonical_on"
        },
        "full_breakdowns": {
            tag: {k: v for k, v in block.items() if k != "trades"}
            for tag, block in all_results.items()
        },
        "historical_comparators": {
            "pr346_combined_book_2026-07-26": HISTORICAL_PR346_COMBINED_BOOK,
            "ioc_622d_mes_orb_reclaim_2026-07-06": HISTORICAL_IOC_622D_MES_ORB_RECLAIM,
        },
        "parity_findings": {
            "predicate_direction_stop_target": (
                "LONG only -- no SHORT branch exists in Python (_try_orb_reclaim, "
                "signal_engine.py:1963-1995) or Pine (risksentinel_context.pine:433-439). "
                "Entry/stop/target formula is an EXACT structural match between Python and "
                "Pine (entry=orb_h+2t, stop=max(orb_l-4t, entry-MAX_STOP*t) with "
                "MNQ=80t/MES=40t on both sides, target=entry+2.5xrisk). No Pine staleness "
                "found for this strategy (contrast with orb_breakout's stale-8-tick finding, "
                "PR #349 -- does NOT apply here)."
            ),
            "trend_gate_replay_vs_live_MATERIAL": (
                "Pine's orb_reclaim branch requires trend_dir=='UP' (its own EMA-stack "
                "recompute) before ever alerting; Python's _try_orb_reclaim has NO trend "
                "check. The 2026-07-24 Pine Parity Audit's Finding 3 hard-gate list (6 "
                "strategies) does not include orb_reclaim. Consequence: replay's orb_reclaim "
                "candidate population is a strict SUPERSET of what live could ever produce "
                "-- this evidence may include bars a live alert would never have fired on. "
                "Not fixed (shared-code change out of scope); reported per instruction."
            ),
            "mes_sole_proof_lane_tension_MATERIAL": (
                "risk_rules.yaml disables every other MES strategy on the basis that MES "
                "orb_reclaim was 'the ONLY strategy with validated positive expectancy "
                "under honest fills' (622-day study, PR #150, pre-#338/#339/#342 engine). "
                "PR #346's newer corrected combined-book run shows MES orb_reclaim net "
                "-$441.00 on this dataset's window -- in tension with that promotion's "
                "stated basis. This isolated run is the first honest-fill MES orb_reclaim "
                "test under the POST-correction engine. Not fixed (risk_rules.yaml "
                "untouched); reported per instruction, addressed empirically below."
            ),
            "sessions_not_strategy_gated": (
                "risk_rules.yaml `sessions.allowed` = [asian, london, new_york], no "
                "per-strategy restriction. The `allowed_sessions: [new_york]` at line 505 "
                "belongs to the unrelated `options_trading` block -- checked directly, "
                "ruled out as a false lead before this write-up."
            ),
            "gex_gate_inert_in_replay": (
                "state.gex.gex_regime is None throughout the corpus (confirmed in the "
                "ORB Breakout PR #349 audit, same corpus) -- _gex_allows_orb() is a no-op "
                "in replay. Consistent with GEX being observe-only/inert in production too."
            ),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.raw.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2, allow_nan=False) + "\n")

    with args.raw.open("w", encoding="utf-8") as handle:
        for tag, block in {**all_results, **session_lane_results}.items():
            for row in sorted(block["trades"], key=lambda r: (r["date"], r["bar_ts"])):
                out_row = dict(row)
                out_row["run_tag"] = tag
                out_row["fill_model"] = block["fill_model"]
                out_row["breaker"] = block["breaker"]
                handle.write(json.dumps(out_row, sort_keys=True) + "\n")

    args.report.write_text(_render_report(results).rstrip() + "\n")
    print(json.dumps({
        "verdicts": verdicts,
        "session_isolated_verdicts": session_lane_verdicts,
        "MNQ_overall_1tick": all_results["MNQ_ioc_1tick"]["overall"],
        "MES_overall_1tick": all_results["MES_ioc_1tick"]["overall"],
        "combined_overall_1tick": combined_overall,
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
    mnq = results["per_instrument_canonical_1tick"]["MNQ"]
    mes = results["per_instrument_canonical_1tick"]["MES"]
    combined = results["combined_reporting_aggregate_1tick"]
    verdicts = results["verdicts"]
    lines = [
        "# ORB Reclaim — isolated honest-fill root-cause audit",
        "",
        f"**MNQ verdict: {verdicts['MNQ']['verdict']}** — " + "; ".join(verdicts['MNQ']['reasons']),
        f"**MES verdict: {verdicts['MES']['verdict']}** — " + "; ".join(verdicts['MES']['reasons']),
        "",
        f"Pinned code: `{results['meta']['main_sha']}`",
        f"Corpus: `{results['meta']['corpus']}` (post-#338 corrected market_condition, post-#339/#342 ReplayEngine)",
        f"Range: {FULL_RANGE[0]} → {FULL_RANGE[1]}",
        "",
        "## Method",
        "",
        "- **Isolated** single-strategy replay (`enabled_concepts=[\"orb_reclaim\"]` only) — MNQ and MES run "
        "as two SEPARATE fresh accounts, never combined. A breaker trip on one reflects only that "
        "instrument's own P&L.",
        f"- `entry_fill_model=\"ioc_limit\"` canonical, MNQ tolerance "
        f"{results['meta']['isolation']['entry_tolerance_ticks_mnq']:.0f}t / MES tolerance "
        f"{results['meta']['isolation']['entry_tolerance_ticks_mes']:.0f}t, asserted not overridden.",
        "- 1/2/3/4-tick adverse slippage sweep on the canonical config, each instrument.",
        "- A diagnostic `entry_fill_model=\"market\"` run (1-tick) joined to the canonical ioc_limit run "
        "by (date, bar_ts) candidate identity, to classify market-vs-IOC outcome transitions. Context "
        "only, not a canonical result.",
        f"- ${COMMISSION_ROUND_TRIP:.2f} round-trip commission at the analysis layer only.",
        "- `risk_rules.yaml` verified byte-identical before/after "
        f"(`{results['meta']['risk_rules_sha256_before'][:16]}…`).",
        "",
        "## MNQ — overall (1-tick canonical)",
        "",
        *_table_rows({"MNQ": mnq["overall"]}),
        "",
        "## MES — overall (1-tick canonical)",
        "",
        *_table_rows({"MES": mes["overall"]}),
        "",
        "## Combined reporting aggregate (post-hoc sum of the two isolated runs, 1-tick)",
        "",
        *_table_rows({"COMBINED": combined["overall"]}),
        "",
        "## By session, all-session-account view (1-tick canonical) — CONTEXT ONLY, NOT independently isolated",
        "",
        "⚠️ These rows are a post-hoc filter of the single all-session account above — a London loss in this "
        "same account CAN still consume the breaker budget that would otherwise be available to a later New "
        "York bar. See the session-isolated lanes section below for the clean, independently-breakered test.",
        "",
        "### MNQ",
        *_table_rows(mnq["by_session"]),
        "",
        "### MES",
        *_table_rows(mes["by_session"]),
        "",
        "### Combined",
        *_table_rows(combined["by_session"]),
        "",
        "## Session-isolated canonical lanes — AUTHORITATIVE London vs New York test (operator HOLD amendment)",
        "",
        results["session_isolated_lanes"]["note"],
        "",
    ]
    sil = results["session_isolated_lanes"]
    for instrument, session in SESSION_LANES:
        key = f"{instrument}_{session}"
        v = sil["verdicts"][key]
        lane = sil["lanes_1tick"][key]
        lines.append(f"### {instrument} {session}")
        lines.append(f"**Verdict: {v['verdict']}** — " + "; ".join(v['reasons']))
        lines += _table_rows({f"{instrument} {session}": lane["overall"]})
        halt = v["drawdown_breaker_halt"]
        lines.append(
            f"Own breaker: {'tripped ' + halt['first_halt_date'] + ' (' + halt['reason'] + ')' if halt else 'did NOT trip'}."
        )
        lines.append(f"Both halves positive: **{v['walk_forward_both_halves_positive']}**. "
                      f"Survives 1-4 tick slippage: **{v['survives_1_4_tick_slippage']}**.")
        lines.append("")
    lines += [
        "## Walk-forward H1/H2, all-session accounts (1-tick canonical)",
        "",
        "### MNQ",
        *_table_rows(mnq["by_half"]),
        f"Both halves positive: **{verdicts['MNQ']['walk_forward_both_halves_positive']}**",
        "",
        "### MES",
        *_table_rows(mes["by_half"]),
        f"Both halves positive: **{verdicts['MES']['walk_forward_both_halves_positive']}**",
        "",
        "## Drawdown breaker — all-session isolated accounts (canonical, used for classification)",
        "",
    ]
    breaker_data = results["breaker_off_diagnostics_OUT_OF_SCOPE_NOT_FOR_CLASSIFICATION"]["data"]
    for instrument in ("MNQ", "MES"):
        halt = breaker_data[instrument]["canonical_halt"]
        if halt is None:
            lines.append(f"- **{instrument}**: isolated account's own breaker did NOT trip during this run.")
        else:
            lines.append(
                f"- **{instrument}**: isolated account's OWN 20% breaker tripped on its own P&L: "
                f"{halt['first_halt_date']} ({halt['reason']}). New order admission stopped from that "
                f"date on the canonical run."
            )
    lines += [
        "",
        "### Breaker-off diagnostic (NON-CANONICAL, OUT OF SCOPE — provenance only, not used for anything above)",
        "",
        results["breaker_off_diagnostics_OUT_OF_SCOPE_NOT_FOR_CLASSIFICATION"]["note"],
        "",
    ]
    for instrument in ("MNQ", "MES"):
        off_overall = breaker_data[instrument].get("breaker_off_diagnostic_overall")
        if off_overall:
            lines.append(
                f"- **{instrument}** (non-canonical, breaker disabled): n={off_overall['resolved']} resolved, "
                f"{_fmt_rate(off_overall['win_rate'])} WR, {_fmt_money(off_overall['net_after_commission'])} "
                f"net, PF {_fmt_pf(off_overall['profit_factor_after_commission'])}. Provenance only — not a "
                f"classification input."
            )
    lines += [
        "",
        "## Slippage sensitivity 1/2/3/4-tick (overall, canonical)",
        "",
        "### MNQ",
        *_table_rows({f"{s:.0f}tick": results["slippage_sweep"][f"MNQ_ioc_{s:.0f}tick"]["overall"] for s in SLIPPAGE_TICKS}),
        f"Survives 1-4 tick: **{verdicts['MNQ']['survives_1_4_tick_slippage']}**",
        "",
        "### MES",
        *_table_rows({f"{s:.0f}tick": results["slippage_sweep"][f"MES_ioc_{s:.0f}tick"]["overall"] for s in SLIPPAGE_TICKS}),
        f"Survives 1-4 tick: **{verdicts['MES']['survives_1_4_tick_slippage']}**",
        "",
        "## Slippage sensitivity 1/2/3/4-tick — session-isolated lanes",
        "",
    ]
    for instrument, session in SESSION_LANES:
        key = f"{instrument}_{session}"
        lines.append(f"### {instrument} {session}")
        lines += _table_rows({
            f"{s:.0f}tick": sil["slippage_sweep"][f"{key}_ioc_{s:.0f}tick"]["overall"] for s in SLIPPAGE_TICKS
        })
        lines.append(f"Survives 1-4 tick: **{sil['verdicts'][key]['survives_1_4_tick_slippage']}**")
        lines.append("")
    lines += [
        "## IOC vs market-fill comparison (1-tick, joined by date+bar_ts candidate identity)",
        "",
    ]
    for instrument in ("MNQ", "MES"):
        lines.append(f"### {instrument}")
        for bucket, count in results["ioc_vs_market_comparison_1tick"][instrument].items():
            lines.append(f"- `{bucket}`: {count}")
        lines.append("")
    lines += [
        "## Root-cause questions (operator's list, answered explicitly)",
        "",
        f"1. Is ORB Reclaim genuinely negative in isolation? MNQ: "
        f"**{(mnq['overall']['net_after_commission'] or 0) <= 0}** (${mnq['overall']['net_after_commission']:,.2f}). "
        f"MES: **{(mes['overall']['net_after_commission'] or 0) <= 0}** (${mes['overall']['net_after_commission']:,.2f}).",
        f"2. Is MES the dominant problem? Combined net ${combined['overall']['net_after_commission']:,.2f} = "
        f"MNQ ${mnq['overall']['net_after_commission']:,.2f} + MES ${mes['overall']['net_after_commission']:,.2f}.",
        "3. Is London the dominant problem? See the session-isolated canonical lanes above — the authoritative "
        "answer (all-session by-session tables are context only, see caveat above them).",
        "4. Is NY materially better? See the session-isolated canonical lanes above.",
        "5. Is the combined-book -$588.28 representative or misleading? See combined reporting aggregate "
        "above vs PR #346's historical comparator — isolated numbers are NOT breaker-truncated to H1-only "
        "the same way (see walk-forward section) unless the isolated account tripped its own breaker (see "
        "drawdown breaker section). Non-canonical breaker-off provenance is preserved below for auditability "
        "only and does not answer this question on its own.",
        f"6. Does the isolated strategy itself hit max drawdown? MNQ: "
        f"**{breaker_data['MNQ']['canonical_halt'] is not None}**. MES: "
        f"**{breaker_data['MES']['canonical_halt'] is not None}**. See breaker section.",
        "7. Does H2 recover or remain weak? See walk-forward H1/H2 tables above.",
        f"8. Does the result survive 1-4 tick slippage? MNQ: **{verdicts['MNQ']['survives_1_4_tick_slippage']}**. "
        f"MES: **{verdicts['MES']['survives_1_4_tick_slippage']}**.",
        "9. Is the problem signal quality, fill behavior, session mix, instrument mix, or a combination? See "
        "IOC-vs-market comparison, by-session, and by-instrument breakdowns above.",
        "10. Is any current runtime/config/Pine mismatch proven? See parity findings below — one material "
        "trend-gate replay/live population gap, one material MES-promotion evidence-basis tension. Neither "
        "fixed here.",
        "11. Does anything justify changing rules? No — evidence-only lane, no tuning performed regardless "
        "of result.",
        f"12. Final classification: MNQ **{verdicts['MNQ']['verdict']}**, MES **{verdicts['MES']['verdict']}**.",
        "",
        "## Parity findings",
        "",
        f"- **Predicate/direction/stop/target**: {results['parity_findings']['predicate_direction_stop_target']}",
        f"- **Trend-gate replay-vs-live population gap (MATERIAL)**: {results['parity_findings']['trend_gate_replay_vs_live_MATERIAL']}",
        f"- **MES sole-proof-lane evidence-basis tension (MATERIAL)**: {results['parity_findings']['mes_sole_proof_lane_tension_MATERIAL']}",
        f"- **Sessions**: {results['parity_findings']['sessions_not_strategy_gated']}",
        f"- **GEX gate**: {results['parity_findings']['gex_gate_inert_in_replay']}",
        "",
        "## Historical comparators (context only)",
        "",
    ]
    for key, comp in results["historical_comparators"].items():
        lines.append(f"- **{comp['label']}** ({comp['source']}): {comp['status']}")
    lines += [
        "",
        "## Reproduction",
        "",
        "```bash",
        "python scripts/orb_reclaim_isolated_root_cause_audit.py \\",
        "  --logs logs/replay_orb_reclaim_isolated \\",
        "  --out scripts/orb_reclaim_isolated_root_cause_audit_results.json \\",
        "  --raw scripts/orb_reclaim_isolated_root_cause_audit_raw_trades.jsonl \\",
        "  --report docs/orb-reclaim-isolated-root-cause-audit-2026-07-26.md",
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
