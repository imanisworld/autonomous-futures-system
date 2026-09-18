#!/usr/bin/env python3
"""Collect prospective 212R trigger + option-selector evidence, observation only.

The lane is deliberately isolated from the options scanner journal and from all
risk/broker/order paths.  It observes Public chart bars, proves a 2-1-2 setup
was ARMED before the trigger bucket when possible, and captures the existing
OPTIONS_PAPER_V1 selector inputs only for timely, pre-armed reversal events.

No alert is sent and no trade/risk state is created.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
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
from alert_ranker.config import load_config  # noqa: E402
from alert_ranker.market_data import PublicMarketDataClient  # noqa: E402
from alert_ranker.options_212r_prospective import evaluate_capture_gate, observe_212_setups  # noqa: E402
from alert_ranker.options_selector_evidence import (  # noqa: E402
    blocked_selector_evidence,
    build_selector_evidence_capture,
    finalize_selector_evidence,
)
from alert_ranker.paper_v1 import choose_contract, choose_expiration  # noqa: E402
from alert_ranker.public_chart_bars import PUBLIC_CHART_SOURCE, parse_regular_market_bars  # noqa: E402
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402

PRIMARY_20 = (
    "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ", "AMZN", "GOOGL", "PLTR", "INTC",
    "IWM", "TLT", "JPM", "BAC", "COIN", "XOM", "MRK", "WMT", "NFLX", "GE",
)
HISTORICDATA_PREFIX = "/userapigateway/historicdata"
COLLECTOR_ID = "OPTIONS_212R_PROSPECTIVE_COLLECTOR"
COLLECTOR_VERSION = "212r-collector-v0.1"


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
                if obs.status == "WATCHING":
                    if setup_id in armed_seen:
                        continue
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
                    continue

                if setup_id in terminal_seen:
                    continue

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
                    gate = evaluate_capture_gate(
                        obs, prearmed_at=prearmed_at, decision_ts=gate_checked_at,
                        max_capture_lag_seconds=args.max_capture_lag_seconds,
                    )
                    if not gate.eligible:
                        detail = gate.reason_code or "capture_gate_blocked"
                        if gate.lag_seconds is not None:
                            detail += f":{round(gate.lag_seconds,3)}"
                        option_evidence = _blocked_option(
                            detail, ticker=ticker, direction=obs.direction, decision_ts=gate_checked_at,
                        )
                    else:
                        capture_gate_eligible = True
                        option_evidence = await _capture_selector_evidence(
                            pub, ticker=ticker, direction=str(obs.direction), cfg=cfg
                        )
                        option_evidence = _enforce_final_capture_lag(
                            option_evidence, observation=obs, prearmed_at=prearmed_at,
                            max_capture_lag_seconds=args.max_capture_lag_seconds,
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
    parser.add_argument("--max-capture-lag-seconds", type=float, required=True,
                        help="Pre-registered max seconds from completed trigger 5m bar to evidence capture")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not args.ticker:
        args.ticker = list(PRIMARY_20)
    result = asyncio.run(run(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
