from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.mnq_missed_opportunity_producer as core
import scripts.mnq_missed_opportunity_repro as repro
from scripts.counterfactual_stats_report import build_report, validate_rows


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


def test_repro_emits_expired_open_and_report_excludes_it_from_terminal_stats(monkeypatch):
    records = [
        _record("2026-09-01T14:00:00+00:00"),
        _record("2026-09-02T14:00:00+00:00"),
    ]

    def fake_resolve(_candidate, signal_ts, **_kwargs):
        if signal_ts.startswith("2026-09-01"):
            return {
                "result": "EXPIRED",
                "exit_reason": "OBSERVATION_DATE_ROLLED",
                "bars_seen": 3,
                "pnl_r": None,
                "pnl_dollars": None,
                "mae_r": 0.9,
                "mfe_r": 1.4,
            }
        return {
            "result": "WIN",
            "exit_reason": "TARGET_HIT",
            "bars_seen": 2,
            "pnl_r": 1.5,
            "pnl_dollars": 10.0,
            "mae_r": 0.2,
            "mfe_r": 1.5,
        }

    monkeypatch.setattr(core, "resolve_ioc", fake_resolve)
    rows, summary = repro.produce_rows(records, bars={}, bar_timestamps=())
    a_rows = [row for row in rows if row["cohort"] == "A"]
    assert [row["result"] for row in a_rows] == ["EXPIRED", "WIN"]
    assert a_rows[0]["filled"] is False
    assert a_rows[0]["entry_filled"] is True
    assert a_rows[0]["pnl_dollars"] is None
    assert a_rows[0]["sample_half"] == "H1"
    assert a_rows[1]["sample_half"] == "H2"
    assert summary["A"]["expired_open"] == 1
    assert summary["A"]["terminal"] == 1
    assert summary["A"]["entry_filled_total"] == 2

    report = build_report(rows)
    assert report["expired_policy"] == "count_separately_exclude_from_terminal_performance"
    a = report["cohorts"]["A"]
    assert a["candidates"] == 2
    assert a["fills"] == 1
    assert a["terminal_fills"] == 1
    assert a["expired_open"] == 1
    assert a["entry_filled_total"] == 2
    assert a["no_fills"] == 0
    assert a["gross_pnl_dollars"] == 10.0
    assert a["mean_mae_r"] == 0.2
    assert a["mean_mfe_r"] == 1.5
    assert a["h1_expired_open"] == 1
    assert a["h2_expired_open"] == 0


def test_reporter_fails_closed_on_inconsistent_expired_state():
    with pytest.raises(ValueError, match="EXPIRED row requires entry_filled=true"):
        validate_rows(
            [
                {
                    "cohort": "A",
                    "sample_half": "H1",
                    "sequence": 0,
                    "ts": "2026-09-01T14:00:00+00:00",
                    "result": "EXPIRED",
                    "filled": False,
                    "pnl_dollars": None,
                }
            ]
        )
    with pytest.raises(ValueError, match="EXPIRED row must not carry pnl_dollars"):
        validate_rows(
            [
                {
                    "cohort": "A",
                    "sample_half": "H1",
                    "sequence": 0,
                    "ts": "2026-09-01T14:00:00+00:00",
                    "result": "EXPIRED",
                    "filled": False,
                    "entry_filled": True,
                    "pnl_dollars": 1.0,
                }
            ]
        )


def test_repro_cli_bootstraps_repo_without_pythonpath(tmp_path):
    script = Path(repro.__file__).resolve()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--data-dir" in result.stdout


def test_repro_driver_has_no_broker_or_network_submission_path():
    source = Path(repro.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "PaperBroker",
        "Tradovate",
        "tradovate_broker",
        "submit_order",
        "place_order",
        "send_order",
        "requests.",
        "httpx.",
    ):
        assert forbidden not in source
