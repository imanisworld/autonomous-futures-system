"""Fail-closed forward-session validator: SESSION_VALID / DEGRADED / INVALID."""

from __future__ import annotations

import pytest

from options_manager.validation import (
    ForwardSessionVerdict,
    check_forward_session_intake,
    evaluate_forward_session,
    parse_session_markdown,
)

VALID = """# morning packet 2026-09-02
## 09:26 ET packet
retrieved_at: 2026-09-02T13:26:10+00:00
session_date: 2026-09-02
selection_rule: largest |premarket gap %| with premarket trades and weekly options
locked_ticker: XYZ
runner_up: ABC
gex_regime: UNAVAILABLE
spy_flip: UNAVAILABLE
qqq_flip: UNAVAILABLE
signa_role: OBSERVATIONAL
signa_grade: B
orb_high: NOT STARTED
orb_low: NOT STARTED
sources: robinhood quotes/bars; signa client

MARKET CONTEXT (template prose follows)
GEX regime: GEX unavailable

## 09:46 ET ORB update
retrieved_at: 2026-09-02T13:46:05+00:00
locked_ticker: XYZ
orb_bars_retrieved_at: 2026-09-02T13:46:02+00:00
orb_window: 09:30-09:45 ET
orb_high: 123.45
orb_low: 121.00

## 10:03 ET verdict
retrieved_at: 2026-09-02T14:03:20+00:00
locked_ticker: XYZ
candle_0930_complete: true
candle_0930_ohlc: 121.5/123.9/120.8/122.0
strat_type_0930: 2U
preceding_sequence: 2D,1
canonical_setup: NO ACTIONABLE 2-1-2
verdict: WAIT — NO ACTIONABLE SETUP
gex_regime: UNAVAILABLE
signa_role: OBSERVATIONAL
"""


def _set(text: str, stage: str, key: str, value: str | None) -> str:
    """Replace (or drop when value is None) one key line inside one stage."""
    headers = {"0926": "## 09:26 ET", "0946": "## 09:46 ET", "1003": "## 10:03 ET"}
    out, current, done = [], None, False
    for line in text.splitlines():
        if line.startswith("## "):
            current = next((k for k, h in headers.items() if line.startswith(h)), None)
        if current == stage and line.startswith(f"{key}:") and not done:
            done = True
            if value is None:
                continue
            out.append(f"{key}: {value}")
            continue
        out.append(line)
    assert done, f"{key} not found in stage {stage}"
    return "\n".join(out) + "\n"


def test_valid_session():
    result = evaluate_forward_session(VALID, session_date="2026-09-02")
    assert result.verdict is ForwardSessionVerdict.VALID, (result.hard_failures, result.soft_gaps)
    assert result.locked_ticker == "XYZ"
    assert result.stages_present == ("0926", "0946", "1003")
    assert result.stage_retrieved_at["1003"] == "2026-09-02T14:03:20+00:00"


