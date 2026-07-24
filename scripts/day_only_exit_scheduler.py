#!/usr/bin/env python3
"""Local-only trigger for the app-owned day-only exit fallback."""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.request


ENDPOINT = "http://127.0.0.1:8000/internal/day-only-exit"
logger = logging.getLogger("day_only_exit_scheduler")


def trigger(secret: str, *, timeout_seconds: float = 20.0) -> dict:
    if not secret:
        raise RuntimeError("DAY_ONLY_EXIT_SECRET is required")
    request = urllib.request.Request(
        ENDPOINT,
        data=b"",
        method="POST",
        headers={"X-Day-Only-Exit-Secret": secret},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(f"day-only endpoint failed closed: {payload}")
    return payload


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        result = trigger(os.getenv("DAY_ONLY_EXIT_SECRET", ""))
        logger.info("day-only fallback result: %s", result)
        return 0
    except (RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
        logger.error("day-only fallback failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
