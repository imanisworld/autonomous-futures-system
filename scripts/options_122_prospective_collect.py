#!/usr/bin/env python3
"""Prospective causal 1-2-2 evidence collector, observation only.

Public chart data arms fixed 1-2-2 structure. Alpaca IEX provides the
provisional exact first-break clock. Existing OPTIONS_PAPER_V1 selector inputs
are captured only for timely, genuinely pre-armed reversals. Delayed SIP later
reconciles every terminal IEX source outcome. No alert, risk, broker, order,
DEMO, or live-trade path is used.
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, build_session_timeframe
from alert_ranker.config import load_config, resolve_alpaca_credentials
from alert_ranker.market_data import PublicMarketDataClient
from alert_ranker.options_122_prospective import evaluate_capture_gate, observe_122_setups
from alert_ranker.paper_v1 import choose_contract, choose_expiration
from alert_ranker.options_selector_evidence import (
    blocked_selector_evidence,
    build_selector_evidence_capture,
    finalize_selector_evidence,
)
from alert_ranker.public_chart_bars import PUBLIC_CHART_SOURCE, parse_regular_market_bars
from alert_ranker.session_calendar import nyse_session_for
from alert_ranker.trigger_time import ArmedStratTrigger, _family_for_break
from scripts.options_122_iex_provisional_audit import (
    Arm,
    HistoricalTradeProvider,
    _first_boundary,
    reconcile_pair,
)
from scripts.options_212r_prospective_collect import _public_chart, _week_sessions
from scripts.options_trigger_trade_timestamp_audit import canonical_trade_payload

PRIMARY_20 = (
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR", "INTC",
    "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
)
COLLECTOR_ID = "OPTIONS_122_IEX_PROSPECTIVE_COLLECTOR"
COLLECTOR_VERSION = "122-iex-collector-v0.1"
POLICY_EPOCH = "122-IEX-E1"
DEFAULT_CADENCE_SECONDS = 60
DEFAULT_MAX_CAPTURE_LAG_SECONDS = 120
RECONCILE_DELAY_MINUTES = 16


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


def _append(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(row), sort_keys=True, separators=(",", ":"), default=str) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _persist_raw(directory: Path, *, setup_id: str, feed: str, payload: bytes) -> tuple[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{setup_id}.{feed}.jsonl"
    digest = hashlib.sha256(payload).hexdigest()
    if target.exists():
        existing = target.read_bytes()
        if existing != payload:
            raise RuntimeError(f"{feed}_raw_window_drift")
        return str(target), digest
    with target.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    return str(target), digest


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


def _load_state(path: Path):
    armed: dict[str, datetime] = {}
    terminal: dict[str, dict[str, Any]] = {}
    fingerprints: dict[str, str] = {}
    reconciled: set[str] = set()
    if not path.exists():
        return armed, terminal, fingerprints, reconciled
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"journal_invalid_json_line_{number}") from exc
        if not isinstance(row, dict) or not row.get("setup_id"):
            raise RuntimeError(f"journal_invalid_row_{number}")
        if row.get("record_type") in {"ARMED", "RESOLUTION", "SOURCE_DRIFT", "RECONCILIATION"}:
            if row.get("collector_id") != COLLECTOR_ID or row.get("collector_version") != COLLECTOR_VERSION:
                raise RuntimeError(f"journal_collector_version_mismatch_{number}")
        setup_id = str(row["setup_id"])
        obs = row.get("observation") if isinstance(row.get("observation"), dict) else {}
        fp = obs.get("setup_fingerprint")
        if fp is not None:
            fp = str(fp)
            if setup_id in fingerprints and fingerprints[setup_id] != fp:
                raise RuntimeError(f"journal_setup_fingerprint_drift_{number}")
            fingerprints[setup_id] = fp
        if row.get("record_type") == "ARMED":
            ts = _parse_ts(row.get("observed_at"))
            if ts is None:
                raise RuntimeError(f"journal_invalid_armed_timestamp_{number}")
            armed[setup_id] = min(ts, armed.get(setup_id, ts))
        elif row.get("record_type") == "RESOLUTION":
            terminal[setup_id] = row
        elif row.get("record_type") == "RECONCILIATION":
            reconciled.add(setup_id)
    return armed, terminal, fingerprints, reconciled


async def _capture_selector_evidence(pub: PublicMarketDataClient, *, ticker: str, direction: str, cfg: Any) -> dict[str, Any]:
    underlying = await pub.fetch_market_snapshot(ticker)
    if underlying.error or underlying.price is None or underlying.stale:
        return _blocked_option(
            f"underlying_snapshot_invalid:{underlying.error or 'missing_or_stale'}",
            ticker=ticker, direction=direction, decision_ts=datetime.now(timezone.utc),
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
        return _blocked_option(f"chain_invalid:{chain.error}", ticker=ticker, direction=direction, decision_ts=decision_ts)
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
    except Exception as exc:
        return _blocked_option(f"selector_capture_invalid:{type(exc).__name__}", ticker=ticker, direction=direction, decision_ts=decision_ts)
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


def _make_arm(obs: Any) -> Arm:
    watch_start = _parse_ts(obs.watch_start)
    watch_end = _parse_ts(obs.watch_until)
    if watch_start is None or watch_end is None or obs.reference_direction not in {"two_up", "two_down"}:
        raise ValueError("invalid 122 arm")
    return Arm(
        session_date=obs.session_date,
        symbol=obs.ticker,
        watch_start=watch_start,
        watch_end=watch_end,
        boundary_high=float(obs.boundary_high),
        boundary_low=float(obs.boundary_low),
        reference_direction=str(obs.reference_direction),
    )


async def _source_first_boundary(provider: HistoricalTradeProvider | None, *, obs: Any, end: datetime, setup_id: str, raw_dir: Path, feed: str, persist_raw: bool) -> dict[str, Any]:
    if provider is None:
        return {"status": "DATA_BLOCKED", "reason_code": f"alpaca_{feed}_credentials_missing"}
    arm = _make_arm(obs)
    window_end = min(end.astimezone(timezone.utc), arm.watch_end)
    if window_end <= arm.watch_start:
        return {"status": "NO_BREAK", "reason_code": "watch_not_started"}
    try:
        trades = await provider.fetch_trades(symbol=arm.symbol, start=arm.watch_start, end=window_end)
        result = _first_boundary(trades, arm=arm, window_start=arm.watch_start, window_end=window_end)
        payload = canonical_trade_payload(trades)
        digest = hashlib.sha256(payload).hexdigest()
        raw_path = None
        full_window = window_end >= arm.watch_end
        if persist_raw and (result.get("status") == "PROVEN" or full_window):
            raw_path, persisted = _persist_raw(raw_dir, setup_id=setup_id, feed=feed, payload=payload)
            if persisted != digest:
                raise RuntimeError("raw_hash_mismatch")
        return {
            **result,
            "source": f"alpaca_{feed}",
            "query_window_start": arm.watch_start.isoformat(),
            "query_window_end_exclusive": window_end.isoformat(),
            "raw_trade_sha256": digest,
            "raw_trade_file": raw_path,
        }
    except Exception as exc:
        return {"status": "DATA_BLOCKED", "reason_code": f"{feed}_query_or_semantics:{type(exc).__name__}", "detail": str(exc)[:240]}


def _live_observation(obs: Any, source: Mapping[str, Any]) -> Any:
    if source.get("status") != "PROVEN":
        raise ValueError("source must be proven")
    crossed = _parse_ts(source.get("timestamp"))
    watch_start = _parse_ts(obs.watch_start)
    watch_until = _parse_ts(obs.watch_until)
    if crossed is None or watch_start is None or watch_until is None or not (watch_start <= crossed < watch_until):
        raise ValueError("cross outside watch")
    side = str(source.get("break_side") or "")
    if side not in {"HIGH", "LOW"}:
        raise ValueError("invalid break side")
    armed = ArmedStratTrigger(
        pattern="122", armed_at=watch_start, watch_until=watch_until,
        boundary_high=float(obs.boundary_high), boundary_low=float(obs.boundary_low),
        reference_direction=obs.reference_direction, source_timeframe=obs.source_timeframe,
    )
    family, subtype, direction = _family_for_break(armed, side)
    elapsed = (crossed - watch_start).total_seconds()
    bucket_start = watch_start + int(elapsed // MINUTE_5.delta.total_seconds()) * MINUTE_5.delta
    trigger_level = float(obs.boundary_high if side == "HIGH" else obs.boundary_low)
    opposite = float(obs.boundary_low if side == "HIGH" else obs.boundary_high)
    if family is None:
        return replace(
            obs, status="CANCELLED", family=None, subtype=subtype, direction=None,
            trigger_bar_start=bucket_start.isoformat(), trigger_detectable_at=(bucket_start + MINUTE_5.delta).isoformat(),
            trigger_level=trigger_level, structural_opposite_boundary=opposite,
            final_scenario="IEX_FIRST_BREAK", opposite_side_broken_later=False,
            reason_code="iex_same_direction_break_precludes_122_reversal",
        )
    if family != "OTHER:strat_122" or direction != source.get("direction"):
        raise ValueError("122 source/family mismatch")
    return replace(
        obs, status="TRIGGERED", family=family, subtype=subtype, direction=direction,
        trigger_bar_start=bucket_start.isoformat(), trigger_detectable_at=(bucket_start + MINUTE_5.delta).isoformat(),
        trigger_level=trigger_level, structural_opposite_boundary=opposite,
        final_scenario="IEX_FIRST_BREAK", opposite_side_broken_later=False,
        reason_code="iex_first_boundary_reversal",
    )


def _enforce_final_capture_lag(option_evidence: Mapping[str, Any], *, obs: Any, prearmed_at: datetime | None, max_capture_lag_seconds: float, crossed_at: datetime) -> dict[str, Any]:
    result = dict(option_evidence)
    if result.get("status") != "CAPTURED":
        return result
    captured_at = _parse_ts(result.get("captured_at"))
    if captured_at is None:
        return {**result, "status": "DATA_BLOCKED", "reason_code": "selector_capture_timestamp_missing"}
    gate = evaluate_capture_gate(
        obs, prearmed_at=prearmed_at, decision_ts=captured_at,
        max_capture_lag_seconds=max_capture_lag_seconds, trigger_crossed_at=crossed_at,
    )
    result["capture_lag_seconds"] = gate.lag_seconds
    if not gate.eligible:
        result["status"] = "DATA_BLOCKED"
        result["reason_code"] = f"post_selector_{gate.reason_code or 'capture_gate_blocked'}"
    return result


async def _reconcile_pending(*, journal: Path, terminal: Mapping[str, dict[str, Any]], reconciled: set[str], sip_provider: HistoricalTradeProvider | None, raw_dir: Path, now: datetime, dry_run: bool) -> dict[str, int]:
    counts = {"reconciled": 0, "pending": 0, "blocked": 0}
    for setup_id, row in terminal.items():
        if setup_id in reconciled:
            continue
        obs = row.get("observation") or {}
        watch_end = _parse_ts(obs.get("watch_until"))
        if watch_end is None or now < watch_end + timedelta(minutes=RECONCILE_DELAY_MINUTES):
            counts["pending"] += 1
            continue
        try:
            class O: pass
            o = O()
            for key, value in obs.items(): setattr(o, key, value)
            sip = await _source_first_boundary(sip_provider, obs=o, end=watch_end, setup_id=setup_id, raw_dir=raw_dir, feed="sip", persist_raw=not dry_run)
        except Exception as exc:
            sip = {"status": "DATA_BLOCKED", "reason_code": f"sip_reconcile:{type(exc).__name__}"}
        iex = row.get("trigger_source") or {"status": "NO_BREAK"}
        if row.get("source_outcome") == "NO_BREAK":
            iex = {"status": "NO_BREAK"}
        elif row.get("source_outcome") == "CONTINUATION" and iex.get("status") != "PROVEN":
            iex = {"status": "DATA_BLOCKED"}
        try:
            arm = Arm(
                session_date=str(obs["session_date"]), symbol=str(obs["ticker"]),
                watch_start=_parse_ts(obs["watch_start"]), watch_end=_parse_ts(obs["watch_until"]),
                boundary_high=float(obs["boundary_high"]), boundary_low=float(obs["boundary_low"]),
                reference_direction=str(obs["reference_direction"]),
            )
            policy = reconcile_pair(arm=arm, sip=sip, iex=iex)
        except Exception as exc:
            policy = {"provisional_emitted": False, "reconciliation": "DATA_BLOCKED", "reason_code": f"reconcile_policy:{type(exc).__name__}", "latency_seconds": None}
        rec = {
            "record_type": "RECONCILIATION", "observed_at": now.isoformat(),
            "collector_id": COLLECTOR_ID, "collector_version": COLLECTOR_VERSION,
            "policy_epoch": POLICY_EPOCH, "setup_id": setup_id,
            "observation": obs, "iex": iex, "sip": sip, "policy": policy,
        }
        if not dry_run:
            _append(journal, rec)
        if policy.get("reconciliation") == "DATA_BLOCKED": counts["blocked"] += 1
        else: counts["reconciled"] += 1
    return counts


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    if args.max_capture_lag_seconds != DEFAULT_MAX_CAPTURE_LAG_SECONDS:
        raise ValueError(f"122-IEX-E1 requires max_capture_lag_seconds={DEFAULT_MAX_CAPTURE_LAG_SECONDS}")
    cfg = load_config()
    key, secret = resolve_alpaca_credentials()
    iex_provider = HistoricalTradeProvider(cfg.alpaca_data_base_url, key, secret, "iex") if key and secret else None
    sip_provider = HistoricalTradeProvider(cfg.alpaca_data_base_url, key, secret, "sip") if key and secret else None
    journal = Path(args.journal)
    raw_dir = Path(args.raw_trade_dir)
    armed_seen, terminal_seen, fingerprints, reconciled = _load_state(journal)
    started = datetime.now(timezone.utc)
    session = nyse_session_for(started.date())
    tickers = tuple(dict.fromkeys(t.upper() for t in (args.ticker or PRIMARY_20)))
    summary = {
        "collector_id": COLLECTOR_ID, "collector_version": COLLECTOR_VERSION,
        "policy_epoch": POLICY_EPOCH, "started_at": started.isoformat(),
        "cadence_seconds": DEFAULT_CADENCE_SECONDS,
        "max_capture_lag_seconds": DEFAULT_MAX_CAPTURE_LAG_SECONDS,
        "tickers": list(tickers), "armed_written": 0, "resolutions_written": 0,
        "reversal_triggers": 0, "continuation_first": 0, "no_break": 0,
        "option_evidence_captured": 0, "option_evidence_blocked": 0, "data_blocked": 0,
        "claims_not_made": ["strategy profitability", "strategy stop/target", "trade authorization", "DEMO eligibility"],
    }
    if session is None:
        summary["status"] = "CLOSED_SESSION"
        completed = datetime.now(timezone.utc)
        summary["completed_at"] = completed.isoformat()
        summary["cycle_seconds"] = round((completed - started).total_seconds(), 3)
        summary["cycle_over_cadence"] = False
        return summary

    if started < session.open.astimezone(timezone.utc) or started >= session.close.astimezone(timezone.utc):
        recon = await _reconcile_pending(
            journal=journal, terminal=terminal_seen, reconciled=reconciled,
            sip_provider=sip_provider, raw_dir=raw_dir, now=started, dry_run=args.dry_run,
        )
        summary.update({f"sip_{k}": v for k, v in recon.items()})
        summary["status"] = "OFF_RTH_RECONCILIATION_ONLY"
        completed = datetime.now(timezone.utc)
        summary["completed_at"] = completed.isoformat()
        summary["cycle_seconds"] = round((completed - started).total_seconds(), 3)
        summary["cycle_over_cadence"] = False
        return summary

    summary["status"] = "RTH_COLLECTION"
    week_sessions = _week_sessions(session.date)
    async with PublicMarketDataClient(cfg) as pub:
        for ticker in tickers:
            try:
                day_payload, week_payload = await asyncio.gather(_public_chart(pub, ticker, "DAY"), _public_chart(pub, ticker, "WEEK"))
                observed_at = datetime.now(timezone.utc)
                current5 = parse_regular_market_bars(day_payload, timeframe=MINUTE_5, decision_ts=observed_at, session=session).bars
                current30 = build_session_timeframe(current5, MINUTE_5, MINUTE_30, session.open)
                history30 = []
                for prior in week_sessions:
                    if prior.date == session.date: continue
                    history30.extend(parse_regular_market_bars(week_payload, timeframe=MINUTE_30, decision_ts=observed_at, session=prior).bars)
                history30.extend(current30); history30.sort(key=lambda b: b.start_utc)
                observations = observe_122_setups(ticker=ticker, history_30m=history30, session_5m=current5, session=session, decision_ts=observed_at)
            except Exception as exc:
                summary["data_blocked"] += 1
                if not args.dry_run:
                    _append(journal, {"record_type":"COLLECTOR_ERROR","setup_id":f"{ticker}|{session.date.isoformat()}|collector","ticker":ticker,"observed_at":datetime.now(timezone.utc).isoformat(),"reason_code":f"source_error:{type(exc).__name__}"})
                continue

            for obs in observations:
                setup_id = obs.setup_id
                if setup_id in terminal_seen:
                    continue
                if setup_id in fingerprints and fingerprints[setup_id] != obs.setup_fingerprint:
                    summary["data_blocked"] += 1
                    if not args.dry_run:
                        _append(journal,{"record_type":"SOURCE_DRIFT","observed_at":observed_at.isoformat(),"collector_id":COLLECTOR_ID,"collector_version":COLLECTOR_VERSION,"policy_epoch":POLICY_EPOCH,"setup_id":setup_id,"observation":obs.to_dict(),"reason_code":"public_completed_bar_revision"})
                    continue
                if setup_id not in armed_seen:
                    arm_record={"record_type":"ARMED","observed_at":observed_at.isoformat(),"collector_id":COLLECTOR_ID,"collector_version":COLLECTOR_VERSION,"policy_epoch":POLICY_EPOCH,"setup_id":setup_id,"observation":obs.to_dict()}
                    if not args.dry_run: _append(journal,arm_record)
                    armed_seen[setup_id]=observed_at; fingerprints[setup_id]=obs.setup_fingerprint; summary["armed_written"] += 1

                source = await _source_first_boundary(iex_provider, obs=obs, end=observed_at, setup_id=setup_id, raw_dir=raw_dir, feed="iex", persist_raw=not args.dry_run)
                if source.get("status") == "DATA_BLOCKED":
                    summary["data_blocked"] += 1
                    continue
                watch_end = _parse_ts(obs.watch_until)
                if source.get("status") == "NO_BREAK" and watch_end is not None and observed_at < watch_end:
                    continue

                source_outcome = "NO_BREAK"
                live_obs = obs
                option_evidence = None
                capture_gate_eligible = False
                if source.get("status") == "PROVEN":
                    live_obs = _live_observation(obs, source)
                    if live_obs.status == "TRIGGERED":
                        source_outcome = "REVERSAL"; summary["reversal_triggers"] += 1
                        crossed_at = _parse_ts(source.get("timestamp"))
                        gate_at = datetime.now(timezone.utc)
                        gate = evaluate_capture_gate(live_obs, prearmed_at=armed_seen.get(setup_id), decision_ts=gate_at, max_capture_lag_seconds=args.max_capture_lag_seconds, trigger_crossed_at=crossed_at)
                        if gate.eligible:
                            capture_gate_eligible = True
                            option_evidence = await _capture_selector_evidence(pub, ticker=ticker, direction=str(live_obs.direction), cfg=cfg)
                            option_evidence = _enforce_final_capture_lag(option_evidence, obs=live_obs, prearmed_at=armed_seen.get(setup_id), max_capture_lag_seconds=args.max_capture_lag_seconds, crossed_at=crossed_at)
                        else:
                            option_evidence = _blocked_option(gate.reason_code or "capture_gate_blocked", ticker=ticker, direction=live_obs.direction, decision_ts=gate_at)
                        if option_evidence.get("status") == "CAPTURED": summary["option_evidence_captured"] += 1
                        else: summary["option_evidence_blocked"] += 1
                    else:
                        source_outcome = "CONTINUATION"; summary["continuation_first"] += 1
                else:
                    summary["no_break"] += 1

                row={"record_type":"RESOLUTION","observed_at":observed_at.isoformat(),"collector_id":COLLECTOR_ID,"collector_version":COLLECTOR_VERSION,"policy_epoch":POLICY_EPOCH,"setup_id":setup_id,"prearmed_at":armed_seen.get(setup_id).isoformat() if armed_seen.get(setup_id) else None,"source_outcome":source_outcome,"capture_gate_eligible":capture_gate_eligible,"option_evidence_usable":bool(option_evidence and option_evidence.get("status")=="CAPTURED"),"observation":live_obs.to_dict(),"trigger_source":source,"option_evidence":option_evidence,"reconciliation_status":"PENDING_DELAYED_SIP"}
                if not args.dry_run: _append(journal,row)
                terminal_seen[setup_id]=row; summary["resolutions_written"] += 1

    recon = await _reconcile_pending(journal=journal, terminal=terminal_seen, reconciled=reconciled, sip_provider=sip_provider, raw_dir=raw_dir, now=datetime.now(timezone.utc), dry_run=args.dry_run)
    summary.update({f"sip_{k}":v for k,v in recon.items()})
    completed=datetime.now(timezone.utc); summary["completed_at"]=completed.isoformat(); summary["cycle_seconds"]=round((completed-started).total_seconds(),3); summary["cycle_over_cadence"]=summary["cycle_seconds"]>DEFAULT_CADENCE_SECONDS
    return summary


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker",action="append",default=[])
    parser.add_argument("--env-file")
    parser.add_argument("--journal",default="logs/options_122_prospective.jsonl")
    parser.add_argument("--raw-trade-dir",default="logs/options_122_source_trades")
    parser.add_argument("--max-capture-lag-seconds",type=float,default=DEFAULT_MAX_CAPTURE_LAG_SECONDS)
    parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(argv)
    print(json.dumps(asyncio.run(run(args)),indent=2,sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
