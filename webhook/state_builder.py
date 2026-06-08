"""
webhook/state_builder.py

Converts an AlertPayload (TradingView webhook body) into a MarketState
that the existing DecisionEngine + RiskEngine pipeline consumes.

Responsibilities:
- Parse/normalize timestamps (ISO 8601 or Unix ms)
- Normalize ticker symbols  (e.g. "MNQ1!" → "MNQ")
- Auto-detect trading session from ET timestamp when not explicitly provided
- Derive price_vs_vwap / price_vs_pdh / price_vs_pdl when absent
- Apply safe defaults for missing optional context so the broker-interface
  types are always satisfied (strategies handle None gracefully)
"""

from __future__ import annotations

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

from context.market_context import (
    MarketState,
    GEXContext,
    HTFContext,
    ICCContext,
    KeyLevels,
    OHLCData,
    ORBData,
    PreviousDayData,
    PriceData,
    SignaContext,
    SupplyDemandData,
    TrendData,
    VWAPData,
    VolumeData,
)
from context.trend import classify_trend, has_ema_inputs
from strategy.strat_classifier import StratContext, classify_from_ohlc, classify_sequence
from webhook.payload import AlertPayload

# ─── Constants ────────────────────────────────────────────────────────────────

_ET = ZoneInfo("America/New_York")
_UTC = ZoneInfo("UTC")

# Root ticker symbols the system trades, sorted longest-first.
_KNOWN_INSTRUMENTS: tuple[str, ...] = tuple(
    sorted(("MNQ", "MES", "MGC", "MCL"), key=len, reverse=True)
)

# A real futures contract suffix: a CME month code (F G H J K M N Q U V X Z)
# followed by 1-4 year digits, e.g. "M6" or "U2026". This is how we tell a
# contract symbol (MESU2026) from a stock that merely shares a leading substring
# (ESTC, NQXX) — substring matching would route those to futures by mistake.
_CONTRACT_SUFFIX = re.compile(r"^[FGHJKMNQUVXZ]\d{1,4}$")


# ─── Normalisation helpers ─────────────────────────────────────────────────────

def futures_root(
    ticker: str | None, roots: tuple[str, ...] = _KNOWN_INSTRUMENTS
) -> str | None:
    """Canonical futures root for `ticker`, or None if it isn't one of `roots`.

    Matches a root EXACTLY after stripping the exchange prefix and either the
    continuous ('1!'/'!') or a month+year contract suffix. Substring matching is
    deliberately avoided — a stock like ESTC or NQXX must never route to futures.

    Examples:
        "MNQ1!"          → "MNQ"
        "MNQU2026"       → "MNQ"
        "CME_MINI:MNQ1!" → "MNQ"
        "MES"            → "MES"
        "ESTC"           → None   (Elastic stock, not the ES future)
        "AAPL"           → None
    """
    if not ticker:
        return None
    sym = ticker.split(":")[-1].upper().strip()
    if sym.endswith("1!"):
        sym = sym[:-2]
    sym = sym.rstrip("!")
    for root in sorted(roots, key=len, reverse=True):  # longest-first
        if sym == root:
            return root
        if sym.startswith(root) and _CONTRACT_SUFFIX.match(sym[len(root):]):
            return root
    return None


def normalize_instrument(ticker: str) -> str:
    """Convert a TradingView ticker to the canonical instrument root.

    Returns the matched root for a known futures contract; otherwise the
    exchange-stripped upper symbol as-is, which the RiskEngine then rejects.
    """
    root = futures_root(ticker)
    if root:
        return root
    return ticker.split(":")[-1].upper()


def parse_timestamp(value: str) -> datetime:
    """
    Accept either an ISO 8601 string or a Unix timestamp (seconds or ms).

    TradingView's {{time}} placeholder emits Unix ms; Pine Script's
    str.tostring(time) also emits ms.  A formatted ISO string is accepted too.
    """
    stripped = value.strip()
    if stripped.lstrip("-").isdigit():
        ts_int = int(stripped)
        if ts_int > 1_000_000_000_000:   # milliseconds
            ts_int //= 1000
        return datetime.fromtimestamp(ts_int, tz=_UTC)
    return datetime.fromisoformat(stripped.replace("Z", "+00:00"))


