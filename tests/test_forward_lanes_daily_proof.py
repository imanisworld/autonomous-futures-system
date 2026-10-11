from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from ops.forward_lanes_daily_proof import (
    FORWARD_SESSION_START,
    _journal_armed_binding_stats,
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
