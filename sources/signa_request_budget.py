"""Shared in-process Signa account-level request backoff.

Observation-only infrastructure. This module has no scanner, strategy, risk,
broker, order, or execution authority. It exists only to prevent independent
Signa consumers in the same process from continuing to spend provider quota
after an account-level HTTP 429 response.
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Callable

DEFAULT_ACCOUNT_BACKOFF_SECONDS = 900.0

_LOCK = threading.Lock()
_BLOCKED_UNTIL: dict[str, float] = {}


def _account_key(base_url: str, api_key: str) -> str:
    blob = f"{str(base_url).rstrip('/')}\0{str(api_key)}".encode()
    return hashlib.sha256(blob).hexdigest()


def retry_after_seconds(
    value: str | None,
    *,
    default_seconds: float = DEFAULT_ACCOUNT_BACKOFF_SECONDS,
) -> float:
    """Parse Retry-After seconds or HTTP-date; fall back conservatively."""
    if value is None or not str(value).strip():
        return max(1.0, float(default_seconds))
    text = str(value).strip()
    try:
        return max(1.0, float(text))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)
        delta = (target.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()
        return max(1.0, delta)
    except (TypeError, ValueError, OverflowError):
        return max(1.0, float(default_seconds))


def account_backoff_remaining(
    base_url: str,
    api_key: str,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> float:
    """Return shared account cooldown remaining in seconds."""
    if not api_key:
        return 0.0
    key = _account_key(base_url, api_key)
    now = float(clock())
    with _LOCK:
        blocked_until = float(_BLOCKED_UNTIL.get(key, 0.0))
        if blocked_until <= now:
            _BLOCKED_UNTIL.pop(key, None)
            return 0.0
        return blocked_until - now


def mark_account_rate_limited(
    base_url: str,
    api_key: str,
    *,
    retry_after: str | None = None,
    clock: Callable[[], float] = time.monotonic,
    default_seconds: float = DEFAULT_ACCOUNT_BACKOFF_SECONDS,
) -> float:
    """Open or extend the shared account circuit after HTTP 429."""
    seconds = retry_after_seconds(retry_after, default_seconds=default_seconds)
    if not api_key:
        return seconds
    key = _account_key(base_url, api_key)
    until = float(clock()) + seconds
    with _LOCK:
        _BLOCKED_UNTIL[key] = max(float(_BLOCKED_UNTIL.get(key, 0.0)), until)
    return seconds


def clear_account_backoff(
    base_url: str,
    api_key: str,
) -> None:
    """Test/maintenance helper; does not make provider requests."""
    key = _account_key(base_url, api_key)
    with _LOCK:
        _BLOCKED_UNTIL.pop(key, None)
