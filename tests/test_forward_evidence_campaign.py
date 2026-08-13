from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from execution.forward_evidence_campaign import (
    CAMPAIGN_ID,
    ENV_NAME,
    EVIDENCE_FILENAME,
    STATE_FILENAME,
    EvidenceValidationError,
    _resolve_fixed,
    append_record,
    candidate_record,
    open_campaign_position,
    resolve_canonical_positions,
    resolve_resting_bracket,
    stable_candidate_id,
    stable_event_id,
    validate_record,
)
from ops.forward_campaign_report import build_report


@pytest.fixture(autouse=True)
def _enable_campaign(monkeypatch):
    monkeypatch.setenv(ENV_NAME, CAMPAIGN_ID)


def _record(**overrides):
    args = dict(
        strategy="vwap_hold", variant="control", direction="SHORT",
        signal_timestamp="2026-08-13T14:30:00+00:00", source_timeframe="15m",
        session="new_york", regime="TREND", market_condition="TRENDING",
        original_entry=20000.0, original_stop=20007.5, original_target=19977.5,
        entry_policy="canonical_resting_entry", exit_policy="runner_1R_0.5R",
    )
    args.update(overrides)
    return candidate_record(**args)


def test_shared_event_ids_are_deterministic_across_5m_and_15m_variant():
    control = stable_event_id(
        instrument="MNQ", strategy="vwap_hold", direction="SHORT",
        signal_timestamp="2026-08-13T14:30:00+00:00",
    )
    modified = stable_event_id(
        instrument="MNQ", strategy="vwap_hold", direction="SHORT",
        signal_timestamp="2026-08-13T14:34:59+00:00",
    )
    assert control == modified
    assert stable_candidate_id(control, "control", "15m") != stable_candidate_id(control, "modified", "5m")


def test_bar_open_episode_boundaries_and_representative_pair():
    def event(ts):
        return stable_event_id(
            instrument="MNQ", strategy="vwap_hold", direction="LONG",
            signal_timestamp=ts,
        )

    episode = event("2026-08-13T14:30:00+00:00")
    assert event("2026-08-13T14:35:00+00:00") == episode
    assert event("2026-08-13T14:40:00+00:00") == episode
    assert event("2026-08-13T14:45:00+00:00") != episode
    # Representative 5m early bar and canonical 15m bar are both bar-open
    # timestamps belonging to the 14:30 market episode.
    assert event("2026-08-13T14:35:00+00:00") == event("2026-08-13T14:30:00+00:00")


def test_control_and_modified_are_separately_attributable():
    control = _record()
    modified = _record(
        variant="modified", source_timeframe="5m", event_id=control["event_id"],
        entry_policy="confirmed_5m_close",
    )
    assert control["event_id"] == modified["event_id"]
    assert control["candidate_id"] != modified["candidate_id"]
    assert {control["variant"], modified["variant"]} == {"control", "modified"}


def test_missing_provenance_or_reason_field_fails_closed():
    record = _record()
    del record["generating_git_sha"]
    with pytest.raises(EvidenceValidationError, match="generating_git_sha"):
        validate_record(record)
    record = _record()
    del record["reject_reason"]
    with pytest.raises(EvidenceValidationError, match="reject_reason"):
        validate_record(record)


def test_campaign_position_dedupes_deterministic_candidate(tmp_path):
    record = _record()
    assert open_campaign_position(tmp_path, record) is True
    assert open_campaign_position(tmp_path, record) is False
    rows = (tmp_path / f"{CAMPAIGN_ID}.jsonl").read_text().splitlines()
    assert len(rows) == 1


