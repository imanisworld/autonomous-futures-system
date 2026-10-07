from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from alert_ranker.setup_capture import structure_key as capture_key
from options_evidence import signal as sg
from options_evidence import strategy_epochs as se
from options_evidence.strategy_epochs import LEGACY_UNVERSIONED

T0 = datetime(2026, 10, 6, 14, 0, tzinfo=timezone.utc)  # structure close
HIGH, LOW = 501.25, 498.10

# A test-only registered epoch whose scope (1H, 222 family, PRIMARY_20,
# #1145 public_regular_30m levels, alpaca_iex/alpaca_sip triggers, window from
# 2026-09-01) matches the default ``identity()`` / ``opened()`` below. The committed registry's
# only epoch (options_122 / 122-IEX-E1: 30m, 122 family) does not, so signals
# opened with the defaults are UNREGISTERED_EPOCH and can never be VALID.
CATCH_STRATEGY, CATCH_EPOCH = "options_222_test", "222-1H-TEST"


def registered_epoch(
    *, strategy=CATCH_STRATEGY, epoch=CATCH_EPOCH, timeframe="1H", family="STRAT_2_2_2",
    effective_from=datetime(2026, 9, 1, tzinfo=timezone.utc), effective_until=None, status=se.EpochStatus.FROZEN,
    universe="PRIMARY_20", arm_source="public_regular_30m", provisional_source="alpaca_iex",
    authoritative_reconciliation="alpaca_sip",
) -> se.StrategyEpoch:
    setup = {"family": family, "timeframe": timeframe, "universe": universe}
    trigger_block = {"rule": "first strict break", "arm_source": arm_source, "provisional_source": provisional_source,
                     "authoritative_reconciliation": authoritative_reconciliation}
    definition = {
        "setup": {k: v for k, v in setup.items() if v is not None},
        "trigger": {k: v for k, v in trigger_block.items() if v is not None},
        "target": {"t1": "1R"},
        "filters": {},
        "authority": {
            "observation_only": True, "execution_authority": False, "risk_reservation": False, "trade_alerts": False,
        },
    }
    return se.StrategyEpoch(
        strategy=strategy, epoch=epoch, status=status, definition=definition, thresholds={},
        definition_sha256=se.definition_hash(definition, {}), effective_from=effective_from,
        effective_until=effective_until, source_commit="a" * 40, preregistration_doc="docs/test.md",
        oos_reference=None, supersedes=None, observation_only=True,
    )


REGISTRY = se.EpochRegistry((registered_epoch(),))


def identity(**overrides) -> sg.StructureIdentity:
    data = dict(ticker="spy", timeframe="1H", pattern="222:2U:2U", structure_close_time=T0)
    data.update(overrides)
    return sg.StructureIdentity(**data)


def opened(journal: sg.SignalJournal, **overrides) -> sg.ProspectiveSignal:
    kwargs = dict(
        strategy="options_122",
        strategy_epoch="122-IEX-E1",
        setup_ready_time=T0,
        first_seen_time=T0 + timedelta(seconds=20),
        data_source="OPTIONS_SETUP_CAPTURE:public_regular_30m",
        levels=sg.Levels(HIGH, LOW),
    )
    ident = overrides.pop("identity", identity())
    kwargs["registry"] = journal.registry
    kwargs.update(overrides)
    return journal.append(sg.open_signal(ident, **kwargs))


def caught(journal: sg.SignalJournal | None = None, *, at=None) -> sg.ProspectiveSignal:
    """A clean prospective catch under the registered test epoch (VALID signal integrity)."""
    journal = journal if journal is not None else sg.SignalJournal(REGISTRY)
    at = at or T0 + timedelta(minutes=10)
    s = opened(journal, strategy=CATCH_STRATEGY, strategy_epoch=CATCH_EPOCH)
    s = trigger(journal, s, at=at)
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=at + timedelta(seconds=4),
                                            prospective_catch=True, capture_late=False, gap_through=False,
                                            trigger_source="alpaca_iex"))
    return journal.append(sg.integrity_event(journal, s.signal_id, detected_at=at + timedelta(seconds=4),
                                             signal_integrity="VALID", data_integrity="VALID"))


