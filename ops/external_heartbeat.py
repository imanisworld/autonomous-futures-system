#!/usr/bin/env python3
"""Provider-neutral external dead-man heartbeat for the AFS VPS.

The external monitor should answer one narrow question: "Can this server still
run the watcher and reach the internet?" Internal watcher findings already
cover trading/feed/runtime problems, so this helper intentionally ignores the
watcher's verdict. It sends a heartbeat only when the watcher's latest tick is
fresh.

Configuration is opt-in through AFS_EXTERNAL_HEARTBEAT_URL. The URL is treated
as a secret-like value and is never printed. No broker, strategy, order, deploy,
restart, or service-control code exists here.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


DEFAULT_WATCHER_STATE = Path("/tmp/afs_watcher/latest_tick.json")
URL_ENV = "AFS_EXTERNAL_HEARTBEAT_URL"
MAX_AGE_ENV = "AFS_EXTERNAL_HEARTBEAT_MAX_AGE_SECONDS"
TIMEOUT_ENV = "AFS_EXTERNAL_HEARTBEAT_TIMEOUT_SECONDS"
DEFAULT_MAX_AGE_SECONDS = 12 * 60
DEFAULT_TIMEOUT_SECONDS = 10.0
USER_AGENT = "afs-external-heartbeat/1"


def _utc(raw: str) -> datetime:
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def _positive_number(raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return float(default)
    value = float(raw)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("value must be a finite positive number")
    return value


def validate_url(raw: str | None) -> str | None:
    if raw is None or not raw.strip():
        return None
    url = raw.strip()
    parsed = urlsplit(url)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise ValueError("AFS_EXTERNAL_HEARTBEAT_URL must be a valid HTTPS URL")
    return url


def watcher_is_fresh(
    state_path: Path,
    *,
    now: datetime | None = None,
    max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
) -> tuple[bool, str]:
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, "watcher state is missing"
    except (OSError, ValueError, TypeError):
        return False, "watcher state is unreadable"

    if not isinstance(payload, dict):
        return False, "watcher state is not a JSON object"
    raw_utc = payload.get("utc")
    if not isinstance(raw_utc, str) or not raw_utc.strip():
        return False, "watcher state has no tick timestamp"
    try:
        observed = _utc(raw_utc)
    except ValueError:
        return False, "watcher tick timestamp is invalid"

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = (current - observed).total_seconds()
    if age < -300:
        return False, "watcher tick timestamp is too far in the future"
    if age > max_age_seconds:
        return False, f"watcher tick is stale ({int(age)}s old)"
    return True, f"watcher tick is fresh ({max(0, int(age))}s old)"


def send_heartbeat(url: str, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        status = getattr(response, "status", None)
        if status is None:
            status = response.getcode()
        if status is not None and not 200 <= int(status) < 300:
            raise RuntimeError(f"heartbeat endpoint returned HTTP {status}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--watcher-state", type=Path, default=DEFAULT_WATCHER_STATE)
    parser.add_argument("--dry-run", action="store_true", help="Validate freshness without sending a request")
    args = parser.parse_args(argv)

    try:
        url = validate_url(os.getenv(URL_ENV))
        max_age = _positive_number(os.getenv(MAX_AGE_ENV), DEFAULT_MAX_AGE_SECONDS)
        timeout = _positive_number(os.getenv(TIMEOUT_ENV), DEFAULT_TIMEOUT_SECONDS)
    except ValueError as exc:
        print(f"external heartbeat configuration error: {exc}", file=sys.stderr)
        return 2

    if url is None:
        print(f"external heartbeat disabled: {URL_ENV} is not configured")
        return 0

    fresh, reason = watcher_is_fresh(args.watcher_state, max_age_seconds=max_age)
    if not fresh:
        print(f"external heartbeat suppressed: {reason}", file=sys.stderr)
        return 3

    if args.dry_run:
        print(f"external heartbeat dry-run: {reason}; no request sent")
        return 0

    try:
        send_heartbeat(url, timeout_seconds=timeout)
    except Exception as exc:  # noqa: BLE001 - fail closed without leaking the configured URL
        print(f"external heartbeat failed: {type(exc).__name__}", file=sys.stderr)
        return 4

    print(f"external heartbeat sent: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
