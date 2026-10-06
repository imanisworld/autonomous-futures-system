from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from options_evidence import signal as sg
from options_evidence.strategy_epochs import LEGACY_UNVERSIONED

T0 = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)  # structure close


def identity(**overrides) -> sg.StructureIdentity:
    data = dict(
        ticker="spy",
        timeframe="30m",
        pattern="1-2-2",
        direction="long",
        structure_close_time=T0,
        trigger=501.25,
        invalidation=498.10,
    )
    data.update(overrides)
    return sg.StructureIdentity(**data)


def opened(journal: sg.SignalJournal, **overrides) -> sg.ProspectiveSignal:
    kwargs = dict(
        strategy="options_122",
        strategy_epoch="122-IEX-E1",
        setup_ready_time=T0,
        first_seen_time=T0 + timedelta(seconds=20),
        data_source="public_chart+alpaca_iex",
    )
    ident = overrides.pop("identity", identity())
    kwargs.update(overrides)
    return journal.append(sg.open_signal(ident, **kwargs))


def test_structure_id_is_stable_across_components_and_normalisation():
    a = identity()
    b = identity(ticker=" SPY ", direction="LONG", trigger=501.2500001, structure_close_time="2026-10-06T10:00:00-04:00")
    assert a.structure_id == b.structure_id
    assert a.structure_id.startswith("st_")
    assert identity(trigger=501.26).structure_id != a.structure_id
    assert identity(timeframe="1h").structure_id != a.structure_id


def test_one_structure_two_strategies_are_two_signals_not_a_merge():
    journal = sg.SignalJournal()
    first = opened(journal)
    second = opened(journal, strategy="options_212r", strategy_epoch="212R-E1")
    assert first.structure_id == second.structure_id
    assert first.signal_id != second.signal_id
    assert {s.signal_id for s in journal.by_structure(first.structure_id)} == {
        first.signal_id,
        second.signal_id,
    }


def test_duplicate_open_is_refused_but_identical_replay_is_idempotent():
    journal = sg.SignalJournal()
    event = sg.open_signal(
        identity(),
        strategy="options_122",
        strategy_epoch="122-IEX-E1",
        setup_ready_time=T0,
        first_seen_time=T0,
        data_source="x",
    )
    journal.append(event)
    assert journal.append(event).signal_id == event.signal_id  # replay
    with pytest.raises(sg.LifecycleError, match="already holds a different event"):
        journal.append(replace(event, payload={**event.payload, "data_source": "y"}))
    with pytest.raises(sg.LifecycleError, match="expected seq"):
        journal.append(replace(event, seq=5))


def test_full_lifecycle_watching_triggered_closed():
    journal = sg.SignalJournal()
    s = opened(journal)
    assert s.state is sg.LifecycleState.WATCHING
    trig = T0 + timedelta(minutes=12)
    s = journal.append(
        sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED, market_time=trig,
                       detected_at=trig + timedelta(seconds=3), reason="strict break")
    )
    assert s.prearmed is True
    assert s.detection_lag_seconds == 3.0
    s = journal.append(
        sg.state_event(journal, s.signal_id, sg.LifecycleState.OUTCOME_CLOSED,
                       detected_at=trig + timedelta(hours=2), reason="t1", payload={"outcome_ref": "oc_1"})
    )
    assert s.terminal and s.links.outcome_ref == "oc_1"
    assert [c.state.value for c in s.history] == ["WATCHING", "TRIGGERED", "OUTCOME_CLOSED"]
    with pytest.raises(sg.LifecycleError, match="terminal"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED,
                                      detected_at=trig + timedelta(hours=3), reason="x"))


@pytest.mark.parametrize(
    "path",
    [
        ("INVALIDATED",),
        ("EXPIRED",),
        ("MISSED_LATE", "OUTCOME_CLOSED"),
        ("MISSED_GAP", "OUTCOME_CLOSED"),
    ],
)
def test_allowed_paths(path):
    journal = sg.SignalJournal()
    s = opened(journal)
    at = T0 + timedelta(minutes=30)
    for i, name in enumerate(path):
        state = sg.LifecycleState(name)
        payload = {}
        if state is sg.LifecycleState.MISSED_GAP:
            payload = {"gap_open_price": 503.0}
        if state is sg.LifecycleState.OUTCOME_CLOSED:
            payload = {"outcome_ref": "oc"}
        market = at if state in (sg.LifecycleState.MISSED_LATE, sg.LifecycleState.MISSED_GAP) else None
        s = journal.append(sg.state_event(journal, s.signal_id, state, market_time=market,
                                          detected_at=at + timedelta(minutes=i + 1), reason=name, payload=payload))
    assert s.state.value == path[-1]


@pytest.mark.parametrize(
    "src,dst",
    [
        ("TRIGGERED", "INVALIDATED"),
        ("TRIGGERED", "EXPIRED"),
        ("TRIGGERED", "TRIGGERED"),
        ("WATCHING", "OUTCOME_CLOSED"),
    ],
)
def test_illegal_transitions_are_refused(src, dst):
    journal = sg.SignalJournal()
    s = opened(journal)
    at = T0 + timedelta(minutes=5)
    if src == "TRIGGERED":
        s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                          market_time=at, detected_at=at, reason="b"))
    with pytest.raises(sg.LifecycleError):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState(dst), market_time=at,
                                      detected_at=at + timedelta(minutes=1), reason="x",
                                      payload={"outcome_ref": "o"}))


