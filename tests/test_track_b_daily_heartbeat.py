"""AFS-0332 Track B evaluation heartbeat proof; synthetic storage only."""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from config.settings import load_config
from context import wide_stop_forward_collector as collector
from context.track_b_daily_heartbeat import (
    JOURNAL_NAME, read_session, record_bar, seal_session,
)
from ops.forward_lanes_daily_proof import (
    check_six_micro, last_completed_track_b_session, track_b_heartbeat_proof,
)


def _cfg():
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = "paper_sim"
    cfg.wide_stop_ledger_epoch_start = "2026-10-12T00:00:00+00:00"
    return cfg


def _payload(ts):
    return SimpleNamespace(
        ticker="MNQ1!", timestamp=ts, timeframe="5m",
        open=20_001.0, high=20_010.0, low=19_990.0,
        close=20_005.0, volume=1000,
    )


def _no_signal_day(tmp_path, monkeypatch):
    # This is a successfully completed canonical decision, without a trade
    # or candidate. The reader must not confuse it with failed evaluation.
    def evaluated(**kwargs):
        if kwargs["strategy"] == collector.FOUR_HR:
            return (
                SimpleNamespace(decision="WAIT", setup=None, reason="No 4HR setup"),
                object(), None, None,
            )
        return None, None, None, None

    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", evaluated)
    monkeypatch.setattr(collector, "_resolve_one_position", lambda **kwargs: None)
    for ts in ("2026-10-20T14:05:00+00:00", "2026-10-20T19:55:00+00:00"):
        collector.process_five_min_bar(
            payload=_payload(ts), cfg=_cfg(), bars_5m=[],
            log_dir=tmp_path, for_date=date(2026, 10, 20),
        )


def test_no_signal_day_seals_one_valid_heartbeat(tmp_path, monkeypatch):
    _no_signal_day(tmp_path, monkeypatch)
    now = datetime(2026, 10, 20, 20, 2, tzinfo=timezone.utc)
    first = seal_session(tmp_path, now=now, fallback_action="NO_ACTION")
    second = seal_session(tmp_path, now=now, fallback_action="NO_ACTION")
    assert first == second
    assert first["complete"] is True
    assert first["bars_evaluated"] == 2
    assert first["setups_checked"] == 1
    assert first["signal"] is False
    assert first["entry_recorded"] is False
    assert first["no_signal_reason"] == "No 4HR setup"
    assert len((tmp_path / JOURNAL_NAME).read_text().splitlines()) == 1
    assert read_session(tmp_path, date(2026, 10, 20)) == first

    shared = tmp_path / "shared"
    (shared / "logs").mkdir(parents=True)
    (shared / "logs" / JOURNAL_NAME).write_text(
        (tmp_path / JOURNAL_NAME).read_text(), encoding="utf-8"
    )
    verified = track_b_heartbeat_proof(
        shared, datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc)
    )
    assert verified["passed"] is True
    assert verified["status"] == "OK — no signal"


def test_missing_heartbeat_fails_even_when_service_is_healthy(tmp_path):
    report = track_b_heartbeat_proof(
        tmp_path, datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc)
    )
    assert report["passed"] is False
    assert report["reason"] == "MISSING_HEARTBEAT"


def test_stale_last_bar_fails(tmp_path):
    record_bar(
        tmp_path / "logs",
        current_ts=datetime(2026, 10, 20, 14, 5, tzinfo=timezone.utc),
        checked=True, signal=False, reason="NO_4HR_SETUP", entry_recorded=False,
    )
    seal_session(
        tmp_path / "logs",
        now=datetime(2026, 10, 20, 20, 2, tzinfo=timezone.utc),
        fallback_action="NO_ACTION",
    )
    report = track_b_heartbeat_proof(
        tmp_path, datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc)
    )
    assert report["passed"] is False
    assert report["reason"] in {"STALE_LAST_BAR", "INCOMPLETE_OR_INVALID_HEARTBEAT"}


def test_duplicate_sealed_rows_fail_closed(tmp_path, monkeypatch):
    _no_signal_day(tmp_path / "logs", monkeypatch)
    first = seal_session(
        tmp_path / "logs",
        now=datetime(2026, 10, 20, 20, 2, tzinfo=timezone.utc),
        fallback_action="NO_ACTION",
    )
    with (tmp_path / "logs" / JOURNAL_NAME).open("a") as handle:
        handle.write(json.dumps(first) + "\n")
    report = track_b_heartbeat_proof(
        tmp_path, datetime(2026, 10, 20, 21, 25, tzinfo=timezone.utc)
    )
    assert report["passed"] is False
    assert report["reason"] == "DUPLICATE_HEARTBEAT"


def test_last_completed_session_skips_weekend():
    assert last_completed_track_b_session(
        datetime(2026, 10, 19, 14, 0, tzinfo=timezone.utc)
    ) == date(2026, 10, 16)


def _fake_run_for_retirement(*, enabled="disabled", active="inactive"):
    def run(cmd):
        if cmd[:3] == ["systemctl", "is-enabled", "six-micro-daily-tsmom.timer"]:
            stdout = enabled
        elif cmd[:3] == ["systemctl", "is-active", "six-micro-daily-tsmom.timer"]:
            stdout = active
        elif cmd[:2] == ["systemctl", "show"]:
            stdout = "success\n0\n2026-10-17T00:00:00 UTC\ninactive\n"
        else:
            stdout = ""
        return SimpleNamespace(stdout=stdout, returncode=0)
    return run


def test_six_micro_retired_is_not_failed(tmp_path):
    result = check_six_micro(
        shared=tmp_path, run=_fake_run_for_retirement(),
        today_et=date(2026, 10, 20),
    )
    assert result["status"] == "RETIRED"
    assert result["passed"] is True
    assert result["retired_on_et"] == "2026-10-18"


def test_retired_timer_reactivation_still_fails(tmp_path):
    result = check_six_micro(
        shared=tmp_path, run=_fake_run_for_retirement(enabled="enabled", active="active"),
        today_et=date(2026, 10, 20),
    )
    assert result["status"] == "RETIRED"
    assert result["passed"] is False
    assert result["reason"] == "RETIRED_TIMER_STILL_ARMED"
