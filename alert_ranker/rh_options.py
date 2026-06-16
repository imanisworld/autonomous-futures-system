"""Robinhood options advisory evaluation.

This module is intentionally advisory-only. It parses manual setup context,
applies deterministic gates, generates a Robinhood-ready order ticket, and can
shadow-journal the idea. It never connects to Robinhood or any broker API.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any

import httpx

from .scorer import ScoreResult
from .storage import ScanStorage


GOOD_GRADES = {"A+", "A", "B"}


def sample_rh_options_payload() -> dict[str, Any]:
    return {
        "ticker": "SPY",
        "direction": "LONG",
        "contract_type": "CALL",
        "signa_score": 82,
        "signa_grade": "A",
        "signa_daily_direction": "BULLISH",
        "signa_weekly_direction": "BULLISH",
        "gex_regime": "LOW_PINNING",
        "gex_support_wall": 495.0,
        "gex_resistance_wall": 510.0,
        "current_price": 500.0,
        "premium": 2.20,
        "expiry_date": "2026-07-07",
        "dte": 18,
        "strike": 505.0,
        "earnings_date": None,
        "option_volume": 850,
        "open_interest": 12000,
        "nine_ma": 498.5,
        "max_premium_per_contract": 250.0,
        "quantity": 1,
        "max_contracts": 2,
    }


def sample_rh_options_text() -> str:
    return "\n".join(
        [
            "SPY bullish",
            "Signa 82 A",
            "daily bullish weekly bullish",
            "GEX low pinning",
            "support 495 resistance 510",
            "price 500",
            "505C 7/7",
            "premium 2.20",
            "dte 18",
            "no earnings",
            "vol 850",
            "OI 12000",
            "9MA 498.5",
        ]
    )


@dataclass(frozen=True)
class RHOptionsInput:
    ticker: str
    direction: str
    contract_type: str
    signa_score: float
    signa_grade: str
    signa_daily_direction: str
    signa_weekly_direction: str | None
    gex_regime: str
    gex_support_wall: float | None
    gex_resistance_wall: float | None
    current_price: float
    premium: float
    expiry_date: str
    dte: int
    strike: float
    earnings_date: str | None = None
    option_volume: int | None = None
    open_interest: int | None = None
    nine_ma: float | None = None
    max_premium_per_contract: float = 250.0
    quantity: int = 1
    max_contracts: int = 2


class RHAdvisoryBroker:
    """Non-executing adapter stub for a future Robinhood integration."""

    def preview_order(self, ticket: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "status": "ADVISORY_ONLY",
            "note": "Robinhood execution is not implemented; review this ticket manually.",
            "ticket": ticket,
        }

    def submit_order(self, ticket: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "NOT_IMPLEMENTED",
            "note": "This advisory stub never submits live Robinhood orders.",
        }

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {
            "status": "NOT_IMPLEMENTED",
            "note": "This advisory stub never cancels live Robinhood orders.",
        }


def _parse_rh_inputs(body: dict[str, Any]) -> RHOptionsInput:
    required = (
        "ticker",
        "direction",
        "contract_type",
        "signa_score",
        "signa_grade",
        "signa_daily_direction",
        "gex_regime",
        "current_price",
        "premium",
        "expiry_date",
        "dte",
        "strike",
    )
    missing = [name for name in required if body.get(name) in {None, ""}]
    if missing:
        raise ValueError(f"Missing required RH options field(s): {', '.join(missing)}")

    try:
        return RHOptionsInput(
            ticker=str(body["ticker"]).upper().strip(),
            direction=_normalize_direction(body["direction"]),
            contract_type=str(body["contract_type"]).upper().strip(),
            signa_score=float(body["signa_score"]),
            signa_grade=str(body["signa_grade"]).upper().strip(),
            signa_daily_direction=_normalize_signal_direction(body["signa_daily_direction"]),
            signa_weekly_direction=_normalize_signal_direction(body.get("signa_weekly_direction")),
            gex_regime=str(body["gex_regime"]).upper().strip(),
            gex_support_wall=_optional_float(body.get("gex_support_wall")),
            gex_resistance_wall=_optional_float(body.get("gex_resistance_wall")),
            current_price=float(body["current_price"]),
            premium=float(body["premium"]),
            expiry_date=str(body["expiry_date"]).strip(),
            dte=int(body["dte"]),
            strike=float(body["strike"]),
            earnings_date=str(body["earnings_date"]).strip() if body.get("earnings_date") else None,
            option_volume=int(body["option_volume"]) if body.get("option_volume") is not None else None,
            open_interest=int(body["open_interest"]) if body.get("open_interest") is not None else None,
            nine_ma=_optional_float(body.get("nine_ma")),
            max_premium_per_contract=float(body.get("max_premium_per_contract", 250.0)),
            quantity=int(body.get("quantity", 1)),
            max_contracts=int(body.get("max_contracts", 2)),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid RH options input: {exc}") from exc


def parse_messy_rh_options_text(text: str, *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Messy RH options text is required.")
    reference = now or datetime.now(timezone.utc)
    normalized = _squash_text(text)
    upper = normalized.upper()
    extracted: dict[str, Any] = {
        "max_premium_per_contract": 250.0,
        "quantity": 1,
        "max_contracts": 2,
    }

    ticker = _match_group(r"\b([A-Z]{1,5})\s+(?:BULLISH|BEARISH|LONG|SHORT|CALL|PUT)\b", upper)
    if ticker is None:
        ticker = _first_ticker_token(upper)
    if ticker:
        extracted["ticker"] = ticker

    if re.search(r"\b(BULLISH|LONG|CALL)\b", upper):
        extracted["direction"] = "LONG"
    if re.search(r"\b(BEARISH|SHORT|PUT)\b", upper):
        extracted["direction"] = "SHORT"

    contract = re.search(r"\b(\d+(?:\.\d+)?)\s*([CP])\b", upper)
    if contract:
        extracted["strike"] = float(contract.group(1))
        extracted["contract_type"] = "CALL" if contract.group(2) == "C" else "PUT"
        extracted.setdefault("direction", "LONG" if contract.group(2) == "C" else "SHORT")

    explicit_contract = _match_group(r"\b(CALL|PUT)\b", upper)
    if explicit_contract:
        extracted["contract_type"] = explicit_contract

    signa = re.search(r"\bSIGNA\s+(\d+(?:\.\d+)?)\s+([A-F][+]?)\b", upper)
    if signa:
        extracted["signa_score"] = float(signa.group(1))
        extracted["signa_grade"] = signa.group(2)

    daily = _match_group(r"\bDAILY\s+(BULLISH|BEARISH|NEUTRAL|UP|DOWN)\b", upper)
    weekly = _match_group(r"\bWEEKLY\s+(BULLISH|BEARISH|NEUTRAL|UP|DOWN)\b", upper)
    if daily:
        extracted["signa_daily_direction"] = _normalize_signal_direction(daily)
    if weekly:
        extracted["signa_weekly_direction"] = _normalize_signal_direction(weekly)

    if re.search(r"\bGEX\s+LOW\s+PINNING\b|\bLOW\s+PINNING\b", upper):
        extracted["gex_regime"] = "LOW_PINNING"
    else:
        regime = _match_group(r"\bGEX\s+([A-Z_ ]+?)(?:\s+SUPPORT|\s+RESISTANCE|\s+PRICE|$)", upper)
        if regime:
            extracted["gex_regime"] = regime.strip().replace(" ", "_")

    for source_key, target_key in (
        ("SUPPORT", "gex_support_wall"),
        ("SUPPORT WALL", "gex_support_wall"),
        ("RESISTANCE", "gex_resistance_wall"),
        ("RESISTANCE WALL", "gex_resistance_wall"),
        ("PRICE", "current_price"),
        ("SPOT", "current_price"),
        ("PREMIUM", "premium"),
        ("DTE", "dte"),
        ("STRIKE", "strike"),
        ("QTY", "quantity"),
        ("QUANTITY", "quantity"),
        ("MAX PREMIUM", "max_premium_per_contract"),
        ("VOL", "option_volume"),
        ("VOLUME", "option_volume"),
        ("OI", "open_interest"),
        ("OPEN INTEREST", "open_interest"),
        ("9MA", "nine_ma"),
        ("9 MA", "nine_ma"),
        ("NINE MA", "nine_ma"),
    ):
        value = _number_after_label(upper, source_key)
        if value is None:
            continue
        if target_key in {"dte", "quantity", "option_volume", "open_interest"}:
            extracted[target_key] = int(value)
        else:
            extracted[target_key] = float(value)

    # "spot $593.48" — second pass in case PRICE label missed it
    if "current_price" not in extracted:
        spot = _match_group(r"\bSPOT\s+(\d+(?:\.\d+)?)\b", upper)
        if spot:
            extracted["current_price"] = float(spot)

    # "Target 1: $600" → resistance wall for longs (first numbered target = nearest resistance)
    if "gex_resistance_wall" not in extracted:
        t1 = _match_group(r"\bTARGET\s+1\s*[:.]?\s*(\d+(?:\.\d+)?)\b", upper)
        if t1 is None:
            t1 = _match_group(r"\bTARGET\s*[:.]?\s*(\d+(?:\.\d+)?)\b", upper)
        if t1 is not None:
            direction_now = extracted.get("direction", "LONG")
            if direction_now == "LONG":
                extracted["gex_resistance_wall"] = float(t1)
            else:
                extracted.setdefault("gex_support_wall", float(t1))

    # Infer contract_type from direction when not stated
    if "contract_type" not in extracted and "direction" in extracted:
        extracted["contract_type"] = "CALL" if extracted["direction"] == "LONG" else "PUT"

    # Infer LOW_PINNING when GEX says "near support" + bullish (price held = low gamma / pinning zone)
    if "gex_regime" not in extracted:
        near_support = bool(re.search(r"\bNEAR\s+SUPPORT\b", upper))
        bullish = extracted.get("direction") == "LONG"
        near_resistance = bool(re.search(r"\bNEAR\s+RESISTANCE\b", upper))
        bearish = extracted.get("direction") == "SHORT"
        if (near_support and bullish) or (near_resistance and bearish):
            extracted["gex_regime"] = "LOW_PINNING"

    expiry = _match_group(r"\b(?:EXPIRY|EXP|EXPIRATION)\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", upper)
    if expiry is None:
        expiry = _match_group(r"\b\d+(?:\.\d+)?[CP]\s+(\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", upper)
    if expiry:
        extracted["expiry_date"] = _normalize_date_string(expiry, reference)

    if re.search(r"\bNO\s+EARNINGS\b", upper):
        extracted["earnings_date"] = None
    else:
        earnings = _match_group(r"\bEARNINGS\s+(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\b", upper)
        if earnings:
            extracted["earnings_date"] = _normalize_date_string(earnings, reference)

    missing = _missing_rh_fields(extracted)
    return {
        "parsed": extracted,
        "missing_fields": missing,
        "normalized_text": normalized,
    }


def evaluate_messy_rh_options_text(
    text: str,
    *,
    storage: ScanStorage | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    parsed = parse_messy_rh_options_text(text, now=now)
    if parsed["missing_fields"]:
        return {
            "decision": "NEEDS_MORE_INFO",
            "failed_gates": [],
            "warnings": [f"missing_fields: {', '.join(parsed['missing_fields'])}"],
            "score": None,
            "risk_result": {"approved": False, "failed_rule": "missing_fields", "reason": "Required fields are missing."},
            "order_ticket": None,
            "broker_preview": RHAdvisoryBroker().preview_order(None),
            "shadow_id": None,
            "advisory_only": True,
            "parsed": parsed,
        }
    result = evaluate_rh_options(_parse_rh_inputs(parsed["parsed"]), storage=storage, now=now)
    result["parsed"] = parsed
    return result


def evaluate_rh_options(
    inputs: RHOptionsInput,
    *,
    storage: ScanStorage | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    timestamp = now or datetime.now(timezone.utc)
    failed_gates = _hard_gates(inputs, timestamp)
    warnings = [] if failed_gates else _soft_warnings(inputs)
    decision = "NO_TRADE" if failed_gates else "WATCH" if warnings else "TRADE"
    risk_result = _risk_check(inputs)
    order_ticket = _build_order_ticket(inputs) if decision != "NO_TRADE" else None
    broker_preview = RHAdvisoryBroker().preview_order(order_ticket)
    shadow_id = None

    if storage is not None and decision in {"TRADE", "WATCH"}:
        raw = asdict(inputs)
        synthetic = ScoreResult(
            ticker=inputs.ticker,
            direction=inputs.direction,
            score=int(inputs.signa_score / 10),
            pattern=f"{inputs.contract_type}_{inputs.direction}",
            components={},
            raw=raw,
            reason="",
        )
        scan_id = storage.record_scan(
            synthetic,
            source="rh_options",
            alert_sent=False,
            alert_suppression_reason="",
            timestamp=timestamp,
        )
        shadow_id = storage.record_shadow_setup(
            synthetic,
            scan_id=scan_id,
            setup_inputs=raw,
            provider_snapshot={},
            selected_contract=order_ticket or {},
            timestamp=timestamp,
        )

    return {
        "decision": decision,
        "failed_gates": failed_gates,
        "warnings": warnings,
        "score": int(inputs.signa_score / 10),
        "risk_result": risk_result,
        "order_ticket": order_ticket,
        "broker_preview": broker_preview,
        "shadow_id": shadow_id,
        "advisory_only": True,
    }


def rank_option_contracts(
    candidates: list[dict[str, Any]],
    *,
    direction: str,
    current_price: float,
    gex_resistance_wall: float | None = None,
    gex_support_wall: float | None = None,
    max_premium_per_contract: float = 250.0,
) -> list[dict[str, Any]]:
    """Rank option contracts by R:R using the GEX wall as the price target.

    Gain estimate (per contract):
      - With delta (from a live quote): delta × (gex_target − current_price) × 100
        This accounts for remaining time value and is accurate for pre-expiry exits.
      - Without delta: max(0, gex_target − strike) × 100  (intrinsic only; conservative)
    R:R = (estimated_gain − premium × 100) / (premium × 0.50 × 100)
    Filtered by: volume ≥ 100, OI ≥ 500, premium ≤ cap, dte > 2, positive price move to target.
    Sorted by R:R desc, then open_interest desc as a liquidity tiebreaker.
    """
    direction = _normalize_direction(direction)
    gex_target = gex_resistance_wall if direction == "LONG" else gex_support_wall

    scored = []
    for raw in candidates:
        strike = _optional_float(raw.get("strike"))
        premium = _optional_float(raw.get("premium"))
        dte = raw.get("dte")
        vol = raw.get("option_volume")
        oi = raw.get("open_interest")
        expiry = str(raw.get("expiry_date") or "").strip()

        if strike is None or premium is None or dte is None:
            continue
        if int(dte) <= 2:
            continue
        if premium * 100 > max_premium_per_contract:
            continue
        if vol is not None and int(vol) < 100:
            continue
        if oi is not None and int(oi) < 500:
            continue

        # Estimate option gain at the GEX wall target.
        # When delta is provided (from a live quote), use delta × price_move — this accounts for
        # time value remaining and is accurate for exits well before expiry.
        # Without delta, fall back to intrinsic value (conservative; accurate only at expiry).
        delta = _optional_float(raw.get("delta"))
        if gex_target is not None:
            price_move = (gex_target - current_price) if direction == "LONG" else (current_price - gex_target)
        else:
            price_move = current_price * 0.20  # 20% fallback

        if price_move <= 0:
            continue

        if delta is not None:
            estimated_gain = abs(delta) * price_move
        else:
            if direction == "LONG":
                estimated_gain = max(0.0, (gex_target or current_price * 1.20) - strike)
            else:
                estimated_gain = max(0.0, strike - (gex_target or current_price * 0.80))

        dollar_gain = (estimated_gain - premium) * 100
        dollar_risk = premium * 0.50 * 100

        if dollar_gain <= 0 or dollar_risk <= 0:
            continue

        rr = round(dollar_gain / dollar_risk, 2)
        scored.append({
            **raw,
            "strike": strike,
            "premium": premium,
            "dte": int(dte),
            "expiry_date": expiry,
            "option_volume": int(vol) if vol is not None else None,
            "open_interest": int(oi) if oi is not None else None,
            "delta": delta,
            "estimated_gain": round(estimated_gain, 2),
            "dollar_gain": round(dollar_gain, 2),
            "dollar_risk": round(dollar_risk, 2),
            "rr": rr,
        })

    scored.sort(key=lambda c: (c["rr"], c.get("open_interest") or 0), reverse=True)
    for i, c in enumerate(scored):
        c["rank"] = i + 1
    return scored


def manage_rh_options_position(
    setup: Any,
    *,
    current_price: float | None = None,
    current_premium: float | None = None,
) -> dict[str, Any]:
    ticket = dict(getattr(setup, "selected_contract", {}) or {})
    setup_inputs = dict(getattr(setup, "setup_inputs", {}) or {})
    direction = str(getattr(setup, "direction", "") or setup_inputs.get("direction") or "").upper()
    reasons = []

    if current_price is None and current_premium is None:
        return _management_result("NEEDS_MORE_INFO", ["current_price or current_premium is required"], setup, ticket)

    stop = _optional_float(ticket.get("stop_premium"))
    target = _optional_float(ticket.get("target_premium"))
    invalidation = _optional_float(ticket.get("invalidation_level"))
    premium = _optional_float(current_premium)
    price = _optional_float(current_price)

    if premium is not None and stop is not None and premium <= stop:
        reasons.append(f"current premium {premium:g} is at/below stop {stop:g}")
        return _management_result("EXIT", reasons, setup, ticket)

    if price is not None and invalidation is not None:
        if direction == "LONG" and price < invalidation:
            reasons.append(f"underlying {price:g} broke invalidation {invalidation:g}")
            return _management_result("INVALIDATED", reasons, setup, ticket)
        if direction == "SHORT" and price > invalidation:
            reasons.append(f"underlying {price:g} broke invalidation {invalidation:g}")
            return _management_result("INVALIDATED", reasons, setup, ticket)

    if premium is not None and target is not None and premium >= target:
        reasons.append(f"current premium {premium:g} reached target {target:g}")
        return _management_result("TRIM", reasons, setup, ticket)

    if premium is not None and target is not None and premium >= target * 0.85:
        reasons.append(f"current premium {premium:g} is within 15% of target {target:g}")
        return _management_result("HOLD", reasons + ["consider trim if rejection appears"], setup, ticket)

    reasons.append("stop, target, and invalidation remain intact")
    return _management_result("HOLD", reasons, setup, ticket)


def morning_check(storage: ScanStorage, discord_url: str) -> dict[str, Any]:
    """Send a Discord recap of every OPEN shadow position — call once at session start."""
    open_positions = storage.latest_shadow_setups(status="OPEN", limit=100)
    if not open_positions:
        discord_sent = _post_discord(discord_url, {"embeds": [{
            "title": "☀️ Morning Check — No Open Positions",
            "description": "Shadow journal is flat. Nothing to manage.",
            "color": 8421504,
            "footer": {"text": "VP Options Advisory — advisory only"},
        }]})
        return {"open_count": 0, "discord_sent": discord_sent}

    fields = []
    for pos in open_positions:
        ticket = pos.selected_contract or {}
        inp = pos.setup_inputs or {}
        contract_type = ticket.get("contract_type") or inp.get("contract_type", "")
        strike = ticket.get("strike") or inp.get("strike", "")
        expiry = ticket.get("expiry") or inp.get("expiry_date", "")
        entry = ticket.get("limit_debit") or inp.get("premium")
        stop = ticket.get("stop_premium")
        target = ticket.get("target_premium")
        style = ticket.get("trade_style", "")
        invalidation = ticket.get("invalidation_level")

        label = f"**{pos.ticker} {pos.direction}** · {strike} {contract_type} exp {expiry}"
        parts = []
        if entry is not None:
            parts.append(f"In ${float(entry):.2f}")
        if stop is not None:
            parts.append(f"Stop ${float(stop):.2f}")
        if target is not None:
            parts.append(f"Target ${float(target):.2f}")
        if style:
            parts.append(style)

        notes = " | ".join(parts)
        if invalidation is not None:
            notes += f"\nInvalidation: ${float(invalidation):g}"
        fields.append({"name": label, "value": notes or "no ticket data", "inline": False})

    n = len(open_positions)
    embed = {
        "title": f"☀️ Morning Check — {n} Open Position{'s' if n != 1 else ''}",
        "description": "Manage in Robinhood. Stop and target levels below.",
        "color": 15908139,  # warm gold
        "fields": fields,
        "footer": {"text": "VP Options Advisory — advisory only"},
    }
    discord_sent = _post_discord(discord_url, {"embeds": [embed]})
    return {
        "open_count": n,
        "discord_sent": discord_sent,
        "positions": [_shadow_to_summary(p) for p in open_positions],
    }


def kill_switch(storage: ScanStorage, discord_url: str) -> dict[str, Any]:
    """Advisory kill switch: Discord-alerts all OPEN shadow positions to close in RH, marks CANCELLED."""
    open_positions = storage.latest_shadow_setups(status="OPEN", limit=100)
    if not open_positions:
        return {"positions_found": 0, "discord_sent": False, "message": "no_open_positions"}

    fields = []
    for pos in open_positions:
        ticket = pos.selected_contract or {}
        inp = pos.setup_inputs or {}
        contract_type = ticket.get("contract_type") or inp.get("contract_type", "")
        strike = ticket.get("strike") or inp.get("strike", "")
        expiry = ticket.get("expiry") or inp.get("expiry_date", "")
        entry = ticket.get("limit_debit") or inp.get("premium")
        stop = ticket.get("stop_premium")
        target = ticket.get("target_premium")
        style = ticket.get("trade_style", "")

        label = f"**{pos.ticker} {pos.direction}** · {strike} {contract_type} exp {expiry}"
        parts = []
        if entry is not None:
            parts.append(f"Entry ${float(entry):.2f}")
        if stop is not None:
            parts.append(f"Stop ${float(stop):.2f}")
        if target is not None:
            parts.append(f"Target ${float(target):.2f}")
        if style:
            parts.append(style)
        fields.append({
            "name": label,
            "value": (" | ".join(parts) if parts else "no ticket data") + "\n→ **Close in Robinhood now**",
            "inline": False,
        })

    embed = {
        "title": f"🚨 KILL SWITCH — {len(open_positions)} POSITION{'S' if len(open_positions) != 1 else ''} TO CLOSE",
        "description": "Close all open option positions in Robinhood immediately.",
        "color": 15158332,
        "fields": fields,
        "footer": {"text": "VP Options Advisory — advisory only"},
    }
    discord_sent = _post_discord(discord_url, {"embeds": [embed]})

    cancelled_ids = []
    for pos in open_positions:
        storage.update_shadow_outcome(pos.id, status="CANCELLED", outcome={"reason": "kill_switch"})
        cancelled_ids.append(pos.id)

    return {
        "positions_found": len(open_positions),
        "cancelled_ids": cancelled_ids,
        "discord_sent": discord_sent,
        "positions": [_shadow_to_summary(p) for p in open_positions],
    }


def check_open_positions(
    storage: ScanStorage,
    discord_url: str,
    marks: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Return open shadow positions; Discord-alert any that have hit their stop or target.

    marks: {str(shadow_id): current_mark_price} — omit to just get the open list.
    """
    open_positions = storage.latest_shadow_setups(status="OPEN", limit=100)
    hits: list[dict[str, Any]] = []

    for pos in open_positions:
        if not marks:
            continue
        mark = marks.get(str(pos.id))
        if mark is None:
            continue

        ticket = pos.selected_contract or {}
        stop = _optional_float(ticket.get("stop_premium"))
        target = _optional_float(ticket.get("target_premium"))

        if stop is not None and mark <= stop:
            hit_type = "STOP_HIT"
        elif target is not None and mark >= target:
            hit_type = "TARGET_HIT"
        else:
            continue

        hits.append({"shadow_id": pos.id, "hit_type": hit_type, "mark": mark, "ticker": pos.ticker})
        _post_discord(discord_url, {"embeds": [_build_position_hit_embed(pos, mark, hit_type)]})

    return {
        "open_count": len(open_positions),
        "hits": hits,
        "positions": [_shadow_to_summary(p) for p in open_positions],
    }


