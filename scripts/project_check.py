#!/usr/bin/env python3
"""Thin CLI wrapper for ops.project_check -- see that module for the routines.

    python scripts/project_check.py session-start
    python scripts/project_check.py precommit
    python scripts/project_check.py promotion --strategy <name> --candles <path> [...]
    python scripts/project_check.py daily
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ops.project_check import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
