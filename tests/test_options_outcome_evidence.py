from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from options_evidence import outcome as oc
from options_evidence import signal as sg
from tests.test_options_prospective_signal import T0, opened, trigger

TRIG = T0 + timedelta(minutes=10)
M = oc.Measured


def triggered_signal() -> sg.ProspectiveSignal:
    journal = sg.SignalJournal()
    return trigger(journal, opened(journal), at=TRIG, detected=TRIG + timedelta(seconds=2))


def outcome(signal: sg.ProspectiveSignal, **overrides) -> oc.OutcomeEvidence:
    data = dict(
        signal_id=signal.signal_id,
        strategy_epoch=signal.strategy_epoch,
        executed=False,
        pnl_basis="paper_equivalent",
        target_1=M.observed(504.40, "frozen_plan"),
        target_2=M.unavailable("no second structural level"),
        invalidation=signal.invalidation,
        premium_entry=M.observed(3.20, "public_chain_ask"),
        premium_stop=M.derived(2.40, "0.75 x entry"),
        mae_price=M.observed(499.675, "alpaca_sip_1m"),
        mfe_price=M.observed(504.40, "alpaca_sip_1m"),
        t1_hit_at=M.observed(TRIG + timedelta(minutes=40), "alpaca_sip_1m"),
        t2_hit_at=M.not_applicable("no target 2"),
        invalidation_hit_at=M.unavailable("not hit before close"),
        trim_event=M.unavailable("paper lane does not model trims"),
        runner_outcome=M.not_applicable("no runner policy in epoch"),
        result_r=M.derived(1.0, "t1 reached"),
        gross_pnl=M.unavailable("no exit quote captured"),
        net_pnl=M.unavailable("no exit quote captured"),
    )
    data.update(overrides)
    return oc.OutcomeEvidence(**data)


def test_valid_outcome_derives_r_and_times_from_the_signal():
    s = triggered_signal()
    o = outcome(s)
    assert oc.validate_outcome(o, s) == []
    # risk unit = 501.25 - 498.10 = 3.15
    assert o.mfe_r(s).value == pytest.approx((504.40 - 501.25) / 3.15)
    assert o.mae_r(s).value == pytest.approx(-0.5)
    assert o.time_to_trigger(s).value == 600.0
    assert o.time_to_t1(s).value == 2400.0
    assert o.time_to_t2(s).status is oc.EvidenceStatus.UNAVAILABLE
    rec = oc.to_record(o, s)
    assert rec["mfe_r"]["status"] == "DERIVED"
    assert oc.result_r_value(rec) == 1.0


def test_missing_evidence_must_be_labelled_with_reason_not_zero():
    with pytest.raises(oc.OutcomeError, match="requires a reason"):
        M.unavailable("")
    with pytest.raises(oc.OutcomeError, match="must not carry a value"):
        M(0.0, oc.EvidenceStatus.UNAVAILABLE, reason="x")
    with pytest.raises(oc.OutcomeError, match="requires a source"):
        M(1.0, oc.EvidenceStatus.OBSERVED)
    with pytest.raises(oc.OutcomeError, match="finite"):
        M.derived(float("nan"))


def test_unavailable_result_is_never_read_as_zero():
    s = triggered_signal()
    rec = oc.to_record(outcome(s, result_r=M.unavailable("data gap")), s)
    assert oc.result_r_value(rec) is None


def test_premium_path_cannot_be_synthetic_or_misordered():
    with pytest.raises(oc.OutcomeError, match="source"):
        oc.PremiumMark(TRIG, 3.1, 3.2, "")
    with pytest.raises(oc.OutcomeError, match="crossed"):
        oc.PremiumMark(TRIG, 3.3, 3.2, "public")
    s = triggered_signal()
    m1 = oc.PremiumMark(TRIG + timedelta(minutes=1), 3.10, 3.20, "public")
    m0 = oc.PremiumMark(TRIG - timedelta(minutes=1), 3.10, 3.20, "public")
    bad = outcome(s, premium_path=(m1, m1), premium_path_status=oc.PathStatus.PARTIAL)
    assert "premium path must be strictly time-ordered" in oc.validate_outcome(bad, s)
    early = outcome(s, premium_path=(m0,), premium_path_status=oc.PathStatus.PARTIAL)
    assert "premium path starts before the trigger" in oc.validate_outcome(early, s)
    hidden = outcome(s, premium_path=(m1,), premium_path_status=oc.PathStatus.UNAVAILABLE)
    assert "UNAVAILABLE premium path must not carry marks" in oc.validate_outcome(hidden, s)


def test_spread_and_premium_change_only_from_observed_path():
    s = triggered_signal()
    none = outcome(s)
    assert none.entry_spread_cost().status is oc.EvidenceStatus.UNAVAILABLE
    assert none.premium_change_mid().status is oc.EvidenceStatus.UNAVAILABLE
    path = (
        oc.PremiumMark(TRIG + timedelta(minutes=1), 3.10, 3.30, "public"),
        oc.PremiumMark(TRIG + timedelta(minutes=41), 4.00, 4.10, "public"),
    )
    partial = outcome(s, premium_path=path[:1], premium_path_status=oc.PathStatus.PARTIAL)
    assert partial.entry_spread_cost().value == pytest.approx(0.10)
    assert partial.premium_change_mid().status is oc.EvidenceStatus.UNAVAILABLE
    full = outcome(s, premium_path=path, premium_path_status=oc.PathStatus.CAPTURED)
    assert full.premium_change_mid().value == pytest.approx(4.05 - 3.20)


def test_outcome_cannot_redefine_identity_or_risk_unit():
    s = triggered_signal()
    assert "outcome invalidation differs from the signal's frozen invalidation" in oc.validate_outcome(
        outcome(s, invalidation=497.0), s
    )
    assert "outcome strategy_epoch does not match the signal" in oc.validate_outcome(
        outcome(s, strategy_epoch="other"), s
    )


def test_paper_outcome_cannot_claim_executed_pnl_and_vice_versa():
    s = triggered_signal()
    assert "a non-executed outcome cannot claim executed P&L" in oc.validate_outcome(
        outcome(s, pnl_basis="executed"), s
    )
    assert "an executed outcome must use pnl_basis=executed" in oc.validate_outcome(
        outcome(s, executed=True), s
    )


def test_event_times_must_follow_trigger_and_t1_before_t2():
    s = triggered_signal()
    early = outcome(s, t1_hit_at=M.observed(TRIG - timedelta(minutes=1), "sip"))
    assert "t1_hit_at precedes the trigger" in oc.validate_outcome(early, s)
    order = outcome(
        s,
        t1_hit_at=M.observed(TRIG + timedelta(minutes=50), "sip"),
        t2_hit_at=M.observed(TRIG + timedelta(minutes=20), "sip"),
    )
    assert "t2 hit before t1" in oc.validate_outcome(order, s)


def test_premium_stop_must_be_below_entry():
    s = triggered_signal()
    bad = outcome(s, premium_stop=M.derived(3.50))
    assert "premium_stop must be below premium_entry" in oc.validate_outcome(bad, s)


def test_outcome_requires_a_resolved_signal():
    journal = sg.SignalJournal()
    watching = opened(journal)
    o = outcome(triggered_signal())
    assert oc.validate_outcome(o, watching)[0].startswith("outcome requires a resolved signal")
    with pytest.raises(oc.OutcomeError, match="resolved signal"):
        oc.underlying_r(watching, 500.0)