def _shadow_to_summary(pos: Any) -> dict[str, Any]:
    ticket = dict(getattr(pos, "selected_contract", {}) or {})
    inp = dict(getattr(pos, "setup_inputs", {}) or {})
    return {
        "shadow_id": getattr(pos, "id", None),
        "ticker": getattr(pos, "ticker", None),
        "direction": getattr(pos, "direction", None),
        "status": getattr(pos, "status", None),
        "strike": ticket.get("strike") or inp.get("strike"),
        "contract_type": ticket.get("contract_type") or inp.get("contract_type"),
        "expiry": ticket.get("expiry") or inp.get("expiry_date"),
        "entry": ticket.get("limit_debit") or inp.get("premium"),
        "stop_premium": ticket.get("stop_premium"),
        "target_premium": ticket.get("target_premium"),
        "trade_style": ticket.get("trade_style"),
        "quantity": ticket.get("quantity") or inp.get("quantity"),
    }


def _build_position_hit_embed(pos: Any, mark: float, hit_type: str) -> dict[str, Any]:
    ticket = getattr(pos, "selected_contract", {}) or {}
    inp = getattr(pos, "setup_inputs", {}) or {}
    stop = ticket.get("stop_premium")
    target = ticket.get("target_premium")
    strike = ticket.get("strike") or inp.get("strike", "")
    contract_type = ticket.get("contract_type") or inp.get("contract_type", "")
    expiry = ticket.get("expiry") or inp.get("expiry_date", "")

    is_stop = hit_type == "STOP_HIT"
    color = 15158332 if is_stop else 3066993
    icon = "🛑" if is_stop else "🎯"
    label = "STOP HIT" if is_stop else "TARGET HIT"
    action = "CUT THE POSITION" if is_stop else "TAKE PROFITS"

    fields: list[dict[str, Any]] = []
    if stop is not None:
        fields.append({"name": "Stop", "value": f"${float(stop):.2f}", "inline": True})
    if target is not None:
        fields.append({"name": "Target", "value": f"${float(target):.2f}", "inline": True})
    fields.append({"name": "Mark", "value": f"${mark:.2f}", "inline": True})
    fields.append({"name": "Action", "value": f"→ **{action} in Robinhood**", "inline": False})

    return {
        "title": f"{icon} {label} — {pos.ticker} {pos.direction} · {strike} {contract_type} {expiry}",
        "color": color,
        "fields": fields,
        "footer": {"text": "VP Options Advisory — advisory only"},
    }


