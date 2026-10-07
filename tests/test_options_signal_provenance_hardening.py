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
                           first_seen_time=T0 + timedelta(seconds=5),
                           data_source="OPTIONS_SETUP_CAPTURE:public_regular_30m", levels=sg.Levels(HIGH, LOW),
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


E1_SOURCE = "OPTIONS_122:public_regular_session_chart"  # 122-IEX-E1 trigger.arm_source


def test_b6_context_window_and_family_are_all_required():
    journal = sg.SignalJournal()
    sep1 = datetime(2026, 9, 1, 14, tzinfo=timezone.utc)
    early = opened(journal, identity=identity(timeframe="30m", pattern="122:1:2U", structure_close_time=sep1),
                   setup_ready_time=sep1, first_seen_time=sep1, strategy="options_122", strategy_epoch="122-IEX-E1",
                   data_source=E1_SOURCE)
    assert early.strategy_epoch == sg.UNREGISTERED_EPOCH and "effective window" in early.epoch_reason
    family = opened(journal, identity=identity(timeframe="30m", pattern="222:2U:2U"),
                    strategy="options_122", strategy_epoch="122-IEX-E1", data_source=E1_SOURCE)
    assert family.strategy_epoch == sg.UNREGISTERED_EPOCH and "family" in family.epoch_reason
    good = opened(journal, identity=identity(timeframe="30m", pattern="122:1:2U"),
                  strategy="options_122", strategy_epoch="122-IEX-E1", data_source=E1_SOURCE)
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


# ── follow-up review of f2aa508 ─────────────────────────────────────────────
# B5: a SIP cross time carried by later evidence is part of the chronology.

_KEY = "SPY|1H|2026-10-06T14:00:00Z|222:2U:2U"


def _capture_rows(sip_crossed_at="2026-10-06T14:00:05+00:00"):
    watching = {
        "record_type": "WATCHING", "structure_key": _KEY, "ticker": "SPY", "timeframe": "1H",
        "pattern": "222:2U:2U", "status": "WATCHING", "boundary_high": HIGH, "boundary_low": LOW,
        "structure_close": "2026-10-06T14:00:00+00:00", "knowable_at": "2026-10-06T14:00:00+00:00",
        "first_seen_at": "2026-10-06T14:00:20+00:00", "observed_at": "2026-10-06T14:00:20+00:00",
        "revision": 0, "observation_only": True, "execution_authority": False,
        "capture_id": "OPTIONS_SETUP_CAPTURE", "level_source": "public_regular_30m",
    }
    resolution = {**watching, "record_type": "RESOLUTION", "status": "TRIGGERED", "direction": "LONG",
                  "trigger_crossed_at": "2026-10-06T14:10:00+00:00", "detected_at": "2026-10-06T14:10:03+00:00",
                  "trigger_feed": "iex", "trigger_source": "alpaca_iex", "observed_at": "2026-10-06T14:10:05+00:00"}
    reconciliation = {**resolution, "record_type": "RECONCILIATION", "sip_crossed_at": sip_crossed_at,
                      "prospective_catch": True, "capture_late": False, "observed_at": "2026-10-06T14:30:00+00:00"}
    return [watching, resolution, reconciliation]


def _fold_one(rows):
    fold = ca.fold_capture_rows(rows, strategy=CATCH_STRATEGY, strategy_epoch=CATCH_EPOCH, registry=REGISTRY)
    return fold.signal_for(_KEY)


def test_b5_adapter_sip_cross_before_first_seen_is_not_a_catch():
    s = _fold_one(_capture_rows())
    assert s.state is sg.LifecycleState.TRIGGERED
    assert s.prearmed is False
    assert s.capture["prospective_catch"] is False
    assert s.signal_integrity is not sg.IntegrityStatus.VALID
    assert not s.is_prospective_catch
    assert sg.verify_record(sg.to_record(s), registry=REGISTRY) == []


def test_b5_adapter_sip_cross_after_first_seen_stays_a_catch():
    s = _fold_one(_capture_rows(sip_crossed_at="2026-10-06T14:09:58+00:00"))
    assert s.prearmed is True and s.is_prospective_catch
    assert s.signal_integrity is sg.IntegrityStatus.VALID


def test_b5_early_sip_cross_revokes_an_existing_catch():
    journal = _reg_journal()
    s = caught(journal)
    early = sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=20),
                                 sip_crossed_at=(s.first_seen_time - timedelta(seconds=5)).isoformat())
    s = journal.append(early)
    assert s.prearmed is False and s.catch_revoked
    assert s.signal_integrity is sg.IntegrityStatus.DEGRADED and not s.is_prospective_catch


