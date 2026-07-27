import json
from pathlib import Path

from research.inverted_lane_b_paper_candidate import (
    OLD_END,
    OLD_ROOT,
    OOS_ROOT,
    _inverse_rows,
    _read_sessions,
)


REPO = Path(__file__).resolve().parents[1]


def test_untouched_extension_cannot_change_old_population():
    old_rows, _ = _inverse_rows(_read_sessions([OLD_ROOT]))
    combined_rows, _ = _inverse_rows(_read_sessions([OLD_ROOT, OOS_ROOT]))
    assert [row for row in combined_rows if row.day <= OLD_END] == old_rows
    assert len(old_rows) == 490
    assert len([row for row in combined_rows if row.day > OLD_END]) == 19


def test_committed_validation_reproduces_frozen_result_and_oos_is_separate():
    results = json.loads(
        (REPO / "scripts/inverted_lane_b_paper_candidate_results.json").read_text()
    )
    assert results["reproduction"]["passed"] is True
    assert results["samples"]["old"]["overall"]["net_pnl"] == 3643.3
    assert results["samples"]["untouched_oos"]["overall"]["net_pnl"] == 645.38
    assert results["samples"]["combined"]["overall"]["net_pnl"] == 4288.68
    assert results["data"]["untouched_oos"]["vendor_overlap_ohlcv_mismatches"] == 0


def test_validation_module_stays_research_only():
    source = (
        REPO / "research" / "inverted_lane_b_paper_candidate.py"
    ).read_text()
    for forbidden in (
        "webhook.runner",
        "execution.paper_broker",
        "risk.risk_engine",
        "config.settings",
    ):
        assert forbidden not in source
