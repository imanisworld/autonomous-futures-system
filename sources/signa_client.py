"""Read-only Signa API client and adapters.

The trading engine treats Signa as optional context. Missing credentials,
timeouts, bad responses, or unsupported symbols return a neutral result instead
of blocking the webhook pipeline.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class SignaSignal:
    symbol: str
    ok: bool
    grade: str | None = None
    score: float | None = None
    daily_direction: str | None = None
    weekly_direction: str | None = None
    action: str | None = None
    confidence: float | None = None
    risk_rating: str | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None

    def to_payload_fields(self) -> dict[str, Any]:
        return {
            "signa_grade": self.grade,
            "signa_score": self.score,
            "signa_daily_direction": self.daily_direction,
            "signa_weekly_direction": self.weekly_direction,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ok": self.ok,
            "grade": self.grade,
            "score": self.score,
            "daily_direction": self.daily_direction,
            "weekly_direction": self.weekly_direction,
            "action": self.action,
            "confidence": self.confidence,
            "risk_rating": self.risk_rating,
            "error": self.error,
        }


class SignaClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://app.getsigna.ai",
        timeout: float = 3.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("SIGNA_API_KEY", "")).strip()
        self.base_url = (base_url or "https://app.getsigna.ai").rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch_signal(self, symbol: str, timeframe: str = "1d") -> SignaSignal:
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return SignaSignal(symbol=symbol, ok=False, error="missing_symbol")
        if not self.configured:
            return SignaSignal(symbol=symbol, ok=False, error="missing_api_key")

        close_client = False
        client = self._client
        if client is None:
            client = httpx.Client(base_url=self.base_url, timeout=self.timeout, follow_redirects=True)
            close_client = True
        try:
            response = client.get(
                "/api/v1/signal",
                params={"sym": symbol, "timeframe": timeframe},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            return parse_signa_signal(symbol=symbol, payload=payload)
        except httpx.HTTPStatusError as exc:
            return SignaSignal(symbol=symbol, ok=False, error=f"http_{exc.response.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            return SignaSignal(symbol=symbol, ok=False, error=exc.__class__.__name__)
        finally:
            if close_client:
                client.close()


def parse_signa_signal(symbol: str, payload: dict[str, Any]) -> SignaSignal:
    engine = _dict(payload.get("engine"))
    signa = _dict(payload.get("signa"))
    data = _dict(payload.get("data"))

    grade = _normalize_grade(engine.get("grade") or signa.get("grade"))
    score = _float_or_none(engine.get("score") or signa.get("conviction") or data.get("confidence"))
    daily_direction = _normalize_direction(
        data.get("direction") or engine.get("direction") or signa.get("action")
    )
    weekly_direction = _normalize_direction(data.get("weekly_direction") or signa.get("weeklyDirection"))

    return SignaSignal(
        symbol=symbol.upper(),
        ok=bool(payload.get("ok", True)),
        grade=grade,
        score=score,
        daily_direction=daily_direction,
        weekly_direction=weekly_direction,
        action=signa.get("action"),
        confidence=_float_or_none(engine.get("confidence") or signa.get("conviction")),
        risk_rating=signa.get("riskRating") or signa.get("risk_rating"),
        raw=payload,
    )


def enrich_payload_with_signa(payload: Any, config: Any, client: SignaClient | None = None) -> SignaSignal | None:
    if not getattr(config, "signa_api_enabled", False):
        return None
    if all(
        getattr(payload, field, None) is not None
        for field in ("signa_grade", "signa_score", "signa_daily_direction")
    ):
        return None

    instrument = _normalize_instrument(getattr(payload, "ticker", ""))
    symbol_map = getattr(config, "signa_symbol_map", {}) or {}
    symbol = symbol_map.get(instrument, instrument)
    signa_client = client or SignaClient(
        base_url=getattr(config, "signa_base_url", "https://app.getsigna.ai"),
        timeout=float(getattr(config, "signa_timeout_seconds", 3.0) or 3.0),
    )
    signal = signa_client.fetch_signal(symbol)
    if not signal.ok:
        return signal

    fields = signal.to_payload_fields()
    for field, value in fields.items():
        if getattr(payload, field, None) is None and value is not None:
            setattr(payload, field, value)
    return signal


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_grade(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().upper()
    if not raw:
        return None
    return raw[0] if raw[0] in {"A", "B", "C", "D", "F"} else raw


def _normalize_direction(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().upper()
    if raw in {"LONG", "BUY", "BULL", "BULLISH", "UP"}:
        return "UP"
    if raw in {"SHORT", "SELL", "BEAR", "BEARISH", "DOWN"}:
        return "DOWN"
    if raw in {"NEUTRAL", "SIDEWAYS", "FLAT"}:
        return "NEUTRAL"
    return raw or None


def _normalize_instrument(ticker: str) -> str:
    upper = (ticker or "").split(":")[-1].upper().strip()
    for root in ("MNQ", "MES", "MGC", "MCL", "NQ", "ES"):
        if upper.startswith(root):
            return root
    return upper.rstrip("!1234567890HMUZ")
