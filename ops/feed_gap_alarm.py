#!/usr/bin/env python3
"""15-minute feed-gap alarm — read-only operational overlay (v2, reviewed).

Watches the newest 15m bar per instrument in the bar-history jsonl files and
Discord-alerts when the decision feed goes quiet during CME Globex trading
hours. Replaces the 2026-07-17 prototype, whose 25-minute threshold
false-fired every healthy half-hour (a healthy 15m feed's newest-bar-open age
legitimately oscillates 0-30 minutes) and whose single global status flapped
stale/recovery pings when the two instruments disagreed.

v2 behavior:
  * 31-minute staleness threshold (healthy peaks at 30).
  * Per-instrument state: transitions, reminders, and recoveries are
    independent for MNQ and MES.
  * Alert on healthy->stale transition, reminder every 120 minutes while
    stale, recovery notice on stale->healthy.
  * CME Globex market-hours aware (Sun 22:00Z - Fri 21:00Z, daily 21:00-22:00Z
    maintenance break, Saturday closed) with reopen grace via a 22:00Z
    baseline.
  * --dry-run prints would-send messages without POSTing; --env selects the
    env file carrying DISCORD_WEBHOOK_URL.

NEVER places/modifies/cancels orders, never restarts services, never touches
modes or config. Pure file reads + optional Discord POSTs. Self-contained,
stdlib-only: this exact file is byte-copied to the box and run by cron under
the system python3.
"""
from __future__ import annotations

import argparse
import glob
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

INSTRUMENTS = ("MNQ", "MES")
THRESHOLD_MIN = 31
REMINDER_MIN = 120
USER_AGENT = "afs-ops/1.0"
STATE_FILENAME = "feed_gap_alarm_state.json"
LOG_FILENAME = "feed_gap_alarm.log"


# ── env / logging / delivery ─────────────────────────────────────────────────

def read_env(env_path: Path, name: str) -> Optional[str]:
    try:
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith(f"{name}=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def log_line(log_dir: Path, msg: str, *, now: datetime) -> None:
    path = log_dir / LOG_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(f"{now.isoformat(timespec='seconds')} {msg}\n")


def send_discord(env_path: Path, log_dir: Path, msg: str, *, now: datetime) -> bool:
    """POST one message. Returns True on delivery; failures are logged
    visibly (silence is never success — 2026-07-16 default-UA lesson)."""
    url = read_env(env_path, "DISCORD_WEBHOOK_URL")
    if not url:
        log_line(log_dir, "WARN no DISCORD_WEBHOOK_URL; alert not sent", now=now)
        return False
    try:
        body = json.dumps({"content": msg}).encode()
        # Discord's edge 403s the default Python-urllib User-Agent —
        # a custom UA is required (verified on-box 2026-07-16).
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json",
                                     "User-Agent": USER_AGENT})
        urllib.request.urlopen(req, timeout=15).read()
        log_line(log_dir, "discord alert delivered", now=now)
        return True
    except urllib.error.HTTPError as exc:
        log_line(log_dir, f"WARN discord alert failed: HTTP {exc.code}", now=now)
    except Exception as exc:  # fail-soft: alerting must never mask the log
        log_line(log_dir, f"WARN discord alert failed: {type(exc).__name__}", now=now)
    return False


# ── market hours ─────────────────────────────────────────────────────────────

def market_open(now: datetime) -> bool:
    """CME Globex equity-futures hours, UTC: Sun 22:00 - Fri 21:00 with a
    daily 21:00-22:00 maintenance break. Saturday fully closed."""
    wd = now.weekday()  # Mon=0 .. Sun=6
    if wd == 5:
        return False
    if wd == 4 and now.hour >= 21:
        return False
    if wd == 6 and now.hour < 22:
        return False
    if now.hour == 21:
        return False
    return True


def last_reopen(now: datetime) -> datetime:
    """Most recent 22:00Z session (re)open at or before now."""
    candidate = now.replace(hour=22, minute=0, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)
    while candidate.weekday() in (4, 5):  # 22:00Z Fri/Sat are not opens
        candidate -= timedelta(days=1)
    return candidate


# ── bar reading ──────────────────────────────────────────────────────────────

def newest_15m_bar(log_dir: Path, instrument: str) -> Optional[datetime]:
    newest: Optional[datetime] = None
    for path in sorted(glob.glob(str(log_dir / f"bars_{instrument}_*.jsonl")))[-2:]:
        try:
            with open(path) as fh:
                for line in fh:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if str(row.get("timeframe", "15")) != "15":
                        continue
                    raw = row.get("time") or row.get("ts") or row.get("timestamp")
                    if not raw:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(raw))
                    except ValueError:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if newest is None or ts > newest:
                        newest = ts
        except OSError:
            continue
    return newest


