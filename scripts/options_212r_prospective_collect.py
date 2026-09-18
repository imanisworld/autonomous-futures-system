#!/usr/bin/env python3
"""Collect prospective 212R trigger + option-selector evidence, observation only.

The lane is deliberately isolated from the options scanner journal and from all
risk/broker/order paths. It observes Public chart bars, proves a 2-1-2 setup
was ARMED before the trigger bucket, resolves the exact strict-through crossing
from Alpaca SIP trades using the same semantics as the frozen historical audit,
and captures existing OPTIONS_PAPER_V1 selector inputs only for timely,
pre-armed reversal events.

No alert is sent and no trade/risk state is created.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
import hashlib
from datetime import date, datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, build_session_timeframe  # noqa: E402
from alert_ranker.config import load_config, resolve_alpaca_credentials  # noqa: E402
from alert_ranker.market_data import PublicMarketDataClient  # noqa: E402
from alert_ranker.options_212r_prospective import (  # noqa: E402
    evaluate_capture_gate,
    observe_212_setups,
    source_reward_to_risk,
)
from alert_ranker.options_selector_evidence import (  # noqa: E402
    blocked_selector_evidence,
    build_selector_evidence_capture,
    finalize_selector_evidence,
)
from alert_ranker.paper_v1 import choose_contract, choose_expiration  # noqa: E402
from alert_ranker.public_chart_bars import PUBLIC_CHART_SOURCE, parse_regular_market_bars  # noqa: E402
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from alert_ranker.trigger_geometry import geometry_for_trigger  # noqa: E402
from alert_ranker.trigger_time import (  # noqa: E402
    ArmedStratTrigger,
    TriggerResolution,
    _family_for_break,
)
from scripts.options_trigger_trade_timestamp_audit import (  # noqa: E402
    AlpacaTradeProvider,
    canonical_trade_payload,
    first_crossing_trade,
)

PRIMARY_20 = (
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR", "INTC",
    "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
)
HISTORICDATA_PREFIX = "/userapigateway/historicdata"
COLLECTOR_ID = "OPTIONS_212R_PROSPECTIVE_COLLECTOR"
COLLECTOR_VERSION = "212r-collector-v0.3"


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _age_seconds(now: datetime, value: Any) -> float | None:
    parsed = _parse_ts(value)
    return (now - parsed).total_seconds() if parsed else None


def _week_sessions(day: date) -> list[Any]:
    # WEEK history must include the previous regular session so a Friday
    # precursor can re-anchor into Monday's opening 30m watch window.
    start = day - timedelta(days=7)
    out = []
    cursor = start
    while cursor <= day:
        session = nyse_session_for(cursor)
        if session is not None:
            out.append(session)
        cursor += timedelta(days=1)
    return out


async def _public_chart(pub: PublicMarketDataClient, ticker: str, period: str) -> dict[str, Any]:
    if period not in {"DAY", "WEEK"}:
        raise ValueError("unsupported chart period")
    token = await pub._ensure_token()
    if token is None:
        raise RuntimeError(pub.last_error or "public_auth_failed")
    path = f"{HISTORICDATA_PREFIX}/EQUITY/{ticker}/{period}"
    client = pub._ensure_client()
    response = await client.get(
        path,
        headers={"Authorization": f"Bearer {token}"},
        params={"tradingSessionToggle": "REGULAR_HOURS"},
    )
    if not response.is_success:
        raise RuntimeError(f"public_historicdata_http_{response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("public_historicdata_bad_shape")
    return payload


def _load_journal(path: Path) -> tuple[dict[str, datetime], set[str], dict[str, str]]:
    armed: dict[str, datetime] = {}
    terminal: set[str] = set()
    fingerprints: dict[str, str] = {}
    if not path.exists():
        return armed, terminal, fingerprints
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"journal_invalid_json_line_{number}") from exc
        if not isinstance(row, dict) or not row.get("setup_id"):
            raise RuntimeError(f"journal_invalid_row_{number}")
        if row.get("record_type") in {"ARMED", "RESOLUTION", "SOURCE_DRIFT"}:
            if (
                row.get("collector_id") != COLLECTOR_ID
                or row.get("collector_version") != COLLECTOR_VERSION
            ):
                raise RuntimeError(f"journal_collector_version_mismatch_{number}")
        setup_id = str(row["setup_id"])
        observation = row.get("observation") if isinstance(row.get("observation"), dict) else {}
        fingerprint = observation.get("setup_fingerprint")
        if fingerprint is not None:
            fingerprint = str(fingerprint)
            existing = fingerprints.get(setup_id)
            if existing is not None and existing != fingerprint:
                raise RuntimeError(f"journal_setup_fingerprint_drift_{number}")
            fingerprints[setup_id] = fingerprint
        if row.get("record_type") == "ARMED":
            observed = _parse_ts(row.get("observed_at"))
            if observed is None:
                raise RuntimeError(f"journal_invalid_armed_timestamp_{number}")
            if setup_id not in armed or observed < armed[setup_id]:
                armed[setup_id] = observed
        if row.get("record_type") == "RESOLUTION":
            terminal.add(setup_id)
    return armed, terminal, fingerprints


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _blocked_option(reason: str, *, ticker: str, direction: str | None, decision_ts: datetime) -> dict[str, Any]:
    return {
        "status": "DATA_BLOCKED",
        "reason_code": reason,
        "selector_evidence": blocked_selector_evidence(
            ticker=ticker,
            production_direction=direction or "LONG",
            decision_ts=decision_ts.isoformat(),
            reason_code=reason,
        ) if direction in {"LONG", "SHORT"} else None,
    }


def _persist_sip_trade_window(
    directory: Path,
    *,
    setup_id: str,
    payload: bytes,
) -> tuple[str, str]:
    """Persist one immutable canonical SIP trade window and return path + SHA."""
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{setup_id}.jsonl"
    digest = hashlib.sha256(payload).hexdigest()
    if target.exists():
        existing = target.read_bytes()
        if existing != payload:
            raise RuntimeError("sip_trade_window_drift")
        return str(target), digest

    with target.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return str(target), digest


async def _capture_exact_trigger_cross(
    provider: AlpacaTradeProvider | None,
    *,
    observation: Any,
    setup_id: str,
    sip_trade_dir: Path,
    persist_raw: bool,
) -> dict[str, Any]:
    """Resolve the exact SIP trade strictly through the proven trigger boundary."""
    if provider is None:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": "alpaca_sip_credentials_missing",
        }
    trigger_start = _parse_ts(observation.trigger_bar_start)
    if trigger_start is None:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": "trigger_bar_start_missing",
        }
    if observation.direction not in {"LONG", "SHORT"} or observation.trigger_level is None:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": "trigger_boundary_missing",
        }
    trigger_end = trigger_start + MINUTE_5.delta

    try:
        trades = await provider.fetch_trades(
            symbol=str(observation.ticker),
            start=trigger_start,
            end=trigger_end,
        )
        crossing, eligible = first_crossing_trade(
            trades=trades,
            direction=str(observation.direction),
            trigger_level=float(observation.trigger_level),
            window_start=trigger_start,
            window_end=trigger_end,
        )
        if not eligible:
            return {
                "status": "DATA_BLOCKED",
                "reason_code": "sip_price_forming_trades_missing",
            }
        if crossing is None:
            return {
                "status": "DATA_BLOCKED",
                "reason_code": "sip_strict_trigger_cross_missing",
            }

        payload = canonical_trade_payload(trades)
        digest = hashlib.sha256(payload).hexdigest()
        raw_path = None
        if persist_raw:
            raw_path, persisted_digest = _persist_sip_trade_window(
                sip_trade_dir,
                setup_id=setup_id,
                payload=payload,
            )
            if persisted_digest != digest:
                raise RuntimeError("sip_trade_window_hash_mismatch")

        return {
            "status": "PROVEN",
            "reason_code": None,
            "source": "alpaca_sip",
            "query_window_start": trigger_start.isoformat(),
            "query_window_end_exclusive": trigger_end.isoformat(),
            "raw_trade_rows": len(trades),
            "eligible_trade_rows": len(eligible),
            "raw_trade_sha256": digest,
            "raw_trade_file": raw_path,
            "trigger_crossed_at": crossing.timestamp,
            "trigger_cross_trade": crossing.as_dict(),
        }
    except Exception as exc:  # fail closed; never synthesize a crossing clock
        return {
            "status": "DATA_BLOCKED",
            "reason_code": f"sip_cross_error:{type(exc).__name__}",
        }


async def _capture_live_first_boundary(
    provider: AlpacaTradeProvider | None,
    *,
    observation: Any,
    observed_until: datetime,
    setup_id: str,
    sip_trade_dir: Path,
    persist_raw: bool,
) -> dict[str, Any]:
    """Resolve the first SIP break of either frozen 212 boundary while WATCHING."""
    if provider is None:
        return {
            "status": "DATA_BLOCKED",
            "reason_code": "alpaca_sip_credentials_missing",
        }

    watch_start = _parse_ts(observation.watch_start)
    watch_until = _parse_ts(observation.watch_until)
    if watch_start is None or watch_until is None:
        return {"status": "DATA_BLOCKED", "reason_code": "watch_window_missing"}
    if observed_until.tzinfo is None or observed_until.utcoffset() is None:
        raise ValueError("observed_until must be timezone-aware")
    end = min(observed_until.astimezone(timezone.utc), watch_until)
    if end <= watch_start:
        return {"status": "WATCHING", "reason_code": "watch_not_started"}

    try:
        trades = await provider.fetch_trades(
            symbol=str(observation.ticker),
            start=watch_start,
            end=end,
        )
        high_cross, eligible = first_crossing_trade(
            trades=trades,
            direction="LONG",
            trigger_level=float(observation.boundary_high),
            window_start=watch_start,
            window_end=end,
        )
        low_cross, eligible_low = first_crossing_trade(
            trades=trades,
            direction="SHORT",
            trigger_level=float(observation.boundary_low),
            window_start=watch_start,
            window_end=end,
        )
        if len(eligible) != len(eligible_low):
            raise RuntimeError("sip_eligibility_pass_mismatch")

        candidates = [
            ("HIGH", "LONG", high_cross),
            ("LOW", "SHORT", low_cross),
        ]
        candidates = [item for item in candidates if item[2] is not None]
        if not candidates:
            return {
                "status": "WATCHING",
                "reason_code": "no_sip_boundary_break_yet",
                "raw_trade_rows": len(trades),
                "eligible_trade_rows": len(eligible),
            }

        candidates.sort(key=lambda item: (item[2].timestamp_ns, item[0]))
        if (
            len(candidates) > 1
            and candidates[0][2].timestamp_ns == candidates[1][2].timestamp_ns
        ):
            return {
                "status": "DATA_BLOCKED",
                "reason_code": "sip_simultaneous_boundary_break",
            }

        side, direction, crossing = candidates[0]
        payload = canonical_trade_payload(trades)
        digest = hashlib.sha256(payload).hexdigest()
        raw_path = None
        if persist_raw:
            raw_path, persisted_digest = _persist_sip_trade_window(
                sip_trade_dir,
                setup_id=setup_id,
                payload=payload,
            )
            if persisted_digest != digest:
                raise RuntimeError("sip_trade_window_hash_mismatch")

        return {
            "status": "PROVEN",
            "reason_code": None,
            "source": "alpaca_sip",
            "query_window_start": watch_start.isoformat(),
            "query_window_end_exclusive": end.isoformat(),
            "raw_trade_rows": len(trades),
            "eligible_trade_rows": len(eligible),
            "raw_trade_sha256": digest,
            "raw_trade_file": raw_path,
            "break_side": side,
            "direction": direction,
            "trigger_crossed_at": crossing.timestamp,
            "trigger_cross_trade": crossing.as_dict(),
        }
    except Exception as exc:  # fail closed; never infer a boundary ordering
        return {
            "status": "DATA_BLOCKED",
            "reason_code": f"sip_live_cross_error:{type(exc).__name__}",
        }


def _live_observation_from_cross(
    observation: Any,
    *,
    cross_evidence: Mapping[str, Any],
    history_30m: list[Any],
) -> Any:
    """Convert one proven SIP first break into the same 212 family/geometry model."""
    crossed = _parse_ts(cross_evidence.get("trigger_crossed_at"))
    watch_start = _parse_ts(observation.watch_start)
    watch_until = _parse_ts(observation.watch_until)
    if crossed is None or watch_start is None or watch_until is None:
        raise ValueError("live crossing timestamps are incomplete")

    side = str(cross_evidence.get("break_side") or "")
    if side not in {"HIGH", "LOW"}:
        raise ValueError("live crossing break side is invalid")

    armed = ArmedStratTrigger(
        pattern="212",
        armed_at=watch_start,
        watch_until=watch_until,
        boundary_high=float(observation.boundary_high),
        boundary_low=float(observation.boundary_low),
        reference_direction=observation.reference_direction,
        source_timeframe=observation.source_timeframe,
    )
    family, subtype, direction = _family_for_break(armed, side)
    if family is None or direction not in {"LONG", "SHORT"}:
        raise ValueError("live 212 family resolution failed")
    if direction != cross_evidence.get("direction"):
        raise ValueError("live crossing direction mismatch")

    trigger_level = (
        float(observation.boundary_high)
        if direction == "LONG"
        else float(observation.boundary_low)
    )
    invalidation = (
        float(observation.boundary_low)
        if direction == "LONG"
        else float(observation.boundary_high)
    )

    completed = sorted(
        (
            bar
            for bar in history_30m
            if bar.start_utc + MINUTE_30.delta <= watch_start
        ),
        key=lambda bar: bar.start_utc,
    )
    if len(completed) < 2:
        raise ValueError("live 212 parent bar missing")
    parent_bar = completed[-2]

    elapsed = (crossed - watch_start).total_seconds()
    if elapsed < 0 or crossed >= watch_until:
        raise ValueError("live crossing outside watch window")
    bucket_index = int(elapsed // MINUTE_5.delta.total_seconds())
    trigger_bucket_start = watch_start + bucket_index * MINUTE_5.delta

    result = TriggerResolution(
        status="TRIGGERED",
        pattern="212",
        family=family,
        subtype=subtype,
        direction=direction,
        break_side=side,
        trigger_level=trigger_level,
        invalidation_level=invalidation,
        trigger_bar_start=trigger_bucket_start,
        trigger_bar_timeframe=MINUTE_5.name,
        final_scenario="LIVE_FIRST_BREAK_PENDING",
        opposite_side_broken_later=False,
        reason_code="live_sip_first_boundary_break",
    )
    geometry = geometry_for_trigger(
        armed=armed,
        result=result,
        parent_bar=parent_bar,
    )

    target = geometry.target
    target_r = None
    consumed = False
    if family == "STRAT_212_REVERSAL" and target is not None:
        target_r, consumed = source_reward_to_risk(
            direction,
            trigger_level,
            invalidation,
            float(target),
        )

    return replace(
        observation,
        status="TRIGGERED",
        family=family,
        subtype=subtype,
        direction=direction,
        trigger_bar_start=trigger_bucket_start.isoformat(),
        trigger_detectable_at=(trigger_bucket_start + MINUTE_5.delta).isoformat(),
        trigger_level=trigger_level,
        invalidation_level=invalidation,
        source_target=target,
        source_target_r=target_r,
        source_target_consumed=consumed,
        final_scenario="LIVE_FIRST_BREAK_PENDING",
        opposite_side_broken_later=False,
        reason_code="live_sip_first_boundary_break",
    )


async def _capture_selector_evidence(
    pub: PublicMarketDataClient,
    *,
    ticker: str,
    direction: str,
    cfg: Any,
) -> dict[str, Any]:
    """Capture current Public underlying + chain and replay production selector."""
    underlying = await pub.fetch_market_snapshot(ticker)
    if underlying.error or underlying.price is None or underlying.stale:
        now = datetime.now(timezone.utc)
        return _blocked_option(
            f"underlying_snapshot_invalid:{underlying.error or 'missing_or_stale'}",
            ticker=ticker, direction=direction, decision_ts=now,
        )
    expirations = await pub.fetch_option_expirations(ticker)
    now_for_expiry = datetime.now(timezone.utc)
    expiry = choose_expiration(expirations, now_for_expiry)
    if not expiry.valid or expiry.expiry is None:
        return _blocked_option(
            f"expiration_invalid:{expiry.reason}", ticker=ticker,
            direction=direction, decision_ts=now_for_expiry,
        )
    chain = await pub.fetch_option_chain(ticker, expiry.expiry.expiration)
    decision_ts = datetime.now(timezone.utc)
    if chain.error:
        return _blocked_option(
            f"chain_invalid:{chain.error}", ticker=ticker,
            direction=direction, decision_ts=decision_ts,
        )

    try:
        evidence = build_selector_evidence_capture(
            ticker=ticker,
            production_direction=direction,
            expirations=expirations,
            chosen_expiration=expiry.expiry.expiration,
            chain=chain,
            decision_ts=decision_ts.isoformat(),
            underlying_price=float(underlying.price),
            underlying_snapshot={
                "provider": (underlying.raw or {}).get("provider") if isinstance(underlying.raw, dict) else None,
                "price": underlying.price,
                "quote_timestamp": underlying.quote_timestamp,
                "stale": underlying.stale,
                "raw": underlying.raw or {},
            },
        )
    except Exception as exc:  # fail closed; no synthetic evidence
        return _blocked_option(
            f"selector_capture_invalid:{type(exc).__name__}", ticker=ticker,
            direction=direction, decision_ts=decision_ts,
        )

    side = "CALL" if direction == "LONG" else "PUT"
    contracts = chain.calls if side == "CALL" else chain.puts
    selection = choose_contract(contracts, option_type=side, underlying_price=underlying.price)
    evidence = finalize_selector_evidence(
        evidence,
        production_selection={
            "status": selection.status,
            "reason": selection.reason,
            "contract": asdict(selection.contract) if selection.contract is not None else None,
        },
    )
    if evidence.get("status") != "CAPTURED" or evidence.get("production_replay_parity") is not True:
        return {"status": "DATA_BLOCKED", "reason_code": evidence.get("reason_code") or "production_replay_not_proven", "selector_evidence": evidence}
    if not selection.valid or selection.contract is None:
        return {"status": "DATA_BLOCKED", "reason_code": selection.reason or "no_contract", "selector_evidence": evidence}

    # Evidence usability is stricter than the production selector: executable
    # option quote time and underlying quote time must both be causal/fresh.
    quote_ts = _parse_ts(selection.contract.quote_timestamp)
    under_ts = _parse_ts(underlying.quote_timestamp)
    if quote_ts is None or under_ts is None:
        return {"status": "DATA_BLOCKED", "reason_code": "missing_quote_timestamp", "selector_evidence": evidence}
    option_age = (decision_ts - quote_ts).total_seconds()
    underlying_age = (decision_ts - under_ts).total_seconds()
    freshness = float(cfg.public_stale_quote_seconds)
    if option_age < 0 or underlying_age < 0:
        return {"status": "DATA_BLOCKED", "reason_code": "future_quote_timestamp", "selector_evidence": evidence}
    if option_age > freshness or underlying_age > freshness:
        return {"status": "DATA_BLOCKED", "reason_code": "stale_quote_timestamp", "selector_evidence": evidence}

    return {
        "status": "CAPTURED",
        "reason_code": None,
        "captured_at": decision_ts.isoformat(),
        "underlying_quote_age_seconds": round(underlying_age, 3),
        "option_quote_age_seconds": round(option_age, 3),
        "selected_contract": selection.contract.symbol,
        "selector_evidence": evidence,
    }


def _enforce_final_capture_lag(
    option_evidence: Mapping[str, Any],
    *,
    observation: Any,
    prearmed_at: datetime | None,
    max_capture_lag_seconds: float,
    trigger_crossed_at: datetime,
) -> dict[str, Any]:
    """Fail closed if selector evidence finishes outside the pre-registered window."""
    result = dict(option_evidence)
    if result.get("status") != "CAPTURED":
        return result
    captured_at = _parse_ts(result.get("captured_at"))
    if captured_at is None:
        result["status"] = "DATA_BLOCKED"
        result["reason_code"] = "selector_capture_timestamp_missing"
        return result
    gate = evaluate_capture_gate(
        observation,
        prearmed_at=prearmed_at,
        decision_ts=captured_at,
        max_capture_lag_seconds=max_capture_lag_seconds,
        trigger_crossed_at=trigger_crossed_at,
    )
    result["capture_lag_seconds"] = gate.lag_seconds
    if not gate.eligible:
        result["status"] = "DATA_BLOCKED"
        result["reason_code"] = f"post_selector_{gate.reason_code or 'capture_gate_blocked'}"
    return result


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    cfg = load_config()
    alpaca_key, alpaca_secret = resolve_alpaca_credentials()
    sip_provider = (
        AlpacaTradeProvider(
            base_url=cfg.alpaca_data_base_url,
            api_key=alpaca_key,
            secret_key=alpaca_secret,
        )
        if alpaca_key and alpaca_secret
        else None
    )
    sip_trade_dir = Path(args.sip_trade_dir)
    run_started_at = datetime.now(timezone.utc)
    session = nyse_session_for(run_started_at.date())
    if session is None:
        raise RuntimeError("not_a_nyse_session")
    if args.max_capture_lag_seconds <= 0:
        raise ValueError("max_capture_lag_seconds must be positive")
    tickers = tuple(dict.fromkeys(item.upper() for item in args.ticker))
    journal = Path(args.journal)
    armed_seen, terminal_seen, fingerprint_seen = _load_journal(journal)

    summary = {
        "collector_id": COLLECTOR_ID,
        "collector_version": COLLECTOR_VERSION,
        "captured_at": run_started_at.isoformat(),
        "source": PUBLIC_CHART_SOURCE,
        "max_capture_lag_seconds": args.max_capture_lag_seconds,
        "tickers": list(tickers),
        "armed_written": 0,
        "resolutions_written": 0,
        "reversal_triggers": 0,
        "sip_trigger_cross_proven": 0,
        "sip_trigger_cross_blocked": 0,
        "option_evidence_captured": 0,
        "option_evidence_blocked": 0,
        "data_blocked": 0,
        "claims_not_made": ["strategy profitability", "trade authorization", "DEMO eligibility"],
    }

    week_sessions = _week_sessions(session.date)
    async with PublicMarketDataClient(cfg) as pub:
        for ticker in tickers:
            try:
                day_payload, week_payload = await asyncio.gather(
                    _public_chart(pub, ticker, "DAY"),
                    _public_chart(pub, ticker, "WEEK"),
                )
                source_observed_at = datetime.now(timezone.utc)
                current5 = parse_regular_market_bars(
                    day_payload, timeframe=MINUTE_5, decision_ts=source_observed_at, session=session
                ).bars
                current30 = build_session_timeframe(current5, MINUTE_5, MINUTE_30, session.open)
                history30 = []
                for prior in week_sessions:
                    if prior.date == session.date:
                        continue
                    parsed = parse_regular_market_bars(
                        week_payload, timeframe=MINUTE_30, decision_ts=source_observed_at, session=prior
                    )
                    history30.extend(parsed.bars)
                history30.extend(current30)
                history30.sort(key=lambda bar: bar.start_utc)
                observations = observe_212_setups(
                    ticker=ticker,
                    history_30m=history30,
                    session_5m=current5,
                    session=session,
                    decision_ts=source_observed_at,
                )
            except Exception as exc:
                summary["data_blocked"] += 1
                error_at = datetime.now(timezone.utc)
                if not args.dry_run:
                    _append(journal, {
                        "record_type": "COLLECTOR_ERROR", "setup_id": f"{ticker}|{session.date.isoformat()}|collector",
                        "ticker": ticker, "observed_at": error_at.isoformat(),
                        "reason_code": f"source_error:{type(exc).__name__}",
                    })
                continue

            for obs in observations:
                row = obs.to_dict()
                setup_id = obs.setup_id
                trigger_cross_evidence: dict[str, Any] | None = None
                prior_fingerprint = fingerprint_seen.get(setup_id)
                if prior_fingerprint is not None and prior_fingerprint != obs.setup_fingerprint:
                    summary["data_blocked"] += 1
                    if not args.dry_run:
                        _append(journal, {
                            "record_type": "SOURCE_DRIFT",
                            "observed_at": source_observed_at.isoformat(),
                            "collector_id": COLLECTOR_ID,
                            "collector_version": COLLECTOR_VERSION,
                            "setup_id": setup_id,
                            "previous_setup_fingerprint": prior_fingerprint,
                            "current_setup_fingerprint": obs.setup_fingerprint,
                            "observation": row,
                            "reason_code": "public_completed_bar_revision",
                        })
                    continue
                if setup_id in terminal_seen:
                    continue

                if obs.status == "WATCHING":
                    if setup_id not in armed_seen:
                        record = {
                            "record_type": "ARMED",
                            "observed_at": source_observed_at.isoformat(),
                            "collector_id": COLLECTOR_ID,
                            "collector_version": COLLECTOR_VERSION,
                            "setup_id": setup_id,
                            "observation": row,
                        }
                        if not args.dry_run:
                            _append(journal, record)
                        armed_seen[setup_id] = source_observed_at
                        fingerprint_seen[setup_id] = obs.setup_fingerprint
                        summary["armed_written"] += 1

                if obs.status in {"WATCHING", "AMBIGUOUS"}:
                    trigger_cross_evidence = await _capture_live_first_boundary(
                        sip_provider,
                        observation=obs,
                        observed_until=source_observed_at,
                        setup_id=setup_id,
                        sip_trade_dir=sip_trade_dir,
                        persist_raw=not args.dry_run,
                    )
                    if trigger_cross_evidence.get("status") == "WATCHING":
                        continue
                    if trigger_cross_evidence.get("status") != "PROVEN":
                        summary["data_blocked"] += 1
                        summary["sip_trigger_cross_blocked"] += 1
                        continue
                    try:
                        obs = _live_observation_from_cross(
                            obs,
                            cross_evidence=trigger_cross_evidence,
                            history_30m=history30,
                        )
                    except Exception:
                        summary["data_blocked"] += 1
                        summary["sip_trigger_cross_blocked"] += 1
                        continue
                    row = obs.to_dict()
                    summary["sip_trigger_cross_proven"] += 1

                if obs.status == "DATA_BLOCKED":
                    summary["data_blocked"] += 1
                    # Do not terminalize a provider gap; a later run may repair it.
                    continue

                option_evidence: dict[str, Any] | None = None
                capture_gate_eligible = False
                prearmed_at = armed_seen.get(setup_id)
                if obs.family == "STRAT_212_REVERSAL" and obs.status == "TRIGGERED":
                    summary["reversal_triggers"] += 1
                    gate_checked_at = datetime.now(timezone.utc)

                    if trigger_cross_evidence is None:
                        trigger_cross_evidence = await _capture_exact_trigger_cross(
                            sip_provider,
                            observation=obs,
                            setup_id=setup_id,
                            sip_trade_dir=sip_trade_dir,
                            persist_raw=not args.dry_run,
                        )
                        if trigger_cross_evidence.get("status") == "PROVEN":
                            summary["sip_trigger_cross_proven"] += 1

                    if trigger_cross_evidence.get("status") != "PROVEN":
                        summary["sip_trigger_cross_blocked"] += 1
                        option_evidence = _blocked_option(
                            str(
                                trigger_cross_evidence.get("reason_code")
                                or "sip_trigger_cross_unproven"
                            ),
                            ticker=ticker,
                            direction=obs.direction,
                            decision_ts=gate_checked_at,
                        )
                    else:
                        crossed_at = _parse_ts(
                            trigger_cross_evidence.get("trigger_crossed_at")
                        )
                        if crossed_at is None:
                            summary["sip_trigger_cross_blocked"] += 1
                            option_evidence = _blocked_option(
                                "sip_trigger_cross_timestamp_invalid",
                                ticker=ticker,
                                direction=obs.direction,
                                decision_ts=gate_checked_at,
                            )
                        else:
                            gate = evaluate_capture_gate(
                                obs,
                                prearmed_at=prearmed_at,
                                decision_ts=gate_checked_at,
                                max_capture_lag_seconds=args.max_capture_lag_seconds,
                                trigger_crossed_at=crossed_at,
                            )
                            if not gate.eligible:
                                detail = gate.reason_code or "capture_gate_blocked"
                                if gate.lag_seconds is not None:
                                    detail += f":{round(gate.lag_seconds,3)}"
                                option_evidence = _blocked_option(
                                    detail,
                                    ticker=ticker,
                                    direction=obs.direction,
                                    decision_ts=gate_checked_at,
                                )
                            else:
                                capture_gate_eligible = True
                                option_evidence = await _capture_selector_evidence(
                                    pub,
                                    ticker=ticker,
                                    direction=str(obs.direction),
                                    cfg=cfg,
                                )
                                option_evidence = _enforce_final_capture_lag(
                                    option_evidence,
                                    observation=obs,
                                    prearmed_at=prearmed_at,
                                    max_capture_lag_seconds=args.max_capture_lag_seconds,
                                    trigger_crossed_at=crossed_at,
                                )
                    if option_evidence and option_evidence.get("status") == "CAPTURED":
                        summary["option_evidence_captured"] += 1
                    else:
                        summary["option_evidence_blocked"] += 1

                record = {
                    "record_type": "RESOLUTION",
                    "observed_at": source_observed_at.isoformat(),
                    "collector_id": COLLECTOR_ID,
                    "collector_version": COLLECTOR_VERSION,
                    "setup_id": setup_id,
                    "prearmed_at": prearmed_at.isoformat() if prearmed_at else None,
                    "capture_gate_eligible": capture_gate_eligible,
                    "option_evidence_usable": bool(
                        option_evidence and option_evidence.get("status") == "CAPTURED"
                    ),
                    "market_context": {
                        "status": "DEFERRED_SIP_RECONCILIATION",
                        "reason": "Public chart bars do not expose the VWAP input used by the frozen context formula",
                    },
                    "observation": row,
                    "trigger_cross_evidence": trigger_cross_evidence,
                    "option_evidence": option_evidence,
                }
                if not args.dry_run:
                    _append(journal, record)
                terminal_seen.add(setup_id)
                summary["resolutions_written"] += 1

    summary["completed_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--env-file")
    parser.add_argument("--journal", default="logs/options_212r_prospective.jsonl")
    parser.add_argument(
        "--sip-trade-dir",
        default="logs/options_212r_sip_trades",
        help="Isolated immutable raw SIP trade windows used to prove exact crossings",
    )
    parser.add_argument(
        "--max-capture-lag-seconds",
        type=float,
        required=True,
        help="Pre-registered max seconds from exact SIP trigger crossing to evidence capture",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.ticker:
        args.ticker = list(PRIMARY_20)
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
