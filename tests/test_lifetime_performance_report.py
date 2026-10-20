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
        {"decision": "TRADE", "account_mode": "PAPER", "ts": "2026-09-15T10:00:00Z", "instrument": "MNQ",
         "paper_order_id": "a", "risk_check": {"result": "APPROVED"},
         "setup": {"strategy": "orb_reclaim", "direction": "LONG", "contracts": 1}},
        {"decision": "TRADE", "account_mode": "PAPER", "ts": "2026-09-16T10:00:00Z", "instrument": "MES",
         "paper_order_id": "b", "risk_check": {"result": "APPROVED"},
         "setup": {"strategy": "vwap_hold", "direction": "LONG", "contracts": 1}},
        {"decision": "TRADE", "account_mode": "PAPER", "ts": "2026-09-16T11:00:00Z", "instrument": "MNQ",
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

def test_timeout_is_not_ignored():
    rows = [{"status": "WIN", "pnl_usd": 10, "recorded_pnl_usd": 10},
            {"status": "TIMEOUT", "pnl_usd": None, "recorded_pnl_usd": 99}]
    result = rep._totals(rows)
    assert result["closed"] == 2
    assert result["unpriced_closed"] == 1
    assert result["complete_pnl_usd"] is None

def test_independent_price_math_and_fee_status():
    assert rep._futures_reprice(
        {"instrument": "MNQ", "setup": {"direction": "SHORT", "contracts": 2}},
        {"entry_price": 20000, "exit_price": 20010, "contracts": 2}) == -40.0
    assert rep._options_reprice(
        {"option_mark": 2, "contracts": 2}, {"exit_mark": 1.5}) == -100.0
    report = rep._totals([{"status": "TIMEOUT", "pnl_usd": -40,
                           "recorded_pnl_usd": 999}])
    assert report["recorded_pnl_discrepancies"] == 1
    assert report["priced_subtotal_usd"] == -40
    assert report["complete_pnl_usd"] is None
    assert report["fees_included"] is False
    assert report["net_after_fees_usd"] is None

def test_real_futures_timeout_price_and_wrong_recorded_dollars(tmp_path):
    folder = _journal_fixture(tmp_path)
    rows, _, _ = rep._read_journals(folder)
    rows += [
        {"decision": "TRADE", "instrument": "MNQ", "paper_order_id": "t",
         "setup": {"direction": "SHORT", "contracts": 2}, "risk_check": {"result": "APPROVED"}},
        {"type": "OUTCOME", "outcome": {"paper_order_id": "t", "result": "TIMEOUT",
         "entry_price": 20000, "exit_price": 20010, "contracts": 2, "pnl_dollars": 0}},
    ]
    result = rep.futures_report(rows)["paper_by_ticker"]["MNQ"]
    assert result["closed"] == 2
    assert result["priced_subtotal_usd"] == 80
    assert result["recorded_pnl_discrepancies"] == 1
    assert result["pnl_complete"] is False

def test_options_timeout_and_fee_caveat_from_real_sqlite(tmp_path):
    db, epoch = _options_fixture(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO options_shadow_journal VALUES (?,?,?,?,?,?,?)",
                     (23, "2026-09-22T15:00:00Z", "SPY", "TIMEOUT", "{}",
                      json.dumps({"paper_evidence_lane": "ACTIVE", "option_mark": 2,
                                  "contracts": 2}),
                      json.dumps({"exit_mark": 1.5, "pnl_dollars": -10})))
    out = rep.options_report(db, epoch)
    spy = out["active_by_ticker"]["SPY"]
    assert spy["closed"] == 5
    assert spy["priced_subtotal_usd"] == -94
    assert spy["recorded_pnl_discrepancies"] == 1
    assert spy["pnl_complete"] is False
    assert spy["fees_included"] is False
    assert spy["net_after_fees_usd"] is None

def test_cancelled_requires_proven_no_fill_to_exclude():
    trade = {"decision": "TRADE", "paper_order_id": "c", "instrument": "MES",
             "risk_check": {"result": "APPROVED"}, "setup": {"direction": "LONG"}}
    with_proof = {"type": "OUTCOME", "outcome": {
        "paper_order_id": "c", "result": "CANCELLED", "no_fill_reason": "ENTRY_NOT_FILLED"}}
    report = rep.futures_report([trade, with_proof])["paper_trade_lifetime"]
    assert report["closed"] == 0
    assert report["other_or_non_outcome"] == 1
    without_proof = {"type": "OUTCOME", "outcome": {
        "paper_order_id": "c", "result": "CANCELLED"}}
    uncertain = rep.futures_report([trade, without_proof])["paper_trade_lifetime"]
    assert uncertain["closed"] == 1
    assert uncertain["unpriced_closed"] == 1
    assert uncertain["pnl_complete"] is False


def test_successful_calculations_cannot_claim_verified_inception(monkeypatch, capsys, tmp_path):
    # Calculation completeness on supplied copies is not historical coverage proof.
    synthetic = {
        "parse_complete": True,
        "since_inception_verified": False,
        "lifetime_source_coverage_verified": False,
        "futures": {"reconciled_within_supplied_journals": True,
                    "paper_trade_lifetime": {"pnl_complete": True}},
        "options": {"reconciled_within_supplied_options_snapshot": True,
                    "lifetime_active": {"pnl_complete": True}},
    }
    monkeypatch.setattr(rep, "build_report", lambda *args: synthetic)
    result = rep.main(["--journal-dir", str(tmp_path), "--options-db",
                       str(tmp_path / "synthetic.db")])
    output = json.loads(capsys.readouterr().out)
    assert output["since_inception_verified"] is False
    assert result == 2


def test_options_sell_to_open_blocks_buy_premium_arithmetic(tmp_path):
    db, epochs = _options_fixture(tmp_path)
    with sqlite3.connect(db) as con:
        selected = {"paper_evidence_lane": "ACTIVE", "option_mark": 2,
                    "contracts": 1, "opening_side": "SELL_TO_OPEN"}
        con.execute("UPDATE options_shadow_journal SET selected_contract_json=? WHERE id=20",
                    (json.dumps(selected),))
    actual = rep.options_report(db, epochs)
    assert actual["sell_to_open_unsupported"] == 1
    assert actual["active_by_ticker"]["SPY"]["unpriced_closed"] == 1
    assert actual["active_by_ticker"]["SPY"]["complete_pnl_usd"] is None
    assert actual["reconciled_within_supplied_options_snapshot"] is False
    assert rep._opening_side({"option_type": "PUT"}, {"direction": "SHORT"}) == "UNSTAMPED_BUY_ONLY_ASSUMPTION"
    assert rep._opening_side({"entry_side": "BUY_TO_OPEN"}, {}) == "BUY_TO_OPEN"


def test_journal_account_mode_is_not_assumed_paper():
    assert rep._execution_mode({}) == "MODE_UNSTAMPED"
    assert rep._execution_mode({"account_mode": "paper"}) == "PAPER"
    assert rep._execution_mode({"context": {"account_mode": "demo"}}) == "DEMO"
    assert rep._execution_mode({"broker_mode": "live"}) == "LIVE_EXCLUDED"
    d = {"decision": "TRADE", "paper_order_id": "L", "instrument": "MNQ",
         "account_mode": "LIVE", "risk_check": {"result": "APPROVED"},
         "setup": {"direction": "LONG"}}
    o = {"type": "OUTCOME", "outcome": {"paper_order_id": "L", "result": "WIN",
         "entry_price": 20000, "exit_price": 20050, "contracts": 1, "pnl_dollars": 100}}
    result = rep.futures_report([d, o])
    assert result["explicit_live_rows_excluded_from_paper"] == 1
    assert result["paper_trade_lifetime"]["records"] == 0
    assert result["reconciled_within_supplied_journals"] is False


def test_cohort_comparison_stats_refuse_unknown_exit_order():
    rows = [{"status": "WIN", "pnl_usd": 100, "recorded_pnl_usd": 100,
             "ts": "2026-09-01T13:00:00Z", "closed_at_utc": "2026-09-01T14:00:00Z"},
            {"status": "LOSS", "pnl_usd": -75, "recorded_pnl_usd": -75,
             "ts": "2026-09-02T13:00:00Z", "closed_at_utc": "2026-09-02T14:00:00Z"}]
    t = rep._totals(rows)
    assert t["closed"] == 2
    assert t["gross_avg_per_closed_usd"] == 12.5
    assert t["priced_win_rate_percent"] == 50
    assert t["realized_gross_closed_trade_max_drawdown_usd"] == 75
    assert t["entry_utc_span"]["distinct_utc_dates"] == 2
    rows[1]["closed_at_utc"] = None
    assert rep._totals(rows)["realized_gross_closed_trade_max_drawdown_usd"] is None
    rows[1]["closed_at_utc"] = rows[0]["closed_at_utc"]
    assert rep._totals(rows)["realized_gross_closed_trade_max_drawdown_usd"] is None


def test_no_closed_trades_cannot_claim_zero_realized_drawdown():
    result = rep._totals([{"status": "OPEN", "pnl_usd": None}])
    assert result["closed"] == 0
    assert result["gross_avg_per_closed_usd"] is None
    assert result["realized_gross_closed_trade_max_drawdown_usd"] is None


def test_conflicting_or_unrecognized_option_open_intent_blocks_pricing(tmp_path):
    assert rep._opening_side({"position_intent": "BUY_TO_CLOSE"}, {}) == "UNRECOGNIZED_EXPLICIT_OPEN_SIDE"
    assert rep._opening_side({"position_intent": "BUY_TO_OPEN",
                               "opening_side": "SELL_TO_OPEN"}, {}) == "CONFLICTING_OPEN_SIDE"
    db, epochs = _options_fixture(tmp_path)
    with sqlite3.connect(db) as con:
        selected = {"paper_evidence_lane": "ACTIVE", "option_mark": 2,
                    "contracts": 1, "position_intent": "BUY_TO_CLOSE"}
        con.execute("UPDATE options_shadow_journal SET selected_contract_json=? WHERE id=20",
                    (json.dumps(selected),))
    report = rep.options_report(db, epochs)
    assert report["unrecognized_or_conflicting_open_side"] == 1
    assert report["sell_to_open_unsupported"] == 0
    assert report["active_by_ticker"]["SPY"]["unpriced_closed"] == 1
    assert report["reconciled_within_supplied_options_snapshot"] is False


def test_unverified_mode_makes_only_affected_futures_ticker_incomplete():
    def trade(order_id, ticker, mode):
        row = {"decision": "TRADE", "paper_order_id": order_id,
               "instrument": ticker, "risk_check": {"result": "APPROVED"},
               "setup": {"direction": "LONG", "contracts": 1},
               "ts": "2026-09-16T13:00:00Z"}
        if mode is not None:
            row["account_mode"] = mode
        return row

    def outcome(order_id, ticker):
        # One tick in MNQ is $0.50; one tick in MES is $1.25.
        tick = 0.25
        recorded = 0.50 if ticker == "MNQ" else 1.25
        return {"type": "OUTCOME", "ts": "2026-09-16T14:00:00Z",
                "outcome": {"paper_order_id": order_id, "result": "WIN",
                            "entry_price": 20000, "exit_price": 20000 + tick,
                            "contracts": 1, "pnl_dollars": recorded}}

    journal = [
        trade("mnq1", "MNQ", "PAPER"), outcome("mnq1", "MNQ"),
        trade("mnq2", "MNQ", None), outcome("mnq2", "MNQ"),
        trade("mes1", "MES", "DEMO"), outcome("mes1", "MES"),
    ]
    result = rep.futures_report(journal)
    mnq = result["paper_by_ticker"]["MNQ"]
    mes = result["paper_by_ticker"]["MES"]
    assert mnq["priced_subtotal_usd"] == 1.0
    assert mnq["unpriced_closed"] == 0
    assert mnq["unverified_execution_mode_rows"] == 1
    assert mnq["pnl_complete"] is False
    assert mnq["complete_pnl_usd"] is None
    assert result["paper_by_ticker_and_change"]["MNQ"]["CHANGE_BOUNDARIES_UNVERIFIED"]["complete_pnl_usd"] is None
    assert result["paper_trade_lifetime"]["complete_pnl_usd"] is None

    assert mes["unverified_execution_mode_rows"] == 0
    assert mes["pnl_complete"] is True
    assert mes["complete_pnl_usd"] == 1.25
    assert result["journal_by_execution_mode"]["MODE_UNSTAMPED"]["pnl_complete"] is False
    assert result["journal_by_execution_mode"]["DEMO"]["pnl_complete"] is True


def test_incomplete_label_names_unverified_mode_without_dropping_priced_subset():
    sample = rep._totals([
        {"status": "WIN", "pnl_usd": 10, "recorded_pnl_usd": 10,
         "execution_mode": "MODE_UNSTAMPED"},
        {"status": "LOSS", "pnl_usd": -3, "recorded_pnl_usd": -3,
         "execution_mode": "PAPER"},
    ])
    assert sample["unverified_execution_mode_rows"] == 1
    assert sample["priced_subtotal_usd"] == 7
    assert sample["complete_pnl_usd"] is None
    blank = rep._totals([{"status": "OPEN", "pnl_usd": None,
                           "execution_mode": "UNRECOGNIZED_MODE"}])
    assert blank["unverified_execution_mode_rows"] == 1
    assert blank["pnl_complete"] is False
    assert rep._totals([{"status": "WIN", "pnl_usd": 1,
                         "recorded_pnl_usd": 1}])["unverified_execution_mode_rows"] == 0


def test_human_report_marks_unverified_ticker_incomplete(tmp_path):
    journal_dir = _journal_fixture(tmp_path)
    options_db, epochs = _options_fixture(tmp_path)
    rows, malformed, _ = rep._read_journals(journal_dir)
    assert malformed == 0
    for row in rows:
        if row.get("paper_order_id") == "a":
            row.pop("account_mode", None)
    report = {
        "futures": rep.futures_report(rows),
        "options": rep.options_report(options_db, epochs),
    }
    rendered = rep.format_human(report)
    assert "MNQ: INCOMPLETE" in rendered
    assert "1 unverified paper/demo mode" in rendered
    assert "MES: $-30.00" in rendered
