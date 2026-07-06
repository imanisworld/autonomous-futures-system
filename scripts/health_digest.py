"""Daily box health digest -> Discord (heartbeat channel).

Runs as an EXTERNAL cron (not inside the trading service) so it can detect the
one failure the in-app heartbeat can't: the service being dead / silently halted.
Checks service reachability, broker flatness + Tradovate auth state, today's real
error count (excluding expected limit-miss CANCELLEDs), and disk usage; posts a
single 🟢/🟡/🔴 line.

    cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m scripts.health_digest

Read-only + fail-soft. ``evaluate_health`` is pure (unit-testable); ``collect`` and
``main`` do the I/O. Posts via DISCORD_ROUTE_HEARTBEAT (fallbacks
DISCORD_ROUTE_DAILY_REPORT, DISCORD_WEBHOOK_URL); no webhook -> prints only.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import subprocess
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

DISK_WARN_PCT = 80.0
DISK_ALERT_PCT = 90.0
_BASE = "http://127.0.0.1:8000"


def _load_env() -> None:
    """Load the repo .env into the process (fail-soft).

    The cron entry runs this script WITHOUT the service's EnvironmentFile, so
    the Discord routes and the status-gate credentials are invisible unless we
    load .env ourselves — that gap is exactly why the digest silently degraded
    to "printing only" + 401 broker reads from 2026-07-03. Never overrides
    values already present in the environment.
    """
    try:
        from dotenv import load_dotenv
        env_path = Path(".env")
        if env_path.exists():
            load_dotenv(env_path)
    except Exception:
        pass


def _gate_cookie() -> Optional[str]:
    """Cookie authorizing the sensitive status reads (/status/broker-account).

    Mirrors webhook.app._gate_token exactly: HMAC-SHA256 keyed by WEBHOOK_SECRET
    over "site-access:" + SITE_ACCESS_CODE (parity-tested against the app in
    tests/test_health_digest_auth.py so drift breaks loudly). Returns None when
    the gate is not configured — sending nothing preserves today's behavior.
    """
    code = os.getenv("SITE_ACCESS_CODE", "").strip()
    secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if not code or not secret:
        return None
    token = hmac.new(
        secret.encode(), ("site-access:" + code).encode(), hashlib.sha256
    ).hexdigest()
    return f"vp_access={token}"


# ── pure ───────────────────────────────────────────────────────────────────
def evaluate_health(checks: dict) -> dict:
    """Turn collected signals into a verdict. status ∈ OK | WARN | ALERT."""
    problems: list[str] = []
    notes: list[str] = []
    level = "OK"

    def esc(new: str) -> None:
        nonlocal level
        order = {"OK": 0, "WARN": 1, "ALERT": 2}
        if order[new] > order[level]:
            level = new

    if not checks.get("service_ok"):
        esc("ALERT")
        problems.append("service unreachable")
        # If the service is down nothing else is meaningful.
        return {"status": "ALERT", "problems": problems, "notes": notes}

    if not checks.get("broker_reachable", True):
        esc("ALERT")
        problems.append("broker status unreachable")
    auth = checks.get("auth_state")
    if auth and auth != "HEALTHY":
        esc("ALERT")
        problems.append(f"Tradovate auth {auth}")

    errors = checks.get("errors_today")
    if isinstance(errors, int) and errors > 0:
        esc("WARN")
        problems.append(f"{errors} real error(s) today")

    disk = checks.get("disk_pct")
    if isinstance(disk, (int, float)):
        if disk >= DISK_ALERT_PCT:
            esc("ALERT")
            problems.append(f"disk {disk:.0f}% full")
        elif disk >= DISK_WARN_PCT:
            esc("WARN")
            problems.append(f"disk {disk:.0f}% full")

    flat = checks.get("position_flat")
    if flat is False:
        notes.append("position OPEN")  # informational, not an alarm on its own

    return {"status": level, "problems": problems, "notes": notes}


def format_digest(verdict: dict, checks: dict, *, day_iso: str) -> str:
    icon = {"OK": "\U0001F7E2", "WARN": "\U0001F7E1", "ALERT": "\U0001F534"}[verdict["status"]]
    if verdict["status"] == "OK":
        head = f"{icon} **Box health — {day_iso}: OK**"
    else:
        head = f"{icon} **Box health — {day_iso}: {verdict['status']}** — " + "; ".join(verdict["problems"])
    detail = (
        f"service {'up' if checks.get('service_ok') else 'DOWN'} · "
        f"auth {checks.get('auth_state') or '?'} · "
        f"{'flat' if checks.get('position_flat') else 'position open' if checks.get('position_flat') is False else 'pos ?'} · "
        f"errors {checks.get('errors_today', '?')} · "
        f"disk {checks.get('disk_pct'):.0f}%" if isinstance(checks.get("disk_pct"), (int, float)) else "disk ?"
    )
    line = head + "\n" + detail
    if verdict["notes"]:
        line += " · " + "; ".join(verdict["notes"])
    return line


# ── I/O (fail-soft) ────────────────────────────────────────────────────────
def _get_json(path: str, timeout: float = 6.0) -> Optional[dict]:
    try:
        req = urllib.request.Request(_BASE + path)
        cookie = _gate_cookie()
        if cookie:
            req.add_header("Cookie", cookie)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def _errors_today() -> Optional[int]:
    try:
        out = subprocess.run(
            ["journalctl", "-u", "futures-bot", "--since", "today", "--no-pager"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    # Expected limit-misses log at WARNING (ENTRY_NOT_FILLED), so error-level lines
    # are genuine failures — count directly.
    return sum(
        1 for ln in out.splitlines()
        if any(t in ln.lower() for t in ("error", "traceback", "exception", "critical"))
    )


def collect() -> dict:
    health = _get_json("/health")
    broker = _get_json("/status/broker-account")
    checks: dict = {"service_ok": health is not None}
    if broker is not None:
        checks["broker_reachable"] = True
        checks["position_flat"] = broker.get("position") in (None, "", [])
        rel = broker.get("reliability") or {}
        checks["auth_state"] = rel.get("state")
    else:
        checks["broker_reachable"] = False
    checks["errors_today"] = _errors_today()
    try:
        usage = shutil.disk_usage("/")
        checks["disk_pct"] = round(100.0 * usage.used / usage.total, 1)
    except OSError:
        checks["disk_pct"] = None
    return checks


def _post_discord(url: str, content: str) -> bool:
    try:
        body = json.dumps({"content": content}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def main(argv: Optional[list[str]] = None) -> int:
    _load_env()
    generated_at = datetime.now(timezone.utc)
    day_iso = generated_at.date().isoformat()
    checks = collect()
    verdict = evaluate_health(checks)
    text = format_digest(verdict, checks, day_iso=day_iso)

    webhook = (
        os.getenv("DISCORD_ROUTE_HEARTBEAT")
        or os.getenv("DISCORD_ROUTE_DAILY_REPORT")
        or os.getenv("DISCORD_WEBHOOK_URL")
        or ""
    ).strip()
    posted = None
    if webhook:
        posted = _post_discord(webhook, text)
        print(f"[health_digest] posted: {posted}")
    else:
        print("[health_digest] no webhook configured; printing only")
    print(text)
    try:
        log_dir = Path(os.getenv("LOG_DIR", "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / "health_digest_latest.json").write_text(json.dumps({
            "job": "health_digest",
            "generated_at": generated_at.isoformat(),
            "verdict": verdict["status"],
            "discord_posted": posted,
        }, indent=2))
    except OSError:
        pass
    # Non-zero exit on ALERT so cron mail / monitoring can also catch it.
    return 2 if verdict["status"] == "ALERT" else 0


if __name__ == "__main__":
    raise SystemExit(main())
