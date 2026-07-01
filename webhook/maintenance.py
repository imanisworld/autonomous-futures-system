"""
webhook/maintenance.py

Deployment / maintenance safety mode.

Maintenance mode can be activated two ways:
  1. MAINTENANCE_MODE=true  — startup-time configuration. Changing it requires a
     process restart (the value is read fresh each call, but the canonical
     contract is "set it and restart").
  2. MAINTENANCE_FLAG_PATH  — an optional runtime file flag. If the env var is
     set and the file exists, maintenance mode is active. This lets an operator
     toggle maintenance WITHOUT a restart (e.g. `touch`/`rm` the flag file).

Either signal activates maintenance mode. The flag-file existence check is
cached for a short TTL (default 5s) so we do not stat the filesystem on every
inbound request.

When maintenance mode is active the webhook intake returns 503 and NOTHING is
allowed into the decision/execution pipeline.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Callable, Mapping, Optional

FLAG_CACHE_TTL_SECONDS = 5.0

# Tiny in-process cache: (expires_at, value) for the flag-file existence check.
_flag_cache: dict[str, tuple[float, bool]] = {}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("true", "1", "yes", "on")


def _flag_file_active(
    flag_path: str,
    clock: Callable[[], float],
    ttl: float,
) -> bool:
    now = clock()
    cached = _flag_cache.get(flag_path)
    if cached is not None and cached[0] > now:
        return cached[1]
    active = Path(flag_path).exists()
    _flag_cache[flag_path] = (now + ttl, active)
    return active


def maintenance_active(
    env: Optional[Mapping[str, str]] = None,
    clock: Callable[[], float] = time.monotonic,
    ttl: float = FLAG_CACHE_TTL_SECONDS,
) -> bool:
    """Return True if maintenance mode is active via env flag or runtime file flag."""
    env = env if env is not None else os.environ
    if _truthy(env.get("MAINTENANCE_MODE")):
        return True
    flag_path = str(env.get("MAINTENANCE_FLAG_PATH", "")).strip()
    if flag_path:
        return _flag_file_active(flag_path, clock, ttl)
    return False


def reset_flag_cache() -> None:
    """Test/maintenance helper — clears the flag-file existence cache."""
    _flag_cache.clear()