@pytest.mark.parametrize("requested_id", [None, "wrong_campaign"])
def test_low_level_writers_do_not_write_without_exact_campaign_id(tmp_path, monkeypatch, requested_id):
    if requested_id is None:
        monkeypatch.delenv(ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(ENV_NAME, requested_id)
    record = _record()
    assert append_record(tmp_path, record) is False
    assert open_campaign_position(tmp_path, record) is False
    assert not (tmp_path / EVIDENCE_FILENAME).exists()
    assert not (tmp_path / STATE_FILENAME).exists()


def test_low_level_writer_writes_with_exact_campaign_id(tmp_path):
    assert append_record(tmp_path, _record()) is True
    assert (tmp_path / EVIDENCE_FILENAME).exists()


def test_low_level_writer_rejects_malformed_metadata_before_any_write(tmp_path):
    record = _record()
    record["campaign_id"] = "wrong_campaign"
    with pytest.raises(EvidenceValidationError, match="wrong campaign"):
        append_record(tmp_path, record)
    with pytest.raises(EvidenceValidationError, match="wrong campaign"):
        open_campaign_position(tmp_path, record)
    assert not (tmp_path / EVIDENCE_FILENAME).exists()
    assert not (tmp_path / STATE_FILENAME).exists()


def test_campaign_writer_cannot_mutate_existing_decision_output(tmp_path):
    from strategy.signal_engine import DecisionOutput

    decision = DecisionOutput(
        timestamp=datetime(2026, 8, 13, 14, 30, tzinfo=timezone.utc),
        instrument="MNQ", session="new_york", decision="NO_TRADE",
        reason="test", failed_gates=["TEST_GATE"],
    )
    before = deepcopy(decision.to_dict())
    assert append_record(tmp_path, _record()) is True
    assert decision.to_dict() == before


def test_campaign_does_not_open_overlapping_trials_in_same_population(tmp_path):
    assert open_campaign_position(tmp_path, _record()) is True
    later = _record(signal_timestamp="2026-08-13T14:45:00+00:00")
    assert open_campaign_position(tmp_path, later) is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_close_priced_resolution_excludes_own_pre_entry_bar_and_is_pessimistic(direction):
    record = _record(
        exit_policy="fixed_bracket", hypothetical_fill_price=100.0,
        direction=direction, original_entry=100.0,
        original_stop=99.0 if direction == "LONG" else 101.0,
        original_target=102.0 if direction == "LONG" else 98.0,
    )
    position = {
        "campaign_record": record, "direction": direction, "entry": 100.0,
        "stop": 99.0 if direction == "LONG" else 101.0,
        "target": 102.0 if direction == "LONG" else 98.0,
        "signal_ts": "2026-08-13T14:30:00+00:00",
        "entry_ts": "2026-08-13T14:30:00+00:00",
    }
    entry_bar = {"ts": position["entry_ts"], "high": 102.5, "low": 98.5}
    later = {"ts": "2026-08-13T14:45:00+00:00", "high": 102.5, "low": 98.5}
    bars = [b for b in (entry_bar, later) if b["ts"] > position["entry_ts"]]
    assert bars == [later]
    outcome = _resolve_fixed(position, bars, later["ts"])
    assert outcome["result"] == "LOSS"
    assert outcome["exit_reason"] == "STOP_HIT"
    assert outcome["max_favorable_excursion"] == (2.5 if direction == "LONG" else 1.5)
    assert outcome["max_adverse_excursion"] == (1.5 if direction == "LONG" else 2.5)


def _resting_position(direction: str) -> dict:
    return {
        "direction": direction,
        "entry": 100.0,
        "stop": 99.0 if direction == "LONG" else 101.0,
        "target": 102.0 if direction == "LONG" else 98.0,
    }


def _bar(ts: str, high: float, low: float, close: float = 100.0) -> dict:
    return {"ts": ts, "high": high, "low": low, "close": close}


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_resting_entry_no_touch_is_no_fill(direction):
    bar = _bar("2026-08-13T14:35:00+00:00", 99.0, 98.0) if direction == "LONG" else _bar(
        "2026-08-13T14:35:00+00:00", 102.0, 101.5
    )
    outcome = resolve_resting_bracket(_resting_position(direction), [bar], final=True)
    assert outcome["result"] == "NO_FILL"
    assert outcome["entry_filled"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_resting_entry_touch_only_remains_open(direction):
    outcome = resolve_resting_bracket(
        _resting_position(direction),
        [_bar("2026-08-13T14:35:00+00:00", 100.5, 99.5)],
        final=True,
    )
    assert outcome["result"] == "OPEN"
    assert outcome["entry_filled"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_resting_entry_and_stop_same_bar_is_pessimistic_loss(direction):
    high, low = ((100.5, 98.5) if direction == "LONG" else (101.5, 99.5))
    outcome = resolve_resting_bracket(
        _resting_position(direction), [_bar("2026-08-13T14:35:00+00:00", high, low)]
    )
    assert outcome["result"] == "LOSS"
    assert outcome["fill_bar_ambiguous"] is True
    assert outcome["intrabar_policy"] == "PESSIMISTIC_STOP_FIRST"
    assert outcome["excursion_policy"] == "EXCLUDE_AMBIGUOUS_FILL_BAR"
    assert outcome["max_favorable_excursion"] is None
    assert outcome["max_adverse_excursion"] is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_resting_entry_and_target_same_bar_is_not_an_unproven_win(direction):
    high, low = ((102.5, 99.5) if direction == "LONG" else (100.5, 97.5))
    outcome = resolve_resting_bracket(
        _resting_position(direction),
        [_bar("2026-08-13T14:35:00+00:00", high, low)],
        final=True,
    )
    assert outcome["result"] == "OPEN"
    assert outcome["exit_price"] is None
    assert outcome["fill_bar_target_ambiguous_ignored"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_fill_bar_open_proves_target_before_entry_and_cannot_create_win(direction):
    # Opening beyond the target proves the target price existed before a later
    # move back to the resting entry. The earlier target is not an earned exit.
    bar = (
        {**_bar("2026-08-13T14:35:00+00:00", 102.5, 99.5), "open": 102.25}
        if direction == "LONG"
        else {**_bar("2026-08-13T14:35:00+00:00", 100.5, 97.5), "open": 97.75}
    )
    outcome = resolve_resting_bracket(_resting_position(direction), [bar], final=True)
    assert outcome["result"] == "OPEN"
    assert outcome["fill_bar_target_ambiguous_ignored"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_unearned_fill_bar_target_can_still_be_followed_by_later_stop(direction):
    fill_high, fill_low = ((102.5, 99.5) if direction == "LONG" else (100.5, 97.5))
    stop_high, stop_low = ((100.5, 98.5) if direction == "LONG" else (101.5, 99.5))
    outcome = resolve_resting_bracket(
        _resting_position(direction),
        [
            _bar("2026-08-13T14:35:00+00:00", fill_high, fill_low),
            _bar("2026-08-13T14:40:00+00:00", stop_high, stop_low),
        ],
    )
    assert outcome["result"] == "LOSS"
    assert outcome["exit_ts"] == "2026-08-13T14:40:00+00:00"
    assert outcome["fill_bar_target_ambiguous_ignored"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_resting_entry_stop_and_target_same_bar_is_pessimistic_loss(direction):
    outcome = resolve_resting_bracket(
        _resting_position(direction),
        [_bar("2026-08-13T14:35:00+00:00", 102.5 if direction == "LONG" else 101.5,
              98.5 if direction == "LONG" else 97.5)],
    )
    assert outcome["result"] == "LOSS"
    assert outcome["fill_bar_ambiguous"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("later_result", ["STOP", "TARGET"])
def test_resting_fill_bar_then_later_resolution(direction, later_result):
    fill = _bar("2026-08-13T14:35:00+00:00", 100.5, 99.5)
    if direction == "LONG":
        later = _bar("2026-08-13T14:40:00+00:00", 101.0, 98.5) if later_result == "STOP" else _bar(
            "2026-08-13T14:40:00+00:00", 102.5, 99.5
        )
    else:
        later = _bar("2026-08-13T14:40:00+00:00", 101.5, 99.0) if later_result == "STOP" else _bar(
            "2026-08-13T14:40:00+00:00", 100.5, 97.5
        )
    outcome = resolve_resting_bracket(_resting_position(direction), [fill, later])
    assert outcome["result"] == ("LOSS" if later_result == "STOP" else "WIN")
    assert outcome["exit_ts"] == later["ts"]
    assert outcome["fill_bar_ambiguous"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_resting_resolver_does_not_look_ahead_from_bars_before_actual_fill(direction):
    prefill = _bar("2026-08-13T14:35:00+00:00", 98.8, 98.0) if direction == "LONG" else _bar(
        "2026-08-13T14:35:00+00:00", 102.0, 101.2
    )
    fill_only = _bar("2026-08-13T14:40:00+00:00", 100.5, 99.5)
    outcome = resolve_resting_bracket(_resting_position(direction), [prefill, fill_only], final=True)
    assert outcome["result"] == "OPEN"
    assert outcome["fill_ts"] == fill_only["ts"]


def test_canonical_resting_fill_bar_both_hit_produces_outcome(tmp_path):
    record = _record(
        strategy="vwap_rejection", variant="observer", direction="LONG",
        exit_policy="fixed_bracket", original_entry=100.0,
        original_stop=99.0, original_target=102.0,
    )
    assert open_campaign_position(tmp_path, record)
    rows = resolve_canonical_positions(
        tmp_path,
        instrument="MNQ",
        bars=[_bar("2026-08-13T14:35:00+00:00", 102.5, 98.5)],
        current_bar_ts="2026-08-13T14:35:00+00:00",
    )
    assert len(rows) == 1
    assert rows[0]["terminal_state"] == "LOSS"
    assert rows[0]["fill_bar_ambiguous"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_canonical_fixed_position_resolves_later_without_requiring_fill_bar_in_history(tmp_path, direction):
    record = _record(
        strategy="vwap_rejection", variant="observer", direction=direction,
        exit_policy="fixed_bracket", original_entry=100.0,
        original_stop=99.0 if direction == "LONG" else 101.0,
        original_target=102.0 if direction == "LONG" else 98.0,
    )
    assert open_campaign_position(tmp_path, record)
    # Target is also touched on the fill bar. That unsequenced touch must not
    # emit a WIN; a later, independently observed target touch must resolve it.
    fill = (
        _bar("2026-08-13T14:35:00+00:00", 102.5, 99.5)
        if direction == "LONG"
        else _bar("2026-08-13T14:35:00+00:00", 100.5, 97.5)
    )
    assert resolve_canonical_positions(
        tmp_path, instrument="MNQ", bars=[fill], current_bar_ts=fill["ts"]
    ) == []
    later = (
        _bar("2026-08-13T14:40:00+00:00", 102.5, 101.5)
        if direction == "LONG"
        else _bar("2026-08-13T14:40:00+00:00", 98.5, 97.5)
    )
    rows = resolve_canonical_positions(
        tmp_path, instrument="MNQ", bars=[later], current_bar_ts=later["ts"]
    )
    assert len(rows) == 1
    assert rows[0]["terminal_state"] == "WIN"
    assert rows[0].get("fill_bar_ambiguous") is None
    assert rows[0]["fill_bar_target_ambiguous_ignored"] is True


def test_report_never_combines_variants(tmp_path):
    path = tmp_path / "campaign.jsonl"
    control = _record()
    modified = _record(
        variant="modified", source_timeframe="5m", event_id=control["event_id"],
        entry_policy="confirmed_5m_close",
    )
    path.write_text("\n".join(json.dumps({"observed_at": "2026-08-13T15:00:00+00:00", **r}) for r in (control, modified)) + "\n")
    report = build_report(path)
    assert report["candidate_rows"] == 2
    assert {(p["strategy"], p["variant"], p["candidates"]) for p in report["populations"]} == {
        ("vwap_hold", "control", 1), ("vwap_hold", "modified", 1),
    }
    assert all(p["review_eligible"] is False for p in report["populations"])


def _outcome(candidate, terminal_state, gross, *, fillable_state="FILLED", mfe=2.0, mae=1.0):
    cost = 1.48 + 0.50
    return {
        **candidate,
        "record_type": "OUTCOME",
        "terminal_state": terminal_state,
        "fillable_state": fillable_state,
        "gross_pnl_dollars": gross,
        "net_pnl_dollars": round(gross - cost, 2) if gross is not None else None,
        "mfe_points": mfe,
        "mae_points": mae,
        "exit_timestamp": "2026-08-13T15:00:00+00:00",
    }


def test_report_has_true_event_id_pair_taxonomy_and_duplicate_protection(tmp_path):
    rows = []

    def candidate(event, variant):
        return _record(
            event_id=event, variant=variant,
            source_timeframe="15m" if variant == "control" else "5m",
            entry_policy="canonical_resting_entry" if variant == "control" else "confirmed_5m_close",
        )

    c1, m1 = candidate("event-resolved", "control"), candidate("event-resolved", "modified")
    rows += [c1, m1, _outcome(c1, "WIN", 10.0), _outcome(m1, "WIN", 12.0)]
    rows += [candidate("event-open", "control"), candidate("event-open", "modified")]
    rows += [candidate("event-control-only", "control")]
    rows += [candidate("event-modified-only", "modified")]
    c5, m5 = candidate("event-control-resolved", "control"), candidate("event-control-resolved", "modified")
    rows += [c5, m5, _outcome(c5, "LOSS", -2.0)]
    c6, m6 = candidate("event-modified-resolved", "control"), candidate("event-modified-resolved", "modified")
    rows += [c6, m6, _outcome(m6, "WIN", 3.0)]
    rows.append(deepcopy(c1))  # exact duplicate row cannot inflate either population
    duplicate_arm = deepcopy(c1)
    duplicate_arm["candidate_id"] = "malformed-second-id-for-same-event-arm"
    rows.append(duplicate_arm)

    path = tmp_path / "campaign.jsonl"
    path.write_text("\n".join(json.dumps({"observed_at": "2026-08-13T15:00:00+00:00", **row}) for row in rows) + "\n")
    report = build_report(path)
    pair = report["matched_pairs"][0]
    assert report["duplicate_candidate_rows_ignored"] == 1
    assert pair["total_unique_event_ids"] == 6
    assert pair["paired_candidates"] == 8
    assert pair["control_only_events"] == 1
    assert pair["modified_only_events"] == 1
    assert pair["pair_complete_candidates"] == 4
    assert pair["pair_complete_resolved"] == 1
    assert pair["pair_complete_unresolved"] == 3
    assert pair["pairing_rate"] == pytest.approx(4 / 6, abs=0.0001)
    assert pair["duplicate_arm_candidates_ignored"] == 1
    assert pair["status_counts"] == {
        "PAIR_COMPLETE_RESOLVED": 1,
        "PAIR_COMPLETE_OPEN": 1,
        "CONTROL_ONLY": 1,
        "MODIFIED_ONLY": 1,
        "CONTROL_RESOLVED_MODIFIED_OPEN": 1,
        "MODIFIED_RESOLVED_CONTROL_OPEN": 1,
    }
    metrics = pair["resolved_pair_metrics"]
    assert metrics["modified_minus_control_delta_dollars"] == 2.0
    assert metrics["modified_better"] == 1


def test_report_recalculates_one_two_three_tick_costs_from_retained_gross(tmp_path):
    path = tmp_path / "campaign.jsonl"
    first = _record(event_id="event-win")
    second = _record(event_id="event-loss", signal_timestamp="2026-08-13T14:45:00+00:00")
    rows = [first, second, _outcome(first, "WIN", 10.0), _outcome(second, "LOSS", -2.0)]
    path.write_text("\n".join(json.dumps({"observed_at": "2026-08-13T15:00:00+00:00", **row}) for row in rows) + "\n")
    population = build_report(path)["populations"][0]
    assert population["gross_pnl_dollars"] == 8.0
    assert population["price_path_outcomes"] == {"wins": 1, "losses": 1, "breakeven": 0}
    assert population["cost_sensitivity"]["1_rt_tick"]["net_pnl_dollars"] == 4.04
    assert population["cost_sensitivity"]["2_rt_tick"]["net_pnl_dollars"] == 3.04
    assert population["cost_sensitivity"]["3_rt_tick"]["net_pnl_dollars"] == 2.04
    assert population["cost_sensitivity"]["1_rt_tick"]["economic_after_cost"] == {
        "wins": 1, "losses": 1, "breakeven": 0,
    }


def test_risk_rules_keep_only_mnq_orb_breakout_executable():
    from config.settings import load_config

    cfg = load_config("risk_rules.yaml")
    assert cfg.allowed_instruments == ["MNQ"]
    assert cfg.enabled_concepts == ["orb_breakout"]
    assert cfg.strategy_status["orb_breakout"] == "PAPER_ELIGIBLE"
    assert cfg.strategy_status["vwap_hold"] == "SHADOW_ONLY"
