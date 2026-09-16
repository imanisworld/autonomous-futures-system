from __future__ import annotations

from datetime import date
import json
from pathlib import Path

import pytest

from scripts import mes_asian_d_ema_baseline as mes


def _bar(ts, o, h, l, c):
    return {
        "instrument": "MES",
        "timeframe": "15",
        "ts": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
    }


def _candidate(strategy="demo", direction="LONG", entry=100.0, stop=95.0, target=110.0):
    return {
        "strategy": strategy,
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
    }


def _record(ts="2026-09-01T23:00:00+00:00", *, session="asian", cohort="D", ema="UP", candidates=None):
    return {
        "ts": ts,
        "session": session,
        "pine": "RANGE_BOUND" if cohort == "D" else "TRENDING",
        "struct": "STRUCTURAL_RANGE" if cohort == "D" else "STRUCTURAL_TREND_UP",
        "sdir": None if cohort == "D" else "UP",
        "bar_cohort": cohort,
        "ema_dir": ema,
        "ema_str": "STRONG",
        "regime_pine": "NOT_EVALUATED(label)",
        "regime_struct": "n/a",
        "regime_nolabel": "RESTRICTED",
        "candidates": candidates or [_candidate()],
    }


def test_mes_economics_and_ioc_tolerance_are_pinned():
    assert mes.INSTRUMENT == "MES"
    assert mes.TICK == 0.25
    assert mes.TICK_VALUE == 1.25
    assert mes.POINT_VALUE == 5.0
    assert mes.IOC_TOLERANCE_TICKS == 16.0
    assert mes.IOC_TOLERANCE_POINTS == 4.0
    assert mes.IOC_TOLERANCE_TICKS * mes.TICK == mes.IOC_TOLERANCE_POINTS


def test_d0_predicate_is_reused_from_parent_593():
    pred = mes._d0_predicate()
    long = _candidate(direction="LONG")
    short = _candidate(direction="SHORT")
    d_up = _record(cohort="D", ema="UP")
    c_up = _record(cohort="C", ema="UP")
    assert pred(d_up, long) is True
    assert pred(d_up, short) is False
    assert pred(c_up, long) is False


def test_mes_ioc_long_uses_four_point_tolerance_and_mes_dollars():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 101.0, 98.0, 100.0),
        "2026-09-01T23:15:00+00:00": _bar("2026-09-01T23:15:00+00:00", 100.0, 102.0, 99.0, 101.0),
        "2026-09-01T23:30:00+00:00": _bar("2026-09-01T23:30:00+00:00", 101.0, 111.0, 100.0, 110.0),
    }
    out = mes.resolve_ioc(_candidate(), signal, bars=bars, bar_timestamps=sorted(bars))
    assert out["result"] == "WIN"
    assert out["fill_price"] == 100.25
    assert out["exit_price"] == 110.0
    assert out["pnl_dollars"] == pytest.approx((110.0 - 100.25) * 5.0)


def test_mes_ioc_rejects_gap_beyond_four_points():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 101.0, 98.0, 100.0),
        "2026-09-01T23:15:00+00:00": _bar("2026-09-01T23:15:00+00:00", 104.25, 105.0, 99.0, 104.5),
    }
    out = mes.resolve_ioc(_candidate(), signal, bars=bars, bar_timestamps=sorted(bars))
    assert out["result"] == "NO_FILL"
    assert out["exit_reason"] == "IOC_GAP_BEYOND_TOLERANCE"


def test_same_fill_bar_stop_is_pessimistic_even_if_target_also_hit():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 101.0, 98.0, 100.0),
        "2026-09-01T23:15:00+00:00": _bar("2026-09-01T23:15:00+00:00", 100.0, 111.0, 94.0, 101.0),
    }
    out = mes.resolve_ioc(_candidate(), signal, bars=bars, bar_timestamps=sorted(bars))
    assert out["result"] == "LOSS"
    assert out["exit_reason"] == "STOP_HIT_ON_FILL_BAR"
    assert out["exit_price"] == 94.75


