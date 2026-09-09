"""Isolated MES 15m 1-2-2 forward-paper evidence lane.

Additive observer. The real book keeps MES parked (`instruments.allowed` has no
MES) and keeps `strat_122` out of `enabled_concepts`; this lane re-evaluates the
MES alerts that already arrive, on its OWN isolated config copy, its OWN journal
root, and its OWN PaperBroker. It never mutates the real book's decision,
journal, balance, risk state or broker, and it can never reach an external
broker.

Why a lane and not a config flip
--------------------------------
`risk_rules.yaml:395-403`: `DecisionEngine` ranks all candidates first and applies
the permission gate only to the winner, so a higher-ranked SHADOW_ONLY strategy
silently suppresses the candidate rather than falling through to it (PR #373: 7 of
33 MES 1-2-2 candidates preempted by a shadow-only `vwap_hold`). Isolation has to
come from a config where `strat_122` is the only enabled concept — which is what
`lane_config()` builds, without touching the shipped config the live lanes use.

Why this cannot reach Tradovate
-------------------------------
`webhook/runner.py:460` derives `simulate` from `cfg.paper_mode`. With
`paper_mode=True`, `:2183-2187` selects `_paper_broker(...)` for execution and
`:1150` makes `_using_tradovate_position` False for the position's whole
lifecycle. The lane pins `paper_mode=True` in its own config copy, so the
guarantee holds structurally regardless of the box's `BROKER` / `SCHEDULE_MODE`.

Realistic ledger
----------------
Fill behavior is deliberately unchanged: `restore_position()` still opens
`strat_122` at the causal entry with no adverse slippage, and same-bar
`force_resolve()` exits still book the exact structural price (see PR #553).
Raw PaperBroker P&L is kept for diagnostics, but the lane's balance, drawdown
warnings and hard halt are computed from the REALISTIC ledger: one adverse tick
on every entry, plus one more on every same-bar `force_resolve()` exit, which is
exactly the per-leg cost #553 measured. Ordinary market exits already carry the
broker's own slippage and are not adjusted again.
"""
from __future__ import annotations

import copy
import dataclasses
import logging
import os
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

INSTRUMENT = "MES"
STRATEGY = "strat_122"
LEDGER_NAME = "mes_122_1500"
LABEL = "hypothetical_mes_122"

STARTING_BALANCE = 1_500.0
CONTRACTS = 1
TICK = 0.25
TICK_VALUE = 1.25
COMMISSION_ROUND_TRIP = 1.48

# Per-leg adverse cost applied to the REALISTIC ledger only (never to fills).
ENTRY_SLIP_TICKS = 1.0
SAME_BAR_EXIT_SLIP_TICKS = 1.0
SAME_BAR_SOURCE = "strat_212_122_same_bar_resolution"

WARN_DRAWDOWNS = (0.20, 0.25)
MAX_DRAWDOWN = 0.30

MODE_ENV = "MES_122_PAPER_MODE"
EPOCH_ENV = "MES_122_PAPER_EPOCH_START"
DEFAULT_MODE = "observe_only"
VALID_MODES = ("observe_only", "paper_sim")

# Set on the lane's own config copy so the runner hook can recognise a lane
# evaluation and refuse to recurse into itself.
REENTRANCY_ATTR = "_mes_122_paper_lane"


# ─────────────────────────────── configuration ──────────────────────────────


def mode(cfg=None) -> str:
    raw = getattr(cfg, "mes_122_paper_mode", None)
    if raw is None:
        raw = os.getenv(MODE_ENV, DEFAULT_MODE)
    value = str(raw or DEFAULT_MODE).strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


def epoch_start(cfg=None) -> Optional[str]:
    raw = getattr(cfg, "mes_122_paper_epoch_start", None)
    if raw is None:
        raw = os.getenv(EPOCH_ENV)
    return str(raw).strip() if raw and str(raw).strip() else None


def epoch(cfg=None) -> Optional[datetime]:
    """Offset-aware epoch start, or None. A naive timestamp is rejected."""
    raw = epoch_start(cfg)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def is_lane_config(cfg) -> bool:
    return bool(getattr(cfg, REENTRANCY_ATTR, False))


def is_active(cfg=None) -> bool:
    """Fail closed: the lane runs only when explicitly in paper_sim WITH an epoch."""
    return mode(cfg) == "paper_sim" and epoch(cfg) is not None


