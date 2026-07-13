"""Read-only per-lane evidence inventory, built exclusively from journal files.

Answers "how much evidence do we have, and for what" across every lane the
system produces: real live/demo trade outcomes (decisions -> fills),
SHADOW_OUTCOME rows (setup/range/GEX observers). Never mutates journal state,
never touches strategy/risk/execution behavior, never infers a fill that
didn't happen.

Lane classification (OBSERVATION_ONLY / SHADOW_TRADEABLE / PAPER_TRADEABLE /
LIVE_ELIGIBLE / DISABLED / BROKEN_OR_INCOMPLETE) is a static map maintained by
hand alongside risk_rules.yaml's enabled_concepts — it is NOT derived from the
journal, because a strategy that never fired this window is not the same as
one that can't fire. Keep it in sync when enabled_concepts changes.

LANE_CLASS answers "is this strategy technically wired to be evaluated at
all" — it is a different axis from risk_rules.yaml's strategy_permission_gate,
which answers "has this strategy earned the right to reach paper/live
execution." A strategy can be LIVE_ELIGIBLE here (present in enabled_concepts,
reachable in signal_engine.py) while still being gated to SHADOW_ONLY by the
permission gate. See LANE_NOTES for any such overrides.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

# ─── Static lane/strategy classification ──────────────────────────────────
# Source: risk_rules.yaml enabled_concepts / disabled_concepts_per_instrument
# (live-eligible allowlist) cross-referenced against strategy/signal_engine.py
# (what's actually wired) and strategy/shadow_setups.py + context/range_signal.py
# (what's shadow-only). Update this table when those files change; this report
# does not attempt to infer classification from journal contents.
LANE_CLASS: dict[str, str] = {
    # Live-executable (risk_rules.yaml enabled_concepts)
    "orb_breakout": "LIVE_ELIGIBLE",
    "orb_reclaim": "LIVE_ELIGIBLE",
    "orb_rejection": "LIVE_ELIGIBLE",
    "vwap_reclaim": "LIVE_ELIGIBLE",
    "vwap_rejection": "LIVE_ELIGIBLE",
    "vwap_hold": "LIVE_ELIGIBLE",
    "pdh_reclaim": "LIVE_ELIGIBLE",
    "pdl_reclaim": "LIVE_ELIGIBLE",
    # Present in signal_engine.py but absent from enabled_concepts -> filtered
    # before evaluation. Code exists; never reached live.
    "strat_212": "DISABLED",
    "strat_122": "DISABLED",
    "strat_4hr_retrigger": "DISABLED",
    "strat_inside_break": "DISABLED",
    "strat_outside_continuation": "DISABLED",
    "continuation_pullback": "DISABLED",
    # Shadow-only observers (strategy/shadow_setups.py) — build a bracket,
    # resolver scores them, never reach risk/broker.
    "strat_22_continuation_observed": "SHADOW_TRADEABLE",
    "strat_22_reversal_observed": "SHADOW_TRADEABLE",
    "strat_312_observed": "SHADOW_TRADEABLE",
    "strat_322_reversal_observed": "SHADOW_TRADEABLE",
    "strat_122_observed": "SHADOW_TRADEABLE",
    "strat_122_pullback": "SHADOW_TRADEABLE",
    "strat_4hr_retrigger_observed": "SHADOW_TRADEABLE",
    "ema_pullback_trend": "SHADOW_TRADEABLE",
    "impulse_first_pullback_observed": "SHADOW_TRADEABLE",
    "trend_consolidation_break_observed": "SHADOW_TRADEABLE",
    "orb_false_break_fade": "SHADOW_TRADEABLE",
    # RangeSignal (context/range_signal.py). break_close/retest_shadow build a
    # bracket; reject/bounce (the actual wall-fade states) do not — hardcoded
    # entry/stop/target=None, so the resolver can never score them.
    "range_break_close": "SHADOW_TRADEABLE",
    "range_retest_shadow": "BROKEN_OR_INCOMPLETE",
    "range_reject": "BROKEN_OR_INCOMPLETE",
    "range_bounce": "BROKEN_OR_INCOMPLETE",
}

LANE_NOTES: dict[str, str] = {
    "range_break_close": "continuation off a broken wall, NOT a fade",
    "range_reject": "watch-state only; entry/stop/target hardcoded None, never resolvable",
    "range_bounce": "watch-state only; entry/stop/target hardcoded None, never resolvable",
    "vwap_hold": "LIVE_ELIGIBLE reflects code wiring only; risk_rules.yaml's "
    "strategy_permission_gate demotes this strategy to SHADOW_ONLY as of "
    "2026-07-09 — it cannot reach paper/live execution despite this class",
}


def _read_jsonl(path: Path) -> Iterable[dict]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (TypeError, ValueError):
            continue


def _journal_files(log_dir: str | Path) -> list[Path]:
    return sorted(Path(log_dir).glob("journal_*.jsonl"))


def _date_of(path: Path) -> str:
    return path.stem.removeprefix("journal_")


_STRATEGY_FROM_REASON = re.compile(r"Setup qualified: (\w+)")


def _strategy_of_decision(record: dict) -> str:
    setup = record.get("setup")
    if isinstance(setup, dict) and setup.get("strategy"):
        return str(setup["strategy"])
    m = _STRATEGY_FROM_REASON.search(record.get("reason") or "")
    return m.group(1) if m else "unknown"


def _mean(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 2) if values else None


def real_trade_rows(paths: Iterable[Path]) -> list[dict]:
    """Correlate each real OUTCOME to the TRADE decision that produced it.

    One row per resolved attempt: strategy, instrument, result, pnl_ticks,
    pnl_dollars, no_fill_reason (present only on journal rows written after
    the no-fill taxonomy shipped — older CANCELLED rows have it as None).
    Manual test payloads (exit_reason containing TEST_PAYLOAD) are excluded.
    """
    pending: dict[str, dict] = {}
    rows: list[dict] = []
    for path in sorted(paths):
        for record in _read_jsonl(path):
            if record.get("type") == "BAR_CLAIM":
                continue
            if record.get("type") == "OUTCOME" and isinstance(record.get("outcome"), dict):
                instrument = record.get("instrument")
                outcome = record["outcome"]
                exit_reason = outcome.get("exit_reason") or ""
                if "TEST_PAYLOAD" in exit_reason:
                    continue
                strategy = pending.pop(instrument, {}).get("strategy", "unknown")
                rows.append(
                    {
                        "strategy": strategy,
                        "instrument": instrument,
                        "result": outcome.get("result"),
                        "pnl_ticks": outcome.get("pnl_ticks"),
                        "pnl_dollars": outcome.get("pnl_dollars"),
                        "no_fill_reason": outcome.get("no_fill_reason"),
                        "date": _date_of(path),
                    }
                )
                continue
            if record.get("decision") == "TRADE" and "instrument" in record:
                pending[record["instrument"]] = {"strategy": _strategy_of_decision(record)}
    return rows


def shadow_rows(paths: Iterable[Path]) -> list[dict]:
    rows: list[dict] = []
    for path in sorted(paths):
        for record in _read_jsonl(path):
            if record.get("type") != "SHADOW_OUTCOME":
                continue
            so = record.get("shadow_outcome") or {}
            rows.append(
                {
                    "lane": record.get("lane") or "unknown",
                    "strategy": record.get("strategy") or "unknown",
                    "instrument": record.get("instrument"),
                    "result": so.get("result"),
                    "pnl_ticks": so.get("pnl_ticks"),
                    "date": _date_of(path),
                }
            )
    return rows


def _bucket_real(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["strategy"], row["instrument"])].append(row)

    out = []
    for (strategy, instrument), group in sorted(grouped.items()):
        decisions = len(group)
        fills = [r for r in group if r["result"] in ("WIN", "LOSS", "BREAKEVEN")]
        cancelled = [r for r in group if r["result"] == "CANCELLED"]
        wins = [r for r in group if r["result"] == "WIN"]
        losses = [r for r in group if r["result"] == "LOSS"]
        win_ticks = [r["pnl_ticks"] for r in wins if isinstance(r.get("pnl_ticks"), (int, float))]
        loss_ticks = [r["pnl_ticks"] for r in losses if isinstance(r.get("pnl_ticks"), (int, float))]
        net_pnl = sum(r.get("pnl_dollars") or 0 for r in group)
        dates = sorted({r["date"] for r in group})
        no_fill_reasons = defaultdict(int)
        for r in cancelled:
            no_fill_reasons[r.get("no_fill_reason") or "UNCLASSIFIED_PRE_TAXONOMY"] += 1
        out.append(
            {
                "lane": "live_or_demo",
                "strategy": strategy,
                "instrument": instrument,
                "class": LANE_CLASS.get(strategy, "UNKNOWN"),
                "decisions": decisions,
                "fills": len(fills),
                "cancelled": len(cancelled),
                "wins": len(wins),
                "losses": len(losses),
                "no_fill_rate_pct": round(100.0 * len(cancelled) / decisions, 1) if decisions else None,
                "net_pnl_dollars": round(net_pnl, 2),
                "avg_win_ticks": _mean(win_ticks),
                "avg_loss_ticks": _mean(loss_ticks),
                "max_win_ticks": max(win_ticks) if win_ticks else None,
                "max_loss_ticks": min(loss_ticks) if loss_ticks else None,
                "expectancy_per_fill_dollars": round(net_pnl / len(fills), 2) if fills else None,
                "expectancy_per_decision_dollars": round(net_pnl / decisions, 2) if decisions else None,
                "date_range": f"{dates[0]}..{dates[-1]}" if dates else None,
                "no_fill_reasons": dict(no_fill_reasons),
                "note": LANE_NOTES.get(strategy),
            }
        )
    return out


def _bucket_shadow(rows: list[dict]) -> list[dict]:
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[(row["lane"], row["strategy"])].append(row)

    out = []
    for (lane, strategy), group in sorted(grouped.items()):
        wins = [r for r in group if r["result"] == "WIN"]
        losses = [r for r in group if r["result"] == "LOSS"]
        no_fill = [r for r in group if r["result"] == "NO_FILL"]
        open_ = [r for r in group if r["result"] == "OPEN"]
        win_ticks = [r["pnl_ticks"] for r in wins if isinstance(r.get("pnl_ticks"), (int, float))]
        loss_ticks = [r["pnl_ticks"] for r in losses if isinstance(r.get("pnl_ticks"), (int, float))]
        net_ticks = sum(r.get("pnl_ticks") or 0 for r in group if isinstance(r.get("pnl_ticks"), (int, float)))
        dates = sorted({r["date"] for r in group})
        out.append(
            {
                "lane": lane,
                "strategy": strategy,
                "instrument": None,
                "class": LANE_CLASS.get(strategy, "UNKNOWN"),
                "resolved": len(group),
                "wins": len(wins),
                "losses": len(losses),
                "no_fill": len(no_fill),
                "open": len(open_),
                "win_rate_pct": round(100.0 * len(wins) / (len(wins) + len(losses)), 1)
                if (wins or losses) else None,
                "avg_win_ticks": _mean(win_ticks),
                "avg_loss_ticks": _mean(loss_ticks),
                "max_win_ticks": max(win_ticks) if win_ticks else None,
                "max_loss_ticks": min(loss_ticks) if loss_ticks else None,
                "net_pnl_ticks": round(net_ticks, 1),
                "date_range": f"{dates[0]}..{dates[-1]}" if dates else None,
                "note": LANE_NOTES.get(strategy),
            }
        )
    return out


def _repo_head() -> Optional[str]:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return out.stdout.strip() or None
    except Exception:
        return None


def mes_orb_reclaim_section(real: list[dict]) -> dict:
    """Isolated section: MES orb_reclaim is the sole replay-robust candidate
    (PR #154 deep-dive). Live eligibility since 2026-06-30 per
    risk_rules.yaml's disabled_concepts_per_instrument comment history."""
    matches = [r for r in real if r["strategy"] == "orb_reclaim" and r["instrument"] == "MES"]
    fills = [r for r in matches if r["result"] in ("WIN", "LOSS", "BREAKEVEN")]
    cancelled = [r for r in matches if r["result"] == "CANCELLED"]
    reasons: dict[str, int] = defaultdict(int)
    for r in cancelled:
        reasons[r.get("no_fill_reason") or "UNCLASSIFIED_PRE_TAXONOMY"] += 1
    return {
        "eligible_since": "2026-06-30",
        "decisions": len(matches),
        "fills": len(fills),
        "cancelled": len(cancelled),
        "wins": len([r for r in fills if r["result"] == "WIN"]),
        "losses": len([r for r in fills if r["result"] == "LOSS"]),
        "no_fill_reasons": dict(reasons),
        "live_sample_status": (
            "no live evidence yet — zero fills" if matches and not fills
            else "no decisions since eligibility" if not matches
            else "has live fills"
        ),
    }


def build_evidence_report(
    log_dir: str | Path,
    *,
    box_release: Optional[str] = None,
) -> dict:
    """Build the full per-lane evidence inventory. Read-only; does not
    mutate journal state or touch execution/strategy/risk code."""
    paths = _journal_files(log_dir)
    real = real_trade_rows(paths)
    shadow = shadow_rows(paths)
    return {
        "journal_dir": str(log_dir),
        "journal_files_scanned": len(paths),
        "repo_head": _repo_head(),
        "box_release": box_release,
        "real_trades_by_strategy": _bucket_real(real),
        "shadow_by_lane_strategy": _bucket_shadow(shadow),
        "mes_orb_reclaim": mes_orb_reclaim_section(real),
        "lane_classification": dict(LANE_CLASS),
    }