def detect_session(ts: datetime) -> str:
    """
    Map a UTC datetime to a session name using ET market hours.

    Full coverage of the CME equity-index session (Sun 18:00 → Fri 17:00 ET,
    with the daily 17:00–18:00 ET maintenance halt). Every hour the market is
    open maps to a tradeable session; only the maintenance halt is off_hours:

      Asian:    18:00–02:59 ET  (overnight, from the daily reopen)
      London:   03:00–09:29 ET  (absorbs the old 08:30–09:30 pre-open gap)
      New York: 09:30–16:59 ET  (through to the maintenance halt)
      17:00–17:59 ET maintenance halt → "off_hours" (market closed; no bars)

    Opening the *hours* does not open the floodgates — trade COUNT is still
    bounded by daily_limits (max 3/day) and per_session_limits.
    """
    et_time = ts.astimezone(_ET).time()
    if et_time >= time(18, 0) or et_time < time(3, 0):
        return "asian"
    if time(3, 0) <= et_time < time(9, 30):
        return "london"
    if time(9, 30) <= et_time < time(17, 0):
        return "new_york"
    return "off_hours"


def derive_orb_status(close: float, orb_high: float | None, orb_low: float | None) -> str:
    """
    Derive a conservative ORB status when Pine omits orb_status.

    TradingView does not have to send session; session can be auto-detected.
    Likewise, if ORB levels are present but status is missing, close vs levels
    is enough to avoid persisting a permanently undefined ORB context.
    """
    if orb_high is None or orb_low is None:
        return "undefined"
    if close > orb_high:
        return "above"
    if close < orb_low:
        return "below"
    return "inside"



