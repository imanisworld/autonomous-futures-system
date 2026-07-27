import hashlib
import json
from pathlib import Path

from research.inverted_lane_b_paper_candidate import OLD_END


REPO = Path(__file__).resolve().parents[1]
TRADES_PATH = REPO / "scripts" / "inverted_lane_b_paper_candidate_trades.jsonl"
OLD_ROWS_SHA256 = "86f1c6e5316d37e17e4dad5be64bda6b7e3b802b45f35b9d8911ee6e03be7c05"


def test_untouched_extension_cannot_change_old_population():
    lines = TRADES_PATH.read_text().splitlines()
    combined_rows = [json.loads(line) for line in lines]
    old_lines = [
        line
        for line, row in zip(lines, combined_rows)
        if row["sample"] == "old"
    ]
    old_rows = [row for row in combined_rows if row["sample"] == "old"]
    oos_rows = [
        row for row in combined_rows if row["sample"] == "untouched_oos"
    ]

    assert combined_rows == old_rows + oos_rows
    assert len(old_rows) == 490
    assert len(oos_rows) == 19
    assert max(row["day"] for row in old_rows) == OLD_END.isoformat()
    assert min(row["day"] for row in oos_rows) > OLD_END.isoformat()
    assert oos_rows[0]["prior_close"] == old_rows[-1]["raw_exit"]
    old_payload = ("\n".join(old_lines) + "\n").encode()
    assert hashlib.sha256(old_payload).hexdigest() == OLD_ROWS_SHA256


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
