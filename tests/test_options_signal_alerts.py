from __future__ import annotations

from datetime import timedelta

import pytest

from options_evidence import alerts as al
from options_evidence import signal as sg
from tests.test_options_prospective_signal import HIGH, LOW, T0, opened, trigger

NOW = T0 + timedelta(minutes=5)


def watching():
    journal = sg.SignalJournal()
    return journal, opened(journal)  # two-sided: high 501.25 / low 498.10 (range 3.15)


RANGE = HIGH - LOW


def test_watching_alert_and_near_trigger_from_canonical_state_only():
    _, s = watching()
    far = al.alert_for(s, as_of=NOW, last_price=499.70, near_trigger_r=0.25)
    assert far.kind is al.AlertKind.WATCHING and far.near_side == "HIGH"
    near = al.alert_for(s, as_of=NOW, last_price=500.80, near_trigger_r=0.25)
    assert near.kind is al.AlertKind.NEAR_TRIGGER
    assert near.near_side == "HIGH"
    assert near.distance_to_trigger_r == pytest.approx((HIGH - 500.80) / RANGE)
    # Through the boundary but watcher has not resolved: NEAR_TRIGGER, never synthetic TRIGGERED.
    through = al.alert_for(s, as_of=NOW, last_price=502.0, near_trigger_r=0.25)
    assert through.kind is al.AlertKind.NEAR_TRIGGER


def test_watching_near_trigger_is_two_sided_and_never_implies_direction():
    _, s = watching()
    low = al.alert_for(s, as_of=NOW, last_price=498.40, near_trigger_r=0.25)
    assert low.kind is al.AlertKind.NEAR_TRIGGER and low.near_side == "LOW"
    assert low.distance_to_trigger_r == pytest.approx((498.40 - LOW) / RANGE)
    record = low.to_record()
    assert record["direction"] is None and record["trigger"] is None and record["invalidation"] is None
    assert (record["boundary_high"], record["boundary_low"]) == (HIGH, LOW)


def test_resolved_alert_carries_direction_and_evidence_state_from_canonical_signal():
    journal, s = watching()
    s = trigger(journal, s, at=NOW, direction="SHORT")
    alert = al.alert_for(s, as_of=NOW + timedelta(seconds=6), last_price=400.0, near_trigger_r=0.25)
    assert alert.kind is al.AlertKind.TRIGGERED
    assert (alert.direction, alert.trigger, alert.invalidation, alert.near_side) == ("SHORT", LOW, HIGH, None)
    assert alert.prospective_catch is False
    record = alert.to_record()
    assert record["prospective_catch"] is False
    assert record["signal_integrity"] != "VALID"
    assert record["trade_authority"] is False


def test_near_trigger_distance_is_a_required_exact_numeric_policy_input():
    _, s = watching()
    with pytest.raises(ValueError, match="explicit positive policy"):
        al.alert_for(s, as_of=NOW, last_price=500.9)
    with pytest.raises(ValueError):
        al.alert_for(s, as_of=NOW, last_price=float("nan"), near_trigger_r=0.25)
    with pytest.raises(ValueError, match="finite number"):
        al.alert_for(s, as_of=NOW, last_price=True, near_trigger_r=0.25)
    with pytest.raises(ValueError, match="finite number"):
        al.alert_for(s, as_of=NOW, last_price=500.9, near_trigger_r=True)


@pytest.mark.parametrize(
    "state,kind,payload",
    [
        (sg.LifecycleState.TRIGGERED, al.AlertKind.TRIGGERED, {}),
        (sg.LifecycleState.MISSED_LATE, al.AlertKind.MISSED_LATE, {}),
        (sg.LifecycleState.MISSED_GAP, al.AlertKind.MISSED_GAP, {"gap_through": True, "first_print_price": 503.0}),
        (sg.LifecycleState.INVALIDATED, al.AlertKind.INVALIDATED, {}),
        (sg.LifecycleState.EXPIRED, al.AlertKind.EXPIRED, {}),
        (sg.LifecycleState.DATA_BLOCKED, al.AlertKind.DATA_BLOCKED, {}),
        (sg.LifecycleState.AMBIGUOUS, al.AlertKind.AMBIGUOUS, {}),
    ],
)
def test_each_lifecycle_state_maps_to_its_alert(state, kind, payload):
    journal, s = watching()
    resolved = state in (sg.LifecycleState.TRIGGERED, sg.LifecycleState.MISSED_LATE, sg.LifecycleState.MISSED_GAP)
    if resolved:
        s = trigger(journal, s, at=NOW, state=state, detected=NOW + timedelta(seconds=5), **payload)
    else:
        s = journal.append(sg.state_event(journal, s.signal_id, state,
                                          detected_at=NOW + timedelta(seconds=5), reason="x", payload=payload))
    alert = al.alert_for(s, as_of=NOW + timedelta(seconds=6), last_price=600.0, near_trigger_r=0.25)
    assert alert.kind is kind  # price is ignored once the state has resolved


def test_outcome_closed_is_not_surfaced():
    journal, s = watching()
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED,
                                      detected_at=NOW, reason="window closed"))
    assert al.alert_for(s, as_of=NOW) is not None
    journal2, s2 = watching()
    s2 = trigger(journal2, s2, at=NOW, state=sg.LifecycleState.MISSED_LATE, detected=NOW)
    s2 = journal2.append(sg.state_event(journal2, s2.signal_id, sg.LifecycleState.OUTCOME_CLOSED,
                                        detected_at=NOW + timedelta(hours=1), reason="c",
                                        payload={"outcome_ref": "o"}))
    assert al.alert_for(s2, as_of=NOW + timedelta(hours=1)) is None


def test_alerts_never_carry_trade_authority():
    _, s = watching()
    alert = al.alert_for(s, as_of=NOW)
    assert alert.trade_authority is False
    record = alert.to_record()
    assert record["trade_authority"] is False
    assert "execution_authority" not in record


def test_ledger_suppresses_repeat_notices_per_signal_and_kind():
    _, s = watching()
    ledger = al.AlertLedger()
    cycles = [al.alert_for(s, as_of=NOW + timedelta(minutes=i), last_price=499.70, near_trigger_r=0.25) for i in range(10)]
    assert len(ledger.admit_many(cycles)) == 1
    near = al.alert_for(s, as_of=NOW, last_price=501.0, near_trigger_r=0.25)
    assert ledger.admit(near) is not None
    assert ledger.admit(near) is None


def test_delivery_is_off_by_default_and_requires_exact_types():
    _, s = watching()
    alert = al.alert_for(s, as_of=NOW)
    assert al.DeliveryPolicy().deliverable(alert) is False
    assert al.DeliveryPolicy(enabled=True, kinds=frozenset({al.AlertKind.WATCHING})).deliverable(alert) is False
    assert al.DeliveryPolicy(enabled=True, kinds=frozenset({al.AlertKind.WATCHING}), channel="research").deliverable(alert)
    with pytest.raises(ValueError, match="exact bool"):
        al.DeliveryPolicy(enabled="false")  # type: ignore[arg-type]


def test_alert_module_does_not_import_scanner_or_discord():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(al.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("alert_ranker", "notifications", "webhook", "options_manager")) for m in mods)
