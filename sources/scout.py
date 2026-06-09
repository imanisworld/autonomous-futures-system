"""Read-only Scout signal adapter (PAPER / CONTEXT ONLY).

Scout emits TradingView/Discord-style text alerts. This module parses
those alerts into a frozen ``ScoutSignal`` and classifies how they relate to the
*internal* system decision — purely as confluence context.

HARD SAFETY CONTRACT (do not weaken):
  * Scout NEVER authorizes a trade. ``scout_trade_authorized`` is always False.
  * Scout NEVER bypasses risk, session, or state gates — those are inputs here,
    decided elsewhere, and a failed gate forces ``BLOCKED``.
  * Scout NEVER overrides internal direction — a direction disagreement forces
    ``CONFLICT`` (no trade), never a flip.
  * Missing / malformed fields fail CLOSED: no crash, no trade, parse_error
    with a reason.
  * This module imports NO broker / execution code and places no orders.

The only outputs are one of four classifications — WATCH_ONLY, CONFIRMATION,
CONFLICT, BLOCKED — plus a journal-ready dict. ``scout_paper_eligible`` is a
*classification* gate for possible FUTURE paper influence; it is NOT permission
to execute.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

SCOUT_SOURCE = "scout"

# Symbol mapping is for CONTEXT ALIGNMENT ONLY (compare Scout vs internal).
# It does not route orders.
SYMBOL_MAP: dict[str, str] = {
    "NQ1!": "MNQ",
    "ES1!": "MES",
}

# ── Classifications (the only things Scout may produce) ───────────────────────
WATCH_ONLY = "WATCH_ONLY"
CONFIRMATION = "CONFIRMATION"
CONFLICT = "CONFLICT"
BLOCKED = "BLOCKED"

_PASS_TRUE = "✅"
_PASS_FALSE = "❌"
_PASS_WARN = "⚠️"

# BUY → LONG, SELL → SHORT (for internal-direction comparison only)
_SIDE_TO_INTERNAL = {"BUY": "LONG", "SELL": "SHORT"}


@dataclass(frozen=True)
class ScoutSignal:
    """Parsed Scout alert. ``ok=False`` means a parse_error (fail closed)."""

    ok: bool
    source: str = SCOUT_SOURCE
    symbol_raw: Optional[str] = None
    symbol_mapped: Optional[str] = None
    side: Optional[str] = None          # BUY | SELL
    grade: Optional[str] = None         # A | B | C ...
    score: Optional[int] = None
    score_max: Optional[int] = None
    setup_type: Optional[str] = None    # e.g. Continuation | Reversal
    bias: Optional[str] = None          # e.g. Bullish Trend
    entry: Optional[float] = None
    tp1: Optional[float] = None
    tp2: Optional[float] = None
    stop: Optional[float] = None
    rr: Optional[float] = None
    volume_pass: Optional[bool] = None
    volatility_pass: Optional[bool] = None
    trend_pass: Optional[bool] = None
    momentum_pass: Optional[bool] = None
    insight: Optional[str] = None
    alert_time: Optional[str] = None
    alert_id: Optional[str] = None
    error: Optional[str] = None
    raw_text: Optional[str] = None

    def internal_side(self) -> Optional[str]:
        """Map Scout side to internal LONG/SHORT, or None if unknown."""
        if self.side is None:
            return None
        return _SIDE_TO_INTERNAL.get(self.side.upper())

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "ok": self.ok,
            "symbol_raw": self.symbol_raw,
            "symbol_mapped": self.symbol_mapped,
            "side": self.side,
            "grade": self.grade,
            "score": self.score,
            "score_max": self.score_max,
            "setup_type": self.setup_type,
            "bias": self.bias,
            "entry": self.entry,
            "tp1": self.tp1,
            "tp2": self.tp2,
            "stop": self.stop,
            "rr": self.rr,
            "volume_pass": self.volume_pass,
            "volatility_pass": self.volatility_pass,
            "trend_pass": self.trend_pass,
            "momentum_pass": self.momentum_pass,
            "insight": self.insight,
            "alert_time": self.alert_time,
            "alert_id": self.alert_id,
            "error": self.error,
        }


# ── Parsing helpers (each is tolerant and never raises) ──────────────────────

def _to_float(value: str) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pass_flag(token: str) -> Optional[bool]:
    """✅ → True; ❌/⚠️ → False (only a clean pass counts). None if absent."""
    if token is None:
        return None
    token = token.strip()
    if token.startswith(_PASS_TRUE):
        return True
    if token.startswith(_PASS_FALSE) or token.startswith("⚠"):
        return False
    return None


def parse_scout_alert(text: str) -> ScoutSignal:
    """Parse a Scout alert string into a ScoutSignal. Fail closed on any problem.

    Minimum required to be a usable signal: side (BUY/SELL), a symbol, and a
    numeric entry. Anything missing → ok=False with an error reason.
    """
    if not isinstance(text, str) or not text.strip():
        return ScoutSignal(ok=False, error="empty_or_non_string_payload", raw_text=text if isinstance(text, str) else None)

    raw = text
    try:
        # Side
        side_m = re.search(r"\b(BUY|SELL)\b", text)
        side = side_m.group(1) if side_m else None

        # Symbol (e.g. NQ1!, ES1!) — first ticker-like token
        sym_m = re.search(r"\b([A-Z]{1,4}\d*!)", text)
        symbol_raw = sym_m.group(1) if sym_m else None
        symbol_mapped = SYMBOL_MAP.get(symbol_raw) if symbol_raw else None

        # Grade + score  e.g. "A (9/12)"
        grade = score = score_max = None
        gm = re.search(r"\b([A-F][+-]?)\s*\(\s*(\d+)\s*/\s*(\d+)\s*\)", text)
        if gm:
            grade = gm.group(1)
            score = int(gm.group(2))
            score_max = int(gm.group(3))

        # Setup type • bias   e.g. "Continuation • Bullish Trend"
        setup_type = bias = None
        sb = re.search(r"(Continuation|Reversal|Breakout|Pullback|Range)\s*[•·|]\s*([^\n]+)", text, re.IGNORECASE)
        if sb:
            setup_type = sb.group(1).strip()
            bias = sb.group(2).strip()

        entry = _extract_price(r"Entry:\s*([\d.]+)", text)
        tp1 = _extract_price(r"TP1:\s*([\d.]+)", text)
        tp2 = _extract_price(r"TP2:\s*([\d.]+)", text)
        stop = _extract_price(r"Stop:\s*([\d.]+)", text)

        # R:R  e.g. "R:R: 1:0.7"  →  reward / risk
        rr = None
        rm = re.search(r"R:R:\s*([\d.]+)\s*:\s*([\d.]+)", text)
        if rm:
            risk = _to_float(rm.group(1))
            reward = _to_float(rm.group(2))
            if risk and reward is not None and risk != 0:
                rr = round(reward / risk, 4)

        volume_pass = _flag(r"Vol:\s*([^\n]+)", text)
        volatility_pass = _flag(r"Vola:\s*([^\n]+)", text)
        trend_pass = _flag(r"Trend:\s*([^\n]+)", text)
        momentum_pass = _flag(r"Mom:\s*([^\n]+)", text)

        insight = None
        im = re.search(r"(?:Nova|Scout) Insight:\s*([^\n]+)", text)
        if im:
            insight = im.group(1).strip()

        alert_time = None
        tm = re.search(r"\b(\d{1,2}:\d{2}:\d{2})\b", text)
        if tm:
            alert_time = tm.group(1)

        alert_id = None
        am = re.search(r"\b([A-Z0-9!]+-\d{4,}-\d{3,}-\d+)\b", text)
        if am:
            alert_id = am.group(1)

        # ── Fail-closed validation ──────────────────────────────────────────
        missing = []
        if side is None:
            missing.append("side")
        if symbol_raw is None:
            missing.append("symbol")
        if entry is None:
            missing.append("entry")
        if missing:
            return ScoutSignal(
                ok=False,
                error=f"parse_error: missing required fields: {', '.join(missing)}",
                symbol_raw=symbol_raw,
                symbol_mapped=symbol_mapped,
                side=side,
                entry=entry,
                raw_text=raw,
            )

        return ScoutSignal(
            ok=True,
            symbol_raw=symbol_raw,
            symbol_mapped=symbol_mapped,
            side=side,
            grade=grade,
            score=score,
            score_max=score_max,
            setup_type=setup_type,
            bias=bias,
            entry=entry,
            tp1=tp1,
            tp2=tp2,
            stop=stop,
            rr=rr,
            volume_pass=volume_pass,
            volatility_pass=volatility_pass,
            trend_pass=trend_pass,
            momentum_pass=momentum_pass,
            insight=insight,
            alert_time=alert_time,
            alert_id=alert_id,
            raw_text=raw,
        )
    except Exception as exc:  # never crash the caller — fail closed
        return ScoutSignal(ok=False, error=f"parse_error: {type(exc).__name__}: {exc}", raw_text=raw)


def _extract_price(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text)
    return _to_float(m.group(1)) if m else None


def _flag(pattern: str, text: str) -> Optional[bool]:
    m = re.search(pattern, text)
    return _pass_flag(m.group(1)) if m else None


# ── Quality gate (CLASSIFICATION ONLY — never execution permission) ──────────

def scout_paper_eligible(
    scout: ScoutSignal,
    *,
    internal_agrees: bool,
    session_allowed: bool,
    risk_allowed: bool,
) -> bool:
    """Whether a Scout signal MIGHT influence FUTURE paper logic.

    This is a classification flag only. It does NOT authorize any order.
    All conditions must hold (item 12 of the spec).
    """
    if not scout.ok:
        return False
    return bool(
        scout.grade == "A"
        and scout.score is not None
        and scout.score_max is not None
        and scout.score >= 9
        and scout.volume_pass is True
        and scout.volatility_pass is True
        and scout.momentum_pass is True
        and scout.stop is not None
        and scout.tp1 is not None
        and scout.rr is not None
        and scout.rr >= 1.0
        and internal_agrees
        and session_allowed
        and risk_allowed
    )


# ── Classification ───────────────────────────────────────────────────────────

def classify_scout(
    scout: ScoutSignal,
    *,
    internal_signal_side: Optional[str],   # "LONG" | "SHORT" | None
    internal_signal_present: bool,
    session_allowed: bool,
    risk_allowed: bool,
) -> dict[str, Any]:
    """Classify a Scout signal against the internal decision and the gates.

    Returns a journal-ready dict. ``final_decision`` is one of WATCH_ONLY,
    CONFIRMATION, CONFLICT, BLOCKED. Never authorizes a trade.

    Precedence (fail-closed): a parse_error or a failed session/risk gate ->
    BLOCKED; then no internal signal -> WATCH_ONLY; then direction agreement ->
    CONFIRMATION; else CONFLICT.
    """
    scout_internal = scout.internal_side()
    internal_present = bool(internal_signal_present and internal_signal_side)
    internal_agrees = bool(
        internal_present
        and scout_internal is not None
        and scout_internal == internal_signal_side
    )
    rr_acceptable = bool(scout.ok and scout.rr is not None and scout.rr >= 1.0)

    if not scout.ok:
        final, reason = BLOCKED, (scout.error or "parse_error")
    elif not session_allowed or not risk_allowed:
        gate = "session" if not session_allowed else "risk"
        final, reason = BLOCKED, f"{gate} gate not satisfied; Scout is context only"
    elif not internal_present:
        final, reason = WATCH_ONLY, "No internal signal; Scout logged as watch-only context"
    elif internal_agrees:
        final, reason = CONFIRMATION, f"Scout {scout.side} agrees with internal {internal_signal_side}"
    else:
        final, reason = CONFLICT, (
            f"Scout {scout.side} ({scout_internal}) conflicts with internal "
            f"{internal_signal_side}; no trade"
        )

    paper_eligible = scout_paper_eligible(
        scout,
        internal_agrees=internal_agrees,
        session_allowed=session_allowed,
        risk_allowed=risk_allowed,
    )

    entry: dict[str, Any] = dict(scout.to_dict())
    entry.update(
        {
            "internal_signal_side": internal_signal_side,
            "internal_signal_present": internal_present,
            "session_allowed": bool(session_allowed),
            "risk_allowed": bool(risk_allowed),
            "rr_acceptable": rr_acceptable,
            "final_decision": final,
            "reason": reason,
            "scout_paper_eligible": paper_eligible,
            # Explicit, immutable safety assertion — Scout never executes.
            "scout_trade_authorized": False,
        }
    )
    return entry
