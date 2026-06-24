"""Read-only GEXsniper API client and adapters.

GEXsniper publishes real-time dealer-mechanics analytics (gamma exposure, walls,
flip point, regime) for index/ETF tickers. The trading engine already models a
``GEXContext`` (see ``context.market_context``) and gates on it via
``strategy.gex_gate``; today that context is hand-fed through the TradingView
alert payload. This client lets the same context be populated from a live source.

The engine treats GEX as optional context. Missing credentials, timeouts, bad
responses, or unsupported tickers return a neutral ``ok=False`` result instead of
blocking the webhook pipeline. NOTHING here mutates ``state.gex`` or the gate by
itself — wiring into the live path is a separate, explicit step.

Field mapping (GEXsniper ``GET /v1/context`` -> engine ``GEXContext``):
    flipPoint            -> gex_flip
    topCallWall.strike   -> call_wall
    bottomPutWall.strike -> put_wall
    regime               -> gex_regime  ("positive" | "negative" | "flip_zone")
    sign(netDEX)         -> delta_bias  ("bullish" | "bearish" | "neutral")

Fields the engine models but GEXsniper does not provide (hvl, max_pain, ghost,
mid_upper/lower, vol_trigger_up/down) are left as ``None`` rather than guessed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

# GEXsniper coverage is index/ETF only. Our micro futures track these underlyings:
#   MNQ/NQ -> NDX (Nasdaq-100),  MES/ES -> SPX (S&P 500).
# Index gamma is not the future's price exactly (basis, weighting) but maps cleanly
# for regime + structural levels, which is all the gate uses.
DEFAULT_SYMBOL_MAP = {"MNQ": "NDX", "NQ": "NDX", "MES": "SPX", "ES": "SPX"}


@dataclass(frozen=True)
class GexContext:
    """Parsed GEXsniper context snapshot. ``raw`` keeps the untouched payload."""

    ticker: str
    ok: bool
    spot_price: float | None = None
    net_gex: float | None = None
    flip_point: float | None = None
    dist_to_flip: float | None = None
    regime: str | None = None
    regime_label: str | None = None
    call_wall: float | None = None
    call_wall_gex: float | None = None
    put_wall: float | None = None
    put_wall_gex: float | None = None
    net_dex: float | None = None
    delta_bias: str | None = None
    chex_regime: str | None = None
    snapshot_age_ms: int | None = None
    error: str | None = None
    raw: dict[str, Any] | None = None

    def to_gex_context_fields(self) -> dict[str, Any]:
        """Map onto ``context.market_context.GEXContext`` field names.

        Drop-in for populating ``state.gex`` when/if we wire this to the live gate.
        Only the fields GEXsniper actually provides are returned; everything the
        engine models but the API lacks stays unset (caller keeps existing/None).
        """
        return {
            "gex_flip": self.flip_point,
            "call_wall": self.call_wall,
            "put_wall": self.put_wall,
            "gex_regime": self.regime,
            "delta_bias": self.delta_bias,
        }

    def to_dict(self) -> dict[str, Any]:
        """Compact observability record for the journal."""
        return {
            "ticker": self.ticker,
            "ok": self.ok,
            "spot_price": self.spot_price,
            "net_gex": self.net_gex,
            "flip_point": self.flip_point,
            "dist_to_flip": self.dist_to_flip,
            "regime": self.regime,
            "regime_label": self.regime_label,
            "call_wall": self.call_wall,
            "call_wall_gex": self.call_wall_gex,
            "put_wall": self.put_wall,
            "put_wall_gex": self.put_wall_gex,
            "net_dex": self.net_dex,
            "delta_bias": self.delta_bias,
            "chex_regime": self.chex_regime,
            "snapshot_age_ms": self.snapshot_age_ms,
            "error": self.error,
        }


class GexClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.gexsniper.com/v1",
        timeout: float = 3.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else os.getenv("GEX_API_KEY", "")).strip()
        self.base_url = (base_url or "https://api.gexsniper.com/v1").rstrip("/")
        self.timeout = timeout
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def fetch_context(self, ticker: str) -> GexContext:
        ticker = (ticker or "").strip().upper()
        if not ticker:
            return GexContext(ticker=ticker, ok=False, error="missing_ticker")
        if not self.configured:
            return GexContext(ticker=ticker, ok=False, error="missing_api_key")

        close_client = False
        client = self._client
        if client is None:
            client = httpx.Client(base_url=self.base_url, timeout=self.timeout, follow_redirects=True)
            close_client = True
        try:
            response = client.get(
                "/context",
                params={"ticker": ticker},
                headers={"X-API-Key": self.api_key},
            )
            response.raise_for_status()
            payload = response.json()
            return parse_gex_context(ticker=ticker, payload=payload)
        except httpx.HTTPStatusError as exc:
            return GexContext(ticker=ticker, ok=False, error=f"http_{exc.response.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            return GexContext(ticker=ticker, ok=False, error=exc.__class__.__name__)
        finally:
            if close_client:
                client.close()


def parse_gex_context(ticker: str, payload: dict[str, Any]) -> GexContext:
    payload = _dict(payload)
    if not payload.get("success", True):
        return GexContext(ticker=ticker.upper(), ok=False, error="api_unsuccessful", raw=payload)

    call_wall = _dict(payload.get("topCallWall"))
    put_wall = _dict(payload.get("bottomPutWall"))

    return GexContext(
        ticker=str(payload.get("ticker") or ticker).upper(),
        ok=True,
        spot_price=_float_or_none(payload.get("spotPrice")),
        net_gex=_float_or_none(payload.get("netGEX")),
        flip_point=_float_or_none(payload.get("flipPoint")),
        dist_to_flip=_float_or_none(payload.get("distToFlip")),
        regime=_normalize_regime(payload.get("regime")),
        regime_label=_str_or_none(payload.get("regimeLabel")),
        call_wall=_float_or_none(call_wall.get("strike")),
        call_wall_gex=_float_or_none(call_wall.get("gex")),
        put_wall=_float_or_none(put_wall.get("strike")),
        put_wall_gex=_float_or_none(put_wall.get("gex")),
        net_dex=_float_or_none(payload.get("netDEX")),
        delta_bias=_delta_bias(payload.get("netDEX")),
        chex_regime=_str_or_none(payload.get("chexRegime")),
        snapshot_age_ms=_int_or_none(payload.get("snapshotAge")),
        raw=payload,
    )


def observe_gex(instrument: str, config: Any, client: GexClient | None = None) -> GexContext | None:
    """Fetch GEX context for a futures instrument's mapped index.

    Returns ``None`` when the integration is disabled or the instrument has no
    mapping; otherwise a ``GexContext`` (which may carry ``ok=False`` on failure,
    so callers can journal the reason). Pure read — never mutates ``state``.
    """
    if not getattr(config, "gex_api_enabled", False):
        return None

    instrument = _normalize_instrument(instrument)
    symbol_map = getattr(config, "gex_symbol_map", None) or DEFAULT_SYMBOL_MAP
    ticker = symbol_map.get(instrument)
    if not ticker:
        return None

    gex_client = client or GexClient(
        base_url=getattr(config, "gex_base_url", "https://api.gexsniper.com/v1"),
        timeout=float(getattr(config, "gex_timeout_seconds", 3.0) or 3.0),
    )
    return gex_client.fetch_context(ticker)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_regime(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"positive", "negative", "flip_zone"}:
        return raw
    return raw or None


def _delta_bias(net_dex: Any) -> str | None:
    value = _float_or_none(net_dex)
    if value is None:
        return None
    if value > 0:
        return "bullish"
    if value < 0:
        return "bearish"
    return "neutral"


def _normalize_instrument(ticker: str) -> str:
    upper = (ticker or "").split(":")[-1].upper().strip()
    for root in ("MNQ", "MES", "MGC", "MCL", "NQ", "ES"):
        if upper.startswith(root):
            return root
    return upper.rstrip("!1234567890HMUZ")
