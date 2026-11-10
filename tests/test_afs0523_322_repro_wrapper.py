"""Reproduction gate for the frozen 60M 3-2-2 population.

The gate reads the committed Oct 4 ledger. It does not copy those rows and it
does not call or edit check_repro().
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from config.futures_contracts import contract_economics
from research.afs0523_322_repro_wrapper import (
    DATA_MANIFEST_SHA256,
    _refuse_changed_gate_data,
    _same_data,
    REPRO_END,
    REPRO_START,
    TRIAL_ID,
    _tree_sha256,
    invalid_brackets_outside_data_gaps,
    partition_invalid_gap_opens,
    record_gate_review,
    run_declared_slices,
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
        SLICES,
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
        "SLICES": SLICES,
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
    with pytest.raises(RuntimeError, match="did not PASS"):
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
    assert _wrapper()["RUN_AUTHORIZED"] is True
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


def _call_gate(data_root: Path, research: Path, audit: Path):
    previous = os.environ.get("MAX_CONTRACTS_HARD_CAP")
    os.environ["MAX_CONTRACTS_HARD_CAP"] = "1"
    try:
        return run_reproduction_gate(
            data_root,
            research.relative_to(data_root),
            audit.relative_to(data_root),
        )
    finally:
        if previous is None:
            os.environ.pop("MAX_CONTRACTS_HARD_CAP", None)
        else:
            os.environ["MAX_CONTRACTS_HARD_CAP"] = previous


def _patch_gate_paths(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "reproduction-gate.json"
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_bytes((ROOT / "docs/research-trial-ledger.jsonl").read_bytes())
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.GATE_ARTIFACT", artifact)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    return artifact, ledger


def test_reproduction_gate_refuses_when_the_flag_is_false(monkeypatch, tmp_path):
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.RUN_AUTHORIZED", False)
    with pytest.raises(RuntimeError, match="RUN_AUTHORIZED"):
        run_reproduction_gate(tmp_path, Path("."), Path("."))


def test_reproduction_gate_refuses_a_day_file_outside_the_window(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2020, 1, 2))
    _bar_file(audit, date(2024, 8, 2))
    _pin_manifest(monkeypatch, tmp_path, _manifest(tmp_path, research, audit))
    with pytest.raises(RuntimeError, match="outside"):
        _call_gate(tmp_path, research, audit)


def test_reproduction_gate_refuses_a_checksum_or_tree_hash_mismatch(monkeypatch, tmp_path):
    research = tmp_path / "research"
    audit = tmp_path / "audit"
    _bar_file(research, date(2024, 8, 2))
    _bar_file(audit, date(2024, 8, 2))
    bad_checksum = _manifest(tmp_path, research, audit)
    bad_checksum["pulled_files"][0]["sha256"] = "0" * 64
    _pin_manifest(monkeypatch, tmp_path, bad_checksum)
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        _call_gate(tmp_path, research, audit)
    bad_tree = _manifest(tmp_path, research, audit)
    _repro(bad_tree)["research_15m_sha256"] = "0" * 64
    _pin_manifest(monkeypatch, tmp_path, bad_tree)
    with pytest.raises(RuntimeError, match="tree hash mismatch"):
        _call_gate(tmp_path, research, audit)


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
        _call_gate(tmp_path, research, audit)
    manifest = _manifest(tmp_path, research, audit)
    _pin_manifest(monkeypatch, tmp_path, manifest)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.DATA_MANIFEST_SHA256", "0" * 64)
    with pytest.raises(RuntimeError, match="manifest hash mismatch"):
        _call_gate(tmp_path, research, audit)
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_reproduction_gate_resolves_paths_under_data_root(monkeypatch, tmp_path):
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")
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
        _call_gate(tmp_path, research, audit)
    absolute = _manifest(tmp_path, research, audit)
    absolute["pulled_files"][0]["path"] = str(tmp_path / "pulled.dbn")
    _pin_manifest(monkeypatch, tmp_path, absolute)
    with pytest.raises(RuntimeError, match="escapes data_root"):
        _call_gate(tmp_path, research, audit)
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir()
    (outside / "secret").write_bytes(b"secret")
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)
    linked = _manifest(tmp_path, research, audit)
    linked["pulled_files"][0]["path"] = "escape/secret"
    _pin_manifest(monkeypatch, tmp_path, linked)
    with pytest.raises(RuntimeError, match="escapes data_root"):
        _call_gate(tmp_path, research, audit)
    manifest = _manifest(tmp_path, research, audit)
    _pin_manifest(monkeypatch, tmp_path, manifest)
    _patch_gate_paths(monkeypatch, tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
    result = _call_gate(tmp_path, research, audit)
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
    artifact, ledger = _patch_gate_paths(monkeypatch, tmp_path)
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
    result = _call_gate(tmp_path, research, audit)
    assert result["pass"] is False
    assert "expected-count" in result["error"]
    assert result["comparisons"] is None
    assert result["outcome_records"] == 306
    assert result["adapter_report"]["contracts"] == ["MNQH5"]
    assert result["data_gap_count"] >= 1
    assert any(item["date"] == day.isoformat() and item["scored"] is False for item in result["data_gaps"])
    written = json.loads(artifact.read_text(encoding="utf-8"))
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
    assert result["manifest_sha256"] == pin
    assert result["research_15m_sha256"] == _repro(manifest)["research_15m_sha256"]
    assert result["audit_5m_sha256"] == _repro(manifest)["audit_5m_sha256"]
    assert result["pulled_files"] == manifest["pulled_files"]
    assert len(result["prior_attempts"]) == 1
    assert result["prior_attempts"][0]["manifest_sha256"] == DATA_MANIFEST_SHA256
    assert result["prior_attempts"][0]["research_15m_sha256"] == "461384bc2c638e849ebcdbd4c1fed09fde79fbc9c48dee7656a1151c259e3339"
    assert written["manifest_sha256"] == pin
    again = _call_gate(tmp_path, research, audit)
    assert again["pass"] is False
    assert len(again["prior_attempts"]) == 2
    assert again["prior_attempts"][0]["manifest_sha256"] == DATA_MANIFEST_SHA256
    assert again["prior_attempts"][0]["research_15m_sha256"] == "461384bc2c638e849ebcdbd4c1fed09fde79fbc9c48dee7656a1151c259e3339"
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_slice_admission_refuses_a_pass_after_different_data(monkeypatch, tmp_path):
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
    result = _call_gate(tmp_path, research, audit)
    assert result["pass"] is False
    assert "not a runtime error" in result["error"]
    assert result["manifest_sha256"] == pin
    last = json.loads(ledger.read_text(encoding="utf-8").splitlines()[-1])
    assert last["event"] == "GATE"
    assert last["gate_pass"] is False
    assert last["manifest_sha256"] == pin
    written = json.loads(artifact.read_text(encoding="utf-8"))
    assert written["pass"] is False
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


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
        _call_gate(tmp_path, research, audit)
    _pin_manifest(monkeypatch, tmp_path, combined)
    with pytest.raises(RuntimeError, match="combined is report-only"):
        _call_gate(tmp_path, research, audit)
    with pytest.raises(RuntimeError, match="combined is report-only"):
        run_declared_slices(research, audit, slice_id="combined")
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_gate_review_admits_a_slice_without_scoring_and_rejects_a_different_artifact(monkeypatch, tmp_path):
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
    result = _call_gate(tmp_path, repro_research, repro_audit)
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
        if line.strip() and json.loads(line).get("event") in {"GATE", "GATE_REVIEW"}
    ]
    assert gate_events.count("GATE") == 1
    assert "GATE_REVIEW" in gate_events
    assert (ROOT / "docs/research-trial-ledger.jsonl").read_bytes() == real_ledger


def test_superseded_trial_stays_refused(monkeypatch, tmp_path):
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
    ledger.write_bytes((ROOT / "docs/research-trial-ledger.jsonl").read_bytes())
    artifact = tmp_path / "reproduction-gate.json"
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.TRIAL_LEDGER", ledger)
    monkeypatch.setattr("research.afs0523_322_repro_wrapper.GATE_ARTIFACT", artifact)
    before = ledger.read_bytes()
    real_ledger = (ROOT / "docs/research-trial-ledger.jsonl").read_bytes()
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


def test_recorded_gate_fail_is_the_pinned_download_and_not_different_data(tmp_path):
    artifact_path = (
        ROOT
        / "docs/research-evidence/T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01/reproduction-gate.json"
    )
    digest = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    assert digest == "ede35c326057ba4fa39cebfeaad8851d97ad60df9de1c2ddedd9b888dcd1dfeb"
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["manifest_sha256"] == DATA_MANIFEST_SHA256
    assert artifact["research_15m_sha256"] == "461384bc2c638e849ebcdbd4c1fed09fde79fbc9c48dee7656a1151c259e3339"
    assert artifact["audit_5m_sha256"] == "a08d909274fa5a06e9234b1c0975977d91a00b1f749c0560fd6d41859410c1e3"
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
    assert gate["gate_artifact_sha256"] == digest
    assert _same_data(gate, artifact)
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
        "trial_id": TRIAL_ID,
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
