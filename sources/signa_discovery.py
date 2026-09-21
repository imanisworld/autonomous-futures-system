"""Read-only Signa discovery normalization for options candidates.

This module has no scanner, strategy, risk, broker, order, or execution imports.
It turns Signa scan/action-card/intelligence payloads into candidate telemetry
that can be journaled and studied before any trading rule changes are proposed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
from typing import Any

import httpx

from .signa_intelligence import parse_action_card, summarize_dataset
from .signa_request_budget import (
    account_backoff_remaining,
    mark_account_rate_limited,
    retry_after_seconds,
)

DEFAULT_BASE_URL = "https://app.getsigna.ai"
DEFAULT_CACHE_TTL_SECONDS = 1800.0

_DISCOVERY_SOURCES = {"scan", "enhanced_signal", "action_card", "options_flow", "dark_pool", "gex"}
_LONG = {"BUY", "BULL", "BULLISH", "LONG", "CALL", "CALLS", "UP"}
_SHORT = {"SELL", "SHORT", "BEAR", "BEARISH", "PUT", "PUTS", "DOWN"}
_NEUTRAL = {"HOLD", "NEUTRAL", "FLAT", "SIDEWAYS", "WATCH", "AVOID"}


@dataclass(frozen=True)
class SignaDiscoveryCandidate:
    ticker: str
    direction: str | None
    source: str
    endpoint: str
    source_agent: str | None = None
    timeframe: str | None = None
    grade: str | None = None
    score: float | None = None
    confidence: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    targets: tuple[float, ...] = ()
    reward_to_risk: float | None = None
    component_scores: dict[str, float | None] = field(default_factory=dict)
    reason: str | None = None
    data_as_of: str | None = None
    retrieved_at: str | None = None
    raw_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def candidate_key(self) -> str:
        payload = {
            "ticker": self.ticker,
            "direction": self.direction,
            "source": self.source,
            "endpoint": self.endpoint,
            "agent": self.source_agent,
            "timeframe": self.timeframe,
            "data_as_of": self.data_as_of,
        }
        blob = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(blob).hexdigest()[:20]

    def to_record(self) -> dict[str, Any]:
        out = asdict(self)
        out["candidate_key"] = self.candidate_key
        out["status"] = "SIGNA_CANDIDATE"
        out["observation_only"] = True
        out["trade_authority"] = False
        return out


@dataclass(frozen=True)
class SignaDiscoveryResponse:
    ok: bool
    endpoint: str
    payload: Any | None = None
    error: str | None = None
    status_code: int | None = None
    retrieved_at: str | None = None
    cached: bool = False
    backoff_active: bool = False


def normalize_direction(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text in _LONG:
        return "LONG"
    if text in _SHORT:
        return "SHORT"
    if text in _NEUTRAL:
        return "NEUTRAL" if text != "AVOID" else "AVOID"
    return text


def candidates_from_action_card(payload: dict[str, Any], *, endpoint: str = "signal", retrieved_at: str | None = None) -> list[SignaDiscoveryCandidate]:
    card = parse_action_card(payload)
    if not card.symbol:
        return []
    return [
        SignaDiscoveryCandidate(
            ticker=card.symbol.upper(),
            direction=normalize_direction(card.direction),
            source="action_card",
            endpoint=endpoint,
            timeframe=card.timeframe,
            grade=(card.grade.upper() if card.grade else None),
            score=card.score,
            confidence=card.confidence,
            entry_low=card.entry_low,
            entry_high=card.entry_high,
            stop_loss=card.stop_loss,
            targets=card.targets,
            reward_to_risk=card.reward_to_risk,
            component_scores=card.component_scores,
            data_as_of=card.data_as_of,
            retrieved_at=retrieved_at or card.server_time,
            raw_summary={"model_version": card.model_version},
        )
    ]


def candidates_from_scan(payload: dict[str, Any], *, endpoint: str = "scan", retrieved_at: str | None = None) -> list[SignaDiscoveryCandidate]:
    out: list[SignaDiscoveryCandidate] = []
    for item in _items(payload):
        symbol = _text_first(item, "ticker", "symbol", "underlying", "name")
        if not symbol:
            continue
        agent = _text_first(item, "agent", "model", "strategy", "source_agent", "scanner")
        direction = _text_first(item, "direction", "bias", "signal", "action", "recommendation")
        entry = _dict(item.get("entry_zone") or item.get("entry"))
        targets = tuple(v for v in (_float(x) for x in _list(item.get("targets") or item.get("target"))) if v is not None)
        if not targets and _float(item.get("target_1")) is not None:
            targets = tuple(v for v in (_float(item.get("target_1")), _float(item.get("target_2"))) if v is not None)
        out.append(
            SignaDiscoveryCandidate(
                ticker=str(symbol).upper(),
                direction=normalize_direction(direction),
                source="scan",
                endpoint=endpoint,
                source_agent=agent,
                timeframe=_text_first(item, "timeframe", "tf", "horizon"),
                grade=_upper_or_none(item.get("grade")),
                score=_float(item.get("score") or item.get("signal_score") or item.get("rank_score")),
                confidence=_float(item.get("confidence") or item.get("conviction")),
                entry_low=_float(entry.get("low") or item.get("entry_low")),
                entry_high=_float(entry.get("high") or item.get("entry_high")),
                stop_loss=_float(item.get("stop_loss") or item.get("stop")),
                targets=targets,
                reward_to_risk=_float(item.get("reward_to_risk") or item.get("rr") or item.get("risk_reward")),
                reason=_text_first(item, "reason", "rationale", "explanation", "summary"),
                data_as_of=_text_first(payload, "data_as_of", "as_of", "server_time"),
                retrieved_at=retrieved_at,
                raw_summary={"top_level_fields": tuple(sorted(str(k) for k in item.keys()))},
            )
        )
    return dedupe_candidates(out)


def dataset_candidate_tags(dataset: str, payload: dict[str, Any], *, symbol: str | None = None) -> dict[str, Any]:
    obs = summarize_dataset(dataset, payload, symbol=symbol)
    return {
        "dataset": obs.dataset,
        "symbol": obs.symbol,
        "count": obs.count,
        "direction": normalize_direction(obs.direction),
        "sentiment": obs.sentiment,
        "top_level_fields": list(obs.top_level_fields),
        "observation_only": True,
        "trade_authority": False,
    }


def dedupe_candidates(candidates: Iterable[SignaDiscoveryCandidate]) -> list[SignaDiscoveryCandidate]:
    seen: set[str] = set()
    out: list[SignaDiscoveryCandidate] = []
    for candidate in candidates:
        key = candidate.candidate_key
        if key in seen:
            continue
        seen.add(key)
        out.append(candidate)
    return out


def manual_context_record(payload: dict[str, Any], *, default_source: str = "manual") -> dict[str, Any]:
    """Normalize manually pasted Signa/GEX/flow context into one safe row."""
    dataset = _text_first(payload, "dataset", "source", "type") or default_source
    symbol = _text_first(payload, "ticker", "symbol", "underlying")
    tag = dataset_candidate_tags(str(dataset), payload, symbol=symbol)
    tag.update({
        "source": str(dataset),
        "status": "SIGNA_CONTEXT",
        "context_source": "manual",
        "timeframe": _text_first(payload, "timeframe", "tf", "horizon"),
        "data_as_of": _text_first(payload, "data_as_of", "as_of"),
        "provider_timestamp": _text_first(payload, "provider_timestamp", "provider_ts", "server_time", "timestamp"),
        "retrieved_at": _text_first(payload, "retrieved_at", "timestamp", "as_of"),
        "raw_summary": {"top_level_fields": sorted(str(key) for key in payload.keys())},
    })
    for key in (
        "callPremium", "putPremium", "totalPremium", "netPremium", "callVolume",
        "putVolume", "putCallRatio", "gamma_wall", "gammaWall", "flip",
        "zero_gamma", "zeroGamma", "support", "resistance", "notes",
    ):
        if key in payload:
            tag[key] = payload[key]
    return tag


def manual_context_records_from_text(text: str, *, default_source: str = "manual") -> list[dict[str, Any]]:
    """Parse simple Discord/paste blocks of key:value context."""
    records: list[dict[str, Any]] = []
    for block in _manual_blocks(text):
        payload: dict[str, Any] = {}
        for line in block.splitlines():
            clean = line.strip().lstrip("-•").strip()
            if not clean:
                continue
            if ":" in clean:
                key, value = clean.split(":", 1)
            elif "=" in clean:
                key, value = clean.split("=", 1)
            else:
                continue
            payload[_manual_key(key)] = _manual_value(value)
        if payload:
            records.append(manual_context_record(payload, default_source=default_source))
    return records


def context_record_from_response(
    source: str,
    response: "SignaDiscoveryResponse",
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Normalize one direct Signa response into a safe context evidence row."""
    payload = response.payload if isinstance(response.payload, dict) else {}
    row = dataset_candidate_tags(source, payload, symbol=symbol)
    row.update(
        {
            "source": source,
            "endpoint": response.endpoint,
            "status": "SIGNA_CONTEXT" if response.ok else "SIGNA_CONTEXT_ERROR",
            "timeframe": _text_first(payload, "timeframe", "tf", "horizon"),
            "data_as_of": _text_first(payload, "data_as_of", "as_of"),
            "provider_timestamp": _text_first(payload, "provider_timestamp", "provider_ts", "server_time", "updated_at", "last_updated", "timestamp"),
            "retrieved_at": response.retrieved_at,
            "request_ok": response.ok,
            "http_status": response.status_code,
            "error": response.error,
            "cached": response.cached,
            "backoff_active": response.backoff_active,
        }
    )
    for key in (
        "callPremium", "putPremium", "totalPremium", "netPremium", "callVolume",
        "putVolume", "putCallRatio", "unusualActivity", "sentiment", "symbol",
        "count", "call_pct", "put_pct", "call_premium", "put_premium", "row_count",
        "signal", "confidence", "trade_count", "buy_count", "sell_count",
        "recent_30d", "house_count", "senate_count",
    ):
        if key in payload:
            row[key] = payload[key]
    row["raw_summary"] = {
        "top_level_fields": sorted(str(key) for key in payload.keys()),
    }
    return row


