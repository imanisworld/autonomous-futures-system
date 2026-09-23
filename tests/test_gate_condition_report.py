"""ops/gate_condition_report.py — bucket gate-free observation outcomes by
market condition. Evidence only; the report must never claim authority."""
from __future__ import annotations

import json

from ops import gate_condition_report as gcr


def _cand(cid, inst, cond, ts="2026-09-20T22:30:00+00:00", tick_value=0.5):
    return {
        "record_type": "CANDIDATE", "candidate_id": cid, "instrument": inst,
        "market_condition": cond, "signal_timestamp": ts, "tick_value_dollars": tick_value,
    }


def _out(cid, result, r, gross):
    return {"record_type": "OUTCOME", "candidate_id": cid, "result": result, "pnl_r": r, "gross_pnl_dollars_1_contract": gross}


def test_buckets_by_condition_and_applies_costs_only_where_proven():
    rows = [
        _cand("a", "MNQ", "RANGE_BOUND"), _out("a", "WIN", 2.0, 100.0),
        _cand("b", "MNQ", "RANGE_BOUND"), _out("b", "LOSS", -1.0, -50.0),
        _cand("c", "MNQ", "TRENDING"), _out("c", "WIN", 2.0, 100.0),
        _cand("d", "MCL", "RANGE_BOUND", tick_value=1.0), _out("d", "WIN", 2.0, 40.0),
        # unresolved / pre-epoch rows are ignored
        _cand("e", "MNQ", "RANGE_BOUND"), {"record_type": "OUTCOME", "candidate_id": "e", "result": None},
        _cand("f", "MNQ", "RANGE_BOUND", ts="2026-09-01T00:00:00+00:00"), _out("f", "WIN", 2.0, 100.0),
    ]
    rep = gcr.build_report(rows)
    mnq = rep["by_instrument"]["MNQ"]
    assert mnq["RANGE_BOUND"]["n"] == 2 and mnq["RANGE_BOUND"]["wins"] == 1 and mnq["RANGE_BOUND"]["r"] == 1.0
    # each MNQ fill nets gross - 1.48 - 1 tick ($0.50)
    assert mnq["RANGE_BOUND"]["net_usd"] == round(100.0 - 1.98 + (-50.0 - 1.98), 2)
    assert mnq["TRENDING"]["n"] == 1
    assert rep["by_instrument"]["MCL"]["RANGE_BOUND"]["net_usd"] is None  # no cost proof
    assert rep["resolved"] == 4
    assert rep["authority"] == "evidence_only"


def test_verdict_is_not_enough_data_below_threshold_and_never_authoritative():
    rows = []
    for i in range(10):
        rows += [_cand(f"c{i}", "MNQ", "RANGE_BOUND"), _out(f"c{i}", "WIN", 2.0, 100.0)]
    rep = gcr.build_report(rows)
    assert rep["verdict"]["state"] == "NOT_ENOUGH_DATA"
    rows = []
    for i in range(30):
        rows += [_cand(f"c{i}", "MNQ", "RANGE_BOUND"), _out(f"c{i}", "WIN", 2.0, 100.0)]
    rep = gcr.build_report(rows)
    assert rep["verdict"]["state"] == "RANGE_BOUND_POSITIVE"
    assert "no rule change" in gcr.format_digest(rep)


def test_main_writes_json_and_prints_digest(tmp_path, capsys):
    rows = [_cand("a", "MNQ", "RANGE_BOUND"), _out("a", "WIN", 2.0, 100.0)]
    (tmp_path / gcr.CAMPAIGN_FILE).write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    assert gcr.main(["--log-dir", str(tmp_path)]) == 0
    assert (tmp_path / "gate_condition_report_latest.json").exists()
    out = capsys.readouterr().out
    assert "Trending-only rule check" in out
    assert "not enough data yet (needs 30 finished trades while sideways)" in out


def test_digest_is_plain_english(tmp_path):
    rows = [_cand("c1", "MNQ", "RANGE_BOUND"), _out("c1", "WIN", 2.0, 100.0),
            _cand("c2", "MNQ", "TRENDING"), _out("c2", "LOSS", -1.0, -50.0)]
    text = gcr.format_digest(gcr.build_report(rows))
    assert "When trending (allowed): 1 trade, 0 won, 1 lost, -$52 after costs" in text
    assert "When sideways (blocked): 1 trade, 1 won, 0 lost, +$98 after costs" in text
    for jargon in ("n=", "R ", "W/", "RANGE_BOUND", "NOT_ENOUGH_DATA", "2026-09-16"):
        assert jargon not in text, jargon
