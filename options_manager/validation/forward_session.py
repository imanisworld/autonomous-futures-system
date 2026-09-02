"""options_manager/validation/forward_session.py

Fail-closed validator for one morning forward-proof session record -- the
three-stage (09:26 / 09:46 / 10:03 ET) file written live during the
session. It answers whether that record is usable forward evidence:

    SESSION_VALID      every hard and soft check passed
    SESSION_DEGRADED   hard checks passed; some soft evidence is missing
    SESSION_INVALID    a hard check failed (lock broken, reconstruction,
                       future leakage, unverified GEX, Signa used as
                       authority, an incomplete candle treated as complete,
                       a 2-1-2 claimed without its preceding sequence)

Pure: parses text it is handed, reads no file, no clock, no network. Never
raises on malformed input -- malformed is SESSION_INVALID with reasons,
the same non-throwing pattern as every other check_*_intake() here.

Session record format (markdown, key: value lines under stage headers):

    ## 09:26 ET packet
    retrieved_at: 2026-09-02T13:26:10+00:00
    locked_ticker: XYZ
    runner_up: ABC
    selection_rule: ...
    gex_regime: UNAVAILABLE
    signa_role: OBSERVATIONAL
    orb_high: NOT STARTED
    ## 09:46 ET ORB update
    retrieved_at: ...
    locked_ticker: XYZ
    orb_high: 123.45
    orb_low: 121.00
    orb_bars_retrieved_at: ...
    ## 10:03 ET verdict
    retrieved_at: ...
    locked_ticker: XYZ
    candle_0930_complete: true
    strat_type_0930: 2U
    preceding_sequence: 2D,1
    canonical_setup: NO ACTIONABLE 2-1-2
    verdict: WAIT — NO ACTIONABLE SETUP
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import Enum
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

STAGES: tuple[tuple[str, str], ...] = (
    ("0926", r"^##\s*09:26\s*ET"),
    ("0946", r"^##\s*09:46\s*ET"),
    ("1003", r"^##\s*10:03\s*ET"),
)
# (earliest acceptable, must be strictly before) in ET for each stage's retrieved_at.
STAGE_WINDOWS: dict[str, tuple[time, time]] = {
    "0926": (time(9, 15), time(9, 30)),   # premarket packet: before the open, no exception
    "0946": (time(9, 45), time(10, 0)),   # ORB final only after 09:45
    "1003": (time(10, 0), time(16, 0)),   # first 30m candle complete only after 10:00
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
    stage_retrieved_at: Mapping[str, Optional[str]] = None  # type: ignore[assignment]


def parse_session_markdown(text: str) -> dict[str, dict[str, str]]:
    """Stage key -> {field: value}. Only the FIRST occurrence of a stage
    header is used; a duplicate header is recorded under ``_duplicate_<stage>``
    so the validator can reject it. Non key:value lines are ignored."""
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
        return None  # naive timestamps are not evidence
    return parsed


def _float(value: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _truthy(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in ("true", "yes", "1"):
        return True
    if lowered in ("false", "no", "0"):
        return False
    return None


def evaluate_forward_session(text: str, *, session_date: Optional[str] = None) -> ForwardSessionResult:
    hard: list[str] = []
    soft: list[str] = []
    try:
        stages = parse_session_markdown(text or "")
    except Exception as exc:  # pragma: no cover - parser is total, kept for fail-closed symmetry
        return ForwardSessionResult(verdict=ForwardSessionVerdict.INVALID, hard_failures=(f"unparseable session record: {exc}",))
    meta = stages.get("_meta", {})
    present = tuple(key for key, _ in STAGES if key in stages)
    for key, _ in STAGES:
        if key not in stages:
            hard.append(f"stage {key} missing")
    for key, _ in STAGES:
        if meta.get(f"duplicate_{key}") == "true":
            hard.append(f"stage {key} appears more than once (possible rewrite)")
    expected_order = ",".join(k for k, _ in STAGES if k in stages)
    if meta.get("order", "") != expected_order:
        hard.append(f"stages out of chronological order: {meta.get('order', '')}")

    # --- retrieval timestamps: present, aware, ordered, inside each stage window --
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
            hard.append(f"stage {key} retrieved on {local.strftime('%Y-%m-%d')}, not session date {session_date}")
        lo, hi = STAGE_WINDOWS[key]
        if not (lo <= local.time() < hi):
            hard.append(f"stage {key} retrieved at {local.strftime('%H:%M:%S')} ET, outside its window {lo.strftime('%H:%M')}–{hi.strftime('%H:%M')}")
    keys = [k for k, _ in STAGES if k in parsed_ts]
    for earlier, later in zip(keys, keys[1:]):
        if parsed_ts[later] <= parsed_ts[earlier]:
            hard.append(f"stage {later} retrieved_at is not after stage {earlier}")

    # --- reconstruction language anywhere ---------------------------------------
    lowered = (text or "").lower()
    for word in _RECONSTRUCTION_WORDS:
        if word in lowered:
            hard.append(f"record contains reconstruction language: {word!r}")
    for key in present:
        if _truthy(stages[key].get("reconstructed")) is True:
            hard.append(f"stage {key} marked reconstructed")

    # --- ticker lock + runner-up --------------------------------------------------
    s0926 = stages.get("0926", {})
    s0946 = stages.get("0946", {})
    s1003 = stages.get("1003", {})
    locked = (s0926.get("locked_ticker") or "").strip().upper() or None
    if "0926" in stages and not locked:
        hard.append("09:26 stage has no locked_ticker")
    for key, section in (("0946", s0946), ("1003", s1003)):
        if key in stages:
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
                hard.append(f"09:26 stage has {field}={value!r} before the opening range existed")

    # --- ORB timing and values --------------------------------------------------------
    if "0946" in stages:
        orb_ts = _parse_ts(s0946.get("orb_bars_retrieved_at"))
        if orb_ts is None:
            soft.append("09:46 stage has no timezone-aware orb_bars_retrieved_at")
        elif orb_ts.astimezone(ET).time() < time(9, 45):
            hard.append("ORB recorded before 09:45 ET: opening range was not final")
        high, low = _float(s0946.get("orb_high")), _float(s0946.get("orb_low"))
        if high is None or low is None:
            hard.append("09:46 stage lacks finite orb_high/orb_low")
        elif high <= low:
            hard.append(f"orb_high {high} is not above orb_low {low}")

    # --- completed-candle proof and the actual preceding Strat sequence ---------
    if "1003" in stages:
        complete = _truthy(s1003.get("candle_0930_complete"))
        if complete is not True:
            hard.append("10:03 stage does not assert candle_0930_complete: true")
        strat = (s1003.get("strat_type_0930") or "").strip().upper()
        if strat not in _STRAT_TYPES:
            hard.append(f"10:03 stage strat_type_0930 {strat or 'missing'!r} is not one of 1/2U/2D/3")
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
            # A 2-1-2 entry off the 09:30 candle needs the 09:30 candle to BE the
            # inside bar (type 1) with a directional bar before it; the 09:30 candle
            # itself can never be the whole pattern.
            if strat != "1":
                hard.append("2-1-2 claimed but the completed 09:30 candle is not an inside bar (1)")
            if not sequence or sequence[-1] not in ("2U", "2D"):
                hard.append("2-1-2 claimed without a directional (2U/2D) candle immediately preceding the inside bar")
        verdict_line = (s1003.get("verdict") or "").strip().upper()
        if not verdict_line:
            hard.append("10:03 stage has no verdict line")
        elif "NO ACTIONABLE" in setup and not verdict_line.startswith("WAIT"):
            hard.append(f"canonical_setup is NO ACTIONABLE but verdict is {verdict_line!r}, not WAIT")

    # --- Signa observational only; GEX verified only ----------------------------
    for key in present:
        section = stages[key]
        role = (section.get("signa_role") or "").strip().upper()
        if role and role != "OBSERVATIONAL":
            hard.append(f"stage {key} records signa_role={role!r}; Signa must be OBSERVATIONAL")
        if _truthy(section.get("signa_used_as_authority")) is True:
            hard.append(f"stage {key} used Signa as authority")
        gex = (section.get("gex_regime") or "").strip().upper()
        if gex and gex != UNAVAILABLE:
            source = (section.get("gex_source") or "").strip().lower()
            if not source.startswith("verified:"):
                hard.append(f"stage {key} records gex_regime={gex!r} without a verified gex_source")
        for flip in ("spy_flip", "qqq_flip"):
            value = (section.get(flip) or "").strip().upper()
            if value and value != UNAVAILABLE and not (section.get("gex_source") or "").strip().lower().startswith("verified:"):
                hard.append(f"stage {key} records {flip}={value!r} without a verified gex_source")
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
    """Manual-payload entry point: a str (the record text) or a mapping with
    ``text`` and optional ``session_date``. Never raises."""
    try:
        if isinstance(payload, str):
            return evaluate_forward_session(payload)
        if isinstance(payload, Mapping):
            text = payload.get("text")
            if not isinstance(text, str):
                return ForwardSessionResult(verdict=ForwardSessionVerdict.INVALID, hard_failures=("payload has no text",))
            date = payload.get("session_date")
            return evaluate_forward_session(text, session_date=str(date) if date else None)
        return ForwardSessionResult(verdict=ForwardSessionVerdict.INVALID, hard_failures=(f"unsupported payload type {type(payload).__name__}",))
    except Exception as exc:  # pragma: no cover
        return ForwardSessionResult(verdict=ForwardSessionVerdict.INVALID, hard_failures=(f"validator error: {exc}",))
