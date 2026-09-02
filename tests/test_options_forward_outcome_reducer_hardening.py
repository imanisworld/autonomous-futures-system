from __future__ import annotations

from datetime import datetime, timedelta, timezone

from options_manager.outcomes import UNDETERMINED, ForwardOutcomeEvent, reduce_forward_outcome

T0 = datetime(2026, 9, 2, 14, 0, tzinfo=timezone.utc)
THESIS = "2026-09-02:XYZ:CALL:strat_212:30m"


def _ev(
    kind: str,
    minutes: int,
    *,
    state: str = "NO_SETUP",
    direction: str = "CALL",
    obs=None,
    contract=None,
    ticker: str = "XYZ",
) -> ForwardOutcomeEvent:
    return ForwardOutcomeEvent(
        session_id="2026-09-02",
        thesis_id=THESIS,
        ticker=ticker,
        direction=direction,
        setup_type="strat_212",
        timeframe="30m",
        event_type=kind,
        event_at=T0 + timedelta(minutes=minutes),
        provider="read-only-provider",
        system_commit_sha="a2d97cf5ce53",
        setup_state=state,
        observations=obs or {},
        contract_facts=contract or {},
    )


def _contract(minutes: int, valid) -> ForwardOutcomeEvent:
    return _ev(
        "CONTRACT_OBSERVATION",
        minutes,
        contract={"strike": 100.0, "bid": 1.95, "ask": 2.05},
        obs={"contract_valid": valid},
    )


def test_contract_blocked_trigger_cannot_be_upgraded_by_later_targets() -> None:
    summary = reduce_forward_outcome(
        [
            _contract(1, False),
            _ev("TRIGGER", 2, obs={"entry_price": 100.0}),
            _ev("TARGET_1", 4),
            _ev("TARGET_2", 6),
        ]
    )

    assert summary.outcome == "TRIGGERED_CONTRACT_BLOCKED"
    assert summary.contract_valid_at_trigger is False
    assert summary.t1_at == (T0 + timedelta(minutes=4)).isoformat()
    assert summary.t2_at == (T0 + timedelta(minutes=6)).isoformat()
    assert any("contract-blocked trigger" in reason for reason in summary.reasons)


def test_string_false_contract_flag_never_becomes_truthy() -> None:
    summary = reduce_forward_outcome(
        [
            _contract(1, "false"),
            _ev("TRIGGER", 2, obs={"entry_price": 100.0}),
        ]
    )

    assert summary.outcome == "TRIGGERED_CONTRACT_BLOCKED"
    assert summary.contract_valid_at_trigger is None
    assert any("not boolean" in reason for reason in summary.reasons)


def test_conflicting_direction_same_thesis_is_undetermined() -> None:
    summary = reduce_forward_outcome(
        [
            _contract(1, True),
            _ev("TRIGGER", 2, direction="PUT", obs={"entry_price": 100.0}),
        ]
    )

    assert summary.outcome == UNDETERMINED
    assert any("disagree on direction" in reason for reason in summary.reasons)


def test_conflicting_ticker_same_thesis_is_undetermined() -> None:
    summary = reduce_forward_outcome(
        [
            _contract(1, True),
            _ev("TRIGGER", 2, ticker="ABC", obs={"entry_price": 100.0}),
        ]
    )

    assert summary.outcome == UNDETERMINED
    assert any("identity" in reason for reason in summary.reasons)


def test_unknown_setup_state_is_undetermined_for_mapping_input() -> None:
    payload = _ev("SETUP_STATE", 1).to_payload()
    payload["setup_state"] = "WIN"

    summary = reduce_forward_outcome([payload])
    assert summary.outcome == UNDETERMINED
    assert any("unknown setup_state" in reason for reason in summary.reasons)


def test_mfe_mae_are_nonnegative_excursions() -> None:
    summary = reduce_forward_outcome(
        [
            _contract(1, True),
            _ev("TRIGGER", 2, obs={"entry_price": 100.0}),
            _ev("PRICE_PATH", 3, obs={"high": 102.0, "low": 101.0}),
        ]
    )

    assert summary.mfe == 2.0
    assert summary.mae == 0.0


def test_inverted_price_path_point_is_ignored() -> None:
    summary = reduce_forward_outcome(
        [
            _contract(1, True),
            _ev("TRIGGER", 2, obs={"entry_price": 100.0}),
            _ev("PRICE_PATH", 3, obs={"high": 99.0, "low": 101.0}),
        ]
    )

    assert summary.price_path_points == 0
    assert summary.mfe is None and summary.mae is None
    assert any("high below low" in reason for reason in summary.reasons)