def _post_discord(url: str, payload: dict[str, Any]) -> bool:
    if not url:
        return False
    try:
        resp = httpx.post(url, json=payload, timeout=5.0)
        return resp.status_code in (200, 204)
    except Exception:
        return False


def _trade_style_and_target(inputs: RHOptionsInput) -> tuple[str, float, float]:
    """Return (trade_style, target_multiplier, stop_multiplier) based on DTE and 9MA distance.

    DTE tiers:
      0–7  → SCALP_INTRADAY: 1.30x target / 0.35x stop  (fast decay, quick exits)
      8–21 → SCALP or SWING based on 9MA: 1.50x / 0.50x or 2.00x / 0.50x
      22+  → SWING: 2.00x target / 0.50x stop
    """
    dte = inputs.dte

    if dte <= 7:
        return "SCALP_INTRADAY", 1.30, 0.35

    if dte <= 21:
        if inputs.nine_ma is not None and inputs.nine_ma > 0:
            pct = (inputs.current_price - inputs.nine_ma) / inputs.nine_ma
            if inputs.direction == "LONG" and pct > 0.02:
                return "SCALP", 1.50, 0.50
            if inputs.direction == "SHORT" and pct < -0.02:
                return "SCALP", 1.50, 0.50
        return "SWING", 2.00, 0.50

    return "SWING", 2.00, 0.50


def _hard_gates(inputs: RHOptionsInput, now: datetime) -> list[str]:
    failures = []
    if inputs.signa_score < 70:
        failures.append("signa_score_too_low")
    if inputs.signa_grade not in GOOD_GRADES:
        failures.append("signa_grade_below_b")
    if _directions_conflict(inputs.signa_daily_direction, inputs.signa_weekly_direction):
        failures.append("direction_conflict")
    if inputs.gex_regime != "LOW_PINNING":
        failures.append("gex_regime_not_low_pinning")
    if inputs.dte < 0:
        failures.append("expiry_too_close")
    if inputs.earnings_date and _date_within_days(_parse_date(inputs.earnings_date), now.date(), days=5):
        failures.append("earnings_too_close")
    if inputs.premium * 100 > inputs.max_premium_per_contract:
        failures.append("premium_over_cap")
    if inputs.option_volume is not None and inputs.option_volume < 100:
        failures.append("low_option_volume")
    if inputs.open_interest is not None and inputs.open_interest < 500:
        failures.append("low_open_interest")
    return failures


def _soft_warnings(inputs: RHOptionsInput) -> list[str]:
    warnings = []
    if (
        inputs.direction == "LONG"
        and inputs.gex_support_wall is not None
        and not _within_percent(inputs.current_price, inputs.gex_support_wall, percent=0.02)
    ):
        warnings.append("price_not_near_support_wall")
    if (
        inputs.direction == "SHORT"
        and inputs.gex_resistance_wall is not None
        and not _within_percent(inputs.current_price, inputs.gex_resistance_wall, percent=0.02)
    ):
        warnings.append("price_not_near_resistance_wall")
    if inputs.option_volume is None and inputs.open_interest is None:
        warnings.append("no_liquidity_data_provided")
    if inputs.nine_ma is not None and inputs.nine_ma > 0:
        if inputs.direction == "LONG" and inputs.current_price < inputs.nine_ma:
            warnings.append("price_below_9ma_for_long_setup")
        elif inputs.direction == "SHORT" and inputs.current_price > inputs.nine_ma:
            warnings.append("price_above_9ma_for_short_setup")
    return warnings


