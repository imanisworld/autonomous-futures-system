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
from datetime import date as _date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

INSTRUMENT = "MES"
STRATEGY = "strat_122"
TIMEFRAME_MINUTES = 15
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
        # The campaign contract is MES / 15m / strat_122 only. Pin the
        # timeframe explicitly rather than inheriting whatever the parent
        # process is running, so the lane cannot silently collect on another
        # decision timeframe.
        "expected_timeframe_minutes": TIMEFRAME_MINUTES,
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
        # CLOSED-TRADE basis: this walks resolved outcomes only and never marks an
        # open position bar by bar, so it is NOT full mark-to-market drawdown (the
        # same distinction PR #547 had to correct). Open-position swing exposure is
        # reported separately by `open_position_exposure()`.
        "drawdown_basis": "closed_trade_realistic",
        "resolved_trades": counted,
        "realistic_balance": balance,
        "realistic_peak": peak,
        "realistic_closed_trade_drawdown_percent": round(max_dd_pct, 6),
        "raw_paper_balance_diagnostic_only": raw_balance,
        "warning_drawdown": warn,
        "halted": max_dd_pct >= MAX_DRAWDOWN,
        "halt_threshold": MAX_DRAWDOWN,
    }


CARRY_LOOKBACK_DAYS = 7


def _find_open_position(journal, for_date: Optional[_date]):
    """Locate a still-open lane position, mirroring the runner's carry lookup.

    `webhook/runner.py:876-885` checks today and then walks back up to 7 calendar
    days so a Friday->Monday swing is still found. This lane is explicitly
    evaluating swing holds, so the observer has to search the same way — checking
    only `for_date` reports `open: False` for a position that is genuinely open
    and that the engine will still resolve.

    Returns (position, date_it_was_opened_on) or (None, None).
    """
    today = for_date or _date.today()
    if journal.get_daily_state(today).has_open_position:
        position = journal.get_open_position(today)
        if position:
            return position, today
    for days_back in range(1, CARRY_LOOKBACK_DAYS + 1):
        candidate = today - timedelta(days=days_back)
        if not journal.get_daily_state(candidate).has_open_position:
            continue
        position = journal.get_open_position(candidate)
        if position:
            return position, candidate
    return None, None


def open_position_exposure(cfg, log_dir, *, mark_price, for_date: Optional[_date] = None) -> dict[str, Any]:
    """OBSERVATIONAL swing risk for a lane position that is still open.

    This campaign is explicitly evaluating swing holds, so the unrealized
    excursion of an open position has to be visible rather than hidden until the
    trade closes. Nothing here halts, force-closes or otherwise changes behavior:
    it is reported alongside the closed-trade ledger so forward swing risk can be
    measured.

    The mark is `realistic_balance` (closed trades) + raw unrealized at
    `mark_price`, minus the entry tick that has already been incurred on the open
    trade. Round-turn commission is NOT charged here — it books at exit.
    """
    from journal.journal_logger import JournalLogger

    status = ledger_status(cfg, log_dir, for_date)
    out: dict[str, Any] = {
        "open": False,
        "basis": "observational_only",
        "note": "reported for swing-risk visibility; never halts or force-closes",
        "realistic_closed_balance": status["realistic_balance"],
    }
    try:
        journal = JournalLogger(log_dir=str(journal_dir(log_dir)))
        position, opened_on = _find_open_position(journal, for_date)
    except Exception as exc:  # pragma: no cover - observability must never raise
        out["error"] = str(exc)
        return out
    if not position:
        return out
    out["open_position_date"] = opened_on.isoformat() if opened_on else None

    setup = position.get("setup") or position
    direction = str(setup.get("direction") or position.get("direction") or "")
    entry = setup.get("entry", position.get("entry"))
    if direction not in {"LONG", "SHORT"} or entry is None or mark_price is None:
        return out

    entry = float(entry)
    ticks = (float(mark_price) - entry) / TICK
    if direction == "SHORT":
        ticks = -ticks
    unrealized_raw = round(ticks * TICK_VALUE * CONTRACTS, 2)
    entry_cost = ENTRY_SLIP_TICKS * TICK_VALUE * CONTRACTS
    mtm_equity = round(status["realistic_balance"] + unrealized_raw - entry_cost, 2)
    peak = max(status["realistic_peak"], mtm_equity)
    dd_pct = round((peak - mtm_equity) / peak, 6) if peak > 0 else 0.0
    out.update(
        open=True,
        direction=direction,
        entry=entry,
        stop=setup.get("stop"),
        target=setup.get("target"),
        mark_price=float(mark_price),
        unrealized_dollars_raw=unrealized_raw,
        entry_slippage_already_incurred=round(entry_cost, 2),
        realistic_mtm_equity=mtm_equity,
        open_position_mtm_drawdown_percent=dd_pct,
        exceeds_halt_threshold_observational=dd_pct >= MAX_DRAWDOWN,
    )
    return out


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
        # Swing-risk visibility: an open position's unrealized excursion is
        # otherwise invisible until it closes. Observational only.
        audit["open_position_exposure"] = open_position_exposure(
            cfg, log_dir, mark_price=getattr(payload, "close", None), for_date=for_date
        )
        return audit
    except Exception as exc:  # pragma: no cover - never break the real book
        logger.warning("mes_122 paper lane skipped: %s", exc)
        audit["lane_result"] = "LANE_ERROR"
        audit["lane_error"] = str(exc)
        return audit
