"""Read-only Signa API client and adapters.

**Signa is OBSERVATIONAL METADATA ONLY.** Its presence, absence, grade, score,
direction, staleness, or internal disagreement must never approve, reject,
block, or alter an authoritative setup, entry, stop, target, or trade status.
The system-owned decision is made from raw market data, price action, setup
detection, contract quality, and risk — and must come out identical whether
Signa is present, absent, neutral, or self-contradictory.

Everything here therefore *records* and never *decides*. Missing credentials,
timeouts, bad responses, or unsupported symbols return a neutral result instead
of blocking the pipeline.

Three independent response surfaces
-----------------------------------
The API returns three blocks that routinely disagree with each other:

    engine  nightly 30+ model consensus; matches the in-app Action Card.
            TIMEFRAME-INVARIANT — identical at every ``tf``.
    signa   undocumented provenance; varies by timeframe.
    data    live single-pass technical analysis; varies by timeframe.

They are preserved separately, with provenance. We do not pick a winner, do not
average them, and do not fail on disagreement — disagreement is recorded as
``signa_grade_conflict`` / ``surfaces_disagree`` for later outcome analysis.

Because ``engine`` never varies with ``tf``, labelling an ``engine`` value as a
"4H" or "1H" read would be false. ``SignaSurface.timeframe_meaningful`` marks
which surfaces a timeframe label actually applies to.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

# The API's timeframe parameter is `tf`. `timeframe`, `interval`, `resolution`,
# and `symbol` are silently ignored by the server, which then falls back to 1d.
# Verified live 2026-07-29 against api_version v1 / engine_version v3.1.
TIMEFRAME_PARAM = "tf"
SYMBOL_PARAM = "sym"

SUPPORTED_TIMEFRAMES = ("1d", "4h", "1h", "30m", "15m", "5m", "1w", "1m", "1mo")

# `engine` is a once-nightly consensus and is byte-identical at every tf.
TIMEFRAME_INVARIANT_SURFACES = frozenset({"engine"})

_UP = {"LONG", "BUY", "BULL", "BULLISH", "UP"}
_DOWN = {"SHORT", "SELL", "BEAR", "BEARISH", "DOWN"}
_NEUTRAL = {"NEUTRAL", "SIDEWAYS", "FLAT", "HOLD"}
_WAIT = {"WAIT"}


@dataclass(frozen=True)
class SignaTrigger:
    name: Optional[str] = None
    description: Optional[str] = None
    weight: Optional[float] = None


@dataclass(frozen=True)
class SignaSurface:
    """One response block, preserved with provenance.

    Distinct numeric fields stay distinct. ``score``, ``confidence``, and
    ``conviction`` are three different measurements from the vendor and are
    never merged into a single "signa score".
    """

    block: str                       # "engine" | "signa" | "data"
    timeframe: Optional[str] = None  # the tf the SERVER echoed, not what we asked
    timeframe_meaningful: bool = True  # False for timeframe-invariant surfaces
    present: bool = False

    grade: Optional[str] = None          # RAW — "A+" preserved verbatim
    score: Optional[float] = None        # engine.score only
    confidence: Optional[float] = None   # engine.confidence / data.confidence
    conviction: Optional[float] = None   # signa.conviction — NOT score
    direction_raw: Optional[str] = None
    direction: Optional[str] = None      # UP | DOWN | NEUTRAL | WAIT | UNKNOWN
    action: Optional[str] = None
    bias: Optional[str] = None
    tier: Optional[str] = None
    stage: Optional[int] = None
    stage_description: Optional[str] = None
    stage_strength: Optional[float] = None
    flow_score: Optional[float] = None
    volume_grade: Optional[str] = None
    regime_class: Optional[str] = None
    risk_rating: Optional[str] = None
    risk_score: Optional[float] = None
    overall_score: Optional[float] = None
    alpha_event: Optional[bool] = None
    conflict_detected: Optional[bool] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[float] = None
    triggers: tuple[SignaTrigger, ...] = ()
    patterns: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    run_at: Optional[str] = None

    @property
    def grade_letter(self) -> Optional[str]:
        """Convenience only. Never overwrites `grade`; "A+" stays "A+" there."""
        if not self.grade:
            return None
        head = self.grade.strip().upper()[:1]
        return head if head in {"A", "B", "C", "D", "F"} else None

    @property
    def is_plus(self) -> bool:
        return bool(self.grade) and self.grade.strip().upper().endswith("+")


@dataclass(frozen=True)
class SignaReading:
    """One symbol at one requested timeframe. Pure observation."""

    symbol: str
    ok: bool = False
    requested_timeframe: Optional[str] = None
    echoed_timeframe: Optional[str] = None
    surfaces: dict[str, SignaSurface] = field(default_factory=dict)
    signal_timestamp: Optional[str] = None   # engine run time, NOT the live data
    generated_at: Optional[str] = None
    age_seconds: Optional[float] = None
    cross_surface_conflict: Any = None       # raw passthrough; semantics unproven
    options_flow: dict[str, Any] = field(default_factory=dict)
    confidence_pillars: dict[str, Any] = field(default_factory=dict)
    trade_plan: Any = None
    engine_version: Optional[str] = None
    error: Optional[str] = None
    raw: Optional[dict[str, Any]] = None

    # ---- derived observations. None of these gate anything. ----

    @property
    def timeframe_mismatch(self) -> bool:
        """True when the server did not honour the tf we asked for. Recorded so a
        silently-substituted timeframe cannot masquerade as the requested one."""
        if not self.requested_timeframe or not self.echoed_timeframe:
            return False
        return self.requested_timeframe.lower() != self.echoed_timeframe.lower()

    @property
    def grades(self) -> dict[str, str]:
        return {
            name: s.grade
            for name, s in self.surfaces.items()
            if s.present and s.grade
        }

    @property
    def signa_grade_conflict(self) -> bool:
        """Two surfaces report different grades. Recorded for outcome analysis;
        deliberately NOT a block and NOT a tiebreak."""
        letters = {g.strip().upper() for g in self.grades.values()}
        return len(letters) > 1

    @property
    def directions(self) -> dict[str, str]:
        return {
            name: s.direction
            for name, s in self.surfaces.items()
            if s.present and s.direction
        }

    @property
    def surfaces_disagree(self) -> bool:
        actionable = {d for d in self.directions.values() if d in {"UP", "DOWN"}}
        has_standdown = any(
            d in {"WAIT", "NEUTRAL"} for d in self.directions.values()
        )
        return len(actionable) > 1 or (bool(actionable) and has_standdown)

    def is_stale(self, max_age_seconds: float) -> Optional[bool]:
        """Observation only. None when age is unknown — an unknown age is not
        evidence of freshness, and is never resolved in either direction here."""
        if self.age_seconds is None:
            return None
        return self.age_seconds > max_age_seconds

    def to_observation(self) -> dict[str, Any]:
        """Flat, journal-friendly record. Every key is metadata; no key here may
        be read as a verdict."""
        out: dict[str, Any] = {
            "signa_ok": self.ok,
            "signa_symbol": self.symbol,
            "signa_requested_timeframe": self.requested_timeframe,
            "signa_echoed_timeframe": self.echoed_timeframe,
            "signa_timeframe_mismatch": self.timeframe_mismatch,
            "signa_signal_timestamp": self.signal_timestamp,
            "signa_age_seconds": self.age_seconds,
            "signa_grade_conflict": self.signa_grade_conflict,
            "signa_surfaces_disagree": self.surfaces_disagree,
            "signa_cross_surface_conflict": self.cross_surface_conflict,
            "signa_error": self.error,
        }
        for name, s in self.surfaces.items():
            if not s.present:
                continue
            out[f"signa_{name}_grade"] = s.grade
            out[f"signa_{name}_direction"] = s.direction
            out[f"signa_{name}_timeframe_meaningful"] = s.timeframe_meaningful
            if s.score is not None:
                out[f"signa_{name}_score"] = s.score
            if s.confidence is not None:
                out[f"signa_{name}_confidence"] = s.confidence
            if s.conviction is not None:
                out[f"signa_{name}_conviction"] = s.conviction
            if s.tier is not None:
                out[f"signa_{name}_tier"] = s.tier
        return out


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

    def fetch_reading(self, symbol: str, timeframe: str = "1d") -> SignaReading:
        symbol = (symbol or "").strip().upper()
        if not symbol:
            return SignaReading(symbol=symbol, ok=False, error="missing_symbol")
        if not self.configured:
            return SignaReading(
                symbol=symbol, ok=False, requested_timeframe=timeframe,
                error="missing_api_key",
            )

        close_client = False
        client = self._client
        if client is None:
            client = httpx.Client(base_url=self.base_url, timeout=self.timeout, follow_redirects=True)
            close_client = True
        try:
            response = client.get(
                "/api/v1/signal",
                params={SYMBOL_PARAM: symbol, TIMEFRAME_PARAM: timeframe},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            payload = response.json()
            return parse_signa_reading(
                symbol=symbol, payload=payload, requested_timeframe=timeframe
            )
        except httpx.HTTPStatusError as exc:
            return SignaReading(
                symbol=symbol, ok=False, requested_timeframe=timeframe,
                error=f"http_{exc.response.status_code}",
            )
        except (httpx.HTTPError, ValueError) as exc:
            return SignaReading(
                symbol=symbol, ok=False, requested_timeframe=timeframe,
                error=exc.__class__.__name__,
            )
        finally:
            if close_client:
                client.close()

    def fetch_signal(self, symbol: str, timeframe: str = "1d") -> "SignaSignal":
        """LEGACY futures-facing fetch. See the frozen-API note at the bottom."""
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
                params={SYMBOL_PARAM: symbol, TIMEFRAME_PARAM: timeframe},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            response.raise_for_status()
            return parse_signa_signal(symbol=symbol, payload=response.json())
        except httpx.HTTPStatusError as exc:
            return SignaSignal(symbol=symbol, ok=False, error=f"http_{exc.response.status_code}")
        except (httpx.HTTPError, ValueError) as exc:
            return SignaSignal(symbol=symbol, ok=False, error=exc.__class__.__name__)
        finally:
            if close_client:
                client.close()

    def fetch_multi_timeframe(
        self, symbol: str, timeframes: tuple[str, ...] = ("1d", "4h", "1h")
    ) -> dict[str, SignaReading]:
        """One request per timeframe — the API has no multi-tf endpoint.

        Note the `engine` surface is identical in every result; only `signa`
        and `data` actually vary. Callers must not present an `engine` value
        under a 4H/1H label.
        """
        return {tf: self.fetch_reading(symbol, tf) for tf in timeframes}


def parse_signa_reading(
    symbol: str,
    payload: dict[str, Any],
    requested_timeframe: str | None = None,
    now: datetime | None = None,
) -> SignaReading:
    engine = _dict(payload.get("engine"))
    signa = _dict(payload.get("signa"))
    data = _dict(payload.get("data"))
    meta = _dict(payload.get("meta"))

    echoed = _str_or_none(payload.get("timeframe"))
    signal_timestamp = _str_or_none(payload.get("signal_timestamp"))

    surfaces = {
        "engine": SignaSurface(
            block="engine",
            timeframe=echoed,
            # engine is a nightly consensus: a tf label on it would be false.
            timeframe_meaningful=False,
            present=bool(engine),
            grade=_raw_grade(engine.get("grade")),
            score=_float_or_none(engine.get("score")),
            confidence=_float_or_none(engine.get("confidence")),
            direction_raw=_str_or_none(engine.get("direction")),
            direction=_normalize_direction(engine.get("direction")),
            conflict_detected=_bool_or_none(engine.get("conflictDetected")),
            reasons=_str_tuple(engine.get("reasons")),
            run_at=_str_or_none(engine.get("runAt")),
        ),
        "signa": SignaSurface(
            block="signa",
            timeframe=echoed,
            present=bool(signa),
            grade=_raw_grade(signa.get("grade")),
            # conviction is its OWN measurement. It is not a score and it is
            # not a confidence, and must never be copied into either.
            conviction=_float_or_none(signa.get("conviction")),
            direction_raw=_str_or_none(signa.get("action")),
            direction=_normalize_direction(signa.get("action")),
            action=_str_or_none(signa.get("action")),
            stage_strength=_float_or_none(signa.get("stageStrength")),
            flow_score=_float_or_none(signa.get("flowScore")),
            volume_grade=_str_or_none(signa.get("volumeGrade")),
            regime_class=_str_or_none(signa.get("regimeClass")),
            risk_rating=_str_or_none(signa.get("riskRating") or signa.get("risk_rating")),
            alpha_event=_bool_or_none(signa.get("alphaEvent")),
            triggers=_triggers(signa.get("triggers")),
        ),
        "data": SignaSurface(
            block="data",
            timeframe=echoed,
            present=bool(data),
            confidence=_float_or_none(data.get("confidence")),
            direction_raw=_str_or_none(data.get("direction")),
            direction=_normalize_direction(data.get("direction")),
            bias=_str_or_none(data.get("bias")),
            tier=_str_or_none(data.get("tier")),
            stage=_int_or_none(data.get("stage")),
            stage_description=_str_or_none(data.get("stageDescription")),
            risk_score=_float_or_none(data.get("riskScore")),
            overall_score=_float_or_none(data.get("overallScore")),
            entry=_float_or_none(data.get("entry")),
            stop=_float_or_none(data.get("stop")),
            target=_float_or_none(data.get("target")),
            rr=_float_or_none(data.get("rr")),
            triggers=_triggers(data.get("triggers")),
            patterns=_str_tuple(data.get("patterns")),
        ),
    }

    return SignaReading(
        symbol=symbol.upper(),
        ok=bool(payload.get("ok", True)),
        requested_timeframe=requested_timeframe,
        echoed_timeframe=echoed,
        surfaces=surfaces,
        signal_timestamp=signal_timestamp,
        generated_at=_str_or_none(meta.get("generated_at")),
        age_seconds=_age_seconds(signal_timestamp, now),
        cross_surface_conflict=payload.get("crossSurfaceConflict"),
        options_flow=_dict(payload.get("options_flow")),
        confidence_pillars=_dict(payload.get("confidence_pillars")),
        trade_plan=payload.get("trade_plan"),
        engine_version=_str_or_none(payload.get("engine_version")),
        raw=payload,
    )


def observe_payload_with_signa(
    payload: Any, config: Any, client: SignaClient | None = None
) -> SignaReading | None:
    """Attach a Signa OBSERVATION to an alert payload.

    Writes only ``signa_*`` observation fields. It never sets, clears, or
    influences direction, entry, stop, target, setup, or status, and it never
    returns a value the caller is expected to gate on.
    """
    if not getattr(config, "signa_api_enabled", False):
        return None

    instrument = _normalize_instrument(getattr(payload, "ticker", ""))
    symbol_map = getattr(config, "signa_symbol_map", {}) or {}
    symbol = symbol_map.get(instrument, instrument)
    signa_client = client or SignaClient(
        base_url=getattr(config, "signa_base_url", "https://app.getsigna.ai"),
        timeout=float(getattr(config, "signa_timeout_seconds", 3.0) or 3.0),
    )
    reading = signa_client.fetch_reading(symbol)

    for field_name, value in reading.to_observation().items():
        if hasattr(payload, field_name) and getattr(payload, field_name, None) is None:
            setattr(payload, field_name, value)
    return reading


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(v) for v in value if v is not None)


def _triggers(value: Any) -> tuple[SignaTrigger, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    out = []
    for item in value:
        if isinstance(item, dict):
            out.append(
                SignaTrigger(
                    name=_str_or_none(item.get("name")),
                    description=_str_or_none(item.get("description")),
                    weight=_float_or_none(item.get("weight")),
                )
            )
        elif item is not None:
            out.append(SignaTrigger(name=str(item)))
    return tuple(out)


def _raw_grade(value: Any) -> str | None:
    """Preserve the vendor grade EXACTLY. "A+" must survive as "A+" — truncating
    to the first character silently destroys the top tier."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _normalize_direction(value: Any) -> str | None:
    """-> UP | DOWN | NEUTRAL | WAIT | UNKNOWN.

    WAIT is a first-class value, distinct from NEUTRAL and from UNKNOWN: the
    vendor uses it to mean "stand down", and collapsing it into NEUTRAL would
    lose that. An unrecognized value becomes UNKNOWN rather than being passed
    through raw, so no downstream string comparison can accidentally match it.
    """
    if value is None:
        return None
    raw = str(value).strip().upper()
    if not raw:
        return None
    if raw in _UP:
        return "UP"
    if raw in _DOWN:
        return "DOWN"
    if raw in _WAIT:
        return "WAIT"
    if raw in _NEUTRAL:
        return "NEUTRAL"
    return "UNKNOWN"


