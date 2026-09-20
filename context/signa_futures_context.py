"""Read-only Signa futures context lane.

Maps futures instruments to ETF/index proxies and normalizes already-fetched
Signa observations into durable regime/context tags. This module has no network
I/O, broker, execution, risk, or strategy authority.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from context.market_context import MarketState
from sources.signa_observation import SignaActionCardObservation

SCHEMA_VERSION = "signa_futures_context_v2"
AUTHORITY = "observation_only"
DEFAULT_TIMEFRAMES = ("1h", "4h", "1d")
DEFAULT_SNAPSHOT_MAX_AGE_SECONDS = 3600
REGIME_PROXIES = ("TLT", "VIX")

_LONG = {"LONG", "UP", "BULL", "BULLISH", "BUY", "CALL"}
_SHORT = {"SHORT", "DOWN", "BEAR", "BEARISH", "SELL", "PUT"}
_NEUTRAL = {"NEUTRAL", "MIXED", "FLAT", "WAIT", "HOLD"}

_PROXY_MAP: dict[str, tuple[str, ...]] = {
    "MES": ("SPY",), "ES": ("SPY",),
    "MNQ": ("QQQ",), "NQ": ("QQQ",),
    "M2K": ("IWM",), "RTY": ("IWM",),
    "MYM": ("DIA",), "YM": ("DIA",),
    "MGC": ("GLD",), "GC": ("GLD",),
    "MCL": ("USO", "XLE"), "CL": ("USO", "XLE"),
    "MBT": ("BTC",),
}


@dataclass(frozen=True)
class SignaProxyObservation:
    proxy: str
    timeframe: str
    observation: SignaActionCardObservation
    snapshot_id: str | None = None
    snapshot_ref: dict[str, Any] | None = None
    snapshot_status: str | None = None
    snapshot_age_seconds: float | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "proxy": self.proxy,
            "timeframe": self.timeframe,
            "ok": self.observation.ok,
            "direction": _bias(self.observation.direction),
            "grade": self.observation.grade,
            "score": self.observation.score,
            "confidence": self.observation.confidence,
            "error": self.observation.error,
            "data_as_of": self.observation.data_as_of,
            "retrieved_at": self.observation.retrieved_at,
            "cached": self.observation.cached,
            "snapshot_id": self.snapshot_id,
            "snapshot_ref": self.snapshot_ref,
            "snapshot_status": self.snapshot_status,
            "snapshot_age_seconds": self.snapshot_age_seconds,
        }


@dataclass(frozen=True)
class FuturesSignaContext:
    schema_version: str
    futures_symbol: str
    futures_root: str
    proxies: tuple[str, ...]
    timeframes_requested: tuple[str, ...]
    observations: tuple[SignaProxyObservation, ...]
    ftfc_state: str
    primary_bias: str
    aligned_with_trade_direction: bool | None
    tags: tuple[str, ...]
    missing: tuple[str, ...]
    errors: tuple[str, ...]
    snapshot_refs: tuple[dict[str, Any], ...] = ()
    snapshot_status: str = "NOT_REQUESTED"

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "definition": self.schema_version,
            "authority": AUTHORITY,
            "futures_symbol": self.futures_symbol,
            "futures_root": self.futures_root,
            "proxies": list(self.proxies),
            "proxy_symbol": self.proxies[0] if self.proxies else None,
            "timeframes_requested": list(self.timeframes_requested),
            "observations": [item.to_record() for item in self.observations],
            "ftfc_state": self.ftfc_state,
            "signa_ftfc_state": self.ftfc_state,
            "primary_bias": self.primary_bias,
            "signa_direction": self.primary_bias if self.primary_bias != "UNKNOWN" else None,
            "aligned_with_trade_direction": self.aligned_with_trade_direction,
            "tags": list(self.tags),
            "derived_states": list(self.tags),
            "missing": list(self.missing),
            "errors": list(self.errors),
            "snapshot_refs": [dict(ref) for ref in self.snapshot_refs],
            "snapshot_ids": [ref.get("snapshot_id") for ref in self.snapshot_refs if ref.get("snapshot_id")],
            "snapshot_status": self.snapshot_status,
            "status": _status(self.missing, self.errors, self.observations),
            "gate_authoritative": False,
            "broker_evaluated": False,
            "risk_evaluated": False,
            "trade_authorized": False,
            "execution_authority": False,
        }


def contract_root(symbol: object) -> str:
    text = str(symbol or "").split(":")[-1].upper().strip().rstrip("!")
    if text.endswith("1"):
        text = text[:-1]
    for root in sorted(_PROXY_MAP, key=len, reverse=True):
        if text == root or text.startswith(root):
            return root
    letters = "".join(ch for ch in text if ch.isalpha())
    for root in sorted(_PROXY_MAP, key=len, reverse=True):
        if letters == root or letters.startswith(root):
            return root
    return letters


def proxies_for_futures(symbol: object) -> tuple[str, ...]:
    return _PROXY_MAP.get(contract_root(symbol), ())


def proxy_for_futures(symbol: str | None) -> str | None:
    proxies = proxies_for_futures(symbol)
    return proxies[0] if proxies else None


def needed_proxies(symbols: Sequence[object]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for symbol in symbols:
        for proxy in proxies_for_futures(symbol):
            if proxy not in seen:
                seen.add(proxy)
                out.append(proxy)
    for proxy in REGIME_PROXIES:
        if proxy not in seen:
            seen.add(proxy)
            out.append(proxy)
    return tuple(out)


def build_futures_signa_context(
    futures_symbol: object,
    observations: Sequence[SignaProxyObservation],
    *,
    trade_direction: str | None = None,
    timeframes_requested: Sequence[str] = DEFAULT_TIMEFRAMES,
) -> FuturesSignaContext:
    futures_name = str(futures_symbol or "").split(":")[-1].upper().strip()
    root = contract_root(futures_symbol)
    proxies = proxies_for_futures(futures_symbol)
    normalized_obs = tuple(observations)
    errors = tuple(f"{x.proxy}:{x.timeframe}:{x.observation.error}" for x in normalized_obs if x.observation.error)
    missing = _missing_observations(proxies, timeframes_requested, normalized_obs)
    biases = [_bias(x.observation.direction) for x in normalized_obs if x.observation.ok]
    directional = [bias for bias in biases if bias in {"LONG", "SHORT"}]
    primary_bias = _primary_bias(directional)
    ftfc_state = _ftfc_state(normalized_obs, timeframes_requested, missing)
    trade_bias = _bias(trade_direction)
    aligned = None
    if trade_bias in {"LONG", "SHORT"} and primary_bias in {"LONG", "SHORT"}:
        aligned = trade_bias == primary_bias
    return FuturesSignaContext(
        schema_version=SCHEMA_VERSION,
        futures_symbol=futures_name,
        futures_root=root,
        proxies=proxies,
        timeframes_requested=tuple(str(tf) for tf in timeframes_requested),
        observations=normalized_obs,
        ftfc_state=ftfc_state,
        primary_bias=primary_bias,
        aligned_with_trade_direction=aligned,
        tags=_tags(ftfc_state, primary_bias, aligned, missing, errors),
        missing=missing,
        errors=errors,
        snapshot_refs=tuple(_snapshot_refs(normalized_obs)),
        snapshot_status=_snapshot_status(normalized_obs),
    )


def build_signa_futures_context(
    state: MarketState,
    *,
    futures_direction: str | None = None,
    timeframe: str = "1d",
    snapshot_db_path: str | Path | None = None,
    snapshot_max_age_seconds: int | float = DEFAULT_SNAPSHOT_MAX_AGE_SECONDS,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    """Return journal-ready context from runtime fields plus shared snapshots.

    Shared Signa snapshots are read from SQLite in read-only mode. A missing,
    stale, or malformed shared DB is recorded as context status only and must
    never affect trade permission.
    """
    signa = state.signa
    observations: list[SignaProxyObservation] = []
    proxy = proxy_for_futures(getattr(state, "instrument", None))
    if snapshot_db_path is not None:
        observations.extend(_observations_from_snapshot_db(
            snapshot_db_path,
            getattr(state, "instrument", None),
            timeframe=timeframe,
            max_age_seconds=snapshot_max_age_seconds,
            now=now,
        ))
    if proxy and signa is not None and (signa.grade is not None or signa.score is not None or signa.daily_direction is not None or signa.weekly_direction is not None):
        observations.append(SignaProxyObservation(
            proxy=proxy,
            timeframe=timeframe,
            observation=SignaActionCardObservation(
                ok=True,
                symbol=proxy,
                timeframe=timeframe,
                direction=signa.daily_direction or signa.weekly_direction,
                grade=str(signa.grade).upper() if signa.grade is not None else None,
                score=signa.score,
            ),
            snapshot_status="RUNTIME_STATE",
        ))
    return build_futures_signa_context(getattr(state, "instrument", None), observations, trade_direction=futures_direction, timeframes_requested=(timeframe,)).to_record()


def _observations_from_snapshot_db(
    db_path: str | Path,
    futures_symbol: object,
    *,
    timeframe: str,
    max_age_seconds: int | float,
    now: datetime | str | None,
) -> list[SignaProxyObservation]:
    proxies = proxies_for_futures(futures_symbol)
    if not proxies:
        return []
    path = Path(db_path)
    if not path.exists():
        return [_error_observation(proxy, timeframe, "snapshot_db_missing") for proxy in proxies]
    current = _parse_utc(now)
    out: list[SignaProxyObservation] = []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        with conn:
            for proxy in proxies:
                row = conn.execute(
                    "SELECT snapshot_id, source, endpoint, symbol, timeframe, params_hash, "
                    "snapshot_bucket, retrieved_at, data_as_of, status, http_status, payload_json "
                    "FROM signa_snapshots WHERE symbol = ? AND timeframe = ? "
                    "ORDER BY "
                    "CASE WHEN upper(status) = 'OK' THEN 0 ELSE 1 END, "
                    "CASE WHEN source = 'action_card' OR endpoint LIKE '/api/v1/signals/%' THEN 0 ELSE 1 END, "
                    "retrieved_at DESC, id DESC LIMIT 1",
                    (proxy, str(timeframe).lower()),
                ).fetchone()
                if row is None:
                    out.append(_error_observation(proxy, timeframe, "snapshot_missing"))
                    continue
                out.append(_observation_from_snapshot_row(row, current, max_age_seconds))
    except Exception as exc:  # noqa: BLE001 - context lookup must fail soft
        return [_error_observation(proxy, timeframe, f"snapshot_read_error:{type(exc).__name__}") for proxy in proxies]
    return out


def _observation_from_snapshot_row(row: sqlite3.Row, now: datetime, max_age_seconds: int | float) -> SignaProxyObservation:
    payload = _json_dict(row[11])
    retrieved_at = str(row[7])
    age = max(0.0, (now - _parse_utc(retrieved_at)).total_seconds())
    status = str(row[9] or "OK").upper()
    stale = age > float(max_age_seconds)
    error = None
    if status != "OK":
        error = f"snapshot_status_{status.lower()}"
    elif stale:
        error = "snapshot_stale"
    direction = _payload_direction(payload)
    snapshot_ref = {
        "snapshot_id": str(row[0]),
        "source": str(row[1]),
        "endpoint": str(row[2]),
        "symbol": str(row[3]),
        "timeframe": str(row[4]),
        "params_hash": str(row[5]),
        "snapshot_bucket": str(row[6]),
        "retrieved_at": retrieved_at,
        "data_as_of": row[8],
        "status": status,
        "http_status": row[10],
        "observation_only": True,
        "trade_authority": False,
    }
    return SignaProxyObservation(
        proxy=str(row[3]),
        timeframe=str(row[4]),
        observation=SignaActionCardObservation(
            ok=error is None,
            symbol=str(row[3]),
            timeframe=str(row[4]),
            direction=direction,
            grade=_payload_str(payload, "grade"),
            score=_payload_float(payload, "score", "signal_score", "rank_score"),
            confidence=_payload_float(payload, "confidence", "conviction"),
            data_as_of=row[8],
            retrieved_at=retrieved_at,
            error=error,
            raw=payload,
        ),
        snapshot_id=str(row[0]),
        snapshot_ref=snapshot_ref,
        snapshot_status="STALE" if stale else status,
        snapshot_age_seconds=age,
    )


def _error_observation(proxy: str, timeframe: str, error: str) -> SignaProxyObservation:
    return SignaProxyObservation(
        proxy=proxy,
        timeframe=timeframe,
        observation=SignaActionCardObservation(ok=False, symbol=proxy, timeframe=timeframe, error=error),
        snapshot_status="ERROR",
    )


def _missing_observations(proxies: Sequence[str], timeframes: Sequence[str], observations: Sequence[SignaProxyObservation]) -> tuple[str, ...]:
    present = {(item.proxy.upper(), str(item.timeframe)) for item in observations if item.observation.ok}
    return tuple(f"{proxy}:{timeframe}" for proxy in proxies for timeframe in timeframes if (proxy.upper(), str(timeframe)) not in present)


def _bias(value: object) -> str:
    raw = str(value or "").upper().strip()
    if raw in _LONG:
        return "LONG"
    if raw in _SHORT:
        return "SHORT"
    if raw in _NEUTRAL:
        return "NEUTRAL"
    return "UNKNOWN"


def _primary_bias(biases: Sequence[str]) -> str:
    longs = sum(1 for x in biases if x == "LONG")
    shorts = sum(1 for x in biases if x == "SHORT")
    if longs > shorts:
        return "LONG"
    if shorts > longs:
        return "SHORT"
    if longs == shorts and longs > 0:
        return "MIXED"
    return "UNKNOWN"


def _ftfc_state(observations: Sequence[SignaProxyObservation], timeframes_requested: Sequence[str], missing: Sequence[str]) -> str:
    directional = [_bias(x.observation.direction) for x in observations if x.observation.ok and _bias(x.observation.direction) in {"LONG", "SHORT"}]
    if missing and not directional:
        return "SIGNA_INCOMPLETE"
    if not directional:
        return "SIGNA_UNKNOWN"
    if len(directional) >= len(timeframes_requested) and len(set(directional)) == 1:
        return f"SIGNA_FTFC_{directional[0]}"
    if len(set(directional)) > 1:
        return "SIGNA_MIXED"
    return "SIGNA_INCOMPLETE"


def _tags(ftfc_state: str, primary_bias: str, aligned: bool | None, missing: Sequence[str], errors: Sequence[str]) -> tuple[str, ...]:
    tags: list[str] = [ftfc_state]
    if primary_bias == "LONG":
        tags.extend(["SIGNA_INDEX_LONG", "RISK_ON"])
    elif primary_bias == "SHORT":
        tags.extend(["SIGNA_INDEX_SHORT", "RISK_OFF"])
    elif primary_bias == "MIXED":
        tags.append("SIGNA_INDEX_MIXED")
    else:
        tags.append("SIGNA_INDEX_UNKNOWN")
    if aligned is True:
        tags.extend(["SIGNA_TRADE_ALIGNED", "SECTOR_SUPPORTIVE"])
    elif aligned is False:
        tags.extend(["SIGNA_TRADE_CONFLICT", "SECTOR_CONFLICT"])
    if missing:
        tags.append("SIGNA_CONTEXT_MISSING")
    if errors:
        tags.append("SIGNA_CONTEXT_ERRORS")
    return tuple(tags)


def _snapshot_refs(observations: Sequence[SignaProxyObservation]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in observations:
        ref = item.snapshot_ref
        sid = item.snapshot_id
        if not ref or not sid or sid in seen:
            continue
        refs.append(ref)
        seen.add(sid)
    return refs


def _snapshot_status(observations: Sequence[SignaProxyObservation]) -> str:
    if not observations:
        return "NO_SNAPSHOT"
    if any(item.snapshot_status == "ERROR" for item in observations):
        return "ERROR"
    if any(item.snapshot_status == "STALE" for item in observations):
        return "STALE"
    if any(item.snapshot_id for item in observations):
        return "OK"
    return "RUNTIME_STATE_ONLY"


def _payload_direction(payload: dict[str, Any]) -> str | None:
    signal = _nested_dict(payload, "data", "signal")
    search = [signal, payload, _nested_dict(payload, "signa")]
    for item in search:
        value = _payload_first(item, "direction", "bias", "signal", "action", "recommendation", "sentiment")
        if value:
            return value
    return None


def _payload_str(payload: dict[str, Any], *keys: str) -> str | None:
    signal = _nested_dict(payload, "data", "signal")
    for item in (signal, payload):
        value = _payload_first(item, *keys)
        if value:
            return value
    return None


def _payload_first(payload: dict[str, Any], *keys: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _payload_float(payload: dict[str, Any], *keys: str) -> float | None:
    signal = _nested_dict(payload, "data", "signal")
    for item in (signal, payload):
        for key in keys:
            value = item.get(key) if isinstance(item, dict) else None
            try:
                if value is not None and str(value).strip():
                    return float(str(value).replace("%", ""))
            except ValueError:
                continue
    return None


def _nested_dict(payload: dict[str, Any], *keys: str) -> dict[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return {}
        current = current.get(key)
    return current if isinstance(current, dict) else {}


def _json_dict(raw: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_utc(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _status(missing: Sequence[str], errors: Sequence[str], observations: Sequence[SignaProxyObservation]) -> str:
    if errors:
        return "ERROR"
    if not observations:
        return "NO_SIGNA_DATA"
    if missing:
        return "PARTIAL_SIGNA_DATA"
    return "OK"
