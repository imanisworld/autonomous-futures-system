"""Synthetic regression tests: no real account, broker, sealed research or VPS evidence."""
import json
import sqlite3
from datetime import datetime, timezone

import pytest

from ops import lifetime_performance_report as rep


def _options_fixture(tmp_path):
    db = tmp_path / "options.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE options_shadow_journal (id INTEGER PRIMARY KEY, timestamp TEXT, "
            "ticker TEXT, status TEXT, setup_inputs_json TEXT, "
            "selected_contract_json TEXT, outcome_json TEXT)"
        )
        rows = [
            (9, "2026-09-14T16:00:00Z", "SPY", "LOSS", "ACTIVE", -20),
            (10, "2026-09-15T16:50:00Z", "SPY", "WIN", "ACTIVE", -12),
            (11, "2026-09-16T16:47:47Z", "SPY", "LOSS", "ACTIVE", 30),
            (12, "2026-09-17T10:00:00Z", "AAPL", "WIN", "COUNTERFACTUAL", 999),
            (13, "2026-09-17T11:00:00Z", "QQQ", "WIN", "ACTIVE", None),
            (20, "2026-09-21T19:23:02Z", "SPY", "WIN", "ACTIVE", 8),
            (21, "2026-09-22T14:00:00Z", "QQQ", "OPEN", "ACTIVE", None),
            (22, "2026-09-22T14:05:00Z", "AAPL", "WIN", "", 3),
        ]
        for rid, stamp, ticker, status, lane, pnl in rows:
            conn.execute("INSERT INTO options_shadow_journal VALUES (?,?,?,?,?,?,?)",
                         (rid, stamp, ticker, status, "{}", json.dumps({"paper_evidence_lane": lane, "option_mark": 2, "contracts": 1}),
                          json.dumps({"pnl_dollars": pnl, "exit_mark": 2.0 + float(pnl) / 100.0} if pnl is not None else {})))
    epoch = tmp_path / "epochs.json"
    epoch.write_text(json.dumps({
        "cohort": "V1-EPOCH-3", "epoch_start": "2026-09-21T19:23:01Z",
        "cohort_boundary": {"options_shadow_journal_first_id": 20},
        "previous_epochs": [
            {"cohort": "V1-EPOCH-1", "epoch_start": "2026-09-15T16:50:00Z",
             "cohort_boundary": {"options_shadow_journal_first_id": 10}},
            {"cohort": "V1-EPOCH-2", "epoch_start": "2026-09-16T16:47:46Z",
             "cohort_boundary": {"options_shadow_journal_first_id": 11}},
        ],
    }))
    return db, epoch


def _journal_fixture(tmp_path):
    folder = tmp_path / "journals"
    folder.mkdir()
    earlier = [
        {"decision": "TRADE", "ts": "2026-09-15T10:00:00Z", "instrument": "MNQ",
         "paper_order_id": "a", "risk_check": {"result": "APPROVED"},
         "setup": {"strategy": "orb_reclaim", "direction": "LONG", "contracts": 1}},
        {"decision": "TRADE", "ts": "2026-09-16T10:00:00Z", "instrument": "MES",
         "paper_order_id": "b", "risk_check": {"result": "APPROVED"},
         "setup": {"strategy": "vwap_hold", "direction": "LONG", "contracts": 1}},
        {"decision": "TRADE", "ts": "2026-09-16T11:00:00Z", "instrument": "MNQ",
         "paper_order_id": "open", "risk_check": {"result": "APPROVED"},
         "setup": {"strategy": "orb_reclaim"}},
        {"type": "SHADOW_OUTCOME", "instrument": "MGC", "candidate_key": "c",
         "strategy": "shadow", "evidence_epoch": "e1",
         "shadow_outcome": {"result": "WIN", "pnl_ticks": 12}},
    ]
    later = [
        {"type": "OUTCOME", "outcome": {"paper_order_id": "a", "result": "WIN", "entry_price": 20000, "exit_price": 20060, "contracts": 1, "pnl_dollars": 120}},
        {"type": "OUTCOME", "outcome": {"paper_order_id": "b", "result": "LOSS", "entry_price": 5000, "exit_price": 4994, "contracts": 1, "pnl_dollars": -30}},
        {"type": "SHADOW_OUTCOME", "instrument": "MGC", "candidate_key": "c",
         "strategy": "shadow", "evidence_epoch": "e1",
         "shadow_outcome": {"result": "LOSS", "pnl_ticks": -5}},
    ]
    (folder / "journal_2026-09-15.jsonl").write_text("".join(json.dumps(x) + "\n" for x in earlier))
    (folder / "journal_2026-09-17.jsonl").write_text("".join(json.dumps(x) + "\n" for x in later))
    return folder