def _risk_check(inputs: RHOptionsInput) -> dict[str, Any]:
    if inputs.premium * 100 > inputs.max_premium_per_contract:
        return {
            "approved": False,
            "failed_rule": "per_contract_premium",
            "reason": (
                f"Premium ${inputs.premium * 100:.2f}/contract exceeds max "
                f"${inputs.max_premium_per_contract:.2f}."
            ),
        }
    total_debit = inputs.premium * 100 * inputs.quantity
    max_total = inputs.max_premium_per_contract * inputs.max_contracts
    if total_debit > max_total:
        return {
            "approved": False,
            "failed_rule": "total_premium",
            "reason": f"Total debit ${total_debit:.2f} exceeds max ${max_total:.2f}.",
        }
    trade_style, target_mult, stop_mult = _trade_style_and_target(inputs)
    entry = inputs.premium
    stop = round(inputs.premium * stop_mult, 2)
    target = round(inputs.premium * target_mult, 2)
    risk = entry - stop
    reward = target - entry
    if risk <= 0:
        return {"approved": False, "failed_rule": "risk_invalid", "reason": "Premium risk must be positive."}
    rr = reward / risk
    min_rr = {"SCALP_INTRADAY": 0.75, "SCALP": 1.0, "SWING": 2.0}.get(trade_style, 1.0)
    if rr < min_rr:
        return {
            "approved": False,
            "failed_rule": "rr_too_low",
            "reason": f"Options R:R {rr:.2f} below minimum {min_rr:.2f} ({trade_style}).",
        }
    return {"approved": True, "failed_rule": None, "reason": None}


