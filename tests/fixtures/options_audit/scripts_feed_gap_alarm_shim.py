#!/usr/bin/env python3
"""Repo entry point for the 15m feed-gap alarm (see ops/feed_gap_alarm.py).

The box runs the self-contained ops/ file directly (byte-copied to
/root/afs-shared/feed_gap_alarm.py); this wrapper exists for running the same
code from a checkout.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops.feed_gap_alarm import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
