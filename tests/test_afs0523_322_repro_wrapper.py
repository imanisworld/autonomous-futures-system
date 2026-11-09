"""Reproduction gate for the frozen 60M 3-2-2 population.

The gate reads the committed Oct 4 ledger. It does not copy those rows and it
does not call or edit check_repro().
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from config.futures_contracts import contract_economics

ROOT = Path(__file__).resolve().parents[1]
LEDGER = (
    ROOT
    / "docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json"
)
HARNESS = ROOT / "scripts/322_trigger_timing_ab_2026_09_18.py"
EOD_DATE = "2025-01-20"


def _harness():
    spec = importlib.util.spec_from_file_location("frozen_322_harness_guard", HARNESS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _wrapper():
    from research.afs0523_322_repro_wrapper import (
        FROZEN_MODULE_SHA256,
        OCT4_LEDGER_COMMIT,
        OCT4_LEDGER_SHA256,
        REPRO_EXPECTED_COUNT,
        REPRO_END,
        REPRO_START,
        RUN_AUTHORIZED,
        SLICES,
        assemble_slice_report,
        compare_replay_to_oct4,
        run_declared_slices,
        run_window,
    )

    return {
        "FROZEN_MODULE_SHA256": FROZEN_MODULE_SHA256,
        "OCT4_LEDGER_COMMIT": OCT4_LEDGER_COMMIT,
        "OCT4_LEDGER_SHA256": OCT4_LEDGER_SHA256,
        "REPRO_EXPECTED_COUNT": REPRO_EXPECTED_COUNT,
        "REPRO_END": REPRO_END,
        "REPRO_START": REPRO_START,
        "RUN_AUTHORIZED": RUN_AUTHORIZED,
        "SLICES": SLICES,
        "assemble_slice_report": assemble_slice_report,
        "compare_replay_to_oct4": compare_replay_to_oct4,
        "run_declared_slices": run_declared_slices,
        "run_window": run_window,
    }


def test_oct4_ledger_hash_matches_the_reference_of_record():
    digest = hashlib.sha256(LEDGER.read_bytes()).hexdigest()
    wrapper = _wrapper()
    assert digest == "0d0d8d72c48b3c9dd1e7065b23a5422176a765ac7b1eb594e2833b72f5d5a9b1"
    assert wrapper["OCT4_LEDGER_SHA256"] == digest
    assert wrapper["OCT4_LEDGER_COMMIT"] == "cf2ac0724a266198629a363fde23364ef00ce721"


def test_imported_detection_and_accounting_modules_match_pinned_hashes():
    wrapper = _wrapper()
    pinned = wrapper["FROZEN_MODULE_SHA256"]
    expected = {
        "scripts/322_trigger_timing_ab_2026_09_18.py",
        "scripts/edge_decomposition_audit.py",
        "research/run_322_expanded_evidence.py",
        "research/detector_322_first_live.py",
        "research/bars_322_polygon_loader.py",
        "execution/paper_broker.py",
        "execution/day_only_exit.py",
    }
    assert set(pinned) == expected
    for rel, digest in pinned.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest


def test_wrapper_does_not_call_or_replace_check_repro():
    source = (ROOT / "research/afs0523_322_repro_wrapper.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            assert name != "check_repro"
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            assert "check_repro" not in targets
            assert "EXPECTED_PLAN" not in targets
            assert "EXPECTED_IOC" not in targets
    assert "TICK_VALUE" not in source
    assert "POINT_VALUE" not in source


def test_frozen_check_repro_pins_stay_at_the_pre_1057_totals():
    harness = _harness()
    assert harness.EXPECTED_PLAN["net"] == 2532.66
    assert harness.EXPECTED_IOC["net"] == 1859.40
    assert harness.EXPECTED_N == 34


def test_reproduction_window_parameters_match_the_frozen_harness():
    harness = _harness()
    wrapper = _wrapper()
    assert wrapper["REPRO_START"] == harness.START == date(2024, 7, 2)
    assert wrapper["REPRO_END"] == harness.END == date(2026, 6, 26)
    assert wrapper["REPRO_EXPECTED_COUNT"] == harness.EXPECTED_N == 34


def test_compare_accepts_the_oct4_ledger_and_keeps_2025_01_20_unscored():
    replay = json.loads(LEDGER.read_text(encoding="utf-8"))
    report = _wrapper()["compare_replay_to_oct4"](replay)
    assert report["ok"] is True
    assert report["rows"] == 34
    assert report["comparisons"] == 306
    assert report["outcome_records"] == 306
    assert report["comparisons"] == report["outcome_records"]
    assert report["mismatches"] == []
    eod = report["eod_bar_missing"]
    assert eod["ok"] is True
    assert eod["date"] == EOD_DATE
    assert eod["scored"] is False
    for slip in ("1tick", "2tick", "3tick"):
        for model in ("original", "honest_ioc", "prearmed"):
            cell = eod["cells"][slip][model]
            assert cell["status"] == "UNRESOLVED"
            assert cell["exit_reason"] == "EOD_BAR_MISSING"
            assert cell["exit_price"] is None
            assert cell["net"] is None
            assert cell["result"] is None


def test_compare_reports_the_exact_mismatch_and_rejects_a_scored_eod_row():
    replay = json.loads(LEDGER.read_text(encoding="utf-8"))
    replay["ledger"]["1tick"][0]["models"]["plan"]["net"] = 0.01
    report = _wrapper()["compare_replay_to_oct4"](replay)
    assert report["ok"] is False
    assert report["mismatches"] == [
        {
            "date": replay["ledger"]["1tick"][0]["date"],
            "slippage": "1tick",
            "model": "original",
            "field": "net",
            "expected": 65.52,
            "actual": 0.01,
        }
    ]

    scored = json.loads(LEDGER.read_text(encoding="utf-8"))
    for slip in ("1tick", "2tick", "3tick"):
        for row in scored["ledger"][slip]:
            if row["date"] != EOD_DATE:
                continue
            row["models"]["prearmed_touch"] = {
                "status": "RESOLVED",
                "result": "WIN",
                "exit_reason": "TARGET_HIT",
                "fill_entry": 21658.25,
                "exit_price": 21538.0,
                "net": 1.0,
            }
    scored_report = _wrapper()["compare_replay_to_oct4"](scored)
    assert scored_report["ok"] is False
    assert scored_report["eod_bar_missing"]["ok"] is False
    assert scored_report["eod_bar_missing"]["scored"] is True
    fields = {
        (item["slippage"], item["model"], item["field"])
        for item in scored_report["mismatches"]
        if item["date"] == EOD_DATE
    }
    assert ("1tick", "prearmed", "status") in fields
    assert ("1tick", "prearmed", "net") in fields
    assert ("2tick", "prearmed", "exit_reason") in fields
    assert ("3tick", "prearmed", "exit_price") in fields


def test_expected_count_is_a_wrapper_parameter_and_new_slices_are_refused(tmp_path):
    wrapper = _wrapper()
    day = date(2024, 8, 2)
    audit = tmp_path / "audit"
    research = tmp_path / "research"
    target = audit / "MNQ"
    target.mkdir(parents=True)
    (research / "MNQ").mkdir(parents=True)
    bar = {
        "timestamp": "2024-08-02T16:00:00+00:00",
        "open": 1.0,
        "high": 1.25,
        "low": 0.75,
        "close": 1.0,
        "volume": 1,
    }
    (target / "MNQ_2024-08-02.jsonl").write_text(json.dumps(bar) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="expected-count"):
        wrapper["run_window"](
            research,
            audit,
            start=day,
            end=day,
            expected_count=34,
        )

    empty = wrapper["run_window"](
        research,
        audit,
        start=day,
        end=day,
        expected_count=None,
    )
    assert empty["candidate_count"] == 0
    assert empty["accounting_instrument"] == "MNQ"
    refused = wrapper["compare_replay_to_oct4"](empty)
    assert refused["ok"] is False
    assert any(item["field"] == "row" for item in refused["mismatches"])

    assert wrapper["RUN_AUTHORIZED"] is False
    with pytest.raises(RuntimeError, match="Claude"):
        wrapper["run_declared_slices"](research, audit)


def test_declared_slices_stay_separate_and_nq_is_mnq_equivalent_dollars():
    wrapper = _wrapper()
    by_id = {item.id: item for item in wrapper["SLICES"]}
    assert set(by_id) == {
        "mnq-2019-05-to-2024-06",
        "nq-2010-06-to-2019-05",
        "combined",
    }
    mnq = by_id["mnq-2019-05-to-2024-06"]
    nq = by_id["nq-2010-06-to-2019-05"]
    assert mnq.start == date(2019, 5, 1)
    assert mnq.end == date(2024, 6, 30)
    assert mnq.price_source == "MNQ"
    assert nq.start == date(2010, 6, 1)
    assert nq.end == date(2019, 4, 30)
    assert nq.price_source == "NQ"
    assert nq.label == "best available evidence, not fully clean"
    assert nq.end < mnq.start

    report = wrapper["assemble_slice_report"](
        {"candidate_count": 2},
        {"candidate_count": 3},
    )
    nq_block = report["nq-2010-06-to-2019-05"]
    assert nq_block["dollar_basis"] == "MNQ-equivalent ($2/point)"
    assert nq_block["label"] == nq.label
    assert nq_block["result_label"] == "MNQ-equivalent research, not executable historical MNQ"
    caveat = (
        "1-3 tick slippage sensitivity tests fill-price sensitivity only, "
        "not whether fills would have happened; spread, depth and fill probability "
        "remain separate unquantified uncertainties."
    )
    assert nq_block["slippage_sensitivity"] == caveat
    assert report["combined"]["result_label"] == nq_block["result_label"]
    assert report["combined"]["slippage_sensitivity"] == caveat
    assert report["mnq-2019-05-to-2024-06"]["slippage_sensitivity"] == caveat
    assert "result_label" not in report["mnq-2019-05-to-2024-06"]
    assert report["combined"]["candidate_count"] == 5
    assert report["combined"]["retuned"] is False
    assert set(report) == set(by_id)


def test_preregistration_cites_the_trial_and_blocks_a_new_data_run():
    prereg = ROOT / "docs/prereg-322-afs0523-oos-reproduction-2026-11-09.md"
    text = prereg.read_text(encoding="utf-8")
    trial_id = "T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01"
    assert trial_id in text
    assert "0d0d8d72c48b3c9dd1e7065b23a5422176a765ac7b1eb594e2833b72f5d5a9b1" in text
    assert "2532.66" in text and "1859.40" in text
    assert "2293.64" in text and "1622.38" in text
    assert "check_repro" in text
    assert "AFS-0523" in text
    assert "Claude" in text
    assert _wrapper()["RUN_AUTHORIZED"] is False
    assert "MNQ-equivalent research, not executable historical MNQ" in text
    assert "fill-price sensitivity only" in text
    assert "306" in text
    assert "score_at_10am_open" in text
    assert "DATA_GAP" in text
    assert "roll_days=8" in text
    assert not (ROOT / "docs/research-evidence/afs0523-322-row-copy.json").exists()


def test_frozen_hashes_are_refused_at_runtime_and_the_5m_window_is_exact(tmp_path):
    from research.afs0523_322_repro_wrapper import assert_5m_window, verify_frozen_modules

    verify_frozen_modules()
    with pytest.raises(RuntimeError, match="frozen module hash mismatch"):
        verify_frozen_modules({"scripts/322_trigger_timing_ab_2026_09_18.py": "0" * 64})

    day = date(2024, 8, 2)
    audit = tmp_path / "audit"
    (audit / "MNQ").mkdir(parents=True)
    (audit / "MNQ" / "MNQ_2024-08-02.jsonl").write_text("{}\n", encoding="utf-8")
    (audit / "MNQ" / "MNQ_2024-08-01.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="declared window"):
        assert_5m_window(audit, day, day)
    assert_5m_window(audit, date(2024, 8, 1), day)


def test_gap_open_days_are_scored_at_the_10am_open_and_counted():
    from research.afs0523_322_repro_wrapper import (
        GAP_OPEN_POLICY,
        comparison_entry,
        summarize_gap_opens,
    )

    gap = {
        "gap_open": True,
        "entry_trigger": 100.0,
        "entry_price": 101.25,
    }
    touched = {
        "gap_open": False,
        "entry_trigger": 100.0,
        "entry_price": 100.0,
    }
    assert comparison_entry(gap) == 101.25
    assert comparison_entry(touched) == 100.0
    summary = summarize_gap_opens([gap, touched])
    assert summary["policy"] == GAP_OPEN_POLICY == "score_at_10am_open"
    assert summary["gap_open_count"] == 1
    assert summary["scored_count"] == 2

    def exclude_gap_opens_for_tests(rows: list[dict]) -> list[dict]:
        return [row for row in rows if not row["gap_open"]]

    assert len(exclude_gap_opens_for_tests([gap, touched])) == 1


def test_data_gap_days_are_reported_and_not_scored(tmp_path):
    day = date(2024, 8, 2)
    audit = tmp_path / "audit"
    research = tmp_path / "research"
    (audit / "MNQ").mkdir(parents=True)
    (research / "MNQ").mkdir(parents=True)
    bar = {
        "timestamp": "2024-08-02T16:00:00+00:00",
        "open": 1.0,
        "high": 1.25,
        "low": 0.75,
        "close": 1.0,
        "volume": 1,
    }
    (audit / "MNQ" / "MNQ_2024-08-02.jsonl").write_text(json.dumps(bar) + "\n", encoding="utf-8")
    result = _wrapper()["run_window"](research, audit, start=day, end=day, expected_count=None)
    assert result["candidate_count"] == 0
    assert result["data_gap_count"] >= 1
    assert result["gap_open_count"] == 0
    assert result["gap_open_policy"] == "score_at_10am_open"
    assert all(item["scored"] is False for item in result["data_gaps"])
    assert "2024-08-02" in {item["date"] for item in result["data_gaps"]}


def test_break_even_uses_one_shared_rate_on_the_oct4_ledger():
    from research.afs0523_322_repro_wrapper import aggregate_break_even

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))["ledger"]
    table = aggregate_break_even(ledger)
    assert "fill-price sensitivity only" in table["slippage_sensitivity"]
    assert "spread, depth and fill probability" in table["slippage_sensitivity"]
    prearmed = table["by_model"]["prearmed"]["3tick"]
    honest = table["by_model"]["honest_ioc"]["3tick"]
    assert prearmed["trades"] == 32
    assert honest["trades"] == 19
    assert prearmed["averaged_per_trade_rates"] is False
    assert prearmed["break_even_win_rate"] * 100 == pytest.approx(77.6, abs=0.1)
    assert honest["break_even_win_rate"] * 100 == pytest.approx(73.8, abs=0.1)
    assert prearmed["aggregate_net_expectancy"] == pytest.approx(0.0, abs=1e-9)
    assert honest["aggregate_net_expectancy"] == pytest.approx(0.0, abs=1e-9)

    per_trade = []
    for row in ledger["3tick"]:
        model = row["models"]["prearmed_touch"]
        if model.get("status") != "RESOLVED":
            continue
        win, loss = _manual_pair(row, model, 3)
        per_trade.append((-loss) / (win - loss))
    assert abs(prearmed["break_even_win_rate"] - (sum(per_trade) / len(per_trade))) > 0.01


def _manual_pair(row: dict, model: dict, ticks: int) -> tuple[float, float]:
    tick, tick_value = contract_economics("MNQ")
    slip = ticks * tick
    dollars = tick_value / tick
    entry = float(model["fill_entry"])
    stop = float(row["stop"])
    target = float(row["target"])
    commission = 1.48
    if row["direction"] == "LONG":
        win = ((target - slip) - entry) * dollars - commission
        loss = ((stop - slip) - entry) * dollars - commission
    else:
        win = (entry - (target + slip)) * dollars - commission
        loss = (entry - (stop + slip)) * dollars - commission
    return win, loss


def test_break_even_does_not_average_two_different_trades():
    from research.afs0523_322_repro_wrapper import aggregate_break_even

    def cell(fill: float, stop: float, target: float) -> dict:
        return {
            "date": "2024-08-02",
            "direction": "LONG",
            "stop": stop,
            "target": target,
            "models": {
                "plan": {"status": "RESOLVED", "fill_entry": fill},
                "ioc_close": {"status": "NO_FILL"},
                "prearmed_touch": {"status": "UNRESOLVED", "reason": "EOD_BAR_MISSING"},
            },
        }

    # Equal dollar wins of about $10 and losses of $10 and $30 after 0 ticks
    # would still differ as per-trade rates. Use prices that make the
    # aggregate rate and the mean of the two rates disagree at 1 tick.
    ledger = {
        "1tick": [
            cell(100.0, 90.0, 110.0),
            cell(100.0, 80.0, 110.0),
        ],
        "2tick": [],
        "3tick": [],
    }
    table = aggregate_break_even(ledger)
    original = table["by_model"]["original"]["1tick"]
    assert original["trades"] == 2
    assert original["break_even_win_rate"] != pytest.approx(
        sum(
            (-loss) / (win - loss)
            for win, loss in (
                _manual_pair(ledger["1tick"][0], ledger["1tick"][0]["models"]["plan"], 1),
                _manual_pair(ledger["1tick"][1], ledger["1tick"][1]["models"]["plan"], 1),
            )
        )
        / 2
    )