def is_candidate(instrument: Optional[str], strategy: Optional[str]) -> bool:
    root = str(instrument or "").upper().rstrip("!1234567890HMUZ")
    return root == INSTRUMENT and str(strategy or "") == STRATEGY


def journal_dir(log_dir) -> Path:
    """The lane's own journal root. Never the real book's."""
    return Path(log_dir) / "hypothetical_ledger" / LEDGER_NAME


def lane_config(cfg):
    """An isolated COPY of the config for this lane. The shipped config is never mutated.

    Only what the campaign contract pins differs:
      - MES is the lane's universe; `strat_122` is the ONLY enabled concept, so
        no higher-ranked candidate can preempt it (see module docstring);
      - fixed 1 MES, all sizing/streak scaling off;
      - $1,500 starting balance;
      - PaperBroker forced via `paper_mode=True`;
      - swings allowed (no day-only flatten for strat_122) and the merged MES
        strat_122 8h stale-timeout exemption already applies.
    Every other gate evaluates exactly as it does for the real book.
    """
    lane = copy.copy(cfg)
    sizing = getattr(cfg, "position_sizing", None)
    if sizing is not None:
        try:
            sizing = dataclasses.replace(
                sizing, enabled=False, sizing_rules=[], starting_balance=STARTING_BALANCE
            )
        except TypeError:  # pragma: no cover - non-dataclass sizing
            sizing = copy.copy(sizing)
            sizing.enabled = False
            sizing.sizing_rules = []
            sizing.starting_balance = STARTING_BALANCE
    updates: dict[str, Any] = {
        "paper_mode": True,
        "allowed_instruments": [INSTRUMENT],
        "required_instruments": [INSTRUMENT],
        "enabled_concepts": [STRATEGY],
        "disabled_concepts_per_instrument": {},
        "strategy_permission_gate_enabled": True,
        "strategy_permission_default_status": "SHADOW_ONLY",
        "strategy_status": {STRATEGY: "PAPER_ELIGIBLE"},
        "strategy_fallback_enabled": False,
        "max_contracts_per_instrument": {INSTRUMENT: CONTRACTS},
        "max_contracts_hard_cap": CONTRACTS,
        "win_streak_bonus_after": 0,
        "win_streak_bonus_contracts": 0,
        "bonus_trades_after_max": 0,
        "breakeven_at_1r": False,
        "runner_mode": False,
        "exit_mode": "static",
    }
    if sizing is not None:
        updates["position_sizing"] = sizing
    for key, value in updates.items():
        try:
            setattr(lane, key, value)
        except Exception:  # pragma: no cover - frozen/unknown field
            logger.debug("mes_122 lane could not pin %s", key)
    setattr(lane, REENTRANCY_ATTR, True)
    return lane


# ─────────────────────────────── realistic ledger ────────────────────────────


def realistic_cost_dollars(*, same_bar_resolved: bool) -> float:
    """Per-leg adverse cost #553 measured as missing from a strat_122 fill.

    Always one adverse tick on the entry (`restore_position` never slips). One
    more on the exit only when the trade was resolved on its own watched bar via
    `force_resolve`, which also books the exact structural price. Ordinary market
    exits already carry the broker's configured slippage.
    """
    ticks = ENTRY_SLIP_TICKS + (SAME_BAR_EXIT_SLIP_TICKS if same_bar_resolved else 0.0)
    return ticks * TICK_VALUE * CONTRACTS


def is_same_bar_resolved(outcome: dict) -> bool:
    audit = (outcome or {}).get("execution_audit") or {}
    return str(audit.get("source") or "") == SAME_BAR_SOURCE


def realistic_pnl(outcome: dict) -> float:
    """Realistic net for one resolved fill: raw − per-leg slippage − commission."""
    raw = float((outcome or {}).get("pnl_dollars") or 0.0)
    cost = realistic_cost_dollars(same_bar_resolved=is_same_bar_resolved(outcome))
    return round(raw - cost - COMMISSION_ROUND_TRIP, 2)


