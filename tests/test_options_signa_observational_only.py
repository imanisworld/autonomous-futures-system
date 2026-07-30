"""Signa and GEX are OBSERVATIONAL METADATA ONLY.

The invariant these tests defend:

    The system-owned decision — price action, setup, contract quality, risk —
    must be IDENTICAL whether Signa is present, absent, neutral, or
    self-contradictory, and whether GEX is present or absent.

If any of these tests starts failing because a Signa or GEX field gained veto
power, that is the regression, not the test.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from alert_ranker.rh_options import (
    _hard_gates,
    _parse_rh_inputs,
    _signa_observations,
    evaluate_rh_options,
    sample_rh_options_payload,
)

NOW = datetime(2026, 6, 20, 15, 0, tzinfo=timezone.utc)


def _payload(**overrides):
    body = sample_rh_options_payload()
    body.update(overrides)
    return body


# Every way Signa/GEX can vary. The system verdict must not move across any of them.
SIGNA_VARIANTS = {
    "present_strong": {},
    "absent_entirely": {
        "signa_score": None, "signa_grade": None,
        "signa_daily_direction": None, "signa_weekly_direction": None,
    },
    "keys_removed": {"__remove__": ["signa_score", "signa_grade", "signa_daily_direction"]},
    "neutral": {"signa_daily_direction": "NEUTRAL", "signa_weekly_direction": "NEUTRAL"},
    "wait": {"signa_daily_direction": "WAIT"},
    "weak_grade": {"signa_grade": "F", "signa_score": 3},
    "zero_score": {"signa_score": 0},
    "opposing_direction": {"signa_daily_direction": "BEARISH"},
    "self_conflicting": {"signa_daily_direction": "BULLISH", "signa_weekly_direction": "BEARISH"},
    "a_plus": {"signa_grade": "A+"},
    "gex_absent": {"gex_regime": None},
    "gex_removed": {"__remove__": ["gex_regime"]},
    "gex_unrecognized": {"gex_regime": "SOME_NEW_VENDOR_LABEL"},
    "everything_absent": {
        "signa_score": None, "signa_grade": None, "signa_daily_direction": None,
        "signa_weekly_direction": None, "gex_regime": None,
    },
}


def _build(variant: dict):
    body = sample_rh_options_payload()
    for key in variant.get("__remove__", []):
        body.pop(key, None)
    body.update({k: v for k, v in variant.items() if k != "__remove__"})
    return _parse_rh_inputs(body)


@pytest.mark.parametrize("name,variant", sorted(SIGNA_VARIANTS.items()))
def test_hard_gates_are_invariant_to_signa_and_gex(name, variant):
    baseline = _hard_gates(_build({}), NOW)
    assert _hard_gates(_build(variant), NOW) == baseline, (
        f"variant {name!r} changed the system gates; Signa/GEX must not decide"
    )


@pytest.mark.parametrize("name,variant", sorted(SIGNA_VARIANTS.items()))
def test_full_decision_is_invariant_to_signa_and_gex(name, variant):
    def decide(v):
        return evaluate_rh_options(_build(v), now=NOW)

    baseline = decide({})
    result = decide(variant)
    assert result["decision"] == baseline["decision"], f"variant {name!r} changed the decision"
    assert result["failed_gates"] == baseline["failed_gates"]
    assert result["risk_result"] == baseline["risk_result"]
    assert result["order_ticket"] == baseline["order_ticket"]


def test_absent_signa_is_evaluable_at_all():
    """Signa used to be a required field: omitting it raised ValueError, which
    made an external vendor a hard dependency of the whole evaluation."""
    body = sample_rh_options_payload()
    for key in ("signa_score", "signa_grade", "signa_daily_direction", "gex_regime"):
        body.pop(key, None)
    result = evaluate_rh_options(_parse_rh_inputs(body), now=NOW)
    assert result["decision"] != "NO_TRADE"
    assert "signa_unavailable" in result["signa_observations"]
    assert "gex_unavailable" in result["signa_observations"]


def test_no_gate_mentions_signa_or_gex():
    """Structural guard: the failure vocabulary itself must stay clean."""
    for variant in SIGNA_VARIANTS.values():
        for failure in _hard_gates(_build(variant), NOW):
            assert "signa" not in failure.lower()
            assert "gex" not in failure.lower()
            assert "pinning" not in failure.lower()


# --- fabricated GEX must not come back -------------------------------------


def test_low_pinning_is_never_inferred_from_support_wording():
    """The old parser wrote gex_regime="LOW_PINNING" whenever the text said
    "near support" and the direction was long — inventing a dealer-gamma regime
    from a support/resistance phrase."""
    from alert_ranker.rh_options import parse_messy_rh_options_text

    extracted = parse_messy_rh_options_text(
        "SPY bullish near support price 500 505C 7/7 premium 2.20 dte 18", now=NOW
    )
    assert extracted["parsed"].get("gex_regime") is None


def test_explicit_gex_text_is_still_honoured():
    from alert_ranker.rh_options import parse_messy_rh_options_text

    extracted = parse_messy_rh_options_text(
        "SPY bullish GEX NEG_GAMMA price 500 505C 7/7 premium 2.20 dte 18", now=NOW
    )
    assert extracted["parsed"].get("gex_regime") == "NEG_GAMMA"


def test_support_resistance_text_populates_pivots_not_gamma_walls():
    from alert_ranker.rh_options import parse_messy_rh_options_text

    extracted = parse_messy_rh_options_text(
        "SPY bullish support 495 resistance 510 price 500 505C 7/7 premium 2.20 dte 18",
        now=NOW,
    )
    parsed = extracted["parsed"]
    assert parsed.get("support_pivot") == 495.0
    assert parsed.get("resistance_pivot") == 510.0
    assert "gex_support_wall" not in parsed
    assert "gex_resistance_wall" not in parsed


def test_price_target_is_not_renamed_a_gamma_wall():
    from alert_ranker.rh_options import parse_messy_rh_options_text

    extracted = parse_messy_rh_options_text(
        "SPY bullish target 1: 600 price 500 505C 7/7 premium 2.20 dte 18", now=NOW
    )
    parsed = extracted["parsed"]
    assert parsed.get("resistance_pivot") == 600.0
    assert "gex_resistance_wall" not in parsed


# --- observations are recorded, distinctly ---------------------------------


def test_a_plus_grade_is_preserved_through_parse():
    inputs = _build({"signa_grade": "A+"})
    assert inputs.signa_grade == "A+"
    assert "signa_grade_observed:A+" in _signa_observations(inputs)


def test_conflict_and_neutrality_are_recorded_as_observations():
    conflicting = _build({"signa_daily_direction": "BULLISH", "signa_weekly_direction": "BEARISH"})
    assert "signa_direction_conflict" in _signa_observations(conflicting)

    neutral = _build({"signa_daily_direction": "NEUTRAL"})
    assert "signa_direction_not_actionable" in _signa_observations(neutral)


def test_zero_signa_score_is_not_treated_as_missing():
    inputs = _build({"signa_score": 0})
    assert inputs.signa_score == 0.0
    notes = _signa_observations(inputs)
    assert "signa_score_observed:0" in notes
    assert "signa_unavailable" not in notes
