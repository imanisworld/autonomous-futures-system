"""Observation-only Signa context for MNQ/MES futures decisions.

This module is intentionally isolated from the futures decision/risk/execution
chain. It may fetch a current Signa Action Card for the ETF proxy of a futures
instrument and append an evidence row. The returned row is descriptive only;
callers must not use it to approve, reject, size, route, or modify a trade.

Current supported mapping:
- MNQ -> QQQ
- MES -> SPY

The collector is OFF by default and requires an explicit timeframe. No provider
request is made when disabled or misconfigured. Flow, dark-pool, market-tide,
and GEX surfaces are deliberately NOT guessed here; they stay blocked until the
Founding-key weekday probe proves their live endpoint/payload contracts.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sources.signa_v2_client import SignaV2Client

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


EVIDENCE_FILENAME = "futures_signa_shadow.jsonl"
PROXY_SYMBOL = {"MNQ": "QQQ", "MES": "SPY"}
SUPPORTED_TIMEFRAMES = {"1h", "1d"}

# One process-local client so SignaV2Client's per-(symbol,timeframe) TTL cache
# survives across webhook bars. Constructing a new client per decision would
# silently defeat the cache and could consume the shared account quota.
_shared_client: SignaV2Client | None = None


def enabled_from_env(env: Mapping[str, str] | None = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get("FUTURES_SIGNA_SHADOW_ENABLED", "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def timeframe_from_env(env: Mapping[str, str] | None = None) -> str | None:
    """Return a proven/allowed timeframe or None.

    There is intentionally no default. Monday's weekday probe must settle the
    operator-selected timeframe before this collector can make requests.
    """
    source = os.environ if env is None else env
    value = str(source.get("FUTURES_SIGNA_SHADOW_TIMEFRAME", "")).strip().lower()
    return value if value in SUPPORTED_TIMEFRAMES else None


def evidence_path(log_dir: str | Path) -> Path:
    return Path(log_dir) / EVIDENCE_FILENAME


def default_client() -> SignaV2Client:
    """Return the process-local read-only client/cache."""
    global _shared_client
    if _shared_client is None:
        _shared_client = SignaV2Client()
    return _shared_client


def append_futures_signa_shadow(
    *,
    log_dir: str | Path,
    instrument: str,
    decision_timestamp: str,
    session: str | None,
    decision: str | None,
    decision_reason: str | None,
    strategy: str | None,
    trade_direction: str | None,
    timeframe_minutes: int | None = None,
    for_date: date | None = None,
    env: Mapping[str, str] | None = None,
    client: SignaV2Client | Any | None = None,
) -> dict[str, Any] | None:
    """Append one futures/Signa observation row.

    OFF means exactly no request and no write. If enabled but the timeframe is
    unproven/unset, an explicit config-blocked evidence row is written without a
    provider call so the absence is auditable rather than silently treated as
    neutral context.
    """
    if not enabled_from_env(env):
        return None

    root = _root(instrument)
    proxy = PROXY_SYMBOL.get(root)
    if proxy is None:
        return None

    signa_timeframe = timeframe_from_env(env)
    base = {
        "kind": "futures_signa_shadow",
        "observation_only": True,
        "gate_authoritative": False,
        "risk_authoritative": False,
        "broker_authoritative": False,
        "execution_authoritative": False,
        "timestamp": decision_timestamp,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "instrument": root,
        "proxy_symbol": proxy,
        "session": session,
        "decision": decision,
        "decision_reason": decision_reason,
        "strategy": strategy,
        "trade_direction": trade_direction,
        "timeframe_minutes": timeframe_minutes,
        "signa_timeframe_requested": signa_timeframe,
    }

    if signa_timeframe is None:
        row = {
            **base,
            "signa_v2_ok": False,
            "signa_v2_error": "timeframe_unset_or_unproven",
        }
        _append(log_dir, row, for_date=for_date)
        return row

    signa = client if client is not None else default_client()
    try:
        observation = signa.fetch_action_card(proxy, signa_timeframe)
        fields = observation.telemetry_fields()
    except Exception as exc:  # noqa: BLE001 - evidence collection must never break futures
        fields = {
            "signa_v2_ok": False,
            "signa_v2_symbol": proxy,
            "signa_v2_timeframe": signa_timeframe,
            "signa_v2_error": type(exc).__name__,
        }

    row = {**base, **fields}
    _append(log_dir, row, for_date=for_date)
    return row


def direction_relation(trade_direction: str | None, signa_direction: str | None) -> str:
    """Descriptive relation for later reporting; never a gate."""
    trade = str(trade_direction or "").strip().upper()
    signa = str(signa_direction or "").strip().upper()
    if not trade or not signa:
        return "MISSING"
    if signa in {"WAIT", "NEUTRAL", "FLAT", "SIDEWAYS"}:
        return "NEUTRAL"
    bullish = signa in {"LONG", "BUY", "UP", "BULL", "BULLISH"}
    bearish = signa in {"SHORT", "SELL", "DOWN", "BEAR", "BEARISH"}
    if trade == "LONG" and bullish:
        return "ALIGNED"
    if trade == "SHORT" and bearish:
        return "ALIGNED"
    if trade in {"LONG", "SHORT"} and (bullish or bearish):
        return "OPPOSED"
    return "UNKNOWN"


def _append(log_dir: str | Path, row: dict[str, Any], *, for_date: date | None) -> None:
    path = evidence_path(log_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(row)
    if for_date is not None:
        payload.setdefault("journal_date", for_date.isoformat())
    with path.open("a") as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.write(json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _root(instrument: str) -> str:
    text = str(instrument or "").strip().upper().replace("1!", "")
    return text.rstrip("1234567890HMUZ")
