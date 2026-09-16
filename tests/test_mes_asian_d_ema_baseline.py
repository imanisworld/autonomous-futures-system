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


def _record(
    ts="2026-09-01T23:00:00+00:00",
    *,
    session="asian",
    cohort="D",
    ema="UP",
    candidates=None,
):
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


def test_canonical_ioc_uses_decision_close_as_arrival_and_real_paperbroker():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 125.0, 90.0, 100.0),
        "2026-09-01T23:15:00+00:00": _bar(
            "2026-09-01T23:15:00+00:00", 100.0, 102.0, 99.0, 101.0
        ),
        "2026-09-01T23:30:00+00:00": _bar(
            "2026-09-01T23:30:00+00:00", 101.0, 111.0, 100.0, 110.0
        ),
    }
    out = mes.resolve_canonical_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=sorted(bars)
    )
    assert out["result"] == "WIN"
    assert out["decision_close"] == 100.0
    assert out["entry_price"] == 100.25
    assert out["exit_price"] == 110.0
    assert out["bars_seen"] == 2
    assert out["pnl_dollars"] == pytest.approx(48.75)
    assert out["exit_ts"] == "2026-09-01T23:30:00+00:00"


def test_canonical_ioc_no_fill_is_decided_at_decision_close_not_future_touch():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 105.0, 98.0, 104.25),
        "2026-09-01T23:15:00+00:00": _bar(
            "2026-09-01T23:15:00+00:00", 100.0, 101.0, 99.0, 100.0
        ),
    }
    out = mes.resolve_canonical_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=sorted(bars)
    )
    assert out["result"] == "NO_FILL"
    assert out["exit_reason"] == "ENTRY_NOT_FILLED"
    assert out["bars_seen"] == 0


def test_canonical_ioc_later_both_hit_is_pessimistic_stop():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 101.0, 98.0, 100.0),
        "2026-09-01T23:15:00+00:00": _bar(
            "2026-09-01T23:15:00+00:00", 100.0, 111.0, 94.0, 101.0
        ),
    }
    out = mes.resolve_canonical_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=sorted(bars)
    )
    assert out["result"] == "LOSS"
    assert out["exit_reason"] == "STOP_HIT"
    assert out["exit_price"] == 94.75
    assert out["bars_seen"] == 1


def test_pnl_r_uses_actual_post_fill_risk_like_v3_v4():
    signal = "2026-09-01T23:00:00+00:00"
    bars = {
        signal: _bar(signal, 99.0, 101.0, 98.0, 100.0),
        "2026-09-01T23:15:00+00:00": _bar(
            "2026-09-01T23:15:00+00:00", 100.0, 111.0, 99.0, 110.0
        ),
    }
    out = mes.resolve_canonical_ioc(
        _candidate(), signal, bars=bars, bar_timestamps=sorted(bars)
    )
    assert out["pnl_r"] == pytest.approx((110.0 - 100.25) / 5.25)


def test_produce_baseline_keeps_full_d_ema_population_and_exports_asian_terminal(monkeypatch):
    records = [
        _record(candidates=[_candidate("asia_win")]),
        _record(
            ts="2026-09-02T08:00:00+00:00",
            session="london",
            candidates=[_candidate("london_loss")],
        ),
        _record(
            ts="2026-09-02T14:00:00+00:00",
            session="new_york",
            candidates=[_candidate("ny_nofill")],
        ),
        _record(
            ts="2026-09-02T14:15:00+00:00",
            session="new_york",
            cohort="C",
            candidates=[_candidate("not_d")],
        ),
        _record(
            ts="2026-09-02T14:30:00+00:00",
            session="new_york",
            ema="DOWN",
            candidates=[_candidate("wrong_ema")],
        ),
    ]

    def fake_resolve(candidate, *_args, **_kwargs):
        name = candidate["strategy"]
        base = {
            "bars_seen": 1,
            "entry_price": 100.25,
            "decision_close": 100.0,
            "mae_r": 0.2,
            "mfe_r": 1.0,
            "exit_ts": "2026-09-02T00:00:00+00:00",
        }
        if name == "asia_win":
            return dict(
                base,
                result="WIN",
                exit_reason="TARGET_HIT",
                exit_price=110.0,
                pnl_r=1.8,
                pnl_dollars=48.75,
            )
        if name == "london_loss":
            return dict(
                base,
                result="LOSS",
                exit_reason="STOP_HIT",
                exit_price=94.75,
                pnl_r=-1.05,
                pnl_dollars=-27.5,
            )
        return dict(
            base,
            result="NO_FILL",
            exit_reason="ENTRY_NOT_FILLED",
            entry_price=100.0,
            exit_price=None,
            exit_ts=None,
            pnl_r=None,
            pnl_dollars=None,
            mae_r=None,
            mfe_r=None,
        )

    monkeypatch.setattr(mes, "resolve_canonical_ioc", fake_resolve)
    full, precursor, summary = mes.produce_baseline(
        records, bars={}, bar_timestamps=[], precursor_session="asian"
    )
    assert [row["strategy"] for row in full] == [
        "asia_win",
        "london_loss",
        "ny_nofill",
    ]
    assert [row["strategy"] for row in precursor] == ["asia_win"]
    assert precursor[0]["outcome_label"] == "WIN"
    assert precursor[0]["session"] == "asian"
    assert summary["selected_candidates"] == 3
    assert summary["terminal"] == 2
    assert summary["no_fill"] == 1
    assert summary["precursor_terminal_rows"] == 1
    assert summary["by_session"]["asian"]["WIN"] == 1
    assert summary["by_session"]["london"]["LOSS"] == 1
    assert summary["by_session"]["new_york"]["NO_FILL"] == 1


