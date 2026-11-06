import json
import sqlite3
from pathlib import Path

from ops import options_epoch_pnl_report as rep


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE options_shadow_journal (
          id INTEGER PRIMARY KEY,
          timestamp TEXT NOT NULL,
          ticker TEXT NOT NULL,
          direction TEXT NOT NULL,
          status TEXT NOT NULL,
          setup_inputs_json TEXT NOT NULL,
          selected_contract_json TEXT NOT NULL,
          outcome_json TEXT NOT NULL
        )
        """
    )
    rows = [
        (99, "2026-09-21T19:22:59+00:00", "OLD", "LONG", "LOSS", {}, {"paper_evidence_lane": "ACTIVE"}, {"pnl_dollars": -999}),
        (100, "2026-09-21T19:23:01.426685+00:00", "MRK", "LONG", "WIN", {}, {"paper_evidence_lane": "ACTIVE", "risk_budget_consumed": True}, {"pnl_dollars": -15, "resolved_at": "2026-09-22T14:00:00+00:00"}),
        (101, "2026-09-22T14:00:00+00:00", "NVDA", "LONG", "WIN", {}, {"paper_evidence_lane": "ACTIVE", "risk_budget_consumed": True}, {"pnl_dollars": 35}),
        (102, "2026-09-22T15:00:00+00:00", "SPY", "LONG", "LOSS", {}, {"paper_evidence_lane": "ACTIVE", "risk_budget_consumed": True}, {"pnl_dollars": -100}),
        (103, "2026-09-22T16:00:00+00:00", "JPM", "LONG", "OPEN", {}, {"paper_evidence_lane": "ACTIVE", "risk_budget_consumed": True}, {}),
        (104, "2026-09-22T16:05:00+00:00", "IWM", "LONG", "TARGET_CONSUMED_AT_ENTRY", {}, {"paper_evidence_lane": "ACTIVE", "risk_budget_consumed": True}, {}),
        (105, "2026-09-23T14:00:00+00:00", "AAPL", "LONG", "WIN", {}, {"paper_evidence_lane": "COUNTERFACTUAL", "risk_budget_consumed": False, "counterfactual_filter_reason": "ENTRY_LATE:price_past_target"}, {"pnl_dollars": 20}),
        (106, "2026-09-23T14:05:00+00:00", "BAC", "LONG", "LOSS", {}, {"paper_evidence_lane": "COUNTERFACTUAL", "risk_budget_consumed": False, "counterfactual_filter_reason": "ENTRY_LATE:price_past_target"}, {"pnl_dollars": -50}),
        (107, "2026-09-23T14:10:00+00:00", "QQQ", "SHORT", "LOSS", {"counterfactual_filter_reason": "market_not_aligned"}, {"risk_budget_consumed": False}, {"pnl_dollars": -10}),
        (108, "2026-09-23T14:15:00+00:00", "TLT", "SHORT", "OPEN", {}, {"paper_evidence_lane": "COUNTERFACTUAL", "risk_budget_consumed": False, "counterfactual_filter_reason": "market_not_aligned"}, {}),
        (109, "2026-09-23T14:20:00+00:00", "GE", "LONG", "STOP_CONSUMED_AT_ENTRY", {}, {"paper_evidence_lane": "COUNTERFACTUAL", "risk_budget_consumed": False}, {}),
    ]
    for row in rows:
        conn.execute(
            "INSERT INTO options_shadow_journal VALUES (?,?,?,?,?,?,?,?)",
            (
                row[0], row[1], row[2], row[3], row[4],
                json.dumps(row[5]), json.dumps(row[6]), json.dumps(row[7]),
            ),
        )
    conn.commit()
    conn.close()


def _epoch(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "policy_id": "OPTIONS_PAPER_V1",
                "cohort": "V1-EPOCH-3",
                "reason": "DAILY_SCORER_ALIGNMENT",
                "deployed_sha": "abc123",
                "epoch_start": "2026-09-21T19:23:01.426685+00:00",
                "cohort_boundary": {"options_shadow_journal_first_id": 100},
            }
        ),
        encoding="utf-8",
    )


def test_epoch_only_active_pnl_and_financial_outcome(tmp_path):
    db = tmp_path / "options.sqlite"
    epoch = tmp_path / "epoch.json"
    _make_db(db)
    _epoch(epoch)

    meta = rep.load_epoch(epoch)
    rows = rep.load_rows(db, first_shadow_id=meta["first_shadow_id"], epoch_start=meta["epoch_start"])
    report = rep.build_report(rows, meta)

    assert report["rows_loaded"] == 10
    active = report["active"]
    assert active["priced_closed"] == 3
    assert active["structural_target_events"] == 2
    assert active["structural_stop_events"] == 1
    assert active["financial_profits"] == 1
    assert active["financial_losses"] == 2
    assert active["open"] == 1
    assert active["entry_consumed_non_outcomes"] == 1
    assert active["pnl_usd"] == -80.0


def test_counterfactuals_group_by_exact_filter_reason(tmp_path):
    db = tmp_path / "options.sqlite"
    epoch = tmp_path / "epoch.json"
    _make_db(db)
    _epoch(epoch)

    meta = rep.load_epoch(epoch)
    rows = rep.load_rows(db, first_shadow_id=meta["first_shadow_id"], epoch_start=meta["epoch_start"])
    cf = rep.build_report(rows, meta)["counterfactual"]

    assert cf["trade_population"] is False
    assert cf["closed_priced_observations"] == 3
    assert cf["observed_pnl_usd"] == -40.0
    groups = {x["filter_reason"]: x for x in cf["by_filter_reason"]}
    late = groups["ENTRY_LATE:price_past_target"]
    assert late["closed_priced_observations"] == 2
    assert late["priced_positive"] == 1
    assert late["priced_negative"] == 1
    assert late["observed_pnl_usd"] == -30.0
    market = groups["market_not_aligned"]
    assert market["closed_priced_observations"] == 1
    assert market["observed_pnl_usd"] == -10.0
    unknown = groups["UNSPECIFIED"]
    assert unknown["entry_consumed_non_outcomes"] == 1
    assert unknown["closed_priced_observations"] == 0


def test_text_labels_counterfactual_as_observation_not_trade(tmp_path):
    db = tmp_path / "options.sqlite"
    epoch = tmp_path / "epoch.json"
    _make_db(db)
    _epoch(epoch)
    meta = rep.load_epoch(epoch)
    rows = rep.load_rows(db, first_shadow_id=meta["first_shadow_id"], epoch_start=meta["epoch_start"])
    text_output = rep.format_text(rep.build_report(rows, meta))
    assert "V1-EPOCH-3" in text_output
    assert "ACTIVE: 3 priced closed" in text_output
    assert "$-80.00" in text_output
    assert "descriptive only; not trades/expectancy" in text_output
    assert "ENTRY_LATE:price_past_target" in text_output
