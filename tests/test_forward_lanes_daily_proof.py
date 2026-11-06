from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ops.forward_lanes_daily_proof import (
    FORWARD_SESSION_START,
    _journal_armed_binding_stats,
    append_jsonl,
    build_report,
    read_env_pins,
)


def test_read_env_pins_allowlist_only(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "EXPECTED_LIVE_COMMIT=abc123\nTRADOVATE_SECRET=hidden\n",
        encoding="utf-8",
    )
    pins = read_env_pins(env, frozenset({"EXPECTED_LIVE_COMMIT"}))
    assert pins == {"EXPECTED_LIVE_COMMIT": "abc123"}
    assert "TRADOVATE_SECRET" not in pins


def test_journal_armed_binding_stats(tmp_path: Path) -> None:
    journal = tmp_path / "options_122_prospective.jsonl"
    rows = [
        {"record_type": "ARMED", "recorded_at": "2026-10-09T15:00:00+00:00"},
        {
            "record_type": "ARMED",
            "recorded_at": "2026-10-12T15:00:00+00:00",
            "canonical_binding": {"strategy_epoch": "122-IEX-E1"},
        },
        {"record_type": "RESOLUTION", "recorded_at": "2026-10-12T16:00:00+00:00"},
    ]
    journal.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    stats = _journal_armed_binding_stats(journal, FORWARD_SESSION_START)
    assert stats["lines_on_or_after_start"] == 2
    assert stats["armed"] == 1
    assert stats["armed_with_canonical_binding"] == 1


