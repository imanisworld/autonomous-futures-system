#!/usr/bin/env python3
"""Paper collection digest -> Discord (optional routes), read-only.

    python -m scripts.paper_collection_digest --domain futures --period daily
    python -m scripts.paper_collection_digest --domain options --period weekly --post
    python -m scripts.paper_collection_digest --domain futures --period daily --date 2026-09-16 --json

Runs as an EXTERNAL systemd oneshot on the box — never inside the trading
service, so scheduling it requires no service restart.  Units live in
deploy/systemd/ (afs-paper-collection-daily / -weekly) and use
``OnCalendar=... America/New_York`` exactly like afs-coverage-collector, so DST
needs no re-pinning: daily Mon-Fri 17:15 ET (after the 16:35 ET options
coverage collector and the 17:00 ET CME pause), weekly Friday 17:25 ET.
Runbook: docs/paper-collection-digest.md.  Paths are passed explicitly on the
command line so nothing in .env can redirect them.

Posting goes through notifications.discord_router.DiscordRouter on the
optional routes ``paper_collection_futures`` / ``paper_collection_options``.
An unset route is a silent local print (exit 0); a delivery failure is logged
by the router and never raised.  The webhook URL is never printed or logged.
Without ``--post`` nothing is sent regardless of environment.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops.paper_collection_digest import (  # noqa: E402
    FUTURES_ROUTE,
    OPTIONS_ROUTE,
    build_futures_digest,
    build_options_digest,
    chunk_message,
    format_digest,
)

log = logging.getLogger("paper_collection_digest")


def _is_nyse_session(day: date) -> bool:
    """Reuse the options session calendar; on any error assume it is a session."""
    try:
        from alert_ranker.session_calendar import nyse_session_for

        return nyse_session_for(day) is not None
    except Exception:  # noqa: BLE001 — never block a report on calendar trouble
        return True


def post(route: str, text: str, *, router=None) -> bool:
    """Send through the router; returns True only if every chunk was delivered."""
    if router is None:
        from notifications.discord_router import DiscordRouter

        router = DiscordRouter()
    if not router.is_enabled(route):
        print(f"[paper_collection_digest] route '{route}' not configured; printed only")
        return False
    delivered = all(router.send(route, chunk) for chunk in chunk_message(text))
    print(f"[paper_collection_digest] posted to route '{route}': {delivered}")
    return delivered


def main(argv: Optional[Sequence[str]] = None, *, router=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--domain", choices=("futures", "options"), required=True)
    parser.add_argument("--period", choices=("daily", "weekly"), default="daily")
    parser.add_argument("--date", type=date.fromisoformat, help="reference date (UTC); default today")
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument("--json", action="store_true", help="print the machine-readable digest")
    parser.add_argument("--post", action="store_true", help="deliver via the optional Discord route")
    parser.add_argument("--skip-non-session", action="store_true",
                        help="options daily only: exit quietly when the date is not an NYSE session")
    parser.add_argument("--scanner-db", type=Path, help="options: V1 scanner sqlite (default LOG_DIR/options_scanner.sqlite)")
    parser.add_argument("--coverage-dir", type=Path, help="options: coverage collector data dir (default LOG_DIR/coverage_collector)")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    ref = args.date or now.date()
    if args.domain == "options" and args.period == "daily" and args.skip_non_session and not _is_nyse_session(ref):
        print(f"[paper_collection_digest] {ref} is not an NYSE session; nothing to report")
        return 0
    try:
        if args.domain == "futures":
            digest = build_futures_digest(args.log_dir, period=args.period, ref_date=ref, now=now)
            route = FUTURES_ROUTE
        else:
            digest = build_options_digest(
                args.log_dir, period=args.period, ref_date=ref, now=now,
                scanner_db=args.scanner_db, coverage_dir=args.coverage_dir,
            )
            route = OPTIONS_ROUTE
    except Exception as exc:  # noqa: BLE001 — a reporting failure is never fatal to anything else
        log.exception("digest build failed")
        print(f"[paper_collection_digest] FAILED: {type(exc).__name__}: {exc}")
        return 1
    text = format_digest(digest)
    if args.json:
        print(json.dumps(digest, indent=2, default=str))
    else:
        print(text)
    if args.post:
        post(route, text, router=router)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raise SystemExit(main())
