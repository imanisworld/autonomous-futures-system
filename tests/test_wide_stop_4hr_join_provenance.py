"""4HR natural-1m/5m join identity: never backfill, route, score or trade."""
from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from context import wide_stop_4hr_join_provenance as join

DAY = date(2026, 6, 2)
ET = ZoneInfo("America/New_York")
SOURCE = "2026-06-02T09:30:00-04:00"
SETUP = "2026-06-02T09:10:00-04:00"
FOUR_AM = "2026-06-02T04:00:00-04:00"
KEY = "strat_4hr_retrigger|2026-06-02T09:35:00-04:00|20000.0000|19900.0000|20200.0000"


def _candidate(**state_override):
    state = {
        "trading_date": DAY.isoformat(), "status": "TRIGGERED",
        "direction": "LONG", "trigger": 20000.0,
        "setup_bar_ts": SETUP, "four_am_bar_ts": FOUR_AM,
    }
    state.update(state_override)
    return {
        "direction": "LONG", "entry": 20000.0, "stop": 19900.0,
        "target": 20200.0, "entry_time": datetime(2026, 6, 2, 9, 35, tzinfo=ET),
        "state": state,
    }


def _build(candidate=None, *, contract_hint="CME_MINI:MNQM2026", **kwargs):
    return join.build_5m_provenance(
        strategy=join.STRATEGY,
        candidate=candidate or _candidate(),
        candidate_key=KEY, source_bar_ts=SOURCE,
        contract_hint=contract_hint, day=DAY, **kwargs,
    )


def _touch(row, contract="MNQM2026"):
    return {
        "event": "TRIGGER_TOUCH", "arm_key": row["arm_key"],
        "contract_check": {
            "status": "MATCH", "arm_contract": contract,
            "bar_contract": contract,
        },
    }


def test_complete_machine_state_yields_same_arm_key_as_1m_observer():
    row = _build()
    assert row["schema"] == join.SCHEMA
    assert row["arm_key"] == f"2026-06-02|LONG|20000.00000000|{SETUP}|{FOUR_AM}"
    assert row["joinability"] == "IDENTITY_AVAILABLE"
    assert row["source_contract"] == "MNQM2026"
    assert row["contract_status"] == "ASSERTED_UNMATCHED"
    assert row["candidate_decision_at"] == "2026-06-02T09:35:00-04:00"
    assert row["broker_authorized"] is False
    assert row["execution_reachable"] is False


def test_verified_same_setup_and_contract_matches_identity_only():
    row = _build()
    assert join.compare_with_1m_touch(_touch(row), row) == {
        "status": "MATCHED_IDENTITY_ONLY",
        "reason": "EXECUTION_PARITY_UNPROVEN",
    }


@pytest.mark.parametrize(("changed", "expected"), [
    ({"setup_bar_ts": "2026-06-02T09:05:00-04:00"}, "ARM_KEY_DIFFERENT"),
    ({"four_am_bar_ts": "2026-06-02T00:00:00-04:00"}, "ARM_KEY_DIFFERENT"),
    ({"trigger": 20000.25}, "ARM_KEY_DIFFERENT"),
    ({"direction": "SHORT"}, "ARM_KEY_DIFFERENT"),
])
def test_same_day_and_ticker_are_not_sufficient_for_match(changed, expected):
    row = _build(candidate=_candidate(**changed))
    if row["joinability"] != "IDENTITY_AVAILABLE":
        assert row["joinability"] == "UNMATCHABLE"
    else:
        original = _build()
        actual = join.compare_with_1m_touch(_touch(original), row)
        assert actual["reason"] == expected


def test_dated_contract_mismatch_never_matches_even_with_same_arm_key():
    row = _build()
    result = join.compare_with_1m_touch(_touch(row, contract="MNQU2026"), row)
    assert result["status"] == "MISMATCH"
    assert result["reason"] == "DATED_CONTRACT_DIFFERENT"


def test_unknown_one_min_bar_contract_is_not_assumed_to_match():
    row = _build()
    touch = _touch(row)
    touch["contract_check"]["status"] = "UNKNOWN"
    assert join.compare_with_1m_touch(touch, row)["status"] == "UNMATCHABLE"