def _payload_to_dict(payload: AlertPayload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


# ─── Main builder ─────────────────────────────────────────────────────────────

def _build_key_levels(payload: AlertPayload) -> "KeyLevels | None":
    """Build KeyLevels from payload. Returns None if no level data present."""
    has_data = any(v is not None for v in (
        payload.hod, payload.lod,
        payload.prev_week_high, payload.prev_week_low,
        payload.ema_9, payload.ema_21, payload.ema_55, payload.ema_200,
        payload.ema_9_above_21,
    ))
    if not has_data:
        return None
    # Derive flags from values when Pine doesn't send them explicitly
    ema_9_above_21 = payload.ema_9_above_21
    if ema_9_above_21 is None and payload.ema_9 is not None and payload.ema_21 is not None:
        ema_9_above_21 = payload.ema_9 > payload.ema_21
    price_above_ema_55 = (payload.close > payload.ema_55) if payload.ema_55 is not None else None
    price_above_ema_200 = (payload.close > payload.ema_200) if payload.ema_200 is not None else None
    return KeyLevels(
        hod=payload.hod,
        lod=payload.lod,
        prev_week_high=payload.prev_week_high,
        prev_week_low=payload.prev_week_low,
        ema_9=payload.ema_9,
        ema_21=payload.ema_21,
        ema_55=payload.ema_55,
        ema_200=payload.ema_200,
        ema_9_above_21=ema_9_above_21,
        price_above_ema_55=price_above_ema_55,
        price_above_ema_200=price_above_ema_200,
    )


def _build_sd(payload: AlertPayload) -> "SupplyDemandData | None":
    """Resolve S&D fields, accepting both naming conventions from TradingView."""
    s_top    = payload.supply_top    or payload.supply_zone_high
    s_bottom = payload.supply_bottom or payload.supply_zone_low
    d_top    = payload.demand_top    or payload.demand_zone_high
    d_bottom = payload.demand_bottom or payload.demand_zone_low
    if not any(v is not None for v in (s_top, s_bottom, d_top, d_bottom)):
        return None
    return SupplyDemandData(
        supply_top=s_top,
        supply_bottom=s_bottom,
        supply_wavg=payload.supply_wavg,
        demand_top=d_top,
        demand_bottom=d_bottom,
        demand_wavg=payload.demand_wavg,
    )


def _build_htf(payload: AlertPayload) -> "HTFContext | None":
    """Build optional higher-timeframe Strat/FTFC context."""
    if not any(v is not None for v in (
        payload.daily_bar_type,
        payload.daily_direction,
        payload.four_hour_bar_type,
        payload.four_hour_direction,
        payload.one_hour_bar_type,
        payload.one_hour_direction,
        payload.ftfc_direction,
        payload.ftfc_aligned,
    )):
        return None
    return HTFContext(
        daily_bar_type=payload.daily_bar_type,
        daily_direction=payload.daily_direction,
        four_hour_bar_type=payload.four_hour_bar_type,
        four_hour_direction=payload.four_hour_direction,
        one_hour_bar_type=payload.one_hour_bar_type,
        one_hour_direction=payload.one_hour_direction,
        ftfc_direction=payload.ftfc_direction,
        ftfc_aligned=payload.ftfc_aligned,
    )


def build_market_state(payload: AlertPayload) -> MarketState:
    """
    Convert an AlertPayload to a MarketState ready for the decision pipeline.
    Missing optional fields are filled with safe defaults so downstream
    dataclasses are never handed None for typed float fields.
    """
    ts = parse_timestamp(payload.timestamp)
    instrument = normalize_instrument(payload.ticker)
    # Always use the server-side clock — Pine mislabels Sunday 18:00 ET reopen
    # as "off_hours" because its session() function doesn't cover the Sun CME
    # reopen as Asian. detect_session() is authoritative.
    session = detect_session(ts)

    # ── Derived string flags ─────────────────────────────────────────────────
    # Missing structural levels must FAIL CLOSED — never silently substitute the
    # current bar. A substituted level reads downstream as a real one: it can
    # satisfy a directional gate or become a fabricated structural stop. When a
    # level is absent we keep a harmless placeholder for the typed-float
    # dataclass field but set the derived comparison to "undefined" — a neutral
    # token every strategy gate treats as not-satisfied, so dependent setups
    # don't fire. The placeholder is unreachable: all level arithmetic sits
    # behind an "above"/"below" guard that "undefined" fails.
    if payload.vwap is not None:
        vwap_value = payload.vwap
        price_vs_vwap = (
            "above" if payload.close > vwap_value else
            "below" if payload.close < vwap_value else
            "at"
        )
    else:
        vwap_value = payload.close   # placeholder; never used while undefined
        price_vs_vwap = "undefined"

    if payload.previous_day_high is not None:
        pdh = payload.previous_day_high
        price_vs_pdh = payload.price_vs_pdh or (
            "above" if payload.close > pdh else
            "below" if payload.close < pdh else "at"
        )
    else:
        pdh = payload.close          # placeholder; never used while undefined
        price_vs_pdh = payload.price_vs_pdh or "undefined"

    if payload.previous_day_low is not None:
        pdl = payload.previous_day_low
        price_vs_pdl = payload.price_vs_pdl or (
            "above" if payload.close > pdl else
            "below" if payload.close < pdl else "at"
        )
    else:
        pdl = payload.close          # placeholder; never used while undefined
        price_vs_pdl = payload.price_vs_pdl or "undefined"

    pdc = payload.previous_day_close if payload.previous_day_close is not None else payload.close

    # ── ORB levels — route by session ────────────────────────────────────────
    # London session uses the London ORB (Pine tracks it separately).
    # NY session uses the standard NY ORB.
    # When the relevant ORB hasn't been established yet, status stays
    # "undefined" and strategies that check specific statuses won't fire.
    if session == "london" and payload.london_orb_high is not None:
        orb_h = payload.london_orb_high
        orb_l = payload.london_orb_low
        orb_status_val = payload.london_orb_status or derive_orb_status(
            payload.close, orb_h, orb_l
        )
    elif session == "london":
        # London ORB is the only relevant ORB during London. If Pine has not
        # established it yet, keep status undefined even when NY ORB fields are
        # present on the payload.
        orb_h = payload.high
        orb_l = payload.low
        orb_status_val = "undefined"
    elif payload.orb_high is not None:
        orb_h = payload.orb_high
        orb_l = payload.orb_low if payload.orb_low is not None else payload.low
        orb_status_val = payload.orb_status or derive_orb_status(payload.close, orb_h, orb_l)
    else:
        # NY ORB not yet established — fail closed (mirror the London branch).
        # Placeholder levels are never used while status is "undefined".
        orb_h = payload.high
        orb_l = payload.low
        orb_status_val = payload.orb_status or "undefined"

    strat = build_strat_context(payload)

    # ── Trend: single source of truth ────────────────────────────────────────
    # Compute trend from the EMAs Pine already sends, using the SAME scale-free
    # EMA-stack definition the replay validates on (context.trend.classify_trend).
    # This deliberately overrides Pine's `trend_strength` (an EMA %-separation
    # metric whose STRONG threshold is unreachable on 15m micros, which silently
    # blocked every live entry). Fall back to the payload-provided trend only
    # when the EMA inputs are absent.
    if has_ema_inputs(payload.ema_9, payload.ema_21, payload.ema_55):
        trend_direction, trend_strength = classify_trend(
            payload.close, payload.ema_9, payload.ema_21, payload.ema_55
        )
    else:
        trend_direction = payload.trend_direction
        trend_strength = payload.trend_strength

    return MarketState(
        timestamp=ts,
        instrument=instrument,
        session=session,
        price=PriceData(last=payload.close, bid=payload.close, ask=payload.close),
        ohlc=OHLCData(
            open=payload.open,
            high=payload.high,
            low=payload.low,
            close=payload.close,
            timeframe=payload.timeframe,
            bar_start=payload.timestamp,
        ),
        vwap=VWAPData(
            value=vwap_value,
            price_vs_vwap=price_vs_vwap,
            reclaimed=payload.vwap_reclaimed,
            holding=price_vs_vwap in ("above", "below"),
        ),
        orb=ORBData(
            high=orb_h,
            low=orb_l,
            timeframe_minutes=15,
            status=orb_status_val,
        ),
        previous_day=PreviousDayData(
            high=pdh,
            low=pdl,
            close=pdc,
            price_vs_pdh=price_vs_pdh,
            price_vs_pdl=price_vs_pdl,
        ),
        volume=VolumeData(
            current_bar=payload.volume,
            avg_bar=max(payload.avg_volume, 1),
            relative=payload.volume / max(payload.avg_volume, 1),
        ),
        market_condition=payload.market_condition,
        trend=TrendData(
            direction=trend_direction,
            strength=trend_strength,
        ),
        strat=strat,
        gex=GEXContext(
            gex_flip=payload.gex_flip,
            call_wall=payload.call_wall,
            put_wall=payload.put_wall,
            hvl=payload.hvl,
            max_pain=payload.max_pain,
            ghost=payload.ghost,
            mid_upper=payload.mid_upper,
            mid_lower=payload.mid_lower,
            vol_trigger_up=payload.vol_trigger_up,
            vol_trigger_down=payload.vol_trigger_down,
            gex_regime=payload.gex_regime,
            delta_bias=payload.delta_bias,
        ),
        signa=SignaContext(
            grade=payload.signa_grade,
            score=payload.signa_score,
            daily_direction=payload.signa_daily_direction,
            weekly_direction=payload.signa_weekly_direction,
        ),
        icc=ICCContext(
            phase=payload.icc_phase,
            entry_signal=payload.icc_entry_signal,
            indication_type=payload.icc_indication_type,
            indication_level=payload.icc_indication_level,
            last_swing_high=payload.icc_last_swing_high,
            last_swing_low=payload.icc_last_swing_low,
            correction_high=payload.icc_correction_high,
            correction_low=payload.icc_correction_low,
            stop_loss=payload.icc_stop_loss,
            tp1=payload.icc_tp1,
            tp2=payload.icc_tp2,
            htf_phase=payload.icc_htf_phase,
        ),
        htf=_build_htf(payload),
        sd=_build_sd(payload),
        key_levels=_build_key_levels(payload),
        raw=_payload_to_dict(payload),
    )


def build_strat_context(payload: AlertPayload) -> StratContext:
    """Use explicit Strat fields first, otherwise classify from optional OHLC history."""
    if any(
        value is not None
        for value in (
            payload.current_bar_type,
            payload.previous_bar_type,
            payload.two_bars_back_type,
            payload.strat_sequence,
            payload.strat_trigger,
            payload.strat_direction,
        )
    ):
        classified = classify_sequence(
            payload.two_bars_back_type,
            payload.previous_bar_type,
            payload.current_bar_type,
        )
        return StratContext(
            current_bar_type=payload.current_bar_type or classified.current_bar_type,
            previous_bar_type=payload.previous_bar_type or classified.previous_bar_type,
            two_bars_back_type=payload.two_bars_back_type or classified.two_bars_back_type,
            strat_sequence=payload.strat_sequence or classified.strat_sequence,
            strat_trigger=payload.strat_trigger or classified.strat_trigger,
            strat_direction=payload.strat_direction or classified.strat_direction,
        )

    return classify_from_ohlc(
        current_high=payload.high,
        current_low=payload.low,
        previous_high=payload.previous_bar_high,
        previous_low=payload.previous_bar_low,
        two_bars_back_high=payload.two_bars_back_high,
        two_bars_back_low=payload.two_bars_back_low,
    )