def test_b5_catch_claim_with_early_sip_cross_is_refused():
    journal = _reg_journal()
    s = trigger(journal, _reg_opened(journal), at=AT)
    with pytest.raises(sg.LifecycleError, match="pre-armed"):
        journal.append(sg.observation_event(
            journal, s.signal_id, detected_at=AT + timedelta(minutes=1), prospective_catch=True,
            sip_crossed_at=(s.first_seen_time - timedelta(seconds=5)).isoformat()))


@pytest.mark.parametrize("crossed, recorded, message", [
    (T0 - timedelta(days=1), AT + timedelta(minutes=1), "precede structure_close_time"),
    (AT + timedelta(hours=3), AT + timedelta(minutes=1), "later than the event that records it"),
])
def test_b5_cross_times_must_lie_between_close_and_recording(crossed, recorded, message):
    journal = _reg_journal()
    s = trigger(journal, _reg_opened(journal), at=AT)
    for key in ("sip_crossed_at", "trigger_crossed_at"):
        with pytest.raises(sg.LifecycleError, match=message):
            journal.append(sg.observation_event(journal, s.signal_id, detected_at=recorded,
                                                **{key: crossed.isoformat()}))


def test_b5_direct_construction_with_early_cross_cannot_be_valid():
    s = caught()
    with pytest.raises(sg.LifecycleError, match="pre-armed"):
        replace(s, capture={**s.capture, "sip_crossed_at": (s.first_seen_time - timedelta(seconds=1)).isoformat()})
    with pytest.raises(sg.LifecycleError, match="precede structure_close_time"):
        replace(s, capture={**s.capture, "sip_crossed_at": (T0 - timedelta(days=1)).isoformat()})


# B2/B4: a stored record cannot claim a catch its own history contradicts.


def test_b2_forged_record_resolution_is_derived_from_history():
    record = sg.to_record(_missed_late())
    forged = {**record, "signal_integrity": "VALID", "prospective_catch": True, "resolution_state": None}
    problems = sg.verify_record(forged, registry=REGISTRY)
    assert "resolution_state does not match the record's history" in problems
    assert "VALID signal integrity on a miss or blocked observation" in problems


def test_b4_forged_valid_record_with_late_or_unarmed_capture_is_detected():
    record = sg.to_record(caught())
    assert sg.verify_record(record, registry=REGISTRY) == []
    late = {**record, "capture": {**record["capture"], "capture_late": True}}
    assert "VALID signal integrity on a late or gapped capture" in sg.verify_record(late, registry=REGISTRY)
    unarmed = {**record, "capture": {**record["capture"],
                                     "sip_crossed_at": record["setup_ready_time"]}}
    assert "VALID signal integrity without pre-armed prospective_catch evidence" in sg.verify_record(
        unarmed, registry=REGISTRY)
    state = {**record, "lifecycle_state": "EXPIRED"}
    assert "lifecycle_state does not match the record's history" in sg.verify_record(state, registry=REGISTRY)


# B3: capture evidence is exact on every construction, not only through events.


@pytest.mark.parametrize("evidence", [
    {"capture_late": "yes"}, {"prospective_catch": 1}, {"true_lag_seconds": float("nan")},
    {"sip_crossed_at": "2026-10-06T14:00:05"}, {"execution_authority": False},
])
def test_b3_replace_cannot_smuggle_untyped_capture(evidence):
    s = caught()
    with pytest.raises(sg.LifecycleError):
        replace(s, capture={**s.capture, **evidence})


def test_b3_hand_built_epoch_cannot_certify_an_executed_outcome():
    fake = registered_epoch(strategy="fake", epoch="FAKE-1")
    s = caught()
    forged = replace(s, strategy="fake", strategy_epoch="FAKE-1", registered_epoch=fake,
                     signal_id=sg.make_signal_id(s.structure_id, "fake", "FAKE-1"))
    o = outcome(forged, executed=True, pnl_basis="executed")
    assert "signal epoch is not the registry's epoch definition" in oc.validate_outcome(o, forged, registry=REGISTRY)
    assert "signal epoch is not the registry's epoch definition" not in oc.validate_outcome(
        outcome(s, executed=True, pnl_basis="executed"), s, registry=REGISTRY)


# B4: data integrity and data_delayed are append-only too.


def test_b4_data_integrity_can_only_be_demoted_and_delay_is_sticky():
    journal = _reg_journal()
    s = caught(journal)
    s = journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=1),
                                          data_integrity="DEGRADED"))
    with pytest.raises(sg.LifecycleError, match="only demotion"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=2),
                                          data_integrity="VALID"))
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=3),
                                            data_delayed=True))
    with pytest.raises(sg.LifecycleError, match="cannot be cleared"):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=4),
                                            data_delayed=False))


def test_b4_adapter_never_repromotes_data_integrity():
    rows = _capture_rows(sip_crossed_at="2026-10-06T14:09:58+00:00")
    rows[1] = {**rows[1], "data_delayed": True}
    rows[2] = {k: v for k, v in rows[2].items() if k != "data_delayed"}
    s = _fold_one(rows)
    assert s.data_integrity is sg.IntegrityStatus.DEGRADED and s.capture["data_delayed"] is True