@pytest.mark.parametrize("hint", [None, "", "MNQ1!", "MNQJ2026"])
def test_unknown_or_unusable_source_contract_cannot_join(hint):
    row = _build(contract_hint=hint)
    assert row["joinability"] == "UNMATCHABLE"
    assert row["reason"] == "DATED_CONTRACT_UNPROVEN"
    assert row["source_contract"] is None
    assert row["arm_key"] is not None
    assert join.compare_with_1m_touch(_touch(row), row)["status"] == "UNMATCHABLE"


@pytest.mark.parametrize(("changed", "reason"), [
    ({"setup_bar_ts": "2026-06-02T09:10:00"}, "MISSING_OR_INVALID_ARM_STAMPS"),
    ({"setup_bar_ts": "2026-06-03T09:10:00-04:00"}, "MISSING_OR_INVALID_ARM_STAMPS"),
    ({"four_am_bar_ts": None}, "MISSING_OR_INVALID_ARM_STAMPS"),
    ({"status": "ARMED"}, "STATE_NOT_TRIGGERED"),
    ({"trading_date": "2026-06-01"}, "INCONSISTENT_TRADING_DATE"),
])
def test_missing_or_malformed_setup_identity_is_explicitly_unmatchable(changed, reason):
    row = _build(candidate=_candidate(**changed))
    assert row["joinability"] == "UNMATCHABLE"
    assert row["reason"] == reason
    assert row["arm_key"] is None


def test_invalid_close_time_refuses_causal_identity():
    row = join.build_5m_provenance(
        strategy=join.STRATEGY, candidate=_candidate(),
        candidate_key=KEY, source_bar_ts="2026-06-02T09:35:00-04:00",
        contract_hint="MNQM2026", day=DAY,
    )
    assert row["reason"] == "INVALID_FIVE_MIN_DECISION_TIME"
    assert row["arm_key"] is None


def test_sidecar_disabled_means_no_writes_or_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(join.ENABLED_ENV, raising=False)
    out = join.maybe_record_five_min_provenance(
        log_dir=tmp_path, strategy=join.STRATEGY,
        candidate=_candidate(), candidate_key=KEY,
        payload=SimpleNamespace(timestamp=SOURCE, contract_hint="MNQM2026"),
        day=DAY,
    )
    assert out is None
    assert not list(tmp_path.iterdir())


def test_enabled_sidecar_is_idempotent_and_separate(tmp_path, monkeypatch):
    monkeypatch.setenv(join.ENABLED_ENV, "true")
    args = dict(
        log_dir=tmp_path, strategy=join.STRATEGY,
        candidate=_candidate(), candidate_key=KEY,
        payload=SimpleNamespace(timestamp=SOURCE, contract_hint="MNQM2026"),
        day=DAY,
    )
    row = join.maybe_record_five_min_provenance(**args)
    assert row is not None
    assert join.maybe_record_five_min_provenance(**args) is None
    files = list((tmp_path / join.DIRECTORY / DAY.isoformat()).glob("*.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text()) == row
    assert not (tmp_path / "tradovate_demo_evidence").exists()
    assert not (tmp_path / "wide_stop_ledger").exists()


def test_bad_strategy_never_generates_join_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv(join.ENABLED_ENV, "true")
    out = join.maybe_record_five_min_provenance(
        log_dir=tmp_path, strategy="strat_322_first_live",
        candidate=_candidate(), candidate_key=KEY,
        payload=SimpleNamespace(timestamp=SOURCE, contract_hint="MNQM2026"),
        day=DAY,
    )
    assert out is None
    assert not list(tmp_path.iterdir())


def test_io_failure_never_raises_into_collector(tmp_path, monkeypatch):
    monkeypatch.setenv(join.ENABLED_ENV, "true")
    blocker = tmp_path / join.DIRECTORY
    blocker.write_text("not a directory")
    assert join.maybe_record_five_min_provenance(
        log_dir=tmp_path, strategy=join.STRATEGY,
        candidate=_candidate(), candidate_key=KEY,
        payload=SimpleNamespace(timestamp=SOURCE, contract_hint="MNQM2026"),
        day=DAY,
    ) is None


def test_nonnumeric_bracket_not_promoted_to_identity():
    candidate = _candidate()
    candidate["stop"] = float("nan")
    row = _build(candidate=candidate)
    assert row["arm_key"] is None
    assert row["joinability"] == "UNMATCHABLE"