def test_build_report_passes_with_stub_runners(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    logs = shared / "logs"
    logs.mkdir(parents=True)
    (shared / ".env").write_text(
        "EXPECTED_LIVE_COMMIT=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
        encoding="utf-8",
    )
    current = tmp_path / "live"
    release = tmp_path / "rel"
    release.mkdir()
    manifest = {
        "repo": {"commit": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
    }
    (release / "release_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    current.symlink_to(release, target_is_directory=True)
    (logs / "six-micro-daily-tsmom").mkdir()
    (logs / "six-micro-daily-tsmom" / "state.json").write_text("{}", encoding="utf-8")
    (logs / "options_122_prospective.jsonl").write_text("", encoding="utf-8")

    def fake_run(cmd: list[str]):
        class Proc:
            stdout = ""
            returncode = 0

        proc = Proc()
        joined = " ".join(cmd)
        if cmd[:3] == ["systemctl", "is-active", "futures-bot"]:
            proc.stdout = "active"
        elif cmd[:3] == ["systemctl", "is-enabled", "six-micro-daily-tsmom.timer"]:
            proc.stdout = "enabled"
        elif cmd[:3] == ["systemctl", "is-active", "options-122-prospective.timer"]:
            proc.stdout = "active"
        elif cmd[:2] == ["systemctl", "show"]:
            proc.stdout = "success\n0\n2026-10-11T00:00:00 UTC\ninactive"
        return proc

    now = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
    report = build_report(
        shared=shared,
        current=current,
        health_url="http://127.0.0.1:9/health",
        now=now,
        run=fake_run,
    )
    assert report["overall"] == "FAIL"  # health URL unreachable in stub


_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_AFTER_FORWARD_START = datetime(2026, 10, 20, 21, 30, tzinfo=timezone.utc)


class _Proc:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class _HealthResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return b'{"ok": true, "live_trading_enabled": false, "broker": "tradovate"}'


def _healthy_lane_tree(
    tmp_path: Path,
    *,
    options_journal: str | None,
    status_tool: bool,
) -> tuple[Path, Path]:
    """Lanes that pass once the options journal and status tool are healthy."""
    shared = tmp_path / "shared"
    logs = shared / "logs"
    logs.mkdir(parents=True)
    (shared / ".env").write_text(f"EXPECTED_LIVE_COMMIT={_SHA}\n", encoding="utf-8")
    release = tmp_path / "rel"
    release.mkdir()
    (release / "release_manifest.json").write_text(
        json.dumps({"repo": {"commit": _SHA}}),
        encoding="utf-8",
    )
    current = tmp_path / "live"
    current.symlink_to(release, target_is_directory=True)
    if status_tool:
        tool = release / "ops" / "options_observer_status.py"
        tool.parent.mkdir(parents=True)
        tool.write_text("raise SystemExit(0)\n", encoding="utf-8")
    heartbeat = {
        "schema": "track-b-daily-heartbeat-v1",
        "session_date_et": "2026-10-20",
        "bars_evaluated": 10,
        "setups_checked": 4,
        "last_bar_time": "2026-10-20T15:55:00-04:00",
        "signal": False,
        "entry_recorded": False,
        "no_signal_reason": "no setup",
        "source": "paper_5m_collector",
        "sealed_by": "demo_eod_fallback",
        "complete": True,
        "bars_fresh": True,
        "fallback_action": "SEALED",
    }
    (logs / "track_b_daily_heartbeat.jsonl").write_text(
        json.dumps(heartbeat) + "\n",
        encoding="utf-8",
    )
    if options_journal is not None:
        (logs / "options_122_prospective.jsonl").write_text(options_journal, encoding="utf-8")
    return shared, current


def _systemd_run(observer_stdout: str):
    def fake_run(cmd: list[str]) -> _Proc:
        joined = " ".join(cmd)
        if cmd[:3] == ["systemctl", "is-active", "futures-bot"]:
            return _Proc("active\n")
        if cmd[:3] == ["systemctl", "is-enabled", "six-micro-daily-tsmom.timer"]:
            return _Proc("disabled\n")
        if cmd[:3] == ["systemctl", "is-active", "six-micro-daily-tsmom.timer"]:
            return _Proc("inactive\n")
        if cmd[:3] == ["systemctl", "is-active", "options-122-prospective.timer"]:
            return _Proc("active\n")
        if cmd[:2] == ["systemctl", "show"]:
            return _Proc("success\n0\n2026-10-20T20:00:00 UTC\ninactive\n")
        if "options_observer_status.py" in joined:
            return _Proc(observer_stdout)
        return _Proc()

    return fake_run


def _options_lane(report: dict) -> dict:
    return next(lane for lane in report["lanes"] if lane["lane"] == "options_122_iex_e1")


def _track_b_lane(report: dict) -> dict:
    return next(lane for lane in report["lanes"] if lane["lane"] == "track_b_wide_stop")


def test_empty_options_journal_is_not_pass(tmp_path: Path, monkeypatch) -> None:
    shared, current = _healthy_lane_tree(tmp_path, options_journal="", status_tool=True)
    monkeypatch.setattr(
        "ops.forward_lanes_daily_proof.urllib.request.urlopen",
        lambda *args, **kwargs: _HealthResponse(),
    )
    report = build_report(
        shared=shared,
        current=current,
        now=_AFTER_FORWARD_START,
        run=_systemd_run('{"overall": "OK", "degraded": []}'),
    )
    options = _options_lane(report)
    assert options["passed"] is False
    assert options["data_status"] == "NO_DATA"
    assert report["overall"] == "FAIL"


def test_missing_options_journal_is_not_pass(tmp_path: Path, monkeypatch) -> None:
    shared, current = _healthy_lane_tree(tmp_path, options_journal=None, status_tool=True)
    monkeypatch.setattr(
        "ops.forward_lanes_daily_proof.urllib.request.urlopen",
        lambda *args, **kwargs: _HealthResponse(),
    )
    report = build_report(
        shared=shared,
        current=current,
        now=_AFTER_FORWARD_START,
        run=_systemd_run('{"overall": "OK", "degraded": []}'),
    )
    options = _options_lane(report)
    assert options["passed"] is False
    assert options["data_status"] == "NO_DATA"
    assert report["overall"] == "FAIL"


def test_missing_options_status_tool_fails_the_check(tmp_path: Path, monkeypatch) -> None:
    row = json.dumps(
        {
            "record_type": "ARMED",
            "recorded_at": "2026-10-20T15:00:00+00:00",
            "canonical_binding": {"strategy_epoch": "122-IEX-E1"},
        }
    )
    shared, current = _healthy_lane_tree(
        tmp_path, options_journal=row + "\n", status_tool=False
    )
    monkeypatch.setattr(
        "ops.forward_lanes_daily_proof.urllib.request.urlopen",
        lambda *args, **kwargs: _HealthResponse(),
    )
    report = build_report(
        shared=shared,
        current=current,
        now=_AFTER_FORWARD_START,
        run=_systemd_run('{"overall": "OK", "degraded": []}'),
    )
    assert report["options_observer"]["passed"] is False
    assert report["options_observer"]["overall"] == "FAILED"
    assert report["overall"] == "FAIL"


def test_malformed_options_status_fails_and_report_is_written(
    tmp_path: Path, monkeypatch
) -> None:
    row = json.dumps(
        {
            "record_type": "ARMED",
            "recorded_at": "2026-10-20T15:00:00+00:00",
            "canonical_binding": {"strategy_epoch": "122-IEX-E1"},
        }
    )
    shared, current = _healthy_lane_tree(
        tmp_path, options_journal=row + "\n", status_tool=True
    )
    monkeypatch.setattr(
        "ops.forward_lanes_daily_proof.urllib.request.urlopen",
        lambda *args, **kwargs: _HealthResponse(),
    )
    report = build_report(
        shared=shared,
        current=current,
        now=_AFTER_FORWARD_START,
        run=_systemd_run("{not-json"),
    )
    log = tmp_path / "proof.jsonl"
    append_jsonl(log, report)
    saved = json.loads(log.read_text(encoding="utf-8"))
    assert report["options_observer"]["passed"] is False
    assert report["options_observer"]["overall"] == "FAILED"
    assert report["overall"] == "FAIL"
    assert saved["overall"] == "FAIL"
    assert "lanes" in saved


def test_dropped_connection_marks_track_b_down_and_writes_report(
    tmp_path: Path, monkeypatch
) -> None:
    row = json.dumps(
        {
            "record_type": "ARMED",
            "recorded_at": "2026-10-20T15:00:00+00:00",
            "canonical_binding": {"strategy_epoch": "122-IEX-E1"},
        }
    )
    shared, current = _healthy_lane_tree(
        tmp_path, options_journal=row + "\n", status_tool=True
    )

    def dropped(*args, **kwargs):
        raise ConnectionResetError("Connection reset by peer")

    monkeypatch.setattr("ops.forward_lanes_daily_proof.urllib.request.urlopen", dropped)
    report = build_report(
        shared=shared,
        current=current,
        now=_AFTER_FORWARD_START,
        run=_systemd_run('{"overall": "OK", "degraded": []}'),
    )
    log = tmp_path / "proof.jsonl"
    append_jsonl(log, report)
    track_b = _track_b_lane(report)
    assert track_b["passed"] is False
    assert track_b["health"]["status"] == "DOWN"
    assert track_b["health"]["error"] == "ConnectionResetError"
    assert report["overall"] == "FAIL"
    assert json.loads(log.read_text(encoding="utf-8"))["overall"] == "FAIL"
