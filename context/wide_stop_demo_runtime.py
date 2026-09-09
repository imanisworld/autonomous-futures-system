"""Public Tradovate-DEMO entrypoint for the 4HR / 3-2-2 evidence layer.

The implementation lives in ``wide_stop_demo_runtime_core``. This wrapper is a
hard storage boundary: every demo state file, candidate audit, daily-risk state,
and outcome journal is rooted under ``tradovate_demo_evidence/`` before the core
is called. PaperBroker evidence from #545 therefore cannot be read, mutated, or
blended into demo performance/risk accounting.
"""
from __future__ import annotations

from pathlib import Path

from context import wide_stop_demo_runtime_core as _core

DEMO_LOG_SUBDIR = "tradovate_demo_evidence"

# Re-export narrow helpers used by safety regression tests. Production routing
# enters only through process_demo_five_min_bar below.
_pending_reconcile = _core._pending_reconcile
_eod_exclusive_gate = _core._eod_exclusive_gate
_broker_factory = _core._broker_factory


def isolated_log_dir(log_dir: str | Path) -> Path:
    return Path(log_dir) / DEMO_LOG_SUBDIR


def process_demo_five_min_bar(
    *,
    payload,
    cfg,
    bars_5m: list[dict],
    log_dir: str | Path,
    for_date=None,
    broker_factory=None,
):
    """Run the demo core against a storage root isolated from paper evidence."""
    return _core.process_demo_five_min_bar(
        payload=payload,
        cfg=cfg,
        bars_5m=bars_5m,
        log_dir=isolated_log_dir(log_dir),
        for_date=for_date,
        broker_factory=broker_factory,
    )