# ── state ────────────────────────────────────────────────────────────────────

def _default_instrument_state() -> dict:
    return {"status": "healthy", "stale_since": None, "last_alert_utc": None}


def load_state(log_dir: Path) -> dict:
    path = log_dir / STATE_FILENAME
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    instruments = raw.get("instruments")
    if not isinstance(instruments, dict):
        instruments = {}
    out = {"instruments": {}}
    for name in INSTRUMENTS:
        entry = instruments.get(name)
        if not isinstance(entry, dict) or entry.get("status") not in ("healthy", "stale"):
            entry = _default_instrument_state()
        out["instruments"][name] = {
            "status": entry.get("status", "healthy"),
            "stale_since": entry.get("stale_since"),
            "last_alert_utc": entry.get("last_alert_utc"),
        }
    return out


def save_state(log_dir: Path, state: dict, *, now: datetime) -> None:
    state["checked_utc"] = now.isoformat(timespec="seconds")
    path = log_dir / STATE_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, sort_keys=True) + "\n")
    tmp.replace(path)


# ── core ─────────────────────────────────────────────────────────────────────

def _minutes_since(now: datetime, iso: Optional[str]) -> Optional[float]:
    if not iso:
        return None
    try:
        then = datetime.fromisoformat(str(iso))
    except ValueError:
        return None
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (now - then).total_seconds() / 60.0


def run_once(
    *,
    log_dir: Path,
    env_path: Path,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    sender: Optional[Callable[[str], bool]] = None,
) -> list[str]:
    """One check cycle. Returns the list of messages emitted (sent or would-send)."""
    now = now or datetime.now(timezone.utc)
    state = load_state(log_dir)
    messages: list[str] = []

    def emit(msg: str) -> None:
        messages.append(msg)
        if dry_run:
            log_line(log_dir, f"DRY-RUN would send: {msg}", now=now)
            print(f"DRY-RUN would send: {msg}")
        elif sender is not None:
            sender(msg)
        else:
            send_discord(env_path, log_dir, msg, now=now)

    if not market_open(now):
        log_line(log_dir, "market closed; no staleness check", now=now)
        save_state(log_dir, state, now=now)
        return messages

    reopen = last_reopen(now)
    summary_parts: list[str] = []
    for instrument in INSTRUMENTS:
        entry = state["instruments"][instrument]
        newest = newest_15m_bar(log_dir, instrument)
        baseline = max(newest, reopen) if newest else reopen
        age_min = (now - baseline).total_seconds() / 60.0
        is_stale = age_min > THRESHOLD_MIN
        summary_parts.append(
            f"{instrument} last 15m bar "
            f"{newest.isoformat(timespec='minutes') if newest else 'NONE'} "
            f"(age vs baseline {age_min:.0f}m)"
        )
        if is_stale:
            if entry["status"] == "healthy":
                entry["status"] = "stale"
                entry["stale_since"] = now.isoformat(timespec="seconds")
                entry["last_alert_utc"] = now.isoformat(timespec="seconds")
                emit(
                    f"🚨 FEED GAP: {instrument} 15m bars stale "
                    f"(>{THRESHOLD_MIN}m during market hours; age {age_min:.0f}m). "
                    "Decision engine is blind on this instrument's 15m until "
                    "delivery resumes. Read-only alarm; no action taken."
                )
            else:
                since_alert = _minutes_since(now, entry["last_alert_utc"])
                if since_alert is None or since_alert >= REMINDER_MIN:
                    entry["last_alert_utc"] = now.isoformat(timespec="seconds")
                    stale_for = _minutes_since(now, entry["stale_since"])
                    emit(
                        f"⏰ FEED GAP ongoing: {instrument} 15m bars still stale "
                        f"({stale_for:.0f}m and counting)."
                        if stale_for is not None else
                        f"⏰ FEED GAP ongoing: {instrument} 15m bars still stale."
                    )
        else:
            if entry["status"] == "stale":
                entry["status"] = "healthy"
                entry["stale_since"] = None
                entry["last_alert_utc"] = None
                emit(f"✅ FEED RECOVERED: {instrument} 15m bars flowing again (age {age_min:.0f}m).")

    statuses = "; ".join(
        f"{name}={state['instruments'][name]['status']}" for name in INSTRUMENTS
    )
    log_line(log_dir, f"{statuses} | {'; '.join(summary_parts)}", now=now)
    save_state(log_dir, state, now=now)
    return messages


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="/root/afs-shared/logs")
    parser.add_argument("--env", default="/root/afs-shared/.env")
    parser.add_argument("--dry-run", action="store_true",
                        help="print would-send messages; never POST")
    args = parser.parse_args(argv)
    run_once(
        log_dir=Path(args.log_dir),
        env_path=Path(args.env),
        dry_run=bool(args.dry_run),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
