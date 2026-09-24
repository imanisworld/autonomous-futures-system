"""Discord notifications for the companion options paper lane.

Self-contained and env-driven (so it works in main + on the box without the box's
notification-router): reads the webhook URL from the environment at send time and
never stores it on config (matches the repo's "no secret values on config" rule).

Routes (env var -> Discord channel):
    DISCORD_OPTIONS_SIGNAL  -> options-signals  (opens + resolutions, watchlist,
                                                 and rejections when opted in)
    DISCORD_OPTIONS_ERROR   -> error             (lane failures)

Gated on DISCORD_NOTIFICATIONS_ENABLED + per-channel URL presence. Every function is
fail-soft: a notification problem must NEVER affect the futures path or the lane.
``DISCORD_OPTIONS_NOTIFY_DECISIONS`` (CSV) reuses the futures vocabulary: ``TRADE``
opts in opens + resolutions, ``RISK_REJECTED`` opts in watchlist/rejected candidates.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from notifications import plain_english as pe

logger = logging.getLogger(__name__)

_SIGNAL_ENV = "DISCORD_OPTIONS_SIGNAL"
_ERROR_ENV = "DISCORD_OPTIONS_ERROR"
_DAILY_REPORT_ENV = "DISCORD_OPTIONS_DAILY_REPORT"
_DECISIONS_ENV = "DISCORD_OPTIONS_NOTIFY_DECISIONS"


def _discord_enabled() -> bool:
    return os.getenv("DISCORD_NOTIFICATIONS_ENABLED", "").strip().lower() in {"true", "1", "yes"}


def _decisions() -> set[str]:
    raw = os.getenv(_DECISIONS_ENV, "TRADE,RISK_REJECTED")
    return {tok.strip().upper() for tok in raw.split(",") if tok.strip()}


def _post(env_var: str, content: str) -> bool:
    """Post one message to the webhook in env_var. Fail-soft; never raises."""
    url = os.getenv(env_var, "").strip()
    if not url:
        return False
    try:
        from notifications.discord_notifier import _post_json

        from notifications.discord_card import post_card_or_text

        post_card_or_text(
            lambda body: _post_json(url, json.dumps(body).encode("utf-8"), {"Content-Type": "application/json"}),
            content,
            source="options companion",
        )
        return True
    except Exception as exc:  # noqa: BLE001 — notification must never affect the lane
        from notifications.discord_router import redact_webhooks

        # No traceback: the HTTP error text carries the webhook URL (token).
        logger.warning("companion discord post failed (%s): %s: %s", env_var, type(exc).__name__, redact_webhooks(exc))
        return False


_BOUNDARY = "PAPER ONLY · practice tracking, no real order was placed"


def _money(value: Any) -> str:
    return pe.money(value)


def _fmt_contract(c: dict[str, Any], *, with_expiry: bool = True, explain: bool = False) -> str:
    """Plain contract, e.g. 'QQQ 741 call, expires today' (never OSI/'0DTE')."""
    label = pe.option_label(c.get("underlying"), c.get("strike"), c.get("contract_type"))
    if explain and c.get("contract_type"):
        kind = pe.option_kind(c.get("contract_type"))
        explained = pe.option_kind(c.get("contract_type"), explain=True)
        label = label[: -len(kind)] + explained if label.endswith(kind) else label
    if with_expiry:
        when = pe.expires(c.get("expiry"), dte=c.get("dte"))
        if when:
            label += f", {when}"
    return label


def _source_trade(c: dict[str, Any]) -> str:
    """'the buy on MNQ (Micro Nasdaq)' — the futures trade this option mirrors."""
    fut = c.get("futures_instrument")
    direction = c.get("futures_direction")
    if not fut and not direction:
        return ""
    return f"the {pe.side(direction).lower()} on {pe.market(fut)}"


def _symbol_footer(c: dict[str, Any]) -> list[str]:
    sym = c.get("option_symbol")
    return [f"-# {sym}"] if sym else []


def _fmt_open(c: dict[str, Any]) -> str:
    lines = [
        f"📄 Paper option opened: {_fmt_contract(c, with_expiry=False)}",
        f"Option: {_fmt_contract(c, explain=True)}",
        f"Bought at: {pe.option_price(c.get('entry_mark'))}",
        f"Stop-loss: {pe.option_price(c.get('stop_mark'))}",
        f"Profit target: {pe.option_price(c.get('target_mark'))}",
    ]
    source = _source_trade(c)
    if source:
        lines.append(f"Follows: {source}")
    lines.append(_BOUNDARY)
    return "\n".join(lines + _symbol_footer(c))


# Plain-English reasons for the internal skip codes (the Discord reader is human,
# not a debugger). Unmapped codes fall back to a humanised form of the code itself.
_REJECT_ENGLISH = {
    # ── Signa gate ──
    "signa_direction_absent": "no futures direction to follow",
    "signa_missing": "no Signa (outside opinion) read available",
    "signa_grade": "Signa (outside opinion) rating too low (needs A or B)",
    "signa_daily_neutral": "Signa daily trend shows no clear direction",
    "signa_opposes": "Signa daily trend opposed the trade",
    # ── Contract selection ──
    "market_data_unavailable": "option prices unavailable",
    "no_valid_expiry": "no option expiring in the allowed window",
    "spread_too_wide": "gap between the buy and sell price is too wide",
    # ── Options risk engine ──
    "options_disabled": "options lane is turned off",
    "live_options_blocked": "lane is paper-only (real trading blocked)",
    "underlying_not_allowed": "that stock/fund isn't on the options list",
    "contract_type_not_allowed": "that option type isn't allowed",
    "short_options_blocked": "only buying options is allowed",
    "session_not_allowed": "not allowed at this time of day",
    "session_window": "outside the options trading hours",
    "daily_trade_limit": "hit the daily options trade limit",
    "daily_loss_limit": "hit the daily options loss limit",
    "consecutive_losses": "too many losses in a row",
    "max_open_positions": "already at the most open option trades allowed",
    "quantity_invalid": "invalid number of contracts",
    "max_contracts": "more contracts than allowed",
    "market_order_blocked": "market orders are off (limit orders only)",
    "order_type_invalid": "unsupported order type",
    "entry_required": "missing entry price",
    "stop_required": "missing stop-loss price",
    "target_required": "missing profit target price",
    "bracket_invalid": "stop-loss and target prices don't make sense together",
    "premium_per_contract": "option costs more per contract than allowed",
    "total_premium": "total cost over the limit",
    "risk_invalid": "possible loss must be more than zero",
    "rr_too_low": "possible profit too small for the possible loss",
    "confluence_grade": "setup quality below the bar",
}


def _english_rule(rule: Any) -> str:
    code = str(rule or "").strip()
    if not code:
        return "no reason given"
    return _REJECT_ENGLISH.get(code, code.replace("_", " "))


def _fmt_reject(c: dict[str, Any]) -> str:
    lines = [
        f"🚫 Paper option skipped: {_fmt_contract(c)}",
        f"Reason: {_english_rule(c.get('rule'))}",
    ]
    source = _source_trade(c)
    if source:
        lines.append(f"Would have followed: {source}")
    lines.append(_BOUNDARY)
    return "\n".join(lines + _symbol_footer(c))


def _fmt_watchlist(c: dict[str, Any]) -> str:
    lines = [
        f"👀 Paper option on watch (not opened): {_fmt_contract(c)}",
        f"Reason: {_english_rule(c.get('rule'))}",
    ]
    source = _source_trade(c)
    if source:
        lines.append(f"Would have followed: {source}")
    lines.append(_BOUNDARY)
    return "\n".join(lines + _symbol_footer(c))


_RESOLVED = {
    "WIN": ("✅", "won"),
    "LOSS": ("❌", "lost"),
    "EXPIRED": ("⌛", "expired"),
}


def _fmt_resolved(r: dict[str, Any]) -> str:
    status = str(r.get("status") or "")
    icon, verb = _RESOLVED.get(status, ("•", status.lower() or "closed"))
    # Prefer the human-readable contract; fall back to the raw symbol.
    label = _fmt_contract(r, with_expiry=False) if r.get("underlying") else (r.get("option_symbol") or "?")
    pnl = r.get("pnl_dollars")
    title = f"{icon} Paper option {verb}: {label}"
    if pnl is not None:
        title += f", {_money(pnl)}"
    lines = [title]
    if pnl is not None:
        lines.append(f"Result: {_money(pnl)} (paper, not real money)")
    if r.get("expiry"):
        lines.append(f"Expiry date: {pe.et_date(r.get('expiry'))}")
    lines.append(_BOUNDARY)
    footer = _symbol_footer(r) if r.get("underlying") else []
    return "\n".join(lines + footer)


def _safe(fmt: Any, item: dict[str, Any]) -> str | None:
    """Format one message; a formatting bug is logged, never raised into the lane."""
    try:
        return fmt(item)
    except Exception:  # noqa: BLE001 — notification must never affect the lane
        logger.warning("companion discord format failed", exc_info=True)
        return None


def notify_companion_create(audit: dict[str, Any] | None) -> None:
    """Post opens (and opted-in rejections) from an evaluate_companion audit."""
    if not audit or not _discord_enabled():
        return
    decisions = _decisions()
    for c in audit.get("candidates", []) or []:
        status = c.get("status")
        if status == "OPEN" and "TRADE" in decisions:
            fmt = _fmt_open
        elif status == "WATCHLIST" and "RISK_REJECTED" in decisions:
            fmt = _fmt_watchlist
        elif status == "REJECTED" and "RISK_REJECTED" in decisions:
            fmt = _fmt_reject
        else:
            continue
        message = _safe(fmt, c)
        if message:
            _post(_SIGNAL_ENV, message)


def notify_companion_resolved(resolved: dict[str, Any] | None) -> None:
    """Post WIN/LOSS/EXPIRED resolutions."""
    if not resolved or not _discord_enabled() or "TRADE" not in _decisions():
        return
    for r in resolved.get("resolved", []) or []:
        message = _safe(_fmt_resolved, r)
        if message:
            _post(_SIGNAL_ENV, message)


_ERROR_STEPS = {
    "create": "opening new paper option trades",
    "resolve": "checking open paper option trades",
}


def notify_companion_error(message: str) -> None:
    """Post a lane error to the options error channel (raw error text in the footer)."""
    if not _discord_enabled():
        return
    text = str(message or "").strip()
    step, _, _detail = text.partition(":")
    step_text = _ERROR_STEPS.get(step.strip().lower())
    lines = ["⚠️ Paper options tracker error"]
    if step_text:
        lines.append(f"While: {step_text}")
    lines.append("What to do: check the options companion logs")
    lines.append(_BOUNDARY)
    if text:
        lines.append(f"-# {text[:300]}")
    _post(_ERROR_ENV, "\n".join(lines))


def notify_companion_daily_report(message: str) -> bool:
    """Post the end-of-day summary to the options daily-report channel."""
    if not _discord_enabled():
        return False
    return _post(_DAILY_REPORT_ENV, message)
