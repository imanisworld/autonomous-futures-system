"""Local tests for the restricted maintenance interface. No VPS calls."""

from __future__ import annotations

import importlib.util
import json
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
        self.clock = datetime(2026, 10, 8, 20, 45, tzinfo=timezone.utc)
        self.environ = {"SUDO_USER": "grok-audit", "SSH_CONNECTION": "test-connection"}
        self.approvals: dict[str, dict[str, object]] = {}

    def systemctl_show(self, unit: str) -> dict[str, str]:
        self.calls.append(("show", unit))
        if unit == afs.SCANNER_UNIT:
            return dict(self.scanner)
        if unit == afs.FUTURES_UNIT:
            return dict(self.futures)
        raise afs.Denied("unit is not available to maintenance")

    def daemon_reload(self) -> None:
        self.calls.append(("daemon-reload",))
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

    def set_property_runtime(self, mib: int) -> None:
        self.calls.append(("set-property-runtime", str(mib)))
        self.scanner["MemoryMax"] = str(mib * afs.MIB)
        self.cgroup["memory.max"] = str(mib * afs.MIB)
        self.runtime["50-MemoryMax.conf"] = f"[Service]\nMemoryMax={mib}M\n"

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
        self.runtime.pop(name, None)

    def write_runtime_dropin(self, name: str, text: str) -> None:
        self.runtime[name] = text

    def health_code(self) -> str:
        return self.health

    def read_optional(self, path: Path) -> str:
        return self.records.get(path, "")

    def append_line(self, path: Path, line: str) -> None:
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


def test_runtime_property_is_used_only_when_reload_does_not_apply_the_limit():
    host = FakeHost()
    host.reload_updates_property = False
    host.reload_updates_live = False
    host.grant("options-scanner-memory set 500M")
    code, out, err = _run(host, "options-scanner-memory", "set", "500M")
    assert code == 0, err
    assert "after_mib=500" in out
    assert ("set-property-runtime", "500") in host.calls
    assert host.runtime["50-MemoryMax.conf"] == "[Service]\nMemoryMax=500M\n"
    assert host.etc[afs.DROPIN_NAME] == "[Service]\nMemoryMax=500M\n"


def test_pid_change_rolls_the_drop_in_back_and_does_not_restart():
    host = FakeHost()
    host.change_pid_on_reload = True
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "PID changed" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.scanner["MemoryMax"] == str(350 * afs.MIB)
    assert not any("restart" in call[0] for call in host.calls)
    assert _log_rows(host)[-1]["result"] == "rolled_back"


def test_health_failure_rolls_back():
    host = FakeHost()
    host.fail_health_on_reload = True
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "health" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.cgroup["memory.max"] == str(350 * afs.MIB)


def test_futures_change_rolls_back_without_touching_futures_commands():
    host = FakeHost()
    host.change_futures_on_reload = True
    host.grant("options-scanner-memory set 600M")
    code, _out, err = _run(host, "options-scanner-memory", "set", "600M")
    assert code == 1
    assert "futures-bot" in err
    assert afs.DROPIN_NAME not in host.etc
    assert host.futures["MainPID"] == "1851835"


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


def test_systemctl_builder_has_no_restart_path():
    with pytest.raises(afs.Denied):
        afs.build_systemctl("restart")
    args = afs.build_systemctl("set-property-runtime", mib=600)
    assert args[:4] == ["/usr/bin/systemctl", "set-property", "--runtime", "options-scanner.service"]
    assert "restart" not in args
    with pytest.raises(afs.Denied):
        afs.build_systemctl("set-property-runtime", mib=601)


def test_program_source_cannot_restart_deploy_or_read_env():
    text = MAINTENANCE_PATH.read_text(encoding="utf-8")
    forbidden = re.compile(
        r"\bsystemctl\s+(restart|stop|start|kill|reboot|poweroff|isolate|disable|enable|mask)\b"
    )
    assert forbidden.search(text) is None
    for needle in ("shell=True", "authorized_keys", "atomic_release", "ssh-keygen", "write_approval"):
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
