from __future__ import annotations

from datetime import timedelta

import pytest

from options_evidence import alerts as al
from options_evidence import signal as sg
from tests.test_options_prospective_signal import T0, opened

NOW = T0 + timedelta(minutes=5)


def watching():
    journal = sg.SignalJournal()
    return journal, opened(journal)  # LONG trigger 501.25, invalidation 498.10 (R = 3.15)


def test_watching_alert_and_near_trigger_from_canonical_state_only():
    _, s = watching()
    far = al.alert_for(s, as_of=NOW, last_price=499.0, near_trigger_r=0.25)
    assert far.kind is al.AlertKind.WATCHING
    near = al.alert_for(s, as_of=NOW, last_price=500.80, near_trigger_r=0.25)
    assert near.kind is al.AlertKind.NEAR_TRIGGER
    assert near.distance_to_trigger_r == pytest.approx((501.25 - 500.80) / 3.15)
    # Through the trigger but watcher has not resolved: NEAR_TRIGGER, never synthetic TRIGGERED.
    through = al.alert_for(s, as_of=NOW, last_price=502.0, near_trigger_r=0.25)
    assert through.kind is al.AlertKind.NEAR_TRIGGER


def test_near_trigger_distance_is_a_required_policy_input():
    _, s = watching()
    with pytest.raises(ValueError, match="explicit positive policy"):
        al.alert_for(s, as_of=NOW, last_price=500.9)
    with pytest.raises(ValueError):
        al.alert_for(s, as_of=NOW, last_price=float("nan"), near_trigger_r=0.25)


@pytest.mark.parametrize(
    "state,kind,payload",
    [
        (sg.LifecycleState.TRIGGERED, al.AlertKind.TRIGGERED, {}),
        (sg.LifecycleState.MISSED_LATE, al.AlertKind.MISSED_LATE, {}),
        (sg.LifecycleState.MISSED_GAP, al.AlertKind.MISSED_GAP, {"gap_open_price": 503.0}),
        (sg.LifecycleState.INVALIDATED, al.AlertKind.INVALIDATED, {}),
        (sg.LifecycleState.EXPIRED, al.AlertKind.EXPIRED, {}),
    ],
)
def test_each_lifecycle_state_maps_to_its_alert(state, kind, payload):
    journal, s = watching()
    market = NOW if state in (sg.LifecycleState.TRIGGERED, sg.LifecycleState.MISSED_LATE, sg.LifecycleState.MISSED_GAP) else None
    s = journal.append(sg.state_event(journal, s.signal_id, state, market_time=market,
                                      detected_at=NOW + timedelta(seconds=5), reason="x", payload=payload))
    alert = al.alert_for(s, as_of=NOW + timedelta(seconds=6), last_price=600.0, near_trigger_r=0.25)
    assert alert.kind is kind  # price is ignored once the state has resolved


def test_outcome_closed_is_not_surfaced():
    journal, s = watching()
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED,
                                      detected_at=NOW, reason="window closed"))
    assert al.alert_for(s, as_of=NOW) is not None
    journal2, s2 = watching()
    s2 = journal2.append(sg.state_event(journal2, s2.signal_id, sg.LifecycleState.MISSED_LATE,
                                        market_time=NOW, detected_at=NOW, reason="late"))
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
    cycles = [al.alert_for(s, as_of=NOW + timedelta(minutes=i), last_price=499.0, near_trigger_r=0.25) for i in range(10)]
    assert len(ledger.admit_many(cycles)) == 1
    near = al.alert_for(s, as_of=NOW, last_price=501.0, near_trigger_r=0.25)
    assert ledger.admit(near) is not None
    assert ledger.admit(near) is None


def test_delivery_is_off_by_default():
    _, s = watching()
    alert = al.alert_for(s, as_of=NOW)
    assert al.DeliveryPolicy().deliverable(alert) is False
    assert al.DeliveryPolicy(enabled=True, kinds=frozenset({al.AlertKind.WATCHING})).deliverable(alert) is False
    assert al.DeliveryPolicy(enabled=True, kinds=frozenset({al.AlertKind.WATCHING}), channel="research").deliverable(alert)


def test_alert_module_does_not_import_scanner_or_discord():
    import ast
    from pathlib import Path

    tree = ast.parse(Path(al.__file__).read_text())
    mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert not any(m.startswith(("alert_ranker", "notifications", "webhook", "options_manager")) for m in mods)