def _age_seconds(timestamp: str | None, now: datetime | None = None) -> float | None:
    if not timestamp:
        return None
    text = timestamp.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return (reference - parsed).total_seconds()


def _normalize_instrument(ticker: str) -> str:
    upper = (ticker or "").split(":")[-1].upper().strip()
    for root in ("MNQ", "MES", "MGC", "MCL", "NQ", "ES"):
        if upper.startswith(root):
            return root
    return upper.rstrip("!1234567890HMUZ")


# ---------------------------------------------------------------------------
# LEGACY FUTURES-FACING API — FROZEN. DO NOT "IMPROVE".
#
# `webhook/runner.py:2711` feeds this into the futures payload, which becomes
# `state.signa`, which `strategy/signa_gate.py` reads. Any semantic change here
# is a FUTURES behavior change and is out of scope for the options lane work.
#
# In particular this path deliberately KEEPS the lossy `_normalize_grade` that
# truncates "A+" to "A". Fixing it here would flip `strategy/signa_gate.py`
# (which tests `grade in {"A", "B"}`) from PASS to NEUTRAL on an A+ ticker —
# a live futures gating change. The options lane must use `SignaReading` /
# `parse_signa_reading` above, which preserve "A+" verbatim.
#
# The only change made here is the request parameter name (`tf`, not the
# silently-ignored `timeframe`). That is provably behavior-neutral: the server
# ignored `timeframe` and defaulted to 1d, and this path only ever requests 1d.
# ---------------------------------------------------------------------------


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