def test_structure_first_seen_after_trigger_cannot_be_a_clean_trigger():
    journal = sg.SignalJournal()
    s = opened(journal, first_seen_time=T0 + timedelta(minutes=20))
    with pytest.raises(sg.LifecycleError, match="MISSED_LATE"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                      market_time=T0 + timedelta(minutes=10),
                                      detected_at=T0 + timedelta(minutes=21), reason="b"))
    late = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.MISSED_LATE,
                                         market_time=T0 + timedelta(minutes=10),
                                         detected_at=T0 + timedelta(minutes=21), reason="seen after trigger"))
    assert late.prearmed is False


def test_clock_inversions_are_refused():
    journal = sg.SignalJournal()
    s = opened(journal)
    with pytest.raises(sg.LifecycleError, match="detection cannot precede"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                      market_time=T0 + timedelta(minutes=10),
                                      detected_at=T0 + timedelta(minutes=9), reason="b"))
    with pytest.raises(sg.LifecycleError, match="precede structure close"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                      market_time=T0 - timedelta(minutes=1),
                                      detected_at=T0 + timedelta(minutes=1), reason="b"))
    with pytest.raises(sg.LifecycleError, match="first_seen_time cannot precede"):
        sg.open_signal(identity(), strategy="s", strategy_epoch="e", setup_ready_time=T0,
                       first_seen_time=T0 - timedelta(seconds=1), data_source="x")


def test_missed_gap_requires_gap_evidence_and_closed_requires_outcome_ref():
    journal = sg.SignalJournal()
    s = opened(journal)
    with pytest.raises(sg.LifecycleError, match="gap open price"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.MISSED_GAP,
                                      market_time=T0 + timedelta(hours=1),
                                      detected_at=T0 + timedelta(hours=1), reason="gap"))
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.MISSED_LATE,
                                      market_time=T0 + timedelta(hours=1),
                                      detected_at=T0 + timedelta(hours=1, minutes=5), reason="late"))
    with pytest.raises(sg.LifecycleError, match="outcome_ref"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.OUTCOME_CLOSED,
                                      detected_at=T0 + timedelta(hours=3), reason="closed"))


def test_identity_rejects_inverted_or_non_finite_levels():
    with pytest.raises(sg.LifecycleError):
        identity(invalidation=502.0)
    with pytest.raises(sg.LifecycleError):
        identity(direction="SHORT")
    with pytest.raises(sg.LifecycleError):
        identity(trigger=float("nan"))
    with pytest.raises(sg.LifecycleError):
        identity(structure_close_time=datetime(2026, 10, 6, 14, 0))


def test_signal_never_opens_with_execution_authority():
    journal = sg.SignalJournal()
    s = opened(journal, observation_only=False)
    assert s.execution_authority is False


def test_observation_only_signal_cannot_be_granted_authority():
    journal = sg.SignalJournal()
    s = opened(journal)
    with pytest.raises(sg.LifecycleError, match="observation-only"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=1),
                                          execution_integrity="VALID", execution_authority=True,
                                          authority_ref="op-approval-1"))


def test_authority_requires_registered_epoch_and_reference():
    journal = sg.SignalJournal()
    legacy = opened(journal, strategy_epoch=LEGACY_UNVERSIONED, observation_only=False)
    with pytest.raises(sg.LifecycleError, match="registered strategy epoch"):
        journal.append(sg.integrity_event(journal, legacy.signal_id, detected_at=T0 + timedelta(minutes=1),
                                          execution_integrity="VALID", execution_authority=True,
                                          authority_ref="x"))
    s = opened(journal, observation_only=False)
    with pytest.raises(sg.LifecycleError, match="authority_ref"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=1),
                                          execution_integrity="VALID", execution_authority=True))


def test_links_accumulate_scanner_watcher_context_plan_outcome():
    journal = sg.SignalJournal()
    s = opened(journal, links=sg.SignalLinks(scanner_sighting_ids=("scan:1",)))
    s = journal.append(sg.link_event(journal, s.signal_id, sg.SignalLinks(
        scanner_sighting_ids=("scan:1", "scan:2"), watcher_refs=("watch:7",),
        context_snapshot_ids=("signa:9", "gex:3"), contract_plan_refs=("plan:SPY-C",)),
        detected_at=T0 + timedelta(minutes=1)))
    assert s.links.scanner_sighting_ids == ("scan:1", "scan:2")
    assert s.links.context_snapshot_ids == ("signa:9", "gex:3")
    with pytest.raises(sg.LifecycleError, match="exactly one outcome"):
        sg.SignalLinks(outcome_ref="a").merged(sg.SignalLinks(outcome_ref="b"))


def test_record_round_trip_carries_every_required_field_and_detects_tampering():
    journal = sg.SignalJournal()
    s = opened(journal)
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                      market_time=T0 + timedelta(minutes=5),
                                      detected_at=T0 + timedelta(minutes=5, seconds=2), reason="b"))
    record = sg.to_record(s)
    assert set(sg.REQUIRED_RECORD_FIELDS) <= set(record)
    assert sg.verify_record(record) == []
    tampered = {**record, "trigger": 499.0}
    assert "structure_id does not match the record's structure fields" in sg.verify_record(tampered)
    relabelled = {**record, "strategy_epoch": "122-IEX-E2"}
    assert "signal_id does not match structure/strategy/epoch" in sg.verify_record(relabelled)
    claims = {**record, "execution_authority": True}
    assert "observation-only record claims execution authority" in sg.verify_record(claims)


def test_dedupe_groups_by_structure_without_rewriting():
    journal = sg.SignalJournal()
    a = sg.to_record(opened(journal))
    b = sg.to_record(opened(journal, strategy="other", strategy_epoch="e1"))
    grouped = sg.dedupe_records([a, b, dict(a)])
    assert list(grouped) == [a["structure_id"]]
    assert len(grouped[a["structure_id"]]) == 3
