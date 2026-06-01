#!/usr/bin/env python3
"""
scripts/daily_digest.py

Sends a daily end-of-day summary to Discord.
Runs automatically at 16:30 ET via systemd timer.

Usage:
    python scripts/daily_digest.py            # send today's digest
    python scripts/daily_digest.py --dry-run  # print without sending
    python scripts/daily_digest.py --date 2026-06-01  # specific date
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import load_config
from journal.journal_logger import JournalLogger

logger = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")

_DIVIDER = "━" * 28


def _load_day(log_dir: str, target: date) -> dict:
    """Build a summary dict for a given date from the journal."""
    journal = JournalLogger(log_dir=log_dir)
    summary = journal.get_summary(for_date=target)
    state = journal.get_daily_state(for_date=target)

    # Read raw entries for NO_TRADE reasons and strategies
    journal_path = Path(log_dir) / f"{target.isoformat()}.jsonl"
    no_trade_reasons: list[str] = []
    trade_strategies: list[str] = []

    if journal_path.exists():
        import json as _json
        for line in journal_path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = _json.loads(line)
            except Exception:
                continue
            t = e.get("type", "")
            if t == "NO_TRADE":
                reason = e.get("reason", "")
                if reason:
                    no_trade_reasons.append(reason)
            elif t == "TRADE":
                strat = e.get("strategy", "")
                if strat:
                    trade_strategies.append(strat)

    trades = state.trade_count
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    pnl = round(state.realized_pnl_dollars or 0.0, 2)
    win_rate = round(wins / trades * 100, 1) if trades > 0 else 0.0

    return {
        "date": target.isoformat(),
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "no_trades": summary.get("no_trades", 0),
        "risk_rejected": summary.get("risk_rejected", 0),
        "pnl": pnl,
        "win_rate": win_rate,
        "open_position": state.has_open_position,
        "no_trade_reasons": no_trade_reasons,
        "strategies": trade_strategies,
    }


def _format_digest(day: dict, config_max_trades: int) -> str:
    pnl = day["pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    pnl_icon = "🟢" if pnl > 0 else ("🔴" if pnl < 0 else "⚪")

    trades = day["trades"]
    wins = day["wins"]
    losses = day["losses"]
    win_rate = day["win_rate"]
    no_trades = day["no_trades"]
    risk_rejected = day["risk_rejected"]
    open_pos = day["open_position"]

    # Top NO_TRADE reasons (max 3)
    from collections import Counter
    top_reasons = Counter(day["no_trade_reasons"]).most_common(3)

    lines = [
        f"📊 **RiskSentinel EOD — {day['date']}**",
        _DIVIDER,
        f"{pnl_icon} P&L       : **{pnl_sign}${pnl:.2f}**",
        f"📈 Trades   : {trades}/{config_max_trades}  (W:{wins} L:{losses})",
        f"🎯 Win Rate : {win_rate}%",
    ]

    if open_pos:
        lines.append("⚠️  Open Pos : YES — position still open")
    else:
        lines.append("✅ Open Pos : None — flat")

    lines.append(f"🚫 No Trade : {no_trades}  |  Risk Rej: {risk_rejected}")

    if top_reasons:
        lines.append(_DIVIDER)
        lines.append("Top NO_TRADE reasons:")
        for reason, count in top_reasons:
            short = reason[:55] + "…" if len(reason) > 55 else reason
            lines.append(f"  • {short} ×{count}")

    if day["strategies"]:
        lines.append(_DIVIDER)
        strat_counts = Counter(day["strategies"]).most_common(3)
        lines.append("Strategies fired:")
        for strat, count in strat_counts:
            lines.append(f"  • {strat} ×{count}")

    lines.append(_DIVIDER)
    now_et = datetime.now(_ET).strftime("%H:%M ET")
    lines.append(f"Generated {now_et}  |  paper mode  |  LIVE OFF")

    return "\n".join(lines)


def _send_discord(webhook_url: str, message: str) -> bool:
    try:
        import httpx
        body = json.dumps({"content": message}).encode("utf-8")
        resp = httpx.post(
            webhook_url,
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as exc:
        logger.error("Discord send failed: %s", exc)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily EOD digest to Discord")
    parser.add_argument("--dry-run", action="store_true", help="Print without sending")
    parser.add_argument("--date", default=None, help="Target date YYYY-MM-DD (default: today)")
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()

    config = load_config()
    day = _load_day(config.log_dir, target)
    message = _format_digest(day, config.max_trades_per_day)

    if args.dry_run:
        print(message)
        return

    webhook_url = config.discord_webhook_url or os.getenv("DISCORD_WEBHOOK_URL", "")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL not set — dry run:")
        print(message)
        return

    sent = _send_discord(webhook_url, message)
    if sent:
        print(f"✓ Digest sent to Discord for {target}")
    else:
        print(f"✗ Failed to send digest")
        sys.exit(1)


if __name__ == "__main__":
    main()
