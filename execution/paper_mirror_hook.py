"""
execution/paper_mirror_hook.py

Fire-and-forget bridge from PaperBroker fills to the Webull sandbox futures
MIRROR lane (execution/webull_sandbox_futures_mirror.py).

Contract:
- OFF unless ``WEBULL_FUTURES_MIRROR_ENABLED`` is true (the mirror module
  re-checks the full paper-only config before any network call).
- Never raises, never blocks: the mirror call runs on a daemon thread with a
  hard timeout; PaperBroker returns exactly what it always returned.
- Never feeds back: results are appended to ``<log_dir>/webull_mirror_<date>.jsonl``
  for the operator's eyes only. Nothing reads that file.
- Cheap when off: one env read per call, no imports of the mirror module.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from execution.broker_interface import BracketOrder, Fill

logger = logging.getLogger(__name__)

MIRROR_ENV_FLAG = "WEBULL_FUTURES_MIRROR_ENABLED"
MIRROR_LOG_DIR_ENV = "WEBULL_FUTURES_MIRROR_LOG_DIR"
_TRUE = {"1", "true", "yes", "on"}
_EXITED_RESULTS = {"WIN", "LOSS", "BREAKEVEN"}

# Test seam: when set, replaces the thread dispatch (called synchronously).
_dispatch_override: Optional[Callable[[Callable[[], None]], None]] = None


def mirror_enabled(env: Optional[dict[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get(MIRROR_ENV_FLAG, "") or "").strip().lower() in _TRUE


def _log_dir() -> Path:
    return Path(os.environ.get(MIRROR_LOG_DIR_ENV) or os.environ.get("AFS_LOG_DIR") or "logs")


def _record(row: dict[str, Any]) -> None:
    try:
        d = _log_dir()
        d.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with (d / f"webull_mirror_{day}.jsonl").open("a") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except Exception as exc:  # never affect trading
        logger.debug("mirror record skipped: %s", exc)


def _dispatch(fn: Callable[[], None]) -> None:
    if _dispatch_override is not None:
        _dispatch_override(fn)
        return
    threading.Thread(target=fn, name="webull-mirror", daemon=True).start()


def _result_row(result: Any) -> dict[str, Any]:
    keys = ("status", "leg", "client_order_id", "broker_order_id", "symbol", "side",
            "quantity", "order_type", "limit_price", "reason")
    return {k: getattr(result, k, None) for k in keys}


def after_entry(order: BracketOrder, fill: Fill, *, lane: str = "paper") -> None:
    """Mirror a paper ENTRY that established a position (fill.result == OPEN)."""
    if not mirror_enabled() or fill is None or str(fill.result).upper() != "OPEN":
        return
    source_id = fill.paper_order_id or order.client_order_id
    ts = datetime.now(timezone.utc).isoformat()

    def run() -> None:
        try:
            from execution.webull_sandbox_futures_mirror import mirror_entry

            res = mirror_entry(order, source_id=source_id)
            _record({"ts": ts, "lane": lane, "event": "entry", "source_id": source_id,
                     "instrument": order.instrument, "direction": order.direction,
                     "entry": order.entry, "contracts": order.contracts,
                     "actual_entry": fill.entry_price, **_result_row(res)})
        except Exception as exc:
            _record({"ts": ts, "lane": lane, "event": "entry", "source_id": source_id,
                     "status": "ERROR", "reason": f"hook:{type(exc).__name__}"})

    _dispatch(run)


def after_exit(fill: Optional[Fill], *, lane: str = "paper") -> None:
    """Mirror a paper EXIT (a resolved fill with an exit price)."""
    if not mirror_enabled() or fill is None:
        return
    if fill.exit_price is None or str(fill.result).upper() not in _EXITED_RESULTS:
        return
    source_id = fill.paper_order_id
    ts = datetime.now(timezone.utc).isoformat()

    def run() -> None:
        try:
            from execution.webull_sandbox_futures_mirror import mirror_exit

            res = mirror_exit(fill, source_id=source_id)
            _record({"ts": ts, "lane": lane, "event": "exit", "source_id": source_id,
                     "instrument": fill.instrument, "direction": fill.direction,
                     "result": fill.result, "exit_reason": fill.exit_reason,
                     "exit_price": fill.exit_price, "pnl_dollars": fill.pnl_dollars,
                     **_result_row(res)})
        except Exception as exc:
            _record({"ts": ts, "lane": lane, "event": "exit", "source_id": source_id,
                     "status": "ERROR", "reason": f"hook:{type(exc).__name__}"})

    _dispatch(run)
