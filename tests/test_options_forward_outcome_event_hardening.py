from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from options_manager.config import OptionsManagerConfig
from options_manager.outcomes import ForwardOutcomeEvent, validate_forward_outcome_event
from options_manager.storage import append_forward_outcome_event, init_options_storage

NOW = datetime(2026, 9, 2, 14, 3, 20, tzinfo=timezone.utc)


def _config() -> OptionsManagerConfig:
    return replace(OptionsManagerConfig(), storage_enabled=True)


def _event(**overrides: object) -> ForwardOutcomeEvent:
    values = dict(
        session_id="2026-09-02",
        thesis_id="2026-09-02:XYZ:CALL:strat_212:30m",
        ticker="XYZ",
        direction="CALL",
        setup_type="strat_212",
        timeframe="30m",
        event_type="SESSION_STAGE",
        event_at=NOW - timedelta(minutes=3),
        provider="read-only-provider",
        system_commit_sha="a2d97cf5ce53",
        setup_state="NO_SETUP",
    )
    values.update(overrides)
    return ForwardOutcomeEvent(**values)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field,value",
    [
        ("session_id", 123),
        ("thesis_id", 123),
        ("ticker", 123),
        ("direction", 123),
        ("setup_type", 123),
        ("timeframe", 123),
        ("event_type", 123),
        ("provider", 123),
        ("system_commit_sha", 1234567),
        ("setup_state", 123),
    ],
)
def test_non_string_identity_fields_fail_closed_without_reaching_strip(
    tmp_path, field: str, value: object
) -> None:
    db_path = str(tmp_path / "options.sqlite")
    assert init_options_storage(db_path, _config()).status == "WRITTEN"
    event = _event(**{field: value})

    reasons = validate_forward_outcome_event(event)
    assert any(reason.startswith(f"{field} must be a string") for reason in reasons)

    result = append_forward_outcome_event(
        db_path,
        event,
        _config(),
        recorded_at=NOW,
    )
    assert result.status == "DATA_BLOCKED"


def test_nested_nonfinite_payload_is_blocked(tmp_path) -> None:
    db_path = str(tmp_path / "options.sqlite")
    assert init_options_storage(db_path, _config()).status == "WRITTEN"
    event = _event(observations={"nested": {"value": float("inf")}})

    result = append_forward_outcome_event(
        db_path,
        event,
        _config(),
        recorded_at=NOW,
    )
    assert result.status == "DATA_BLOCKED"
