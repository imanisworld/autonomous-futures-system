from __future__ import annotations

from pathlib import Path

import pytest

import scripts.mnq_missed_opportunity_producer as producer
from scripts.counterfactual_stats_report import build_report


def _bar(ts: str, *, open_: float, high: float, low: float, close: float = 100.0):
    return {
        "ts": ts,
        "timeframe": "15m",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def _candidate(
    *,
    strategy: str = "strat_22_reversal",
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 99.0,
    target: float = 102.0,
):
    return {
        "strategy": strategy,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def test_ioc_same_bar_stop_is_pessimistic_with_entry_and_stop_slippage():
    signal = "2026-09-01T14:00:00+00:00"
    ts = "2026-09-01T14:15:00+00:00"
    bars = {ts: _bar(ts, open_=100.0, high=102.5, low=98.5)}
    outcome = producer.resolve_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=tuple(bars)
    )
    assert outcome["result"] == "LOSS"
    assert outcome["exit_reason"] == "STOP_HIT_ON_FILL_BAR"
    assert outcome["fill_price"] == 100.25
    assert outcome["exit_price"] == 98.75
    assert outcome["pnl_dollars"] == -3.0


def test_ioc_gap_beyond_eight_points_is_no_fill():
    signal = "2026-09-01T14:00:00+00:00"
    ts = "2026-09-01T14:15:00+00:00"
    bars = {ts: _bar(ts, open_=109.0, high=110.0, low=99.0)}
    outcome = producer.resolve_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=tuple(bars)
    )
    assert outcome["result"] == "NO_FILL"
    assert outcome["exit_reason"] == "IOC_GAP_BEYOND_TOLERANCE"
    assert outcome["pnl_dollars"] is None


def test_ioc_clean_target_keeps_target_unslipped():
    signal = "2026-09-01T14:00:00+00:00"
    fill_ts = "2026-09-01T14:15:00+00:00"
    exit_ts = "2026-09-01T14:30:00+00:00"
    bars = {
        fill_ts: _bar(fill_ts, open_=100.0, high=101.0, low=99.5),
        exit_ts: _bar(exit_ts, open_=101.0, high=102.2, low=100.5),
    }
    outcome = producer.resolve_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=tuple(sorted(bars))
    )
    assert outcome["result"] == "WIN"
    assert outcome["fill_price"] == 100.25
    assert outcome["exit_price"] == 102.0
    assert outcome["pnl_dollars"] == 3.5


def test_build_records_dedupes_shadow_candidate_by_archived_identity(monkeypatch):
    ts1 = "2026-09-01T14:00:00+00:00"
    ts2 = "2026-09-01T14:15:00+00:00"
    bars = {
        ts1: _bar(ts1, open_=100.0, high=101.0, low=99.0),
        ts2: _bar(ts2, open_=100.5, high=101.5, low=100.0),
    }
    candidate = _candidate()
    journals = []
    for ts in (ts1, ts2):
        journals.append(
            {
                "instrument": "MNQ",
                "decision": "NO_TRADE",
                "regime": None,
                "context": {
                    "timestamp": ts,
                    "timeframe": "15m",
                    "market_condition": "RANGE_BOUND",
                    "structural_market_condition": "STRUCTURAL_TREND_UP",
                    "structural_direction": "UP",
                    "trend": {"direction": "UP", "strength": "STRONG"},
                },
                "shadow_candidates": [dict(candidate)],
            }
        )
    inputs = producer.StudyInputs(
        bars=bars,
        bar_timestamps=tuple(sorted(bars)),
        journal_rows=tuple(journals),
        bar_files=tuple(),
        journal_files=tuple(),
        journal_parse_skips=0,
    )
    monkeypatch.setattr(producer, "regime_for", lambda _ctx, _market: "FULL_LONG")
    records, summary = producer.build_records(inputs)
    assert len(records) == 2
    assert len(records[0]["candidates"]) == 1
    assert records[1]["candidates"] == []
    assert summary["distinct_shadow_candidates"] == 1


def _record(
    ts: str,
    *,
    pine: str,
    struct: str,
    sdir: str | None,
    ema_dir: str,
    regime_pine: str,
    regime_struct: str,
    bar_cohort: str,
):
    return {
        "ts": ts,
        "session": "ny",
        "pine": pine,
        "struct": struct,
        "sdir": sdir,
        "bar_cohort": bar_cohort,
        "ema_dir": ema_dir,
        "ema_str": "STRONG",
        "regime_pine": regime_pine,
        "regime_struct": regime_struct,
        "regime_nolabel": regime_struct,
        "reason": "test",
        "candidates": [_candidate()],
    }