def test_lifetime_separates_futures_option_epochs_and_shadow(tmp_path):
    folder = _journal_fixture(tmp_path)
    db, epoch = _options_fixture(tmp_path)
    report = rep.build_report(folder, db, epoch)
    futures = report["futures"]
    assert futures["paper_trade_lifetime"]["complete_pnl_usd"] == 90.0
    assert futures["paper_by_ticker"]["MNQ"]["complete_pnl_usd"] == 120.0
    assert futures["paper_by_ticker"]["MES"]["financial_losses"] == 1
    assert futures["paper_by_change"]["CHANGE_BOUNDARIES_UNVERIFIED"]["records"] == 3
    assert futures["identity_problems"]["unresolved_with_identity"] == 1
    assert futures["shadow_observations_by_ticker"]["MGC"]["pnl_ticks_priced_subtotal"] == -5
    assert report["lifetime_source_coverage_verified"] is False
    assert report["options"]["lifetime_active"]["priced_subtotal_usd"] == 6
    assert report["options"]["lifetime_active"]["complete_pnl_usd"] is None
    assert report["options"]["active_by_ticker"]["SPY"]["complete_pnl_usd"] == 6
    assert report["options"]["active_by_epoch"]["V1-EPOCH-1"]["complete_pnl_usd"] == -12
    assert report["options"]["active_by_epoch"]["V1-EPOCH-2"]["complete_pnl_usd"] is None
    assert report["options"]["active_by_epoch"]["V1-EPOCH-3"]["complete_pnl_usd"] == 8
    assert report["options"]["active_by_epoch"]["BEFORE_REGISTERED_EPOCHS"]["complete_pnl_usd"] == -20
    assert report["options"]["counterfactual_excluded"] == 1
    assert report["options"]["unknown_lane_excluded"] == 1
    assert report["options"]["reconciled_within_supplied_options_snapshot"] is False


def test_futures_change_markers_require_evidence_and_use_entry_time(tmp_path):
    folder = _journal_fixture(tmp_path)
    journal, errors, _ = rep._read_journals(folder)
    assert errors == 0
    manifest = tmp_path / "futures_changes.json"
    manifest.write_text(json.dumps({"changes": [
        {"instrument": "MNQ", "label": "NEW_RULE", "effective_utc": "2026-09-16T00:00:00Z",
         "evidence_ref": "verified install snapshot"},
    ]}))
    changes = rep._futures_changes(manifest)
    by = rep.futures_report(journal, changes=changes)["paper_by_ticker_and_change"]
    assert by["MNQ"]["BEFORE_FIRST_DOCUMENTED_CHANGE"]["complete_pnl_usd"] == 120
    assert by["MNQ"]["NEW_RULE"]["records"] == 1
    assert by["MES"]["CHANGE_BOUNDARIES_UNVERIFIED"]["complete_pnl_usd"] == -30
    manifest.write_text(json.dumps({"changes": [{"instrument": "MNQ",
        "label": "unsourced", "effective_utc": "2026-09-16T00:00:00Z"}]}))
    with pytest.raises(ValueError, match="evidence_ref"):
        rep._futures_changes(manifest)


def test_ambiguity_and_nonfinite_prices_are_never_assumed_valid(tmp_path):
    folder = _journal_fixture(tmp_path)
    with (folder / "journal_2026-09-17.jsonl").open("a") as file:
        file.write(json.dumps({"type": "OUTCOME", "outcome": {
            "paper_order_id": "a", "result": "LOSS", "pnl_dollars": -500}}) + "\n")
    journal, _, _ = rep._read_journals(folder)
    actual = rep.futures_report(journal)
    assert actual["identity_problems"]["ambiguous_duplicate_id_decisions"] == 1
    assert actual["paper_trade_lifetime"]["priced_subtotal_usd"] == -30
    assert actual["reconciled_within_supplied_journals"] is False
    assert rep._money(float("nan")) is None
    assert rep._money(float("inf")) is None
    result = rep._totals([{"status": "WIN", "pnl_usd": None}])
    assert result["complete_pnl_usd"] is None
    assert result["unpriced_closed"] == 1


def test_epoch_boundary_mismatch_is_counted_not_relabelled(tmp_path):
    db, epoch = _options_fixture(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE options_shadow_journal SET timestamp='2026-09-22T10:00:00Z' WHERE id=11")
    out = rep.options_report(db, epoch)
    assert out["epoch_boundary_conflicts"] == 1
    assert out["active_by_epoch"]["BOUNDARY_CONFLICT"]["records"] == 1
    assert out["reconciled_within_supplied_options_snapshot"] is False


def test_absent_inputs_fail_and_no_sqlite_is_created(tmp_path):
    with pytest.raises(FileNotFoundError):
        rep.options_report(tmp_path / "missing.sqlite", tmp_path / "epochs.json")
    assert not (tmp_path / "missing.sqlite").exists()
    with pytest.raises(FileNotFoundError):
        rep._read_journals(tmp_path / "missing")


def test_cli_reports_partial_evidence_and_nonzero_status(tmp_path, capsys):
    folder = _journal_fixture(tmp_path)
    db, epoch = _options_fixture(tmp_path)
    code = rep.main(["--journal-dir", str(folder), "--options-db", str(db),
                     "--options-epochs", str(epoch)])
    assert code == 2
    printed = json.loads(capsys.readouterr().out)
    assert printed["options"]["lifetime_active"]["pnl_complete"] is False
    assert printed["futures"]["paper_trade_lifetime"]["complete_pnl_usd"] == 90.0

def test_text_view_keeps_unpriced_and_unverified_labels(tmp_path, capsys):
    folder = _journal_fixture(tmp_path)
    db, epoch = _options_fixture(tmp_path)
    code = rep.main(["--journal-dir", str(folder), "--options-db", str(db),
                     "--options-epochs", str(epoch), "--text"])
    assert code == 2
    text = capsys.readouterr().out
    assert "MNQ: $+120.00" in text
    assert "SPY: $+6.00" in text
    assert "QQQ: INCOMPLETE" in text
    assert "CHANGE_BOUNDARIES_UNVERIFIED" in text
    assert "NOT YET INDEPENDENTLY VERIFIED" in text
    assert "MGC: -5.0 recorded ticks" in text
    assert "\nOptions ACTIVE" in text
