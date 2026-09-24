"""ops/options_daily_pnl_report.py — one New York day of options scanner paper
rows in dollars. Paper and what-if lanes must never mix."""
from __future__ import annotations

import json
import sqlite3
from datetime import date

from ops import options_daily_pnl_report as odp

DAY = date(2026, 9, 23)
CF = {"paper_evidence_lane": "COUNTERFACTUAL"}


def _db(tmp_path, journal, blocks=(), scans=()):
    path = tmp_path / "options_scanner.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE options_shadow_journal (id INTEGER PRIMARY KEY, timestamp TEXT, status TEXT, "
        "selected_contract_json TEXT, outcome_json TEXT)"
    )
    conn.execute("CREATE TABLE options_episode_blocks (blocked_at TEXT, reason TEXT)")
    conn.execute("CREATE TABLE scans (timestamp TEXT, alert_sent INTEGER)")
    for ts, status, contract, outcome in journal:
        conn.execute(
            "INSERT INTO options_shadow_journal (timestamp, status, selected_contract_json, outcome_json) VALUES (?,?,?,?)",
            (ts, status, json.dumps(contract, sort_keys=True), json.dumps(outcome)),
        )
    conn.executemany("INSERT INTO options_episode_blocks VALUES (?,?)", blocks)
    conn.executemany("INSERT INTO scans VALUES (?,?)", scans)
    conn.commit()
    conn.close()
    return path


def test_paper_and_whatif_lanes_stay_separate(tmp_path):
    path = _db(tmp_path, [
        # paper: opened today, closed today (resolved_at is ET)
        ("2026-09-23T14:00:00+00:00", "WIN", {}, {"pnl_dollars": 80.0, "resolved_at": "2026-09-23T15:30:00-04:00"}),
        # paper: opened earlier, closed today
        ("2026-09-21T14:00:00+00:00", "LOSS", {}, {"pnl_dollars": -124.0, "resolved_at": "2026-09-23T10:00:00-04:00"}),
        # paper: closed on another day -> all-time only
        ("2026-09-18T14:00:00+00:00", "WIN", {}, {"pnl_dollars": 50.0, "resolved_at": "2026-09-18T15:00:00-04:00"}),
        ("2026-09-23T15:00:00+00:00", "OPEN", {}, {}),
        ("2026-09-23T15:00:00+00:00", "REJECTED", {}, {}),           # never counted
        # what-if
        ("2026-09-23T16:00:00+00:00", "WIN", CF, {"pnl_dollars": 30.0, "resolved_at": "2026-09-23T13:00:00-04:00"}),
        ("2026-09-23T16:00:00+00:00", "TARGET_CONSUMED_AT_ENTRY", CF, {}),
        # 00:30Z on the 24th is still the 23rd in New York
        ("2026-09-24T00:30:00+00:00", "LOSS", CF, {"pnl_dollars": -5.0, "resolved_at": "2026-09-23T20:40:00-04:00"}),
    ], blocks=[
        ("2026-09-23T14:05:00+00:00", "ENTRY_LATE:price_past_target"),
        ("2026-09-23T14:06:00+00:00", "ENTRY_LATE:remaining_rr_0.45_below_1.00"),
        ("2026-09-23T14:07:00+00:00", "ENTRY_LATE:remaining_rr_0.07_below_1.00"),
        ("2026-09-22T14:07:00+00:00", "ENTRY_LATE:price_past_target"),
    ], scans=[("2026-09-23T14:00:00+00:00", 1), ("2026-09-23T14:30:00+00:00", 0)])
    rep = odp.build_report(odp.load_rows(path, DAY), DAY)
    p = rep["paper"]
    assert (p["opened"], p["closed"], p["wins"], p["losses"], p["open"]) == (2, 2, 1, 1, 1)
    assert p["pnl_usd"] == -44.0
    assert rep["paper_all_time"] == {"closed": 3, "wins": 2, "losses": 1, "open": 1, "pnl_usd": 6.0}
    w = rep["what_if"]
    assert (w["closed"], w["wins"], w["losses"], w["consumed_at_entry"], w["pnl_usd"]) == (2, 1, 1, 1, 25.0)
    assert rep["blocked"] == {"ENTRY_LATE:remaining_rr_below_min": 2, "ENTRY_LATE:price_past_target": 1}
    assert rep["authority"] == "evidence_only"
    text = odp.format_digest(rep)
    assert "-$44 today" in text and "What-if (filtered out, not trades)" in text
    assert "too little reward left" in text and "no rule change" in text


def test_quiet_day_and_missing_db(tmp_path):
    rep = odp.build_report(odp.load_rows(tmp_path / "nope.sqlite", DAY), DAY)
    text = odp.format_digest(rep)
    assert "none opened or closed today" in text
    assert "What-if" not in text


def test_main_writes_files(tmp_path, capsys):
    path = _db(tmp_path, [("2026-09-23T14:00:00+00:00", "OPEN", {}, {})])
    assert odp.main(["--db", str(path), "--log-dir", str(tmp_path), "--day", "2026-09-23"]) == 0
    assert json.loads((tmp_path / "options_daily_pnl_latest.json").read_text())["paper_all_time"]["open"] == 1
    assert "Options paper P&L" in capsys.readouterr().out