def _build_order_ticket(inputs: RHOptionsInput) -> dict[str, Any]:
    trade_style, target_mult, stop_mult = _trade_style_and_target(inputs)
    invalidation = inputs.gex_support_wall if inputs.direction == "LONG" else inputs.gex_resistance_wall
    notes = []
    if inputs.gex_support_wall is not None:
        notes.append(f"GEX support wall: {inputs.gex_support_wall:g}")
    if inputs.gex_resistance_wall is not None:
        notes.append(f"GEX resistance wall: {inputs.gex_resistance_wall:g}")
    notes.append(
        "Bullish setup: support wall is the floor; scale toward resistance walls."
        if inputs.direction == "LONG"
        else "Bearish setup: resistance wall is the ceiling; scale toward support walls."
    )
    if inputs.nine_ma is not None and inputs.nine_ma > 0:
        pct = (inputs.current_price - inputs.nine_ma) / inputs.nine_ma * 100
        style_note = {
            "SCALP_INTRADAY": "fast decay — exit at 1.3x, cut at 35% loss",
            "SCALP": "extended from 9MA — take quick profits at 1.5x",
            "SWING": "clean setup — hold for full move at 2x",
        }.get(trade_style, trade_style)
        notes.append(
            f"9MA {inputs.nine_ma:g} ({pct:+.1f}% from price) — {trade_style}: {style_note}"
        )
    return {
        "action": "Buy to open",
        "ticker": inputs.ticker,
        "strike": inputs.strike,
        "expiry": inputs.expiry_date,
        "contract_type": inputs.contract_type,
        "quantity": inputs.quantity,
        "limit_debit": inputs.premium,
        "stop_premium": round(inputs.premium * stop_mult, 2),
        "target_premium": round(inputs.premium * target_mult, 2),
        "trade_style": trade_style,
        "invalidation_level": invalidation,
        "management_notes": notes,
    }