def test_produce_baseline_keeps_only_asian_d_ema_and_terminal_precursor(monkeypatch):
    records = [
        _record(candidates=[_candidate("win")]),
        _record(ts="2026-09-01T23:15:00+00:00", candidates=[_candidate("nofill")]),
        _record(ts="2026-09-01T23:30:00+00:00", session="london", candidates=[_candidate("london")]),
        _record(ts="2026-09-01T23:45:00+00:00", cohort="C", candidates=[_candidate("not_d")]),
        _record(ts="2026-09-02T00:00:00+00:00", ema="DOWN", candidates=[_candidate("wrong_ema")]),
    ]

    def fake_resolve(candidate, *_args, **_kwargs):
        if candidate["strategy"] == "win":
            return {
                "result": "WIN",
                "exit_reason": "TARGET_HIT",
                "bars_seen": 2,
                "pnl_r": 1.9,
                "pnl_dollars": 48.75,
                "mae_r": 0.1,
                "mfe_r": 2.0,
            }
        return {
            "result": "NO_FILL",
            "exit_reason": "NEVER_TOUCHED",
            "bars_seen": 3,
            "pnl_r": None,
            "pnl_dollars": None,
            "mae_r": None,
            "mfe_r": None,
        }

    monkeypatch.setattr(mes, "resolve_ioc", fake_resolve)
    full, precursor, summary = mes.produce_baseline(records, bars={}, bar_timestamps=[])
    assert [row["strategy"] for row in full] == ["win", "nofill"]
    assert len(precursor) == 1
    assert precursor[0]["outcome_label"] == "WIN"
    assert precursor[0]["instrument"] == "MES"
    assert precursor[0]["session"] == "asian"
    assert precursor[0]["source_variant"] == "D0_D_EMA"
    assert summary == {
        "selected_candidates": 2,
        "terminal": 1,
        "wins": 1,
        "losses": 0,
        "no_fill": 1,
        "expired_open": 0,
        "entry_filled_total": 1,
        "precursor_rows": 1,
    }


def test_expired_is_counted_but_not_emitted_to_precursor(monkeypatch):
    records = [
        _record(candidates=[_candidate("expired")]),
        _record(ts="2026-09-01T23:15:00+00:00", candidates=[_candidate("loss")]),
    ]

    def fake_resolve(candidate, *_args, **_kwargs):
        if candidate["strategy"] == "expired":
            return {
                "result": "EXPIRED",
                "exit_reason": "OBSERVATION_DATE_ROLLED",
                "bars_seen": 4,
                "pnl_r": None,
                "pnl_dollars": None,
                "mae_r": 0.4,
                "mfe_r": 0.7,
            }
        return {
            "result": "LOSS",
            "exit_reason": "STOP_HIT",
            "bars_seen": 2,
            "pnl_r": -1.05,
            "pnl_dollars": -26.25,
            "mae_r": 1.0,
            "mfe_r": 0.2,
        }

    monkeypatch.setattr(mes, "resolve_ioc", fake_resolve)
    full, precursor, summary = mes.produce_baseline(records, bars={}, bar_timestamps=[])
    assert [row["result"] for row in full] == ["EXPIRED", "LOSS"]
    assert [row["outcome_label"] for row in precursor] == ["LOSS"]
    assert summary["expired_open"] == 1
    assert summary["terminal"] == 1


def test_candidate_id_is_stable_and_encodes_deduped_identity():
    record = _record()
    c = _candidate()
    first = mes._candidate_id(record, c)
    second = mes._candidate_id(dict(record), dict(c))
    assert first == second
    assert first.startswith("MES-D-EMA-")


def test_discovery_uses_mes_files_and_filters_date_and_instrument(tmp_path):
    bars = tmp_path / "bars_MES_sample.jsonl"
    bars.write_text(json.dumps(_bar("2026-09-01T23:00:00+00:00", 99, 101, 98, 100)) + "\n")
    journal = tmp_path / "journal_2026-09-01.jsonl"
    row = {
        "instrument": "MES",
        "decision": "NO_TRADE",
        "context": {
            "timeframe": "15",
            "timestamp": "2026-09-01T23:00:00+00:00",
            "market_condition": "RANGE_BOUND",
            "structural_market_condition": "STRUCTURAL_RANGE",
            "trend": {"direction": "UP", "strength": "STRONG"},
            "session": "asian",
            "shadow_candidates": [_candidate()],
        },
    }
    other = dict(row, instrument="MNQ")
    journal.write_text(json.dumps(row) + "\n" + json.dumps(other) + "\n")
    (tmp_path / "journal_2026-08-31.jsonl").write_text(json.dumps(row) + "\n")

    inputs = mes.discover_inputs(
        tmp_path,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )
    assert len(inputs.bars) == 1
    assert len(inputs.journal_rows) == 1
    assert inputs.journal_files == (journal,)


def test_discovery_fails_closed_on_wrong_instrument_inside_mes_bar_file(tmp_path):
    bad = _bar("2026-09-01T23:00:00+00:00", 99, 101, 98, 100)
    bad["instrument"] = "MNQ"
    (tmp_path / "bars_MES_bad.jsonl").write_text(json.dumps(bad) + "\n")
    (tmp_path / "journal_2026-09-01.jsonl").write_text("{}\n")
    with pytest.raises(mes.core.StudyError, match="MES bar archive contains"):
        mes.discover_inputs(
            tmp_path,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )


def test_no_broker_or_runtime_submission_imports_in_portability_script():
    path = Path(mes.__file__)
    text = path.read_text()
    forbidden = (
        "paper_broker",
        "tradovate_broker",
        "webhook.runner",
        "PaperBroker",
        "TradovateBroker",
        "execute_bracket",
        "submit_order",
        "requests",
        "httpx",
    )
    assert all(token not in text for token in forbidden)
