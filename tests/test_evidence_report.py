"""ops/evidence_report.py: read-only per-lane evidence inventory from journal fixtures."""
from __future__ import annotations

import json

from ops.evidence_report import (
    LANE_CLASS,
    build_evidence_report,
    mes_orb_reclaim_section,
    real_trade_rows,
    shadow_rows,
)


def _write_journal(tmp_path, date_str, records):
    path = tmp_path / f"journal_{date_str}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def test_real_trade_rows_pairs_decision_to_outcome_and_excludes_test_payloads(tmp_path):
    records = [
        {"decision": "TRADE", "instrument": "MES", "setup": {"strategy": "orb_reclaim"}},
        {"type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "WIN", "pnl_ticks": 20.0, "pnl_dollars": 25.0}},
        {"decision": "TRADE", "instrument": "MES", "setup": {"strategy": "vwap_hold"}},
        {"type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "VOID", "pnl_ticks": 0, "pnl_dollars": 0,
                      "exit_reason": "MANUAL_VOID_TEST_PAYLOAD"}},
    ]
    path = _write_journal(tmp_path, "2026-07-07", records)
    rows = real_trade_rows([path])
    assert len(rows) == 1
    assert rows[0]["strategy"] == "orb_reclaim"
    assert rows[0]["result"] == "WIN"


def test_real_trade_rows_falls_back_to_reason_regex_when_setup_missing(tmp_path):
    records = [
        {"decision": "TRADE", "instrument": "MNQ", "reason": "Setup qualified: orb_breakout | LONG @ 100"},
        {"type": "OUTCOME", "instrument": "MNQ",
         "outcome": {"result": "CANCELLED", "pnl_ticks": 0, "pnl_dollars": 0,
                      "exit_reason": "execution_failed:CANCELLED", "no_fill_reason": "NO_FILL_LIMIT_TOO_PASSIVE"}},
    ]
    path = _write_journal(tmp_path, "2026-07-07", records)
    rows = real_trade_rows([path])
    assert rows[0]["strategy"] == "orb_breakout"
    assert rows[0]["no_fill_reason"] == "NO_FILL_LIMIT_TOO_PASSIVE"


def test_shadow_rows_reads_shadow_outcome_entries(tmp_path):
    records = [
        {"type": "SHADOW_OUTCOME", "lane": "shadow_setups", "strategy": "orb_false_break_fade",
         "instrument": "MNQ", "shadow_outcome": {"result": "LOSS", "pnl_ticks": -30.0}},
    ]
    path = _write_journal(tmp_path, "2026-07-07", records)
    rows = shadow_rows([path])
    assert len(rows) == 1
    assert rows[0]["lane"] == "shadow_setups"
    assert rows[0]["result"] == "LOSS"


def test_mes_orb_reclaim_section_reports_zero_fills_correctly():
    real = [
        {"strategy": "orb_reclaim", "instrument": "MES", "result": "CANCELLED",
         "no_fill_reason": "NO_FILL_PRICE_MOVED_AWAY", "pnl_dollars": 0},
        {"strategy": "orb_reclaim", "instrument": "MES", "result": "CANCELLED",
         "no_fill_reason": None, "pnl_dollars": 0},
        {"strategy": "orb_reclaim", "instrument": "MNQ", "result": "WIN", "pnl_dollars": 25.0},
    ]
    section = mes_orb_reclaim_section(real)
    assert section["decisions"] == 2  # MNQ row excluded, MES-only
    assert section["fills"] == 0
    assert section["cancelled"] == 2
    assert section["live_sample_status"] == "no live evidence yet — zero fills"
    assert section["no_fill_reasons"]["NO_FILL_PRICE_MOVED_AWAY"] == 1
    assert section["no_fill_reasons"]["UNCLASSIFIED_PRE_TAXONOMY"] == 1


def test_range_reject_and_bounce_are_classified_broken_or_incomplete():
    assert LANE_CLASS["range_reject"] == "BROKEN_OR_INCOMPLETE"
    assert LANE_CLASS["range_bounce"] == "BROKEN_OR_INCOMPLETE"
    assert LANE_CLASS["range_break_close"] == "SHADOW_TRADEABLE"


def test_build_evidence_report_is_read_only_and_returns_expected_shape(tmp_path):
    _write_journal(tmp_path, "2026-07-07", [
        {"decision": "TRADE", "instrument": "MES", "setup": {"strategy": "orb_reclaim"}},
        {"type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "CANCELLED", "pnl_ticks": 0, "pnl_dollars": 0}},
    ])
    before = sorted(tmp_path.iterdir())
    report = build_evidence_report(tmp_path, box_release="abc123")
    after = sorted(tmp_path.iterdir())
    assert before == after  # no files created/modified
    assert report["box_release"] == "abc123"
    assert report["journal_files_scanned"] == 1
    assert "real_trades_by_strategy" in report
    assert "shadow_by_lane_strategy" in report
    assert "mes_orb_reclaim" in report
    assert "lane_classification" in report
