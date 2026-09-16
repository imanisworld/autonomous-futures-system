from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone

from ops.collector_census import build_census
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
    assert "1 fresh" in f

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
    card = {"embeds": [{"title": "Futures report", "fields": []}], "allowed_mentions": {"parse": []}}
    assert report._post_discord("https://example.invalid/hook", card) is True
    assert json.loads(seen["data"]) == card


def _screenshot_summary():
    return {
        "rows": 554,
        "row_types": {"SHADOW_OUTCOME": 218, "BAR_CLAIM": 168, "DECISION": 168},
        "decisions": {"NO_TRADE": 168},
        "shadow_outcomes": {"LOSS": 119, "WIN": 49, "NO_FILL": 30, "OPEN": 20},
        "shadow_strategies": {
            "strat_22_continuation_observed": 49, "ema_pullback_trend": 45,
            "impulse_first_pullback_observed": 31, "strat_22_reversal_observed": 31,
            "orb_false_break_fade": 14, "other_setup": 48,
        },
        "shadow_lanes": {"shadow_setups": 215, "range_signal": 3},
        "instruments": {"MNQ": 285, "MES": 269},
    }


def test_futures_card_prioritizes_attention_without_claiming_executed_results():
    summary = _screenshot_summary()
    before = json.dumps(summary, sort_keys=True)
    census = {"collectors": [
        {"name": "proof backup", "status": "DEAD"},
        *[{"name": f"collector {i}", "status": "FRESH"} for i in range(12)],
        {"name": "options scans", "status": "DEAD"},
    ]}
    payload = report.futures_discord_payload(summary, census, period="eod", start=date(2026, 9, 16), end=date(2026, 9, 16))
    embed = payload["embeds"][0]
    assert "content" not in payload  # one card, no duplicate wall of text
    assert payload["allowed_mentions"] == {"parse": []}
    assert embed["color"] == 0xF0B232
    assert "Proof backup" in embed["fields"][0]["value"]
    assert "12 fresh" in embed["fields"][0]["value"]
    assert "options scans" not in json.dumps(embed)
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "**49** win · **119** loss" in fields["Shadow outcomes"]
    assert "**30** no fill · **20** open" in fields["Shadow outcomes"]
    assert "not executed trades" in fields["Shadow outcomes"]
    assert "**554** journal rows" in fields["Collection"]
    assert "MNQ **285**" in fields["Collection"]
    assert "2-2 continuation" in fields["Shadow activity · top 5"]
    assert "+ 14 across 1 other categories" in fields["Shadow activity · top 5"]
    assert "resolved" not in json.dumps(embed).lower()
    assert json.dumps(summary, sort_keys=True) == before


def test_futures_card_missing_health_and_long_unknown_categories_stay_visible_and_bounded():
    summary = _screenshot_summary()
    summary["shadow_strategies"] = {f"unknown_{i}_" + "x" * 2000: i for i in range(40)}
    summary["shadow_outcomes"]["UNKNOWN"] = 7
    payload = report.futures_discord_payload(summary, {"status": "ERROR"}, period="eow", start=date(2026, 9, 14), end=date(2026, 9, 18))
    embed = payload["embeds"][0]
    assert embed["color"] == 0xF0B232
    assert "unavailable" in embed["fields"][0]["value"]
    assert "Weekly" in embed["title"]
    assert "Sep 14, 2026 → Sep 18, 2026" in embed["description"]
    assert "**7** · Unknown" in embed["fields"][2]["value"]
    assert all(len(f["value"]) <= 1024 for f in embed["fields"])
    assert sum(len(f["name"]) + len(f["value"]) for f in embed["fields"]) + len(embed["title"]) + len(embed["description"]) + len(embed["footer"]["text"]) < 6000


def test_main_posts_futures_embed_and_retains_raw_artifact(tmp_path, monkeypatch):
    summary = _screenshot_summary()
    monkeypatch.setattr(report, "summarize_futures", lambda rows: summary)
    monkeypatch.setattr(report, "run_collector_census", lambda path: {"collectors": [{"name": "futures journal", "status": "FRESH"}]})
    monkeypatch.setenv(report.FUTURES_ENV, "https://example.invalid/futures")
    monkeypatch.delenv(report.OPTIONS_ENV, raising=False)
    posts = []
    monkeypatch.setattr(report, "_post_discord", lambda url, payload: posts.append((url, payload)) or True)
    args = ["--period", "eod", "--date", "2026-09-16", "--log-dir", str(tmp_path), "--options-db", str(tmp_path / "missing.sqlite")]
    assert report.main(args) == 0
    assert len(posts) == 1
    assert posts[0][1]["embeds"][0]["color"] == 0x5865F2
    artifact = json.loads((tmp_path / "paper_collection_eod_2026-09-16.json").read_text())
    assert artifact["futures"] == summary
    posts.clear()
    assert report.main(args + ["--no-discord"]) == 0
    assert posts == []


def test_retired_overnight_watch_log_never_raises_collector_attention(tmp_path):
    """A stale legacy ``overnight_watch_summary.log`` on the box must not surface as
    a false DEAD health item in the EOD/EOW cards once the census registration is
    retired; the rest of the census flows through untouched."""
    (tmp_path / "overnight_watch_summary.log").write_text(
        "2026-09-01T18:04:25.671722+00:00 cycle ok: service=active\n"
    )
    census = build_census(tmp_path, datetime(2026, 9, 16, 21, 0, tzinfo=timezone.utc))
    assert all(row["name"] != "overnight watch" for row in census["collectors"])
    payload = report.futures_discord_payload(
        _screenshot_summary(), census, period="eow", start=date(2026, 9, 14), end=date(2026, 9, 16)
    )
    assert "overnight" not in json.dumps(payload).lower()
    text = report.format_futures_report(
        _screenshot_summary(), census, period="eow", start=date(2026, 9, 14), end=date(2026, 9, 16)
    )
    assert "overnight" not in text.lower()