def trigger(journal, s, *, at, direction="LONG", state=sg.LifecycleState.TRIGGERED, detected=None, **payload):
    return journal.append(
        sg.state_event(
            journal, s.signal_id, state, market_time=at, detected_at=detected or at + timedelta(seconds=3),
            reason="first_observable_cross", payload={"direction": direction, **payload},
        )
    )


def test_structure_id_is_exactly_the_1145_structure_key():
    ident = identity(ticker=" spy ", structure_close_time="2026-10-06T10:00:00-04:00")
    assert ident.structure_id == capture_key(
        ticker="SPY", timeframe="1H", structure_close=T0, pattern="222:2U:2U"
    )
    assert ident.structure_id == "SPY|1H|2026-10-06T14:00:00Z|222:2U:2U"


def test_levels_are_attributes_not_identity():
    journal = sg.SignalJournal()
    s = opened(journal)
    revised = journal.append(sg.levels_event(journal, s.signal_id, sg.Levels(501.26, 498.10, 1),
                                             detected_at=T0 + timedelta(minutes=1)))
    assert revised.structure_id == s.structure_id and revised.signal_id == s.signal_id
    assert revised.levels.boundary_high == 501.26
    with pytest.raises(sg.LifecycleError, match="revision must increase"):
        journal.append(sg.levels_event(journal, s.signal_id, sg.Levels(502.0, 498.10, 1),
                                       detected_at=T0 + timedelta(minutes=2)))


def test_watching_is_two_sided_until_resolution():
    journal = sg.SignalJournal()
    s = opened(journal)
    assert s.direction is None and s.trigger is None and s.invalidation is None
    long_ = trigger(journal, s, at=T0 + timedelta(minutes=10))
    assert (long_.direction, long_.trigger, long_.invalidation) == ("LONG", HIGH, LOW)
    j2 = sg.SignalJournal()
    short = trigger(j2, opened(j2), at=T0 + timedelta(minutes=10), direction="SHORT")
    assert (short.trigger, short.invalidation) == (LOW, HIGH)


def test_one_structure_two_strategies_are_two_signals_not_a_merge():
    journal = sg.SignalJournal()
    first = opened(journal)
    second = opened(journal, strategy="options_212r", strategy_epoch="212R-E1")
    assert first.structure_id == second.structure_id
    assert first.signal_id != second.signal_id
    assert len(journal.by_structure(first.structure_id)) == 2


def test_duplicate_open_is_refused_but_identical_replay_is_idempotent():
    journal = sg.SignalJournal()
    event = sg.open_signal(identity(), strategy="s", strategy_epoch="e", setup_ready_time=T0,
                           first_seen_time=T0, data_source="x", levels=sg.Levels(HIGH, LOW))
    journal.append(event)
    assert journal.append(event).signal_id == event.signal_id
    with pytest.raises(sg.LifecycleError, match="already holds a different event"):
        journal.append(replace(event, payload={**event.payload, "data_source": "y"}))
    with pytest.raises(sg.LifecycleError, match="expected seq"):
        journal.append(replace(event, seq=5))


def test_full_lifecycle_watching_triggered_closed():
    journal = sg.SignalJournal()
    s = opened(journal)
    at = T0 + timedelta(minutes=12)
    s = trigger(journal, s, at=at)
    assert s.prearmed is True and s.detection_lag_seconds == 3.0
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.OUTCOME_CLOSED,
                                      detected_at=at + timedelta(hours=2), reason="t1",
                                      payload={"outcome_ref": "oc_1"}))
    assert s.terminal and s.links.outcome_ref == "oc_1"
    assert [c.state.value for c in s.history] == ["WATCHING", "TRIGGERED", "OUTCOME_CLOSED"]
    with pytest.raises(sg.LifecycleError, match="terminal"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED,
                                      detected_at=at + timedelta(hours=3), reason="x"))


