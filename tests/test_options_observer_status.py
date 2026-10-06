from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops import options_observer_status as status

NOW = datetime(2026, 10, 6, 15, 0, tzinfo=timezone.utc)
RELEASE = "db9bc7e2c00559fc969af7be0e2cb12b00a1454c"


def _show(**props: str):
    calls: list[list[str]] = []

    def run(argv: list[str]) -> tuple[int, str]:
        calls.append(argv)
        return 0, "".join(f"{k}={v}\n" for k, v in props.items())

    return run, calls


def _pinned_service_props() -> dict[str, str]:
    return {
        "Id": "options-122-prospective.service",
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "SubState": "dead",
        "Result": "success",
        "WorkingDirectory": f"/root/afs-releases/{RELEASE}",
        "ExecStart": (
            f"{{ path=/root/afs-releases/{RELEASE}/.venv/bin/python ; argv[]="
            f"/root/afs-releases/{RELEASE}/.venv/bin/python -m scripts.options_122_prospective_collect "
            "--env-file /root/afs-shared/.env --journal /x ; }"
        ),
        "DropInPaths": "/etc/systemd/system/options-122-prospective.service.d/10-release.conf",
        # Even if systemd ever returned it, an Environment line must not leak.
        "Environment": "PUBLIC_API_KEY=supersecretvalue",
    }


def test_systemctl_is_called_with_property_allowlist_only():
    run, calls = _show(**_pinned_service_props())
    row = status.unit_status("options-122-prospective.service", run)
    argv = calls[0]
    assert argv[:3] == ["systemctl", "show", "options-122-prospective.service"]
    props = argv[argv.index("-p") + 1].split(",")
    assert not [p for p in props if "nviron" in p or "redential" in p]
    assert "Environment" not in row
    assert "supersecret" not in json.dumps(row)
    # argv values (env-file path, journal path) are dropped; only path + module kept.
    assert "env-file" not in json.dumps(row)
    assert row["ExecStartModule"] == "scripts.options_122_prospective_collect"


def test_pinned_release_is_classified_and_sha_not_redacted():
    run, _ = _show(**_pinned_service_props())
    row = status.unit_status("options-122-prospective.service", run)
    assert row["runtime_tree"] == {"classification": "PINNED_RELEASE", "release": RELEASE}
    assert RELEASE in row["WorkingDirectory"]
    assert row["health"] == "OK"


def test_repo_unit_file_shape_is_flagged_as_live_tree():
    """The tracked unit file runs from the mutable live path; that must be visible."""
    props = _pinned_service_props()
    props["WorkingDirectory"] = "/root/autonomous-futures-system"
    props["ExecStart"] = "{ path=/root/autonomous-futures-system/.venv/bin/python ; argv[]=x ; }"
    run, _ = _show(**props)
    row = status.unit_status("options-122-prospective.service", run)
    assert row["runtime_tree"]["classification"] == "LIVE_TREE"


def test_mixed_tree_is_flagged():
    assert (
        status.classify_runtime_tree(
            f"/root/afs-releases/{RELEASE}", "/root/autonomous-futures-system/.venv/bin/python"
        )["classification"]
        == "LIVE_TREE"
    )
    assert (
        status.classify_runtime_tree(f"/root/afs-releases/{RELEASE}", "/usr/bin/python3")[
            "classification"
        ]
        == "MIXED"
    )


def test_unit_outside_allowlist_is_refused():
    run, calls = _show()
    with pytest.raises(ValueError):
        status.unit_status("futures-bot.service", run)
    assert calls == []


def test_failed_oneshot_and_inactive_timer_are_degraded():
    props = _pinned_service_props()
    props["Result"] = "exit-code"
    run, _ = _show(**props)
    assert status.unit_status("options-122-prospective.service", run)["health"] == "DEGRADED"
    run, _ = _show(Id="options-122-prospective.timer", LoadState="loaded", ActiveState="inactive")
    assert status.unit_status("options-122-prospective.timer", run)["health"] == "DEGRADED"


def test_systemctl_failure_is_unknown_not_ok():
    row = status.unit_status("options-scanner.service", lambda argv: (1, ""))
    assert row["health"] == "UNKNOWN"


def _write_journal(root: Path, rows: list[object]) -> Path:
    path = root / "options_122_prospective.jsonl"
    path.write_text("".join((r if isinstance(r, str) else json.dumps(r)) + "\n" for r in rows))
    return path