def test_layer_variants_emit_strict_reporter_contract(monkeypatch):
    records = [
        _record(
            "2026-09-01T14:00:00+00:00",
            pine="RANGE_BOUND",
            struct="STRUCTURAL_TREND_UP",
            sdir="UP",
            ema_dir="UP",
            regime_pine="NOT_EVALUATED(label)",
            regime_struct="FULL_LONG",
            bar_cohort="A",
        ),
        _record(
            "2026-09-02T14:00:00+00:00",
            pine="TRENDING",
            struct="STRUCTURAL_RANGE",
            sdir=None,
            ema_dir="UP",
            regime_pine="FULL_LONG",
            regime_struct="n/a",
            bar_cohort="B",
        ),
        _record(
            "2026-09-03T14:00:00+00:00",
            pine="TRENDING",
            struct="STRUCTURAL_TREND_UP",
            sdir="UP",
            ema_dir="UP",
            regime_pine="FULL_LONG",
            regime_struct="FULL_LONG",
            bar_cohort="C",
        ),
        _record(
            "2026-09-04T14:00:00+00:00",
            pine="RANGE_BOUND",
            struct="STRUCTURAL_RANGE",
            sdir=None,
            ema_dir="UP",
            regime_pine="NOT_EVALUATED(label)",
            regime_struct="n/a",
            bar_cohort="D",
        ),
    ]

    def fake_resolve(_candidate, signal_ts, **_kwargs):
        return {
            "result": "WIN",
            "exit_reason": "TARGET_HIT",
            "bars_seen": 2,
            "pnl_r": 1.5,
            "pnl_dollars": 10.0,
            "mae_r": 0.5,
            "mfe_r": 1.5,
        }

    monkeypatch.setattr(producer, "resolve_ioc", fake_resolve)
    rows, summary = producer.produce_rows(records, bars={}, bar_timestamps=())

    counts = {cohort: 0 for cohort in producer.VARIANT_DESCRIPTIONS}
    for row in rows:
        counts[row["cohort"]] += 1
    assert counts == {"A": 2, "B": 2, "C": 2, "D1": 2, "D2": 4, "D0": 1}
    assert summary["A"]["sample_split_mid_day"] == "2026-09-03"
    assert [row["sequence"] for row in rows if row["cohort"] == "A"] == [0, 1]
    assert [row["sample_half"] for row in rows if row["cohort"] == "A"] == ["H1", "H2"]

    # Direct proof that legacy producer rows still satisfy the reporter contract.
    report = build_report(rows)
    assert report["cohorts"]["A"]["fills"] == 2
    assert report["cohorts"]["A"]["half_coverage_complete"] is True
    assert report["cohorts"]["D0"]["half_coverage_complete"] is False


def test_no_fill_rows_keep_null_pnl_and_still_receive_half_and_sequence(monkeypatch):
    records = [
        _record(
            "2026-09-01T14:00:00+00:00",
            pine="TRENDING",
            struct="STRUCTURAL_TREND_UP",
            sdir="UP",
            ema_dir="UP",
            regime_pine="FULL_LONG",
            regime_struct="FULL_LONG",
            bar_cohort="C",
        ),
        _record(
            "2026-09-02T14:00:00+00:00",
            pine="TRENDING",
            struct="STRUCTURAL_TREND_UP",
            sdir="UP",
            ema_dir="UP",
            regime_pine="FULL_LONG",
            regime_struct="FULL_LONG",
            bar_cohort="C",
        ),
    ]

    def fake_resolve(_candidate, signal_ts, **_kwargs):
        if signal_ts.startswith("2026-09-01"):
            return {
                "result": "NO_FILL",
                "exit_reason": "NEVER_TOUCHED",
                "bars_seen": 1,
                "pnl_r": None,
                "pnl_dollars": None,
                "mae_r": None,
                "mfe_r": None,
            }
        return {
            "result": "WIN",
            "exit_reason": "TARGET_HIT",
            "bars_seen": 2,
            "pnl_r": 1.0,
            "pnl_dollars": 5.0,
            "mae_r": 0.2,
            "mfe_r": 1.1,
        }

    monkeypatch.setattr(producer, "resolve_ioc", fake_resolve)
    rows, _ = producer.produce_rows(records, bars={}, bar_timestamps=())
    a_rows = [row for row in rows if row["cohort"] == "A"]
    assert a_rows[0]["filled"] is False
    assert a_rows[0]["pnl_dollars"] is None
    assert a_rows[0]["sequence"] == 0
    assert a_rows[0]["sample_half"] == "H1"
    build_report(rows)  # legacy contract validation must pass


def test_legacy_producer_still_fails_closed_on_expired_candidate(monkeypatch):
    records = [
        _record(
            "2026-09-01T14:00:00+00:00",
            pine="TRENDING",
            struct="STRUCTURAL_TREND_UP",
            sdir="UP",
            ema_dir="UP",
            regime_pine="FULL_LONG",
            regime_struct="FULL_LONG",
            bar_cohort="C",
        )
    ]
    monkeypatch.setattr(
        producer,
        "resolve_ioc",
        lambda *_args, **_kwargs: {
            "result": "EXPIRED",
            "exit_reason": "OBSERVATION_DATE_ROLLED",
            "bars_seen": 1,
            "pnl_r": None,
            "pnl_dollars": None,
            "mae_r": 0.2,
            "mfe_r": 0.4,
        },
    )
    with pytest.raises(producer.StudyError, match="EXPIRED"):
        producer.produce_rows(records, bars={}, bar_timestamps=())


def test_producer_has_no_order_submission_or_network_path():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "mnq_missed_opportunity_producer.py"
    ).read_text(encoding="utf-8")
    forbidden = (
        "PaperBroker",
        "Tradovate",
        "tradovate_broker",
        "webhook.runner",
        "submit_order",
        "place_order",
        "send_order",
        "requests.",
        "httpx.",
    )
    for token in forbidden:
        assert token not in source