@pytest.mark.parametrize(
    "terminal", ["INVALIDATED", "EXPIRED", "DATA_BLOCKED", "AMBIGUOUS"]
)
def test_watching_terminal_states(terminal):
    journal = sg.SignalJournal()
    s = opened(journal)
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState(terminal),
                                      detected_at=T0 + timedelta(hours=1), reason=terminal.lower()))
    assert s.terminal and s.direction is None


def test_reconciliation_can_demote_triggered_to_data_blocked():
    journal = sg.SignalJournal()
    s = trigger(journal, opened(journal), at=T0 + timedelta(minutes=5))
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.DATA_BLOCKED,
                                      detected_at=T0 + timedelta(hours=2), reason="sip_reconcile_unavailable"))
    assert s.state is sg.LifecycleState.DATA_BLOCKED and s.terminal


@pytest.mark.parametrize(
    "src,dst",
    [("TRIGGERED", "INVALIDATED"), ("TRIGGERED", "EXPIRED"), ("TRIGGERED", "TRIGGERED"),
     ("WATCHING", "OUTCOME_CLOSED")],
)
def test_illegal_transitions_are_refused(src, dst):
    journal = sg.SignalJournal()
    s = opened(journal)
    at = T0 + timedelta(minutes=5)
    if src == "TRIGGERED":
        s = trigger(journal, s, at=at)
    with pytest.raises(sg.LifecycleError):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState(dst), market_time=at,
                                      detected_at=at + timedelta(minutes=1), reason="x",
                                      payload={"outcome_ref": "o", "direction": "LONG"}))


def test_resolution_requires_direction_and_times():
    journal = sg.SignalJournal()
    s = opened(journal)
    at = T0 + timedelta(minutes=5)
    with pytest.raises(sg.LifecycleError, match="resolved direction"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                      market_time=at, detected_at=at, reason="b"))
    with pytest.raises(sg.LifecycleError, match="trigger market time"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                      detected_at=at, reason="b", payload={"direction": "LONG"}))


def test_structure_first_seen_after_trigger_cannot_be_a_clean_trigger():
    journal = sg.SignalJournal()
    s = opened(journal, first_seen_time=T0 + timedelta(minutes=20))
    with pytest.raises(sg.LifecycleError, match="MISSED_LATE"):
        trigger(journal, s, at=T0 + timedelta(minutes=10), detected=T0 + timedelta(minutes=21))
    late = trigger(journal, s, at=T0 + timedelta(minutes=10), detected=T0 + timedelta(minutes=21),
                   state=sg.LifecycleState.MISSED_LATE)
    assert late.prearmed is False


def test_clock_inversions_are_refused():
    journal = sg.SignalJournal()
    s = opened(journal)
    with pytest.raises(sg.LifecycleError, match="detection cannot precede|recorded before it happened"):
        trigger(journal, s, at=T0 + timedelta(minutes=10), detected=T0 + timedelta(minutes=9))
    with pytest.raises(sg.LifecycleError, match="precede structure close"):
        trigger(journal, s, at=T0 - timedelta(minutes=1), detected=T0 + timedelta(minutes=1))
    with pytest.raises(sg.LifecycleError, match="first_seen_time cannot precede"):
        sg.open_signal(identity(), strategy="s", strategy_epoch="e", setup_ready_time=T0,
                       first_seen_time=T0 - timedelta(seconds=1), data_source="x", levels=sg.Levels(HIGH, LOW))


def test_missed_gap_needs_gap_evidence_and_closed_needs_outcome_ref():
    journal = sg.SignalJournal()
    s = opened(journal)
    at = T0 + timedelta(hours=1)
    with pytest.raises(sg.LifecycleError, match="gap_through"):
        trigger(journal, s, at=at, state=sg.LifecycleState.MISSED_GAP)
    s = trigger(journal, s, at=at, state=sg.LifecycleState.MISSED_GAP, gap_through=True)
    with pytest.raises(sg.LifecycleError, match="outcome_ref"):
        journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.OUTCOME_CLOSED,
                                      detected_at=at + timedelta(hours=1), reason="closed"))


