"""
tests/test_opportunity_tracker.py

Locks the read-only shadow opportunity tracker: block attribution (schedule-only
vs multi-gate vs risk vs no-setup), the pessimistic deterministic resolver
(target/stop/same-bar/unresolved + MFE/MAE + costs), bracket validity, candidate
expiry, and JSONL persistence round-trip.
"""
from __future__ import annotations

from adaptive.opportunity_tracker import (
    OpportunityCandidate, OpportunityOutcome, OpportunityStore,
    classify_block, resolve_outcome,
    SETUP_BLOCKED, RISK_REJECTED, QUALITY_BLOCKED, NO_SETUP,
)


def _cand(direction="LONG", entry=30000.0, stop=29992.0, target=30024.0, **kw):
    base = dict(
        candidate_id="MNQ:b1:vwap_hold:" + direction,
        source_bar_id="b1", detected_at="2026-06-05T07:00:00+00:00",
        instrument="MNQ", session="asian", timeframe="15", strategy="vwap_hold",
        direction=direction, entry=entry, stop=stop, target=target,
    )
    base.update(kw)
    return OpportunityCandidate(**base)


# ── Attribution ──────────────────────────────────────────────────────────────

def test_schedule_only_block_is_setup_blocked():
    bt, multi = classify_block(has_valid_setup=True, gate_ids=["SESSION_NOT_ALLOWED"], risk_failed_rule=None)
    assert bt == SETUP_BLOCKED and multi is False


def test_schedule_only_via_risk_session_cutoff():
    bt, multi = classify_block(has_valid_setup=True, gate_ids=[], risk_failed_rule="session_cutoff")
    assert bt == SETUP_BLOCKED


def test_schedule_plus_quality_is_not_a_schedule_miss():
    # Multiple independent gates failed → never attribute to schedule alone.
    bt, multi = classify_block(
        has_valid_setup=True,
        gate_ids=["SESSION_WINDOW", "TREND_STRENGTH_BELOW_REQUIRED"],
        risk_failed_rule=None,
    )
    assert bt == QUALITY_BLOCKED and multi is True


def test_risk_rule_is_risk_rejected():
    bt, _ = classify_block(has_valid_setup=True, gate_ids=[], risk_failed_rule="max_daily_loss")
    assert bt == RISK_REJECTED


def test_no_setup_when_no_valid_setup():
    bt, _ = classify_block(has_valid_setup=False, gate_ids=["SESSION_WINDOW"], risk_failed_rule=None)
    assert bt == NO_SETUP


def test_valid_setup_with_no_blockers_is_not_an_opportunity():
    bt, _ = classify_block(has_valid_setup=True, gate_ids=[], risk_failed_rule=None)
    assert bt == NO_SETUP


# ── Bracket validity ─────────────────────────────────────────────────────────

def test_bracket_validity():
    assert _cand("LONG", 100, 99, 102).has_valid_bracket() is True
    assert _cand("SHORT", 100, 101, 98).has_valid_bracket() is True
    assert _cand("LONG", 100, 102, 105).has_valid_bracket() is False   # stop above entry
    assert _cand("SHORT", 100, 99, 98).has_valid_bracket() is False    # stop below entry


# ── Resolver ─────────────────────────────────────────────────────────────────

def test_long_target_hit_pnl_and_costs():
    c = _cand("LONG", 30000, 29992, 30024)
    bars = [{"ts": "t1", "high": 30025, "low": 29999}]  # tags target
    o = resolve_outcome(c, bars)
    assert o.result == "TARGET_HIT"
    # eff_entry 30000.25, exit 30024 → 23.75pt = 95 ticks → 95*0.5 - 5 = 42.5
    assert o.pnl_dollars == 42.5
    assert o.bars_to_resolution == 1


def test_long_stop_hit_pnl_and_costs():
    c = _cand("LONG", 30000, 29992, 30024)
    bars = [{"ts": "t1", "high": 30001, "low": 29990}]  # tags stop
    o = resolve_outcome(c, bars)
    assert o.result == "STOP_HIT"
    # eff_entry 30000.25, eff_exit 29991.75 → -8.5pt = -34 ticks → -34*0.5 - 5 = -22.0
    assert o.pnl_dollars == -22.0


def test_pessimistic_same_bar_is_a_loss():
    c = _cand("LONG", 30000, 29992, 30024)
    bars = [{"ts": "t1", "high": 30025, "low": 29990}]  # straddles BOTH
    o = resolve_outcome(c, bars)
    assert o.result == "STOP_HIT"           # pessimistic
    assert o.entry_touched and o.stop_touched and o.target_touched
    assert o.pessimistic_same_bar is True
    o2 = resolve_outcome(c, bars, pessimistic_both_hit=False)
    assert o2.result == "TARGET_HIT"        # optimistic toggle for comparison


def test_short_target_hit():
    c = _cand("SHORT", 30000, 30008, 29976)
    bars = [{"ts": "t1", "high": 30001, "low": 29975}]  # tags short target (low<=target)
    o = resolve_outcome(c, bars)
    assert o.result == "TARGET_HIT"


def test_unresolved_returns_expired_open_not_dropped():
    c = _cand("LONG", 30000, 29992, 30024)
    bars = [{"ts": "t1", "high": 30005, "low": 29998},
            {"ts": "t2", "high": 30010, "low": 29996}]  # never reaches stop or target
    o = resolve_outcome(c, bars)
    assert o.result == "EXPIRED_OPEN"
    assert o.pnl_dollars == 0.0
    assert o.bars_to_resolution == 2
    assert o.entry_touched is True


def test_entry_never_touched_is_reported_as_unfilled():
    c = _cand("LONG", 30000, 29992, 30024)
    bars = [{"ts": "t1", "high": 29990, "low": 29980}]
    o = resolve_outcome(c, bars)
    assert o.result == "ENTRY_NOT_TOUCHED"
    assert o.entry_touched is False
    assert o.stop_touched is False
    assert o.target_touched is False


def test_mfe_mae_tracked_until_resolution():
    c = _cand("LONG", 30000, 29980, 30040)
    bars = [
        {"ts": "t1", "high": 30012, "low": 29994},   # mfe +12, mae -6
        {"ts": "t2", "high": 30041, "low": 29999},   # target hit; mfe could be +41
    ]
    o = resolve_outcome(c, bars)
    assert o.result == "TARGET_HIT"
    assert o.mae_ticks == -24.0   # -6pt / 0.25
    assert o.mfe_ticks >= 48.0    # at least +12pt/0.25 = 48 before the resolving bar's excursion


# ── Persistence ──────────────────────────────────────────────────────────────

def test_store_roundtrip(tmp_path):
    store = OpportunityStore(log_dir=str(tmp_path))
    c = _cand("LONG", 30000, 29992, 30024, block_type=SETUP_BLOCKED)
    store.record_candidate(c)
    o = resolve_outcome(c, [{"ts": "t1", "high": 30025, "low": 29999}])
    store.record_outcome(o)
    rows = store.read_day()
    assert len(rows) == 2
    assert rows[0]["_type"] == "candidate" and rows[0]["block_type"] == SETUP_BLOCKED
    assert rows[1]["_type"] == "outcome" and rows[1]["result"] == "TARGET_HIT"
    # round-trip the candidate dataclass
    back = OpportunityCandidate.from_dict(rows[0])
    assert back.candidate_id == c.candidate_id and back.has_valid_bracket()
