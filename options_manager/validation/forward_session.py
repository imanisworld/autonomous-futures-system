"""Fail-closed validation for one preregistered morning forward-proof record.

The record is collected at three named ET stages: 09:26, 09:46, and 10:03.
This module is pure: it reads only the supplied text, uses no clock/network/I/O,
and never promotes a trade. Missing or contradictory evidence fails closed.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, time
from enum import Enum
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from strategy.strat_classifier import StratBar, TWO_DOWN, TWO_UP, classify_bar

ET = ZoneInfo("America/New_York")

STAGES: tuple[tuple[str, str], ...] = (
    ("0926", r"^##\s*09:26\s*ET"),
    ("0946", r"^##\s*09:46\s*ET"),
    ("1003", r"^##\s*10:03\s*ET"),
)

# A stage named 09:26/09:46/10:03 must actually be captured in that minute.
# This uses the preregistered labels themselves rather than inventing a broad
# tolerance that could admit later-session reconstruction.
STAGE_WINDOWS: dict[str, tuple[time, time]] = {
    "0926": (time(9, 26), time(9, 27)),
    "0946": (time(9, 46), time(9, 47)),
    "1003": (time(10, 3), time(10, 4)),
}

UNAVAILABLE = "UNAVAILABLE"
NOT_STARTED = "NOT STARTED"
_RECONSTRUCTION_WORDS = ("reconstructed", "backfilled", "retroactively", "after the fact")
_KV_RE = re.compile(r"^([a-z0-9_]+)\s*:\s*(.*)$")
_STRAT_TYPES = {"1", "2U", "2D", "3"}


class ForwardSessionVerdict(str, Enum):
    VALID = "SESSION_VALID"
    DEGRADED = "SESSION_DEGRADED"
    INVALID = "SESSION_INVALID"


@dataclass(frozen=True, kw_only=True)
class ForwardSessionResult:
    verdict: ForwardSessionVerdict
    hard_failures: tuple[str, ...] = ()
    soft_gaps: tuple[str, ...] = ()
    locked_ticker: Optional[str] = None
    stages_present: tuple[str, ...] = ()
    stage_retrieved_at: Mapping[str, Optional[str]] | None = None


def parse_session_markdown(text: str) -> dict[str, dict[str, str]]:
    """Parse only the first occurrence of each named stage."""
    stages: dict[str, dict[str, str]] = {}
    current: Optional[str] = None
    order: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        header = next((key for key, pattern in STAGES if re.match(pattern, line)), None)
        if header is not None:
            if header in stages:
                stages.setdefault("_meta", {})[f"duplicate_{header}"] = "true"
                current = None
                continue
            stages[header] = {}
            order.append(header)
            current = header
            continue
        if line.startswith("## "):
            current = None
            continue
        if current is None:
            continue
        match = _KV_RE.match(line.strip())
        if match:
            key, value = match.group(1), match.group(2).strip()
            stages[current].setdefault(key, value)
    stages.setdefault("_meta", {})["order"] = ",".join(order)
    return stages


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def _float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _truthy(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    return None


def _ohlc(value: Optional[str]) -> Optional[tuple[float, float, float, float]]:
    if not value:
        return None
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 4:
        return None
    parsed = tuple(_float(part) for part in parts)
    if any(item is None for item in parsed):
        return None
    open_, high, low, close = parsed
    assert open_ is not None and high is not None and low is not None and close is not None
    if high < max(open_, close, low) or low > min(open_, close, high):
        return None
    return open_, high, low, close


def _same_price(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=1e-9)


def evaluate_forward_session(
    text: str, *, session_date: Optional[str] = None
) -> ForwardSessionResult:
    hard: list[str] = []
    soft: list[str] = []
    try:
        stages = parse_session_markdown(text or "")
    except Exception as exc:  # pragma: no cover
        return ForwardSessionResult(
            verdict=ForwardSessionVerdict.INVALID,
            hard_failures=(f"unparseable session record: {exc}",),
        )

    meta = stages.get("_meta", {})
    present = tuple(key for key, _ in STAGES if key in stages)
    for key, _ in STAGES:
        if key not in stages:
            hard.append(f"stage {key} missing")
        if meta.get(f"duplicate_{key}") == "true":
            hard.append(f"stage {key} appears more than once (possible rewrite)")
    expected_order = ",".join(k for k, _ in STAGES if k in stages)
    if meta.get("order", "") != expected_order:
        hard.append(f"stages out of chronological order: {meta.get('order', '')}")

    retrieved: dict[str, Optional[str]] = {}
    parsed_ts: dict[str, datetime] = {}
    for key in present:
        value = stages[key].get("retrieved_at")
        retrieved[key] = value
        ts = _parse_ts(value)
        if ts is None:
            hard.append(f"stage {key} has no timezone-aware retrieved_at")
            continue
        parsed_ts[key] = ts
        local = ts.astimezone(ET)
        if session_date and local.strftime("%Y-%m-%d") != session_date:
            hard.append(
                f"stage {key} retrieved on {local.strftime('%Y-%m-%d')}, "
                f"not session date {session_date}"
            )
        lo, hi = STAGE_WINDOWS[key]
        if not (lo <= local.time() < hi):
            hard.append(
                f"stage {key} retrieved at {local.strftime('%H:%M:%S')} ET, "
                f"outside its preregistered minute {lo.strftime('%H:%M')}"
            )

    ordered_keys = [key for key, _ in STAGES if key in parsed_ts]
    for earlier, later in zip(ordered_keys, ordered_keys[1:]):
        if parsed_ts[later] <= parsed_ts[earlier]:
            hard.append(f"stage {later} retrieved_at is not after stage {earlier}")

    lowered = (text or "").lower()
    for word in _RECONSTRUCTION_WORDS:
        if word in lowered:
            hard.append(f"record contains reconstruction language: {word!r}")
    for key in present:
        if _truthy(stages[key].get("reconstructed")) is True:
            hard.append(f"stage {key} marked reconstructed")

    s0926 = stages.get("0926", {})
    s0946 = stages.get("0946", {})
    s1003 = stages.get("1003", {})

    locked = (s0926.get("locked_ticker") or "").strip().upper() or None
    if "0926" in stages and not locked:
        hard.append("09:26 stage has no locked_ticker")
    for key, section in (("0946", s0946), ("1003", s1003)):
        if key not in stages:
            continue
        other = (section.get("locked_ticker") or "").strip().upper() or None
        if other is None:
            hard.append(f"stage {key} does not restate locked_ticker")
        elif locked and other != locked:
            hard.append(f"ticker lock broken: 09:26 locked {locked}, stage {key} has {other}")

    if "0926" in stages:
        if not (s0926.get("runner_up") or "").strip():
            soft.append("09:26 stage has no runner_up")
        if not (s0926.get("selection_rule") or "").strip():
            soft.append("09:26 stage has no selection_rule")
        for field in ("orb_high", "orb_low"):
            value = (s0926.get(field) or "").strip().upper()
            if value and value != NOT_STARTED:
                hard.append(
                    f"09:26 stage has {field}={value!r} before the opening range existed"
                )

    if "0946" in stages:
        orb_ts = _parse_ts(s0946.get("orb_bars_retrieved_at"))
        stage_ts = parsed_ts.get("0946")
        if orb_ts is None:
            soft.append("09:46 stage has no timezone-aware orb_bars_retrieved_at")
        else:
            orb_local = orb_ts.astimezone(ET)
            if orb_local.time() < time(9, 45):
                hard.append("ORB recorded before 09:45 ET: opening range was not final")
            if stage_ts is not None:
                stage_local = stage_ts.astimezone(ET)
                if orb_local.date() != stage_local.date():
                    hard.append("ORB source timestamp is not on the 09:46 stage date")
                if orb_ts > stage_ts:
                    hard.append("ORB source timestamp is after the 09:46 retrieval time")
            if session_date and orb_local.strftime("%Y-%m-%d") != session_date:
                hard.append(
                    f"ORB source timestamp is on {orb_local.strftime('%Y-%m-%d')}, "
                    f"not session date {session_date}"
                )

        high = _float(s0946.get("orb_high"))
        low = _float(s0946.get("orb_low"))
        if high is None or low is None:
            hard.append("09:46 stage lacks finite orb_high/orb_low")
        elif high <= low:
            hard.append(f"orb_high {high} is not above orb_low {low}")

    if "1003" in stages:
        complete = _truthy(s1003.get("candle_0930_complete"))
        if complete is not True:
            hard.append("10:03 stage does not assert candle_0930_complete: true")

        strat = (s1003.get("strat_type_0930") or "").strip().upper()
        if strat not in _STRAT_TYPES:
            hard.append(
                f"10:03 stage strat_type_0930 {strat or 'missing'!r} "
                "is not one of 1/2U/2D/3"
            )

        sequence_raw = (s1003.get("preceding_sequence") or "").strip().upper()
        sequence = tuple(part.strip() for part in sequence_raw.split(",") if part.strip())
        if not sequence:
            soft.append("10:03 stage has no preceding_sequence")
        elif any(part not in _STRAT_TYPES for part in sequence):
            hard.append(f"preceding_sequence contains a non-Strat type: {sequence_raw!r}")

        setup = (s1003.get("canonical_setup") or "").strip().upper()
        if not setup:
            hard.append("10:03 stage has no canonical_setup line")
        elif "2-1-2" in setup and "NO ACTIONABLE" not in setup:
            # Prove the full continuation, including the right-side 2. The
            # completed 09:30 candle is the inside bar; the 10:00-forming bar
            # must mechanically classify as the requested directional 2.
            if strat != "1":
                hard.append(
                    "2-1-2 claimed but the completed 09:30 candle is not an inside bar (1)"
                )

            direction = (s1003.get("direction") or "").strip().upper()
            if direction not in ("CALL", "PUT"):
                hard.append("actionable 2-1-2 requires direction: CALL or PUT")
                direction = ""

            expected_prior = "2U" if direction == "CALL" else "2D" if direction == "PUT" else None
            if expected_prior and (not sequence or sequence[-1] != expected_prior):
                hard.append(
                    f"actionable {direction} 2-1-2 requires preceding directional "
                    f"bar {expected_prior} immediately before the inside bar"
                )

            candle = _ohlc(s1003.get("candle_0930_ohlc"))
            if candle is None:
                hard.append("actionable 2-1-2 requires finite candle_0930_ohlc")
            current_high = _float(s1003.get("current_high"))
            current_low = _float(s1003.get("current_low"))
            if current_high is None or current_low is None or current_high < current_low:
                hard.append("actionable 2-1-2 requires finite current_high/current_low")
            entry_trigger = _float(s1003.get("entry_trigger"))
            if entry_trigger is None:
                hard.append("actionable 2-1-2 requires finite entry_trigger")

            if (
                direction in ("CALL", "PUT")
                and candle is not None
                and current_high is not None
                and current_low is not None
                and current_high >= current_low
                and entry_trigger is not None
            ):
                _, inside_high, inside_low, _ = candle
                current_type = classify_bar(
                    StratBar(high=current_high, low=current_low),
                    StratBar(high=inside_high, low=inside_low),
                )
                expected_type = TWO_UP if direction == "CALL" else TWO_DOWN
                expected_entry = inside_high if direction == "CALL" else inside_low
                if current_type != expected_type:
                    hard.append(
                        f"actionable {direction} 2-1-2 right-side candle is "
                        f"{current_type!r}, expected {expected_type!r}"
                    )
                if not _same_price(entry_trigger, expected_entry):
                    hard.append(
                        f"actionable {direction} 2-1-2 entry_trigger {entry_trigger:g} "
                        f"does not equal inside-bar {'high' if direction == 'CALL' else 'low'} "
                        f"{expected_entry:g}"
                    )

        verdict_line = (s1003.get("verdict") or "").strip().upper()
        if not verdict_line:
            hard.append("10:03 stage has no verdict line")
        elif "NO ACTIONABLE" in setup and not verdict_line.startswith("WAIT"):
            hard.append(
                f"canonical_setup is NO ACTIONABLE but verdict is {verdict_line!r}, not WAIT"
            )

    for key in present:
        section = stages[key]
        role = (section.get("signa_role") or "").strip().upper()
        if role and role != "OBSERVATIONAL":
            hard.append(
                f"stage {key} records signa_role={role!r}; Signa must be OBSERVATIONAL"
            )
        if _truthy(section.get("signa_used_as_authority")) is True:
            hard.append(f"stage {key} used Signa as authority")
        gex = (section.get("gex_regime") or "").strip().upper()
        if gex and gex != UNAVAILABLE:
            source = (section.get("gex_source") or "").strip().lower()
            if not source.startswith("verified:"):
                hard.append(
                    f"stage {key} records gex_regime={gex!r} without a verified gex_source"
                )
        for flip in ("spy_flip", "qqq_flip"):
            value = (section.get(flip) or "").strip().upper()
            source = (section.get("gex_source") or "").strip().lower()
            if value and value != UNAVAILABLE and not source.startswith("verified:"):
                hard.append(
                    f"stage {key} records {flip}={value!r} without a verified gex_source"
                )

    if "0926" in stages:
        if not (s0926.get("signa_role") or "").strip():
            soft.append("09:26 stage does not state signa_role")
        if not (s0926.get("gex_regime") or "").strip():
            soft.append("09:26 stage does not state gex_regime (expected UNAVAILABLE)")
        if not (s0926.get("sources") or "").strip():
            soft.append("09:26 stage does not list sources")

    if hard:
        verdict = ForwardSessionVerdict.INVALID
    elif soft:
        verdict = ForwardSessionVerdict.DEGRADED
    else:
        verdict = ForwardSessionVerdict.VALID

    return ForwardSessionResult(
        verdict=verdict,
        hard_failures=tuple(hard),
        soft_gaps=tuple(soft),
        locked_ticker=locked,
        stages_present=present,
        stage_retrieved_at=dict(retrieved),
    )


def check_forward_session_intake(payload: Any) -> ForwardSessionResult:
    """Non-throwing manual-payload entry point."""
    try:
        if isinstance(payload, str):
            return evaluate_forward_session(payload)
        if isinstance(payload, Mapping):
            text = payload.get("text")
            if not isinstance(text, str):
                return ForwardSessionResult(
                    verdict=ForwardSessionVerdict.INVALID,
                    hard_failures=("payload has no text",),
                )
            date = payload.get("session_date")
            return evaluate_forward_session(text, session_date=str(date) if date else None)
        return ForwardSessionResult(
            verdict=ForwardSessionVerdict.INVALID,
            hard_failures=(f"unsupported payload type {type(payload).__name__}",),
        )
    except Exception as exc:  # pragma: no cover
        return ForwardSessionResult(
            verdict=ForwardSessionVerdict.INVALID,
            hard_failures=(f"validator error: {exc}",),
        )
