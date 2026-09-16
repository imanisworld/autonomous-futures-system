"""Targeted tests for the read-only paper collection digest.

Proves: both routes resolve through DiscordRouter and fail soft when unset; no
webhook secret ever reaches output or logs; daily/weekly window counts are
right; zero-activity lanes stay visible; #595 is included when active; and no
evidence/state file is modified by building or posting a digest.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from notifications.discord_router import DiscordRouter, load_routes, reset_disabled_route_warnings
from ops import paper_collection_digest as digest
from scripts import paper_collection_digest as cli

NOW = datetime(2026, 9, 16, 21, 15, tzinfo=timezone.utc)
SECRET = "https://discord.com/api/webhooks/123456/SECRET-TOKEN-DO-NOT-LEAK"


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _asia_event(event: str, day: str, ts: str, **extra) -> dict:
    return {
        "campaign_id": "asia_d_ema_2026_09_v1", "event": event, "instrument": "MNQ", "session": "asian",
        "ts": ts, "day": day, "observed_at": ts, "et_hour": 20, "strategy": "ema_pullback_trend",
        "direction": "LONG", **extra,
    }


@pytest.fixture
def log_dir(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "logs"
    root.mkdir()
    # #595 cohort: 2 observation days inside the ISO week of 2026-09-16, one before it.
    _jsonl(root / "asia_d_ema_cohort" / "evidence.jsonl", [
        _asia_event("CANDIDATE_PRE_EPOCH", "2026-09-11", "2026-09-10T23:00:00+00:00"),
        _asia_event("CANDIDATE_FILLED", "2026-09-15", "2026-09-15T00:15:00+00:00"),
        _asia_event("OUTCOME", "2026-09-15", "2026-09-15T02:15:00+00:00", result="WIN", pnl_r=1.0),
        _asia_event("CANDIDATE_FILLED", "2026-09-16", "2026-09-16T00:30:00+00:00"),
        _asia_event("CANDIDATE_SKIPPED_BUSY", "2026-09-16", "2026-09-16T00:45:00+00:00"),
        _asia_event("OUTCOME", "2026-09-16", "2026-09-16T03:00:00+00:00", result="LOSS", pnl_r=-1.0),
        _asia_event("NO_FILL", "2026-09-16", "2026-09-16T05:00:00+00:00", result="NO_FILL"),
    ])
    (root / "asia_d_ema_cohort" / "state.json").write_text(json.dumps({
        "version": 1, "campaign_id": "asia_d_ema_2026_09_v1", "seen": [], "position": None, "pending_events": [],
    }))
    monkeypatch.setenv("ASIA_D_EMA_PAPER_MODE", "paper_sim")
    monkeypatch.setenv("ASIA_D_EMA_PAPER_EPOCH_START", "2026-09-14T22:00:00Z")
    # wide-stop 4k lane: one candidate on the 15th, one on the 16th.
    for day in ("2026-09-15", "2026-09-16"):
        _jsonl(root / "hypothetical_ledger" / "wide_stop_4k" / f"journal_{day}.jsonl", [{
            "decision": "HYPOTHETICAL_LEDGER", "ledger": "wide_stop_4k", "instrument": "MNQ",
            "wide_stop_ledger": {"collector_event": "CANDIDATE", "lane_result": "REJECTED_UPSTREAM", "fill_status": None},
        }])
    # cross-instrument campaign: 3 rows on the 16th, 1 on the 14th.
    _jsonl(root / "cross_instrument_observation_v1.jsonl", [
        {"record_type": "CANDIDATE", "instrument": "M2K", "observed_at": "2026-09-14T13:00:00+00:00"},
        {"record_type": "CANDIDATE", "instrument": "M2K", "observed_at": "2026-09-16T13:00:00+00:00"},
        {"record_type": "SIGNAL", "instrument": "MGC", "observed_at": "2026-09-16T14:00:00+00:00"},
        {"record_type": "OUTCOME", "instrument": "M2K", "observed_at": "2026-09-16T18:00:00+00:00"},
    ])
    return root


def _snapshot(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


# ── routes ──────────────────────────────────────────────────────────────────
def test_both_routes_exist_and_are_optional():
    routes = load_routes()
    for name, env_var in (
        (digest.FUTURES_ROUTE, "DISCORD_ROUTE_PAPER_COLLECTION_FUTURES"),
        (digest.OPTIONS_ROUTE, "DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS"),
    ):
        assert routes[name].env_var == env_var
        assert routes[name].required is False


def test_routes_resolve_through_router_and_deliver():
    sent: list[tuple[str, str]] = []
    router = DiscordRouter(env={"DISCORD_ROUTE_PAPER_COLLECTION_FUTURES": SECRET}, transport=lambda u, m: sent.append((u, m)))
    assert cli.post(digest.FUTURES_ROUTE, "hello", router=router) is True
    assert sent == [(SECRET, "hello")]


def test_missing_route_fails_soft(capsys, caplog):
    reset_disabled_route_warnings()
    router = DiscordRouter(env={}, transport=lambda u, m: (_ for _ in ()).throw(AssertionError("must not send")))
    with caplog.at_level(logging.WARNING):
        assert cli.post(digest.OPTIONS_ROUTE, "hello", router=router) is False
    assert "not configured" in capsys.readouterr().out


def test_no_secret_in_output_or_logs(log_dir, capsys, caplog, monkeypatch):
    monkeypatch.setenv("DISCORD_ROUTE_PAPER_COLLECTION_FUTURES", SECRET)
    router = DiscordRouter(env=os.environ, transport=lambda u, m: None)
    with caplog.at_level(logging.DEBUG):
        rc = cli.main(["--domain", "futures", "--period", "daily", "--date", "2026-09-16", "--log-dir", str(log_dir), "--post", "--json"], router=router)
    assert rc == 0
    out = capsys.readouterr()
    for blob in (out.out, out.err, caplog.text):
        assert SECRET not in blob
        assert "SECRET-TOKEN" not in blob


def test_delivery_failure_never_raises(log_dir, monkeypatch):
    def boom(url, message):
        raise RuntimeError("discord down")
    router = DiscordRouter(env={"DISCORD_ROUTE_PAPER_COLLECTION_FUTURES": SECRET}, transport=boom)
    assert cli.main(["--domain", "futures", "--date", "2026-09-16", "--log-dir", str(log_dir), "--post"], router=router) == 0


# ── windows ─────────────────────────────────────────────────────────────────
def test_daily_window_counts(log_dir):
    d = digest.build_futures_digest(log_dir, period="daily", ref_date=date(2026, 9, 16), now=NOW)
    assert d["window"] == {"start": "2026-09-16", "end": "2026-09-16", "basis": d["window"]["basis"]}
    lanes = {l["lane"]: l for l in d["lanes"]}
    asia = lanes["MNQ Asia D+EMA cohort (#595)"]
    assert asia["window"]["fills"] == 1
    assert asia["window"]["skipped_busy"] == 1
    assert asia["window"]["no_fills"] == 1
    assert asia["window"]["losses"] == 1 and asia["window"]["wins"] == 0
    assert asia["window"]["pre_epoch"] == 0
    assert asia["cumulative"]["resolved_trades"] == 2
    ws = lanes["MNQ 4HR Re-Trigger (wide_stop_4k)"]
    assert ws["window"]["candidates"] == 1 and ws["cumulative"]["candidates"] == 2
    ci = lanes["Cross-instrument observation campaign"]
    assert (ci["window"]["candidates"], ci["window"]["signals"], ci["window"]["outcomes"]) == (1, 1, 1)
    assert ci["cumulative"]["rows"] == 4


def test_weekly_window_counts(log_dir):
    d = digest.build_futures_digest(log_dir, period="weekly", ref_date=date(2026, 9, 16), now=NOW)
    assert (d["window"]["start"], d["window"]["end"]) == ("2026-09-14", "2026-09-20")
    lanes = {l["lane"]: l for l in d["lanes"]}
    asia = lanes["MNQ Asia D+EMA cohort (#595)"]
    assert asia["window"]["fills"] == 2 and asia["window"]["wins"] == 1 and asia["window"]["losses"] == 1
    assert asia["window"]["observation_days"] == 2
    assert asia["window"]["pre_epoch"] == 0  # 09-11 row is outside the week
    assert asia["review_gate"]["resolved_trades_so_far"] == 2 and asia["review_gate"]["reached"] is False
    assert lanes["MNQ 4HR Re-Trigger (wide_stop_4k)"]["window"]["candidates"] == 2
    ci = lanes["Cross-instrument observation campaign"]
    assert ci["window"]["candidates"] == 2 and ci["window"]["days"] == 2


def test_first_run_never_counts_history_as_today(log_dir):
    d = digest.build_futures_digest(log_dir, period="daily", ref_date=date(2026, 9, 17), now=NOW)
    asia = {l["lane"]: l for l in d["lanes"]}["MNQ Asia D+EMA cohort (#595)"]
    assert asia["zero_activity"] is True
    assert asia["cumulative"]["fills"] == 2  # history is still visible, just not "today"


# ── visibility / inclusion ──────────────────────────────────────────────────
def test_zero_activity_lanes_remain_visible(log_dir):
    d = digest.build_futures_digest(log_dir, period="weekly", ref_date=date(2026, 9, 16), now=NOW)
    names = [l["lane"] for l in d["lanes"]]
    assert "MES 15m 1-2-2 (mes_122_1500)" in names
    assert "MNQ Daily 2-2 (daily_22_5k)" in d["zero_activity_lanes"]
    text = digest.format_digest(d)
    assert "MES 15m 1-2-2 (mes_122_1500)" in text
    assert "MNQ Daily 2-2 (daily_22_5k)" in text
    daily = digest.format_digest(digest.build_futures_digest(log_dir, period="daily", ref_date=date(2026, 9, 16), now=NOW))
    assert "Zero activity" in daily and "Runner shadow evidence" in daily


def test_595_included_and_flagged_when_active(log_dir):
    d = digest.build_futures_digest(log_dir, period="daily", ref_date=date(2026, 9, 16), now=NOW)
    asia = d["lanes"][0]
    assert asia["campaign_id"] == "asia_d_ema_2026_09_v1"
    assert asia["mode"] == "paper_sim"
    assert asia["epoch"] == "2026-09-14T22:00:00Z"
    assert asia["health"] == "OK"
    assert "asia_d_ema_2026_09_v1" in digest.format_digest(d)


def test_595_shown_inactive_when_off(log_dir, monkeypatch):
    monkeypatch.setenv("ASIA_D_EMA_PAPER_MODE", "off")
    d = digest.build_futures_digest(log_dir, period="daily", ref_date=date(2026, 9, 16), now=NOW)
    assert d["lanes"][0]["mode"] == "off (inactive)"


def test_pending_events_surface_as_attention(log_dir):
    state = log_dir / "asia_d_ema_cohort" / "state.json"
    data = json.loads(state.read_text()); data["pending_events"] = [{"event": "OUTCOME"}]
    state.write_text(json.dumps(data))
    d = digest.build_futures_digest(log_dir, period="daily", ref_date=date(2026, 9, 16), now=NOW)
    assert d["lanes"][0]["health"] == "PENDING_EVENTS"
    assert "🔴" in digest.format_digest(d).splitlines()[1]


# ── options ─────────────────────────────────────────────────────────────────
@pytest.fixture
def options_dir(tmp_path: Path) -> Path:
    root = tmp_path / "options_logs"; root.mkdir()
    db = root / "options_scanner.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE scans (id INTEGER PRIMARY KEY, timestamp TEXT, source TEXT, ticker TEXT, direction TEXT,
            score INTEGER, pattern TEXT, components_json TEXT, raw_json TEXT, alert_sent INTEGER, alert_suppression_reason TEXT);
        CREATE TABLE options_shadow_journal (id INTEGER PRIMARY KEY, timestamp TEXT, scan_id INTEGER, ticker TEXT,
            direction TEXT, score INTEGER, pattern TEXT, status TEXT, setup_inputs_json TEXT, provider_snapshot_json TEXT,
            selected_contract_json TEXT, outcome_json TEXT);
    """)
    for i, (ts, ticker) in enumerate([("2026-09-15T15:00:00+00:00", "AAPL"), ("2026-09-16T15:00:00+00:00", "AAPL"), ("2026-09-16T15:05:00+00:00", "NVDA")]):
        conn.execute("INSERT INTO scans VALUES (?,?,?,?,?,?,?,?,?,?,?)", (i + 1, ts, "s", ticker, "LONG", 5, "p", "{}", "{}", 0, ""))
    sel_active = json.dumps({"paper_policy_id": "OPTIONS_PAPER_V1", "paper_evidence_lane": "ACTIVE"})
    sel_cf = json.dumps({"paper_policy_id": "OPTIONS_PAPER_V1", "paper_evidence_lane": "COUNTERFACTUAL"})
    rows = [
        # journal status WIN but recorded P&L negative -> financial LOSS (never trust the label)
        (1, "2026-09-16T15:10:00+00:00", "WIN", sel_active, json.dumps({"pnl_dollars": -5.0})),
        (2, "2026-09-16T15:20:00+00:00", "LOSS", sel_active, json.dumps({"pnl_dollars": 12.0})),
        (3, "2026-09-16T15:30:00+00:00", "OPEN", sel_cf, "{}"),
        (4, "2026-09-10T15:30:00+00:00", "WIN", sel_active, json.dumps({"pnl_dollars": 3.0})),  # pre-epoch, last week
    ]
    for rid, ts, status, sel, out in rows:
        conn.execute("INSERT INTO options_shadow_journal VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, ts, 1, "AAPL", "LONG", 5, "p", status, "{}", "{}", sel, out))
    conn.commit(); conn.close()
    cov = root / "coverage_collector"; cov.mkdir()
    _jsonl(cov / "ledger.jsonl", [
        {"session_date": "2026-09-15", "status": "STARTED", "recorded_at": "2026-09-15T20:35:00+00:00"},
        {"session_date": "2026-09-15", "status": "DONE", "recorded_at": "2026-09-15T20:40:00+00:00"},
        {"session_date": "2026-09-15", "status": "AGGREGATED", "recorded_at": "2026-09-15T20:41:00+00:00"},
        {"session_date": "2026-09-16", "status": "STARTED", "recorded_at": "2026-09-16T20:35:00+00:00"},
        {"session_date": "2026-09-16", "status": "FAILED", "reason": "observer_provider_errors", "detail": "x", "recorded_at": "2026-09-16T20:35:17+00:00"},
    ])
    return root


def test_options_daily_and_weekly_counts(options_dir, tmp_path):
    companion = tmp_path / "companion.sqlite"  # absent -> zero rows, still visible
    d = digest.build_options_digest(options_dir, period="daily", ref_date=date(2026, 9, 16), now=NOW,
                                    scanner_db=options_dir / "options_scanner.sqlite",
                                    coverage_dir=options_dir / "coverage_collector", companion_db=companion)
    v1, cov, comp = d["lanes"]
    assert v1["window"]["scans"] == 2 and v1["window"]["symbols_scanned"] == 2
    assert v1["window"]["journal_rows"] == 3
    assert v1["window"]["active_loss"] == 1 and v1["window"]["active_profit"] == 1
    assert cov["window"]["sessions_failed"] == 1 and cov["health"] == "FAILED_LAST_SESSION"
    assert comp["zero_activity"] is True and "Options companion" in digest.format_digest(d)
    w = digest.build_options_digest(options_dir, period="weekly", ref_date=date(2026, 9, 16), now=NOW,
                                    scanner_db=options_dir / "options_scanner.sqlite",
                                    coverage_dir=options_dir / "coverage_collector", companion_db=companion)
    v1w, covw, _ = w["lanes"]
    assert v1w["window"]["scans"] == 3 and v1w["window"]["journal_rows"] == 3  # 09-10 row is last week
    assert covw["window"]["sessions_attempted"] == 2 and covw["window"]["sessions_done"] == 1
    assert v1w["review_gate"]["sample_status"] == "INSUFFICIENT"


def test_options_skip_non_session(capsys, options_dir):
    rc = cli.main(["--domain", "options", "--period", "daily", "--date", "2026-09-19", "--log-dir", str(options_dir), "--skip-non-session"])
    assert rc == 0 and "not an NYSE session" in capsys.readouterr().out


# ── read-only guarantee ─────────────────────────────────────────────────────
def test_building_and_posting_modifies_no_file(log_dir, options_dir, monkeypatch):
    before = _snapshot(log_dir), _snapshot(options_dir)
    router = DiscordRouter(env={"DISCORD_ROUTE_PAPER_COLLECTION_FUTURES": SECRET, "DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS": SECRET},
                           transport=lambda u, m: None)
    for period in ("daily", "weekly"):
        assert cli.main(["--domain", "futures", "--period", period, "--date", "2026-09-16", "--log-dir", str(log_dir), "--post"], router=router) == 0
        monkeypatch.setenv("OPTIONS_SCANNER_SQLITE_PATH", str(options_dir / "options_scanner.sqlite"))
        monkeypatch.setenv("OPTIONS_COVERAGE_DATA_DIR", str(options_dir / "coverage_collector"))
        assert cli.main(["--domain", "options", "--period", period, "--date", "2026-09-16", "--log-dir", str(options_dir), "--post"], router=router) == 0
    assert (_snapshot(log_dir), _snapshot(options_dir)) == before


def test_chunking_respects_discord_limit():
    text = "\n".join(f"line {i} " + "x" * 120 for i in range(60))
    chunks = digest.chunk_message(text)
    assert len(chunks) > 1 and all(len(c) <= digest.DISCORD_CHUNK for c in chunks)
    assert "\n".join(chunks) == text


def test_coverage_health_follows_final_status_not_any_failure(tmp_path):
    cov = tmp_path / "cov"; cov.mkdir()
    _jsonl(cov / "ledger.jsonl", [
        {"session_date": "2026-09-16", "status": "STARTED", "recorded_at": "2026-09-16T20:35:00+00:00"},
        {"session_date": "2026-09-16", "status": "FAILED", "reason": "observer_provider_errors", "detail": "x", "recorded_at": "2026-09-16T20:35:17+00:00"},
        # later retry of the SAME session succeeds
        {"session_date": "2026-09-16", "status": "STARTED", "recorded_at": "2026-09-16T22:05:00+00:00"},
        {"session_date": "2026-09-16", "status": "DONE", "recorded_at": "2026-09-16T22:09:00+00:00"},
    ])
    lane = digest._coverage_lane(cov, date(2026, 9, 16), date(2026, 9, 16))
    assert lane["health"] == "OK"
    assert lane["window"]["sessions_done"] == 1 and lane["window"]["sessions_failed"] == 0
    assert lane["window"]["failed_runs_later_recovered"] == 1
    assert lane["note"] == ""
    # and a session whose final status is still FAILED is unhealthy
    _jsonl(cov / "ledger.jsonl", [
        {"session_date": "2026-09-16", "status": "STARTED", "recorded_at": "2026-09-16T20:35:00+00:00"},
        {"session_date": "2026-09-16", "status": "FAILED", "reason": "observer_provider_errors", "detail": "x", "recorded_at": "2026-09-16T20:35:17+00:00"},
    ])
    lane = digest._coverage_lane(cov, date(2026, 9, 16), date(2026, 9, 16))
    assert lane["health"] == "FAILED_LAST_SESSION" and "observer_provider_errors" in lane["note"]


def test_systemd_units_use_new_york_calendar_after_the_collector():
    root = Path(__file__).resolve().parents[1] / "deploy" / "systemd"
    daily = (root / "afs-paper-collection-daily.timer").read_text()
    weekly = (root / "afs-paper-collection-weekly.timer").read_text()
    assert "OnCalendar=Mon..Fri *-*-* 17:15:00 America/New_York" in daily
    assert "OnCalendar=Fri *-*-* 17:25:00 America/New_York" in weekly
    collector = (root / "afs-coverage-collector.timer").read_text()
    assert "16:45:00 America/New_York" in collector  # #606 contract; the digest fires after it
    for name in ("afs-paper-collection-daily.service", "afs-paper-collection-weekly.service"):
        text = (root / name).read_text()
        assert "scripts.paper_collection_digest" in text and "--post" in text
        assert "discord.com/api/webhooks" not in text
