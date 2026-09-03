"""Tests for scripts/weekly_review.py — pure aggregation + fail-soft main()."""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from scripts import weekly_review as wr


def _journal_week() -> list[dict]:
    """Synthetic week: 4 approved trades (2 filled W, 1 filled L, 1 cancelled),
    plus no-trade / risk-rejected decisions and bar-claims."""
    return [
        {"type": "BAR_CLAIM", "instrument": "MNQ", "claimed_at": "x"},
        {"type": "BAR_CLAIM", "instrument": "MES", "claimed_at": "x"},
        {"decision": "NO_TRADE", "instrument": "MNQ"},
        {"decision": "NO_TRADE", "instrument": "MES"},
        {"decision": "RISK_REJECTED", "instrument": "MNQ"},
        {"decision": "TRADE", "instrument": "MNQ"},
        {"decision": "TRADE", "instrument": "MNQ"},
        {"decision": "TRADE", "instrument": "MES"},
        {"decision": "TRADE", "instrument": "MES"},
        {"type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "WIN", "pnl_dollars": 94.5}},
        {"type": "OUTCOME", "instrument": "MES", "outcome": {"result": "WIN", "pnl_dollars": 25.0}},
        {"type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "LOSS", "pnl_dollars": -50.0}},
        {"type": "OUTCOME", "instrument": "MES", "outcome": {"result": "CANCELLED"}},
    ]


def _option_rows() -> list[dict]:
    return [
        {"status": "REJECTED", "risk_failed_rule": "signa_daily_neutral", "paper_pnl_dollars": None,
         "created_at": "2026-06-23T15:00:00+00:00"},
        {"status": "REJECTED", "risk_failed_rule": "signa_daily_neutral", "paper_pnl_dollars": None,
         "created_at": "2026-06-25T07:00:00+00:00"},
        {"status": "WIN", "risk_failed_rule": None, "paper_pnl_dollars": 40.0,
         "created_at": "2026-06-24T15:00:00+00:00"},
    ]


def test_week_bounds_and_label():
    # 2026-06-27 is a Saturday -> ISO week Mon 06-22 .. Sun 06-28
    monday, sunday = wr.week_bounds(date(2026, 6, 27))
    assert monday == date(2026, 6, 22)
    assert sunday == date(2026, 6, 28)
    assert wr.iso_week_label(date(2026, 6, 27)) == "2026-W26"


def test_summarize_week_core_math():
    data = wr.summarize_week(_journal_week(), _option_rows())
    assert data["approved_trades"] == 4
    assert data["no_trade"] == 2
    assert data["risk_rejected"] == 1
    # 3 filled (2W + 1L) + 1 cancelled => fill rate 75%
    assert data["filled"] == 3
    assert data["cancelled"] == 1
    assert data["attempted"] == 4
    assert data["fill_rate_pct"] == 75.0
    assert data["wins"] == 2 and data["losses"] == 1
    assert data["win_rate_pct"] == round(100 * 2 / 3, 1)
    assert data["pnl_total"] == 69.5
    assert data["pnl_by_instrument"] == {"MNQ": 44.5, "MES": 25.0}


def test_summarize_week_options():
    opt = wr.summarize_week(_journal_week(), _option_rows())["options"]
    assert opt["candidates"] == 3
    assert opt["opened"] == 1  # the WIN row; 2 REJECTED excluded
    assert opt["rejects_by_reason"] == {"signa_daily_neutral": 2}
    assert opt["paper_pnl"] == 40.0


def test_summarize_empty_week_is_safe():
    data = wr.summarize_week([], [])
    assert data["approved_trades"] == 0
    assert data["fill_rate_pct"] is None
    assert data["win_rate_pct"] is None
    assert data["options"]["candidates"] == 0


def test_format_report_contains_key_numbers():
    data = wr.summarize_week(_journal_week(), _option_rows())
    text = wr.format_report(data, week="2026-W26", monday=date(2026, 6, 22), sunday=date(2026, 6, 28))
    assert "Weekly review — 2026-W26" in text
    assert "fill rate **75%**" in text
    assert "2W / 1L" in text
    assert "$69.50" in text


def test_load_option_rows_filters_by_week(tmp_path):
    db = tmp_path / "options_companion.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE options_companion (created_at TEXT, status TEXT, risk_failed_rule TEXT, paper_pnl_dollars REAL)")
    conn.executemany(
        "INSERT INTO options_companion VALUES (?,?,?,?)",
        [("2026-06-23T15:00:00+00:00", "REJECTED", "signa_daily_neutral", None),
         ("2026-07-01T15:00:00+00:00", "WIN", None, 10.0)],  # outside the week
    )
    conn.commit(); conn.close()
    rows = wr.load_option_rows(db, date(2026, 6, 22), date(2026, 6, 28))
    assert len(rows) == 1 and rows[0]["status"] == "REJECTED"
    # missing file is safe
    assert wr.load_option_rows(tmp_path / "nope.sqlite", date(2026, 6, 22), date(2026, 6, 28)) == []


def test_main_failsoft_writes_artifact_no_webhook(tmp_path, monkeypatch, capsys):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "journal_2026-06-24.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _journal_week())
    )
    monkeypatch.setenv("LOG_DIR", str(log_dir))
    monkeypatch.setenv("OPTIONS_COMPANION_SQLITE_PATH", "")
    monkeypatch.setenv("WEEKLY_REVIEW_DATE", "2026-06-27")
    monkeypatch.delenv("DISCORD_ROUTE_DAILY_REPORT", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    rc = wr.main()
    assert rc == 0
    artifact = log_dir / "weekly_review_2026-W26.json"
    assert artifact.exists()
    data = json.loads(artifact.read_text())
    assert data["week"] == "2026-W26"
    assert data["approved_trades"] == 4
    assert "no webhook configured" in capsys.readouterr().out
