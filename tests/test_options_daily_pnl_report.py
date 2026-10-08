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
        "CREATE TABLE options_shadow_journal (id INTEGER PRIMARY KEY, timestamp TEXT, ticker TEXT DEFAULT 'SPY', "
        "status TEXT, selected_contract_json TEXT, outcome_json TEXT)"
    )
    conn.execute("CREATE TABLE options_episode_blocks (blocked_at TEXT, reason TEXT)")
    conn.execute("CREATE TABLE scans (timestamp TEXT, alert_sent INTEGER)")
    for ts, status, contract, outcome, *ticker in journal:
        conn.execute(
            "INSERT INTO options_shadow_journal (timestamp, ticker, status, selected_contract_json, outcome_json)"
            " VALUES (?,?,?,?,?)",
            (ts, ticker[0] if ticker else "SPY", status, json.dumps(contract, sort_keys=True), json.dumps(outcome)),
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


# ── your limits: at most 3 new paper trades a day ────────────────────────
def _won(ts, ticker, pnl, resolved):
    return (ts, "WIN" if pnl > 0 else "LOSS", {}, {"pnl_dollars": pnl, "resolved_at": resolved}, ticker)


def test_daily_cap_keeps_first_three_entries_account_wide_and_per_ticker(tmp_path):
    path = _db(tmp_path, [
        _won("2026-09-23T14:00:00+00:00", "SPY", 100.0, "2026-09-23T15:00:00-04:00"),
        _won("2026-09-23T14:05:00+00:00", "SPY", -40.0, "2026-09-23T14:00:00-04:00"),
        _won("2026-09-23T14:10:00+00:00", "IWM", -30.0, "2026-09-23T13:00:00-04:00"),
        _won("2026-09-23T14:15:00+00:00", "JPM", -200.0, "2026-09-23T15:30:00-04:00"),   # 4th of the day
        _won("2026-09-23T14:20:00+00:00", "SPY", 10.0, "2026-09-23T15:45:00-04:00"),     # 3rd SPY: kept per ticker
        _won("2026-09-23T14:25:00+00:00", "SPY", 77.0, "2026-09-23T15:50:00-04:00"),     # 4th SPY: out everywhere
        ("2026-09-23T14:30:00+00:00", "WIN", CF, {"pnl_dollars": 999.0, "resolved_at": "2026-09-23T15:00:00-04:00"}, "SPY"),
        _won("2026-09-24T14:00:00+00:00", "JPM", 5.0, "2026-09-24T15:00:00-04:00"),      # next day: new count
    ])
    rep = odp.build_report(odp.load_rows(path, DAY), DAY)
    c = rep["capped"]
    assert c["opened_today"] == 6 and c["left_out_today"] == {"account": 3, "per_ticker": 1}
    assert c["today"]["account"] == {"closed": 3, "wins": 1, "losses": 2, "pnl_usd": 30.0, "max_drawdown_usd": 70.0}
    assert c["today"]["per_ticker"]["pnl_usd"] == round(100 - 40 - 30 - 200 + 10, 2)
    assert c["today"]["no_limit"]["pnl_usd"] == rep["paper"]["pnl_usd"] == round(100 - 40 - 30 - 200 + 10 + 77, 2)
    assert c["all_time"]["account"]["closed"] == 4                       # what-if row never counted
    text = odp.format_digest(rep)
    assert "**Your limits** — at most 3 new paper trades a day" in text
    assert "Today: 6 opened · left out by the limit: 3 (whole account), 1 (per ticker)" in text
    assert "All time, whole account: 4 closed, 2 won, 2 lost, +$35" in text


def test_daily_cap_counts_open_trades_and_skips_consumed_and_cancelled(tmp_path):
    path = _db(tmp_path, [
        ("2026-09-23T14:00:00+00:00", "TARGET_CONSUMED_AT_ENTRY", {}, {}, "SPY"),
        ("2026-09-23T14:01:00+00:00", "CANCELLED", {}, {}, "SPY"),
        ("2026-09-23T14:02:00+00:00", "OPEN", {}, {}, "SPY"),
        ("2026-09-23T14:03:00+00:00", "OPEN", {}, {}, "IWM"),
        ("2026-09-23T14:04:00+00:00", "OPEN", {}, {}, "JPM"),
        _won("2026-09-23T14:05:00+00:00", "BAC", 50.0, "2026-09-23T15:00:00-04:00"),      # 4th real entry
    ])
    c = odp.build_report(odp.load_rows(path, DAY), DAY)["capped"]
    assert c["opened_today"] == 4 and c["left_out_today"]["account"] == 1
    assert c["all_time"]["account"]["closed"] == 0 and c["all_time"]["no_limit"]["pnl_usd"] == 50.0
