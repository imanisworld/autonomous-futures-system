#!/usr/bin/env python3
"""Repo entry point for the Strategy Promotion Proof Gate (see ops/promotion_gate.py).

Read-only aggregator/classifier over already-produced canonical-evidence
artifacts (results.json + raw_trades.jsonl), the journal, and risk_rules.yaml
/ config.settings -- it does NOT invoke ReplayEngine or any strategy-replay
machinery itself. This wrapper is only argument parsing and printing; all
logic lives in ops/promotion_gate.py.

Usage:
    python3 scripts/promotion_gate.py --strategy orb_breakout
    python3 scripts/promotion_gate.py --strategy vwap_reclaim --json
    python3 scripts/promotion_gate.py --strategy strat_212 \\
        --results scripts/strat_212_122_canonical_evidence_results.json \\
        --raw-trades scripts/strat_212_122_canonical_evidence_raw_trades.jsonl
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.promotion_gate import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