def test_levels_and_identity_reject_bad_values():
    with pytest.raises(sg.LifecycleError):
        sg.Levels(498.0, 501.0)
    with pytest.raises(sg.LifecycleError):
        sg.Levels(float("nan"), 1.0)
    with pytest.raises(sg.LifecycleError):
        identity(structure_close_time=datetime(2026, 10, 6, 14, 0))
    with pytest.raises(sg.LifecycleError):
        identity(pattern=" ")


def test_observation_carries_only_capture_evidence():
    journal = sg.SignalJournal()
    s = trigger(journal, opened(journal), at=T0 + timedelta(minutes=5))
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=30),
                                            capture_late=False, prospective_catch=True))
    assert s.capture["prospective_catch"] is True
    with pytest.raises(sg.LifecycleError, match="capture evidence"):
        journal.append(sg.observation_event(journal, s.signal_id, detected_at=T0 + timedelta(minutes=31),
                                            execution_authority=True))
    with pytest.raises(TypeError):
        s.capture["prospective_catch"] = False  # read-only mapping


def test_signal_cannot_be_opened_without_observation_only():
    journal = sg.SignalJournal()
    for value in (False, None, 1, "true"):
        with pytest.raises(sg.LifecycleError, match="observation-only"):
            opened(journal, observation_only=value)
    assert opened(journal).execution_authority is False


@pytest.mark.parametrize("value", [True, False, "true", "false", 1, 0, None])
def test_integrity_events_never_carry_authority(value):
    journal = sg.SignalJournal(REGISTRY)
    s = caught(journal)
    with pytest.raises(sg.LifecycleError, match="only carry integrity statuses"):
        journal.append(sg.integrity_event(journal, s.signal_id, detected_at=T0 + timedelta(hours=1),
                                          execution_authority=value, authority_ref="op-1"))
    assert journal.get(s.signal_id).execution_authority is False


def test_links_accumulate_and_late_links_are_allowed():
    journal = sg.SignalJournal()
    s = opened(journal, links=sg.SignalLinks(scanner_sighting_ids=("scan:1",)))
    assert s.links.capture_structure_key == s.structure_id
    s = trigger(journal, s, at=T0 + timedelta(minutes=10))
    # A scanner sighting recorded with an earlier timestamp is still linkable.
    s = journal.append(sg.link_event(journal, s.signal_id, sg.SignalLinks(
        scanner_sighting_ids=("scan:1", "scan:2"), context_snapshot_ids=("gex:3",),
        contract_plan_refs=("plan:SPY-C",)), detected_at=T0 + timedelta(minutes=1)))
    assert s.links.scanner_sighting_ids == ("scan:1", "scan:2")
    with pytest.raises(sg.LifecycleError, match="exactly one"):
        sg.SignalLinks(outcome_ref="a").merged(sg.SignalLinks(outcome_ref="b"))


def test_record_round_trip_and_tamper_detection():
    journal = sg.SignalJournal()
    s = trigger(journal, opened(journal), at=T0 + timedelta(minutes=5))
    record = sg.to_record(s)
    assert set(sg.REQUIRED_RECORD_FIELDS) <= set(record)
    assert sg.verify_record(record) == []
    assert "structure_id does not match the record's structure fields" in sg.verify_record(
        {**record, "pattern": "212:2D:2U"})
    assert "signal_id does not match structure/strategy/epoch" in sg.verify_record(
        {**record, "strategy_epoch": "other"})
    assert "trigger/invalidation do not match direction and levels" in sg.verify_record(
        {**record, "trigger": 499.0})
    assert "observation-only record claims execution authority" in sg.verify_record(
        {**record, "execution_authority": True})
    watching = sg.to_record(opened(sg.SignalJournal()))
    assert sg.verify_record(watching) == []
    assert "an unresolved (two-sided) structure cannot carry trigger/invalidation" in sg.verify_record(
        {**watching, "trigger": HIGH})


def test_dedupe_groups_by_structure_without_rewriting():
    journal = sg.SignalJournal()
    a = sg.to_record(opened(journal))
    b = sg.to_record(opened(journal, strategy="other", strategy_epoch="e1"))
    grouped = sg.dedupe_records([a, b, dict(a)])
    assert list(grouped) == [a["structure_id"]] and len(grouped[a["structure_id"]]) == 3
