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
    assert "nothing was recorded for futures in this window" in f
    assert "1 up to date" in f

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
    assert "No option scans in this window" in o
    assert "not option profit or loss" in o
    assert "⚠ Data collectors need a look" in o
    assert "**Options scans** — running late" in o


def test_off_session_collectors_are_healthy_not_attention():
    census = {
        "collectors": [
            {"name": "futures journal", "status": "OFF_SESSION"},
            {"name": "options scans", "status": "OFF_SESSION"},
        ]
    }
    futures_field, futures_warn = report._collector_health(
        census, options=False, end=date(2026, 9, 20)
    )
    options_field, options_warn = report._collector_health(
        census, options=True, end=date(2026, 9, 20)
    )

    assert futures_warn is False
    assert options_warn is False
    assert futures_field["name"] == "✓ Data collectors OK"
    assert options_field["name"] == "✓ Data collectors OK"
    assert "market closed" in futures_field["value"]
    assert "market closed" in options_field["value"]


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
    assert "12 up to date" in embed["fields"][0]["value"]
    assert "options scans" not in json.dumps(embed)
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "**49** won · **119** lost" in fields["Practice results"]
    assert "**30** never filled · **20** still open" in fields["Practice results"]
    assert "not real trades" in fields["Practice results"]
    assert "**554** records saved" in fields["Activity recorded"]
    assert "MNQ **285**" in fields["Activity recorded"]
    assert "2-2 continuation" in fields["Most active setups"]
    assert "Pullback in a trend" in fields["Most active setups"]
    assert "+ 14 more across 1 other type" in fields["Most active setups"]
    assert "resolved" not in json.dumps(embed).lower()
    assert embed["description"] == "Wed Sep 16 · counts up to 8:00 PM ET"
    assert embed["footer"]["text"].startswith("READ ONLY")
    # Plain English: no UTC, no shadow/journal/bar-claim jargon, no raw ids.
    text = json.dumps(embed, ensure_ascii=False)
    for jargon in ("UTC", "Shadow", "shadow", "journal rows", "bar claims", "strat_", "EMA", "ORB", "NO_TRADE"):
        assert jargon not in text, jargon
    assert json.dumps(summary, sort_keys=True) == before


def test_futures_card_missing_health_and_long_unknown_categories_stay_visible_and_bounded():
    summary = _screenshot_summary()
    summary["shadow_strategies"] = {f"unknown_{i}_" + "x" * 2000: i for i in range(40)}
    summary["shadow_outcomes"]["UNKNOWN"] = 7
    payload = report.futures_discord_payload(summary, {"status": "ERROR"}, period="eow", start=date(2026, 9, 14), end=date(2026, 9, 18))
    embed = payload["embeds"][0]
    assert embed["color"] == 0xF0B232
    assert "Can't tell if the data collectors are working" in embed["fields"][0]["value"]
    assert "weekly" in embed["title"]
    assert "Mon Sep 14 – Fri Sep 18" in embed["description"]
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


def test_options_card_matches_screenshot_counts_and_close_context():
    summary = {"status": "OK", "tables": {
        "scans": {"status": "OK", "rows": 3165},
        "options_shadow_journal": {"status": "OK", "rows": 69, "status_counts": {"WIN": 51, "LOSS": 10, "OPEN": 8}},
    }}
    census = {"collectors": [
        {"name": "options scans", "status": "STALE", "last": "2026-09-17T19:57:00Z", "limit_minutes": 30},
        {"name": "options companion", "status": "DEAD"},
        {"name": "options shadow journal", "status": "FRESH"},
        {"name": "futures journal", "status": "DEAD"},
    ]}
    before = json.dumps([summary, census], sort_keys=True)
    payload = report.options_discord_payload(summary, census, period="eod", start=date(2026, 9, 17), end=date(2026, 9, 17))
    embed = payload["embeds"][0]
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert embed["title"] == "✅ Options practice report · daily"
    assert embed["color"] == 0x57F287
    assert fields["Status"].startswith("**All good**")
    assert "3:57 PM ET" in fields["✓ Data collectors OK"]
    assert "turned off on purpose" in fields["✓ Data collectors OK"]
    assert "Scans run: **3,165**" in fields["What was collected"]
    assert "No problems found" in fields["Problems"]
    assert "**51** · won" in fields["Practice setups by status"]
    assert "not option profit or loss" in fields["Practice setups by status"]
    assert "paper_collection_eod_2026-09-17.json" in embed["footer"]["text"]
    text = json.dumps(embed, ensure_ascii=False)
    for jargon in ("UTC", "PASS", "shadow", "--no-discord", "OPTIONS_COMPANION_ENABLED"):
        assert jargon not in text, jargon
    assert "futures journal" not in json.dumps(payload)
    assert payload["allowed_mentions"] == {"parse": []}
    assert json.dumps([summary, census], sort_keys=True) == before


def test_options_unscoped_or_missing_data_never_looks_like_healthy_window_counts():
    for status in ("NO_TIMESTAMP_COLUMN", "MISSING_TABLE", "QUERY_ERROR"):
        summary = {"status": "OK", "tables": {"scans": {"status": status, "rows": 5000}}}
        card = report.options_discord_payload(summary, {"status": "ERROR"}, period="eow", start=date(2026, 9, 14), end=date(2026, 9, 18))["embeds"][0]
        text = json.dumps(card)
        assert card["color"] == 0xED4245
        assert status.replace("_", " ").capitalize() in text
        assert "5,000" not in text
        assert "Can't count this window's scans" in text
        assert all(len(f["value"]) <= 1024 for f in card["fields"])
        assert sum(len(f["name"]) + len(f["value"]) for f in card["fields"]) < 5500


def test_main_routes_options_card_and_keeps_missing_db_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "run_collector_census", lambda _: {})
    monkeypatch.delenv(report.FUTURES_ENV, raising=False)
    monkeypatch.setenv(report.OPTIONS_ENV, "https://example.invalid/options")
    posts = []
    monkeypatch.setattr(report, "_post_discord", lambda url, payload: posts.append((url, payload)) or True)
    assert report.main(["--period", "eod", "--date", "2026-09-17", "--log-dir", str(tmp_path), "--options-db", str(tmp_path / "missing.sqlite")]) == 0
    assert len(posts) == 1
    assert posts[0][0] == "https://example.invalid/options"
    assert "Can't read the scanner database (database file missing)" in json.dumps(posts[0][1])
    assert posts[0][1]["embeds"][0]["title"] == "🔴 Options practice report · daily"
