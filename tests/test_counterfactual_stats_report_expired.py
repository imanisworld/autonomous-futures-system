from __future__ import annotations

import pytest

from scripts.counterfactual_stats_report import build_report, validate_rows


def _base(**overrides):
    row = {
        "cohort": "A",
        "sample_half": "H1",
        "sequence": 0,
        "ts": "2026-09-01T10:00:00+00:00",
        "result": "WIN",
        "filled": True,
        "entry_filled": True,
        "pnl_dollars": 10.0,
    }
    row.update(overrides)
    return row


def test_expired_open_is_counted_but_excluded_from_terminal_performance():
    rows = [
        _base(sequence=0, result="WIN", pnl_dollars=10.0),
        _base(
            sequence=1,
            ts="2026-09-01T10:15:00+00:00",
            result="EXPIRED",
            filled=False,
            entry_filled=True,
            pnl_dollars=None,
            mae_r=0.4,
            mfe_r=0.8,
        ),
        _base(
            sequence=2,
            ts="2026-09-01T10:30:00+00:00",
            result="NO_FILL",
            filled=False,
            entry_filled=False,
            pnl_dollars=None,
        ),
        _base(
            sequence=3,
            ts="2026-09-02T10:00:00+00:00",
            sample_half="H2",
            result="LOSS",
            pnl_dollars=-5.0,
        ),
    ]
    report = build_report(rows, hypothetical_costs=(0.0, 2.0))
    a = report["cohorts"]["A"]
    assert a["candidates"] == 4
    assert a["fills"] == 2
    assert a["terminal_fills"] == 2
    assert a["expired_open"] == 1
    assert a["no_fills"] == 1
    assert a["entry_filled_total"] == 3
    assert a["fill_rate_percent"] == 50.0
    assert a["entry_fill_rate_percent"] == 75.0
    assert a["gross_pnl_dollars"] == 5.0
    assert a["profit_factor"] == 2.0
    assert a["h1_expired_open"] == 1
    assert a["h2_expired_open"] == 0
    assert a["h1_pnl_dollars"] == 10.0
    assert a["h2_pnl_dollars"] == -5.0
    assert a["hypothetical_cost_sensitivity"] == {"0": 5.0, "2": 1.0}
    assert report["expired_policy"] == "count_separately_exclude_from_terminal_performance"


def test_expired_contract_fails_closed_on_ambiguous_state():
    with pytest.raises(ValueError, match="requires entry_filled=true"):
        validate_rows([
            _base(result="EXPIRED", filled=False, entry_filled=False, pnl_dollars=None)
        ])
    with pytest.raises(ValueError, match="must have filled=false"):
        validate_rows([
            _base(result="EXPIRED", filled=True, entry_filled=True, pnl_dollars=None)
        ])
    with pytest.raises(ValueError, match="must not carry pnl_dollars"):
        validate_rows([
            _base(result="EXPIRED", filled=False, entry_filled=True, pnl_dollars=1.0)
        ])


def test_legacy_rows_without_result_remain_supported():
    report = build_report([
        {
            "cohort": "A",
            "sample_half": "H1",
            "sequence": 0,
            "ts": "2026-09-01T10:00:00+00:00",
            "filled": True,
            "pnl_dollars": 2.0,
        },
        {
            "cohort": "A",
            "sample_half": "H2",
            "sequence": 1,
            "ts": "2026-09-02T10:00:00+00:00",
            "filled": False,
            "pnl_dollars": None,
        },
    ])
    a = report["cohorts"]["A"]
    assert a["fills"] == 1
    assert a["expired_open"] == 0
    assert a["no_fills"] == 1
