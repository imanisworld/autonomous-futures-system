#!/usr/bin/env python3
"""Authoritative feed-health proof for cross_instrument_observation_v1.

Unlike latest_webhook_<ROOT>.json receipt freshness, this module proves that the
campaign itself successfully processed a 15-minute bar AND that the exact bar is
present in BarHistory. A fresh 5-minute webhook, an unsupported-timeframe alert,
or a transport failure cannot refresh this proof.

Read-only. Never touches decisions, risk, brokers, positions, or orders.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from context.bar_history import BarHistory, _parse_dt  # noqa: E402
from context.futures_session import feed_stale_after_minutes, product_of, product_session_active  # noqa: E402
from execution.cross_instrument_observation import (  # noqa: E402
    DECISION_TIMEFRAME_MINUTES,
    OBSERVATION_UNIVERSE,
    STATE_FILENAME,
    campaign_enabled,
    evidence_epoch,
    normalize_timeframe_minutes,
)

_TRANSPORT_PREFIX = "cross_instrument_observation_v1_transport_"
_COLLECTION_ONLY = {"M2K", "MGC", "MCL", "MBT"}


def _load_state(log_dir: str | Path) -> dict:
    path = Path(log_dir) / STATE_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _load_transport_status(log_dir: str | Path, instrument: str) -> dict:
    path = Path(log_dir) / f"{_TRANSPORT_PREFIX}{instrument}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _parse_seen_bar(value: object) -> Optional[tuple[str, str, int, str]]:
    """Parse epoch|instrument|timeframe|bar_ts. Legacy/unscoped keys are ignored."""
    if not isinstance(value, str):
        return None
    parts = value.split("|", 3)
    if len(parts) != 4:
        return None
    epoch, instrument, tf_raw, bar_ts = parts
    tf = normalize_timeframe_minutes(tf_raw)
    if not epoch or instrument not in OBSERVATION_UNIVERSE or tf is None or _parse_dt(bar_ts) is None:
        return None
    return epoch, instrument, tf, bar_ts


def _matching_recorded_bar(log_dir: str | Path, instrument: str, bar_ts: str) -> bool:
    target = _parse_dt(bar_ts)
    if target is None:
        return False
    history = BarHistory(log_dir=str(log_dir)).recent(
        instrument,
        800,
        for_date=target.date(),
        lookback_days=2,
    )
    for bar in history:
        if normalize_timeframe_minutes(bar.get("timeframe")) != DECISION_TIMEFRAME_MINUTES:
            continue
        observed = _parse_dt(str(bar.get("ts") or ""))
        if observed is not None and observed == target:
            return True
    return False


def build_feed_health(
    log_dir: str | Path,
    *,
    now: datetime | None = None,
    epoch: str | None = None,
) -> dict:
    """Return per-instrument 15m campaign-processing proof.

    A root is healthy only when its active-epoch campaign seen-bar is backed by
    the exact 15m BarHistory row. For collection-only roots, the latest explicit
    15m transport attempt is also authoritative: a newer failed attempt flips
    health to TRANSPORT_ERROR immediately instead of waiting for staleness.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)
    active_epoch = epoch if epoch is not None else evidence_epoch()
    state = _load_state(log_dir)
    latest: dict[str, str] = {}
    if active_epoch:
        for raw in state.get("seen_bars") or []:
            parsed = _parse_seen_bar(raw)
            if parsed is None:
                continue
            row_epoch, instrument, tf, bar_ts = parsed
            if row_epoch != active_epoch or tf != DECISION_TIMEFRAME_MINUTES:
                continue
            prior = latest.get(instrument)
            prior_dt = _parse_dt(prior) if prior else None
            row_dt = _parse_dt(bar_ts)
            if row_dt is not None and (prior_dt is None or row_dt > prior_dt):
                latest[instrument] = bar_ts

    stale_after_seconds = feed_stale_after_minutes(DECISION_TIMEFRAME_MINUTES) * 60
    instruments: dict[str, dict] = {}
    missing: list[str] = []
    stale: list[str] = []
    transport_errors: list[str] = []
    for instrument in OBSERVATION_UNIVERSE:
        bar_ts = latest.get(instrument)
        last_dt = _parse_dt(bar_ts) if bar_ts else None
        bar_recorded = bool(bar_ts) and _matching_recorded_bar(log_dir, instrument, bar_ts)
        proven = last_dt is not None and bar_recorded

        marker = _load_transport_status(log_dir, instrument) if instrument in _COLLECTION_ONLY else {}
        marker_epoch = marker.get("evidence_epoch")
        marker_tf = normalize_timeframe_minutes(marker.get("timeframe_minutes"))
        marker_dt = _parse_dt(marker.get("bar_ts"))
        marker_applies = bool(
            marker
            and active_epoch
            and marker_epoch == active_epoch
            and marker_tf == DECISION_TIMEFRAME_MINUTES
            and marker_dt is not None
        )
        latest_attempt_ok = None if not marker_applies else bool(marker.get("transport_ok"))
        latest_attempt_recorded = None if not marker_applies else bool(marker.get("bar_recorded"))
        last_error = str(marker.get("last_error")) if marker_applies and marker.get("last_error") else None
        newer_failed_attempt = bool(
            marker_applies
            and latest_attempt_ok is False
            and (last_dt is None or (marker_dt is not None and marker_dt >= last_dt))
        )

        age = int((now - last_dt).total_seconds()) if last_dt is not None else None
        session_active = product_session_active(instrument, now)
        is_stale = bool(session_active) and (not proven or age is None or age > stale_after_seconds)
        if not proven:
            missing.append(instrument)
        if is_stale:
            stale.append(instrument)
        if newer_failed_attempt:
            transport_errors.append(instrument)

        # `transport_ok` is strict for roots with an explicit marker. MNQ/MES do
        # not traverse webhook.observation_transport, so their proof is inferred
        # from successful active-epoch seen-bar persistence + BarHistory.
        transport_ok = (not newer_failed_attempt) and (proven if not marker_applies else bool(latest_attempt_ok))
        if newer_failed_attempt:
            status = "TRANSPORT_ERROR"
        elif last_dt is None:
            status = "UNPROVEN"
        elif not bar_recorded:
            status = "BAR_HISTORY_MISSING"
        elif is_stale:
            status = "STALE"
        elif session_active is False:
            status = "IDLE_SESSION"
        else:
            status = "HEALTHY"
        instruments[instrument] = {
            "status": status,
            "evidence_epoch": active_epoch,
            "required_timeframe_minutes": DECISION_TIMEFRAME_MINUTES,
            "last_successful_15m_bar_ts": last_dt.isoformat() if last_dt else None,
            "age_seconds": age,
            "stale_after_seconds": stale_after_seconds,
            "transport_ok": transport_ok,
            "bar_recorded": bar_recorded,
            "proven_15m": proven,
            "latest_15m_attempt_ts": marker_dt.isoformat() if marker_applies and marker_dt else None,
            "latest_15m_attempt_transport_ok": latest_attempt_ok,
            "latest_15m_attempt_bar_recorded": latest_attempt_recorded,
            "last_error": last_error,
            "session_active": session_active,
            "product": product_of(instrument),
            "collection_only": instrument in _COLLECTION_ONLY,
        }

    all_proven = not missing and active_epoch is not None
    active_healthy = not stale and not transport_errors
    return {
        "campaign_enabled": campaign_enabled(),
        "evidence_epoch": active_epoch,
        "authority": "campaign_seen_bar_plus_matching_15m_bar_history_plus_latest_transport_attempt",
        "receipt_freshness_is_not_authoritative": True,
        "instruments": instruments,
        "unproven_instruments": missing,
        "stale_active_instruments": stale,
        "transport_error_instruments": transport_errors,
        "all_instruments_proven_once": all_proven,
        "active_instruments_healthy": active_healthy,
        "ready_to_trust_collection_feed": bool(active_epoch) and all_proven and active_healthy,
        "note": (
            "This is an evidence-feed gate only. It never grants strategy, risk, broker, or execution eligibility."
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--epoch", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = build_feed_health(args.log_dir, epoch=args.epoch)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(
            f"epoch={report['evidence_epoch']} proven={report['all_instruments_proven_once']} "
            f"healthy={report['active_instruments_healthy']} trust={report['ready_to_trust_collection_feed']}"
        )
        for root, row in report["instruments"].items():
            print(
                f"{root:4s} {row['status']:20s} last15={row['last_successful_15m_bar_ts']} "
                f"transport_ok={row['transport_ok']} bar_recorded={row['bar_recorded']} "
                f"last_error={row['last_error']}"
            )
    return 0 if report["ready_to_trust_collection_feed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
