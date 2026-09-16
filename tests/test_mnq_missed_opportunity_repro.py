from __future__ import annotations

from pathlib import Path

import scripts.mnq_missed_opportunity_repro as repro
from scripts.counterfactual_stats_report import build_report


def _candidate():
    return {
        "strategy": "strat_22_reversal",
        "direction": "LONG",
        "entry": 100.0,
        "stop": 99.0,
        "target": 102.0,
    }


def _record(ts: str):
    return {
        "ts": ts,
        "session": "ny",
        "pine": "RANGE_BOUND",
        "struct": "STRUCTURAL_TREND_UP",
        "sdir": "UP",
        "bar_cohort": "A",
        "ema_dir": "UP",
        "ema_str": "STRONG",
        "regime_pine": "NOT_EVALUATED(label)",
        "regime_struct": "FULL_LONG",
        "regime_nolabel": "FULL_LONG",
        "reason": "test",
        "candidates": [_candidate()],
    }


def test_expired_rows_are_emitted_and_excluded_from_terminal_performance(monkeypatch):
    records = [
        _record("2026-09-01T14:00:00+00:00"),
        _record("2026-09-02T14:00:00+00:00"),
        _record("2026-09-03T14:00:00+00:00"),
    ]

    def fake_resolve(_candidate, signal_ts, **_kwargs):
        if signal_ts.startswith("2026-09-02"):
            return {
                "result": "EXPIRED",
                "exit_reason": "OBSERVATION_DATE_ROLLED",
                "bars_seen": 4,
                "pnl_r": None,
                "pnl_dollars": None,
                "mae_r": 0.4,
                "mfe_r": 0.9,
            }
        if signal_ts.startswith("2026-09-01"):
            return {
                "result": "WIN",
                "exit_reason": "TARGET_HIT",
                "bars_seen": 2,
                "pnl_r": 1.0,
                "pnl_dollars": 10.0,
                "mae_r": 0.2,
                "mfe_r": 1.1,
            }
        return {
            "result": "LOSS",
            "exit_reason": "STOP_HIT",
            "bars_seen": 2,
            "pnl_r": -1.0,
            "pnl_dollars": -5.0,
            "mae_r": 1.0,
            "mfe_r": 0.3,
        }

    monkeypatch.setattr(repro.core, "resolve_ioc", fake_resolve)
    rows, summary = repro.produce_rows(records, bars={}, bar_timestamps=())
    a_rows = [row for row in rows if row["cohort"] == "A"]
    expired = next(row for row in a_rows if row["result"] == "EXPIRED")
    assert expired["filled"] is False
    assert expired["entry_filled"] is True
    assert expired["pnl_dollars"] is None
    assert expired["mae_r"] == 0.4
    assert summary["A"]["expired_open"] == 1
    assert summary["A"]["terminal"] == 2

    report = build_report(a_rows)
    stats = report["cohorts"]["A"]
    assert stats["candidates"] == 3
    assert stats["fills"] == 2
    assert stats["expired_open"] == 1
    assert stats["no_fills"] == 0
    assert stats["gross_pnl_dollars"] == 5.0
    assert stats["entry_filled_total"] == 3


def test_direct_entrypoint_bootstraps_repo_root_without_pythonpath():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "mnq_missed_opportunity_repro.py"
    ).read_text(encoding="utf-8")
    assert "sys.path.insert(0, str(_REPO_ROOT))" in source


def test_repro_driver_has_no_order_submission_or_network_path():
    source = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "mnq_missed_opportunity_repro.py"
    ).read_text(encoding="utf-8")
    for token in (
        "PaperBroker",
        "Tradovate",
        "tradovate_broker",
        "webhook.runner",
        "submit_order",
        "place_order",
        "send_order",
        "requests.",
        "httpx.",
    ):
        assert token not in source