def parse_signa_signal(symbol: str, payload: dict[str, Any]) -> SignaSignal:
    """LEGACY. Lossy by design — see the frozen-API note above."""
    engine = _dict(payload.get("engine"))
    signa = _dict(payload.get("signa"))
    data = _dict(payload.get("data"))

    grade = _normalize_grade(engine.get("grade") or signa.get("grade"))
    score = _float_or_none(engine.get("score") or signa.get("conviction") or data.get("confidence"))
    daily_direction = _legacy_direction(
        data.get("direction") or engine.get("direction") or signa.get("action")
    )
    weekly_direction = _legacy_direction(data.get("weekly_direction") or signa.get("weeklyDirection"))

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
    """LEGACY futures enrichment. Behavior frozen."""
    if not getattr(config, "signa_api_enabled", False):
        return None
    if all(
        getattr(payload, field_name, None) is not None
        for field_name in ("signa_grade", "signa_score", "signa_daily_direction")
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
    for field_name, value in fields.items():
        if getattr(payload, field_name, None) is None and value is not None:
            setattr(payload, field_name, value)
    return signal


def _normalize_grade(value: Any) -> str | None:
    """LEGACY. Truncates "A+" -> "A". Frozen: futures gating depends on it."""
    if value is None:
        return None
    raw = str(value).strip().upper()
    if not raw:
        return None
    return raw[0] if raw[0] in {"A", "B", "C", "D", "F"} else raw


def _legacy_direction(value: Any) -> str | None:
    """LEGACY. Passes unrecognized values through raw (so "WAIT" stays "WAIT")."""
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