# B3: the adapter does not coerce level revisions.


@pytest.mark.parametrize("revision", ["3", 2.9, True, -1])
def test_b3_adapter_level_revision_is_exact(revision):
    rows = _capture_rows()
    rows[0] = {**rows[0], "revision": revision}
    with pytest.raises(ca.AdapterError):
        _fold_one(rows[:1])


# ── final pass: non-catches are counterfactual only ─────────────────────────


def _late_trigger() -> sg.ProspectiveSignal:
    """Registered, pre-armed TRIGGERED capture that #1145 classified late: not a catch."""
    journal = _reg_journal()
    s = trigger(journal, _reg_opened(journal), at=AT)
    return journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(minutes=5),
                                               capture_late=True, prospective_catch=False,
                                               trigger_source="alpaca_iex"))


def _pending_trigger() -> sg.ProspectiveSignal:
    """TRIGGERED, still pending SIP reconciliation (signal integrity UNKNOWN)."""
    journal = _reg_journal()
    return trigger(journal, _reg_opened(journal), at=AT)


@pytest.mark.parametrize("build", [_late_trigger, _pending_trigger])
def test_non_catch_trigger_outcome_is_counterfactual_only(build):
    s = build()
    assert s.resolution is sg.LifecycleState.TRIGGERED and not s.is_prospective_catch
    assert "a non-catch outcome must use pnl_basis=counterfactual" in oc.validate_outcome(outcome(s), s)
    assert any("requires a prospective catch" in p
               for p in oc.validate_outcome(outcome(s, executed=True, pnl_basis="executed"), s))
    realised = outcome(s, pnl_basis="counterfactual", net_pnl=M.observed(80.0, "fills"),
                       result_r=M.observed(1.0, "fills"))
    problems = oc.validate_outcome(realised, s)
    assert "a non-catch outcome has no P&L" in problems
    assert "a non-catch outcome has no observed (realised) R" in problems
    # Hypothetical analytics survive, labelled counterfactual, and never read as a trade result.
    honest = outcome(s, pnl_basis="counterfactual", result_r=M.derived(1.0, "t1 reached (hypothetical)"))
    assert oc.validate_outcome(honest, s) == []
    record = oc.to_record(honest, s)
    assert record["result_r"]["value"] == 1.0 and record["prospective_catch"] is False
    assert oc.result_r_value(record) is None


def test_result_r_value_reads_only_verified_catches():
    s = caught()
    record = oc.to_record(outcome(s), s)
    assert record["prospective_catch"] is True and oc.result_r_value(record) == 1.0
    assert oc.result_r_value({**record, "prospective_catch": False}) is None
    assert oc.result_r_value({**record, "prospective_catch": "true"}) is None
    assert oc.result_r_value({k: v for k, v in record.items() if k != "prospective_catch"}) is None
    assert oc.result_r_value({**record, "pnl_basis": "counterfactual"}) is None
    assert oc.result_r_value({**record, "pnl_basis": "whatever"}) is None


# ── final pass: epoch membership is the epoch's declared scope, exactly ─────


def _scoped(**epoch_kw):
    return se.EpochRegistry((registered_epoch(**epoch_kw),))


@pytest.mark.parametrize("ident_kw, opened_kw, epoch_kw, reason", [
    ({"ticker": "ZZZZ"}, {}, {}, "not in epoch universe PRIMARY_20"),
    ({}, {}, {"universe": None}, "does not declare a known universe"),
    ({}, {}, {"universe": "PRIMARY_21"}, "does not declare a known universe"),
    ({"timeframe": "1h", "pattern": "222:2U:2U"}, {}, {}, "timeframe"),
    ({}, {}, {"timeframe": "60m"}, "timeframe"),
    ({}, {"data_source": "OPTIONS_SETUP_CAPTURE:public_index_30m"}, {}, "arm_source"),
    ({}, {"data_source": "OPTIONS_SETUP_CAPTURE:PUBLIC_REGULAR_30M"}, {}, "arm_source"),
    ({}, {}, {"arm_source": None}, "does not declare trigger.arm_source"),
])
def test_epoch_scope_mismatch_is_unregistered(ident_kw, opened_kw, epoch_kw, reason):
    journal = sg.SignalJournal(_scoped(**epoch_kw))
    s = _reg_opened(journal, identity=identity(**ident_kw), **opened_kw)
    assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and not s.epoch_registered
    assert reason in s.epoch_reason and s.requested_epoch == CATCH_EPOCH


