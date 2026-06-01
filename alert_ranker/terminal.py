"""Bloomberg-style terminal state for the advisory options scanner.

This is read-only. It summarizes scanner/watchlist/risk/broker status for UI
consumption without placing orders or creating live broker clients.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from execution.alpaca_options_broker import AlpacaOptionsBroker, AlpacaOptionsConfig
from risk.options_risk_engine import OptionsRiskConfig

from .scanner import OptionsScanner


def build_terminal_state(scanner: OptionsScanner, *, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    cfg = scanner.config
    latest = scanner.storage.latest(limit=50)
    latest_by_ticker = _latest_by_ticker(latest)
    risk = OptionsRiskConfig.from_rules_file()
    alpaca = AlpacaOptionsBroker(
        config=AlpacaOptionsConfig.from_env(),
        auto_connect=False,
    )

    watchlist = []
    for ticker in cfg.watchlist:
        item = latest_by_ticker.get(ticker.upper())
        watchlist.append(_watchlist_row(ticker.upper(), item, now))

    actionable = [row for row in watchlist if row["score"] >= cfg.alert_threshold]
    blocked = [row for row in watchlist if row["status"] in {"stale", "never_seen"}]

    return {
        "service": "options-terminal",
        "advisory_only": True,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "market_hours": scanner.is_market_hours(now),
        "scanner": {
            "watchlist": cfg.watchlist,
            "alert_threshold": cfg.alert_threshold,
            "interval_minutes": cfg.interval_minutes,
            "last_run_at": scanner.last_run_at,
            "last_skip_reason": scanner.last_skip_reason,
            "tastytrade_configured": cfg.tastytrade_configured,
            "sqlite_path": str(cfg.sqlite_path),
        },
        "options_risk": {
            "enabled": risk.enabled,
            "paper_only": risk.paper_only,
            "allowed_underlyings": risk.allowed_underlyings,
            "allowed_sessions": risk.allowed_sessions,
            "session_windows": risk.session_windows,
            "max_contracts": risk.max_contracts,
            "max_premium_per_contract": risk.max_premium_per_contract,
            "max_total_premium": risk.max_total_premium,
            "max_daily_trades": risk.max_daily_trades,
            "max_daily_loss": risk.max_daily_loss,
            "min_rr_ratio": risk.min_rr_ratio,
            "allow_market_orders": risk.allow_market_orders,
            "require_confluence_grade": risk.require_confluence_grade,
        },
        "broker": alpaca.health_check(),
        "summary": {
            "symbols": len(watchlist),
            "actionable_count": len(actionable),
            "stale_or_missing_count": len(blocked),
            "latest_scan_count": len(latest),
        },
        "watchlist": watchlist,
        "actionable": actionable,
        "latest": [asdict(item) for item in latest[:10]],
    }


def _latest_by_ticker(rows):
    latest = {}
    for row in rows:
        key = row.ticker.upper()
        if key not in latest:
            latest[key] = row
    return latest


def _watchlist_row(ticker: str, row: Any | None, now: datetime) -> dict[str, Any]:
    if row is None:
        return {
            "ticker": ticker,
            "status": "never_seen",
            "direction": "UNKNOWN",
            "score": 0,
            "pattern": "N/A",
            "alert_sent": False,
            "alert_suppression_reason": "no_scan_yet",
            "last_scan_at": None,
            "age_minutes": None,
        }

    age_minutes = _age_minutes(row.timestamp, now)
    stale = age_minutes is not None and age_minutes > 30
    status = "stale" if stale else "actionable" if row.score >= 7 else "watch"
    if row.direction == "UNKNOWN" or row.score <= 0:
        status = "blocked" if not stale else "stale"

    return {
        "ticker": ticker,
        "status": status,
        "direction": row.direction,
        "score": row.score,
        "pattern": row.pattern,
        "alert_sent": row.alert_sent,
        "alert_suppression_reason": row.alert_suppression_reason,
        "last_scan_at": row.timestamp,
        "age_minutes": age_minutes,
    }


def _age_minutes(timestamp: str, now: datetime) -> int | None:
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return max(0, int((now.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))
