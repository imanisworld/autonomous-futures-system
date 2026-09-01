"""Tests for the read-only collector freshness census."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ops.collector_census import (
    ABSENT,
    DEAD,
    FRESH,
    STALE,
    Collector,
    build_census,
    campaign_arms,
    check,
    format_census,
    _classify,
    _sqlite_last,
)

NOW = datetime(2026, 8, 25, 13, 0, tzinfo=timezone.utc)


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_classify_bands():
    assert _classify(None, 60) == ABSENT
    assert _classify(10, 60) == FRESH
    assert _classify(60, 60) == FRESH
    assert _classify(120, 60) == STALE
    assert _classify(241, 60) == DEAD


def test_jsonl_uses_last_record_timestamp_not_mtime(tmp_path):
    # mtime is "now" because we just wrote it, but the newest record is old --
    # the census must trust the record, which is what caught the dead journal.
    _write_jsonl(
        tmp_path / "lane.jsonl",
        [{"ts": "2026-08-01T00:00:00+00:00"}, {"ts": "2026-08-02T00:00:00+00:00"}],
    )
    collector = Collector("lane", "jsonl", "lane.jsonl", 60)
    result = check(collector, tmp_path, NOW)
    assert result["status"] == DEAD
    assert result["last"].startswith("2026-08-02")


def test_jsonl_skips_unparseable_trailing_lines(tmp_path):
    path = tmp_path / "lane.jsonl"
    path.write_text(
        json.dumps({"ts": "2026-08-25T12:30:00+00:00"}) + "\n{ broken json\n\n"
    )
    result = check(Collector("lane", "jsonl", "lane.jsonl", 60), tmp_path, NOW)
    assert result["status"] == FRESH


def test_daily_jsonl_absent_when_file_not_created(tmp_path):
    collector = Collector("journal", "daily_jsonl", "journal_{date}.jsonl", 30)
    assert check(collector, tmp_path, NOW)["status"] == ABSENT


def test_daily_jsonl_resolves_todays_filename(tmp_path):
    _write_jsonl(tmp_path / "journal_2026-08-25.jsonl", [{"ts": "2026-08-25T12:50:00+00:00"}])
    collector = Collector("journal", "daily_jsonl", "journal_{date}.jsonl", 30)
    assert check(collector, tmp_path, NOW)["status"] == FRESH


@pytest.mark.parametrize("column", ["timestamp", "ts", "created_at", "observed_at"])
def test_sqlite_time_column_is_discovered(tmp_path, column):
    db = tmp_path / "x.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(f"CREATE TABLE t (id INTEGER, [{column}] TEXT)")
    conn.execute(f"INSERT INTO t VALUES (1, '2026-08-25T12:40:00+00:00')")
    conn.commit()
    conn.close()
    last, rows, exists = _sqlite_last(db, "t", ("timestamp", "ts", "created_at", "observed_at"))
    assert exists and rows == 1
    assert last == datetime(2026, 8, 25, 12, 40, tzinfo=timezone.utc)


def test_populated_table_without_timestamp_still_reports_rows(tmp_path):
    # A frozen-but-populated table must not read as absent/empty.
    db = tmp_path / "x.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.executemany("INSERT INTO t VALUES (?)", [(1,), (2,)])
    conn.commit()
    conn.close()
    last, rows, exists = _sqlite_last(db, "t", ("timestamp",))
    assert exists and rows == 2 and last is None


def test_missing_table_reports_absent(tmp_path):
    db = tmp_path / "x.sqlite"
    sqlite3.connect(db).close()
    collector = Collector("j", "sqlite_table", "x.sqlite", 60, table="nope")
    assert check(collector, tmp_path, NOW)["status"] == ABSENT


def test_campaign_arms_report_exact_configured_populations_and_idle(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [
            {"record_type": "CANDIDATE", "strategy": "vwap_hold", "variant": "control",
             "signal_timestamp": "2026-08-25T12:00:00+00:00"},
            {"record_type": "CANDIDATE", "strategy": "vwap_hold", "variant": "modified",
             "signal_timestamp": "2026-08-19T04:00:00+00:00"},
            {"record_type": "OUTCOME", "strategy": "vwap_hold", "variant": "control",
             "signal_timestamp": "2026-08-25T12:30:00+00:00"},
        ],
    )
    campaign = campaign_arms(tmp_path, NOW)
    arms = campaign["configured"]
    assert set(arms) == {
        "vwap_hold/control", "vwap_hold/modified",
        "orb_reclaim/control", "orb_reclaim/modified",
        "vwap_rejection/observer",
    }
    assert arms["vwap_hold/control"]["count"] == 1  # OUTCOME rows are not candidates
    assert arms["vwap_hold/modified"]["count"] == 1
    assert arms["orb_reclaim/control"]["count"] == 0
    assert arms["orb_reclaim/control"]["last"] is None
    assert arms["vwap_hold/modified"]["idle_hours"] > arms["vwap_hold/control"]["idle_hours"]
    assert campaign["unexpected"] == {}


def test_census_flags_dead_and_renders(tmp_path):
    _write_jsonl(tmp_path / "strategy_context_observations.jsonl",
                 [{"ts": "2026-08-01T00:00:00+00:00"}])
    census = build_census(tmp_path, NOW)
    assert "strategy context" in census["dead"]
    rendered = format_census(census)
    assert "COLLECTOR CENSUS" in rendered
    assert "strategy context" in rendered


def test_census_never_writes(tmp_path):
    before = sorted(p.name for p in tmp_path.iterdir())
    build_census(tmp_path, NOW)
    assert sorted(p.name for p in tmp_path.iterdir()) == before


# --- suppression-reason honesty -------------------------------------------
# A scan that could never be scored must not be logged as a near-miss.

from datetime import datetime as _dt

from alert_ranker.scorer import score_setup

_NY = _dt(2026, 8, 25, 10, 0, tzinfo=timezone.utc)


def test_missing_feed_inputs_are_named_not_hidden():
    # Exactly the shape the box produces today: a Public equity quote with no
    # vwap/ema20, which yields UNKNOWN direction.
    result = score_setup({"ticker": "AAPL", "price": 312.36}, now=_NY)
    assert result.score == 0
    assert result.direction == "UNKNOWN"
    assert result.reason == "missing_inputs:vwap,ema20"


def test_all_inputs_missing_lists_all_three():
    result = score_setup({"ticker": "AAPL"}, now=_NY)
    assert result.reason == "missing_inputs:price,vwap,ema20"


def test_inputs_present_but_unaligned_is_not_a_missing_input():
    # price sits between vwap and ema20 -> genuinely undecidable, not blind.
    result = score_setup(
        {"ticker": "AAPL", "price": 100.0, "vwap": 99.0, "ema20": 101.0}, now=_NY
    )
    assert result.reason == "direction_unknown"


def test_scorer_reason_survives_into_the_suppression_decision():
    import asyncio
    from alert_ranker.discord import DiscordAlerter

    class _Cfg:
        alert_threshold = 6
        discord_webhook_url = ""
        duplicate_window_minutes = 30

    result = score_setup({"ticker": "AAPL", "price": 312.36}, now=_NY)
    alerter = DiscordAlerter.__new__(DiscordAlerter)
    alerter.config = _Cfg()
    alerter.storage = None

    decision = asyncio.run(alerter.send_if_eligible(result))
    assert decision.sent is False
    # The bug: this used to read "score_below_threshold" on a scan that never
    # had the inputs to be scored at all.
    assert decision.reason == "missing_inputs:vwap,ema20"


# --- Alpaca credential-name resolution ------------------------------------
# Real credentials on the box live in ALPACA_KEY / ALPACA_SECRET while the
# loader read only ALPACA_API_KEY / ALPACA_SECRET_KEY, so a fully-credentialed
# provider reported `credentials_missing`.

from alert_ranker.config import (
    misnamed_alpaca_env,
    resolve_alpaca_credentials,
)


def test_canonical_names_resolve():
    key, secret = resolve_alpaca_credentials(
        {"ALPACA_API_KEY": "k1", "ALPACA_SECRET_KEY": "s1"}
    )
    assert (key, secret) == ("k1", "s1")


def test_box_spelling_resolves():
    key, secret = resolve_alpaca_credentials({"ALPACA_KEY": "k2", "ALPACA_SECRET": "s2"})
    assert (key, secret) == ("k2", "s2")


def test_canonical_wins_over_alias():
    key, _ = resolve_alpaca_credentials({"ALPACA_API_KEY": "canon", "ALPACA_KEY": "alias"})
    assert key == "canon"


def test_empty_canonical_falls_through_to_alias():
    # The exact box shape: canonical present but blank, value under the alias.
    key, secret = resolve_alpaca_credentials(
        {"ALPACA_API_KEY": "", "ALPACA_KEY": "k3",
         "ALPACA_SECRET_KEY": "  ", "ALPACA_SECRET": "s3"}
    )
    assert (key, secret) == ("k3", "s3")


def test_nothing_set_resolves_empty():
    assert resolve_alpaca_credentials({}) == ("", "")


def test_misnamed_env_is_reported():
    flagged = misnamed_alpaca_env({"ALPACA_KEY": "k", "ALPACA_SECRET": "s"})
    assert sorted(flagged) == ["ALPACA_KEY", "ALPACA_SECRET"]


def test_correctly_named_env_is_not_flagged():
    assert misnamed_alpaca_env({"ALPACA_API_KEY": "k", "ALPACA_SECRET_KEY": "s"}) == []


def test_loader_sees_alias_credentials_as_configured():
    from alert_ranker.config import load_config

    cfg = load_config(
        [("ALPACA_KEY", "k"), ("ALPACA_SECRET", "s"),
         ("OPTIONS_MARKET_DATA_PROVIDER", "alpaca")]
    )
    assert cfg.alpaca_api_key_configured is True
    assert cfg.alpaca_secret_key_configured is True


def test_campaign_arms_separate_shared_variants_by_strategy(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [
            {"record_type": "CANDIDATE", "strategy": "vwap_hold", "variant": "control",
             "signal_timestamp": "2026-08-25T12:00:00+00:00"},
            {"record_type": "CANDIDATE", "strategy": "orb_reclaim", "variant": "control",
             "signal_timestamp": "2026-08-25T11:00:00+00:00"},
        ],
    )
    arms = campaign_arms(tmp_path, NOW)["configured"]
    assert arms["vwap_hold/control"]["count"] == 1
    assert arms["orb_reclaim/control"]["count"] == 1


def test_campaign_arms_report_unexpected_population_separately(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [{"record_type": "CANDIDATE", "strategy": "unexpected", "variant": "control",
          "signal_timestamp": "2026-08-25T12:00:00+00:00"}],
    )
    campaign = campaign_arms(tmp_path, NOW)
    assert "unexpected/control" not in campaign["configured"]
    assert campaign["unexpected"]["unexpected/control"]["count"] == 1


def test_event_driven_futures_files_are_not_false_dead_cadence_collectors(tmp_path):
    census = build_census(tmp_path, NOW)
    names = {row["name"] for row in census["collectors"]}
    assert names.isdisjoint({
        "vwap_hold_early shadow",
        "mnq_strat_22 continuation",
        "mes_trend_consolidation",
        "mnq_strat_22 reversal",
        "mnq_strat_32",
        "mnq_strat_322",
    })