def test_parser_uses_first_occurrence_and_flags_duplicates():
    stages = parse_session_markdown(VALID + "\n## 09:26 ET packet\nlocked_ticker: ZZZ\n")
    assert stages["0926"]["locked_ticker"] == "XYZ"
    assert stages["_meta"]["duplicate_0926"] == "true"
    assert stages["_meta"]["order"] == "0926,0946,1003"


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda t: t.split("## 10:03")[0], "stage 1003 missing"),
        (lambda t: t + "\n## 09:46 ET ORB update\nlocked_ticker: XYZ\n", "appears more than once"),
        (lambda t: _set(t, "0926", "retrieved_at", None), "no timezone-aware retrieved_at"),
        (lambda t: _set(t, "0926", "retrieved_at", "2026-09-02T13:26:10"), "no timezone-aware retrieved_at"),
        (lambda t: _set(t, "0926", "retrieved_at", "2026-09-02T13:31:00+00:00"), "outside its window"),  # after the open
        (lambda t: _set(t, "0946", "retrieved_at", "2026-09-02T13:44:00+00:00"), "outside its window"),  # ORB not final
        (lambda t: _set(t, "1003", "retrieved_at", "2026-09-02T13:59:00+00:00"), "outside its window"),  # candle not closed
        (lambda t: _set(t, "0946", "retrieved_at", "2026-09-02T13:25:00+00:00"), "not after stage 0926"),
        (lambda t: _set(t, "1003", "retrieved_at", "2026-09-03T14:03:20+00:00"), "not session date"),
        (lambda t: t + "\nnote: values reconstructed from the chart afterwards\n", "reconstruction language"),
        (lambda t: _set(t, "1003", "verdict", "WAIT — NO ACTIONABLE SETUP\nreconstructed: true"), "marked reconstructed"),
        (lambda t: _set(t, "0946", "locked_ticker", "QQQ"), "ticker lock broken"),
        (lambda t: _set(t, "1003", "locked_ticker", None), "does not restate locked_ticker"),
        (lambda t: _set(t, "0926", "locked_ticker", None), "no locked_ticker"),
        (lambda t: _set(t, "0926", "orb_high", "123.4"), "before the opening range existed"),
        (lambda t: _set(t, "0946", "orb_bars_retrieved_at", "2026-09-02T13:40:00+00:00"), "ORB recorded before 09:45"),
        (lambda t: _set(t, "0946", "orb_high", "nan"), "lacks finite orb_high/orb_low"),
        (lambda t: _set(t, "0946", "orb_high", "120.0"), "not above orb_low"),
        (lambda t: _set(t, "1003", "candle_0930_complete", "false"), "candle_0930_complete: true"),
        (lambda t: _set(t, "1003", "candle_0930_complete", None), "candle_0930_complete: true"),
        (lambda t: _set(t, "1003", "strat_type_0930", "4"), "not one of 1/2U/2D/3"),
        (lambda t: _set(t, "1003", "preceding_sequence", "2D,X"), "non-Strat type"),
        (lambda t: _set(t, "1003", "canonical_setup", "2-1-2 CALL entry over 123.9"), "not an inside bar"),
        (lambda t: _set(_set(t, "1003", "strat_type_0930", "1"), "1003", "canonical_setup", "2-1-2 CALL"), "without a directional"),
        (lambda t: _set(t, "1003", "canonical_setup", None), "no canonical_setup"),
        (lambda t: _set(t, "1003", "verdict", "TAKE"), "not WAIT"),
        (lambda t: _set(t, "1003", "verdict", None), "no verdict line"),
        (lambda t: _set(t, "0926", "signa_role", "GATE"), "must be OBSERVATIONAL"),
        (lambda t: _set(t, "1003", "verdict", "WAIT — NO ACTIONABLE SETUP\nsigna_used_as_authority: true"), "used Signa as authority"),
        (lambda t: _set(t, "0926", "gex_regime", "POSITIVE"), "without a verified gex_source"),
        (lambda t: _set(t, "0926", "spy_flip", "760"), "without a verified gex_source"),
    ],
)
def test_hard_failures_are_invalid(mutate, fragment):
    result = evaluate_forward_session(mutate(VALID), session_date="2026-09-02")
    assert result.verdict is ForwardSessionVerdict.INVALID, (result.hard_failures, result.soft_gaps)
    assert any(fragment in h for h in result.hard_failures), result.hard_failures


def test_legitimate_212_on_an_inside_0930_candle_is_valid():
    text = _set(VALID, "1003", "strat_type_0930", "1")
    text = _set(text, "1003", "preceding_sequence", "1,2U")
    text = _set(text, "1003", "canonical_setup", "2-1-2 CALL: entry break of 09:30 high 123.9, invalidation 120.8")
    text = _set(text, "1003", "verdict", "WAIT — trigger not yet broken")
    result = evaluate_forward_session(text, session_date="2026-09-02")
    assert result.verdict is ForwardSessionVerdict.VALID, result.hard_failures


def test_verified_gex_source_is_accepted():
    text = _set(VALID, "0926", "gex_regime", "POSITIVE\ngex_source: verified:vendor-feed-x")
    assert evaluate_forward_session(text, session_date="2026-09-02").verdict is ForwardSessionVerdict.VALID


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (lambda t: _set(t, "0926", "runner_up", None), "no runner_up"),
        (lambda t: _set(t, "0926", "selection_rule", None), "no selection_rule"),
        (lambda t: _set(t, "0946", "orb_bars_retrieved_at", None), "no timezone-aware orb_bars_retrieved_at"),
        (lambda t: _set(t, "1003", "preceding_sequence", None), "no preceding_sequence"),
        (lambda t: _set(t, "0926", "signa_role", None), "does not state signa_role"),
        (lambda t: _set(t, "0926", "gex_regime", None), "does not state gex_regime"),
        (lambda t: _set(t, "0926", "sources", None), "does not list sources"),
    ],
)
def test_soft_gaps_degrade(mutate, fragment):
    result = evaluate_forward_session(mutate(VALID), session_date="2026-09-02")
    assert result.verdict is ForwardSessionVerdict.DEGRADED, (result.hard_failures, result.soft_gaps)
    assert any(fragment in s for s in result.soft_gaps)
    assert result.hard_failures == ()


def test_intake_never_raises():
    assert check_forward_session_intake(None).verdict is ForwardSessionVerdict.INVALID
    assert check_forward_session_intake({"text": 42}).verdict is ForwardSessionVerdict.INVALID
    assert check_forward_session_intake("").verdict is ForwardSessionVerdict.INVALID
    assert check_forward_session_intake({"text": VALID, "session_date": "2026-09-02"}).verdict is ForwardSessionVerdict.VALID
    assert check_forward_session_intake(VALID).verdict is ForwardSessionVerdict.VALID


def test_module_is_pure():
    import options_manager.validation.forward_session as mod
    from pathlib import Path

    source = Path(mod.__file__).read_text()
    for forbidden in ("import os", "subprocess", "open(", "requests", "httpx", "datetime.now", "socket"):
        assert forbidden not in source, forbidden