def test_journal_existence_size_hash_counts_and_last_event(tmp_path):
    rows = [
        {"record_type": "ARMED", "observed_at": "2026-10-06T14:00:00+00:00", "setup_id": "SPY|a"},
        {
            "record_type": "RESOLUTION",
            "observed_at": "2026-10-06T14:55:00+00:00",
            "setup_id": "SPY|a",
            "option_evidence": {"contract": "SPY", "raw": "x" * 500},
            "note": "webhook=https://discord.com/api/webhooks/123/abcdef",
        },
    ]
    path = _write_journal(tmp_path, rows)
    out = status.journal_status(tmp_path, "options_122_prospective", tail=5, now=NOW)
    assert out["exists"] is True
    assert out["size_bytes"] == path.stat().st_size
    assert out["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert out["lines"] == 2
    assert out["record_type_counts"] == {"ARMED": 1, "RESOLUTION": 1}
    assert out["last_event_at"] == "2026-10-06T14:55:00+00:00"
    assert out["last_event_age_seconds"] == 300.0
    # Tail is projected onto allowlisted keys: nested evidence and free text are dropped.
    assert all(set(row) <= set(status.TAIL_KEYS) for row in out["tail"])
    assert "discord" not in json.dumps(out)
    assert out["health"] == "OK"


def test_tail_is_capped(tmp_path):
    _write_journal(
        tmp_path,
        [{"record_type": "ARMED", "observed_at": f"2026-10-06T14:{i:02d}:00+00:00"} for i in range(50)],
    )
    out = status.journal_status(tmp_path, "options_122_prospective", tail=999, now=NOW)
    assert len(out["tail"]) == status.MAX_TAIL
    assert out["tail"][-1]["observed_at"] == "2026-10-06T14:49:00+00:00"


def test_malformed_lines_are_counted_and_degrade(tmp_path):
    _write_journal(tmp_path, [{"record_type": "ARMED"}, "{not json", "[1,2]"])
    out = status.journal_status(tmp_path, "options_122_prospective", now=NOW)
    assert out["malformed_lines"] == 2
    assert out["health"] == "DEGRADED"


def test_missing_journal_is_degraded(tmp_path):
    out = status.journal_status(tmp_path, "options_122_prospective", now=NOW)
    assert out == {
        "journal": "options_122_prospective",
        "file": "options_122_prospective.jsonl",
        "exists": False,
        "health": "DEGRADED",
    }


def test_symlink_escape_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / ".env"
    secret.write_text("PUBLIC_API_KEY=supersecret\n")
    root = tmp_path / "logs"
    root.mkdir()
    (root / "options_122_prospective.jsonl").symlink_to(secret)
    out = status.journal_status(root, "options_122_prospective", now=NOW)
    assert out["error"] == "path_outside_root"
    assert "supersecret" not in json.dumps(out)


def test_journal_name_outside_allowlist_is_refused(tmp_path):
    with pytest.raises(ValueError):
        status.journal_status(tmp_path, "../.env")


def test_redact_hides_urls_bearer_and_opaque_tokens_but_keeps_shas():
    text = (
        "Bearer abcdefghijklmnop https://x.example/hook token=zzz "
        "AbCdEfGhIjKlMnOpQrStUvWxYz012345678 "
        f"{RELEASE}"
    )
    out = status.redact(text)
    assert "abcdefghijklmnop" not in out
    assert "x.example" not in out
    assert "zzz" not in out
    assert "AbCdEfGhIjKlMnOpQrStUvWxYz012345678" not in out
    assert RELEASE in out


def test_heartbeat_absent_is_reported_not_faked(tmp_path):
    out = status.heartbeat_status(tmp_path, "options_prospective_trigger_monitor", now=NOW)
    assert out["exists"] is False
    assert out["health"] == "NOT_PRESENT"


def test_heartbeat_age(tmp_path):
    (tmp_path / "options_prospective_trigger_monitor_heartbeat.json").write_text(
        json.dumps({"ts": "2026-10-06T14:59:30+00:00", "state": "WATCHING"})
    )
    out = status.heartbeat_status(tmp_path, "options_prospective_trigger_monitor", now=NOW)
    assert out["age_seconds"] == 30.0
    assert out["state"] == "WATCHING"


def test_snapshot_flags_live_tree_and_is_read_only(tmp_path):
    _write_journal(tmp_path, [{"record_type": "ARMED", "observed_at": "2026-10-06T14:00:00+00:00"}])
    before = sorted(p.name for p in tmp_path.iterdir())
    props = _pinned_service_props()
    props["WorkingDirectory"] = "/root/autonomous-futures-system"
    run, _ = _show(**props)
    report = status.snapshot(
        root=tmp_path, units=["options-122-prospective.service"], run=run, now=NOW
    )
    assert report["read_only"] is True and report["execution_authority"] is False
    assert report["runtime_integrity"] == {
        "units_running_from_live_tree": ["options-122-prospective.service"],
        "ok": False,
    }
    assert report["overall"] == "DEGRADED"
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_module_imports_stdlib_only():
    import ast

    tree = ast.parse(Path(status.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    import sys

    assert imported - {"__future__"} <= set(sys.stdlib_module_names)


def test_cli_rejects_relative_root_and_bad_tail(monkeypatch, capsys):
    monkeypatch.setenv("AFS_OBSERVER_STATUS_LOG_ROOT", "relative/logs")
    with pytest.raises(SystemExit) as exc:
        status.main(["--tail", "3"])
    assert exc.value.code == 2
    monkeypatch.setenv("AFS_OBSERVER_STATUS_LOG_ROOT", "/tmp")
    with pytest.raises(SystemExit):
        status.main(["--tail", "21"])


def test_release_pin_template_keeps_frozen_collector_arguments():
    """The pin may change only the code tree, never the frozen 122-IEX-E1 argv."""
    root = Path(__file__).resolve().parents[1] / "ops" / "systemd"
    base = (root / "options-122-prospective.service").read_text()
    template = (root / "options-122-prospective.service.d" / "10-release.conf.template").read_text()

    def argv(text: str) -> list[str]:
        lines = [l for l in text.splitlines() if l.startswith("ExecStart=") and l != "ExecStart="]
        assert len(lines) == 1
        return lines[0].split("=", 1)[1].split()

    base_argv, pinned_argv = argv(base), argv(template)
    assert base_argv[1:] == pinned_argv[1:]
    assert pinned_argv[0] == "/root/afs-releases/@RELEASE_SHA@/.venv/bin/python"
    assert "WorkingDirectory=/root/afs-releases/@RELEASE_SHA@" in template
    assert status.classify_runtime_tree(
        "/root/afs-releases/abc", "/root/afs-releases/abc/.venv/bin/python"
    )["classification"] == "PINNED_RELEASE"
