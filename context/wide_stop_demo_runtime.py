"""Public Tradovate-DEMO entrypoint for the 4HR / 3-2-2 evidence layer.

The implementation lives in ``wide_stop_demo_runtime_core``. This wrapper is a
hard storage boundary: every demo state file, candidate audit, daily-risk state,
and outcome journal is rooted under ``tradovate_demo_evidence/`` before the core
is called. PaperBroker evidence from #545 therefore cannot be read, mutated, or
blended into demo performance/risk accounting.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from context.track_b_daily_heartbeat import seal_session

logger = logging.getLogger(__name__)

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


def run_demo_eod_fallback(*, cfg, log_dir: str | Path, now=None, broker_factory=None):
    """Run the unchanged DEMO exit fallback; seal shared paper evidence after close."""
    current = now or datetime.now(timezone.utc)
    result = _core.run_demo_eod_fallback(
        cfg=cfg,
        log_dir=isolated_log_dir(log_dir),
        now=now,
        broker_factory=broker_factory,
    )
    if result.get("reason") != "BEFORE_EOD_CLOSE":
        # Exit-only DEMO behavior is unchanged; this adds no entry path and
        # does not read DEMO P&L. Missing paper bars seal as INCOMPLETE.
        try:
            seal_session(
                log_dir, now=current, fallback_action=str(result.get("action") or "UNKNOWN")
            )
        except (OSError, ValueError) as exc:
            logger.error("Track B EOD heartbeat seal failed: %s", exc)
            # The independent forward daily proof will flag the absent row.
    return result
