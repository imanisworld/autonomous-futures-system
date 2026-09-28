#!/usr/bin/env python3
"""Read-only weekly surfaces. Four commands, no look, no fetch, no writes.

  python3 scripts/afs_weekly_read.py futures-trigger-counts --log-dir LOG
  python3 scripts/afs_weekly_read.py forward-campaign-counts \\
      [--mnq-corpus-5m DIR --mnq-corpus-15m DIR] [--mgc-counts FILE]
  python3 scripts/afs_weekly_read.py options-reclaim-counts --db SNAPSHOT
  python3 scripts/afs_weekly_read.py feed-15m-status --log-dir LOG

Not an SSH allowlist change and not a deployment. The 2-1-2 target-geometry
experiment is outside this command.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.weekly_read_surfaces import (  # noqa: E402
    SurfaceError,
    feed_15m_status,
    forward_campaign_counts,
    futures_trigger_counts,
    options_reclaim_counts,
)


def _as_of(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise SurfaceError("--as-of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _build() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    triggers = sub.add_parser("futures-trigger-counts")
    triggers.add_argument("--log-dir", type=Path, required=True)

    forward = sub.add_parser("forward-campaign-counts")
    forward.add_argument("--mnq-corpus-5m", type=Path)
    forward.add_argument("--mnq-corpus-15m", type=Path)
    forward.add_argument("--mgc-counts", type=Path)
    forward.add_argument("--as-of")

    reclaim = sub.add_parser("options-reclaim-counts")
    reclaim.add_argument("--db", type=Path, required=True)
    reclaim.add_argument("--as-of")

    feed = sub.add_parser("feed-15m-status")
    feed.add_argument("--log-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == "options-reclaim-counts" and any(
        token in {"look", "--confirm-single-look"} for token in tokens[1:]
    ):
        print("REFUSED: options-reclaim-counts cannot take look", file=sys.stderr)
        return 2
    args = _build().parse_args(tokens)
    try:
        if args.command == "futures-trigger-counts":
            report = futures_trigger_counts(args.log_dir)
        elif args.command == "forward-campaign-counts":
            if bool(args.mnq_corpus_5m) != bool(args.mnq_corpus_15m):
                raise SurfaceError("MNQ counts need both --mnq-corpus-5m and --mnq-corpus-15m")
            report = forward_campaign_counts(
                mnq_corpus_5m=args.mnq_corpus_5m,
                mnq_corpus_15m=args.mnq_corpus_15m,
                mgc_counts=args.mgc_counts,
                as_of=_as_of(args.as_of),
            )
        elif args.command == "options-reclaim-counts":
            report = options_reclaim_counts(args.db, as_of=_as_of(args.as_of))
        elif args.command == "feed-15m-status":
            report = feed_15m_status(args.log_dir)
        else:
            raise SurfaceError(f"unknown command {args.command}")
    except (SurfaceError, AssertionError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
