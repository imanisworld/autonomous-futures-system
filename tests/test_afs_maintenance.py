"""Local tests for the restricted maintenance interface. No VPS calls."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import stat
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MAINTENANCE_PATH = ROOT / "scripts" / "afs_maintenance.py"
INSTALL_PATH = ROOT / "scripts" / "install_afs_maintenance.py"
SUDOERS_PATH = ROOT / "scripts" / "afs-maintenance.sudoers"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


afs = _load("afs_maintenance_test", MAINTENANCE_PATH)
installer = _load("install_afs_maintenance_test", INSTALL_PATH)


class FakeHost(afs.Host):
    def __init__(self) -> None:
        self.scanner = {
            "ActiveState": "active",
            "MainPID": "1695873",
            "NRestarts": "0",
            "MemoryMax": str(350 * afs.MIB),
            "ControlGroup": afs.EXPECTED_CGROUP,
        }
        self.futures = {
            "ActiveState": "active",
            "MainPID": "1851835",
            "NRestarts": "0",
            "MemoryMax": "infinity",
            "ControlGroup": "/system.slice/futures-bot.service",
        }
        self.cgroup = {
            "memory.max": str(350 * afs.MIB),
            "memory.current": "5361664",
            "memory.swap.current": "501694464",
        }
        self.health = "200"
        self.base = "[Service]\nMemoryMax=350M\nExecStart=/usr/bin/true\n"
        self.etc = {"no-bytecode.conf": "Environment=PYTHONDONTWRITEBYTECODE=1\n"}
        self.runtime: dict[str, str] = {}
        self.records: dict[Path, str] = {}
        self.calls: list[tuple[str, ...]] = []
        self.reload_updates_property = True
        self.reload_updates_live = True
        self.change_pid_on_reload = False
        self.fail_health_on_reload = False
        self.change_futures_on_reload = False
        self.cgroup_write_error = False
        self.reload_count = 0
        self.fail_reload_on_call: int | None = None
        self.fail_remove_runtime: set[str] = set()
        self.etc_control: dict[str, str] = {}
        self.run_control: dict[str, str] = {}
        self.futures_dropin_paths = ""
        self.extra_scanner_paths: list[str] = []
        self.crash_on_reload = False
        self.cgroup_write_sticks = True
        self.replace_dropin_on_reload: str | None = None
        self.runtime_mutation: tuple[str, str] | None = None
        self.futures_dropin_on_reload: str | None = None
        self.fail_history = None
        self.recovery_drift: str | None = None
        self.clock = datetime(2026, 10, 8, 20, 45, tzinfo=timezone.utc)
        self.environ = {"SUDO_USER": "grok-audit", "SSH_CONNECTION": "test-connection"}
        self.approvals: dict[str, dict[str, object]] = {}

    def _scanner_paths(self) -> str:
        paths = [str(afs.ETC_DROPIN_DIR / name) for name in sorted(self.etc)]
        return " ".join([*paths, *self.extra_scanner_paths])

    def systemctl_show(self, unit: str) -> dict[str, str]:
        self.calls.append(("show", unit))
        if unit == afs.SCANNER_UNIT:
            props = dict(self.scanner)
            props.setdefault("NeedDaemonReload", "no")
            props["DropInPaths"] = self._scanner_paths()
            return props
        if unit == afs.FUTURES_UNIT:
            props = dict(self.futures)
            props.setdefault("NeedDaemonReload", "no")
            props["DropInPaths"] = self.futures_dropin_paths
            return props
        raise afs.Denied("unit is not available to maintenance")

    def daemon_reload(self) -> None:
        self.reload_count += 1
        self.calls.append(("daemon-reload",))
        if self.crash_on_reload:
            raise SystemExit("killed")
        if self.fail_reload_on_call == self.reload_count:
            raise OSError("reload failed")
        text = self.etc.get(afs.DROPIN_NAME)
        if text:
            match = re.search(r"MemoryMax=(\d+)M", text)
            assert match is not None
            raw = str(int(match.group(1)) * afs.MIB)
        else:
            raw = str(350 * afs.MIB)
        if self.reload_updates_property:
            self.scanner["MemoryMax"] = raw
        if self.reload_updates_live:
            self.cgroup["memory.max"] = self.scanner["MemoryMax"]
        if self.change_pid_on_reload:
            self.scanner["MainPID"] = str(int(self.scanner["MainPID"]) + 1)
            self.change_pid_on_reload = False
        if self.fail_health_on_reload:
            self.health = "000"
            self.fail_health_on_reload = False
        if self.change_futures_on_reload:
            self.futures["MemoryMax"] = "1"
            self.change_futures_on_reload = False
        if self.replace_dropin_on_reload is not None and afs.DROPIN_NAME in self.etc:
            self.etc[afs.DROPIN_NAME] = self.replace_dropin_on_reload
            self.replace_dropin_on_reload = None
        if self.runtime_mutation is not None:
            name, text = self.runtime_mutation
            self.runtime[name] = text
            self.runtime_mutation = None
        if self.futures_dropin_on_reload is not None:
            self.futures_dropin_paths = self.futures_dropin_on_reload
            self.futures_dropin_on_reload = None
        self._apply_recovery_drift()

    def _apply_recovery_drift(self) -> None:
        drift = self.recovery_drift
        if drift is None:
            return
        self.recovery_drift = None
        if drift == "scanner-pid":
            self.scanner["MainPID"] = str(int(self.scanner["MainPID"]) + 1)
        elif drift == "scanner-restarts":
            self.scanner["NRestarts"] = str(int(self.scanner["NRestarts"]) + 1)
        elif drift == "futures-active":
            self.futures["ActiveState"] = "activating"
        elif drift == "futures-restarts":
            self.futures["NRestarts"] = "1"
        elif drift == "health":
            self.health = "500"
        elif drift == "need-reload":
            self.scanner["NeedDaemonReload"] = "yes"
        elif drift == "dropin-paths":
            self.extra_scanner_paths.append(
                "/etc/systemd/system/options-scanner.service.d/unexpected.conf"
            )
        else:
            raise AssertionError(drift)

    def read_cgroup(self, control_group: str, leaf: str) -> str | None:
        if control_group != afs.EXPECTED_CGROUP:
            raise afs.Denied("cgroup path is not available to maintenance")
        return self.cgroup.get(leaf)

    def write_cgroup_max(self, control_group: str, value: str) -> None:
        self.calls.append(("cgroup-write", value))
        if control_group != afs.EXPECTED_CGROUP or not value.isdigit():
            raise afs.Denied("cgroup path is not available to maintenance")
        if self.cgroup_write_error:
            raise OSError("device busy")
        if self.cgroup_write_sticks:
            self.cgroup["memory.max"] = value

    def read_base_unit(self) -> str:
        return self.base

    def etc_dropin_names(self) -> list[str]:
        return sorted(self.etc)

    def read_etc_dropin(self, name: str) -> str:
        return self.etc[name]

    def write_our_dropin(self, text: str) -> None:
        self.calls.append(("write-dropin", text))
        self.etc[afs.DROPIN_NAME] = text

    def remove_our_dropin(self) -> None:
        self.calls.append(("remove-dropin",))
        self.etc.pop(afs.DROPIN_NAME, None)

    def runtime_dropin_names(self) -> list[str]:
        return sorted(self.runtime)

    def read_runtime_dropin(self, name: str) -> str:
        return self.runtime[name]

    def remove_runtime_dropin(self, name: str) -> None:
        if name in self.fail_remove_runtime:
            raise OSError(f"remove failed for {name}")
        self.runtime.pop(name, None)

    def write_runtime_dropin(self, name: str, text: str) -> None:
        self.runtime[name] = text

    def restore_runtime_dropin(self, name: str, text: str) -> None:
        self.runtime[name] = text

    def control_dropin_names(self, area: str) -> list[str]:
        if area == "etc":
            return sorted(self.etc_control)
        if area == "run":
            return sorted(self.run_control)
        raise afs.Denied("control directory is not available to maintenance")

    def read_control_dropin(self, area: str, name: str) -> str:
        source = self.etc_control if area == "etc" else self.run_control
        return source[name]

    def health_code(self) -> str:
        return self.health

    def read_optional(self, path: Path) -> str:
        return self.records.get(path, "")

    def append_line(self, path: Path, line: str) -> None:
        if self.fail_history is not None and self.fail_history(line):
            raise OSError("history full")
        self.records[path] = self.records.get(path, "") + line + "\n"

    def ensure_state_dirs(self) -> None:
        return None

    @contextmanager
    def exclusive_lock(self):
        yield

    def now(self) -> datetime:
        return self.clock

    def env(self, name: str) -> str:
        return self.environ.get(name, "")

    def exec_audit(self, token: str) -> int:
        self.calls.append(("audit", token))
        return 0

    def grant(
        self,
        operation: str,
        name: str = "approval0001",
        expires: str = "20990101T000000Z",
        restricted: bool = True,
    ) -> None:
        self.approvals[name] = {
            "text": f"operation={operation}\nexpires={expires}\n",
            "restricted": restricted,
        }

    def approval_names(self) -> list[str]:
        return sorted(self.approvals)

    def read_approval(self, name: str) -> str:
        return str(self.approvals[name]["text"])

    def approval_is_restricted(self, name: str) -> bool:
        return bool(self.approvals[name]["restricted"])

    def consume_approval(self, name: str) -> None:
        self.approvals[name + ".consumed"] = self.approvals.pop(name)


def _run(host: FakeHost, *args: str) -> tuple[int, str, str]:
    stdout, stderr = StringIO(), StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = afs.execute(["afs-maintenance", *args], host)
    return code, stdout.getvalue(), stderr.getvalue()


def _log_rows(host: FakeHost) -> list[dict]:
    raw = host.records.get(afs.LOG_PATH, "")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def test_help_and_self_check_do_not_touch_the_host():
    host = FakeHost()
    code, out, err = _run(host, "help")
    assert code == 0, err
    assert "options-scanner-memory set <350-600>M" in out
    assert "status" in out
    assert host.calls == []

    code, out, err = _run(host, "self-check")
    assert code == 0, err
    assert "restarts=forbidden" in out
    assert host.calls == []


def test_show_reports_the_current_limit_without_writing():
    host = FakeHost()
    code, out, err = _run(host, "options-scanner-memory", "show")
    assert code == 0, err
    assert "memory_max_mib=350" in out
    assert "within_authorized_window=true" in out
    assert "futures_bot_main_pid=1851835" in out
    assert "drop_in=absent" in out
    assert "base_unit_floor=350M" in out
    assert not any(call[0] in {"daemon-reload", "write-dropin", "cgroup-write"} for call in host.calls)


@pytest.mark.parametrize("value", ["349M", "601M", "600", "600m", "1G", "0350M", "600.5M"])
def test_values_outside_the_window_are_rejected_before_any_change(value: str):
    host = FakeHost()
    code, _out, err = _run(host, "options-scanner-memory", "set", value)
    assert code in {2, 126}
    assert host.etc == {"no-bytecode.conf": "Environment=PYTHONDONTWRITEBYTECODE=1\n"}
    assert host.calls == []
    assert "FAILED" not in err or code == 2


@pytest.mark.parametrize(
    "args",
    [
        ("run", "options-scanner-memory set 600M; reboot"),
        ("run", "bash"),
        ("run", "options-scanner-memory set 600M restart futures-bot"),
        ("systemctl", "restart", "options-scanner"),
        ("run", "token=abc"),
    ],
)
def test_unlisted_commands_are_denied(args: tuple[str, ...]):
    host = FakeHost()
    code, _out, err = _run(host, *args)
    assert code == 126
    assert "DENIED" in err
    assert host.calls == []
    logged = host.records.get(afs.LOG_PATH, "")
    assert "abc" not in logged


def test_existing_audit_command_is_delegated_without_a_mutation():
    host = FakeHost()
    code, _out, err = _run(host, "run", "status")
    assert code == 0, err
    assert ("audit", "status") in host.calls
    assert not any(call[0] == "daemon-reload" for call in host.calls)


def test_set_without_operator_approval_does_not_change_memory():
    host = FakeHost()
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 126
    assert "operator approval is required" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert not any(call[0] == "daemon-reload" for call in host.calls)


def test_expired_or_writable_or_reused_approval_cannot_authorize_a_change():
    host = FakeHost()
    host.grant("options-scanner-memory set 600M", name="expired001", expires="20200101T000000Z")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 126
    assert "expired" in err
    assert "expired001" in host.approvals
    assert afs.DROPIN_NAME not in host.etc

    host.approvals.clear()
    host.grant("options-scanner-memory set 600M", name="writable01", restricted=False)
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 126
    assert "not a restricted root-owned file" in err
    assert "writable01" in host.approvals

    host.approvals.clear()
    host.grant("options-scanner-memory set 500M", name="wrongvalue")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 126
    assert "operator approval is required" in err
    assert "wrongvalue" in host.approvals


def test_set_600_updates_only_the_scanner_limit_and_can_roll_back():
    host = FakeHost()
    host.grant("options-scanner-memory set 600M")
    base_before = host.base
    code, out, err = _run(host, "run", "options-scanner-memory set 600M")
    assert code == 0, err
    assert "result=ok" in out
    assert "before_mib=350" in out
    assert "after_mib=600" in out
    assert "scanner_pid_unchanged=true" in out
    assert "futures_pid_unchanged=true" in out
    assert host.scanner["MainPID"] == "1695873"
    assert host.scanner["NRestarts"] == "0"
    assert host.futures["MainPID"] == "1851835"
    assert host.futures["MemoryMax"] == "infinity"
    assert host.base == base_before
    assert host.etc["no-bytecode.conf"].startswith("Environment=")
    assert host.etc[afs.DROPIN_NAME] == "[Service]\nMemoryMax=600M\n"
    assert host.scanner["MemoryMax"] == str(600 * afs.MIB)
    assert host.cgroup["memory.max"] == str(600 * afs.MIB)
    assert not any(call[0] == "set-property-runtime" for call in host.calls)
    stamp = out.split("stamp=", 1)[1].splitlines()[0]
    assert stamp == "20261008T204500Z"
    assert "approval0001" not in host.approvals
    assert "approval0001.consumed" in host.approvals

    host.grant(f"options-scanner-memory rollback {stamp}", name="rollback0001")
    code, out, err = _run(host, "options-scanner-memory", "rollback", stamp)
    assert code == 0, err
    assert "restored_mib=350" in out
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert host.scanner["MainPID"] == "1695873"
    assert host.base == base_before

    code, _out, err = _run(host, "options-scanner-memory", "rollback", stamp)
    assert code == 2
    assert "already rolled back" in err


def test_edges_of_the_authorized_window_are_accepted():
    host = FakeHost()
    code, out, err = _run(host, "options-scanner-memory", "set", "350M")
    assert code == 0, err
    assert "result=noop" in out
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 0, err
    assert "after_mib=600" in out


def test_reload_that_misses_memorymax_does_not_use_set_property():
    host = FakeHost()
    host.reload_updates_property = False
    host.reload_updates_live = False
    host.grant("options-scanner-memory set 500M")
    code, out, err = _run(host, "options-scanner-memory", "set", "500M")
    assert code == 1
    assert "result=ok" not in out
    assert "set-property" not in err
    assert not any(call[0] == "set-property-runtime" for call in host.calls)
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert afs.DROPIN_NAME not in host.etc


def test_pid_change_restores_the_limit_and_does_not_claim_rollback():
    host = FakeHost()
    host.change_pid_on_reload = True
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "ROLLBACK UNVERIFIED / HOLD" in err
    assert "scanner pid" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert not any("restart" in call[0] for call in host.calls)
    assert _log_rows(host)[-1]["result"] == "rollback_unverified"


def test_health_failure_does_not_claim_a_proved_rollback():
    host = FakeHost()
    host.fail_health_on_reload = True
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "ROLLBACK UNVERIFIED / HOLD" in err
    assert "health" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.cgroup["memory.max"] == str(350 * afs.MIB)
    assert _log_rows(host)[-1]["result"] == "rollback_unverified"


def test_futures_change_rolls_back_without_touching_futures_commands():
    host = FakeHost()
    host.change_futures_on_reload = True
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "ROLLBACK UNVERIFIED / HOLD" in err
    assert "futures memory" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.futures["MainPID"] == "1851835"
    assert _log_rows(host)[-1]["result"] == "rollback_unverified"


def test_partial_runtime_removal_restores_the_earlier_override():
    host = FakeHost()
    first = "[Service]\nMemoryMax=400M\n"
    second = "[Service]\nMemoryMax=450M\n"
    host.runtime["a.conf"] = first
    host.runtime["b.conf"] = second
    host.fail_remove_runtime.add("b.conf")
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert host.runtime["a.conf"] == first
    assert host.runtime["b.conf"] == second
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert host.cgroup["memory.max"] == str(350 * afs.MIB)
    assert _log_rows(host)[-1]["result"] == "rolled_back"
    assert "ROLLBACK UNVERIFIED" not in err


def test_failed_restore_reload_is_unverified_hold():
    host = FakeHost()
    host.change_pid_on_reload = True
    host.fail_reload_on_call = 2
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "ROLLBACK UNVERIFIED / HOLD" in err
    assert "reload failed" in err
    assert host.scanner["MemoryMax"] == str(600 * afs.MIB)
    assert _log_rows(host)[-1]["result"] == "rollback_unverified"
    assert "rolled_back" not in err


def test_lower_limit_is_not_persisted_when_the_kernel_rejects_it():
    host = FakeHost()
    host.etc[afs.DROPIN_NAME] = "[Service]\nMemoryMax=600M\n"
    host.scanner["MemoryMax"] = str(600 * afs.MIB)
    host.cgroup["memory.max"] = str(600 * afs.MIB)
    host.cgroup_write_error = True
    host.grant("options-scanner-memory set 350M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "350M")
    assert code == 1
    assert "not changed" in err
    assert host.etc[afs.DROPIN_NAME] == "[Service]\nMemoryMax=600M\n"
    assert not any(call[0] == "daemon-reload" for call in host.calls)
    assert _log_rows(host)[-1]["result"] == "rejected"


def test_missing_base_floor_refuses_the_change():
    host = FakeHost()
    host.base = "[Service]\nExecStart=/usr/bin/true\n"
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "350M" in err
    assert afs.DROPIN_NAME not in host.etc


def test_persistent_system_control_blocks_set_before_any_reload():
    host = FakeHost()
    host.etc_control["50-MemoryMax.conf"] = "[Service]\nMemoryMax=600M\n"
    code, out, err = _run(host, "options-scanner-memory", "set", "350M")
    assert code == 1
    assert "result=noop" not in out
    assert "result=ok" not in out
    assert "system.control" in err
    assert not any(call[0] == "daemon-reload" for call in host.calls)
    assert host.etc_control["50-MemoryMax.conf"].endswith("MemoryMax=600M\n")

    host.etc_control.clear()
    host.run_control["50-MemoryMax.conf"] = "[Service]\nMemoryMax=600M\n"
    host.grant("options-scanner-memory set 350M")
    code, out, err = _run(host, "options-scanner-memory", "set", "350M")
    assert code == 1
    assert "system.control" in err
    assert "approval0001" in host.approvals
    assert not any(call[0] == "daemon-reload" for call in host.calls)


def test_pending_futures_reload_is_not_activated():
    host = FakeHost()
    host.futures["NeedDaemonReload"] = "yes"
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert "daemon reload" in err
    assert not any(call[0] == "daemon-reload" for call in host.calls)
    assert afs.DROPIN_NAME not in host.etc
    assert "approval0001" in host.approvals


def test_futures_drop_in_activated_by_reload_is_not_ok():
    host = FakeHost()
    host.futures_dropin_on_reload = "/etc/systemd/system/futures-bot.service.d/half.conf"
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert "ROLLBACK UNVERIFIED / HOLD" in err
    assert "futures DropInPaths" in err
    assert _log_rows(host)[-1]["result"] == "rollback_unverified"


def test_cgroup_that_does_not_stick_is_not_reported_ok():
    host = FakeHost()
    host.reload_updates_live = False
    host.cgroup_write_sticks = False
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert host.cgroup["memory.max"] == str(350 * afs.MIB)
    assert afs.DROPIN_NAME not in host.etc


def test_changed_runtime_file_and_our_drop_in_are_not_ignored():
    host = FakeHost()
    original = "[Service]\nEnvironment=A=1\n"
    host.runtime["other.conf"] = original
    host.runtime_mutation = ("other.conf", "[Service]\nEnvironment=A=2\n")
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert host.runtime["other.conf"] == original

    host = FakeHost()
    host.replace_dropin_on_reload = "[Service]\nMemoryMax=500M\n"
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert host.etc.get(afs.DROPIN_NAME) != "[Service]\nMemoryMax=500M\n"


def test_recheck_flags_each_hidden_drift_field():
    host = FakeHost()
    before = afs.capture(host)
    memory = afs.capture(host)
    memory.scanner_memory_raw = str(600 * afs.MIB)
    assert "MemoryMax" in afs._restoration_mismatches(before, memory)

    cgroup = afs.capture(host)
    cgroup.cgroup_max = str(600 * afs.MIB)
    assert "cgroup memory.max" in afs._restoration_mismatches(before, cgroup)

    runtime = afs.capture(host)
    runtime.runtime_files = {"other.conf": "[Service]\nEnvironment=A=2\n"}
    assert any(item.startswith("unexpected runtime") for item in afs._restoration_mismatches(before, runtime))

    changed = afs.capture(host)
    changed.runtime_files = {"other.conf": "changed\n"}
    before_runtime = afs.capture(host)
    before_runtime.runtime_files = {"other.conf": "original\n"}
    assert "runtime other.conf" in afs._restoration_mismatches(before_runtime, changed)

    dropin = afs.capture(host)
    dropin.our_dropin = "[Service]\nMemoryMax=600M\n"
    assert "etc drop-in" in afs._restoration_mismatches(before, dropin)

    control = afs.capture(host)
    control.run_control = {"50-MemoryMax.conf": "[Service]\nMemoryMax=600M\n"}
    assert "run system.control" in afs._restoration_mismatches(before, control)

    with pytest.raises(afs.MaintenanceFailure, match="MemoryMax"):
        afs.verify_target(host, before, memory, 350)
    with pytest.raises(afs.MaintenanceFailure, match="cgroup"):
        afs.verify_target(host, before, cgroup, 350)
    with pytest.raises(afs.MaintenanceFailure, match="drop-in"):
        afs.verify_target(host, before, dropin, 350)


def test_restore_error_is_unverified_when_the_snapshot_matches():
    host = FakeHost()
    before = afs.capture(host)
    host.cgroup_write_error = True
    proved, detail = afs._prove_restored(host, before, {}, False)
    assert proved is False
    assert "cgroup restore failed" in detail


def test_history_write_failure_reverses_the_change_instead_of_crashing():
    host = FakeHost()
    host.fail_history = lambda line: '"action": "set"' in line
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert "history record failed" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert _log_rows(host)[-1]["result"] == "rolled_back"


def test_killed_change_keeps_a_record_rollback_can_use():
    host = FakeHost()
    host.crash_on_reload = True
    host.grant("options-scanner-memory set 600M")
    with pytest.raises(SystemExit):
        _run(host, "options-scanner-memory", "set", "600M")
    assert host.etc[afs.DROPIN_NAME] == "[Service]\nMemoryMax=600M\n"
    rows = [json.loads(line) for line in host.records[afs.HISTORY_PATH].splitlines()]
    intent = [row for row in rows if row["action"] == "intent"][-1]
    assert intent["our_dropin"] is None
    assert intent["result"] == "open"
    host.crash_on_reload = False
    host.grant(f"options-scanner-memory rollback {intent['stamp']}", name="rollback0001")
    code, out, err = _run(host, "options-scanner-memory", "rollback", intent["stamp"])
    assert code == 0, err
    assert "result=ok" in out
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    code, _out, err = _run(host, "options-scanner-memory", "set", "500M")
    assert code == 126
    assert "operator approval is required" in err


def _open_intent(host: FakeHost) -> dict:
    rows = [json.loads(line) for line in host.records[afs.HISTORY_PATH].splitlines()]
    return [row for row in rows if row["action"] == "intent"][-1]


def test_intent_write_failure_does_not_change_memory():
    host = FakeHost()
    host.fail_history = lambda line: '"action": "intent"' in line
    host.grant("options-scanner-memory set 600M")
    code, out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "result=ok" not in out
    assert "could not record the maintenance intent" in err
    assert "configuration was not changed" in err
    assert ("daemon-reload",) not in host.calls
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)


def test_intent_record_keeps_the_service_baseline():
    host = FakeHost()
    host.crash_on_reload = True
    host.grant("options-scanner-memory set 600M")
    with pytest.raises(SystemExit):
        _run(host, "options-scanner-memory", "set", "600M")
    intent = _open_intent(host)
    assert intent["scanner_pid"] == "1695873"
    assert intent["scanner_restarts"] == "0"
    assert intent["futures_pid"] == "1851835"
    assert intent["futures_restarts"] == "0"
    assert intent["futures_active"] == "active"
    assert intent["health_http"] == "200"
    assert intent["scanner_need_reload"] == "no"
    assert intent["futures_need_reload"] == "no"
    assert "no-bytecode.conf" in intent["scanner_dropin_paths"]


@pytest.mark.parametrize(
    "drift",
    [
        "scanner-pid",
        "scanner-restarts",
        "futures-active",
        "futures-restarts",
        "health",
        "need-reload",
        "dropin-paths",
    ],
)
def test_recovery_drift_cannot_report_success(drift: str):
    host = FakeHost()
    host.crash_on_reload = True
    host.grant("options-scanner-memory set 600M")
    with pytest.raises(SystemExit):
        _run(host, "options-scanner-memory", "set", "600M")
    intent = _open_intent(host)
    host.crash_on_reload = False
    host.recovery_drift = drift
    host.grant(f"options-scanner-memory rollback {intent['stamp']}", name="rollback0001")
    code, out, err = _run(host, "options-scanner-memory", "rollback", intent["stamp"])
    assert code == 1
    assert "result=ok" not in out
    assert "restarts_unchanged=true" not in out
    assert "ROLLBACK UNVERIFIED / HOLD" in err


def test_recovery_without_a_continuity_baseline_does_not_mutate():
    host = FakeHost()
    stamp = "20261008T204500Z"
    row = {
        "action": "intent",
        "before_mib": 350,
        "cgroup_max": str(350 * afs.MIB),
        "command": "options-scanner-memory set 600M",
        "our_dropin": None,
        "result": "open",
        "runtime_files": {},
        "stamp": stamp,
        "target_mib": 600,
    }
    host.records[afs.HISTORY_PATH] = json.dumps(row, sort_keys=True) + "\n"
    host.grant(f"options-scanner-memory rollback {stamp}", name="rollback0001")
    code, out, err = _run(host, "options-scanner-memory", "rollback", stamp)
    assert code == 1
    assert "result=ok" not in out
    assert "intent has no futures_active baseline" in err
    assert ("daemon-reload",) not in host.calls
    assert "rollback0001" in host.approvals


def test_history_append_retries_short_writes_and_fsyncs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    history = tmp_path / "maintenance" / "history.jsonl"
    history.parent.mkdir()
    monkeypatch.setattr(afs, "HISTORY_PATH", history)
    fsynced: list[int] = []
    real_fsync = os.fsync
    real_write = os.write

    def spy_fsync(fd: int) -> None:
        fsynced.append(fd)
        real_fsync(fd)

    def one_byte(fd: int, data: bytes) -> int:
        return real_write(fd, data[:1])

    monkeypatch.setattr(afs.os, "fsync", spy_fsync)
    monkeypatch.setattr(afs.os, "write", one_byte)
    line = '{"action": "intent", "result": "open"}'
    afs.ProductionHost().append_line(history, line)
    assert history.read_text(encoding="utf-8") == line + "\n"
    assert len(fsynced) >= 2


def test_partial_history_write_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    history = tmp_path / "history.jsonl"
    original = '{"action": "old"}\n'
    history.write_text(original, encoding="utf-8")
    monkeypatch.setattr(afs, "HISTORY_PATH", history)
    real_write = os.write
    calls = {"n": 0}

    def short_then_fail(fd: int, data: bytes) -> int:
        calls["n"] += 1
        if calls["n"] == 1:
            return real_write(fd, data[:2])
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(afs.os, "write", short_then_fail)
    with pytest.raises(OSError):
        afs.ProductionHost().append_line(history, '{"action": "intent"}')
    assert history.read_text(encoding="utf-8") == original


def test_history_fsync_failure_is_not_a_successful_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    history = tmp_path / "history.jsonl"
    monkeypatch.setattr(afs, "HISTORY_PATH", history)

    def boom(_fd: int) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(afs.os, "fsync", boom)
    with pytest.raises(OSError):
        afs.ProductionHost().append_line(history, '{"action": "intent"}')
    assert history.read_text(encoding="utf-8") == ""


def test_history_is_append_only_and_noop_does_not_add_a_set():
    host = FakeHost()
    host.grant("options-scanner-memory set 600M")
    assert _run(host, "options-scanner-memory", "set", "600M")[0] == 0
    first = host.records[afs.HISTORY_PATH]
    assert _run(host, "options-scanner-memory", "set", "600M")[0] == 0
    assert host.records[afs.HISTORY_PATH] == first
    code, out, err = _run(host, "options-scanner-memory", "history")
    assert code == 0, err
    assert '"after_mib": 600' in out


def test_systemctl_builder_has_no_restart_or_set_property_path():
    with pytest.raises(afs.Denied):
        afs.build_systemctl("restart")
    with pytest.raises(afs.Denied):
        afs.build_systemctl("set-property")
    args = afs.build_systemctl("show", unit=afs.SCANNER_UNIT)
    assert "NeedDaemonReload" in args
    assert "DropInPaths" in args
    assert "set-property" not in args
    assert "restart" not in args


def test_program_source_cannot_restart_deploy_or_read_env():
    text = MAINTENANCE_PATH.read_text(encoding="utf-8")
    forbidden = re.compile(
        r"\bsystemctl\s+(restart|stop|start|kill|reboot|poweroff|isolate|disable|enable|mask)\b"
    )
    assert forbidden.search(text) is None
    for needle in ("shell=True", "authorized_keys", "atomic_release", "ssh-keygen", "write_approval", "set-property"):
        assert needle not in text
    assert re.search(r"(?<![\w])\.env\b", text) is None


def _keys(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def test_installer_changes_only_the_cursor_command_restriction(tmp_path: Path):
    blob = "AAAACURSORblob"
    other = "AAAAotherblob"
    original = _keys(
        f'{installer.AUDIT_OPTIONS} ssh-ed25519 {blob} cursor-cloud-agent',
        f"ssh-ed25519 {other} afs-grok-audit",
    )
    updated = installer.rewrite_authorized_keys(original)
    assert blob in updated and other in updated
    assert "afs-maintenance run" in updated
    assert "afs-grok-audit" in updated.splitlines()[1]
    assert updated.splitlines()[1] == original.splitlines()[1]
    assert installer.rewrite_authorized_keys(updated) == updated

    root = tmp_path / "root"
    keys = installer.authorized_keys_path(root)
    keys.parent.mkdir(parents=True)
    keys.write_text(original, encoding="utf-8")
    program, sudoers = installer.install_paths(root)
    report = installer.run_install(
        root=root,
        script_path=MAINTENANCE_PATH,
        sudoers_path=SUDOERS_PATH,
        apply=False,
        sudoers_check=lambda _path: None,
    )
    assert "dry-run" in report
    assert keys.read_text(encoding="utf-8") == original
    assert not program.exists()

    report = installer.run_install(
        root=root,
        script_path=MAINTENANCE_PATH,
        sudoers_path=SUDOERS_PATH,
        apply=True,
        sudoers_check=lambda _path: None,
    )
    assert "installed" in report
    assert "services=not-restarted" in report
    installed = keys.read_text(encoding="utf-8")
    assert installed.splitlines()[1] == original.splitlines()[1]
    assert "afs-maintenance run" in installed.splitlines()[0]
    assert blob in installed.splitlines()[0]
    assert stat.S_IMODE(program.stat().st_mode) == 0o755
    assert stat.S_IMODE(sudoers.stat().st_mode) == 0o440
    assert b"systemctl" not in sudoers.read_bytes()


def test_installer_preserves_authorized_keys_owner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    blob = "AAAACURSORblob"
    original = _keys(f'{installer.AUDIT_OPTIONS} ssh-ed25519 {blob} cursor-cloud-agent')
    root = tmp_path / "root"
    keys = installer.authorized_keys_path(root)
    keys.parent.mkdir(parents=True)
    keys.write_text(original, encoding="utf-8")
    os_chown = os.chown
    os_fchown = os.fchown
    seen: list[tuple[str, int, int]] = []

    def spy_chown(path, uid, gid):
        if Path(path).name == "authorized_keys":
            seen.append(("chown", uid, gid))
        return os_chown(path, uid, gid)

    def spy_fchown(fd, uid, gid):
        seen.append(("fchown", uid, gid))
        return os_fchown(fd, uid, gid)

    monkeypatch.setattr(installer.os, "chown", spy_chown)
    monkeypatch.setattr(installer.os, "fchown", spy_fchown)
    before = keys.stat()
    installer.run_install(
        root=root,
        script_path=MAINTENANCE_PATH,
        sudoers_path=SUDOERS_PATH,
        apply=True,
        sudoers_check=lambda _path: None,
    )
    after = keys.stat()
    assert after.st_uid == before.st_uid
    assert after.st_gid == before.st_gid
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert ("chown", before.st_uid, before.st_gid) in seen
    assert ("fchown", before.st_uid, before.st_gid) in seen
    assert "afs-maintenance run" in keys.read_text(encoding="utf-8")


def test_installer_restores_keys_when_owner_cannot_be_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    blob = "AAAACURSORblob"
    original = _keys(f'{installer.AUDIT_OPTIONS} ssh-ed25519 {blob} cursor-cloud-agent')
    root = tmp_path / "root"
    keys = installer.authorized_keys_path(root)
    keys.parent.mkdir(parents=True)
    keys.write_text(original, encoding="utf-8")
    before = keys.stat()

    def fail_fchown(_fd, _uid, _gid):
        raise OSError("chown failed")

    monkeypatch.setattr(installer.os, "fchown", fail_fchown)
    with pytest.raises(installer.InstallError, match="owner was not preserved"):
        installer.run_install(
            root=root,
            script_path=MAINTENANCE_PATH,
            sudoers_path=SUDOERS_PATH,
            apply=True,
            sudoers_check=lambda _path: None,
        )
    assert keys.read_text(encoding="utf-8") == original
    assert keys.stat().st_uid == before.st_uid
    assert keys.stat().st_gid == before.st_gid
    assert not keys.with_name("authorized_keys.tmp").exists()


def test_installer_refuses_an_unexpected_or_duplicate_cursor_key():
    line = f'{installer.AUDIT_OPTIONS} ssh-ed25519 AAAAblob cursor-cloud-agent'
    with pytest.raises(installer.InstallError):
        installer.rewrite_authorized_keys(line + "\n" + line + "\n")
    altered = line.replace("afs-grok-audit", "afs-other")
    with pytest.raises(installer.InstallError):
        installer.rewrite_authorized_keys(altered + "\n")
    with pytest.raises(installer.InstallError):
        installer.rewrite_authorized_keys("")


def test_sudoers_fragment_is_limited_to_the_maintenance_program():
    text = SUDOERS_PATH.read_text(encoding="utf-8")
    assert "NOPASSWD: /usr/local/sbin/afs-maintenance run *" in text
    assert "NOPASSWD:ALL" not in text
    assert "systemctl" not in text
    for shell in ("/bin/bash", "/bin/sh", "/usr/bin/sudo"):
        assert shell not in text
