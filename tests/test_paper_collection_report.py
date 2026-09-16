from __future__ import annotations

import sqlite3
from datetime import date

from scripts import paper_collection_report as report


def test_period_bounds():
    ref = date(2026, 9, 16)
    assert report.period_bounds(ref, "eod") == (ref, ref)
    assert report.period_bounds(ref, "eow") == (date(2026, 9, 14), ref)


def test_summarize_futures_counts_without_inference():
    rows = [
        {"decision": "NO_TRADE", "strategy": "strat_212", "instrument": "MNQ"},
        {"decision": "TRADE", "strategy": "asia_d_ema", "instrument": "MNQ"},
        {"type": "OUTCOME", "instrument": "MNQ", "outcome": {"result": "WIN"}},
    ]
    out = report.summarize_futures(rows)
    assert out["rows"] == 3
    assert out["decisions"] == {"NO_TRADE": 1, "TRADE": 1}
    assert out["outcomes"] == {"WIN": 1}
    assert out["strategies"] == {"strat_212": 1, "asia_d_ema": 1}


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
        {"rows": 0, "decisions": {}, "outcomes": {}, "strategies": {}, "instruments": {}},
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
    assert "STALE 1" in o


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