def test_committed_122_epoch_requires_its_own_universe_source_and_timeframe():
    journal = sg.SignalJournal()
    e1 = dict(strategy="options_122", strategy_epoch="122-IEX-E1")
    ident = dict(timeframe="30m", pattern="122:1:2U")
    assert opened(journal, identity=identity(**ident), data_source="OPTIONS_122:public_regular_session_chart",
                  **e1).epoch_registered
    for ident_kw, source, why in (
        (ident, "OPTIONS_SETUP_CAPTURE:public_regular_30m", "arm_source"),  # #1145 level source
        ({**ident, "timeframe": "30M"}, "OPTIONS_122:public_regular_session_chart", "timeframe"),
        ({**ident, "ticker": "AMD"}, "OPTIONS_122:public_regular_session_chart", "universe"),
    ):
        s = opened(sg.SignalJournal(), identity=identity(**ident_kw), data_source=source, **e1)
        assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and why in s.epoch_reason


@pytest.mark.parametrize("source", [None, "alpaca_iex_trades", "ALPACA_IEX", "public_index_1m_bar"])
def test_valid_requires_a_declared_trigger_source(source):
    journal = _reg_journal()
    s = trigger(journal, _reg_opened(journal), at=AT)
    evidence = {"prospective_catch": True, "capture_late": False, "gap_through": False}
    if source is not None:
        evidence["trigger_source"] = source
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=AT + timedelta(seconds=4), **evidence))
    assert "trigger source" in s.valid_integrity_problem()
    with pytest.raises(sg.LifecycleError, match="trigger source"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=AT + timedelta(seconds=5),
                                          signal_integrity="VALID"))


def test_adapter_caps_an_undeclared_trigger_source_below_valid():
    rows = _capture_rows(sip_crossed_at="2026-10-06T14:09:58+00:00")
    rows[1] = {**rows[1], "trigger_source": "alpaca_iex_unknown"}
    rows[2] = {**rows[2], "trigger_source": "alpaca_iex_unknown"}
    s = _fold_one(rows)
    assert s.prearmed is True and s.signal_integrity is sg.IntegrityStatus.DEGRADED and not s.is_prospective_catch


def test_record_outside_epoch_scope_is_detected():
    record = sg.to_record(caught())
    assert sg.verify_record(record, registry=REGISTRY) == []
    moved = {**record, "data_source": "OPTIONS_SETUP_CAPTURE:public_index_30m"}
    assert any("outside its epoch's scope" in p for p in sg.verify_record(moved, registry=REGISTRY))
    resourced = {**record, "capture": {**record["capture"], "trigger_source": "alpaca_iex_trades"}}
    assert "record trigger source is not a source the epoch declares" in sg.verify_record(
        resourced, registry=REGISTRY)


@pytest.mark.parametrize("script", ["options_122_prospective_collect.py", "options_212r_prospective_collect.py"])
def test_primary_20_universe_matches_the_collectors(script):
    import ast
    from pathlib import Path

    tree = ast.parse((Path(__file__).resolve().parents[1] / "scripts" / script).read_text())
    declared = next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PRIMARY_20")
    assert sg.EPOCH_UNIVERSES["PRIMARY_20"] == frozenset(declared) and len(declared) == 20


@pytest.mark.parametrize("source", [
    "OPTIONS_SETUP_CAPTURE:evil:public_regular_30m",  # extra ':' must not be stripped away
    "public_regular_30m",                             # bare level source, no capture_id
    ":public_regular_30m",
    "OPTIONS_SETUP_CAPTURE:",
    "OPTIONS_SETUP_CAPTURE:public_regular_30m ",
])
def test_data_source_must_be_exactly_capture_id_colon_arm_source(source):
    assert sg.level_source_of(source) != "public_regular_30m"
    s = _reg_opened(_reg_journal(), data_source=source)
    assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and "arm_source" in s.epoch_reason


def test_adapter_level_source_with_separator_is_unregistered():
    rows = _capture_rows(sip_crossed_at="2026-10-06T14:09:58+00:00")
    rows = [{**r, "level_source": "evil:public_regular_30m"} for r in rows]
    s = _fold_one(rows)
    assert s.strategy_epoch == sg.UNREGISTERED_EPOCH and not s.is_prospective_catch
    assert s.signal_integrity is not sg.IntegrityStatus.VALID


def test_non_valid_record_with_registered_label_is_scope_checked():
    record = sg.to_record(_reg_opened(_reg_journal()))  # WATCHING, signal integrity UNKNOWN
    assert record["strategy_epoch"] == CATCH_EPOCH and sg.verify_record(record, registry=REGISTRY) == []
    moved = {**record, "data_source": "C:other"}
    assert any("outside its epoch's scope" in p for p in sg.verify_record(moved, registry=REGISTRY))
    unhashed = {**record, "epoch_definition_sha256": None}
    assert "record epoch is not the registered epoch definition" in sg.verify_record(unhashed, registry=REGISTRY)