def ledger_state(outcomes: list[dict]) -> dict[str, Any]:
    """Balance / peak / drawdown / warning / halt from the REALISTIC ledger.

    Raw PaperBroker P&L is reported alongside for diagnostics but never drives
    the halt.
    """
    balance = peak = STARTING_BALANCE
    raw_balance = STARTING_BALANCE
    max_dd_pct = 0.0
    counted = 0
    for outcome in outcomes:
        result = str((outcome or {}).get("result") or "")
        if result not in {"WIN", "LOSS", "BREAKEVEN"}:
            continue
        counted += 1
        balance = round(balance + realistic_pnl(outcome), 2)
        raw_balance = round(raw_balance + float(outcome.get("pnl_dollars") or 0.0), 2)
        peak = max(peak, balance)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, (peak - balance) / peak)
    warn = max((w for w in WARN_DRAWDOWNS if max_dd_pct >= w), default=None)
    return {
        "ledger": LEDGER_NAME,
        "basis": "realistic_1_tick_per_leg",
        "resolved_trades": counted,
        "realistic_balance": balance,
        "realistic_peak": peak,
        "realistic_max_drawdown_percent": round(max_dd_pct, 6),
        "raw_paper_balance_diagnostic_only": raw_balance,
        "warning_drawdown": warn,
        "halted": max_dd_pct >= MAX_DRAWDOWN,
        "halt_threshold": MAX_DRAWDOWN,
    }


def _lane_outcomes(log_dir, cfg, for_date: Optional[_date]) -> list[dict]:
    """Resolved OUTCOME rows from the lane's own journal, at or after the epoch."""
    from journal.journal_logger import JournalLogger

    started = epoch(cfg)
    rows: list[dict] = []
    root = journal_dir(log_dir)
    if not root.exists():
        return rows
    for path in sorted(root.glob("journal_*.jsonl")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:  # pragma: no cover
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                import json

                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "OUTCOME":
                continue
            outcome = entry.get("outcome") or {}
            if str(outcome.get("strategy") or entry.get("strategy") or "") != STRATEGY:
                continue
            if started is not None:
                ts = str(entry.get("ts") or "")
                try:
                    when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    when = None
                if when is not None:
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    if when < started:
                        continue
            rows.append(outcome)
    _ = JournalLogger  # imported for symmetry with the other lanes' journal use
    return rows


def ledger_status(cfg, log_dir, for_date: Optional[_date] = None) -> dict[str, Any]:
    return ledger_state(_lane_outcomes(log_dir, cfg, for_date))


# ─────────────────────────────── lane execution ──────────────────────────────


def observe_alert(payload, *, cfg, log_dir, for_date: Optional[_date] = None) -> Optional[dict]:
    """Re-evaluate one MES alert on the isolated lane. Returns an audit dict or None.

    Returns None on every non-lane path — the overwhelmingly common case — so the
    real book pays almost nothing for this. Any failure is caught and reported:
    an evidence lane must never be able to break the real book's decision.
    """
    if is_lane_config(cfg) or not is_active(cfg):
        return None
    root = str(getattr(payload, "ticker", "") or "").upper().rstrip("!1234567890HMUZ")
    if root != INSTRUMENT:
        return None

    audit: dict[str, Any] = {
        "label": LABEL,
        "ledger": LEDGER_NAME,
        "instrument": INSTRUMENT,
        "strategy": STRATEGY,
        "epoch_start": epoch_start(cfg),
    }
    try:
        status = ledger_status(cfg, log_dir, for_date)
        audit["ledger_status"] = status
        if status["halted"]:
            # The realistic ledger, not raw PaperBroker P&L, is what halts the lane.
            audit["lane_result"] = "HALTED_MAX_DRAWDOWN"
            return audit

        from webhook.runner import process_alert as _process_alert

        result = _process_alert(
            payload,
            config=lane_config(cfg),
            log_dir=str(journal_dir(log_dir)),
            for_date=for_date,
        )
        audit.update(
            lane_result=result.get("decision"),
            lane_resolution=result.get("resolution"),
            lane_risk=result.get("risk"),
            lane_fill=result.get("fill"),
        )
        audit["ledger_status_after"] = ledger_status(cfg, log_dir, for_date)
        return audit
    except Exception as exc:  # pragma: no cover - never break the real book
        logger.warning("mes_122 paper lane skipped: %s", exc)
        audit["lane_result"] = "LANE_ERROR"
        audit["lane_error"] = str(exc)
        return audit