def records_from_direct_response(
    source: str,
    response: "SignaDiscoveryResponse",
    *,
    symbol: str | None = None,
) -> list[dict[str, Any]]:
    """Convert direct Signa API responses into candidate/context rows."""
    if not response.ok:
        return [context_record_from_response(source, response, symbol=symbol)]
    payload = response.payload if isinstance(response.payload, dict) else {}
    if source == "scan":
        return [candidate.to_record() for candidate in candidates_from_scan(payload, endpoint=response.endpoint, retrieved_at=response.retrieved_at)]
    if source == "action_card":
        return [candidate.to_record() for candidate in candidates_from_action_card(payload, endpoint=response.endpoint, retrieved_at=response.retrieved_at)]
    return [context_record_from_response(source, response, symbol=symbol)]


def _manual_blocks(text: str) -> list[str]:
    raw = str(text or "").strip()
    if not raw:
        return []
    return [block for block in re.split(r"\n\s*\n", raw) if block.strip()]


def _manual_key(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^0-9A-Za-z]+", "_", text).strip("_")
    return text or "field"


def _manual_value(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return None
    lower = text.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    cleaned = text.replace(",", "").replace("$", "")
    try:
        if "." in cleaned:
            return float(cleaned)
        return int(cleaned)
    except ValueError:
        return text


class SignaDiscoveryClient:
    """Small read-only client for capability probes and candidate ingestion.

    It is intentionally not imported by the options scanner. It supports TTL
    caching and per-key backoff after HTTP 429 so discovery cannot burn quota in
    a tight polling loop.
    """

    def __init__(self, api_key: str | None = None, base_url: str = DEFAULT_BASE_URL, timeout: float = 5.0, client: httpx.Client | None = None, cache_ttl_seconds: float | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("SIGNA_API_KEY", "")).strip()
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = float(timeout)
        self._client = client
        self.cache_ttl_seconds = DEFAULT_CACHE_TTL_SECONDS if cache_ttl_seconds is None else max(0.0, float(cache_ttl_seconds))
        self._clock = clock
        self._cache: dict[str, tuple[float, SignaDiscoveryResponse]] = {}
        self._backoff_until: dict[str, float] = {}

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def me(self) -> SignaDiscoveryResponse:
        return self.fetch("/api/v1/me")

    def health(self) -> SignaDiscoveryResponse:
        return self.fetch("/health")

    def scan(self, symbols: Iterable[str] | str, **params: Any) -> SignaDiscoveryResponse:
        """Fetch the documented scan surface; Signa requires symbols=."""
        symbol_text = symbols if isinstance(symbols, str) else ",".join(str(s).upper() for s in symbols)
        return self.fetch("/api/v1/scan", params={"symbols": symbol_text, **params})

    def signal_index(self, **params: Any) -> SignaDiscoveryResponse:
        return self.fetch("/api/v1/signal-index", params=params)

    def enhanced_signal(self, symbol: str, **params: Any) -> SignaDiscoveryResponse:
        return self.fetch("/api/v1/enhanced-signal", params={"symbol": symbol.upper(), **params})

    def action_card(self, symbol: str, timeframe: str = "1d") -> SignaDiscoveryResponse:
        return self.fetch(f"/api/v1/signals/{symbol.upper()}", params={"timeframe": timeframe})

    def options_flow(self, symbol: str | None = None, **params: Any) -> SignaDiscoveryResponse:
        if symbol:
            return self.fetch(f"/api/options-flow/{symbol.upper()}", params=params)
        return self.fetch("/api/options-flow/tide", params=params)

    def dark_pool(self, symbol: str | None = None, **params: Any) -> SignaDiscoveryResponse:
        if symbol:
            return self.fetch(f"/api/options-flow/darkpool/{symbol.upper()}", params=params)
        return self.fetch("/api/darkpool/prints", params=params)

    def market_tide(self) -> SignaDiscoveryResponse:
        return self.fetch("/api/options-flow/tide")

    def political_trades(self, symbol: str, **params: Any) -> SignaDiscoveryResponse:
        return self.fetch("/api/v1/political-trades", params={"ticker": symbol.upper(), **params})

    def congress_flow(self, symbol: str, **params: Any) -> SignaDiscoveryResponse:
        return self.fetch("/api/options-flow/congress", params={"ticker": symbol.upper(), **params})

    def gex(self, symbol: str, **params: Any) -> SignaDiscoveryResponse:
        _ = symbol, params
        return SignaDiscoveryResponse(False, "gex", error="standalone_gex_endpoint_unresolved", retrieved_at=_utc_now())

    def fetch_any(self, endpoints: Iterable[str], *, params: dict[str, Any] | None = None) -> SignaDiscoveryResponse:
        last: SignaDiscoveryResponse | None = None
        for endpoint in endpoints:
            response = self.fetch(endpoint, params=params)
            if response.ok or response.status_code not in {404, 405}:
                return response
            last = response
        return last or SignaDiscoveryResponse(False, "", error="no_endpoints", retrieved_at=_utc_now())

    def fetch(self, endpoint: str, *, params: dict[str, Any] | None = None) -> SignaDiscoveryResponse:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = _cache_key(endpoint, params)
        now = self._clock()
        if account_backoff_remaining(
            self.base_url,
            self.api_key,
            clock=self._clock,
        ) > 0:
            return SignaDiscoveryResponse(
                False,
                endpoint,
                error="account_backoff_active",
                retrieved_at=_utc_now(),
                backoff_active=True,
            )
        if self._backoff_until.get(key, 0.0) > now:
            return SignaDiscoveryResponse(False, endpoint, error="backoff_active", retrieved_at=_utc_now(), backoff_active=True)
        cached = self._cached(key)
        if cached is not None:
            return cached
        if not self.configured:
            return SignaDiscoveryResponse(False, endpoint, error="missing_api_key", retrieved_at=_utc_now())
        close_client = self._client is None
        http = self._client or httpx.Client(base_url=self.base_url, timeout=self.timeout, follow_redirects=True)
        try:
            response = http.get(endpoint, params=params, headers={"Authorization": f"Bearer {self.api_key}"})
            if response.status_code == 429:
                retry_seconds = mark_account_rate_limited(
                    self.base_url,
                    self.api_key,
                    retry_after=response.headers.get("Retry-After"),
                    clock=self._clock,
                )
                self._backoff_until[key] = now + retry_seconds
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, list):
                payload = {"items": payload}
            elif not isinstance(payload, dict):
                payload = {"value": payload}
            out = SignaDiscoveryResponse(True, endpoint, payload=payload, status_code=response.status_code, retrieved_at=_utc_now())
            self._store(key, out)
            return out
        except httpx.HTTPStatusError as exc:
            return SignaDiscoveryResponse(False, endpoint, error=f"http_{exc.response.status_code}", status_code=exc.response.status_code, retrieved_at=_utc_now())
        except (httpx.HTTPError, ValueError) as exc:
            return SignaDiscoveryResponse(False, endpoint, error=type(exc).__name__, retrieved_at=_utc_now())
        finally:
            if close_client:
                http.close()

    def _cached(self, key: str) -> SignaDiscoveryResponse | None:
        if self.cache_ttl_seconds <= 0:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        stored_at, response = entry
        if self._clock() - stored_at >= self.cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return replace(response, cached=True)

    def _store(self, key: str, response: SignaDiscoveryResponse) -> None:
        if self.cache_ttl_seconds <= 0 or not response.ok:
            return
        self._cache[key] = (self._clock(), response)


def _cache_key(endpoint: str, params: dict[str, Any]) -> str:
    blob = json.dumps({"endpoint": endpoint, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def _items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("results", "data", "signals", "items", "trades", "candidates"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _items(value)
            if nested:
                return nested
    return [payload] if any(k in payload for k in ("ticker", "symbol")) else []


def _retry_after_seconds(value: str | None) -> float:
    return retry_after_seconds(value)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _text_first(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _upper_or_none(value: Any) -> str | None:
    return str(value).strip().upper() if value is not None and str(value).strip() else None
