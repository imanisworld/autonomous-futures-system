#!/usr/bin/env python3
"""
scripts/feed_watchdog.py

Dead-man's-switch for TradingView webhook ingestion.

Run on a short systemd timer (every ~5 min). If no webhook has been recorded for
longer than the configured tolerance DURING an active futures session, it posts a
Discord alert — once per outage, with a periodic reminder — and a recovery notice
when bars resume. This exists because on 2026-06-04 the ingestion path silently
dropped every signal for ~10 hours and nothing actively warned the operator.

Read-only with respect to trading: it only reads latest_webhook.json plus a small
state file and posts to Discord. It never touches positions, orders, or risk.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# Make the repo importable when run directly by systemd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config  # noqa: E402
from context.futures_session import futures_session_active, feed_stale_after_minutes  # noqa: E402
from notifications.discord_notifier import NotificationResult, send_discord_alert  # noqa: E402

logger = logging.getLogger("feed_watchdog")
_REMINDER_SECONDS = 2 * 3600  # while still down, re-alert at most every 2h


def _load_received_at(log_dir: Path) -> datetime | None:
    path = log_dir / "latest_webhook.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("received_at")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_state(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as exc:
        logger.warning("could not write watchdog state: %s", exc)


def run(now: datetime | None = None, send=send_discord_alert, config=None) -> dict:
    cfg = config or load_config()
    now = now or datetime.now(timezone.utc)
    log_dir = Path(cfg.log_dir)
    tf = int(getattr(cfg, "expected_timeframe_minutes", 15) or 15)
    tolerance = feed_stale_after_minutes(tf) * 60  # shared ~2 bars + grace

    state_path = log_dir / "feed_watchdog_state.json"
    state = _read_state(state_path)

    # Outside an active session, no bars are expected — clear any prior alert.
    if not futures_session_active(now):
        if state.get("status") == "down":
            _write_state(state_path, {"status": "ok"})
        return {"action": "idle_session", "active": False}

    received_at = _load_received_at(log_dir)
    age = (now - received_at).total_seconds() if received_at else None
    stale = age is None or age > tolerance
    now_epoch = now.timestamp()

    if stale:
        first_time = state.get("status") != "down"
        due_reminder = (now_epoch - float(state.get("last_alert_epoch", 0))) >= _REMINDER_SECONDS
        if first_time or due_reminder:
            age_txt = f"{int(age / 60)}m" if age is not None else "no webhook on record"
            msg = (
                "🚨 RiskSentinel feed watchdog — INGESTION STALE\n"
                f"No TradingView webhook for {age_txt} during an active futures session "
                f"(expected a {tf}m bar every {tf} minutes).\n"
                "Check the TradingView alert log for webhook delivery failures and "
                "confirm the alert is still running."
            )
            result = send(cfg, msg)
            _write_state(state_path, {
                "status": "down",
                "last_alert_epoch": now_epoch,
                "last_received_at": received_at.isoformat() if received_at else None,
            })
            return {"action": "alerted", "sent": getattr(result, "sent", None), "age_seconds": age}
        return {"action": "still_down_no_reminder", "age_seconds": age}

    # Fresh again — send a recovery notice if we had previously alerted.
    if state.get("status") == "down":
        send(cfg, (
            "✅ RiskSentinel feed watchdog — INGESTION RECOVERED\n"
            f"TradingView webhooks are arriving again (last one {int(age / 60)}m ago)."
        ))
        _write_state(state_path, {"status": "ok"})
        return {"action": "recovered", "age_seconds": age}

    return {"action": "ok", "age_seconds": age}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="TradingView ingestion dead-man's-switch.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the message that would be sent; do not post to Discord.")
    args = parser.parse_args(argv)

    if args.dry_run:
        captured: dict = {}

        def _fake(cfg, content) -> NotificationResult:
            captured["content"] = content
            return NotificationResult(sent=False, reason="dry_run")

        out = run(send=_fake)
        if "content" in captured:
            print(captured["content"])
        print(json.dumps(out, default=str))
        return 0

    print(json.dumps(run(), default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
