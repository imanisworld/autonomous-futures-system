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
    assert "Weekly review · Week of Jun 22–28" in text
    assert "Orders filled: **3 of 4** (75%)" in text
    assert "**2 won, 1 lost**" in text
    assert "Profit: **+$69.50**" in text
    assert "most common skip: signa daily neutral" in text
    # Plain English: no ISO week label, no W/L shorthand, no snake codes.
    for jargon in ("2026-W26", "2W", "1L", "signa_daily_neutral", "P&L"):
        assert jargon not in text, jargon


def test_week_words_across_months():
    assert wr.week_words(date(2026, 6, 29), date(2026, 7, 5)) == "Week of Jun 29–Jul 5"


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


def test_your_limits_lines_render_both_systems_in_plain_english():
    from scripts.weekly_review import your_limits_lines

    fut = {"trades": 12, "wins": 5, "losses": 7, "net_usd": -40.5, "max_drawdown_usd": 210.0}
    opt = {"closed": 4, "wins": 1, "losses": 3, "pnl_usd": -300.0, "max_drawdown_usd": 320.0}
    lines = your_limits_lines({
        "futures": {"account": fut, "per_market": {**fut, "trades": 0}, "every_signal": fut},
        "options": {"account": opt, "per_ticker": opt, "no_limit": opt},
    })
    text = "\n".join(lines)
    assert "Whole account: 12 trades, 5 won, 7 lost, -$40.50, deepest drop $210" in text
    assert "Each market separately: no trades" in text
    assert "**Your limits · options, what-if** (at most 3 new paper trades a day)" in text
    assert your_limits_lines(None) == [] and your_limits_lines({}) == []


def test_collect_your_limits_is_fail_soft(tmp_path):
    from datetime import date as _date

    from scripts.weekly_review import collect_your_limits, your_limits_lines

    got = collect_your_limits(tmp_path, tmp_path / "missing.sqlite", _date(2026, 9, 28), _date(2026, 10, 4))
    assert got["futures"]["account"]["trades"] == 0          # no journals: empty, not an error
    assert "not found" in got["options"]["unavailable"]      # missing data is said, not shown as "no trades"
    assert "not shown — options database not found" in "\n".join(your_limits_lines(got))


def test_collect_your_limits_skips_futures_for_a_week_with_no_trading_days_yet(tmp_path):
    from datetime import date as _date, timedelta as _td

    from scripts.weekly_review import collect_your_limits

    monday = _date.today() + _td(days=14 - _date.today().weekday())
    got = collect_your_limits(tmp_path, tmp_path / "missing.sqlite", monday, monday + _td(days=6))
    assert "futures" not in got


def test_your_limits_lines_show_running_total_coverage_and_tie_range():
    from scripts.weekly_review import your_limits_lines

    fut = {"trades": 4, "wins": 1, "losses": 3, "net_usd": -50.0, "max_drawdown_usd": 80.0,
           "priced": 3, "unpriced": 1}
    opt = {"closed": 4, "wins": 1, "losses": 3, "pnl_usd": -144.0, "max_drawdown_usd": 347.0,
           "same_scan_ties": 1, "pnl_range_usd": [-834.0, -144.0]}
    text = "\n".join(your_limits_lines({
        "futures": {"account": fut, "per_market": fut, "every_signal": fut},
        "futures_total": {"account": fut, "per_market": fut, "every_signal": fut},
        "futures_since": "2026-09-23",
        "options": {"account": opt, "per_ticker": opt, "no_limit": {**opt, "pnl_range_usd": None}},
    }))
    assert "**Your limits · futures, what-if**" in text and "the bot's own limit is unchanged" in text
    assert "Running total since Sep 23:" in text
    assert "(dollars cover 3 of 4 trades; 1 could not be priced)" in text
    assert "-$834.00 to -$144.00 depending on which same-time trade is taken" in text


def test_your_limits_futures_unavailable_is_said_not_dropped():
    from scripts.weekly_review import your_limits_lines

    text = "\n".join(your_limits_lines({"futures": {"unavailable": "futures limits could not be computed"}}))
    assert "**Your limits · futures**: not shown — futures limits could not be computed" in text


def test_k35_k36_weekly_guards_show_unavailable_when_a_calculation_fails(tmp_path, monkeypatch):
    from datetime import date as _date

    from ops import options_daily_pnl_report as odp
    from ops import shadow_daily_pnl_report as sdp
    from scripts.weekly_review import collect_your_limits, your_limits_lines

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(sdp, "capped_report", boom)
    monkeypatch.setattr(odp, "load_rows", boom)
    db = tmp_path / "options.sqlite"
    db.write_bytes(b"")
    got = collect_your_limits(tmp_path, db, _date(2026, 9, 28), _date(2026, 10, 4))
    assert got["futures"] == {"unavailable": "futures limits could not be computed"}
    assert got["options"] == {"unavailable": "options limits could not be computed"}
    text = "\n".join(your_limits_lines(got))
    assert "futures**: not shown — futures limits could not be computed" in text
    assert "options**: not shown — options limits could not be computed" in text
