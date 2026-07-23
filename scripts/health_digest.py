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
import re
import shutil
import subprocess
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

DISK_WARN_PCT = 80.0
DISK_ALERT_PCT = 90.0
# A held position past this age is no longer "resolving" — it is stuck (the
# 2026-07-21 orphan sat 46.8h; the longest legitimate hold in 46 days was ~2.5h).
POSITION_STALE_MINUTES = 180.0
_BASE = "http://127.0.0.1:8000"


def _load_env() -> None:
    """Load env files into the process (fail-soft).

    The cron entry runs this script WITHOUT the service's EnvironmentFile, so
    the Discord routes and the status-gate credentials are invisible unless we
    load them ourselves — that gap is exactly why the digest silently degraded
    to "printing only" + 401 broker reads from 2026-07-03. A relative ``.env``
    alone isn't enough: it resolves against the current release folder (cwd),
    which an atomic release swap can leave without one — the same gap
    resurfaced that way afterward. ``AFS_SHARED_DIR`` (same default as
    scripts/atomic_release.sh) is the one location guaranteed to survive a
    release swap, so it's loaded second as the durable fallback. Never
    overrides values already present in the environment.
    """
    try:
        from dotenv import load_dotenv
        env_path = Path(".env")
        if env_path.exists():
            load_dotenv(env_path)
        shared_env_path = Path(os.getenv("AFS_SHARED_DIR", "/root/afs-shared")) / ".env"
        if shared_env_path.exists():
            load_dotenv(shared_env_path)
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

    # ── Pipeline-visibility escalations (the proven monitoring gap) ──────────
    # Broker says flat while the local journal still shows an open slot — the
    # drift class (phantom / erased-outcome). Distinct from a legitimately open
    # position (handled below): here the broker is FLAT.
    if checks.get("broker_local_drift"):
        esc("ALERT")
        problems.append(
            "broker FLAT but local shows an OPEN position — state drift, reconcile/verify"
        )
    # A held position past the stale threshold is stuck, not resolving.
    if checks.get("block_stale"):
        esc("ALERT")
        problems.append("open position unresolved past threshold — stuck, not resolving")
    # Bars kept arriving while every decision was blocked — the pipeline is blind
    # (the 2026-07-22 signature: 184 bars, 0 authorized decisions).
    if checks.get("bars_without_decisions"):
        esc("ALERT")
        problems.append(
            "bars arriving but ALL candidate evaluation blocked — pipeline blind"
        )

    flat = checks.get("position_flat")
    if flat is False:
        # An open position is only routine while its bracket children are
        # working. Zero working orders = NAKED (the MES 2026-07-21 orphan sat
        # open ~36h while this digest said "OK" — position-open was a note).
        working = checks.get("working_orders")
        if working == 0:
            esc("ALERT")
            problems.append(
                "position OPEN with ZERO working orders — NAKED, "
                "flatten/verify in Tradovate"
            )
        elif working is None:
            esc("WARN")
            problems.append("position OPEN, protection state unknown")
        else:
            notes.append(f"position OPEN ({working} working order(s))")

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
    # Working-order count from the live-preflight surface — needed to tell a
    # routine bracketed hold apart from a NAKED open position (0 working).
    preflight = _get_json("/status/live-preflight")
    checks["working_orders"] = None
    if preflight is not None:
        for c in preflight.get("checks") or []:
            if c.get("name") == "no_working_orders":
                m = re.match(r"\s*(\d+)", str(c.get("detail") or ""))
                if m:
                    checks["working_orders"] = int(m.group(1))
                break
    checks["errors_today"] = _errors_today()
    try:
        usage = shutil.disk_usage("/")
        checks["disk_pct"] = round(100.0 * usage.used / usage.total, 1)
    except OSError:
        checks["disk_pct"] = None
    # Pipeline-visibility signal from today's journal (fail-soft — a read hiccup
    # must never break the digest). Surfaces the block conditions the runner now
    # journals: a stale/unresolved hold, bars arriving while every decision is
    # blocked (the 2026-07-22 blinded-pipeline signature), and broker-flat /
    # local-open drift (broker says flat while the journal still shows a slot).
    checks["block_stale"] = False
    checks["bars_without_decisions"] = False
    checks["broker_local_drift"] = False
    try:
        _sig = _block_visibility_signal()
        if _sig is not None:
            checks["block_stale"] = bool(
                _sig["summary"]["has_stale_resolve"]
                or _sig["summary"]["worst_position_age_minutes"] > POSITION_STALE_MINUTES
            )
            checks["bars_without_decisions"] = bool(_sig["summary"]["bars_without_decisions"])
            # Broker read (position_flat) contradicts the local slot → drift. Only
            # meaningful when the broker was actually reachable this run.
            if checks.get("broker_reachable") and checks.get("position_flat") is True:
                checks["broker_local_drift"] = bool(_sig["local_open"])
    except Exception:  # noqa: BLE001 — visibility signal is best-effort only
        pass
    return checks


def _block_visibility_signal() -> Optional[dict]:
    """Read today's journal for the pipeline-visibility signal: the day's
    BLOCK_VISIBILITY records aggregated, the local open-position flag, and the
    bar count. Pure aggregation lives in ops.block_visibility; this only reads.
    Returns None if the journal or its deps are unavailable."""
    try:
        from journal.journal_logger import JournalLogger
        from ops.block_visibility import summarize_blocks
    except Exception:
        return None
    log_dir = os.getenv("LOG_DIR", "logs")
    j = JournalLogger(log_dir=log_dir)
    path = j._journal_path()  # today
    if not path.exists():
        return None
    records, bars = [], 0
    for entry in j._read_entries(path):
        t = entry.get("type")
        if t == "BLOCK_VISIBILITY":
            records.append(entry)
        elif t == "BAR_CLAIM":
            bars += 1
    return {
        "summary": summarize_blocks(records, bars_claimed=bars),
        "local_open": bool(j.get_daily_state().has_open_position),
    }


def _post_discord(url: str, content: str) -> bool:
    try:
        body = json.dumps({"content": content}).encode()
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json",
            # Discord's edge 403s urllib's default "Python-urllib/x" UA — the
            # service's own posts work because httpx sends a real UA. Verified
            # on the box 2026-07-06: same webhook, curl 204 / bare urllib 403.
            "User-Agent": "afs-health-digest/1.0",
        })
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
