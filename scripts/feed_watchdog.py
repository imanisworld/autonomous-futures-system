#!/usr/bin/env python3
"""
scripts/feed_watchdog.py

Dead-man's-switch for TradingView webhook ingestion.

Run on a short systemd timer (every ~5 min). If no webhook has been recorded for
longer than the configured tolerance DURING an active futures session, it posts a
Discord alert — once per outage, with a periodic reminder — and a recovery notice
when bars resume. This exists because on 2026-06-04 the ingestion path silently
dropped every signal for ~10 hours and nothing actively warned the operator.

Read-only with respect to trading. For the cross-instrument observation campaign,
per-instrument health is based on a successfully processed 15m campaign bar plus
a matching BarHistory row — not on generic webhook receipt freshness. Therefore
a healthy 5m stream or a failed observation transport cannot mask a dead 15m
collector.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Make the repo importable when run directly by systemd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import load_config  # noqa: E402
from context.futures_session import (  # noqa: E402
    futures_session_active, feed_stale_after_minutes, product_session_active,
)
from execution.cross_instrument_observation import (  # noqa: E402
    OBSERVATION_UNIVERSE,
    campaign_enabled as observation_campaign_enabled,
)
from notifications.discord_notifier import NotificationResult, send_discord_alert  # noqa: E402
from ops.cross_instrument_feed_health import build_feed_health  # noqa: E402

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


def _load_instrument_received_at(log_dir: Path, root: str) -> tuple[datetime | None, int | None]:
    """Legacy receipt freshness used only while the observation campaign is OFF."""
    path = log_dir / f"latest_webhook_{root}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None
    raw = data.get("received_at")
    if not raw:
        return None, None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None, None
    tf_raw = ((data.get("payload") or {}).get("timeframe"))
    tf = None
    try:
        digits = "".join(ch for ch in str(tf_raw or "") if ch.isdigit())
        tf = int(digits) if digits else None
    except ValueError:
        tf = None
    return dt, tf


def _check_campaign_instruments(now: datetime, log_dir: Path, state: dict, send, cfg) -> dict:
    """Watch proven campaign-processing freshness, never generic receipt freshness."""
    per_state: dict = dict(state.get("instruments") or {})
    now_epoch = now.timestamp()
    stale_now: list[str] = []
    recovered: list[str] = []
    health = build_feed_health(log_dir, now=now)

    for root in OBSERVATION_UNIVERSE:
        item = health["instruments"][root]
        prior = per_state.get(root, {})
        if not item["proven_15m"]:
            # This is an activation-proof failure, not yet a runtime outage.
            # Keep it visible in `unproven`; do not spam Discord before the
            # operator has proven the first successful 15m bar for the root.
            continue
        if item["session_active"] is False:
            if prior.get("status") == "down":
                per_state[root] = {"status": "ok"}
            continue
        if item["stale_after_seconds"] is not None and item["status"] == "STALE":
            due = (now_epoch - float(prior.get("last_alert_epoch", 0))) >= _REMINDER_SECONDS
            if prior.get("status") != "down" or due:
                age = item["age_seconds"]
                stale_now.append(f"{root} ({int((age or 0) / 60)}m, authoritative 15m campaign bars)")
                per_state[root] = {
                    "status": "down",
                    "last_alert_epoch": now_epoch,
                    "last_successful_15m_bar_ts": item["last_successful_15m_bar_ts"],
                }
        elif prior.get("status") == "down":
            recovered.append(root)
            per_state[root] = {"status": "ok"}

    if stale_now:
        send(cfg, (
            "🚨 RiskSentinel feed watchdog — OBSERVATION 15M FEED STALE\n"
            + "\n".join(f"• {item}" for item in stale_now)
            + "\nFresh 5m webhooks do not satisfy this check; each root requires a successfully processed 15m campaign bar."
        ))
    if recovered:
        send(cfg, "✅ RiskSentinel feed watchdog — observation 15m feed recovered: " + ", ".join(recovered))
    return {
        "instruments": per_state,
        "stale": stale_now,
        "recovered": recovered,
        "unproven": health["unproven_instruments"],
        "authority": health["authority"],
        "ready_to_trust_collection_feed": health["ready_to_trust_collection_feed"],
    }


def _check_legacy_instruments(now: datetime, log_dir: Path, state: dict, send, cfg, tf_default: int) -> dict:
    """Legacy receipt-based per-instrument monitor, retained while campaign OFF."""
    per_state: dict = dict(state.get("instruments") or {})
    now_epoch = now.timestamp()
    stale_now: list[str] = []
    recovered: list[str] = []
    for root in OBSERVATION_UNIVERSE:
        received, tf = _load_instrument_received_at(log_dir, root)
        if received is None:
            continue
        active = product_session_active(root, now)
        if not active:
            if per_state.get(root, {}).get("status") == "down":
                per_state[root] = {"status": "ok"}
            continue
        tolerance = feed_stale_after_minutes(tf or tf_default) * 60
        age = (now - received).total_seconds()
        prior = per_state.get(root, {})
        if age > tolerance:
            due = (now_epoch - float(prior.get("last_alert_epoch", 0))) >= _REMINDER_SECONDS
            if prior.get("status") != "down" or due:
                stale_now.append(f"{root} ({int(age / 60)}m, {tf or tf_default}m bars)")
                per_state[root] = {"status": "down", "last_alert_epoch": now_epoch,
                                   "last_received_at": received.isoformat()}
        elif prior.get("status") == "down":
            recovered.append(root)
            per_state[root] = {"status": "ok"}
    if stale_now:
        send(cfg, (
            "🚨 RiskSentinel feed watchdog — INSTRUMENT FEED STALE\n"
            + "\n".join(f"• {item}" for item in stale_now)
            + "\nOther instruments may be healthy; each feed is judged on its own product calendar."
        ))
    if recovered:
        send(cfg, "✅ RiskSentinel feed watchdog — instrument feed recovered: " + ", ".join(recovered))
    return {"instruments": per_state, "stale": stale_now, "recovered": recovered, "authority": "webhook_receipt_legacy"}


def check_instruments(now: datetime, log_dir: Path, state: dict, send, cfg, tf_default: int) -> dict:
    """Per-instrument monitoring; campaign ON switches to authoritative 15m proof."""
    if observation_campaign_enabled():
        return _check_campaign_instruments(now, log_dir, state, send, cfg)
    return _check_legacy_instruments(now, log_dir, state, send, cfg, tf_default)


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
    tolerance = feed_stale_after_minutes(tf) * 60

    state_path = log_dir / "feed_watchdog_state.json"
    state = _read_state(state_path)

    # Per-instrument pass first. When cross-instrument observation is armed,
    # this reads campaign seen-bar + BarHistory proof, not latest webhook files.
    per = check_instruments(now, log_dir, state, send, cfg, tf)
    state["instruments"] = per["instruments"]

    # Legacy/global ingestion check remains unchanged and independent. It still
    # protects the primary trading webhook path; it is NOT the activation proof
    # for cross-instrument collection.
    if not futures_session_active(now):
        if state.get("status") == "down":
            _write_state(state_path, {"status": "ok", "instruments": per["instruments"]})
        else:
            _write_state(state_path, state)
        return {"action": "idle_session", "active": False, "instruments": per}

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
                "instruments": per["instruments"],
            })
            return {"action": "alerted", "sent": getattr(result, "sent", None), "age_seconds": age, "instruments": per}
        _write_state(state_path, state)
        return {"action": "still_down_no_reminder", "age_seconds": age, "instruments": per}

    if state.get("status") == "down":
        send(cfg, (
            "✅ RiskSentinel feed watchdog — INGESTION RECOVERED\n"
            f"TradingView webhooks are arriving again (last one {int(age / 60)}m ago)."
        ))
        _write_state(state_path, {"status": "ok", "instruments": per["instruments"]})
        return {"action": "recovered", "age_seconds": age, "instruments": per}

    _write_state(state_path, state)
    return {"action": "ok", "age_seconds": age, "instruments": per}


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
