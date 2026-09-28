"""Regression coverage for read-only server resource diagnostics."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).parent.parent
WATCHER_DIR = ROOT / "ops" / "afs_watcher"


def _load_watcher():
    if str(WATCHER_DIR) not in sys.path:
        sys.path.insert(0, str(WATCHER_DIR))
    spec = importlib.util.spec_from_file_location(
        "afs_watcher_resource_diagnostics", WATCHER_DIR / "watcher.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


w = _load_watcher()


def test_deployed_memory_guard_matches_canonical_source():
    assert (ROOT / "ops" / "watcher_memory_guard.py").read_bytes() == (
        WATCHER_DIR / "watcher_memory_guard.py"
    ).read_bytes()


def test_pressure_parser(tmp_path):
    pressure = tmp_path / "memory"
    pressure.write_text(
        "some avg10=0.10 avg60=0.20 avg300=0.30 total=123\n"
        "full avg10=0.01 avg60=0.02 avg300=0.03 total=45\n",
        encoding="utf-8",
    )
    got = w._read_pressure(pressure)
    assert got["some"] == {
        "avg10": 0.10, "avg60": 0.20, "avg300": 0.30, "total": 123
    }
    assert got["full"]["total"] == 45


def test_service_resource_snapshot_reads_fd_threads_and_cgroup(tmp_path):
    proc = tmp_path / "proc"
    cgroups = tmp_path / "cgroup"
    pid = 123
    pdir = proc / str(pid)
    (pdir / "fd").mkdir(parents=True)
    for i in range(8):
        (pdir / "fd" / str(i)).touch()
    (pdir / "status").write_text(
        "Name:\tpython\nVmRSS:\t102400 kB\nVmSwap:\t10240 kB\nThreads:\t7\n",
        encoding="utf-8",
    )
    (pdir / "limits").write_text(
        "Limit                     Soft Limit           Hard Limit           Units\n"
        "Max open files            10                   20                   files\n",
        encoding="utf-8",
    )
    (pdir / "cgroup").write_text(
        "0::/system.slice/futures-bot.service\n", encoding="utf-8"
    )
    cg = cgroups / "system.slice" / "futures-bot.service"
    cg.mkdir(parents=True)
    (cg / "memory.current").write_text("123456\n", encoding="utf-8")
    (cg / "memory.events").write_text(
        "low 0\nhigh 2\nmax 0\noom 1\noom_kill 0\n", encoding="utf-8"
    )

    got = w._service_resource_snapshot(
        pid, proc_root=proc, cgroup_root=cgroups
    )
    assert got["rss_mb"] == 100.0
    assert got["swap_mb"] == 10.0
    assert got["threads"] == 7
    assert got["fd_count"] == 8
    assert got["fd_soft_limit"] == 10
    assert got["fd_fraction"] == 0.8
    assert got["cgroup_memory_current_bytes"] == 123456
    assert got["cgroup_memory_events"]["oom"] == 1


def test_check_resources_warns_before_fd_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "LOG_DIR", tmp_path)
    monkeypatch.setattr(w, "_read_pressure", lambda: {})
    monkeypatch.setattr(
        w, "_fs_usage",
        lambda _p: {"used_fraction": 0.1, "total_bytes": 100, "used_bytes": 10, "free_bytes": 90},
    )
    monkeypatch.setattr(w, "_top_memory_processes", lambda: [])
    monkeypatch.setattr(
        w, "_service_resource_snapshot",
        lambda _pid: {
            "pid": _pid,
            "rss_mb": 100.0,
            "swap_mb": 0.0,
            "threads": 5,
            "fd_count": 800,
            "fd_soft_limit": 1000,
            "fd_fraction": 0.8,
            "cgroup_memory_current_bytes": None,
            "cgroup_memory_events": {},
        },
    )

    def fake_run(cmd, **_kwargs):
        if cmd[:2] == ["systemctl", "show"]:
            return 0, "ActiveState=active\nExecMainPID=222\nNRestarts=0\n"
        raise AssertionError(cmd)

    monkeypatch.setattr(w, "run", fake_run)
    findings = w.Findings()
    tick = {
        "runtime": {
            "service": {
                "ActiveState": "active",
                "ExecMainPID": "111",
                "NRestarts": "0",
            }
        }
    }
    w.check_resources({}, findings, tick)

    keys = {row["key"] for row in findings.warns()}
    assert keys == {"fd_pressure_futures_bot", "fd_pressure_options_scanner"}
    assert tick["resources"]["services"]["futures-bot"]["fd_count"] == 800
    assert tick["resources"]["services"]["options-scanner"]["fd_count"] == 800


def test_tmpfs_telemetry_append_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setattr(w, "STATE_DIR", tmp_path)
    monkeypatch.setattr(w, "STATE_LOG_CAP_BYTES", {"memory.jsonl": 131072})
    path = tmp_path / "memory.jsonl"
    line = "x" * 1023 + "\n"
    for _ in range(200):
        w.state_append(path, line)
    assert path.stat().st_size <= 131072
    assert path.read_text(encoding="utf-8").endswith("\n")


def test_resource_warning_card_keeps_the_actual_fd_detail(monkeypatch):
    monkeypatch.setattr(w, "RELEASE_SHA", "0123456789abcdef")
    finding = {
        "summary": "options-scanner has 800 of 1000 file handles open (80%)",
        "detail": {},
    }
    text = w._finding_discord_text(
        "WARNING", "fd_pressure_options_scanner", finding, "/tmp/snapshot"
    )
    assert "Options scanner is running out of file handles" in text
    assert "800 of 1000 file handles open" in text
    assert "file-handle leak" in text
