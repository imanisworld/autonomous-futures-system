from __future__ import annotations

import sqlite3
from datetime import date

from scripts import paper_collection_report as report


def test_period_bounds():
    ref = date(2026, 9, 16)
    assert report.period_bounds(ref, "eod") == (ref, ref)
    assert report.period_bounds(ref, "eow") == (date(2026, 9, 14), ref)


def test_summarize_futures_reads_real_journal_row_shapes():
    rows = [
        {"decision": "NO_TRADE", "setup": None, "instrument": "MNQ"},
        {"type": "BAR_CLAIM", "instrument": "MNQ", "timeframe_minutes": 15},
        {
            "type": "SHADOW_OUTCOME",
            "lane": "shadow_setups",
            "strategy": "strat_22_continuation_observed",
            "instrument": "MNQ",
            "final": True,
            "shadow_outcome": {"result": "WIN", "entry_filled": True},
        },
        {
            "type": "SHADOW_OUTCOME",
            "lane": "range_signal",
            "strategy": "range_break_close",
            "instrument": "MES",
            "final": True,
            "shadow_outcome": {"result": "NO_FILL", "entry_filled": False},
        },
    ]
    out = report.summarize_futures(rows)
    assert out["rows"] == 4
    assert out["row_types"] == {"DECISION": 1, "BAR_CLAIM": 1, "SHADOW_OUTCOME": 2}
    assert out["decisions"] == {"NO_TRADE": 1}
    assert out["shadow_outcomes"] == {"WIN": 1, "NO_FILL": 1}
    assert out["shadow_strategies"] == {"strat_22_continuation_observed": 1, "range_break_close": 1}
    assert out["shadow_lanes"] == {"shadow_setups": 1, "range_signal": 1}
    assert out["instruments"] == {"MNQ": 3, "MES": 1}


def test_census_options_scans_judged_against_session_close_not_wall_clock():
    # 15:57 ET last scan on the report day: FRESH at the 16:00 close even though
    # the census (run at 17:10 ET) calls it STALE.
    census = {
        "collectors": [
            {"name": "options scans", "status": "STALE", "last": "2026-09-16T19:57:46+00:00", "limit_minutes": 30},
            {"name": "options companion", "status": "DEAD", "last": "2026-07-21T00:00:00+00:00", "limit_minutes": 10080},
            {"name": "options shadow journal", "status": "FRESH", "limit_minutes": 1440},
        ]
    }
    lines = report._census_lines(census, options=True, session_end=date(2026, 9, 16))
    assert "attention" not in lines[0]
    assert "FRESH_AT_CLOSE 1" in lines[0]
    assert "QUIET_BY_DESIGN 1" in lines[0]
    assert "FRESH 1" in lines[0]
    # A genuinely dead scanner (last scan hours before the close) is still raised.
    census["collectors"][0]["last"] = "2026-09-16T14:05:00+00:00"
    lines = report._census_lines(census, options=True, session_end=date(2026, 9, 16))
    assert "attention: options scans" in lines[0]


def test_summarize_options_is_read_only_and_date_scoped(tmp_path):
    db = tmp_path / "options_scanner.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE scans (id INTEGER PRIMARY KEY, timestamp TEXT, ticker TEXT)")
    conn.execute(
        "CREATE TABLE options_shadow_journal (id INTEGER PRIMARY KEY, timestamp TEXT, status TEXT)"
    )
    conn.executemany(
        "INSERT INTO scans(timestamp,ticker) VALUES (?,?)",
        [
            ("2026-09-15T14:00:00Z", "AAPL"),
            ("2026-09-16T14:00:00Z", "MSFT"),
            ("2026-09-16T14:05:00Z", "NVDA"),
        ],
    )
    conn.executemany(
        "INSERT INTO options_shadow_journal(timestamp,status) VALUES (?,?)",
        [
            ("2026-09-16T14:00:00Z", "WATCH"),
            ("2026-09-16T15:00:00Z", "ACTIVE"),
        ],
    )
    conn.commit()
    conn.close()

    out = report.summarize_options(db, date(2026, 9, 16), date(2026, 9, 16))
    assert out["status"] == "OK"
    assert out["tables"]["scans"]["rows"] == 2
    assert out["tables"]["options_shadow_journal"]["rows"] == 2
    assert out["tables"]["options_shadow_journal"]["status_counts"] == {"WATCH": 1, "ACTIVE": 1}

    # Reporter must not mutate either table.
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM scans").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM options_shadow_journal").fetchone()[0] == 2
    conn.close()


def test_format_reports_show_zero_activity_and_health():
    census = {
        "collectors": [
            {"name": "futures journal", "status": "FRESH"},
            {"name": "options scans", "status": "STALE"},
        ]
    }
    f = report.format_futures_report(
        {"rows": 0, "decisions": {}, "instruments": {}},
        census,
        period="eod",
        start=date(2026, 9, 16),
        end=date(2026, 9, 16),
    )
    assert "zero futures journal rows" in f
    assert "FRESH 1" in f

    o = report.format_options_report(
        {
            "status": "OK",
            "tables": {
                "scans": {"status": "OK", "rows": 0},
                "options_shadow_journal": {"status": "OK", "rows": 0, "status_counts": {}},
            },
        },
        census,
        period="eod",
        start=date(2026, 9, 16),
        end=date(2026, 9, 16),
    )
    assert "zero option scans" in o
    assert "NOT option P&L outcomes" in o
    assert "attention: options scans" in o


def test_post_discord_uses_only_supplied_url(monkeypatch):
    seen = {}

    class Resp:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, timeout):
        seen["url"] = req.full_url
        seen["data"] = req.data
        seen["timeout"] = timeout
        return Resp()

    monkeypatch.setattr(report.urllib.request, "urlopen", fake_urlopen)
    assert report._post_discord("https://example.invalid/hook", "hello") is True
    assert seen["url"] == "https://example.invalid/hook"
    assert b"hello" in seen["data"]
