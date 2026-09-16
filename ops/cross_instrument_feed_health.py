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


def _load_state(log_dir: str | Path) -> dict:
    path = Path(log_dir) / STATE_FILENAME
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

    `transport_ok` means observe_bar completed far enough to persist its epoch-
    scoped seen-bar key. `bar_recorded` independently verifies that exact 15m bar
    exists in BarHistory. Both are required for `proven_15m`.
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
    for instrument in OBSERVATION_UNIVERSE:
        bar_ts = latest.get(instrument)
        last_dt = _parse_dt(bar_ts) if bar_ts else None
        transport_ok = last_dt is not None
        bar_recorded = bool(bar_ts) and _matching_recorded_bar(log_dir, instrument, bar_ts)
        proven = transport_ok and bar_recorded
        age = int((now - last_dt).total_seconds()) if last_dt is not None else None
        session_active = product_session_active(instrument, now)
        is_stale = bool(session_active) and (not proven or age is None or age > stale_after_seconds)
        if not proven:
            missing.append(instrument)
        if is_stale:
            stale.append(instrument)
        if not transport_ok:
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
            "session_active": session_active,
            "product": product_of(instrument),
            "collection_only": instrument in {"M2K", "MGC", "MCL", "MBT"},
        }

    return {
        "campaign_enabled": campaign_enabled(),
        "evidence_epoch": active_epoch,
        "authority": "campaign_seen_bar_plus_matching_15m_bar_history",
        "receipt_freshness_is_not_authoritative": True,
        "instruments": instruments,
        "unproven_instruments": missing,
        "stale_active_instruments": stale,
        "all_instruments_proven_once": not missing and active_epoch is not None,
        "active_instruments_healthy": not stale,
        "ready_to_trust_collection_feed": bool(active_epoch) and not missing and not stale,
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
                f"transport_ok={row['transport_ok']} bar_recorded={row['bar_recorded']}"
            )
    return 0 if report["ready_to_trust_collection_feed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
