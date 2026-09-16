"""Cross-instrument shadow evidence must never pool to satisfy a readiness gate.

Report buckets and readiness populations are keyed by
(lane, strategy, instrument, evidence_epoch, variant). Legacy MNQ rows keep
their existing shape (epoch/variant None) and existing thresholds are unchanged.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from ops.evidence_readiness import (
    POPULATION_KEY,
    STRATEGY_MIN_DAYS,
    STRATEGY_MIN_EXAMPLES,
    build_evidence_readiness,
)
from ops.evidence_report import SHADOW_PARTITION_KEY, _bucket_shadow, build_evidence_report


def _outcome(day, instrument, strategy, result="WIN", epoch=None, variant=None, n=0):
    row = {
        "ts": f"{day}T15:{n % 60:02d}:00+00:00",
        "type": "SHADOW_OUTCOME",
        "lane": "shadow_setups",
        "instrument": instrument,
        "strategy": strategy,
        "candidate_key": f"{instrument}|{strategy}|{day}|{n}",
        "candidate_day": str(day),
        "shadow_outcome": {"result": result, "pnl_ticks": 8.0 if result == "WIN" else -4.0},
    }
    if epoch is not None:
        row["evidence_epoch"] = epoch
    if variant is not None:
        row["variant"] = variant
    return row


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _mixed_population_journal(tmp_path, end, per_pop=8):
    """4 populations × 8 terminal outcomes over 10+ days = 32 pooled, 8 each."""
    pops = [("M2K", "vwap_hold_observed"), ("MGC", "strat_122_pullback"),
            ("MCL", "ema_pullback_trend"), ("MBT", "strat_122_pullback")]
    for offset in range(STRATEGY_MIN_DAYS + 2):
        day = end - timedelta(days=offset)
        rows = []
        for i, (inst, strat) in enumerate(pops):
            if offset < per_pop:
                rows.append(_outcome(day, inst, strat, "WIN" if i % 2 else "LOSS", n=offset))
        _write(tmp_path / f"journal_{day}.jsonl", rows)
    return pops


def test_readiness_never_pools_instruments_to_reach_the_gate(tmp_path, config):
    end = date(2026, 9, 15)
    pops = _mixed_population_journal(tmp_path, end)
    report = build_evidence_readiness(tmp_path, days=30, through_date=end, config=config)
    track = next(t for t in report["tracks"] if t["key"] == "shadow_setups")
    assert track["resolved_terminal_examples"] == 32 >= STRATEGY_MIN_EXAMPLES  # pooled total WOULD pass
    assert track["status"] == "INSUFFICIENT SAMPLE"                              # but does not
    assert track["pooled_gate"] is False and track["partition_key"] == list(POPULATION_KEY)
    assert track["ready_populations"] == []
    assert {(p["instrument"], p["strategy"]) for p in track["populations"]} == set(pops)
    for p in track["populations"]:
        assert p["resolved_terminal_examples"] == 8 and p["status"] == "INSUFFICIENT SAMPLE"
        assert p["lane"] == "shadow_setups" and p["evidence_epoch"] is None and p["variant"] is None


def test_readiness_ready_only_when_one_population_meets_thresholds_alone(tmp_path, config):
    end = date(2026, 9, 15)
    for offset in range(STRATEGY_MIN_DAYS):
        day = end - timedelta(days=offset)
        rows = [_outcome(day, "M2K", "strat_122_pullback", "WIN" if i % 2 else "LOSS", n=i) for i in range(3)]
        rows += [_outcome(day, "MGC", "strat_122_pullback", "WIN", n=i) for i in range(2)]  # 20 total, below gate
        _write(tmp_path / f"journal_{day}.jsonl", rows)
    report = build_evidence_readiness(tmp_path, days=30, through_date=end, config=config)
    track = next(t for t in report["tracks"] if t["key"] == "shadow_setups")
    assert track["status"] == "READY FOR REVIEW"
    assert track["ready_populations"] == [
        {"lane": "shadow_setups", "strategy": "strat_122_pullback", "instrument": "M2K", "evidence_epoch": None, "variant": None}
    ]
    by_inst = {p["instrument"]: p for p in track["populations"]}
    assert by_inst["M2K"]["status"] == "READY FOR REVIEW" and by_inst["M2K"]["resolved_terminal_examples"] == 30
    assert by_inst["MGC"]["status"] == "INSUFFICIENT SAMPLE" and by_inst["MGC"]["resolved_terminal_examples"] == 20


def test_readiness_separates_epoch_and_variant_for_the_same_instrument(tmp_path, config):
    end = date(2026, 9, 15)
    for offset in range(STRATEGY_MIN_DAYS):
        day = end - timedelta(days=offset)
        rows = [_outcome(day, "MNQ", "strat_122_pullback", "WIN", epoch="e1", variant="control", n=i) for i in range(2)]
        rows += [_outcome(day, "MNQ", "strat_122_pullback", "WIN", epoch="e2", variant="control", n=10 + i) for i in range(2)]
        _write(tmp_path / f"journal_{day}.jsonl", rows)
    report = build_evidence_readiness(tmp_path, days=30, through_date=end, config=config)
    track = next(t for t in report["tracks"] if t["key"] == "shadow_setups")
    assert track["resolved_terminal_examples"] == 40 and track["status"] == "INSUFFICIENT SAMPLE"
    assert sorted(p["evidence_epoch"] for p in track["populations"]) == ["e1", "e2"]
    assert all(p["resolved_terminal_examples"] == 20 for p in track["populations"])


def test_readiness_observations_are_counted_per_population(tmp_path, config):
    today = date(2026, 9, 15)
    _write(tmp_path / f"journal_{today}.jsonl", [
        {"ts": f"{today}T14:30:00+00:00", "instrument": "MNQ", "decision": "NO_TRADE",
         "shadow_candidates": [{"strategy": "gap_fill", "direction": "SHORT", "entry": 100, "stop": 110, "target": 80}]},
        {"ts": f"{today}T14:35:00+00:00", "instrument": "M2K", "decision": "NO_TRADE",
         "shadow_candidates": [{"strategy": "gap_fill", "direction": "SHORT", "entry": 100, "stop": 110, "target": 80},
                               {"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 100, "stop": 90, "target": 120}]},
    ])
    report = build_evidence_readiness(tmp_path, through_date=today, config=config)
    track = next(t for t in report["tracks"] if t["key"] == "shadow_setups")
    assert track["status"] == "COLLECTING" and track["observations"] == 3
    obs = {(p["instrument"], p["strategy"]): p["observations"] for p in track["populations"]}
    assert obs == {("MNQ", "gap_fill"): 1, ("M2K", "gap_fill"): 1, ("M2K", "ema_pullback_trend"): 1}


def test_readiness_observation_epoch_is_read_from_the_candidate_like_the_resolver(tmp_path, config):
    today = date(2026, 9, 15)
    _write(tmp_path / f"journal_{today}.jsonl", [
        {"ts": f"{today}T14:30:00+00:00", "instrument": "M2K", "decision": "NO_TRADE", "evidence_epoch": "row-epoch",
         "shadow_candidates": [
             {"strategy": "gap_fill", "direction": "SHORT", "entry": 100, "stop": 110, "target": 80,
              "evidence_epoch": "cand-epoch", "variant": "control"},
             {"strategy": "gap_fill", "direction": "SHORT", "entry": 100, "stop": 110, "target": 80},
         ]},
    ])
    track = next(t for t in build_evidence_readiness(tmp_path, through_date=today, config=config)["tracks"]
                 if t["key"] == "shadow_setups")
    assert {(p["evidence_epoch"], p["variant"], p["observations"]) for p in track["populations"]} == {
        ("cand-epoch", "control", 1), ("row-epoch", None, 1)}


def test_report_buckets_shadow_by_instrument_epoch_and_variant():
    rows = []
    for inst in ("M2K", "MGC", "MCL", "MBT"):
        rows += [{"lane": "shadow_setups", "strategy": "strat_122_pullback", "instrument": inst,
                  "evidence_epoch": None, "variant": None, "result": "WIN", "pnl_ticks": 8.0, "date": "2026-09-15"}] * 15
    rows += [{"lane": "shadow_setups", "strategy": "strat_122_pullback", "instrument": "MNQ",
              "evidence_epoch": "e2", "variant": "modified", "result": "LOSS", "pnl_ticks": -4.0, "date": "2026-09-15"}]
    buckets = _bucket_shadow(rows)
    assert SHADOW_PARTITION_KEY == ("lane", "strategy", "instrument", "evidence_epoch", "variant")
    assert len(buckets) == 5 and all(b["instrument"] is not None for b in buckets)
    assert {b["instrument"]: b["resolved"] for b in buckets} == {"M2K": 15, "MGC": 15, "MCL": 15, "MBT": 15, "MNQ": 1}
    mnq = next(b for b in buckets if b["instrument"] == "MNQ")
    assert mnq["evidence_epoch"] == "e2" and mnq["variant"] == "modified"


def test_report_legacy_mnq_rows_keep_shape_and_expose_partition(tmp_path):
    _write(tmp_path / "journal_2026-09-15.jsonl", [
        {"type": "SHADOW_OUTCOME", "lane": "shadow_setups", "strategy": "orb_false_break_fade",
         "instrument": "MNQ", "shadow_outcome": {"result": "LOSS", "pnl_ticks": -30.0}},
    ])
    report = build_evidence_report(tmp_path)
    assert report["shadow_partition_key"] == list(SHADOW_PARTITION_KEY)
    [bucket] = report["shadow_by_lane_strategy"]
    assert bucket["instrument"] == "MNQ" and bucket["evidence_epoch"] is None and bucket["variant"] is None
    assert bucket["resolved"] == 1 and bucket["losses"] == 1 and bucket["net_pnl_ticks"] == -30.0


# ── end-to-end: candidate → resolver → outcome → readiness/report ─────────────

def _e2e_candidate_row(ts, epoch=None, variant=None, instrument="MNQ", strategy="vwap_hold_observed"):
    cand = {"strategy": strategy, "direction": "LONG", "entry": 100.0, "stop": 96.0, "target": 108.0}
    if epoch is not None:
        cand["evidence_epoch"] = epoch
    if variant is not None:
        cand["variant"] = variant
    return {"ts": ts, "instrument": instrument, "decision": "NO_TRADE", "shadow_candidates": [cand]}


def _e2e_resolve(tmp_path, day, rows_per_day, instrument="MNQ"):
    """Journal one candidate per population, record a target-hitting bar, resolve."""
    from datetime import datetime, timezone
    from context.bar_history import BarHistory
    from strategy.shadow_resolver import resolve_pending_shadow_outcomes
    t0, t1, t2 = (datetime(day.year, day.month, day.day, 14, m, tzinfo=timezone.utc).isoformat()
                  for m in (30, 45, 59))
    _write(tmp_path / f"journal_{day}.jsonl", [dict(r, ts=t0) for r in rows_per_day])
    hist = BarHistory(log_dir=str(tmp_path))
    hist.record(instrument, ts=t0, open=100, high=101, low=99, close=100)
    hist.record(instrument, ts=t1, open=100, high=101, low=99, close=100)    # fill bar (entry 100 traded)
    hist.record(instrument, ts=t2, open=101, high=109, low=101, close=108)   # target 108 hit, stop 96 untouched
    return resolve_pending_shadow_outcomes(log_dir=str(tmp_path), instrument=instrument,
                                           current_bar_ts=t2, for_date=day)


def test_resolver_keeps_epoch_and_variant_separate_end_to_end(tmp_path, config):
    """Same instrument/strategy/bar/geometry, two epochs and a legacy row:
    three independent outcomes, three populations, no shared gate."""
    from strategy.shadow_resolver import _candidate_key
    day = date(2026, 9, 15)
    resolved = _e2e_resolve(tmp_path, day, [
        _e2e_candidate_row(None, epoch="epoch-A", variant="control"),
        _e2e_candidate_row(None, epoch="epoch-B", variant="control"),
        _e2e_candidate_row(None),                                    # legacy: no epoch / variant
    ])
    assert len(resolved) == 3 and all(r["shadow_outcome"]["result"] == "WIN" for r in resolved)
    assert [(r["evidence_epoch"], r["variant"]) for r in resolved] == [
        ("epoch-A", "control"), ("epoch-B", "control"), (None, None)]
    keys = [r["candidate_key"] for r in resolved]
    assert len(set(keys)) == 3
    assert keys[2] == _candidate_key("shadow_setups", "MNQ", resolved[2]["candidate_bar_ts"],
                                     "vwap_hold_observed", "LONG", 100.0)          # legacy key byte-identical
    assert "|epoch=" not in keys[2] and keys[0].endswith("|epoch=epoch-A|variant=control")
    # Re-running resolves nothing new: every population is already journaled under its own key.
    from strategy.shadow_resolver import resolve_pending_shadow_outcomes
    assert resolve_pending_shadow_outcomes(log_dir=str(tmp_path), instrument="MNQ",
                                           current_bar_ts=resolved[0]["resolved_at_bar_ts"], for_date=day) == []
    # Report and readiness see three populations, one outcome each.
    report = build_evidence_report(tmp_path)
    buckets = report["shadow_by_lane_strategy"]
    assert {(b["evidence_epoch"], b["variant"], b["resolved"]) for b in buckets} == {
        (None, None, 1), ("epoch-A", "control", 1), ("epoch-B", "control", 1)}
    assert len(buckets) == 3
    track = next(t for t in build_evidence_readiness(tmp_path, through_date=day, config=config)["tracks"]
                 if t["key"] == "shadow_setups")
    assert track["resolved_examples"] == 3 and len(track["populations"]) == 3
    assert all(p["resolved_terminal_examples"] == 1 for p in track["populations"])


def test_resolver_epochs_cannot_share_the_readiness_gate_end_to_end(tmp_path, config):
    """15 days × (epoch-A + epoch-B) → 30 pooled terminal outcomes, 15 each: not READY."""
    end = date(2026, 9, 15)
    for offset in range(15):
        day = end - timedelta(days=offset)
        out = _e2e_resolve(tmp_path, day, [
            _e2e_candidate_row(None, epoch="epoch-A", variant="control"),
            _e2e_candidate_row(None, epoch="epoch-B", variant="control"),
        ])
        assert len(out) == 2
    track = next(t for t in build_evidence_readiness(tmp_path, days=30, through_date=end, config=config)["tracks"]
                 if t["key"] == "shadow_setups")
    assert track["resolved_terminal_examples"] == 30 >= STRATEGY_MIN_EXAMPLES
    assert track["status"] == "INSUFFICIENT SAMPLE" and track["ready_populations"] == []
    assert sorted(p["evidence_epoch"] for p in track["populations"]) == ["epoch-A", "epoch-B"]
    assert all(p["resolved_terminal_examples"] == 15 and p["status"] == "INSUFFICIENT SAMPLE"
               for p in track["populations"])
