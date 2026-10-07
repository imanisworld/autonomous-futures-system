"""Regression tests for the #1151 review blockers B1-B6 (canonical signal / outcome / adapter).

B1 signals never acquire execution authority (events, constructor, replace, records, adapter rows).
B2 a MISSED_LATE / non-prospective resolution can never become an executed trade.
B3 malformed / non-finite / wrongly-typed numbers and booleans fail closed.
B4 provenance (capture_late, prospective_catch, signal_integrity, resolution) is never overwritten.
B5 impossible chronology is refused.
B6 strategy_epoch labels are validated against the #1150 registry, including context.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from options_evidence import capture_adapter as ca
from options_evidence import outcome as oc
from options_evidence import signal as sg
from options_evidence import strategy_epochs as se
from tests.test_options_outcome_evidence import outcome
from tests.test_options_prospective_signal import (
    CATCH_EPOCH,
    CATCH_STRATEGY,
    HIGH,
    LOW,
    REGISTRY,
    T0,
    caught,
    identity,
    opened,
    registered_epoch,
    trigger,
)

AT = T0 + timedelta(minutes=10)
M = oc.Measured


def _reg_journal() -> sg.SignalJournal:
    return sg.SignalJournal(REGISTRY)


def _reg_opened(journal: sg.SignalJournal, **kw) -> sg.ProspectiveSignal:
    return opened(journal, strategy=CATCH_STRATEGY, strategy_epoch=CATCH_EPOCH, **kw)


def _missed_late(journal: sg.SignalJournal | None = None) -> sg.ProspectiveSignal:
    journal = journal if journal is not None else _reg_journal()
    s = _reg_opened(journal, first_seen_time=T0 + timedelta(minutes=20))
    return trigger(journal, s, at=AT, detected=T0 + timedelta(minutes=21), state=sg.LifecycleState.MISSED_LATE)


def _signal_kwargs(**overrides):
    s = caught()
    data = {f: getattr(s, f) for f in s.__dataclass_fields__}
    data.update(overrides)
    return data


# ── B1: no authority, ever ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "field_name, value",
    [("execution_authority", True), ("execution_authority", "false"), ("execution_authority", 0),
     ("execution_authority", None), ("observation_only", False), ("observation_only", "true"),
     ("observation_only", 1)],
)
def test_b1_direct_construction_cannot_carry_authority(field_name, value):
    with pytest.raises(sg.LifecycleError):
        sg.ProspectiveSignal(**_signal_kwargs(**{field_name: value}))


def test_b1_replace_cannot_grant_authority():
    s = caught()
    for kw in ({"execution_authority": True}, {"observation_only": False}, {"execution_integrity": sg.IntegrityStatus.VALID}):
        with pytest.raises(sg.LifecycleError):
            replace(s, **kw)


def test_b1_fabricated_epoch_cannot_unlock_authority_or_validity():
    journal = sg.SignalJournal()
    s = opened(journal, strategy="options_122", strategy_epoch="FABRICATED-E9")
    assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and s.requested_epoch == "FABRICATED-E9"
    with pytest.raises(sg.LifecycleError, match="only carry integrity statuses"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=1),
                                          execution_authority=True, authority_ref="op"))


def test_b1_forged_opened_event_cannot_claim_an_unregistered_epoch():
    journal = sg.SignalJournal()  # committed registry: no 222-1H-TEST epoch
    event = sg.open_signal(identity(), strategy=CATCH_STRATEGY, strategy_epoch=CATCH_EPOCH, setup_ready_time=T0,
                           first_seen_time=T0 + timedelta(seconds=5), data_source="x", levels=sg.Levels(HIGH, LOW),
                           registry=REGISTRY)
    with pytest.raises(sg.LifecycleError, match="not this journal's registry entry"):
        journal.append(event)


def test_b1_records_and_adapter_rows_use_exact_booleans():
    record = sg.to_record(caught())
    for bad in ({"execution_authority": "false"}, {"execution_authority": 0}, {"observation_only": "true"}):
        assert "observation-only record claims execution authority" in sg.verify_record({**record, **bad})
    row = {"record_type": "WATCHING", "structure_key": "SPY|1H|2026-10-02T20:00:00Z|222:2U:2U"}
    for bad in ({"execution_authority": "false"}, {"trade_authority": 1}, {"risk_reservation": "no"}):
        with pytest.raises(ca.AdapterError, match="claims authority"):
            ca.fold_capture_rows([{**row, **bad}])
    with pytest.raises(ca.AdapterError, match="not observation-only"):
        ca.fold_capture_rows([{**row, "observation_only": "true"}])


# ── B2: a miss is never a trade ─────────────────────────────────────────────


def test_b2_missed_late_outcome_cannot_be_executed_or_realised():
    s = _missed_late()
    assert s.resolution is sg.LifecycleState.MISSED_LATE and s.signal_integrity is sg.IntegrityStatus.DEGRADED
    executed = oc.validate_outcome(outcome(s, executed=True, pnl_basis="executed"), s)
    assert any("requires a prospective catch" in p for p in executed)
    assert "a MISSED_LATE outcome must use pnl_basis=counterfactual" in oc.validate_outcome(outcome(s), s)
    realised = outcome(s, pnl_basis="counterfactual", gross_pnl=M.observed(120.0, "fills"),
                       result_r=M.observed(1.0, "fills"))
    problems = oc.validate_outcome(realised, s)
    assert "a missed signal has no P&L" in problems
    assert "a missed signal has no observed (realised) R" in problems
    honest = outcome(s, pnl_basis="counterfactual")
    assert oc.validate_outcome(honest, s) == []
    assert oc.result_r_value(oc.to_record(honest, s)) is None  # never read as a trade result


def test_b2_closed_miss_is_still_a_miss():
    journal = _reg_journal()
    s = _missed_late(journal)
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.OUTCOME_CLOSED,
                                      detected_at=T0 + timedelta(hours=3), reason="closed",
                                      payload={"outcome_ref": "oc_1"}))
    assert s.state is sg.LifecycleState.OUTCOME_CLOSED and s.resolution is sg.LifecycleState.MISSED_LATE
    assert any("requires a prospective catch" in p for p in
               oc.validate_outcome(outcome(s, executed=True, pnl_basis="executed"), s))
    assert sg.to_record(s)["resolution_state"] == "MISSED_LATE"


def test_b2_integrity_cannot_be_promoted_to_manufacture_a_catch():
    journal = _reg_journal()
    s = _missed_late(journal)
    with pytest.raises(sg.LifecycleError):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=30),
                                          signal_integrity="VALID"))
    with pytest.raises(sg.LifecycleError, match="pre-armed TRIGGERED"):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=30),
                                            prospective_catch=True))
    with pytest.raises(sg.LifecycleError):
        replace(s, signal_integrity=sg.IntegrityStatus.VALID)
    laundered = outcome(s, pnl_basis="counterfactual", signal_integrity=sg.IntegrityStatus.VALID)
    assert "outcome cannot report VALID signal integrity for a signal that is not VALID" in oc.validate_outcome(
        laundered, s)


def test_b2_unverified_trigger_cannot_be_executed_but_a_real_catch_can():
    journal = _reg_journal()
    pending = trigger(journal, _reg_opened(journal), at=AT)  # no catch evidence yet
    assert not pending.is_prospective_catch
    assert any("requires a prospective catch" in p for p in
               oc.validate_outcome(outcome(pending, executed=True, pnl_basis="executed"), pending))
    s = caught()
    assert s.is_prospective_catch
    assert oc.validate_outcome(outcome(s, executed=True, pnl_basis="executed"), s) == []


# ── B3: exact, finite types ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "build",
    [
        lambda: {"invalidation": float("nan")}, lambda: {"invalidation": float("inf")},
        lambda: {"invalidation": True}, lambda: {"invalidation": "498.10"}, lambda: {"invalidation": 10**400},
        lambda: {"mae_price": M.observed(float("nan"), "sip")}, lambda: {"mae_price": M.observed(True, "sip")},
        lambda: {"mfe_price": M.observed("504.4", "sip")},
        lambda: {"premium_entry": M.observed(float("inf"), "chain")},
        lambda: {"target_1": M.observed(-1.0, "plan")}, lambda: {"result_r": M.derived(True)},
        lambda: {"result_r": M.derived("1.0")}, lambda: {"gross_pnl": M.observed(float("-inf"), "fills")},
        lambda: {"net_pnl": M.observed("x", "fills")},
        lambda: {"t1_hit_at": M.observed("2026-10-06T15:00:00Z", "sip")},
        lambda: {"t1_hit_at": M.observed(datetime(2026, 10, 6, 15), "sip")},
        lambda: {"executed": "yes"}, lambda: {"executed": 1}, lambda: {"executed": None},
        lambda: {"pnl_basis": "realised"}, lambda: {"trim_event": M.observed(3, "x")},
        lambda: {"signal_integrity": "VALID"}, lambda: {"premium_path": [1]},
    ],
)
def test_b3_outcome_fields_fail_closed(build):
    s = caught()
    with pytest.raises(oc.OutcomeError):
        outcome(s, **build())


def test_b3_measured_and_premium_marks_are_exact():
    with pytest.raises(oc.OutcomeError):
        M.derived(float("inf"))
    with pytest.raises(oc.OutcomeError):
        M(1.0, "DERIVED")  # type: ignore[arg-type]
    for bid, ask in (("3.1", 3.2), (True, 3.2), (float("nan"), 3.2), (3.1, float("inf"))):
        with pytest.raises(oc.OutcomeError):
            oc.PremiumMark(AT, bid, ask, "public")  # type: ignore[arg-type]
    with pytest.raises(oc.OutcomeError):
        oc.PremiumMark("2026-10-06T15:00:00Z", 3.1, 3.2, "public")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "high, low, revision",
    [("501.25", LOW, 0), (True, LOW, 0), (float("nan"), LOW, 0), (float("inf"), LOW, 0), (HIGH, LOW, True),
     (HIGH, LOW, -1), (HIGH, LOW, 1.0)],
)
def test_b3_levels_are_exact(high, low, revision):
    with pytest.raises(sg.LifecycleError):
        sg.Levels(high, low, revision)


@pytest.mark.parametrize(
    "evidence",
    [{"capture_late": "yes"}, {"capture_late": 0}, {"prospective_catch": "true"}, {"data_delayed": None},
     {"true_lag_seconds": float("nan")}, {"true_lag_seconds": True}, {"iex_lag_seconds": "5"},
     {"trigger_trade_price": float("inf")}, {"sip_crossed_at": "2026-10-06T14:10:00"},
     {"sip_crossed_at": 1759759800}, {"status_reason": 7}],
)
def test_b3_capture_evidence_is_exact(evidence):
    journal = _reg_journal()
    s = trigger(journal, _reg_opened(journal), at=AT)
    with pytest.raises(sg.LifecycleError):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=1), **evidence))


@pytest.mark.parametrize("value", [True, "valid", 1, None, "PASS"])
def test_b3_integrity_values_are_exact(value):
    journal = _reg_journal()
    s = trigger(journal, _reg_opened(journal), at=AT)
    with pytest.raises(sg.LifecycleError):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=1),
                                          data_integrity=value))


# ── B4: provenance is never overwritten ─────────────────────────────────────


def _triggered(journal):
    return trigger(journal, _reg_opened(journal), at=AT)


def test_b4_capture_late_and_gap_are_sticky():
    for key in ("capture_late", "gap_through"):
        journal = _reg_journal()
        s = _triggered(journal)
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=1), **{key: True}))
        with pytest.raises(sg.LifecycleError, match="provenance cannot be cleared"):
            journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=2),
                                                **{key: False}))


def test_b4_revoked_catch_never_returns_and_demotes_valid():
    journal = _reg_journal()
    s = caught(journal)
    assert s.signal_integrity is sg.IntegrityStatus.VALID
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=5),
                                            prospective_catch=False))
    assert s.catch_revoked and s.signal_integrity is sg.IntegrityStatus.DEGRADED and not s.is_prospective_catch
    with pytest.raises(sg.LifecycleError, match="revoked prospective_catch"):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=6),
                                            prospective_catch=True))


def test_b4_late_evidence_demotes_a_valid_catch():
    journal = _reg_journal()
    s = caught(journal)
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=5),
                                            capture_late=True))
    assert s.signal_integrity is sg.IntegrityStatus.DEGRADED and not s.is_prospective_catch


def test_b4_cross_times_are_write_once():
    journal = _reg_journal()
    s = _triggered(journal)
    journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=1),
                                        sip_crossed_at="2026-10-06T14:10:00Z"))
    journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=2),
                                        sip_crossed_at="2026-10-06T14:10:00+00:00"))  # same instant: fine
    with pytest.raises(sg.LifecycleError, match="write-once"):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=3),
                                            sip_crossed_at="2026-10-06T14:09:00Z"))


@pytest.mark.parametrize(
    "first, second",
    [("VALID", "UNKNOWN"), ("DEGRADED", "VALID"), ("INVALID", "VALID"), ("INVALID", "DEGRADED"),
     ("DEGRADED", "UNKNOWN")],
)
def test_b4_signal_integrity_can_only_be_demoted(first, second):
    journal = _reg_journal()
    s = caught(journal) if first == "VALID" else _triggered(journal)
    if first != "VALID":
        s = journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=1),
                                              signal_integrity=first))
    with pytest.raises(sg.LifecycleError, match="only demotion"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=2),
                                          signal_integrity=second))


def test_b4_invalid_data_integrity_is_final_and_blocked_states_force_invalid():
    journal = _reg_journal()
    s = _triggered(journal)
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.DATA_BLOCKED,
                                      detected_at=AT + timedelta(hours=1), reason="sip_unavailable"))
    assert s.data_integrity is s.signal_integrity is sg.IntegrityStatus.INVALID
    with pytest.raises(sg.LifecycleError):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(hours=2),
                                          data_integrity="VALID"))
    with pytest.raises(sg.LifecycleError, match="terminal"):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(hours=2),
                                            capture_late=False))


def test_b4_missed_late_classification_cannot_be_rewritten():
    journal = _reg_journal()
    s = _missed_late(journal)
    with pytest.raises(sg.LifecycleError, match="illegal transition"):
        trigger(journal, s, at=AT, detected=T0 + timedelta(minutes=22))
    assert journal.get(s.signal_id).resolution is sg.LifecycleState.MISSED_LATE


# ── B5: chronology ──────────────────────────────────────────────────────────


def test_b5_open_chronology():
    journal = sg.SignalJournal()
    with pytest.raises(sg.LifecycleError, match="first_seen_time cannot precede setup_ready_time"):
        opened(journal, setup_ready_time=T0 + timedelta(minutes=5), first_seen_time=T0 + timedelta(minutes=1))
    with pytest.raises(sg.LifecycleError, match="setup_ready_time cannot precede"):
        opened(journal, setup_ready_time=T0 - timedelta(minutes=1))


def test_b5_setup_knowable_after_trigger_is_never_prearmed():
    journal = _reg_journal()
    s = _reg_opened(journal, setup_ready_time=T0 + timedelta(minutes=15), first_seen_time=T0 + timedelta(minutes=15))
    with pytest.raises(sg.LifecycleError, match="record MISSED_LATE"):
        trigger(journal, s, at=AT, detected=T0 + timedelta(minutes=16))
    late = trigger(journal, s, at=AT, detected=T0 + timedelta(minutes=16), state=sg.LifecycleState.MISSED_LATE)
    assert late.prearmed is False


def test_b5_detection_and_recording_order():
    journal = _reg_journal()
    s = _reg_opened(journal)
    with pytest.raises(sg.LifecycleError, match="cannot follow the event"):
        trigger(journal, s, at=AT, detected=AT + timedelta(seconds=3), trigger_detected_at=AT + timedelta(minutes=5))
    with pytest.raises(sg.LifecycleError, match="recorded before it happened"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED, market_time=AT,
                                      detected_at=AT - timedelta(minutes=1), reason="window"))
    with pytest.raises(sg.LifecycleError, match="precede structure close"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED,
                                      market_time=T0 - timedelta(minutes=1), detected_at=AT, reason="window"))


def test_b5_tampered_record_chronology_is_detected():
    record = sg.to_record(caught())
    later = (T0 + timedelta(hours=2)).isoformat()
    assert "first_seen_time precedes setup_ready_time" in sg.verify_record({**record, "setup_ready_time": later})
    assert "TRIGGERED resolution for a structure not knowable/seen before the trigger" in sg.verify_record(
        {**record, "first_seen_time": later, "setup_ready_time": later})
    assert "trigger detection precedes the market trigger" in sg.verify_record(
        {**record, "trigger_detection_time": T0.isoformat()})


# ── B6: epochs come from the registry, with context ─────────────────────────


def test_b6_grok_example_sep1_1h_structure_is_not_122_iex_e1():
    journal = sg.SignalJournal()  # committed registry
    sep1 = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    s = opened(journal, identity=identity(structure_close_time=sep1), setup_ready_time=sep1,
               first_seen_time=sep1 + timedelta(seconds=30), strategy="options_122", strategy_epoch="122-IEX-E1")
    assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and not s.epoch_registered
    assert "timeframe" in s.epoch_reason


def test_b6_context_window_and_family_are_all_required():
    journal = sg.SignalJournal()
    sep1 = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    early = opened(journal, identity=identity(timeframe="30m", pattern="122:1:2U", structure_close_time=sep1),
                   setup_ready_time=sep1, first_seen_time=sep1, strategy="options_122", strategy_epoch="122-IEX-E1")
    assert early.strategy_epoch == sg.UNREGISTERED_EPOCH and "effective window" in early.epoch_reason
    family = opened(journal, identity=identity(timeframe="30m", pattern="222:2U:2U"),
                    strategy="options_122", strategy_epoch="122-IEX-E1")
    assert family.strategy_epoch == sg.UNREGISTERED_EPOCH and "family" in family.epoch_reason
    good = opened(journal, identity=identity(timeframe="30m", pattern="122:1:2U"),
                  strategy="options_122", strategy_epoch="122-IEX-E1")
    assert good.strategy_epoch == "122-IEX-E1" and good.epoch_registered


def test_b6_unknown_draft_retired_and_malformed_epochs():
    sep1 = datetime(2026, 9, 1, tzinfo=timezone.utc)
    oct1 = datetime(2026, 10, 1, tzinfo=timezone.utc)
    reg = se.EpochRegistry((
        registered_epoch(epoch="draft", status=se.EpochStatus.DRAFT),
        registered_epoch(epoch="retired", effective_from=sep1, effective_until=oct1, status=se.EpochStatus.RETIRED),
        registered_epoch(epoch="nofamily", family="three-two-two"),
    ))
    for label, reason in (("unknown", "not in the epoch registry"), ("draft", "DRAFT"),
                          ("retired", "effective window"), ("nofamily", "STRAT_a_b_c")):
        s = opened(sg.SignalJournal(reg), strategy=CATCH_STRATEGY, strategy_epoch=label)
        assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and reason in s.epoch_reason, (label, s.epoch_reason)
    for bad in (None, 7, ["122-IEX-E1"], " "):
        with pytest.raises(sg.LifecycleError):
            opened(sg.SignalJournal(), strategy_epoch=bad)


def test_b6_unregistered_signal_is_never_valid():
    journal = sg.SignalJournal()
    s = trigger(journal, opened(journal), at=AT)  # 1H/222 under 122-IEX-E1 -> UNREGISTERED
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(seconds=4),
                                            prospective_catch=True))
    with pytest.raises(sg.LifecycleError, match="epoch is not registered"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(seconds=5),
                                          signal_integrity="VALID"))


def test_b6_direct_construction_cannot_trust_a_label():
    with pytest.raises(sg.LifecycleError, match="not validated against the epoch registry"):
        sg.ProspectiveSignal(**_signal_kwargs(registered_epoch=None))
    other = registered_epoch(timeframe="30m")
    with pytest.raises(sg.LifecycleError, match="registered epoch mismatch"):
        sg.ProspectiveSignal(**_signal_kwargs(registered_epoch=other))
    with pytest.raises(sg.LifecycleError, match="does not match strategy"):
        sg.ProspectiveSignal(**_signal_kwargs(strategy_epoch="other"))


def test_b6_record_epoch_hash_is_checked_against_the_registry():
    record = sg.to_record(caught())
    assert sg.verify_record(record, registry=REGISTRY) == []
    assert "record epoch is not the registered epoch definition" in sg.verify_record(
        {**record, "epoch_definition_sha256": "0" * 64}, registry=REGISTRY)
    assert "record epoch is not the registered epoch definition" in sg.verify_record(record, registry=se.load_registry())
    forged = {**record, "strategy_epoch": sg.UNREGISTERED_EPOCH,
              "signal_id": sg.make_signal_id(record["structure_id"], record["strategy"], sg.UNREGISTERED_EPOCH)}
    assert "VALID signal integrity under an unregistered epoch" in sg.verify_record(forged)


# ── behaviour Grok already confirmed stays intact ───────────────────────────


def test_confirmed_first_seen_after_trigger_stays_missed_late():
    journal = _reg_journal()
    s = _reg_opened(journal, first_seen_time=T0 + timedelta(minutes=20))
    with pytest.raises(sg.LifecycleError, match="MISSED_LATE"):
        trigger(journal, s, at=AT, detected=T0 + timedelta(minutes=21))
    assert _missed_late().state is sg.LifecycleState.MISSED_LATE


def test_confirmed_adapter_refuses_resolution_without_prior_watching():
    resolution = {
        "record_type": "RESOLUTION", "structure_key": "SPY|1H|2026-10-02T20:00:00Z|222:2U:2U", "status": "TRIGGERED",
        "direction": "LONG", "trigger_crossed_at": "2026-10-05T13:30:20+00:00",
        "observed_at": "2026-10-05T13:31:00+00:00",
    }
    with pytest.raises(ca.AdapterError, match="before any WATCHING row"):
        ca.fold_capture_rows([resolution])
