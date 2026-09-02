"""Outcome reducer: seven causal outcomes, theoretical moves never count."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from options_manager.outcomes import UNDETERMINED, ForwardOutcomeEvent, reduce_forward_outcome

T0 = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
THESIS = "2026-09-02:XYZ:CALL:strat_212:30m"


def _ev(kind, minutes, *, state="NO_SETUP", direction="CALL", obs=None, contract=None, market=None, thesis=THESIS):
    return ForwardOutcomeEvent(
        session_id="2026-09-02", thesis_id=thesis, ticker="XYZ", direction=direction, setup_type="strat_212", timeframe="30m",
        event_type=kind, event_at=T0 + timedelta(minutes=minutes), provider="robinhood-readonly", system_commit_sha="a2d97cf5ce53",
        setup_state=state, observations=obs or {}, contract_facts=contract or {}, market_context=market or {},
    )


def _contract(minutes, valid=True, *, direction="CALL"):
    return _ev("CONTRACT_OBSERVATION", minutes, direction=direction, contract={"strike": 100.0, "bid": 1.95, "ask": 2.05}, obs={"contract_valid": valid})


def test_no_events_is_no_setup():
    assert reduce_forward_outcome([]).outcome == "NO_SETUP"


def test_no_setup_and_setup_not_triggered():
    assert reduce_forward_outcome([_ev("SESSION_STAGE", 0), _ev("SETUP_STATE", 1, state="NO_SETUP")]).outcome == "NO_SETUP"
    summary = reduce_forward_outcome([_ev("SETUP_STATE", 1, state="SETUP_NOT_TRIGGERED")])
    assert summary.outcome == "SETUP_NOT_TRIGGERED" and summary.setup_seen


def test_trigger_without_contract_proof_is_blocked():
    summary = reduce_forward_outcome([_ev("SETUP_STATE", 1, state="SETUP_NOT_TRIGGERED"), _ev("TRIGGER", 5, obs={"entry_price": 123.9})])
    assert summary.outcome == "TRIGGERED_CONTRACT_BLOCKED"
    assert summary.contract_valid_at_trigger is None
    assert "no valid contract observation" in summary.reasons[0]
    blocked = reduce_forward_outcome([_contract(2, valid=False), _ev("TRIGGER", 5, obs={"entry_price": 123.9})])
    assert blocked.outcome == "TRIGGERED_CONTRACT_BLOCKED" and blocked.contract_valid_at_trigger is False


def test_trigger_with_valid_contract_is_actionable_and_captures_context():
    events = [
        _ev("MARKET_CONTEXT", 1, market={"spy": 761.0, "qqq": 704.0}),
        _contract(2),
        _ev("MARKET_CONTEXT", 3, market={"spy": 762.0, "qqq": 705.0}),
        _ev("TRIGGER", 5, obs={"entry_price": 123.9}),
        _ev("MARKET_CONTEXT", 6, market={"spy": 700.0}),  # after trigger: not the trigger context
    ]
    summary = reduce_forward_outcome(events)
    assert summary.outcome == "TRIGGERED_ACTIONABLE"
    assert summary.trigger_at == (T0 + timedelta(minutes=5)).isoformat()
    assert summary.market_context_at_trigger == {"spy": 762.0, "qqq": 705.0}
    assert summary.contract_at_trigger["strike"] == 100.0 and summary.contract_at_trigger["contract_valid"] is True
    assert summary.entry_price == 123.9 and summary.direction == "CALL"


def test_t1_and_t2_with_timing_and_call_mfe_mae():
    events = [
        _contract(2),
        _ev("TRIGGER", 5, obs={"entry_price": 100.0}),
        _ev("PRICE_PATH", 6, obs={"high": 101.0, "low": 99.5}),
        _ev("PRICE_PATH", 8, obs={"high": 103.0, "low": 100.2}),
        _ev("TARGET_1", 8),
        _ev("PRICE_PATH", 12, obs={"high": 105.5, "low": 102.0}),
        _ev("TARGET_2", 12),
    ]
    summary = reduce_forward_outcome(events)
    assert summary.outcome == "T2_HIT"
    assert summary.minutes_to_t1 == 3.0 and summary.minutes_to_t2 == 7.0
    assert summary.mfe == 5.5 and summary.mae == 0.5 and summary.price_path_points == 3


def test_put_mfe_mae_mirror():
    events = [
        _contract(2, direction="PUT"),
        _ev("TRIGGER", 5, direction="PUT", obs={"entry_price": 100.0}),
        _ev("PRICE_PATH", 6, direction="PUT", obs={"high": 100.8, "low": 97.0}),
        _ev("TARGET_1", 6, direction="PUT"),
    ]
    summary = reduce_forward_outcome(events)
    assert summary.outcome == "T1_HIT" and summary.mfe == 3.0 and summary.mae == 0.8


def test_invalidation_after_trigger_and_events_after_it_are_ignored():
    events = [
        _contract(2),
        _ev("TRIGGER", 5, obs={"entry_price": 100.0}),
        _ev("INVALIDATION", 9),
        _ev("TARGET_1", 15),  # would-have-been: ignored
        _ev("PRICE_PATH", 16, obs={"high": 120.0, "low": 90.0}),
    ]
    summary = reduce_forward_outcome(events)
    assert summary.outcome == "INVALIDATED"
    assert summary.minutes_to_invalidation == 4.0 and summary.t1_at is None
    assert summary.post_invalidation_events_ignored == 2 and summary.mfe is None


def test_invalidation_before_trigger_is_invalidated():
    summary = reduce_forward_outcome([_ev("SETUP_STATE", 1, state="SETUP_NOT_TRIGGERED"), _ev("INVALIDATION", 3), _ev("TRIGGER", 4)])
    assert summary.outcome == "INVALIDATED" and summary.trigger_at is None


def test_theoretical_target_touch_without_trigger_never_counts():
    summary = reduce_forward_outcome([_ev("SETUP_STATE", 1, state="SETUP_NOT_TRIGGERED"), _ev("TARGET_1", 4), _ev("TARGET_2", 6)])
    assert summary.outcome == "SETUP_NOT_TRIGGERED"
    assert len(summary.untriggered_target_touches) == 2
    assert any("theoretical" in r for r in summary.reasons)


def test_events_are_folded_in_source_time_order_not_insertion_order():
    events = [_ev("TARGET_1", 8), _ev("TRIGGER", 5, obs={"entry_price": 100.0}), _contract(2)]
    assert reduce_forward_outcome(events).outcome == "T1_HIT"


def test_price_path_before_trigger_and_missing_entry_price_do_not_fabricate_mfe():
    events = [_ev("PRICE_PATH", 1, obs={"high": 200.0, "low": 1.0}), _contract(2), _ev("TRIGGER", 5), _ev("PRICE_PATH", 6, obs={"high": 101.0, "low": 99.0})]
    summary = reduce_forward_outcome(events)
    assert summary.mfe is None and summary.mae is None and summary.price_path_points == 1
    assert any("entry_price" in r for r in summary.reasons)


def test_non_finite_price_points_are_skipped():
    events = [_contract(2), _ev("TRIGGER", 5, obs={"entry_price": 100.0}), _ev("PRICE_PATH", 6, obs={"high": "nan", "low": 99.0}), _ev("PRICE_PATH", 7, obs={"high": 102.0, "low": 99.0})]
    summary = reduce_forward_outcome(events)
    assert summary.price_path_points == 1 and summary.mfe == 2.0


def test_mixed_or_malformed_input_is_undetermined():
    assert reduce_forward_outcome([_ev("TRIGGER", 5), _ev("TRIGGER", 6, thesis="other")]).outcome == UNDETERMINED
    assert reduce_forward_outcome([{"thesis_id": THESIS, "event_type": "TRIGGER", "event_at": "2026-09-02T14:05:00"}]).outcome == UNDETERMINED
    assert reduce_forward_outcome([{"thesis_id": THESIS, "event_type": "WIN", "event_at": T0.isoformat()}]).outcome == UNDETERMINED
    assert reduce_forward_outcome(["not an event"]).outcome == UNDETERMINED


def test_accepts_stored_payload_dicts():
    payloads = [_contract(2).to_payload(), _ev("TRIGGER", 5, obs={"entry_price": 100.0}).to_payload(), _ev("TARGET_1", 9).to_payload()]
    summary = reduce_forward_outcome(payloads)
    assert summary.outcome == "T1_HIT" and summary.event_count == 3 and summary.minutes_to_t1 == 4.0


def test_reducer_is_pure():
    import options_manager.outcomes.reducer as mod
    from pathlib import Path

    source = Path(mod.__file__).read_text()
    for forbidden in ("datetime.now", "utcnow", "sqlite3", "subprocess", "open(", "requests", "httpx"):
        assert forbidden not in source, forbidden
