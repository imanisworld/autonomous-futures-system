"""Observation-only transport for collection-only futures roots (M2K/MGC/MCL/MBT).

A collection-only alert NEVER enters ``webhook.runner.process_alert``. This
module records the bar, evaluates the pure structural detectors, and writes
``cross_instrument_observation_v1`` evidence. It has no path to
DecisionEngine, RiskEngine, PaperBroker, Tradovate or any order route, and it
reads no trading state (daily trade count, loss lockout, open position), so
trading state can never suppress observation.

Pine advisory brackets (entry/stop/target/signal_*) are stripped before the
market state is built and recorded as ignored: the Pine defaults for
non-MES/MNQ instruments are not proven geometry.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from config.futures_contracts import contract_root
from context.bar_history import BarHistory
from context.five_min_feed import is_five_min, record_five_min
from execution import cross_instrument_observation as cio
from webhook.payload import AlertPayload
from webhook.state_builder import build_market_state

logger = logging.getLogger(__name__)

PINE_ADVISORY_FIELDS = ("entry", "stop", "target", "signal_strategy", "signal_direction")


def strip_pine_advisory(payload: AlertPayload) -> tuple[AlertPayload, Optional[dict]]:
    """Return a copy with every Pine advisory bracket field cleared, plus the
    ignored values (None when the payload carried none)."""
    ignored = {}
    for field in PINE_ADVISORY_FIELDS:
        value = getattr(payload, field, None)
        if value is not None:
            ignored[field] = value
    if not ignored:
        return payload, None
    data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    for field in ignored:
        data[field] = None
    return AlertPayload(**data), ignored


def _result(payload: AlertPayload, root: Optional[str], **extra) -> dict:
    return {
        "timestamp": payload.timestamp,
        "instrument": root or payload.ticker,
        "session": getattr(payload, "session", None),
        "resolution": None,
        "decision": cio.DECISION_OBSERVATION_ONLY,
        "risk": None,
        "fill": None,
        "context": None,
        "regime": None,
        "gex_status": None,
        "signa_status": None,
        "failed_gates": [],
        "confidence_score": None,
        "event_id": getattr(payload, "event_id", None),
        "execution_reachable": False,
        "observation": {"campaign_id": cio.CAMPAIGN_ID, "enabled": cio.campaign_enabled(), **extra},
    }


def observe_collection_only_alert(
    payload: AlertPayload,
    *,
    config=None,
    log_dir: str = "logs",
    for_date: Optional[date] = None,
) -> dict:
    """Handle one alert for a collection-only root. Always returns
    ``decision == "OBSERVATION_ONLY"``; never raises into the caller."""
    root = contract_root(payload.ticker)
    if root is None or not cio.is_collection_only(root):
        return _result(payload, root, skipped="not a collection-only root")

    clean, ignored = strip_pine_advisory(payload)
    try:
        if is_five_min(clean.timeframe):
            record_five_min(clean, log_dir, for_date=for_date)
            return _result(clean, root, lane="5m_feed", bar_recorded=True, pine_advisory_ignored=ignored)

        state = build_market_state(clean)
        bar_hist = BarHistory(log_dir=log_dir)
        bar_hist.record(
            root,
            ts=clean.timestamp,
            open=state.ohlc.open, high=state.ohlc.high, low=state.ohlc.low, close=state.ohlc.close,
            volume=state.volume.current_bar if state.volume else None,
            timeframe=state.ohlc.timeframe,
            for_date=for_date,
        )
        recent_bars = bar_hist.recent(root, 8, for_date=for_date)
        history = bar_hist.recent(root, 500, for_date=for_date, lookback_days=1)
        resolved = cio.resolve_pending(
            log_dir, instrument=root, bars=history,
            current_bar_ts=state.timestamp.isoformat(), for_date=for_date,
        )
        # Pure structural detectors only. include_canonical_observers=False keeps
        # the DecisionEngine-backed VWAP observers out of this route entirely.
        from strategy.shadow_setups import evaluate_shadow_setups

        candidates = [
            c.to_dict() for c in evaluate_shadow_setups(
                state, recent_bars, config, include_canonical_observers=False
            )
        ]
        summary = cio.observe_bar(
            log_dir, state, candidates,
            timeframe=str(clean.timeframe), for_date=for_date, source="observation_transport",
            pine_advisory_ignored=ignored,
        )
        out = _result(clean, root, lane="15m_observation", bar_recorded=True,
                      candidates_evaluated=len(candidates), outcomes_resolved=len(resolved),
                      pine_advisory_ignored=ignored, **summary)
        out["session"] = state.session
        out["context"] = {"instrument": root, "session": state.session, "timestamp": state.timestamp.isoformat(),
                          "close": float(state.ohlc.close), "timeframe": state.ohlc.timeframe}
        return out
    except Exception as exc:  # noqa: BLE001 — observation must never raise into ingestion
        logger.warning("observation transport failed for %s: %s", payload.ticker, exc, exc_info=True)
        return _result(clean, root, error=str(exc), pine_advisory_ignored=ignored)