def test_expired_is_counted_but_not_emitted_to_terminal_precursor(monkeypatch):
    records = [
        _record(candidates=[_candidate("expired")]),
        _record(ts="2026-09-01T23:15:00+00:00", candidates=[_candidate("loss")]),
    ]

    def fake_resolve(candidate, *_args, **_kwargs):
        common = {
            "bars_seen": 4,
            "entry_price": 100.25,
            "decision_close": 100.0,
            "mae_r": 0.4,
            "mfe_r": 0.7,
            "exit_ts": "2026-09-02T01:00:00+00:00",
        }
        if candidate["strategy"] == "expired":
            return dict(
                common,
                result="EXPIRED",
                exit_reason="OBSERVATION_DATE_ROLLED",
                exit_price=None,
                pnl_r=None,
                pnl_dollars=None,
            )
        return dict(
            common,
            result="LOSS",
            exit_reason="STOP_HIT",
            exit_price=94.75,
            pnl_r=-1.05,
            pnl_dollars=-27.5,
        )

    monkeypatch.setattr(mes, "resolve_canonical_ioc", fake_resolve)
    full, precursor, summary = mes.produce_baseline(
        records, bars={}, bar_timestamps=[]
    )
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


def _journal_row():
    return {
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


def test_discovery_accepts_unique_mes_inputs_and_filters_date(tmp_path):
    bars = tmp_path / "bars_MES_sample.jsonl"
    bars.write_text(json.dumps(_bar("2026-09-01T23:00:00+00:00", 99, 101, 98, 100)) + "\n")
    journal = tmp_path / "journal_2026-09-01.jsonl"
    journal.write_text(json.dumps(_journal_row()) + "\n")
    (tmp_path / "journal_2026-08-31.jsonl").write_text(json.dumps(_journal_row()) + "\n")

    inputs = mes.discover_inputs(
        tmp_path,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 1),
    )
    assert len(inputs.bars) == 1
    assert len(inputs.journal_rows) == 1
    assert inputs.journal_files == (journal,)


def test_discovery_fails_closed_on_identical_duplicate_journal_rows(tmp_path):
    (tmp_path / "bars_MES_sample.jsonl").write_text(
        json.dumps(_bar("2026-09-01T23:00:00+00:00", 99, 101, 98, 100)) + "\n"
    )
    row = _journal_row()
    (tmp_path / "journal_2026-09-01.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n"
    )
    with pytest.raises(mes.core.StudyError, match="duplicate MES journal row"):
        mes.discover_inputs(
            tmp_path,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )


def test_discovery_fails_closed_on_conflicting_duplicate_journal_rows(tmp_path):
    (tmp_path / "bars_MES_sample.jsonl").write_text(
        json.dumps(_bar("2026-09-01T23:00:00+00:00", 99, 101, 98, 100)) + "\n"
    )
    row = _journal_row()
    changed = json.loads(json.dumps(row))
    changed["context"]["market_condition"] = "DEAD"
    (tmp_path / "journal_2026-09-01.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps(changed) + "\n"
    )
    with pytest.raises(mes.core.StudyError, match="duplicate MES journal row"):
        mes.discover_inputs(
            tmp_path,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )


def test_discovery_fails_closed_on_duplicate_bar_timestamp(tmp_path):
    row = _bar("2026-09-01T23:00:00+00:00", 99, 101, 98, 100)
    (tmp_path / "bars_MES_sample.jsonl").write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n"
    )
    (tmp_path / "journal_2026-09-01.jsonl").write_text(json.dumps(_journal_row()) + "\n")
    with pytest.raises(mes.core.StudyError, match="duplicate MES 15m bar timestamp"):
        mes.discover_inputs(
            tmp_path,
            start_date=date(2026, 9, 1),
            end_date=date(2026, 9, 1),
        )


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


def test_safety_boundary_allows_paperbroker_but_no_external_execution_surface():
    path = Path(mes.__file__)
    text = path.read_text()
    assert "PaperBroker" in text
    forbidden = (
        "tradovate_broker",
        "webhook.runner",
        "TradovateBroker",
        "requests",
        "httpx",
        "subprocess",
    )
    assert all(token not in text for token in forbidden)