def _normalize_direction(value: Any) -> str:
    raw = str(value).upper().strip()
    if raw in {"CALL", "BULL", "BULLISH", "UP"}:
        return "LONG"
    if raw in {"PUT", "BEAR", "BEARISH", "DOWN"}:
        return "SHORT"
    return raw


def _normalize_signal_direction(value: Any) -> str:
    raw = str(value).upper().strip()
    if raw in {"UP", "LONG", "BULL"}:
        return "BULLISH"
    if raw in {"DOWN", "SHORT", "BEAR"}:
        return "BEARISH"
    return raw


def _directions_conflict(daily: str, weekly: str) -> bool:
    return (daily == "BULLISH" and weekly == "BEARISH") or (daily == "BEARISH" and weekly == "BULLISH")


def _optional_float(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    return float(value)


def _parse_date(value: str) -> date:
    return date.fromisoformat(value)


def _date_within_days(target: date, reference: date, *, days: int) -> bool:
    delta = (target - reference).days
    return 0 <= delta <= days


def _within_percent(value: float, reference: float, *, percent: float) -> bool:
    if reference == 0:
        return False
    return abs(value - reference) / abs(reference) <= percent


def _management_result(action: str, reasons: list[str], setup: Any, ticket: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": action,
        "reasons": reasons,
        "shadow_id": getattr(setup, "id", None),
        "ticker": getattr(setup, "ticker", None),
        "status": getattr(setup, "status", None),
        "ticket": ticket,
        "advisory_only": True,
    }


def _squash_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("$", " ")).strip()


