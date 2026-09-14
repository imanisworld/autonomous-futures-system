"""Regression tests for the watcher's hypothetical-ledger lane checks.

Defect proven 2026-09-14 (read-only monitoring audit): the watcher was armed on
the retired inverse-ORB epoch and the forward_ab campaign only. The Daily 2-2
lane held an OPEN MNQ swing since 2026-09-10 while DAILY PASS was reported three
times, and a 2026-09-11 03:35Z 5-minute bar went missing during that exposure
with no alert — every freshness check watched the 15-minute stream.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

WATCHER_DIR = Path(__file__).parent.parent / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location("afs_watcher_lanes", WATCHER_DIR / "watcher.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()
NOW = datetime(2026, 9, 14, 10, 5, tzinfo=timezone.utc)
EPOCH_PIN = "2026-09-09T04:21:04Z"


def _touch(path: Path, when: datetime, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (when.timestamp(), when.timestamp()))


def _setup(tmp_path, monkeypatch, *, five_min_at: datetime, fifteen_min_at: datetime,
           d22_state_at: datetime | None = None, d22_position: bool = True,
           d22_epoch: str = "2026-09-09T04:21:04+00:00", wide_mode: str = "paper_sim",
           mes_mode: str = "paper_sim", mes_journal_at: datetime | None = None,
           mes_15m_at: datetime | None = None):
    log_dir = tmp_path / "logs"
    ledger = log_dir / "hypothetical_ledger"
    _touch(log_dir / "bars_MNQ_2026-09-14.jsonl", fifteen_min_at, '{"timeframe":"15"}\n')
    _touch(log_dir / "tf5m" / "bars_MNQ_2026-09-14.jsonl", five_min_at, '{"timeframe":"5m"}\n')
    state = {
        "balance": 5000.0, "epoch": d22_epoch, "halted": False, "peak": 5000.0, "seen": [],
        "position": {
            "direction": "SHORT", "entry": 29338.25, "stop": 29634.25, "target": 28728.25,
            "entry_time": "2026-09-10T11:10:00+00:00", "paper_order_id": "PAPER-38b3",
        } if d22_position else None,
    }
    _touch(ledger / "daily_22_5k" / "swing_state.json", d22_state_at or five_min_at, json.dumps(state))
    _touch(ledger / "wide_stop_6k" / "forward_collector_state.json", NOW - timedelta(days=3),
           json.dumps({"filled_count": 0, "filled_date": "2026-09-11", "position": None, "seen": []}))
    _touch(log_dir / "bars_MES_2026-09-14.jsonl", mes_15m_at or fifteen_min_at, '{"timeframe":"15"}\n')
    _touch(ledger / "mes_122_1500" / "journal_2026-09-14.jsonl", mes_journal_at or fifteen_min_at,
           json.dumps({"ts": "2026-09-14T10:00:19+00:00", "instrument": "MES", "decision": "NO_TRADE"}) + "\n")
    env = tmp_path / ".env"
    env.write_text(
        f"WIDE_STOP_LEDGER_MODE={wide_mode}\nWIDE_STOP_LEDGER_EPOCH_START={EPOCH_PIN}\n"
        f"MES_122_PAPER_MODE={mes_mode}\nMES_122_PAPER_EPOCH_START=2026-09-09T06:12:55Z\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(w, "LOG_DIR", log_dir)
    monkeypatch.setattr(w, "LEDGER_DIR", ledger)
    monkeypatch.setattr(w, "FIVE_MIN_DIR", log_dir / "tf5m")
    monkeypatch.setattr(w, "ENV_FILE", env)
    monkeypatch.setattr(w, "now_utc", lambda: NOW)
    monkeypatch.setattr(w, "read_prod_text", lambda path: Path(path).read_text(encoding="utf-8"))
    monkeypatch.setattr(w, "notify", lambda *a, **k: None)
    monkeypatch.setattr(w, "state_append", lambda *a, **k: None)
    monkeypatch.setattr(w, "log", lambda *a, **k: None)
    state = {"events_seen": {}, "notified": {}}
    findings = w.Findings()
    tick: dict = {}
    w.check_lanes(state, findings, tick)
    return state, findings, tick


def _keys(findings):
    return {item["key"] for item in findings.items}


def test_static_selfcheck_still_passes_with_lane_checks():
    w.static_selfcheck()  # raises SystemExit on any forbidden token


def test_open_swing_with_fresh_bars_is_recorded_not_blocked(tmp_path, monkeypatch):
    state, findings, tick = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(minutes=5),
                                   fifteen_min_at=NOW - timedelta(minutes=5))
    assert not findings.blocked()
    assert tick["lanes"]["open_positions"] == ["daily_22_5k"]
    assert tick["lanes"]["inventory"]["daily_22_5k"]["open_position"]["direction"] == "SHORT"
    assert tick["lanes"]["inventory"]["wide_stop_4k"]["exists"] is False
    assert tick["lanes"]["five_min_feed_stalled"] is False
    # The operator is told once that a paper position is open.
    assert any(k.startswith("hypothetical_position_open:daily_22_5k") for k in state["events_seen"])


def test_five_min_stall_while_15m_continues_blocks_and_flags_exposure(tmp_path, monkeypatch):
    _, findings, tick = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(minutes=50),
                               fifteen_min_at=NOW - timedelta(minutes=5),
                               d22_state_at=NOW - timedelta(minutes=50))
    keys = _keys(findings)
    assert "five_min_feed_stalled" in keys
    assert "hypothetical_position_exposed_stale_bars" in keys
    assert tick["lanes"]["five_min_feed_stalled"] is True
    stall = next(i for i in findings.items if i["key"] == "five_min_feed_stalled")
    assert "every MNQ paper lane resolves on 5m bars" in stall["summary"]


def test_five_min_stall_with_flat_lanes_blocks_feed_only(tmp_path, monkeypatch):
    _, findings, _ = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(minutes=50),
                            fifteen_min_at=NOW - timedelta(minutes=5), d22_position=False,
                            d22_state_at=NOW - timedelta(minutes=50))
    keys = _keys(findings)
    assert "five_min_feed_stalled" in keys
    assert "hypothetical_position_exposed_stale_bars" not in keys


def test_quiet_15m_and_5m_together_is_not_a_stall(tmp_path, monkeypatch):
    # Overnight / weekend: both streams stop together. Not a defect.
    _, findings, _ = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(hours=6),
                            fifteen_min_at=NOW - timedelta(hours=6), d22_state_at=NOW - timedelta(hours=6))
    assert not findings.blocked()


def test_daily_collector_stalled_while_5m_bars_flow(tmp_path, monkeypatch):
    # Bars arrive every 5 minutes but swing_state.json stopped being rewritten:
    # the router raised before the Daily collector (state-integrity or wide-stop
    # error) on every bar. Distinguishes "alive, no setup" from "dead".
    _, findings, _ = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(minutes=5),
                            fifteen_min_at=NOW - timedelta(minutes=5),
                            d22_state_at=NOW - timedelta(minutes=40))
    assert "daily_22_collector_stalled" in _keys(findings)


def test_state_epoch_differing_from_pin_blocks(tmp_path, monkeypatch):
    _, findings, _ = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(minutes=5),
                            fifteen_min_at=NOW - timedelta(minutes=5),
                            d22_epoch="2026-09-05T17:45:00+00:00")
    assert "daily_22_state_epoch_mismatch" in _keys(findings)


def test_mes_lane_stall_behind_mes_15m_bars_blocks(tmp_path, monkeypatch):
    _, findings, _ = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(minutes=5),
                            fifteen_min_at=NOW - timedelta(minutes=5),
                            mes_15m_at=NOW - timedelta(minutes=5), mes_journal_at=NOW - timedelta(minutes=50))
    assert "mes_122_lane_stalled" in _keys(findings)


def test_lanes_inert_when_pins_say_observe_only(tmp_path, monkeypatch):
    # Nothing is expected of an inert lane; nothing may block on it either.
    _, findings, tick = _setup(tmp_path, monkeypatch, five_min_at=NOW - timedelta(hours=5),
                               fifteen_min_at=NOW - timedelta(minutes=5), wide_mode="observe_only",
                               mes_mode="observe_only", mes_journal_at=NOW - timedelta(hours=5),
                               d22_state_at=NOW - timedelta(hours=5))
    assert not findings.blocked()
    assert tick["lanes"]["wide_stop_paper_sim"] is False


def test_finding_titles_and_fixes_exist_for_new_keys():
    for key in ("five_min_feed_stalled", "five_min_feed_missing", "daily_22_collector_stalled",
                "daily_22_state_epoch_mismatch", "mes_122_lane_stalled",
                "hypothetical_position_exposed_stale_bars"):
        assert w._finding_title(key) != key.replace("_", " ").capitalize()
        assert w.smallest_fix(key) != "operator: inspect the snapshot; no automatic fix"
