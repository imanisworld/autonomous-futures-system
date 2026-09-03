from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from options_manager.config import OptionsManagerConfig
from options_manager.outcomes import UNDETERMINED, ForwardOutcomeEvent, reduce_forward_outcome
from options_manager.storage import (
    append_forward_outcome_event,
    init_options_storage,
    load_forward_outcome_events,
)

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


def test_empty_event_list_is_undetermined_not_no_setup() -> None:
    summary = reduce_forward_outcome([])

    assert summary.outcome == UNDETERMINED
    assert summary.outcome != "NO_SETUP"
    assert summary.reasons == ("no events",)


def test_mixed_case_thesis_id_is_preserved_byte_identical() -> None:
    thesis = "2026-09-02:xYz:Call:Strat_212:30m"
    events = [
        replace(_contract(1, True), thesis_id=thesis),
        replace(_ev("TRIGGER", 2, obs={"entry_price": 100.0}), thesis_id=thesis),
    ]

    summary = reduce_forward_outcome(events)

    assert summary.outcome == "TRIGGERED_ACTIONABLE"
    assert summary.thesis_id == thesis
    assert summary.thesis_id != thesis.upper()

    # Early-return paths must report the same literal id, never a folded one.
    broken = _ev("SETUP_STATE", 1).to_payload()
    broken["thesis_id"] = thesis
    broken["setup_state"] = "WIN"
    assert reduce_forward_outcome([broken]).thesis_id == thesis


def test_thesis_ids_differing_only_by_case_are_distinct_theses() -> None:
    upper = replace(_contract(1, True), thesis_id="THESIS-A")
    lower = replace(_ev("TRIGGER", 2, obs={"entry_price": 100.0}), thesis_id="thesis-a")

    summary = reduce_forward_outcome([upper, lower])

    assert summary.outcome == UNDETERMINED
    assert any("span thesis ids" in reason for reason in summary.reasons)


def test_reduces_stored_rows_with_canonical_utc_timestamps(tmp_path) -> None:
    """Rows come back from storage UTC-normalized and in canonical event_at order;
    the reducer must fold that order as-is and report the stored id and UTC text."""
    config = replace(OptionsManagerConfig())
    db_path = str(tmp_path / "options.sqlite")
    assert init_options_storage(db_path, config).status == "WRITTEN"

    thesis = "2026-09-02:xYz:Call:Strat_212:30m"
    eastern = timezone(timedelta(hours=-4))
    recorded_at = T0 + timedelta(hours=1)
    # Submitted out of order and from a non-UTC producer offset.
    submitted = [
        replace(_ev("TARGET_1", 9), thesis_id=thesis, event_at=(T0 + timedelta(minutes=9)).astimezone(eastern)),
        replace(_ev("TRIGGER", 5, obs={"entry_price": 100.0}), thesis_id=thesis, event_at=(T0 + timedelta(minutes=5)).astimezone(eastern)),
        replace(_contract(2, True), thesis_id=thesis, event_at=(T0 + timedelta(minutes=2)).astimezone(eastern)),
    ]
    for event in submitted:
        assert append_forward_outcome_event(db_path, event, config, recorded_at=recorded_at).status == "WRITTEN"

    read = load_forward_outcome_events(db_path, config, thesis_id=thesis)
    assert read.status == "FOUND" and read.record["count"] == 3
    rows = read.record["events"]
    assert [row["event_type"] for row in rows] == ["CONTRACT_OBSERVATION", "TRIGGER", "TARGET_1"]

    summary = reduce_forward_outcome(rows)

    assert summary.outcome == "T1_HIT" and summary.minutes_to_t1 == 4.0
    assert summary.thesis_id == thesis
    assert {row["thesis_id"] for row in rows} == {summary.thesis_id}
    # Canonical UTC text from storage, not the producer's -04:00 offset.
    assert summary.trigger_at == rows[1]["event_at"] == (T0 + timedelta(minutes=5)).isoformat()
    assert summary.trigger_at.endswith("+00:00")
    assert summary.first_event_at == rows[0]["event_at"] and summary.last_event_at == rows[-1]["event_at"]