def _match_group(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _first_ticker_token(text: str) -> str | None:
    blocked = {
        "A",
        "AN",
        "CALL",
        "DTE",
        "EXP",
        "EXPIRY",
        "GEX",
        "LOW",
        "NO",
        "PINNING",
        "PREMIUM",
        "PRICE",
        "PUT",
        "RESISTANCE",
        "SIGNA",
        "SUPPORT",
        "WEEKLY",
        "DAILY",
    }
    for token in re.findall(r"\b[A-Z]{1,5}\b", text):
        if token not in blocked:
            return token
    return None


def _number_after_label(text: str, label: str) -> float | None:
    pattern = rf"\b{re.escape(label)}\s*[:=]?\s*(\d+(?:\.\d+)?)\b"
    value = _match_group(pattern, text)
    return float(value) if value is not None else None


def _normalize_date_string(value: str, reference: datetime) -> str:
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", raw)
    if not match:
        raise ValueError(f"Unsupported date format: {value}")
    month = int(match.group(1))
    day = int(match.group(2))
    year_raw = match.group(3)
    if year_raw is None:
        year = reference.year
    else:
        year = int(year_raw)
        if year < 100:
            year += 2000
    return date(year, month, day).isoformat()


def _missing_rh_fields(body: dict[str, Any]) -> list[str]:
    required = (
        "ticker",
        "direction",
        "contract_type",
        "signa_score",
        "signa_grade",
        "signa_daily_direction",
        "gex_regime",
        "current_price",
        "premium",
        "expiry_date",
        "dte",
        "strike",
    )
    return [name for name in required if body.get(name) in {None, ""}]


# ── GEX wall computation from RH option chain ───────────────────────────────


def compute_gex_walls(
    chain: list[dict[str, Any]], current_price: float
) -> dict[str, Any]:
    """Compute GEX walls from option chain open-interest data.

    chain: list of contract dicts, each with 'strike_price', 'type'/'option_type',
           and 'open_interest' (strings or numbers — both accepted).
    current_price: underlying spot price.

    Returns:
        call_wall: highest-OI call strike at or above current price
        put_wall: highest-OI put strike at or below current price
        regime: LOW_PINNING | BREAKOUT | BREAKDOWN | TRANSITION
        confidence: HIGH | MEDIUM | LOW
    """
    calls: list[tuple[float, float]] = []  # (strike, oi)
    puts: list[tuple[float, float]] = []

    for contract in chain:
        try:
            strike = float(contract.get("strike_price") or contract.get("strike") or 0)
            oi = float(contract.get("open_interest") or 0)
            raw_type = str(
                contract.get("type") or contract.get("option_type") or ""
            ).upper()
        except (TypeError, ValueError):
            continue
        if strike <= 0 or oi <= 0:
            continue
        if "CALL" in raw_type or raw_type == "C":
            calls.append((strike, oi))
        elif "PUT" in raw_type or raw_type == "P":
            puts.append((strike, oi))

    call_wall: float | None = None
    put_wall: float | None = None

    if calls:
        call_wall = max(calls, key=lambda x: x[1])[0]
    if puts:
        put_wall = max(puts, key=lambda x: x[1])[0]

    # Regime inference
    regime = "TRANSITION"
    if call_wall is not None and put_wall is not None:
        pct_to_call = abs(current_price - call_wall) / current_price
        pct_to_put = abs(current_price - put_wall) / current_price
        if current_price > call_wall:
            regime = "BREAKOUT"
        elif current_price < put_wall:
            regime = "BREAKDOWN"
        elif pct_to_call <= 0.01 or pct_to_put <= 0.01:
            regime = "LOW_PINNING"
        else:
            regime = "TRANSITION"
        confidence = "HIGH" if max(pct_to_call, pct_to_put) <= 0.03 else "MEDIUM"
    elif call_wall is not None or put_wall is not None:
        confidence = "LOW"
    else:
        confidence = "LOW"

    return {
        "call_wall": call_wall,
        "put_wall": put_wall,
        "regime": regime,
        "confidence": confidence,
    }


def build_candidate_embed(
    ticker: str,
    direction: str,
    signa_score: float,
    signa_grade: str,
    price: float,
    *,
    call_wall: float | None = None,
    put_wall: float | None = None,
    regime: str = "TRANSITION",
    strat: dict[str, Any] | None = None,
    orb: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Build a Discord embed for a scanner candidate alert.

    Does NOT fire Discord — call _post_discord(url, {"embeds": [...]}) with the result.
    """
    is_long = direction.upper() in ("LONG", "BULLISH", "UP")
    color = 0x20C783 if is_long else 0xFF4D5A
    emoji = "▲" if is_long else "▼"
    title = f"{emoji} CANDIDATE — {ticker.upper()} {direction.upper()}"

    # Core Signa row
    fields: list[dict[str, Any]] = [
        {
            "name": "Signa",
            "value": f"Grade **{signa_grade}** · Score **{signa_score:.0f}** · {direction.capitalize()}",
            "inline": True,
        },
        {
            "name": "Price / Walls",
            "value": (
                f"Spot **${price:.2f}**"
                + (f" | Put wall ${put_wall:.2f}" if put_wall else "")
                + (f" | Call wall ${call_wall:.2f}" if call_wall else "")
            ),
            "inline": True,
        },
        {"name": "GEX Regime", "value": f"**{regime}**", "inline": True},
    ]

    # Strat context
    if strat:
        pattern = strat.get("pattern") or "—"
        bias = strat.get("bias") or "—"
        bar_types = " → ".join(strat.get("bar_types") or []) or "—"
        fields.append(
            {
                "name": "Strat (4hr)",
                "value": f"**{pattern}** · bias {bias} · {bar_types}",
                "inline": True,
            }
        )

    # ORB context
    if orb:
        status = orb.get("status", "unknown")
        orb_h = orb.get("orb_high")
        orb_l = orb.get("orb_low")
        win = orb.get("window_minutes", 15)
        orb_val = f"**{status.upper()}** ORB{win}"
        if orb_h and orb_l:
            orb_val += f" (H:{orb_h:.2f} / L:{orb_l:.2f})"
        fields.append({"name": "ORB", "value": orb_val, "inline": True})

    if note:
        fields.append({"name": "Note", "value": note, "inline": False})

    return {
        "color": color,
        "title": title,
        "fields": fields,
        "footer": {"text": "VP Options Advisory · context only, not a trade signal"},
    }
