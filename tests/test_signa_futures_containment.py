"""Futures containment proof for the sources/signa_client.py rework.

`sources/signa_client.py` is SHARED. The options lane got a new observational
API (`SignaReading` / `parse_signa_reading`), but the legacy half still feeds
the futures path:

    webhook/runner.py:2711  enrich_payload_with_signa(payload, cfg)
      -> AlertPayload.signa_{grade,score,daily_direction,weekly_direction}
      -> webhook/state_builder.py:410  SignaContext(...)
      -> strategy/signa_gate.py        evaluate_signa(state, direction)

These tests pin the LEGACY behavior exactly as it was before the rework, so a
future "cleanup" of the shared module cannot silently change futures gating.

Expected values below are the pre-rework outputs, derived from the original
implementation and checked against a real captured API response. If one of
these fails, futures behavior moved — that is the regression, not the test.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from sources.signa_client import (
    SignaClient,
    enrich_payload_with_signa,
    parse_signa_signal,
)
from strategy.signa_gate import evaluate_signa

FIXTURES = Path(__file__).parent / "fixtures" / "signa_api_v1"


def _payload(tf: str = "1d") -> dict:
    return json.loads((FIXTURES / f"signa_spy_{tf}.json").read_text())


# --- legacy parse output is byte-for-byte what it was ----------------------


def test_legacy_parse_output_is_unchanged_on_a_real_response():
    """Pinned against the real captured 1d response. Every field is the value
    the ORIGINAL parser produced."""
    signal = parse_signa_signal("SPY", _payload("1d"))

    assert signal.ok is True
    assert signal.grade == "B"              # engine.grade preferred over signa.grade "C"
    assert signal.score == 81.0             # engine.score
    assert signal.daily_direction == "WAIT"  # data.direction, passed through RAW
    assert signal.weekly_direction is None   # field absent from the API entirely
    assert signal.action == "HOLD"
    assert signal.confidence == 31.0        # engine.confidence, NOT the score
    assert signal.risk_rating == "MODERATE"


def test_legacy_parse_is_identical_across_timeframe_fixtures():
    """The legacy path prefers `engine`, which is timeframe-invariant. This was
    true before the rework and must stay true."""
    signals = [parse_signa_signal("SPY", _payload(tf)) for tf in ("1d", "4h", "1h")]
    assert {s.grade for s in signals} == {"B"}
    assert {s.score for s in signals} == {81.0}


def test_legacy_payload_fields_are_unchanged():
    signal = parse_signa_signal("SPY", _payload("1d"))
    assert signal.to_payload_fields() == {
        "signa_grade": "B",
        "signa_score": 81.0,
        "signa_daily_direction": "WAIT",
        "signa_weekly_direction": None,
    }


# --- the lossy A+ truncation is DELIBERATELY retained on this path ---------


def test_legacy_path_still_truncates_a_plus_to_a():
    """NOT a bug on this path. strategy/signa_gate.py tests `grade in {A, B}`;
    preserving "A+" here would flip an A+ ticker from PASS to NEUTRAL, which is
    a live futures gating change. The options lane uses parse_signa_reading,
    which preserves "A+" verbatim."""
    payload = _payload("1d")
    payload["engine"]["grade"] = "A+"
    assert parse_signa_signal("SPY", payload).grade == "A"


def test_futures_gate_still_passes_on_an_a_plus_ticker():
    """The end-to-end consequence of the line above, asserted at the gate."""

    class _Signa:
        grade = parse_signa_signal(
            "SPY", {**_payload("1d"), "engine": {**_payload("1d")["engine"], "grade": "A+"}}
        ).grade
        score = 81.0
        weekly_direction = None
        daily_direction = "UP"

    class _State:
        signa = _Signa()

    result = evaluate_signa(_State(), "LONG")
    assert result.status == "PASS", "an A+ ticker must still PASS the futures gate"


@pytest.mark.parametrize(
    "grade,expected_status",
    [("A", "PASS"), ("B", "PASS"), ("C", "FAIL"), ("D", "FAIL"), ("F", "FAIL")],
)
def test_futures_signa_gate_verdicts_are_unchanged(grade, expected_status):
    class _Signa:
        pass

    signa = _Signa()
    signa.grade = grade
    signa.score = 50.0
    signa.weekly_direction = None
    signa.daily_direction = "UP"

    class _State:
        pass

    state = _State()
    state.signa = signa

    assert evaluate_signa(state, "LONG").status == expected_status


# --- the tf param change is behavior-neutral for futures -------------------


def test_futures_path_only_ever_requests_daily():
    """`enrich_payload_with_signa` calls fetch_signal(symbol) with the default
    timeframe. Before the fix the server IGNORED `timeframe=` and returned 1d;
    now it is sent `tf=1d` and returns 1d. Same data, so the futures path is
    unaffected. Verified live 2026-07-29:

        {'sym':'SPY'}                    -> timeframe='1d'
        {'sym':'SPY','timeframe':'4h'}   -> timeframe='1d'   (ignored)
        {'sym':'SPY','tf':'1d'}          -> timeframe='1d'
    """
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json=_payload("1d"))

    client = SignaClient(
        api_key="k",
        client=httpx.Client(
            base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler)
        ),
    )

    class _Cfg:
        signa_api_enabled = True
        signa_symbol_map = {"MNQ": "QQQ"}
        signa_base_url = "https://app.getsigna.ai"
        signa_timeout_seconds = 3.0

    class _Payload:
        ticker = "MNQ1!"
        signa_grade = None
        signa_score = None
        signa_daily_direction = None
        signa_weekly_direction = None

    payload = _Payload()
    enrich_payload_with_signa(payload, _Cfg(), client=client)

    assert seen == {"sym": "QQQ", "tf": "1d"}, "futures path must still request daily"
    # And the enrichment wrote exactly the legacy fields, unchanged.
    assert payload.signa_grade == "B"
    assert payload.signa_score == 81.0
    assert payload.signa_daily_direction == "WAIT"
    assert payload.signa_weekly_direction is None


def test_enrichment_still_skips_when_payload_already_populated():
    """Legacy short-circuit preserved: a payload that already carries Signa
    fields must not trigger a network call."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=_payload("1d"))

    client = SignaClient(
        api_key="k",
        client=httpx.Client(
            base_url="https://app.getsigna.ai", transport=httpx.MockTransport(handler)
        ),
    )

    class _Cfg:
        signa_api_enabled = True
        signa_symbol_map = {}
        signa_base_url = "https://app.getsigna.ai"
        signa_timeout_seconds = 3.0

    class _Payload:
        ticker = "MNQ1!"
        signa_grade = "A"
        signa_score = 90.0
        signa_daily_direction = "UP"
        signa_weekly_direction = "UP"

    assert enrich_payload_with_signa(_Payload(), _Cfg(), client=client) is None
    assert calls == []


def test_enrichment_disabled_makes_no_call():
    class _Cfg:
        signa_api_enabled = False

    assert enrich_payload_with_signa(object(), _Cfg()) is None


# --- structural guard: the futures gate modules were not touched -----------


def test_strategy_signa_gate_does_not_import_the_new_observational_api():
    """strategy/ must keep using the frozen legacy contract."""
    source = Path("strategy/signa_gate.py").read_text()
    assert "parse_signa_reading" not in source
    assert "SignaReading" not in source
