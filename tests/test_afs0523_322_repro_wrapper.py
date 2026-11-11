"""Reproduction gate for the frozen 60M 3-2-2 population.

The gate reads the committed Oct 4 ledger. It does not copy those rows and it
does not call or edit check_repro().
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config.futures_contracts import contract_economics
from research.afs0523_322_repro_wrapper import (
    BAR_EVIDENCE_PATH,
    BAR_EVIDENCE_SHA256,
    DATA_MANIFEST_SHA256,
    MAR06_BAR_EVIDENCE_PATH,
    MAR06_BAR_EVIDENCE_SHA256,
    _classify_mar06,
    _fail_closed_on_ohl_disagreement,
    _load_bar_evidence,
    _load_mar06_evidence,
    _ohl_resolution,
    _refuse_changed_gate_data,
    REPRO_END,
    REPRO_START,
    TRIAL_ID,
    _tree_sha256,
    invalid_brackets_outside_data_gaps,
    partition_invalid_gap_opens,
    record_gate_review,
    OLDER_SLICE_IOC_NOTE,
    _ioc_close_allowlist,
    _write_gate_artifact,
    SLICE_SCORING_AUTHORIZED,
    _git_head_and_clean,
    OUTCOME_RECORD_COUNT,
    REPRO_EXPECTED_COUNT,
    _holiday_closed_dates,
    _run_window,
    _unscored_dates,
    compare_replay_structural,
    compare_replay_to_oct4,
    run_declared_slices,
    summarize_slice_ledger,
    run_reproduction_gate,
    run_window,
    validate_download_manifest,
)
from strategy.strat_322_first_live import advance_strat_322_first_live

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
        SUPERSEDED_TRIAL_ID,
        SLICES,
        TRIAL_ID,
        OLDER_SLICE_IOC_NOTE,
        assemble_slice_report,
        compare_replay_to_oct4,
        _run_window,
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
        "SUPERSEDED_TRIAL_ID": SUPERSEDED_TRIAL_ID,
        "SLICES": SLICES,
        "TRIAL_ID": TRIAL_ID,
        "OLDER_SLICE_IOC_NOTE": OLDER_SLICE_IOC_NOTE,
        "assemble_slice_report": assemble_slice_report,
        "compare_replay_to_oct4": compare_replay_to_oct4,
        "_run_window": _run_window,
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


def test_expected_count_is_a_wrapper_parameter_and_new_slices_are_refused(monkeypatch, tmp_path):
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

    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    with pytest.raises(RuntimeError, match="expected-count"):
        wrapper["_run_window"](
            research,
            audit,
            start=day,
            end=day,
            expected_count=34,
        )

    empty = wrapper["_run_window"](
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

    assert wrapper["RUN_AUTHORIZED"] is True
    assert wrapper["TRIAL_ID"] != wrapper["SUPERSEDED_TRIAL_ID"]
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    with pytest.raises(RuntimeError, match="slice admission requires data_root"):
        wrapper["run_declared_slices"](research, audit)


def test_slice_admission_refuses_a_temporary_ledger_with_no_gate_review(monkeypatch, tmp_path):
    """A ledger with no GATE_REVIEW line must still refuse. Deleting that check fails here."""
    source = ROOT / "docs/research-trial-ledger.jsonl"
    kept = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if json.loads(line).get("event") == "GATE_REVIEW":
            continue
        kept.append(line)
    text = "\n".join(kept) + "\n"
    assert not any(json.loads(line).get("event") == "GATE_REVIEW" for line in kept)
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(text, encoding="utf-8")
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    real_before = source.read_bytes()
    with pytest.raises(RuntimeError, match="Claude's review of the gate result is required"):
        run_declared_slices(tmp_path / "research", tmp_path / "audit")
    assert source.read_bytes() == real_before
    assert ledger.read_text(encoding="utf-8") == text


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
    assert nq.start == date(2010, 6, 6)
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
    note = wrapper["OLDER_SLICE_IOC_NOTE"]
    assert nq_block["older_slice_ioc_note"] == note
    assert report["combined"]["result_label"] == nq_block["result_label"]
    assert report["combined"]["slippage_sensitivity"] == caveat
    assert report["combined"]["older_slice_ioc_note"] == note
    assert report["mnq-2019-05-to-2024-06"]["slippage_sensitivity"] == caveat
    assert report["mnq-2019-05-to-2024-06"]["older_slice_ioc_note"] == note
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
    wrapper = _wrapper()
    assert wrapper["RUN_AUTHORIZED"] is True
    assert wrapper["TRIAL_ID"] == "T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01"
    assert wrapper["SUPERSEDED_TRIAL_ID"] == trial_id
    assert wrapper["TRIAL_ID"] != wrapper["SUPERSEDED_TRIAL_ID"]
    assert "MNQ-equivalent research, not executable historical MNQ" in text
    assert "fill-price sensitivity only" in text
    assert "306" in text
    assert "score_at_10am_open" in text
    assert "INVALID_ENTRY_BRACKET" in text
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


def _production_gap_decision(direction: str, entry: float, stop: float, target: float, trigger: float):
    day = date(2024, 8, 2)
    opened = datetime(2024, 8, 2, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    state, candidate = advance_strat_322_first_live(
        bars_5m=[{
            "ts": opened.isoformat(),
            "open": entry,
            "high": max(entry, trigger) + 1.0,
            "low": min(entry, trigger) - 1.0,
            "close": entry,
        }],
        current_bar_ts=opened,
        instrument="MNQ",
        persisted_state={
            "trading_date": day.isoformat(),
            "status": "ARMED",
            "direction": direction,
            "trigger": trigger,
            "stop": stop,
            "target": target,
        },
    )
    return candidate is not None, state.get("invalidation")


def test_invalid_gap_open_brackets_match_the_state_machine_and_do_not_break_parity():
    from research.afs0523_322_repro_wrapper import (
        entry_inside_bracket,
        partition_invalid_gap_opens,
    )

    cases = [
        ("SHORT", 85.0, 110.0, 90.0, 100.0, False),
        ("LONG", 115.0, 90.0, 110.0, 100.0, False),
        ("SHORT", 95.0, 110.0, 90.0, 100.0, True),
        ("LONG", 105.0, 90.0, 110.0, 100.0, True),
        ("SHORT", 90.0, 110.0, 90.0, 100.0, False),
        ("LONG", 110.0, 90.0, 110.0, 100.0, False),
        ("SHORT", 110.0, 110.0, 90.0, 120.0, False),
        ("LONG", 105.0, 105.0, 120.0, 100.0, False),
    ]
    for direction, entry, stop, target, trigger, accepted in cases:
        assert entry_inside_bracket(direction, entry, stop, target) is accepted
        produced, reason = _production_gap_decision(direction, entry, stop, target, trigger)
        assert produced is accepted
        if not accepted:
            assert reason == "INVALID_ENTRY_BRACKET"

    short_past = {
        "date": date(2024, 8, 2),
        "direction": "SHORT",
        "gap_open": True,
        "entry_trigger": 100.0,
        "entry_price": 85.0,
        "stop": 110.0,
        "target": 90.0,
    }
    long_past = {
        "date": date(2024, 8, 5),
        "direction": "LONG",
        "gap_open": True,
        "entry_trigger": 100.0,
        "entry_price": 115.0,
        "stop": 90.0,
        "target": 110.0,
    }
    short_inside = {
        "date": date(2024, 8, 6),
        "direction": "SHORT",
        "gap_open": True,
        "entry_trigger": 100.0,
        "entry_price": 95.0,
        "stop": 110.0,
        "target": 90.0,
    }
    kept, invalid = partition_invalid_gap_opens([short_past, long_past, short_inside])
    assert [row["date"] for row in kept] == [short_inside["date"]]
    assert [row["direction"] for row in invalid] == ["SHORT", "LONG"]
    assert all(row["invalidation"] == "INVALID_ENTRY_BRACKET" for row in invalid)
    state_count = 1
    assert len(kept) == state_count


def test_data_gap_day_is_not_also_listed_as_invalid_entry_bracket():
    gap_day = date(2024, 8, 2)
    other_day = date(2024, 8, 5)
    bad = {
        "direction": "SHORT",
        "gap_open": True,
        "entry_trigger": 100.0,
        "entry_price": 85.0,
        "stop": 110.0,
        "target": 90.0,
    }
    kept, invalid = partition_invalid_gap_opens(
        [
            {**bad, "date": gap_day},
            {**bad, "date": other_day},
        ]
    )
    reported = invalid_brackets_outside_data_gaps(invalid, {gap_day.isoformat()})
    assert kept == []
    assert [row["date"] for row in reported] == [other_day.isoformat()]
    assert len(reported) == 1


def test_strict_bracket_drops_non_gap_rows_and_keeps_a_valid_one():
    """Match strategy/strat_322_first_live.py:193-198.

    SHORT is ``target < entry_price < stop``. LONG is ``stop < entry_price < target``.
    A gap-open-only partition keeps the zero-width row and fails this test.
    """
    zero_width = {
        "date": date(2012, 6, 4),
        "direction": "LONG",
        "gap_open": False,
        "entry_trigger": 100.0,
        "entry_price": 100.0,
        "stop": 90.0,
        "target": 100.0,
    }
    inverted = {
        "date": date(2012, 6, 5),
        "direction": "SHORT",
        "gap_open": False,
        "entry_trigger": 100.0,
        "entry_price": 100.0,
        "stop": 90.0,
        "target": 110.0,
    }
    valid = {
        "date": date(2012, 6, 6),
        "direction": "LONG",
        "gap_open": False,
        "entry_trigger": 100.0,
        "entry_price": 100.0,
        "stop": 90.0,
        "target": 110.0,
    }
    kept, invalid = partition_invalid_gap_opens([zero_width, inverted, valid])
    assert [row["date"] for row in kept] == [valid["date"]]
    assert [(row["date"], row["gap_open"]) for row in invalid] == [
        ("2012-06-04", False),
        ("2012-06-05", False),
    ]
    assert all(row["invalidation"] == "INVALID_ENTRY_BRACKET" for row in invalid)
    assert zero_width["entry_price"] == zero_width["target"]
    assert zero_width["gap_open"] is False


def test_slice_report_counts_gap_open_and_non_gap_drops(monkeypatch, tmp_path):
    day = date(2012, 6, 4)
    zero_width = {
        "date": day,
        "direction": "LONG",
        "gap_open": False,
        "entry_trigger": 100.0,
        "entry_price": 100.0,
        "stop": 90.0,
        "target": 100.0,
    }
    gap_inverted = {
        "date": date(2012, 6, 5),
        "direction": "SHORT",
        "gap_open": True,
        "entry_trigger": 100.0,
        "entry_price": 85.0,
        "stop": 110.0,
        "target": 90.0,
    }
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.data_gap_dates_from_trees",
        lambda *_args, **_kwargs: {
            "data_gaps": [],
            "data_gap_count": 0,
            "expected_holiday_closures": [],
            "early_close_sessions": [],
        },
    )
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.verify_frozen_modules", lambda *_a, **_k: None)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.assert_5m_window", lambda *_a, **_k: None)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.detect_candidates",
        lambda *_args, **_kwargs: [zero_width, gap_inverted],
    )
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.load_bars", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.extract_state_machine",
        lambda *_a, **_k: [],
    )
    result = _run_window(
        tmp_path / "research",
        tmp_path / "audit",
        start=day,
        end=day,
        expected_count=None,
    )
    assert result["candidate_count"] == 0
    assert result["invalid_entry_bracket_count"] == 2
    assert result["invalid_entry_bracket_gap_open_count"] == 1
    assert result["invalid_entry_bracket_non_gap_open_count"] == 1
    assert SLICE_SCORING_AUTHORIZED is True


def test_data_gap_days_are_reported_and_not_scored(monkeypatch, tmp_path):
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
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    result = _wrapper()["_run_window"](research, audit, start=day, end=day, expected_count=None)
    assert result["candidate_count"] == 0
    assert result["data_gap_count"] >= 1
    assert result["gap_open_count"] == 0
    assert result["invalid_entry_bracket_count"] == 0
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


def _bar_file(root: Path, day: date) -> None:
    folder = root / "MNQ"
    folder.mkdir(parents=True, exist_ok=True)
    bar = {
        "timestamp": f"{day.isoformat()}T14:00:00+00:00",
        "open": 1.0,
        "high": 1.25,
        "low": 0.75,
        "close": 1.0,
        "volume": 1,
    }
    (folder / f"MNQ_{day.isoformat()}.jsonl").write_text(json.dumps(bar) + "\n", encoding="utf-8")


def _window(window_id: str, start: str, end: str, research_hash: str, audit_hash: str) -> dict:
    return {
        "id": window_id,
        "start": start,
        "end": end,
        "research_15m_sha256": research_hash,
        "audit_5m_sha256": audit_hash,
    }


def _manifest(tmp_path: Path, research: Path, audit: Path, *, slices: dict | None = None) -> dict:
    pulled = tmp_path / "pulled.dbn"
    if not pulled.exists():
        pulled.write_bytes(b"synthetic-download-record")
    slice_hashes = slices or {}
    def pair(window_id: str) -> tuple[str, str]:
        found = slice_hashes.get(window_id)
        if found is None:
            return ("ab" * 32, "cd" * 32)
        return found
    mnq = pair("mnq-2019-05-to-2024-06")
    nq = pair("nq-2010-06-to-2019-05")
    return {
        "pulled_files": [{"path": "pulled.dbn", "sha256": hashlib.sha256(pulled.read_bytes()).hexdigest()}],
        "windows": [
            _window("reproduction", "2024-07-02", "2026-06-26", _tree_sha256(research), _tree_sha256(audit)),
            _window("mnq-2019-05-to-2024-06", "2019-05-01", "2024-06-30", mnq[0], mnq[1]),
            _window("nq-2010-06-to-2019-05", "2010-06-06", "2019-04-30", nq[0], nq[1]),
        ],
        "adapter_report": {"contracts": ["MNQH5"], "missing_minutes": []},
    }


def _repro(manifest: dict) -> dict:
    return next(item for item in manifest["windows"] if item["id"] == "reproduction")


def _pin_manifest(monkeypatch, tmp_path: Path, manifest: dict) -> str:
    path = tmp_path / "databento-download-manifest.json"
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    path.write_bytes(raw)
    pin = hashlib.sha256(raw).hexdigest()
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.DATA_MANIFEST_PATH", path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.DATA_MANIFEST_SHA256", pin)
    return pin


def _call_gate(monkeypatch, data_root: Path, research: Path, audit: Path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.RUN_AUTHORIZED", True)
    return run_reproduction_gate(
        data_root,
        research.relative_to(data_root),
        audit.relative_to(data_root),
    )


def _open_trial_ledger_text() -> str:
    kept = []
    for line in (ROOT / "docs/research-trial-ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("trial_id") == TRIAL_ID and row.get("event") == "SUPERSEDED":
                continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def _patch_gate_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "reproduction-gate.json"
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(_open_trial_ledger_text(), encoding="utf-8")
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.GATE_ARTIFACT", artifact)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    return artifact, ledger


def test_reproduction_gate_refuses_when_the_flag_is_false(monkeypatch, tmp_path):
    _patch_gate_paths(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.RUN_AUTHORIZED", False)
    with pytest.raises(RuntimeError, match="RUN_AUTHORIZED"):
        run_reproduction_gate(tmp_path, Path("."), Path("."))


def test_reproduction_gate_refuses_a_day_file_outside_the_window(monkeypatch, tmp_path):
    _patch_gate_paths(monkeypatch, tmp_path)
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2020, 1, 2))
    _bar_file(audit, date(2024, 8, 2))
    _pin_manifest(monkeypatch, tmp_path, _manifest(tmp_path, research, audit))
    with pytest.raises(RuntimeError, match="outside"):
        _call_gate(monkeypatch, tmp_path, research, audit)


def test_reproduction_gate_refuses_a_checksum_or_tree_hash_mismatch(monkeypatch, tmp_path):
    _patch_gate_paths(monkeypatch, tmp_path)
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    bad_checksum = _manifest(tmp_path, research, audit)
    bad_checksum["pulled_files"][0]["sha256"] = "0" * 64
    _pin_manifest(monkeypatch, tmp_path, bad_checksum)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    bad_tree = _manifest(tmp_path, research, audit)
    _repro(bad_tree)["research_15m_sha256"] = "0" * 64
    _pin_manifest(monkeypatch, tmp_path, bad_tree)
    with pytest.raises(RuntimeError, match="tree hash mismatch"):
        _call_gate(monkeypatch, tmp_path, research, audit)


def test_reproduction_gate_refuses_a_caller_window_or_count_override(tmp_path):
    with pytest.raises(TypeError):
        run_reproduction_gate(
            tmp_path,
            tmp_path,
            {},
            start=date(2019, 5, 1),
            end=date(2024, 6, 30),
            expected_count=1,
        )


def test_reproduction_gate_refuses_a_caller_manifest_or_placeholder_pin(monkeypatch, tmp_path):
    _patch_gate_paths(monkeypatch, tmp_path)
    assert DATA_MANIFEST_SHA256 != "PLACEHOLDER"
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(TypeError):
        run_reproduction_gate(research, audit, data_manifest=_manifest(tmp_path, research, audit))
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.DATA_MANIFEST_SHA256", "PLACEHOLDER")
    with pytest.raises(RuntimeError, match="placeholder"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    manifest = _manifest(tmp_path, research, audit)
    _pin_manifest(monkeypatch, tmp_path, manifest)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.DATA_MANIFEST_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="manifest hash mismatch"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_reproduction_gate_resolves_paths_under_data_root(monkeypatch, tmp_path):
    _patch_gate_paths(monkeypatch, tmp_path)
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.RUN_AUTHORIZED", True)
    with pytest.raises(RuntimeError, match="data_root is missing"):
        run_reproduction_gate(tmp_path / "missing-root", Path("research"), Path("audit"))
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    escaped = _manifest(tmp_path, research, audit)
    escaped["pulled_files"][0]["path"] = "../pulled.dbn"
    _pin_manifest(monkeypatch, tmp_path, escaped)
    with pytest.raises(RuntimeError, match="escapes data_root"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    absolute = _manifest(tmp_path, research, audit)
    absolute["pulled_files"][0]["path"] = str(tmp_path / "pulled.dbn")
    _pin_manifest(monkeypatch, tmp_path, absolute)
    with pytest.raises(RuntimeError, match="escapes data_root"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "secret").write_bytes(b"secret")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    linked = _manifest(tmp_path, research, audit)
    linked["pulled_files"][0]["path"] = "escape/secret"
    _pin_manifest(monkeypatch, tmp_path, linked)
    with pytest.raises(RuntimeError, match="escapes data_root"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    manifest = _manifest(tmp_path, research, audit)
    _pin_manifest(monkeypatch, tmp_path, manifest)
    _patch_gate_paths(monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    result = _call_gate(monkeypatch, tmp_path, research, audit)
    assert result["pass"] is False
    assert result["data_root"] == str(tmp_path.resolve())
    assert "expected-count" in result["error"]
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_run_window_refuses_a_call_outside_the_reproduction_window(tmp_path):
    with pytest.raises(RuntimeError, match="window other than the reproduction window"):
        run_window(
            tmp_path,
            tmp_path,
            start=date(2019, 5, 1),
            end=date(2024, 6, 30),
            expected_count=None,
        )
    with pytest.raises(RuntimeError, match="run_reproduction_gate"):
        run_window(
            tmp_path,
            tmp_path,
            start=REPRO_START,
            end=REPRO_END,
            expected_count=34,
        )


def _write_gate(artifact: Path, ledger: Path, payload: dict, *, tamper: bool = False) -> None:
    text = json.dumps(payload, indent=2) + "\n"
    artifact.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    if tamper:
        artifact.write_text(text + " ", encoding="utf-8")
    line = {
        "trial_id": TRIAL_ID,
        "event": "GATE",
        "gate_pass": payload["pass"],
        "gate_artifact_sha256": digest,
        "pulled_files": payload.get("pulled_files"),
        "research_15m_sha256": payload.get("research_15m_sha256"),
        "audit_5m_sha256": payload.get("audit_5m_sha256"),
        "manifest_sha256": payload.get("manifest_sha256"),
        "prior_attempts": payload.get("prior_attempts"),
    }
    ledger.write_text(json.dumps(line) + "\n", encoding="utf-8")


def test_declared_slices_refuse_without_a_verified_pass_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", False)
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    kept = [
        line
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if not line.strip()
        or json.loads(line).get("trial_id") != TRIAL_ID
        or json.loads(line).get("event") != "GATE"
    ]
    ledger.write_text("\n".join(kept) + "\n", encoding="utf-8")
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    with pytest.raises(RuntimeError, match="Claude"):
        run_declared_slices()
    _write_gate(artifact, ledger, {"pass": False, "claude_review": None}, tamper=True)
    with pytest.raises(RuntimeError, match="hash mismatch"):
        run_declared_slices()
    _write_gate(artifact, ledger, {"pass": False, "claude_review": None})
    with pytest.raises(RuntimeError, match="did not PASS"):
        run_declared_slices()
    _write_gate(artifact, ledger, {"pass": True, "claude_review": True})
    with pytest.raises(RuntimeError, match="Claude's review"):
        run_declared_slices()
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    slice_research = tmp_path / "slice-research"
    slice_audit = tmp_path / "slice-audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    _bar_file(slice_research, date(2020, 1, 6))
    _bar_file(slice_audit, date(2020, 1, 6))
    manifest = _manifest(
        tmp_path,
        research,
        audit,
        slices={
            "mnq-2019-05-to-2024-06": (
                _tree_sha256(slice_research),
                _tree_sha256(slice_audit),
            )
        },
    )
    pin = _pin_manifest(monkeypatch, tmp_path, manifest)
    _write_gate(
        artifact,
        ledger,
        {
            "pass": True,
            "claude_review": None,
            "prior_attempts": [],
            "pulled_files": manifest["pulled_files"],
            "research_15m_sha256": _repro(manifest)["research_15m_sha256"],
            "audit_5m_sha256": _repro(manifest)["audit_5m_sha256"],
            "manifest_sha256": pin,
        },
    )
    review = {
        "trial_id": TRIAL_ID,
        "event": "GATE_REVIEW",
        "gate_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "pr_head_sha": "a" * 40,
    }
    ledger.write_text(ledger.read_text(encoding="utf-8") + json.dumps(review) + "\n", encoding="utf-8")
    assert run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    ) is None
    other = tmp_path / "other"
    _bar_file(other, date(2020, 1, 7))
    with pytest.raises(RuntimeError, match="does not match the PASS gate"):
        run_declared_slices(
            Path("other"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )


def test_reproduction_gate_records_a_count_failure_on_a_synthetic_fixture(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    day = date(2024, 8, 2)
    _bar_file(research, day)
    _bar_file(audit, day)
    manifest = _manifest(tmp_path, research, audit)
    pin = _pin_manifest(monkeypatch, tmp_path, manifest)
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    result = _call_gate(monkeypatch, tmp_path, research, audit)
    assert result["pass"] is False
    assert "expected-count" in result["error"]
    assert result["comparisons"] is None
    assert result["outcome_records"] == 306
    assert result["adapter_report"]["contracts"] == ["MNQH5"]
    assert result["data_gap_count"] >= 1
    assert any(item["date"] == day.isoformat() and item["scored"] is False for item in result["data_gaps"])
    written_path = artifact.with_name("reproduction-gate.attempt-02.json")
    assert not artifact.exists()
    written = json.loads(written_path.read_text(encoding="utf-8"))
    assert written["pass"] is False
    last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "GATE"
    assert last["gate_pass"] is False
    assert last["trial_id"] == TRIAL_ID
    assert result["scoring_profile"]["MAX_CONTRACTS_HARD_CAP"] == "1"
    assert result["scoring_profile"]["risk_rules_contract_ceiling"] == 6
    assert last["scoring_profile"] == result["scoring_profile"]
    assert written["scoring_profile"] == result["scoring_profile"]
    assert last["manifest_sha256"] == pin
    assert last["research_15m_sha256"] == _repro(manifest)["research_15m_sha256"]
    assert last["audit_5m_sha256"] == _repro(manifest)["audit_5m_sha256"]
    assert last["pulled_files"] == manifest["pulled_files"]
    assert last["replay_ledger"] is None
    assert last["replay_ledger_sha256"] is None
    assert written["replay_ledger"] is None
    assert written["replay_ledger_sha256"] is None
    assert not artifact.with_name("reproduction-replay-ledger.json").exists()
    assert not written_path.with_name("reproduction-replay-ledger.attempt-02.json").exists()
    assert result["manifest_sha256"] == pin
    assert result["research_15m_sha256"] == _repro(manifest)["research_15m_sha256"]
    assert result["audit_5m_sha256"] == _repro(manifest)["audit_5m_sha256"]
    assert result["pulled_files"] == manifest["pulled_files"]
    assert len(result["prior_attempts"]) == 1
    assert result["prior_attempts"][0]["gate_pass"] is True
    assert result["prior_attempts"][0]["gate_artifact_sha256"] == (
        "41d76ab1ef549316eebe58b1c8f5878184f43a181bb2164802805974f6f54da8"
    )
    assert written["manifest_sha256"] == pin
    again = _call_gate(monkeypatch, tmp_path, research, audit)
    assert again["pass"] is False
    assert len(again["prior_attempts"]) == 2
    assert again["prior_attempts"][1]["manifest_sha256"] == pin
    assert again["prior_attempts"][1]["research_15m_sha256"] == _repro(manifest)["research_15m_sha256"]
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_slice_admission_refuses_a_pass_after_different_data(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    manifest = _manifest(tmp_path, research, audit)
    pin = _pin_manifest(monkeypatch, tmp_path, manifest)
    earlier = {
        "recorded_at": "2026-11-09T15:00:00Z",
        "gate_pass": False,
        "pulled_files": [{"path": "other", "sha256": "a" * 64}],
        "research_15m_sha256": "b" * 64,
        "audit_5m_sha256": "c" * 64,
        "manifest_sha256": "d" * 64,
        "gate_artifact_sha256": "e" * 64,
    }
    passing = {
        "pass": True,
        "claude_review": "Claude reviewed the committed gate artifact.",
        "prior_attempts": [earlier],
        "pulled_files": manifest["pulled_files"],
        "research_15m_sha256": _repro(manifest)["research_15m_sha256"],
        "audit_5m_sha256": _repro(manifest)["audit_5m_sha256"],
        "manifest_sha256": pin,
    }
    text = json.dumps(passing, indent=2) + "\n"
    artifact.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    fail_line = {"trial_id": TRIAL_ID, "event": "GATE", **earlier}
    pass_line = {
        "trial_id": TRIAL_ID,
        "event": "GATE",
        "gate_pass": True,
        "gate_artifact_sha256": digest,
        "pulled_files": passing["pulled_files"],
        "research_15m_sha256": passing["research_15m_sha256"],
        "audit_5m_sha256": passing["audit_5m_sha256"],
        "manifest_sha256": pin,
        "prior_attempts": [earlier],
    }
    ledger.write_text(json.dumps(fail_line) + "\n" + json.dumps(pass_line) + "\n", encoding="utf-8")
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="different data") as caught:
        run_declared_slices(research, audit)
    assert "b" * 64 in str(caught.value)
    hidden = dict(passing)
    hidden["prior_attempts"] = []
    _write_gate(artifact, ledger, hidden)
    ledger.write_text(
        json.dumps(fail_line) + "\n" + ledger.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="does not report every prior GATE attempt"):
        run_declared_slices(research, audit)
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_scoring_exception_is_recorded_as_fail(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    manifest = _manifest(tmp_path, research, audit)
    pin = _pin_manifest(monkeypatch, tmp_path, manifest)
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()

    def boom(*_args, **_kwargs):
        raise ValueError("not a runtime error")

    monkeypatch.setattr("research.afs0523_322_repro_wrapper._run_window", boom)
    result = _call_gate(monkeypatch, tmp_path, research, audit)
    assert result["pass"] is False
    assert "not a runtime error" in result["error"]
    assert result["manifest_sha256"] == pin
    last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "GATE"
    assert last["gate_pass"] is False
    assert last["manifest_sha256"] == pin
    written_path = artifact.with_name("reproduction-gate.attempt-02.json")
    assert not artifact.exists()
    written = json.loads(written_path.read_text(encoding="utf-8"))
    assert written["pass"] is False
    assert written["replay_ledger"] is None
    assert written["replay_ledger_sha256"] is None
    assert last["replay_ledger"] is None
    assert last["replay_ledger_sha256"] is None
    assert not artifact.with_name("reproduction-replay-ledger.json").exists()
    assert not written_path.with_name("reproduction-replay-ledger.attempt-02.json").exists()
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def _fake_scored_window(replay: dict):
    def fake_window(*_args, **_kwargs):
        return {
            "ledger": replay,
            "data_gaps": [],
            "data_gap_count": 0,
            "invalid_entry_brackets": [],
            "invalid_entry_bracket_count": 0,
            "gap_open_count": 0,
        }

    return fake_window


def test_failed_comparison_saves_each_attempt_replay_ledger(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    _pin_manifest(monkeypatch, tmp_path, _manifest(tmp_path, research, audit))
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    replay = {
        "1tick": [{"date": "2026-03-06", "trigger_bar_close": 1.0}],
        "2tick": [],
        "3tick": [],
    }
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        _fake_scored_window(replay),
    )
    result = _call_gate(monkeypatch, tmp_path, research, audit)
    assert result["pass"] is False
    saved = artifact.with_name("reproduction-replay-ledger.attempt-02.json")
    raw = saved.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert json.loads(raw) == {"ledger": replay}
    assert result["replay_ledger"] == str(saved)
    assert result["replay_ledger_sha256"] == digest
    written = json.loads(artifact.with_name("reproduction-gate.attempt-02.json").read_text(encoding="utf-8"))
    assert written["replay_ledger_sha256"] == digest
    assert written["pass"] is False
    last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "GATE"
    assert last["replay_ledger"] == str(saved)
    assert last["replay_ledger_sha256"] == digest
    assert hashlib.sha256(raw + b" ").hexdigest() != digest
    again = _call_gate(monkeypatch, tmp_path, research, audit)
    second = artifact.with_name("reproduction-replay-ledger.attempt-03.json")
    second_digest = hashlib.sha256(second.read_bytes()).hexdigest()
    assert again["replay_ledger_sha256"] == second_digest
    assert again["prior_attempts"][1]["replay_ledger_sha256"] == digest
    assert json.loads(artifact.with_name("reproduction-gate.attempt-03.json").read_text(encoding="utf-8"))[
        "replay_ledger_sha256"
    ] == second_digest
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_replay_ledger_is_saved_when_comparison_raises(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    _pin_manifest(monkeypatch, tmp_path, _manifest(tmp_path, research, audit))
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    replay = {"1tick": [], "2tick": [], "3tick": []}
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        _fake_scored_window(replay),
    )

    def boom(_scored):
        raise RuntimeError("comparison failed closed")

    monkeypatch.setattr("research.afs0523_322_repro_wrapper.compare_replay_structural", boom)
    result = _call_gate(monkeypatch, tmp_path, research, audit)
    assert result["pass"] is False
    assert "comparison failed closed" in result["error"]
    saved = artifact.with_name("reproduction-replay-ledger.attempt-02.json")
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    assert json.loads(saved.read_text(encoding="utf-8")) == {"ledger": replay}
    assert result["replay_ledger_sha256"] == digest
    last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert last["replay_ledger_sha256"] == digest


def test_existing_replay_ledger_is_not_overwritten(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    _pin_manifest(monkeypatch, tmp_path, _manifest(tmp_path, research, audit))
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    saved = artifact.with_name("reproduction-replay-ledger.attempt-02.json")
    saved.write_text("{}\n", encoding="utf-8")
    before_ledger = ledger.read_bytes()
    before_saved = saved.read_bytes()
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        _fake_scored_window({"1tick": [], "2tick": [], "3tick": []}),
    )
    with pytest.raises(RuntimeError, match="overwrite replay ledger"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    assert saved.read_bytes() == before_saved
    assert ledger.read_bytes() == before_ledger
    assert not artifact.exists()


def test_pinned_manifest_hash_matches_the_committed_file_and_june_1_is_refused(tmp_path):
    from research.afs0523_322_repro_wrapper import DATA_MANIFEST_PATH, DATA_MANIFEST_SHA256

    raw = DATA_MANIFEST_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == DATA_MANIFEST_SHA256
    assert DATA_MANIFEST_SHA256 != "PLACEHOLDER"
    committed = json.loads(raw)
    nq = next(item for item in committed["windows"] if item["id"] == "nq-2010-06-to-2019-05")
    assert nq["start"] == "2010-06-06"
    assert nq["research_15m_sha256"] == "cb0457ed6f277caa14b55d4d249d9f2b67a55a9668b9f163fe1a0c6c521b2b3d"
    assert nq["audit_5m_sha256"] == "9621dfb3d2e42ae0ef775eee35d589c5eb3412f51f689c5e76c1bd72cb963235"
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    early = _manifest(tmp_path, research, audit)
    for window in early["windows"]:
        if window["id"] == "nq-2010-06-to-2019-05":
            window["start"] = "2010-06-01"
    with pytest.raises(RuntimeError, match="nq-2010-06-to-2019-05 bounds"):
        validate_download_manifest(early)


def test_download_manifest_schema_requires_each_window(monkeypatch, tmp_path):
    _patch_gate_paths(monkeypatch, tmp_path)
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    manifest = _manifest(tmp_path, research, audit)
    validate_download_manifest(manifest)
    broken = json.loads(json.dumps(manifest))
    broken["windows"] = [item for item in broken["windows"] if item["id"] != "nq-2010-06-to-2019-05"]
    with pytest.raises(RuntimeError, match="download manifest schema"):
        validate_download_manifest(broken)
    combined = json.loads(json.dumps(manifest))
    combined["windows"].append(
        _window("combined", "2010-06-01", "2024-06-30", "ab" * 32, "cd" * 32)
    )
    with pytest.raises(RuntimeError, match="combined is report-only"):
        validate_download_manifest(combined)
    schema = json.loads(
        (ROOT / "docs/schemas/afs0523-databento-download-manifest.schema.json").read_text(encoding="utf-8")
    )
    assert schema["required"] == ["pulled_files", "windows"]
    window_ids = schema["properties"]["windows"]["items"]["properties"]["id"]["enum"]
    assert "mnq-2019-05-to-2024-06" in window_ids
    assert "combined" not in window_ids
    _pin_manifest(monkeypatch, tmp_path, broken)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="download manifest schema"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    _pin_manifest(monkeypatch, tmp_path, combined)
    with pytest.raises(RuntimeError, match="combined is report-only"):
        _call_gate(monkeypatch, tmp_path, research, audit)
    with pytest.raises(RuntimeError, match="combined is report-only"):
        run_declared_slices(research, audit, slice_id="combined")
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_gate_review_admits_a_slice_without_scoring_and_rejects_a_different_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", False)
    repro_research = tmp_path / "repro-research"
    repro_audit = tmp_path / "repro-audit"
    slice_research = tmp_path / "slice-research"
    slice_audit = tmp_path / "slice-audit"
    _bar_file(repro_research, date(2024, 8, 2))
    _bar_file(repro_audit, date(2024, 8, 2))
    _bar_file(slice_research, date(2020, 1, 6))
    _bar_file(slice_audit, date(2020, 1, 6))
    manifest = _manifest(
        tmp_path,
        repro_research,
        repro_audit,
        slices={
            "mnq-2019-05-to-2024-06": (
                _tree_sha256(slice_research),
                _tree_sha256(slice_audit),
            )
        },
    )
    _pin_manifest(monkeypatch, tmp_path, manifest)
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    kept = [
        line
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if not line.strip()
        or json.loads(line).get("event") != "GATE"
        or json.loads(line).get("trial_id") != TRIAL_ID
    ]
    ledger.write_text("\n".join(kept) + "\n", encoding="utf-8")
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: {
            "data_gaps": [],
            "data_gap_count": 0,
            "invalid_entry_brackets": [],
            "invalid_entry_bracket_count": 0,
            "gap_open_count": 0,
        },
    )
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.compare_replay_to_oct4",
        lambda *_args, **_kwargs: {"ok": True, "comparisons": [], "mismatches": []},
    )
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._ohl_disagreement_days",
        lambda: set(),
    )
    result = _call_gate(monkeypatch, tmp_path, repro_research, repro_audit)
    assert result["pass"] is True
    assert result["research_15m_sha256"] != _tree_sha256(slice_research)
    wrong = {
        "trial_id": TRIAL_ID,
        "event": "GATE_REVIEW",
        "recorded_at": "2026-11-09T17:00:00Z",
        "gate_artifact_sha256": "0" * 64,
        "pr_head_sha": "b" * 40,
    }
    ledger.write_text(ledger.read_text(encoding="utf-8") + json.dumps(wrong) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="different artifact hash"):
        run_declared_slices(slice_research, slice_audit, slice_id="mnq-2019-05-to-2024-06")
    reviewed = record_gate_review("c" * 40)
    assert reviewed["event"] == "GATE_REVIEW"
    assert reviewed["gate_artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert reviewed["pr_head_sha"] == "c" * 40
    assert run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    ) is None
    gate_events = [
        json.loads(line)["event"]
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and json.loads(line).get("trial_id") == TRIAL_ID
        and json.loads(line).get("event") in {"GATE", "GATE_REVIEW"}
    ]
    assert gate_events.count("GATE") == 1
    assert "GATE_REVIEW" in gate_events
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_superseded_trial_stays_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    slice_research = tmp_path / "slice-research"
    slice_audit = tmp_path / "slice-audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    _bar_file(slice_research, date(2020, 1, 6))
    _bar_file(slice_audit, date(2020, 1, 6))
    manifest = _manifest(
        tmp_path,
        research,
        audit,
        slices={
            "mnq-2019-05-to-2024-06": (
                _tree_sha256(slice_research),
                _tree_sha256(slice_audit),
            )
        },
    )
    pin = _pin_manifest(monkeypatch, tmp_path, manifest)
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    _write_gate(
        artifact,
        ledger,
        {
            "pass": True,
            "claude_review": None,
            "prior_attempts": [],
            "pulled_files": manifest["pulled_files"],
            "research_15m_sha256": _repro(manifest)["research_15m_sha256"],
            "audit_5m_sha256": _repro(manifest)["audit_5m_sha256"],
            "manifest_sha256": pin,
        },
    )
    review = {
        "trial_id": TRIAL_ID,
        "event": "GATE_REVIEW",
        "gate_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "pr_head_sha": "d" * 40,
    }
    superseded = {"trial_id": TRIAL_ID, "event": "SUPERSEDED"}
    ledger.write_text(
        ledger.read_text(encoding="utf-8") + json.dumps(review) + "\n" + json.dumps(superseded) + "\n",
        encoding="utf-8",
    )
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="SUPERSEDED"):
        run_declared_slices(slice_research, slice_audit, slice_id="mnq-2019-05-to-2024-06")
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def _superseded_ledger(monkeypatch, tmp_path: Path) -> tuple[Path, bytes]:
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes((ROOT / "docs/research-trial-ledger.jsonl").read_bytes())
    ledger.write_text(
        ledger.read_text(encoding="utf-8")
        + json.dumps({"trial_id": TRIAL_ID, "event": "SUPERSEDED", "recorded_at": "2026-11-09T18:00:00Z"})
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.GATE_ARTIFACT",
        tmp_path / "reproduction-gate.json",
    )
    return ledger, ledger.read_bytes()


def test_superseded_trial_refuses_the_reproduction_gate(monkeypatch, tmp_path):
    ledger, before = _superseded_ledger(monkeypatch, tmp_path)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="SUPERSEDED"):
        run_reproduction_gate(tmp_path, Path("."), Path("."))
    assert ledger.read_bytes() == before
    assert not (tmp_path / "reproduction-gate.json").exists()
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_bad_scoring_profile_refuses_before_a_gate_line(monkeypatch, tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(_open_trial_ledger_text(), encoding="utf-8")
    artifact = tmp_path / "reproduction-gate.json"
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.GATE_ARTIFACT", artifact)
    before = ledger.read_bytes()
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.RUN_AUTHORIZED", True)
    monkeypatch.delenv("MAX_CONTRACTS_HARD_CAP", raising=False)
    with pytest.raises(RuntimeError, match="MAX_CONTRACTS_HARD_CAP"):
        run_reproduction_gate(tmp_path, Path("research"), Path("audit"))
    for bad in ("2", ""):
        monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", bad)
        with pytest.raises(RuntimeError, match="MAX_CONTRACTS_HARD_CAP"):
            run_reproduction_gate(tmp_path, Path("research"), Path("audit"))
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.per_instrument_contract_ceiling",
        lambda: 5,
    )
    with pytest.raises(RuntimeError, match="risk_rules_contract_ceiling"):
        run_reproduction_gate(tmp_path, Path("research"), Path("audit"))
    assert ledger.read_bytes() == before
    assert not artifact.exists()
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_recorded_attempt_02_gate_line_is_the_pinned_download():
    fail_sha = "255f669c785a7396c773f6143ecf8b82afe7292cd012566cda879c880e0d1a0f"
    evidence = (
        ROOT
        / "docs/research-evidence/T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01"
    )
    assert hashlib.sha256((evidence / "reproduction-gate.json").read_bytes()).hexdigest() == (
        "ede35c326057ba4fa39cebfeaad8851d97ad60df9de1c2ddedd9b888dcd1dfeb"
    )
    assert hashlib.sha256((evidence / "reproduction-gate.attempt-02.json").read_bytes()).hexdigest() == fail_sha
    matches = []
    for line in (ROOT / "docs/research-trial-ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event") == "GATE" and row.get("gate_artifact_sha256") == fail_sha:
            matches.append(row)
    assert len(matches) == 1
    gate = matches[0]
    assert gate["gate_pass"] is False
    assert gate["event"] == "GATE"
    assert gate["manifest_sha256"] == "6c9d5ab3c29a7aeade90e95c84e0128795afc1bdd19aa165e18d364760b7f43d"
    assert gate["research_15m_sha256"] == "461384bc2c638e849ebcdbd4c1fed09fde79fbc9c48dee7656a1151c259e3339"
    assert gate["audit_5m_sha256"] == "a08d909274fa5a06e9234b1c0975977d91a00b1f749c0560fd6d41859410c1e3"
    assert gate["manifest_sha256"] == DATA_MANIFEST_SHA256


def test_recorded_gate_fail_is_the_pinned_download_and_not_different_data(tmp_path):
    fail_sha = "ede35c326057ba4fa39cebfeaad8851d97ad60df9de1c2ddedd9b888dcd1dfeb"
    matches = []
    for line in (ROOT / "docs/research-trial-ledger.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("event") == "GATE" and row.get("gate_artifact_sha256") == fail_sha:
            matches.append(line)
    assert len(matches) == 1
    gate_line = matches[0]
    gate = json.loads(gate_line)
    assert gate["event"] == "GATE"
    assert gate["gate_pass"] is False
    assert gate["gate_artifact_sha256"] == fail_sha
    assert gate["manifest_sha256"] == DATA_MANIFEST_SHA256
    assert gate["research_15m_sha256"] == "461384bc2c638e849ebcdbd4c1fed09fde79fbc9c48dee7656a1151c259e3339"
    assert gate["audit_5m_sha256"] == "a08d909274fa5a06e9234b1c0975977d91a00b1f749c0560fd6d41859410c1e3"
    later = {
        "pass": True,
        "manifest_sha256": gate["manifest_sha256"],
        "research_15m_sha256": gate["research_15m_sha256"],
        "audit_5m_sha256": gate["audit_5m_sha256"],
        "prior_attempts": [
            {
                "manifest_sha256": gate["manifest_sha256"],
                "research_15m_sha256": gate["research_15m_sha256"],
                "audit_5m_sha256": gate["audit_5m_sha256"],
            }
        ],
    }
    ledger = tmp_path / "ledger.jsonl"
    follow = {
        "trial_id": gate["trial_id"],
        "event": "GATE",
        "gate_pass": True,
        "manifest_sha256": later["manifest_sha256"],
        "research_15m_sha256": later["research_15m_sha256"],
        "audit_5m_sha256": later["audit_5m_sha256"],
        "gate_artifact_sha256": "ab" * 32,
    }
    ledger.write_text(gate_line + "\n" + json.dumps(follow) + "\n", encoding="utf-8")
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_ID", gate["trial_id"])
    try:
        _refuse_changed_gate_data(later)
    finally:
        monkeypatch.undo()


def test_superseded_trial_refuses_a_gate_review(monkeypatch, tmp_path):
    ledger, before = _superseded_ledger(monkeypatch, tmp_path)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="SUPERSEDED"):
        record_gate_review("c" * 40)
    assert ledger.read_bytes() == before
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_writer_refuses_an_existing_attempt_file(monkeypatch, tmp_path):
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    prior = [
        json.dumps({"trial_id": TRIAL_ID, "event": "GATE", "gate_pass": False, "gate_artifact_sha256": "a" * 64}),
        json.dumps({"trial_id": TRIAL_ID, "event": "GATE", "gate_pass": False, "gate_artifact_sha256": "b" * 64}),
    ]
    ledger.write_text(ledger.read_text(encoding="utf-8") + "\n".join(prior) + "\n", encoding="utf-8")
    path = artifact.with_name("reproduction-gate.attempt-04.json")
    path.write_text("{}\n", encoding="utf-8")
    before = ledger.read_bytes()
    with pytest.raises(RuntimeError, match="overwrite"):
        _write_gate_artifact({"pass": False})
    assert path.read_text(encoding="utf-8") == "{}\n"
    assert ledger.read_bytes() == before
    assert not artifact.exists()


def test_slice_admission_refuses_a_missing_cap(monkeypatch, tmp_path):
    _artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    monkeypatch.delenv("MAX_CONTRACTS_HARD_CAP", raising=False)
    before = ledger.read_bytes()
    with pytest.raises(RuntimeError, match="MAX_CONTRACTS_HARD_CAP"):
        run_declared_slices()
    assert ledger.read_bytes() == before


def _stamp_scored_databento_bars(ledger: dict) -> dict:
    """Give the replay the pinned Databento decision bars the structural gate requires."""
    mar06 = json.loads(MAR06_BAR_EVIDENCE_PATH.read_text(encoding="utf-8"))["decision_bar"]["databento_5m"]
    evidence = json.loads(BAR_EVIDENCE_PATH.read_text(encoding="utf-8"))
    closes = {day["date"]: day["databento_5m"]["close"] for day in evidence["mismatch_days"]}
    for rows in ledger.values():
        for row in rows:
            if row["date"] == "2026-03-06":
                row["trigger_bar_open"] = mar06["open"]
                row["trigger_bar_high"] = mar06["high"]
                row["trigger_bar_low"] = mar06["low"]
                row["trigger_bar_close"] = mar06["close"]
            elif row["date"] in closes:
                row["trigger_bar_close"] = closes[row["date"]]
    return ledger


def test_structural_gate_allows_only_documented_close_deltas():
    reference = json.loads(
        (ROOT / "docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json").read_text(
            encoding="utf-8"
        )
    )
    exact = compare_replay_to_oct4({"ledger": reference["ledger"]})
    unbound = compare_replay_structural({"ledger": reference["ledger"]})
    assert exact["ok"] is True
    assert unbound["ok"] is False
    assert any(row["field"] == "scored_bar" and row["date"] == "2026-03-06" for row in unbound["mismatches"])
    scored = json.loads(json.dumps(reference))
    _stamp_scored_databento_bars(scored["ledger"])
    structural = compare_replay_structural({"ledger": scored["ledger"]})
    assert structural["ok"] is True
    assert structural["ohl_reference_exclusions"] == {"count": 1, "dates": ["2026-03-06"]}
    assert not any(row["field"] == "open_high_low" for row in structural["mismatches"])
    allowlist = _ioc_close_allowlist()
    assert set(allowlist) == {"2024-09-06", "2026-06-11"}
    assert "2026-03-06" not in allowlist
    moved = json.loads(json.dumps(reference))
    _stamp_scored_databento_bars(moved["ledger"])
    for rows in moved["ledger"].values():
        for row in rows:
            if row["date"] == "2024-09-06":
                row["models"]["ioc_close"]["fill_entry"] = float(row["models"]["ioc_close"]["fill_entry"]) + 0.75
                row["models"]["ioc_close"]["net"] = float(row["models"]["ioc_close"]["net"]) + 1.50
    assert compare_replay_to_oct4({"ledger": moved["ledger"]})["ok"] is False
    moved_structural = compare_replay_structural({"ledger": moved["ledger"]})
    assert moved_structural["ok"] is True
    assert moved_structural["ohl_reference_exclusions"]["dates"] == ["2026-03-06"]
    assert not any(
        row["date"] == "2024-09-06" and row["field"] in {"fill_price", "net"}
        for row in moved_structural["mismatches"]
    )
    wrong = json.loads(json.dumps(reference))
    _stamp_scored_databento_bars(wrong["ledger"])
    for rows in wrong["ledger"].values():
        for row in rows:
            if row["date"] == "2024-09-06":
                row["models"]["ioc_close"]["fill_entry"] = float(row["models"]["ioc_close"]["fill_entry"]) + 1.0
    assert any(
        row["date"] == "2024-09-06" and row["field"] == "fill_price"
        for row in compare_replay_structural({"ledger": wrong["ledger"]})["mismatches"]
    )
    pre_state = json.loads(json.dumps(reference))
    for rows in pre_state["ledger"].values():
        for row in rows:
            if row["date"] == "2026-03-06":
                row["direction"] = "SHORT" if row["direction"] == "LONG" else "LONG"
    assert compare_replay_structural({"ledger": pre_state["ledger"]})["ok"] is False
    prereg = (ROOT / "docs/prereg-322-afs0523-oos-structural-2026-11-10.md").read_text(encoding="utf-8")
    assert "written after AFS-0586" in prereg
    assert "written after AFS-0594" in prereg
    assert "not the reason it is excluded" in prereg
    assert "Original and pre-armed match exactly" in prereg
    assert OLDER_SLICE_IOC_NOTE in prereg
    assert "data-gap count goes from 3 to 2" in prereg
    assert "34 scored dates stay the same" in prereg
    for year in ("2011", "2012", "2013", "2015", "2016", "2017", "2018", "2019", "2020", "2022", "2023"):
        assert year in prereg
    assert allowlist["2024-09-06"] == Decimal("0.75")
    assert allowlist["2026-06-11"] == Decimal("-0.25")
    gate_source = Path(__file__).resolve().parents[1].joinpath(
        "research/afs0523_322_repro_wrapper.py"
    ).read_text(encoding="utf-8")
    gate_body = gate_source.split("def run_reproduction_gate", 1)[1].split("\ndef ", 1)[0]
    assert "compare_replay_structural" in gate_body
    assert "compare_replay_to_oct4" not in gate_body


def test_edited_bar_evidence_fails_the_pin(monkeypatch, tmp_path):
    raw = BAR_EVIDENCE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == BAR_EVIDENCE_SHA256
    payload = json.loads(raw)
    payload["other_decision_bar_close_disagreements"][0]["ohl_agree"] = True
    edited = tmp_path / "BAR_EVIDENCE.json"
    edited.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.BAR_EVIDENCE_PATH", edited)
    with pytest.raises(RuntimeError, match="BAR_EVIDENCE.json hash mismatch"):
        _load_bar_evidence()
    with pytest.raises(RuntimeError, match="BAR_EVIDENCE.json hash mismatch"):
        _ioc_close_allowlist()


def test_gate_and_slice_refuse_ohl_disagreement_when_fills_match(monkeypatch, tmp_path):
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", False)
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    slice_research = tmp_path / "slice-research"
    slice_audit = tmp_path / "slice-audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    _bar_file(slice_research, date(2020, 1, 6))
    _bar_file(slice_audit, date(2020, 1, 6))
    manifest = _manifest(
        tmp_path,
        research,
        audit,
        slices={
            "mnq-2019-05-to-2024-06": (
                _tree_sha256(slice_research),
                _tree_sha256(slice_audit),
            ),
        },
    )
    _pin_manifest(monkeypatch, tmp_path, manifest)
    artifact, _ledger = _patch_gate_paths(monkeypatch, tmp_path)
    reference = json.loads(LEDGER.read_text(encoding="utf-8"))
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: {
            "ledger": reference["ledger"],
            "data_gaps": [],
            "data_gap_count": 0,
            "invalid_entry_brackets": [],
            "invalid_entry_bracket_count": 0,
            "gap_open_count": 0,
        },
    )
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.compare_replay_structural",
        lambda *_args, **_kwargs: {"ok": True, "comparisons": 306, "mismatches": []},
    )
    result = _call_gate(monkeypatch, tmp_path, research, audit)
    assert result["pass"] is True
    assert result["ohl_reference_exclusions"] == {"count": 1, "dates": ["2026-03-06"]}
    assert not any(row.get("field") == "open_high_low" for row in result["mismatches"])
    written_gate = artifact.with_name("reproduction-gate.attempt-02.json")
    review = {
        "trial_id": TRIAL_ID,
        "event": "GATE_REVIEW",
        "gate_artifact_sha256": hashlib.sha256(written_gate.read_bytes()).hexdigest(),
        "pr_head_sha": "d" * 40,
    }
    passing = json.loads(written_gate.read_text(encoding="utf-8"))
    passing["pass"] = True
    passing["mismatches"] = []
    passing["prior_attempts"] = []
    text = json.dumps(passing, indent=2) + "\n"
    artifact.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    review["gate_artifact_sha256"] = digest
    gate_line = {
        "trial_id": TRIAL_ID,
        "event": "GATE",
        "gate_pass": True,
        "gate_artifact_sha256": digest,
        "pulled_files": passing["pulled_files"],
        "research_15m_sha256": passing["research_15m_sha256"],
        "audit_5m_sha256": passing["audit_5m_sha256"],
        "manifest_sha256": passing["manifest_sha256"],
        "prior_attempts": passing["prior_attempts"],
    }
    _ledger.write_text(json.dumps(gate_line) + "\n" + json.dumps(review) + "\n", encoding="utf-8")
    assert run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    ) is None


def test_tampered_mar06_evidence_fails_the_pin(monkeypatch, tmp_path):
    raw = MAR06_BAR_EVIDENCE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == MAR06_BAR_EVIDENCE_SHA256
    edited = tmp_path / "MAR06_BAR_EVIDENCE.json"
    edited.write_bytes(raw.replace(b"REFERENCE_DEFECT", b"UNEXPLAINEDXXXXX", 1))
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.MAR06_BAR_EVIDENCE_PATH", edited)
    with pytest.raises(RuntimeError, match="MAR06_BAR_EVIDENCE.json hash mismatch"):
        _load_mar06_evidence()


def test_ohl_disagreement_without_1m_evidence_fails(monkeypatch):
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._ohl_disagreement_days",
        lambda: {"2024-08-02"},
    )
    resolution = _ohl_resolution()
    assert resolution["exclude"] == []
    assert resolution["fail"] == [{"date": "2024-08-02", "classification": "UNEXPLAINED"}]
    comparison = _fail_closed_on_ohl_disagreement({"ok": True, "mismatches": []})
    assert comparison["ok"] is False
    assert comparison["ohl_reference_exclusions"] == {"count": 0, "dates": []}
    assert any(
        row["date"] == "2024-08-02" and row["actual"] == "UNEXPLAINED"
        for row in comparison["mismatches"]
    )


def test_1m_rows_that_do_not_rebuild_the_5m_bar_fail(monkeypatch):
    payload = json.loads(MAR06_BAR_EVIDENCE_PATH.read_text(encoding="utf-8"))
    payload["classification"] = "REFERENCE_DEFECT"
    payload["mnqh6_1m_15_00_to_15_04"]["matches_committed_databento_5m_ohlcv"] = True
    payload["mnqh6_1m_15_00_to_15_04"]["rows"][0]["open"] = 1.0
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._load_mar06_evidence",
        lambda: payload,
    )
    resolution = _ohl_resolution()
    assert resolution["exclude"] == []
    assert {"date": "2026-03-06", "classification": "DATABENTO_SIDE"} in resolution["fail"]
    comparison = _fail_closed_on_ohl_disagreement({"ok": True, "mismatches": []})
    assert comparison["ok"] is False
    assert any(
        row["date"] == "2026-03-06" and row["actual"] == "DATABENTO_SIDE"
        for row in comparison["mismatches"]
    )


def test_real_2026_03_06_is_excluded_and_the_label_is_not_trusted():
    payload = json.loads(MAR06_BAR_EVIDENCE_PATH.read_text(encoding="utf-8"))
    payload["classification"] = "DATABENTO_SIDE"
    payload["mnqh6_1m_15_00_to_15_04"]["matches_committed_databento_5m_ohlcv"] = False
    assert _classify_mar06(payload) == "REFERENCE_DEFECT"
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))["ledger"]
    _stamp_scored_databento_bars(ledger)
    structural = compare_replay_structural({"ledger": ledger})
    assert structural["ok"] is True
    assert structural["ohl_reference_exclusions"] == {"count": 1, "dates": ["2026-03-06"]}
    assert not any(row["field"] == "open_high_low" for row in structural["mismatches"])
    assert "exclude" not in run_reproduction_gate.__code__.co_varnames


def test_excluded_day_fails_when_the_scored_bar_is_not_the_pinned_databento_bar():
    reference = json.loads(LEDGER.read_text(encoding="utf-8"))
    _stamp_scored_databento_bars(reference["ledger"])
    for rows in reference["ledger"].values():
        for row in rows:
            if row["date"] == "2026-03-06":
                row["trigger_bar_high"] = float(row["trigger_bar_high"]) + 0.25
    structural = compare_replay_structural({"ledger": reference["ledger"]})
    assert structural["ok"] is False
    assert structural["ohl_reference_exclusions"] == {"count": 0, "dates": []}
    assert any(
        row["date"] == "2026-03-06" and row["field"] == "scored_bar" and row["actual"] == "DATABENTO_SIDE"
        for row in structural["mismatches"]
    )


def _synthetic_slice_ledger() -> dict:
    def row(day: str, result: str, net: float, *, status: str = "RESOLVED") -> dict:
        plan = {"status": status, "result": result, "net": net, "fill_entry": 1.0}
        if status != "RESOLVED":
            plan["reason"] = "EOD_BAR_MISSING"
        if status == "RESOLVED":
            prearmed = {"status": "RESOLVED", "result": result, "net": net - 1.0}
        else:
            prearmed = {"status": status, "reason": "EOD_BAR_MISSING", "fill_entry": 1.0}
        return {
            "date": day,
            "models": {
                "plan": plan,
                "ioc_close": {"status": "NO_FILL", "reason": "ENTRY_NOT_FILLED"},
                "prearmed_touch": prearmed,
            },
        }

    rows = [
        row("2020-01-06", "WIN", 10.0),
        row("2020-01-07", "LOSS", -4.0),
        row("2020-01-08", "LOSS", -3.0),
        row("2020-01-09", "WIN", 2.0),
        row("2020-01-10", "BREAKEVEN", 0.0, status="UNRESOLVED"),
    ]
    return {"1tick": rows, "2tick": [], "3tick": []}


def test_slice_summary_is_raw_and_has_no_pass_fail():
    assert SLICE_SCORING_AUTHORIZED is True
    summary = summarize_slice_ledger(_synthetic_slice_ledger())
    original = summary["1tick"]["original"]
    assert original["candidates"] == 5
    assert original["fills"] == 5
    assert original["resolved"] == 4
    assert original["unresolved_count"] == 1
    assert original["unresolved"] == [{"date": "2020-01-10", "reason": "EOD_BAR_MISSING"}]
    assert original["no_fill_count"] == 0
    assert [item["net"] for item in original["resolved_trade_list"]] == [10.0, -4.0, -3.0, 2.0]
    assert original["net_pnl_resolved_only"] == 5.0
    assert original["win_rate_resolved_only"] == 0.5
    assert original["drawdown_resolved_only"] == 7.0
    assert original["longest_losing_streak_resolved_only"] == 2
    assert "net_pnl" not in original
    prearmed = summary["1tick"]["prearmed"]
    assert prearmed["resolved"] == 4
    assert prearmed["unresolved_count"] == 1
    assert prearmed["fills"] == 5
    assert prearmed["net_pnl_resolved_only"] == 1.0
    assert prearmed["drawdown_resolved_only"] == 9.0
    assert prearmed["longest_losing_streak_resolved_only"] == 2
    honest = summary["1tick"]["honest_ioc"]
    assert honest["candidates"] == 5
    assert honest["fills"] == 0
    assert honest["resolved"] == 0
    assert honest["unresolved_count"] == 0
    assert honest["no_fill_count"] == 5
    assert honest["no_fills"] == {"ENTRY_NOT_FILLED": 5}
    assert honest["net_pnl_resolved_only"] == 0.0
    assert honest["win_rate_resolved_only"] is None
    assert honest["drawdown_resolved_only"] == 0.0
    assert honest["longest_losing_streak_resolved_only"] == 0
    assert summary["2tick"]["original"]["resolved"] == 0
    assert "pass" not in summary and "ok" not in summary


def test_git_checkout_helper_sees_a_dirty_tree(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "note.txt").write_text("one\n", encoding="utf-8")
    subprocess.run(["git", "add", "note.txt"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    head, clean = _git_head_and_clean(tmp_path)
    assert clean is True
    assert len(head) == 40
    (tmp_path / "note.txt").write_text("two\n", encoding="utf-8")
    _dirty_head, dirty = _git_head_and_clean(tmp_path)
    assert dirty is False
    (tmp_path / "note.txt").write_text("one\n", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("x\n", encoding="utf-8")
    _untracked_head, untracked = _git_head_and_clean(tmp_path)
    assert untracked is False


def _prepare_admitted_slice(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    repro_research = tmp_path / "repro-research"
    repro_audit = tmp_path / "repro-audit"
    slice_research = tmp_path / "slice-research"
    slice_audit = tmp_path / "slice-audit"
    _bar_file(repro_research, date(2024, 8, 2))
    _bar_file(repro_audit, date(2024, 8, 2))
    _bar_file(slice_research, date(2020, 1, 6))
    _bar_file(slice_audit, date(2020, 1, 6))
    manifest = _manifest(
        tmp_path,
        repro_research,
        repro_audit,
        slices={
            "mnq-2019-05-to-2024-06": (
                _tree_sha256(slice_research),
                _tree_sha256(slice_audit),
            ),
            "nq-2010-06-to-2019-05": (
                _tree_sha256(slice_research),
                _tree_sha256(slice_audit),
            ),
        },
    )
    pin = _pin_manifest(monkeypatch, tmp_path, manifest)
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
    _write_gate(
        artifact,
        ledger,
        {
            "pass": True,
            "claude_review": None,
            "prior_attempts": [],
            "pulled_files": manifest["pulled_files"],
            "research_15m_sha256": _repro(manifest)["research_15m_sha256"],
            "audit_5m_sha256": _repro(manifest)["audit_5m_sha256"],
            "manifest_sha256": pin,
        },
    )
    review = {
        "trial_id": TRIAL_ID,
        "event": "GATE_REVIEW",
        "gate_artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "pr_head_sha": "c" * 40,
    }
    ledger.write_text(ledger.read_text(encoding="utf-8") + json.dumps(review) + "\n", encoding="utf-8")
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.SLICE_ARTIFACT_DIR",
        tmp_path / "slices",
    )
    return slice_research, slice_audit, ledger


def _slice_events(ledger: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("event") == "SLICE"
    ]


def test_admitted_slice_does_not_score_while_authorization_is_false(monkeypatch, tmp_path):
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", False)
    slice_research, slice_audit, ledger = _prepare_admitted_slice(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: calls.append(True),
    )
    before = ledger.read_bytes()
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    assert (
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
        is None
    )
    assert calls == []
    assert ledger.read_bytes() == before
    assert not (tmp_path / "slices").exists()
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_authorized_slice_writes_attempt_files_and_refuses_a_dirty_tree(monkeypatch, tmp_path):
    slice_research, slice_audit, ledger = _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", True)
    scored = {
        "ledger": _synthetic_slice_ledger(),
        "data_gaps": [{"date": "2020-01-02", "status": "DATA_GAP", "scored": False}],
        "data_gap_count": 1,
        "early_close_sessions": [],
    }
    calls = []

    def _fake_window(*_args, **kwargs):
        calls.append(kwargs)
        return scored

    monkeypatch.setattr("research.afs0523_322_repro_wrapper._run_window", _fake_window)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ab" * 20, False),
    )
    before = ledger.read_bytes()
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    with pytest.raises(RuntimeError, match="dirty checkout"):
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
    assert calls == []
    assert ledger.read_bytes() == before
    assert not (tmp_path / "slices").exists()

    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("cd" * 20, True),
    )
    result = run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    )
    assert calls[0]["start"] == date(2019, 5, 1)
    assert calls[0]["end"] == date(2024, 6, 30)
    assert calls[0]["expected_count"] is None
    assert result["git_head"] == "cd" * 20
    assert result["clean_tree"] is True
    wrapper_path = ROOT / "research/afs0523_322_repro_wrapper.py"
    assert result["wrapper_sha256"] == hashlib.sha256(wrapper_path.read_bytes()).hexdigest()
    assert result["by_slippage"]["1tick"]["original"]["net_pnl_resolved_only"] == 5.0
    assert result["by_slippage"]["1tick"]["original"]["resolved"] == 4
    assert result["by_slippage"]["1tick"]["original"]["unresolved_count"] == 1
    assert result["manifest_sha256"]
    assert result["research_15m_sha256"] == _tree_sha256(slice_research)
    assert result["audit_5m_sha256"] == _tree_sha256(slice_audit)
    assert result["data_root"] == str(tmp_path.resolve())
    assert result["MAX_CONTRACTS_HARD_CAP"] == "1"
    assert result["data_gap_count"] == 1
    assert "pass" not in result and "ok" not in result
    artifact = tmp_path / "slices" / "mnq-2019-05-to-2024-06.attempt-01.json"
    replay = tmp_path / "slices" / "mnq-2019-05-to-2024-06.replay-ledger.attempt-01.json"
    artifact_bytes = artifact.read_bytes()
    replay_bytes = replay.read_bytes()
    assert hashlib.sha256(replay_bytes).hexdigest() == result["replay_ledger_sha256"]
    assert json.loads(replay_bytes)["ledger"]["1tick"][0]["date"] == "2020-01-06"
    event = _slice_events(ledger)[-1]
    assert event["event"] == "SLICE"
    assert event["slice_artifact_sha256"] == hashlib.sha256(artifact_bytes).hexdigest()
    assert event["replay_ledger_sha256"] == result["replay_ledger_sha256"]
    assert event["git_head"] == result["git_head"]
    assert event["clean_tree"] is True
    assert event["wrapper_sha256"] == result["wrapper_sha256"]
    assert "gate_pass" not in event
    assert json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])["event"] == "SLICE"

    scored["ledger"] = {"1tick": [], "2tick": [], "3tick": []}
    again = run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    )
    second = tmp_path / "slices" / "mnq-2019-05-to-2024-06.attempt-02.json"
    second_replay = tmp_path / "slices" / "mnq-2019-05-to-2024-06.replay-ledger.attempt-02.json"
    assert second.is_file() and second_replay.is_file()
    assert artifact.read_bytes() == artifact_bytes
    assert replay.read_bytes() == replay_bytes
    assert again["by_slippage"]["1tick"]["original"]["resolved"] == 0
    assert len(_slice_events(ledger)) == 2
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_authorized_slice_keeps_admission_refusals(monkeypatch, tmp_path):
    _slice_research, _slice_audit, ledger = _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", True)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ef" * 20, True),
    )
    before = ledger.read_bytes()
    with pytest.raises(RuntimeError, match="combined is report-only"):
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="combined",
        )
    wrong_audit = tmp_path / "wrong-audit"
    _bar_file(wrong_audit, date(2020, 1, 6))
    (wrong_audit / "MNQ" / "MNQ_2020-01-06.jsonl").write_text(
        '{"timestamp": "2020-01-06T14:00:00+00:00", "open": 9}\n',
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="tree hash"):
        run_declared_slices(
            Path("slice-research"),
            Path("wrong-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
    monkeypatch.delenv("MAX_CONTRACTS_HARD_CAP", raising=False)
    with pytest.raises(RuntimeError, match="MAX_CONTRACTS_HARD_CAP"):
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    superseded = {"trial_id": TRIAL_ID, "event": "SUPERSEDED"}
    ledger.write_text(ledger.read_text(encoding="utf-8") + json.dumps(superseded) + "\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="SUPERSEDED"):
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
    assert not (tmp_path / "slices").exists()
    assert b"SLICE" not in ledger.read_bytes()
    assert before in ledger.read_bytes()


def test_slice_attempt_file_is_never_overwritten(monkeypatch, tmp_path):
    _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", True)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ab" * 20, True),
    )
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: {"ledger": {"1tick": [], "2tick": [], "3tick": []}},
    )
    target = tmp_path / "slices"
    target.mkdir()
    existing = target / "mnq-2019-05-to-2024-06.attempt-01.json"
    existing.write_text("{}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="overwrite"):
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
    assert existing.read_text(encoding="utf-8") == "{}\n"


def test_scoring_exception_writes_a_slice_event_with_null_products(monkeypatch, tmp_path):
    slice_research, slice_audit, ledger = _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", True)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ab" * 20, True),
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic scoring failure")

    monkeypatch.setattr("research.afs0523_322_repro_wrapper._run_window", _boom)
    with pytest.raises(RuntimeError, match="synthetic scoring failure"):
        run_declared_slices(
            Path("slice-research"),
            Path("slice-audit"),
            data_root=tmp_path,
            slice_id="mnq-2019-05-to-2024-06",
        )
    event = _slice_events(ledger)[-1]
    assert event["event"] == "SLICE"
    assert event["slice_artifact"] is None
    assert event["slice_artifact_sha256"] is None
    assert event["replay_ledger"] is None
    assert event["replay_ledger_sha256"] is None
    assert event["error"] == "synthetic scoring failure"
    assert event["manifest_sha256"]
    assert event["research_15m_sha256"] == _tree_sha256(slice_research)
    assert event["audit_5m_sha256"] == _tree_sha256(slice_audit)
    assert event["MAX_CONTRACTS_HARD_CAP"] == "1"
    assert event["git_head"] == "ab" * 20
    assert not (tmp_path / "slices").exists()
    assert "pass" not in event and "gate_pass" not in event


def test_unscored_dates_include_an_excluded_early_close():
    dates = _unscored_dates(
        {
            "data_gaps": [{"date": "2020-01-02", "status": "DATA_GAP", "scored": False}],
            "early_close_sessions": [
                {"date": "2020-07-03", "status": "EARLY_CLOSE", "scored": False, "disposition": "exclude"}
            ],
        }
    )
    assert dates == {"2020-01-02", "2020-07-03"}
    assert _unscored_dates({"data_gaps": []}) == set()
    held = _unscored_dates(
        {
            "data_gaps": [],
            "expected_holiday_closures": [{"date": "2014-07-04", "kind": "closed"}],
        },
        include_holiday_closures=True,
    )
    assert held == {"2014-07-04"}
    assert _unscored_dates(
        {
            "data_gaps": [{"date": "2025-01-09"}],
            "expected_holiday_closures": [{"date": "2025-07-04", "kind": "closed"}],
        },
        include_holiday_closures=False,
    ) == {"2025-01-09"}


def test_early_close_stays_out_of_the_holiday_closed_count():
    closures = [
        {"date": "2014-07-04", "kind": "closed"},
        {"date": "2014-07-03", "kind": "early_close"},
    ]
    excluded = {
        "data_gaps": [],
        "expected_holiday_closures": closures,
        "early_close_sessions": [
            {"date": "2014-07-03", "scored": False, "disposition": "exclude"},
        ],
    }
    included = {
        "data_gaps": [],
        "expected_holiday_closures": closures,
        "early_close_sessions": [
            {"date": "2014-07-03", "scored": True, "disposition": "include"},
        ],
    }
    assert _holiday_closed_dates(excluded) == ["2014-07-04"]
    assert len(_holiday_closed_dates(excluded)) == 1
    assert _unscored_dates(excluded, include_holiday_closures=True) == {"2014-07-04", "2014-07-03"}
    assert _holiday_closed_dates(included) == ["2014-07-04"]
    assert len(_holiday_closed_dates(included)) == 1
    assert _unscored_dates(included, include_holiday_closures=True) == {"2014-07-04"}


def test_slice_holiday_closure_with_bars_yields_no_candidate(monkeypatch, tmp_path):
    from research.databento_322_bar_adapter import cme_equity_index_session

    day = date(2014, 7, 4)
    assert cme_equity_index_session(day) == "closed"
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, day)
    _bar_file(audit, day)
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.detect_candidates",
        lambda *_args, **_kwargs: [
            {
                "date": day,
                "direction": "LONG",
                "entry_trigger": 10.0,
                "entry_price": 10.0,
                "stop": 9.0,
                "target": 12.0,
                "gap_open": False,
            }
        ],
    )

    class _State:
        date = day.isoformat()
        bar_idx = 0
        direction = "SHORT"
        entry = 1.0
        stop = 2.0
        target = 3.0

    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.extract_state_machine",
        lambda *_args, **_kwargs: [_State()],
    )
    result = _run_window(research, audit, start=day, end=day, expected_count=None)
    assert result["candidate_count"] == 0
    assert result["data_gap_count"] == 0
    assert result["holiday_closed_unscored"] == ["2014-07-04"]
    assert result["holiday_closed_unscored_count"] == 1
    assert SLICE_SCORING_AUTHORIZED is True


def test_holiday_closed_list_is_on_the_slice_artifact_and_event(monkeypatch, tmp_path):
    slice_research, slice_audit, ledger = _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", True)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ab" * 20, True),
    )
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: {
            "ledger": {"1tick": [], "2tick": [], "3tick": []},
            "data_gaps": [],
            "data_gap_count": 0,
            "holiday_closed_unscored": ["2014-07-04", "2014-11-27"],
            "holiday_closed_unscored_count": 2,
        },
    )
    result = run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    )
    assert result["holiday_closed_unscored"] == ["2014-07-04", "2014-11-27"]
    assert result["holiday_closed_unscored_count"] == 2
    event = _slice_events(ledger)[-1]
    assert event["holiday_closed_unscored"] == result["holiday_closed_unscored"]
    assert event["holiday_closed_unscored_count"] == 2


def test_slice_artifact_reports_gap_open_and_non_gap_drops(monkeypatch, tmp_path):
    slice_research, slice_audit, ledger = _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.SLICE_SCORING_AUTHORIZED", True)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ab" * 20, True),
    )
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: {
            "ledger": {"1tick": [], "2tick": [], "3tick": []},
            "data_gaps": [],
            "data_gap_count": 0,
            "invalid_entry_bracket_count": 2,
            "invalid_entry_bracket_gap_open_count": 1,
            "invalid_entry_bracket_non_gap_open_count": 1,
        },
    )
    result = run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    )
    assert result["invalid_entry_bracket_gap_open_count"] == 1
    assert result["invalid_entry_bracket_non_gap_open_count"] == 1
    event = _slice_events(ledger)[-1]
    assert event["invalid_entry_bracket_gap_open_count"] == 1
    assert event["invalid_entry_bracket_non_gap_open_count"] == 1
    assert slice_research.is_dir() and slice_audit.is_dir()


def test_reproduction_window_run_does_not_remove_holiday_closures(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
    closures = [
        {"date": "2024-07-04", "kind": "closed"},
        {"date": "2024-11-29", "kind": "early_close"},
    ]
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper.data_gap_dates_from_trees",
        lambda *_args, **_kwargs: {
            "data_gaps": [
                {"date": "2025-01-09", "status": "DATA_GAP", "scored": False},
                {"date": "2025-11-28", "status": "DATA_GAP", "scored": False},
            ],
            "data_gap_count": 2,
            "expected_holiday_closures": closures,
            "early_close_sessions": [],
        },
    )
    seen = {}

    def _parity(_research, _audit, start, end, _expected, gap_dates):
        seen["window"] = (start, end)
        seen["gap_dates"] = set(gap_dates)
        return [], {}, [], []

    monkeypatch.setattr("research.afs0523_322_repro_wrapper._parity", _parity)
    result = _run_window(
        tmp_path / "research",
        tmp_path / "audit",
        start=REPRO_START,
        end=REPRO_END,
        expected_count=None,
    )
    assert seen["window"] == (REPRO_START, REPRO_END)
    assert result["holiday_closed_unscored"] == []
    assert result["holiday_closed_unscored_count"] == 0
    assert seen["gap_dates"] == {"2025-01-09", "2025-11-28"}


def test_reproduction_window_keeps_recorded_gaps_and_derives_34_from_recorded_comparisons():
    """34 is recorded comparisons divided by 9 (306/9). This test does not detect candidates."""
    from research.databento_322_bar_adapter import cme_equity_index_session

    artifact = json.loads(
        (
            ROOT
            / "docs/research-evidence/T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01/reproduction-gate.json"
        ).read_text(encoding="utf-8")
    )
    assert artifact["pass"] is True
    assert artifact["data_gap_count"] == 2
    assert [row["date"] for row in artifact["data_gaps"]] == ["2025-01-09", "2025-11-28"]
    assert artifact["comparisons"] == 306 == OUTCOME_RECORD_COUNT
    assert artifact["outcome_records"] == OUTCOME_RECORD_COUNT
    cells_per_candidate = OUTCOME_RECORD_COUNT // REPRO_EXPECTED_COUNT
    assert cells_per_candidate == 9
    assert OUTCOME_RECORD_COUNT % REPRO_EXPECTED_COUNT == 0
    candidate_count = artifact["comparisons"] // cells_per_candidate
    assert artifact["comparisons"] % cells_per_candidate == 0
    assert candidate_count == artifact["comparisons"] // 9 == REPRO_EXPECTED_COUNT == 34
    closures = []
    day = REPRO_START
    while day <= REPRO_END:
        if day.weekday() < 5:
            kind = cme_equity_index_session(day)
            if kind in {"closed", "early_close"}:
                closures.append({"date": day.isoformat(), "kind": kind})
        day += timedelta(days=1)
    closed = [item["date"] for item in closures if item["kind"] == "closed"]
    early = [item["date"] for item in closures if item["kind"] == "early_close"]
    assert closed == [
        "2024-07-04",
        "2024-11-28",
        "2024-12-25",
        "2025-01-01",
        "2025-04-18",
        "2025-07-04",
        "2025-11-27",
        "2025-12-25",
        "2026-01-01",
        "2026-04-03",
    ]
    assert "2025-12-26" not in closed
    report = {"data_gaps": artifact["data_gaps"], "expected_holiday_closures": closures}
    assert _holiday_closed_dates(report) == closed
    gate_unscored = _unscored_dates(report, include_holiday_closures=False)
    assert gate_unscored == {"2025-01-09", "2025-11-28"}
    assert set(closed).isdisjoint(gate_unscored)
    slice_unscored = _unscored_dates(report, include_holiday_closures=True)
    assert set(closed) <= slice_unscored
    gap_dates = {row["date"] for row in artifact["data_gaps"]}
    assert set(early).isdisjoint(slice_unscored - gap_dates)


def test_recorded_replay_stays_34_candidates_with_zero_strict_bracket_drops():
    """The committed replay is not re-detected. 306 comparisons and 0 mismatches stay."""
    evidence = ROOT / "docs/research-evidence/T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01"
    artifact = json.loads((evidence / "reproduction-gate.json").read_text(encoding="utf-8"))
    replay_path = evidence / "reproduction-replay-ledger.json"
    replay_bytes = replay_path.read_bytes()
    assert hashlib.sha256(replay_bytes).hexdigest() == artifact["replay_ledger_sha256"]
    replay = json.loads(replay_bytes)
    structural = compare_replay_structural(replay)
    assert structural["comparisons"] == 306
    assert structural["mismatches"] == []
    assert artifact["invalid_entry_bracket_count"] == 0
    assert artifact["invalid_entry_brackets"] == []
    assert artifact["gap_open_count"] == 0
    recorded = []
    for row in replay["ledger"]["1tick"]:
        assert row.get("gap_open") is not True
        recorded.append(
            {
                "date": row["date"],
                "direction": row["direction"],
                "gap_open": False,
                "entry_trigger": float(row["trigger"]),
                "entry_price": float(row["trigger"]),
                "stop": float(row["stop"]),
                "target": float(row["target"]),
            }
        )
    assert len(recorded) == artifact["comparisons"] // 9 == 34
    kept, invalid = partition_invalid_gap_opens(recorded)
    assert len(kept) == 34
    assert invalid == []
    assert SLICE_SCORING_AUTHORIZED is True


def test_close_delta_day_fails_when_the_scored_close_is_not_the_pinned_databento_close():
    reference = json.loads(LEDGER.read_text(encoding="utf-8"))
    _stamp_scored_databento_bars(reference["ledger"])
    for rows in reference["ledger"].values():
        for row in rows:
            if row["date"] == "2024-09-06":
                row["trigger_bar_close"] = float(row["trigger_bar_close"]) + 0.25
    structural = compare_replay_structural({"ledger": reference["ledger"]})
    assert structural["ok"] is False
    assert any(
        row["date"] == "2024-09-06" and row["field"] == "scored_bar" and row["actual"] == "close_mismatch"
        for row in structural["mismatches"]
    )


def test_scored_slice_stamps_the_evaluator_and_combine_waits_for_both(monkeypatch, tmp_path):
    _prepare_admitted_slice(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._git_head_and_clean",
        lambda _repo: ("ab" * 20, True),
    )
    ledger_rows = {
        "1tick": [],
        "2tick": [],
        "3tick": [
            {
                "date": "2019-06-03",
                "direction": "LONG",
                "stop": 985.0,
                "target": 1007.0,
                "models": {"ioc_close": {"status": "NO_FILL", "reason": "ENTRY_NOT_FILLED"}},
            }
        ],
    }
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._run_window",
        lambda *_args, **_kwargs: {
            "ledger": ledger_rows,
            "data_gaps": [],
            "data_gap_count": 0,
            "early_close_sessions": [],
        },
    )
    evaluator = ROOT / "research/afs0523_322_slice_evaluator.py"
    digest = hashlib.sha256(evaluator.read_bytes()).hexdigest()
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    mnq = run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="mnq-2019-05-to-2024-06",
    )
    assert mnq["evaluator_path"] == "research/afs0523_322_slice_evaluator.py"
    assert mnq["evaluator_sha256"] == digest
    assert mnq["decisive_cell"]["evaluator_sha256"] == digest
    assert mnq["decisive_cell"]["status"] == "INSUFFICIENT"
    assert mnq["decisive_cell"]["market"] == "MNQ"
    assert not (tmp_path / "slices" / "combine.attempt-01.json").exists()
    monkeypatch.setattr(
        "research.afs0523_322_repro_wrapper._refuse_slice_data_mismatch",
        lambda *_args, **_kwargs: None,
    )
    nq = run_declared_slices(
        Path("slice-research"),
        Path("slice-audit"),
        data_root=tmp_path,
        slice_id="nq-2010-06-to-2019-05",
    )
    assert nq["decisive_cell"]["market"] == "NQ"
    assert nq["evaluator_sha256"] == digest
    combine_path = tmp_path / "slices" / "combine.attempt-01.json"
    combine = json.loads(combine_path.read_text(encoding="utf-8"))
    assert combine["evaluator_sha256"] == digest
    assert combine["inputs"]["nq-2010-06-to-2019-05"]["evaluator_sha256"] == digest
    assert combine["inputs"]["mnq-2019-05-to-2024-06"]["evaluator_sha256"] == digest
    assert combine["inputs"]["nq-2010-06-to-2019-05"]["slice_artifact_sha256"]
    assert combine["verdict"]
    assert "VALIDATED" not in combine["verdict"]
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger
