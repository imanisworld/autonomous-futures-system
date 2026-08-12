#!/usr/bin/env python3
"""CLI entry point for ops.project_check (session-start / precommit / promotion / daily)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.project_check import main

if __name__ == "__main__":
    raise SystemExit(main())
