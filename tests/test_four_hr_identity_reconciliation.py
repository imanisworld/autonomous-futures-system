"""Synthetic QA only: no VPS logs, market data, P&L, orders or epoch reads."""
from __future__ import annotations

from copy import deepcopy

from research.four_hr_identity_reconciliation import reconcile_identity_only
from tests.test_wide_stop_4hr_join_provenance import _build, _touch


def _natural(five):
    event = _touch(five)
    event.update(
        bar_ts="2026-06-02T09:31:00-04:00",
        decision_time="2026-06-02T09:32:00-04:00",
        stop=19900.0,
        external_broker=False,
        trade_authorized=False,
        source_state={
            "armed_available_at": "2026-06-02T09:30:00-04:00",
            "executable": False,
            "trade_authorized": False,
        },
    )
    return event


def test_unique_causal_setup_matches_identity_but_not_execution_or_pnl():
    five = _build()
    data = reconcile_identity_only([_natural(five)], [five])
    assert data["counts"] == {"MATCHED_IDENTITY_ONLY": 1}
    item = data["touch_classifications"][0]
    assert item["five_decision_at"] == "2026-06-02T09:35:00-04:00"
    assert item["five_signal_after_one_min_touch"] is True
    assert item["five_after_one_min_seconds"] == 240
    assert item["stop_price_equal"] is True
    assert item["entry_fill_parity"] == "UNPROVEN"
    assert item["outcome_parity"] == "UNPROVEN"
    assert data["unused_five_candidates"] == []
    assert data["profitability_proven"] is False
    assert data["order_authority"] is False
    assert data["demo_ready"] is False
    assert "pnl" not in str(data).lower()


def test_earlier_one_min_entry_never_becomes_five_min_execution_proof():
    five = _build()
    data = reconcile_identity_only([_natural(five)], [five])
    item = data["touch_classifications"][0]
    assert item["status"] == "MATCHED_IDENTITY_ONLY"
    assert item["five_after_one_min_seconds"] > 0


def test_different_1m_and_5m_stop_is_exposed_not_hidden():
    five = _build()
    touch = _natural(five)
    touch["stop"] = 19850.0
    item = reconcile_identity_only([touch], [five])["touch_classifications"][0]
    assert item["status"] == "MATCHED_IDENTITY_ONLY"
    assert item["stop_price_equal"] is False
    assert item["entry_fill_parity"] == "UNPROVEN"


def test_missing_sidecar_cannot_guess_identity_by_date_or_ticker():
    five = _build()
    data = reconcile_identity_only([_natural(five)], [])
    assert data["counts"] == {"UNMATCHED": 1}
    assert data["touch_classifications"][0]["reason"] == "NO_FIVE_MIN_FULL_ARM_IDENTITY"


def test_duplicate_natural_touch_is_not_two_eligible_arms():
    five = _build()
    touch = _natural(five)
    data = reconcile_identity_only([touch, deepcopy(touch)], [five])
    assert data["counts"] == {"AMBIGUOUS": 2}
    assert all(x["reason"] == "DUPLICATE_NATURAL_TOUCH_ARM"
               for x in data["touch_classifications"])
    assert len(data["unused_five_candidates"]) == 1


def test_two_five_min_candidates_for_same_arm_are_ambiguous():
    five = _build()
    other = deepcopy(five)
    other["candidate_key"] = "different-5m-candidate"
    other["candidate_decision_at"] = "2026-06-02T09:40:00-04:00"
    other["source_bar_ts"] = "2026-06-02T09:35:00-04:00"
    data = reconcile_identity_only([_natural(five)], [five, other])
    assert data["counts"] == {"AMBIGUOUS": 1}
    assert data["touch_classifications"][0]["reason"] == "MULTIPLE_FIVE_MIN_FOR_ONE_ARM"
    assert len(data["unused_five_candidates"]) == 2


def test_incomplete_5m_clock_fails_closed():
    five = _build()
    five["candidate_decision_at"] = "2026-06-02T09:31:00-04:00"
    data = reconcile_identity_only([_natural(five)], [five])
    assert data["counts"] == {"UNMATCHABLE": 1}
    assert data["touch_classifications"][0]["reason"] == "FIVE_MIN_DECISION_CLOCK_UNPROVEN"


def test_observation_state_must_precede_1m_touch_open():
    five = _build()
    touch = _natural(five)
    touch["source_state"]["armed_available_at"] = "2026-06-02T09:32:00-04:00"
    data = reconcile_identity_only([touch], [five])
    assert data["touch_classifications"][0]["reason"] == "ARM_NOT_AVAILABLE_AT_TOUCH_OPEN"
    assert data["counts"] == {"UNMATCHABLE": 1}


def test_invalid_one_min_close_cannot_claim_eligible_touch():
    five = _build()
    touch = _natural(five)
    touch["decision_time"] = "2026-06-02T09:31:00-04:00"
    data = reconcile_identity_only([touch], [five])
    assert data["touch_classifications"][0]["reason"] == "INVALID_NATURAL_ONE_MIN_CLOCK"


def test_unknown_contract_and_roll_month_mismatch_are_not_matches():
    five = _build()
    unknown = _natural(five)
    unknown["contract_check"]["status"] = "UNKNOWN"
    mismatch = _natural(five)
    mismatch["contract_check"]["arm_contract"] = "MNQU2026"
    assert reconcile_identity_only([unknown], [five])["counts"] == {"UNMATCHABLE": 1}
    assert reconcile_identity_only([mismatch], [five])["counts"] == {"MISMATCH": 1}


def test_structural_stamp_changed_never_matches_same_day():
    five = _build()
    touch = _natural(five)
    touch["arm_key"] = touch["arm_key"].replace("09:10:00", "09:05:00")
    data = reconcile_identity_only([touch], [five])
    assert data["counts"] == {"UNMATCHED": 1}


def test_blocked_or_duplicate_events_preserved_but_not_counted_as_touches():
    five = _build()
    data = reconcile_identity_only([
        {"event": "TRIGGER_BLOCKED", "arm_key": five["arm_key"]},
        {"event": "TRIGGER_DUPLICATE", "arm_key": five["arm_key"]},
    ], [five])
    assert data["counts"] == {"NON_TOUCH_EVENT": 2}
    assert len(data["unused_five_candidates"]) == 1


def test_absent_observer_authority_flags_fail_closed():
    five = _build()
    natural = _natural(five)
    natural["trade_authorized"] = True
    data = reconcile_identity_only([natural], [five])
    assert data["counts"] == {"UNMATCHABLE": 1}
    assert data["touch_classifications"][0]["reason"] == "NOT_OBSERVATION_ONLY"


def test_absent_five_min_broker_isolation_fails_closed():
    five = _build()
    five["broker_authorized"] = True
    data = reconcile_identity_only([_natural(five)], [five])
    assert data["counts"] == {"UNMATCHABLE": 1}
    assert data["touch_classifications"][0]["reason"] == "FIVE_MIN_AUTHORITY_INCONSISTENT"
