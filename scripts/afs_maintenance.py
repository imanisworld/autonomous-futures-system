#!/usr/bin/env python3
"""Restricted, logged maintenance interface for one existing SSH account.

The program permits only the operations named below. It does not open a
shell, restart a service, deploy a release, read environment files, or
change SSH keys.

Authorized mutation:
  options-scanner MemoryMax, whole mebibytes from 350 through 600 inclusive.
  A set or rollback runs only when root has placed a matching one-time
  approval file. This program can read and consume those files. It cannot
  create one.

The base unit file stays at MemoryMax=350M. A higher value is stored only in
the drop-in zz-afs-memory-max.conf. Rollback restores the recorded previous
value. A lower live cgroup limit is attempted before that drop-in is changed;
if the kernel refuses the lower limit, the drop-in is left untouched.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

SCANNER_UNIT = "options-scanner.service"
FUTURES_UNIT = "futures-bot.service"
MIN_MIB = 350
MAX_MIB = 600
BASE_MEMORY_LINE = "MemoryMax=350M"
DROPIN_NAME = "zz-afs-memory-max.conf"
EXPECTED_CGROUP = "/system.slice/options-scanner.service"
BASE_UNIT = Path("/etc/systemd/system/options-scanner.service")
ETC_DROPIN_DIR = Path("/etc/systemd/system/options-scanner.service.d")
RUNTIME_DROPIN_DIR = Path("/run/systemd/system/options-scanner.service.d")
ETC_CONTROL_DIR = Path("/etc/systemd/system.control/options-scanner.service.d")
RUN_CONTROL_DIR = Path("/run/systemd/system.control/options-scanner.service.d")
OUR_DROPIN_PATH = str(ETC_DROPIN_DIR / DROPIN_NAME)
CGROUP_ROOT = Path("/sys/fs/cgroup")
SHARED_DIR = Path("/root/afs-shared")
STATE_DIR = SHARED_DIR / "maintenance" / "options-scanner-memory"
APPROVAL_DIR = SHARED_DIR / "maintenance" / "approvals"
LOCK_PATH = SHARED_DIR / "maintenance" / "afs-maintenance.lock"
HISTORY_PATH = STATE_DIR / "history.jsonl"
LOG_PATH = SHARED_DIR / "logs" / "afs-maintenance.log"
AUDIT_PROGRAM = Path("/usr/local/sbin/afs-grok-audit")
HEALTH_URL = "http://127.0.0.1:8010/health"
MIB = 1024 * 1024

# Read-only commands accepted by the live afs-grok-audit program inspected
# 2026-10-08 (sha256 179c11d69a222ae2851ae7913dfe707a6753b737078e3ba51f92982de12cb994).
# help is answered here so the maintenance allowlist stays visible.
AUDIT_COMMANDS = frozenset(
    {
        "identity",
        "status",
        "release",
        "runtime-pins",
        "health",
        "broker",
        "evidence",
        "journal-today",
        "demo-evidence",
        "deploy-state",
        "campaign",
        "list-logs",
        "service-logs",
    }
)

_SAFE_TEXT = re.compile(r"^[A-Za-z0-9._:-]+$")
_APPROVAL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{7,79}$")
_VALUE = re.compile(r"^([1-9][0-9]{2})M$")
_STAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
_DROPIN = re.compile(r"\[Service\]\nMemoryMax=([1-9][0-9]{2})M\n\Z")
_SECRET = re.compile(
    r"(?i)((?:secret|token|password|api[_-]?key|authorization)[^\s:=]*\s*[:=]\s*)\S+"
)


class Denied(Exception):
    """The command is not on the allowlist."""


class UsageError(Exception):
    """The command is recognized but its arguments are not valid."""


class MaintenanceFailure(Exception):
    """A permitted operation did not complete cleanly."""

    def __init__(self, message: str, *, restored: bool = False, unverified: bool = False) -> None:
        super().__init__(message)
        self.restored = restored
        self.unverified = unverified


@dataclass(frozen=True)
class ShowAction:
    pass


@dataclass(frozen=True)
class HistoryAction:
    pass


@dataclass(frozen=True)
class SelfCheckAction:
    pass


@dataclass(frozen=True)
class HelpAction:
    pass


@dataclass(frozen=True)
class SetAction:
    mib: int


@dataclass(frozen=True)
class RollbackAction:
    stamp: str | None


@dataclass(frozen=True)
class AuditAction:
    name: str


Action = (
    ShowAction
    | HistoryAction
    | SelfCheckAction
    | HelpAction
    | SetAction
    | RollbackAction
    | AuditAction
)


@dataclass
class Snapshot:
    scanner_active: str
    scanner_pid: str
    scanner_restarts: str
    scanner_memory_raw: str
    scanner_mib: int | None
    scanner_cgroup: str
    futures_active: str
    futures_pid: str
    futures_restarts: str
    futures_memory_raw: str
    cgroup_max: str | None
    cgroup_current: str | None
    cgroup_swap: str | None
    health_http: str
    base_sha256: str
    base_floor_ok: bool
    etc_files: dict[str, str]
    our_dropin: str | None
    runtime_files: dict[str, str]
    scanner_need_reload: str
    futures_need_reload: str
    scanner_dropin_paths: str
    futures_dropin_paths: str
    etc_control: dict[str, str]
    run_control: dict[str, str]


class Host:
    """Narrow host operations. There is no method that restarts a unit."""

    def systemctl_show(self, unit: str) -> dict[str, str]:
        raise NotImplementedError

    def daemon_reload(self) -> None:
        raise NotImplementedError

    def read_cgroup(self, control_group: str, leaf: str) -> str | None:
        raise NotImplementedError

    def write_cgroup_max(self, control_group: str, value: str) -> None:
        raise NotImplementedError

    def read_base_unit(self) -> str:
        raise NotImplementedError

    def etc_dropin_names(self) -> list[str]:
        raise NotImplementedError

    def read_etc_dropin(self, name: str) -> str:
        raise NotImplementedError

    def write_our_dropin(self, text: str) -> None:
        raise NotImplementedError

    def remove_our_dropin(self) -> None:
        raise NotImplementedError

    def runtime_dropin_names(self) -> list[str]:
        raise NotImplementedError

    def read_runtime_dropin(self, name: str) -> str:
        raise NotImplementedError

    def remove_runtime_dropin(self, name: str) -> None:
        raise NotImplementedError

    def write_runtime_dropin(self, name: str, text: str) -> None:
        raise NotImplementedError

    def restore_runtime_dropin(self, name: str, text: str) -> None:
        raise NotImplementedError

    def control_dropin_names(self, area: str) -> list[str]:
        raise NotImplementedError

    def read_control_dropin(self, area: str, name: str) -> str:
        raise NotImplementedError

    def health_code(self) -> str:
        raise NotImplementedError

    def read_optional(self, path: Path) -> str:
        raise NotImplementedError

    def append_line(self, path: Path, line: str) -> None:
        raise NotImplementedError

    def ensure_state_dirs(self) -> None:
        raise NotImplementedError

    def approval_names(self) -> list[str]:
        raise NotImplementedError

    def read_approval(self, name: str) -> str:
        raise NotImplementedError

    def approval_is_restricted(self, name: str) -> bool:
        raise NotImplementedError

    def consume_approval(self, name: str) -> None:
        raise NotImplementedError

    @contextlib.contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        raise NotImplementedError
        yield  # pragma: no cover

    def now(self) -> datetime:
        raise NotImplementedError

    def env(self, name: str) -> str:
        raise NotImplementedError

    def exec_audit(self, token: str) -> int:
        raise NotImplementedError


def build_systemctl(action: str, unit: str = "") -> list[str]:
    """Return a fixed systemctl argument list. Unknown actions are rejected."""

    if action == "show":
        if unit not in {SCANNER_UNIT, FUTURES_UNIT}:
            raise Denied("unit is not available to maintenance")
        return [
            "/usr/bin/systemctl",
            "show",
            unit,
            "-p",
            "ActiveState",
            "-p",
            "MainPID",
            "-p",
            "NRestarts",
            "-p",
            "MemoryMax",
            "-p",
            "ControlGroup",
            "-p",
            "NeedDaemonReload",
            "-p",
            "DropInPaths",
            "--no-pager",
        ]
    if action == "daemon-reload":
        return ["/usr/bin/systemctl", "daemon-reload"]
    raise Denied("systemctl action is not available to maintenance")


def redact(text: str) -> str:
    return _SECRET.sub(r"\1[REDACTED]", text)


def parse_memory_max(raw: str) -> int | None:
    text = raw.strip()
    if text in {"", "infinity"}:
        return None
    if text.isdigit():
        return int(text)
    match = re.fullmatch(r"([1-9][0-9]*)M", text)
    if match:
        return int(match.group(1)) * MIB
    raise MaintenanceFailure(f"unrecognized MemoryMax value: {text}")


def mib_of(num_bytes: int | None) -> int | None:
    if num_bytes is None:
        return None
    if num_bytes % MIB != 0:
        return None
    return num_bytes // MIB


def dropin_text(mib: int) -> str:
    if not MIN_MIB <= mib <= MAX_MIB:
        raise UsageError("MemoryMax must be a whole number of mebibytes from 350 through 600")
    return f"[Service]\nMemoryMax={mib}M\n"


def parse_command_text(text: str) -> Action:
    if text != text.strip() or "\n" in text or "\r" in text or "\t" in text:
        raise Denied("command is not in the maintenance allowlist")
    if any(ch in text for ch in ";&|$`<>(){}'\"\\*?!~"):
        raise Denied("command is not in the maintenance allowlist")
    if "  " in text:
        raise Denied("command is not in the maintenance allowlist")
    parts = text.split(" ") if text else []
    if any(not _SAFE_TEXT.fullmatch(part) for part in parts):
        raise Denied("command is not in the maintenance allowlist")
    if parts == ["help"]:
        return HelpAction()
    if parts == ["self-check"]:
        return SelfCheckAction()
    if len(parts) == 1 and parts[0] in AUDIT_COMMANDS:
        return AuditAction(parts[0])
    if len(parts) == 2 and parts == ["options-scanner-memory", "show"]:
        return ShowAction()
    if len(parts) == 2 and parts == ["options-scanner-memory", "history"]:
        return HistoryAction()
    if len(parts) == 3 and parts[:2] == ["options-scanner-memory", "set"]:
        match = _VALUE.fullmatch(parts[2])
        if not match:
            raise UsageError("set requires a value such as 600M")
        mib = int(match.group(1))
        if not MIN_MIB <= mib <= MAX_MIB:
            raise UsageError("MemoryMax must stay between 350M and 600M")
        return SetAction(mib)
    if len(parts) == 2 and parts == ["options-scanner-memory", "rollback"]:
        return RollbackAction(None)
    if len(parts) == 3 and parts[:2] == ["options-scanner-memory", "rollback"]:
        if not _STAMP.fullmatch(parts[2]):
            raise UsageError("rollback stamp must look like 20261008T204500Z")
        return RollbackAction(parts[2])
    raise Denied("command is not in the maintenance allowlist")


def parse_argv(argv: list[str]) -> Action:
    args = argv[1:]
    if not args:
        raise UsageError("missing command")
    if args[0] == "run":
        args = args[1:]
        if not args:
            raise UsageError("missing command")
        if len(args) == 1:
            return parse_command_text(args[0])
        return parse_command_text(" ".join(args))
    return parse_command_text(" ".join(args))


def canonical(action: Action) -> str:
    if isinstance(action, HelpAction):
        return "help"
    if isinstance(action, SelfCheckAction):
        return "self-check"
    if isinstance(action, ShowAction):
        return "options-scanner-memory show"
    if isinstance(action, HistoryAction):
        return "options-scanner-memory history"
    if isinstance(action, SetAction):
        return f"options-scanner-memory set {action.mib}M"
    if isinstance(action, RollbackAction):
        if action.stamp:
            return f"options-scanner-memory rollback {action.stamp}"
        return "options-scanner-memory rollback"
    if isinstance(action, AuditAction):
        return action.name
    raise Denied("command is not in the maintenance allowlist")


def help_text() -> str:
    names = " ".join(sorted(AUDIT_COMMANDS))
    return "\n".join(
        [
            "Allowed maintenance commands:",
            "  help",
            "  self-check",
            "  options-scanner-memory show",
            "  options-scanner-memory history",
            "  options-scanner-memory set <350-600>M",
            "  options-scanner-memory rollback [YYYYMMDDTHHMMSSZ]",
            "Read-only audit commands delegated unchanged:",
            f"  {names}",
            "set and rollback require a one-time root approval file.",
            "This program cannot create an approval.",
            "MemoryMax outside 350M..600M, other units, restarts, and deploys are rejected.",
        ]
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _show_value(props: dict[str, str], key: str) -> str:
    return props.get(key, "").strip()


def _need_reload(props: dict[str, str], unit: str) -> str:
    value = _show_value(props, "NeedDaemonReload")
    if value not in {"yes", "no"}:
        raise MaintenanceFailure(f"{unit} did not report NeedDaemonReload")
    return value


def _control_files(host: Host, area: str) -> dict[str, str]:
    return {name: host.read_control_dropin(area, name) for name in host.control_dropin_names(area)}


def capture(host: Host) -> Snapshot:
    scanner = host.systemctl_show(SCANNER_UNIT)
    futures = host.systemctl_show(FUTURES_UNIT)
    memory_raw = _show_value(scanner, "MemoryMax")
    memory_bytes = parse_memory_max(memory_raw)
    cgroup = _show_value(scanner, "ControlGroup")
    pid = _show_value(scanner, "MainPID")
    cgroup_max = cgroup_current = cgroup_swap = None
    if pid not in {"", "0"}:
        if cgroup != EXPECTED_CGROUP:
            raise MaintenanceFailure("options-scanner cgroup is not the expected service cgroup")
        cgroup_max = host.read_cgroup(cgroup, "memory.max")
        cgroup_current = host.read_cgroup(cgroup, "memory.current")
        cgroup_swap = host.read_cgroup(cgroup, "memory.swap.current")
    base = host.read_base_unit()
    floor_count = sum(1 for line in base.splitlines() if line.strip() == BASE_MEMORY_LINE)
    etc: dict[str, str] = {}
    our_dropin = None
    for name in host.etc_dropin_names():
        text = host.read_etc_dropin(name)
        etc[name] = _sha256(text)
        if name == DROPIN_NAME:
            our_dropin = text
    runtime = {name: host.read_runtime_dropin(name) for name in host.runtime_dropin_names()}
    return Snapshot(
        scanner_active=_show_value(scanner, "ActiveState"),
        scanner_pid=pid,
        scanner_restarts=_show_value(scanner, "NRestarts"),
        scanner_memory_raw=memory_raw,
        scanner_mib=mib_of(memory_bytes) if memory_bytes is not None else None,
        scanner_cgroup=cgroup,
        futures_active=_show_value(futures, "ActiveState"),
        futures_pid=_show_value(futures, "MainPID"),
        futures_restarts=_show_value(futures, "NRestarts"),
        futures_memory_raw=_show_value(futures, "MemoryMax"),
        cgroup_max=None if cgroup_max is None else cgroup_max.strip(),
        cgroup_current=None if cgroup_current is None else cgroup_current.strip(),
        cgroup_swap=None if cgroup_swap is None else cgroup_swap.strip(),
        health_http=host.health_code(),
        base_sha256=_sha256(base),
        base_floor_ok=floor_count == 1,
        etc_files=etc,
        our_dropin=our_dropin,
        runtime_files=runtime,
        scanner_need_reload=_need_reload(scanner, SCANNER_UNIT),
        futures_need_reload=_need_reload(futures, FUTURES_UNIT),
        scanner_dropin_paths=_show_value(scanner, "DropInPaths"),
        futures_dropin_paths=_show_value(futures, "DropInPaths"),
        etc_control=_control_files(host, "etc"),
        run_control=_control_files(host, "run"),
    )


def _require_floor(snapshot: Snapshot) -> None:
    if not snapshot.base_floor_ok:
        raise MaintenanceFailure("base options-scanner unit must still contain MemoryMax=350M")
    if snapshot.our_dropin is not None and not _DROPIN.fullmatch(snapshot.our_dropin):
        raise MaintenanceFailure("existing memory drop-in is not in the expected form")
    if snapshot.our_dropin is not None:
        found = int(_DROPIN.fullmatch(snapshot.our_dropin).group(1))
        if not MIN_MIB <= found <= MAX_MIB:
            raise MaintenanceFailure("existing memory drop-in is outside 350M..600M")


def _expected_bytes(mib: int) -> str:
    return str(mib * MIB)


def _live_bytes(snapshot: Snapshot) -> int | None:
    raw = snapshot.cgroup_max
    if raw is None or raw == "max" or not raw.isdigit():
        return None
    return int(raw)


def _lower_live_limit(host: Host, before: Snapshot, target_mib: int) -> None:
    target = target_mib * MIB
    raw = before.cgroup_max
    if raw is None or before.scanner_pid in {"", "0"}:
        return
    unlimited = raw == "max"
    current = None if unlimited or not raw.isdigit() else int(raw)
    if not unlimited and (current is None or current <= target):
        return
    if before.scanner_cgroup != EXPECTED_CGROUP:
        raise MaintenanceFailure("refusing to write an unexpected cgroup")
    try:
        host.write_cgroup_max(before.scanner_cgroup, str(target))
    except OSError as exc:
        raise MaintenanceFailure(
            "kernel refused the lower memory limit; configuration was not changed"
        ) from exc
    got = host.read_cgroup(before.scanner_cgroup, "memory.max")
    if got is None or got.strip() != str(target):
        restored_prior = False
        if raw is not None:
            try:
                host.write_cgroup_max(before.scanner_cgroup, raw)
                checked = host.read_cgroup(before.scanner_cgroup, "memory.max")
                restored_prior = checked is not None and checked.strip() == raw
            except OSError:
                restored_prior = False
        if not restored_prior:
            raise MaintenanceFailure(
                "ROLLBACK UNVERIFIED / HOLD: lower memory limit did not stick and the prior limit was not restored",
                unverified=True,
            )
        raise MaintenanceFailure(
            "lower memory limit did not stick; configuration was not changed"
        )


def _persist(host: Host, target_mib: int) -> None:
    if target_mib == MIN_MIB:
        host.remove_our_dropin()
        return
    text = dropin_text(target_mib)
    if not _DROPIN.fullmatch(text):
        raise MaintenanceFailure("refusing to write an unexpected drop-in")
    host.write_our_dropin(text)


def _retire_mismatched_runtime(host: Host, target_mib: int, removed: dict[str, str]) -> None:
    """Remove mismatched runtime overrides, journaling each one before removal.

    ``removed`` is updated before each removal so a later failure still tells
    the caller which earlier files must be put back.
    """

    for name in list(host.runtime_dropin_names()):
        text = host.read_runtime_dropin(name)
        if not _memory_assignment_only(text):
            continue
        if _runtime_memory_only(text, target_mib):
            continue
        removed[name] = text
        host.remove_runtime_dropin(name)


def _sync_live(host: Host, before: Snapshot, target_mib: int) -> None:
    target = _expected_bytes(target_mib)
    if before.scanner_pid not in {"", "0"} and before.scanner_cgroup == EXPECTED_CGROUP:
        current = host.read_cgroup(before.scanner_cgroup, "memory.max")
        if current is None or current.strip() != target:
            try:
                host.write_cgroup_max(before.scanner_cgroup, target)
            except OSError as exc:
                raise MaintenanceFailure("live memory limit was not updated") from exc
    shown = host.systemctl_show(SCANNER_UNIT)
    shown_bytes = parse_memory_max(_show_value(shown, "MemoryMax"))
    if shown_bytes != target_mib * MIB:
        raise MaintenanceFailure("daemon-reload did not apply MemoryMax")
    current = host.read_cgroup(before.scanner_cgroup, "memory.max") if before.scanner_pid not in {"", "0"} else target
    if before.scanner_pid not in {"", "0"} and (current is None or current.strip() != target):
        raise MaintenanceFailure("live memory limit was not updated")


def _same_identity(before: Snapshot, after: Snapshot) -> None:
    if after.scanner_pid != before.scanner_pid:
        raise MaintenanceFailure("options-scanner PID changed; memory change was reversed")
    if after.scanner_restarts != before.scanner_restarts:
        raise MaintenanceFailure("options-scanner restart count changed; memory change was reversed")
    if after.scanner_active != before.scanner_active:
        raise MaintenanceFailure("options-scanner state changed; memory change was reversed")
    if after.futures_pid != before.futures_pid or after.futures_restarts != before.futures_restarts:
        raise MaintenanceFailure("futures-bot identity changed; memory change was reversed")
    if after.futures_active != before.futures_active or after.futures_memory_raw != before.futures_memory_raw:
        raise MaintenanceFailure("futures-bot memory or state changed; memory change was reversed")
    if after.base_sha256 != before.base_sha256 or not after.base_floor_ok:
        raise MaintenanceFailure("base unit file changed; memory change was reversed")
    if before.health_http == "200" and after.health_http != "200":
        raise MaintenanceFailure("options-scanner health check failed; memory change was reversed")


def _same_unrelated_files(before: Snapshot, after: Snapshot) -> None:
    for name, digest in before.etc_files.items():
        if name == DROPIN_NAME:
            continue
        if after.etc_files.get(name) != digest:
            raise MaintenanceFailure("an unrelated scanner drop-in changed")
    extra = set(after.etc_files) - set(before.etc_files) - {DROPIN_NAME}
    if extra:
        raise MaintenanceFailure("an unexpected scanner drop-in appeared")


def verify_target(_host: Host, before: Snapshot, after: Snapshot, target_mib: int) -> None:
    _same_identity(before, after)
    _same_unrelated_files(before, after)
    expected = target_mib * MIB
    got = parse_memory_max(after.scanner_memory_raw)
    if got != expected:
        raise MaintenanceFailure("MemoryMax did not match the requested value")
    if after.scanner_pid not in {"", "0"}:
        if after.cgroup_max != str(expected):
            raise MaintenanceFailure("live cgroup memory.max did not match the requested value")
    if target_mib == MIN_MIB:
        if after.our_dropin not in {None, ""}:
            raise MaintenanceFailure("350M must come from the base unit, not a drop-in")
    else:
        if after.our_dropin != dropin_text(target_mib):
            raise MaintenanceFailure("memory drop-in does not match the requested value")
    for name, text in after.runtime_files.items():
        previous = before.runtime_files.get(name)
        if previous == text:
            continue
        if previous is not None:
            raise MaintenanceFailure("a pre-existing runtime drop-in changed")
        if not _runtime_memory_only(text, target_mib):
            raise MaintenanceFailure("runtime drop-in is not a MemoryMax override")
    if after.futures_dropin_paths != before.futures_dropin_paths:
        raise MaintenanceFailure("futures-bot DropInPaths changed")
    if not _scanner_paths_allowed(before.scanner_dropin_paths, after.scanner_dropin_paths):
        raise MaintenanceFailure("options-scanner DropInPaths changed")
    if after.etc_control != before.etc_control or after.run_control != before.run_control:
        raise MaintenanceFailure("systemd system.control changed")
    if after.scanner_need_reload != "no" or after.futures_need_reload != "no":
        raise MaintenanceFailure("NeedDaemonReload remained set")


def _runtime_memory_only(text: str, target_mib: int) -> bool:
    assignments: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";") or line.startswith("["):
            continue
        if "=" not in line:
            return False
        key, value = line.split("=", 1)
        assignments.append((key.strip(), value.strip()))
    expected = {("MemoryMax", f"{target_mib}M"), ("MemoryMax", str(target_mib * MIB))}
    return len(assignments) == 1 and tuple(assignments[0]) in expected


def _path_set(raw: str) -> set[str]:
    return {part for part in raw.split() if part}


def _scanner_paths_allowed(before_raw: str, after_raw: str) -> bool:
    added = _path_set(after_raw) - _path_set(before_raw)
    removed = _path_set(before_raw) - _path_set(after_raw)
    return added <= {OUR_DROPIN_PATH} and removed <= {OUR_DROPIN_PATH}


def _require_quiet_units(before: Snapshot) -> None:
    if before.scanner_need_reload != "no" or before.futures_need_reload != "no":
        raise MaintenanceFailure("a unit already needs a daemon reload; configuration was not changed")
    if before.etc_control or before.run_control:
        raise MaintenanceFailure("systemd system.control override is present; configuration was not changed")


def _restore_files(host: Host, before: Snapshot, removed_runtime: dict[str, str]) -> None:
    if before.our_dropin is None:
        host.remove_our_dropin()
    else:
        host.write_our_dropin(before.our_dropin)
    recorded = dict(before.runtime_files)
    recorded.update(removed_runtime)
    current_runtime = set(host.runtime_dropin_names())
    for name in current_runtime - set(recorded):
        text = host.read_runtime_dropin(name)
        if _memory_assignment_only(text):
            host.remove_runtime_dropin(name)
    for name, text in recorded.items():
        host.restore_runtime_dropin(name, text)


def _memory_assignment_only(text: str) -> bool:
    assignments = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";") or line.startswith("["):
            continue
        if "=" not in line:
            return False
        key, _value = line.split("=", 1)
        assignments.append(key.strip())
    return assignments == ["MemoryMax"]


def _restore_cgroup(host: Host, before: Snapshot) -> None:
    if before.cgroup_max is None or before.scanner_pid in {"", "0"}:
        return
    if before.scanner_cgroup != EXPECTED_CGROUP:
        return
    host.write_cgroup_max(before.scanner_cgroup, before.cgroup_max)


def _restoration_mismatches(before: Snapshot, after: Snapshot) -> list[str]:
    problems: list[str] = []
    if after.scanner_memory_raw != before.scanner_memory_raw:
        problems.append("MemoryMax")
    if after.cgroup_max != before.cgroup_max:
        problems.append("cgroup memory.max")
    if after.scanner_pid != before.scanner_pid:
        problems.append("scanner pid")
    if after.scanner_restarts != before.scanner_restarts:
        problems.append("scanner restarts")
    if after.scanner_active != before.scanner_active:
        problems.append("scanner state")
    if (
        after.futures_pid != before.futures_pid
        or after.futures_restarts != before.futures_restarts
        or after.futures_active != before.futures_active
    ):
        problems.append("futures identity")
    if after.futures_memory_raw != before.futures_memory_raw:
        problems.append("futures memory")
    if after.health_http != before.health_http:
        problems.append("health")
    if after.base_sha256 != before.base_sha256 or after.base_floor_ok != before.base_floor_ok:
        problems.append("base unit")
    if after.our_dropin != before.our_dropin:
        problems.append("etc drop-in")
    for name, digest in before.etc_files.items():
        if name == DROPIN_NAME:
            continue
        if after.etc_files.get(name) != digest:
            problems.append(f"etc {name}")
    for name in set(after.etc_files) - set(before.etc_files):
        if name != DROPIN_NAME:
            problems.append(f"unexpected etc {name}")
    for name, text in before.runtime_files.items():
        if after.runtime_files.get(name) != text:
            problems.append(f"runtime {name}")
    for name in set(after.runtime_files) - set(before.runtime_files):
        problems.append(f"unexpected runtime {name}")
    if after.futures_dropin_paths != before.futures_dropin_paths:
        problems.append("futures DropInPaths")
    if after.scanner_dropin_paths != before.scanner_dropin_paths:
        problems.append("scanner DropInPaths")
    if after.etc_control != before.etc_control:
        problems.append("etc system.control")
    if after.run_control != before.run_control:
        problems.append("run system.control")
    if after.scanner_need_reload != before.scanner_need_reload:
        problems.append("scanner NeedDaemonReload")
    if after.futures_need_reload != before.futures_need_reload:
        problems.append("futures NeedDaemonReload")
    return problems


def _prove_restored(
    host: Host,
    before: Snapshot,
    removed_runtime: dict[str, str],
    files_touched: bool,
) -> tuple[bool, str]:
    """Put files and the live limit back, then require a fresh snapshot to match."""

    errors: list[str] = []
    try:
        _restore_files(host, before, removed_runtime)
    except Exception as exc:
        errors.append(f"file restore failed: {exc}")
    if files_touched or removed_runtime:
        try:
            host.daemon_reload()
        except Exception as exc:
            errors.append(f"reload failed: {exc}")
    try:
        _restore_cgroup(host, before)
    except Exception as exc:
        errors.append(f"cgroup restore failed: {exc}")
    try:
        after = capture(host)
    except Exception as exc:
        errors.append(f"recapture failed: {exc}")
        return False, "; ".join(errors)
    mismatches = _restoration_mismatches(before, after)
    if mismatches:
        errors.append("mismatch: " + ", ".join(mismatches))
    if errors:
        return False, "; ".join(errors)
    return True, ""


def apply_target(host: Host, before: Snapshot, target_mib: int) -> Snapshot:
    _require_floor(before)
    if before.scanner_mib is None:
        raise MaintenanceFailure("options-scanner MemoryMax is not a finite mebibyte value")
    removed_runtime: dict[str, str] = {}
    files_touched = False
    try:
        _lower_live_limit(host, before, target_mib)
        _retire_mismatched_runtime(host, target_mib, removed_runtime)
        files_touched = bool(removed_runtime)
        _persist(host, target_mib)
        files_touched = True
        host.daemon_reload()
        _sync_live(host, before, target_mib)
        after = capture(host)
        verify_target(host, before, after, target_mib)
        return after
    except Exception as exc:
        files_touched = files_touched or bool(removed_runtime)
        if (
            isinstance(exc, MaintenanceFailure)
            and not exc.restored
            and not exc.unverified
            and "not changed" in str(exc)
            and not files_touched
            and not removed_runtime
        ):
            raise
        proved, detail = _prove_restored(host, before, removed_runtime, files_touched)
        if not proved:
            raise MaintenanceFailure(
                f"ROLLBACK UNVERIFIED / HOLD: {detail}",
                restored=False,
                unverified=True,
            ) from exc
        message = str(exc) if str(exc) else "memory change failed"
        raise MaintenanceFailure(message, restored=True) from exc


def _consistent(snapshot: Snapshot, mib: int) -> bool:
    if snapshot.scanner_mib != mib:
        return False
    if mib == MIN_MIB:
        return snapshot.our_dropin is None
    return snapshot.our_dropin == dropin_text(mib)


def _load_history(host: Host) -> list[dict[str, object]]:
    raw = host.read_optional(HISTORY_PATH)
    rows: list[dict[str, object]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MaintenanceFailure(
                "ROLLBACK UNVERIFIED / HOLD: maintenance history is unreadable",
                unverified=True,
            ) from exc
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _append_history(host: Host, row: dict[str, object]) -> None:
    host.append_line(HISTORY_PATH, json.dumps(row, sort_keys=True))


_CONTINUITY_FIELDS = (
    "futures_active",
    "futures_dropin_paths",
    "futures_memory_raw",
    "futures_pid",
    "futures_restarts",
    "health_http",
    "scanner_active",
    "scanner_dropin_paths",
    "scanner_pid",
    "scanner_restarts",
)


def _intent_row(stamp: str, command: str, before: Snapshot, target_mib: int) -> dict[str, object]:
    return {
        "action": "intent",
        "before_mib": before.scanner_mib,
        "base_sha256": before.base_sha256,
        "cgroup_max": before.cgroup_max,
        "command": command,
        "etc_files": before.etc_files,
        "futures_active": before.futures_active,
        "futures_dropin_paths": before.futures_dropin_paths,
        "futures_memory_raw": before.futures_memory_raw,
        "futures_need_reload": before.futures_need_reload,
        "futures_pid": before.futures_pid,
        "futures_restarts": before.futures_restarts,
        "health_http": before.health_http,
        "our_dropin": before.our_dropin,
        "result": "open",
        "runtime_files": before.runtime_files,
        "scanner_active": before.scanner_active,
        "scanner_dropin_paths": before.scanner_dropin_paths,
        "scanner_need_reload": before.scanner_need_reload,
        "scanner_pid": before.scanner_pid,
        "scanner_restarts": before.scanner_restarts,
        "stamp": stamp,
        "target_mib": target_mib,
    }


def _continuity_baseline(intent: dict[str, object]) -> dict[str, str]:
    """Return the pre-mutation service facts recovery must prove, or fail closed."""

    baseline: dict[str, str] = {}
    for key in _CONTINUITY_FIELDS:
        value = intent.get(key)
        if not isinstance(value, str):
            raise MaintenanceFailure(
                f"ROLLBACK UNVERIFIED / HOLD: intent has no {key} baseline",
                unverified=True,
            )
        baseline[key] = value
    for key in ("scanner_need_reload", "futures_need_reload"):
        value = intent.get(key)
        if value != "no":
            raise MaintenanceFailure(
                f"ROLLBACK UNVERIFIED / HOLD: intent has no quiet {key} baseline",
                unverified=True,
            )
        baseline[key] = "no"
    return baseline


def _closed_stamps(rows: list[dict[str, object]]) -> set[object]:
    closed: set[object] = set()
    for row in rows:
        action = row.get("action")
        result = row.get("result")
        if action == "intent-close" and result in {"ok", "restored", "unchanged"}:
            closed.add(row.get("closes"))
        elif action == "set" and result == "ok":
            closed.add(row.get("closes") or row.get("stamp"))
        elif action == "rollback" and result == "ok":
            closed.add(row.get("undoes"))
            closed.add(row.get("closes"))
    return closed


def _open_intents(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    closed = _closed_stamps(rows)
    return [row for row in rows if row.get("action") == "intent" and row.get("stamp") not in closed]


def _close_intent(host: Host, stamp: str, result: str) -> None:
    _append_history(host, {"action": "intent-close", "closes": stamp, "result": result})


def _record_intent(host: Host, rows: list[dict[str, object]], command: str, before: Snapshot, target_mib: int) -> str:
    stamp = _unique_stamp(host, rows)
    try:
        _append_history(host, _intent_row(stamp, command, before, target_mib))
    except MaintenanceFailure:
        raise
    except Exception as exc:
        raise MaintenanceFailure(
            "could not record the maintenance intent; configuration was not changed"
        ) from exc
    return stamp


def _revert_unrecorded(host: Host, before: Snapshot, stamp: str, exc: Exception) -> None:
    proved, detail = _prove_restored(host, before, {}, True)
    if proved:
        with contextlib.suppress(Exception):
            _close_intent(host, stamp, "restored")
        raise MaintenanceFailure("history record failed; memory change was reversed", restored=True) from exc
    raise MaintenanceFailure(
        f"ROLLBACK UNVERIFIED / HOLD: history record failed; {detail}",
        restored=False,
        unverified=True,
    ) from exc


def _log(host: Host, row: dict[str, object]) -> None:
    safe = {key: redact(value) if isinstance(value, str) else value for key, value in row.items()}
    safe.setdefault("ts", host.now().strftime("%Y-%m-%dT%H:%M:%SZ"))
    safe.setdefault("ssh_connection", host.env("SSH_CONNECTION") or "unknown")
    safe.setdefault("sudo_user", host.env("SUDO_USER") or "unknown")
    host.append_line(LOG_PATH, json.dumps(safe, sort_keys=True))


def _identity_log(before: Snapshot, after: Snapshot | None = None) -> dict[str, object]:
    row: dict[str, object] = {
        "scanner_pid": before.scanner_pid,
        "futures_pid": before.futures_pid,
        "before_mib": before.scanner_mib,
        "health_http_before": before.health_http,
    }
    if after is not None:
        row.update(
            {
                "after_mib": after.scanner_mib,
                "scanner_pid_unchanged": after.scanner_pid == before.scanner_pid,
                "futures_pid_unchanged": after.futures_pid == before.futures_pid,
                "restarts_unchanged": (
                    after.scanner_restarts == before.scanner_restarts
                    and after.futures_restarts == before.futures_restarts
                ),
                "health_http_after": after.health_http,
            }
        )
    return row


def _unique_stamp(host: Host, rows: list[dict[str, object]]) -> str:
    used = {row.get("stamp") for row in rows}
    base = host.now()
    for offset in range(60):
        stamp = (base + timedelta(seconds=offset)).strftime("%Y%m%dT%H%M%SZ")
        if stamp not in used:
            return stamp
    raise MaintenanceFailure("could not allocate a unique maintenance stamp")


def _open_sets(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    undone = {
        row.get("undoes")
        for row in rows
        if row.get("action") == "rollback" and row.get("result") == "ok"
    }
    return [
        row
        for row in rows
        if row.get("action") == "set" and row.get("result") == "ok" and row.get("stamp") not in undone
    ]


def format_snapshot(snapshot: Snapshot) -> str:
    lines = [
        "operation=options-scanner-memory",
        "action=show",
        f"active_state={snapshot.scanner_active}",
        f"main_pid={snapshot.scanner_pid}",
        f"n_restarts={snapshot.scanner_restarts}",
        f"memory_max_raw={snapshot.scanner_memory_raw}",
        f"memory_max_mib={'' if snapshot.scanner_mib is None else snapshot.scanner_mib}",
        f"cgroup_memory_max={snapshot.cgroup_max or ''}",
        f"cgroup_memory_current={snapshot.cgroup_current or ''}",
        f"cgroup_swap_current={snapshot.cgroup_swap or ''}",
        f"authorized_min_mib={MIN_MIB}",
        f"authorized_max_mib={MAX_MIB}",
        "within_authorized_window="
        + (
            "true"
            if snapshot.scanner_mib is not None and MIN_MIB <= snapshot.scanner_mib <= MAX_MIB
            else "false"
        ),
        "drop_in=" + ("present" if snapshot.our_dropin else "absent"),
        f"base_unit_floor={'350M' if snapshot.base_floor_ok else 'missing'}",
        f"health_http={snapshot.health_http}",
        f"futures_bot_main_pid={snapshot.futures_pid}",
        f"futures_bot_active_state={snapshot.futures_active}",
        f"futures_bot_memory_max={snapshot.futures_memory_raw}",
    ]
    return "\n".join(lines)


def do_show(host: Host) -> int:
    snapshot = capture(host)
    sys.stdout.write(format_snapshot(snapshot) + "\n")
    _log(host, {"event": "finish", "command": "options-scanner-memory show", "result": "ok"})
    return 0


def do_history(host: Host) -> int:
    rows = _load_history(host)
    if not rows:
        sys.stdout.write("history=empty\n")
    for row in rows:
        sys.stdout.write(json.dumps(row, sort_keys=True) + "\n")
    _log(host, {"event": "finish", "command": "options-scanner-memory history", "result": "ok"})
    return 0


def parse_approval_text(text: str) -> tuple[str, datetime]:
    """Return the operation and expiry from an exact two-line approval."""

    if text.count("\n") != 2 or not text.endswith("\n"):
        raise ValueError("approval file shape is invalid")
    operation_line, expires_line = text.splitlines()
    if not operation_line.startswith("operation=") or not expires_line.startswith("expires="):
        raise ValueError("approval file keys are invalid")
    operation = operation_line.removeprefix("operation=")
    expires_raw = expires_line.removeprefix("expires=")
    if not _STAMP.fullmatch(expires_raw):
        raise ValueError("approval expiry is invalid")
    try:
        action = parse_command_text(operation)
    except (Denied, UsageError) as exc:
        raise ValueError("approval operation is invalid") from exc
    if not isinstance(action, (SetAction, RollbackAction)) or canonical(action) != operation:
        raise ValueError("approval operation is invalid")
    expires = datetime.strptime(expires_raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return operation, expires


def consume_matching_approval(host: Host, command: str) -> str:
    """Consume the single root approval for this exact command.

    Missing, expired, writable, or ambiguous approvals are rejected and left
    in place. There is no way for this function to create an approval.
    """

    valid: list[str] = []
    problems: list[str] = []
    for name in host.approval_names():
        if name.endswith(".consumed") or not _APPROVAL_NAME.fullmatch(name):
            continue
        try:
            operation, expires = parse_approval_text(host.read_approval(name))
        except (OSError, UnicodeError, ValueError):
            continue
        if operation != command:
            continue
        if not host.approval_is_restricted(name):
            problems.append(f"{name} is not a restricted root-owned file")
            continue
        if expires <= host.now():
            problems.append(f"{name} is expired")
            continue
        valid.append(name)
    if len(valid) > 1:
        raise Denied("more than one approval matches this command")
    if len(valid) == 1:
        host.consume_approval(valid[0])
        return valid[0]
    if problems:
        raise Denied(problems[0])
    raise Denied("operator approval is required")


def _failure_result(exc: MaintenanceFailure) -> str:
    if exc.unverified:
        return "rollback_unverified"
    if exc.restored:
        return "rolled_back"
    return "rejected"


def do_self_check() -> int:
    sys.stdout.write(
        "\n".join(
            [
                "program=afs-maintenance",
                f"memory_window_mib={MIN_MIB}-{MAX_MIB}",
                "unit=options-scanner.service",
                "restarts=forbidden",
                "deploys=forbidden",
                "ssh_key_changes=forbidden",
                "mutations=require-operator-approval",
            ]
        )
        + "\n"
    )
    return 0


def do_set(host: Host, mib: int) -> int:
    with host.exclusive_lock():
        rows = _load_history(host)
        if _open_intents(rows):
            raise MaintenanceFailure("incomplete maintenance record; HOLD")
        before = capture(host)
        command = f"options-scanner-memory set {mib}M"
        _require_quiet_units(before)
        if _consistent(before, mib):
            _log(host, {"event": "finish", "command": command, "result": "noop", **_identity_log(before, before)})
            sys.stdout.write(f"result=noop\nalready_mib={mib}\nscanner_pid={before.scanner_pid}\n")
            return 0
        approval = consume_matching_approval(host, command)
        stamp = _record_intent(host, rows, command, before, mib)
        _log(
            host,
            {
                "event": "start",
                "command": command,
                "result": "started",
                "approval": approval,
                "stamp": stamp,
                **_identity_log(before),
            },
        )
        try:
            after = apply_target(host, before, mib)
        except MaintenanceFailure as exc:
            if not exc.unverified:
                result = "unchanged" if (not exc.restored and "not changed" in str(exc)) else "restored"
                with contextlib.suppress(Exception):
                    _close_intent(host, stamp, result)
            _log(
                host,
                {
                    "event": "finish",
                    "command": command,
                    "result": _failure_result(exc),
                    "error": str(exc),
                    **_identity_log(before),
                },
            )
            raise
        try:
            _append_history(
                host,
                {
                    "action": "set",
                    "after_mib": mib,
                    "before_mib": before.scanner_mib,
                    "closes": stamp,
                    "result": "ok",
                    "stamp": stamp,
                },
            )
        except Exception as exc:
            try:
                _revert_unrecorded(host, before, stamp, exc)
            except MaintenanceFailure as failure:
                _log(
                    host,
                    {
                        "event": "finish",
                        "command": command,
                        "result": _failure_result(failure),
                        "error": str(failure),
                        **_identity_log(before),
                    },
                )
                raise
        _log(
            host,
            {
                "event": "finish",
                "command": command,
                "result": "ok",
                "stamp": stamp,
                "approval": approval,
                **_identity_log(before, after),
            },
        )
        sys.stdout.write(
            "\n".join(
                [
                    "result=ok",
                    f"stamp={stamp}",
                    f"before_mib={before.scanner_mib}",
                    f"after_mib={mib}",
                    f"scanner_pid_unchanged={str(after.scanner_pid == before.scanner_pid).lower()}",
                    f"futures_pid_unchanged={str(after.futures_pid == before.futures_pid).lower()}",
                f"restarts_unchanged={str(after.scanner_restarts == before.scanner_restarts and after.futures_restarts == before.futures_restarts).lower()}",
                f"health_http={after.health_http}",
                    f"rollback=options-scanner-memory rollback {stamp}",
                ]
            )
            + "\n"
        )
        return 0


def _select_rollback(rows: list[dict[str, object]], stamp: str | None) -> dict[str, object]:
    open_sets = _open_sets(rows)
    if stamp is None:
        if not open_sets:
            raise UsageError("there is no memory change to roll back")
        return open_sets[-1]
    matches = [row for row in open_sets if row.get("stamp") == stamp]
    if not matches:
        raise UsageError("that stamp is unknown or already rolled back")
    return matches[-1]


def _restore_recorded_files(host: Host, our_dropin: str | None, runtime_files: dict[str, str]) -> None:
    if our_dropin is None:
        host.remove_our_dropin()
    else:
        host.write_our_dropin(our_dropin)
    current = set(host.runtime_dropin_names())
    for name in current - set(runtime_files):
        text = host.read_runtime_dropin(name)
        if _memory_assignment_only(text):
            host.remove_runtime_dropin(name)
    for name, text in runtime_files.items():
        host.restore_runtime_dropin(name, text)


def _runtime_record(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise MaintenanceFailure("ROLLBACK UNVERIFIED / HOLD: intent runtime record is unusable", unverified=True)
    recorded: dict[str, str] = {}
    for name, text in value.items():
        if not isinstance(name, str) or not isinstance(text, str):
            raise MaintenanceFailure("ROLLBACK UNVERIFIED / HOLD: intent runtime record is unusable", unverified=True)
        recorded[name] = text
    return recorded


def _recorded_dropin(intent: dict[str, object]) -> str | None:
    value = intent.get("our_dropin")
    if value is None:
        return None
    if not isinstance(value, str):
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: intent drop-in record is unusable",
            unverified=True,
        )
    return value


def _foreign_etc_changes(intent: dict[str, object], before: Snapshot) -> list[str]:
    """Names of scanner drop-ins, other than our memory file, that differ from the intent."""

    recorded = intent.get("etc_files")
    if not isinstance(recorded, dict):
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: intent has no etc file baseline",
            unverified=True,
        )
    changes: list[str] = []
    for name, digest in recorded.items():
        if not isinstance(name, str) or not isinstance(digest, str):
            raise MaintenanceFailure(
                "ROLLBACK UNVERIFIED / HOLD: intent etc file baseline is unusable",
                unverified=True,
            )
        if name == DROPIN_NAME:
            continue
        if before.etc_files.get(name) != digest:
            changes.append(name)
    for name in before.etc_files:
        if name != DROPIN_NAME and name not in recorded:
            changes.append(name)
    return changes


def _unexpected_runtime_on_disk(intent: dict[str, object], before: Snapshot) -> list[str]:
    """On-disk runtime drop-ins that are absent from or differ from the intent.

    Loaded ``DropInPaths`` alone is not enough: systemd keeps the pre-reload
    path set until ``daemon-reload``, so a new runtime file (for example an
    ``ExecStart=`` override) can sit on disk while ``systemctl show`` still
    looks unchanged. Missing intent files are allowed — recovery restores them.
    """

    recorded = _runtime_record(intent.get("runtime_files"))
    unexpected: list[str] = []
    for name, text in before.runtime_files.items():
        if recorded.get(name) != text:
            unexpected.append(name)
    return unexpected


def _require_same_unit_sources(intent: dict[str, object], before: Snapshot, baseline: dict[str, str]) -> None:
    """Refuse recovery before reload when the base unit or drop-in path set changed.

    The only DropInPaths change recovery may proceed through is our own memory
    drop-in appearing or disappearing. That is the crash between writing it
    and daemon-reload. Any other path, and any base-unit change, stays unloaded.
    """

    recorded_base = intent.get("base_sha256")
    if not isinstance(recorded_base, str) or not recorded_base:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: intent has no base unit baseline",
            unverified=True,
        )
    if before.base_sha256 != recorded_base:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: base unit changed",
            unverified=True,
        )
    if not _scanner_paths_allowed(baseline["scanner_dropin_paths"], before.scanner_dropin_paths):
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: scanner DropInPaths changed",
            unverified=True,
        )
    if before.futures_dropin_paths != baseline["futures_dropin_paths"]:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: futures DropInPaths changed",
            unverified=True,
        )


def _log_recovery_failure(host: Host, command: str, exc: MaintenanceFailure) -> None:
    with contextlib.suppress(Exception):
        _log(
            host,
            {
                "event": "finish",
                "command": command,
                "result": "rollback_unverified",
                "error": str(exc),
            },
        )


def _recover_open_intent(host: Host, intent: dict[str, object], requested: str | None) -> int:
    command = canonical(RollbackAction(requested))
    try:
        return _recover_recorded_intent(host, intent, command)
    except MaintenanceFailure as exc:
        labelled = exc
        if "ROLLBACK UNVERIFIED" not in str(exc):
            labelled = MaintenanceFailure(
                f"ROLLBACK UNVERIFIED / HOLD: {exc}",
                unverified=True,
            )
        _log_recovery_failure(host, command, labelled)
        if labelled is not exc:
            raise labelled from exc
        raise
    except Exception as exc:
        labelled = MaintenanceFailure(
            f"ROLLBACK UNVERIFIED / HOLD: {exc}",
            unverified=True,
        )
        _log_recovery_failure(host, command, labelled)
        raise labelled from exc


def _recover_recorded_intent(host: Host, intent: dict[str, object], command: str) -> int:
    baseline = _continuity_baseline(intent)
    before = capture(host)
    if before.futures_need_reload != "no" or before.etc_control or before.run_control:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: hidden unit state is present",
            unverified=True,
        )
    foreign = _foreign_etc_changes(intent, before)
    if foreign:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: unrelated drop-in changed: " + ", ".join(foreign),
            unverified=True,
        )
    unexpected_runtime = _unexpected_runtime_on_disk(intent, before)
    if unexpected_runtime:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: on-disk runtime drop-in changed: "
            + ", ".join(unexpected_runtime),
            unverified=True,
        )
    _require_same_unit_sources(intent, before, baseline)
    if before.scanner_need_reload != "no" and before.our_dropin == _recorded_dropin(intent):
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: hidden unit state is present",
            unverified=True,
        )
    approval = consume_matching_approval(host, command)
    our_dropin = intent.get("our_dropin")
    if our_dropin is not None and not isinstance(our_dropin, str):
        raise MaintenanceFailure("ROLLBACK UNVERIFIED / HOLD: intent drop-in record is unusable", unverified=True)
    runtime = _runtime_record(intent.get("runtime_files"))
    try:
        _restore_recorded_files(host, our_dropin if isinstance(our_dropin, str) else None, runtime)
        host.daemon_reload()
        cgroup = intent.get("cgroup_max")
        if (
            isinstance(cgroup, str)
            and before.scanner_pid not in {"", "0"}
            and before.scanner_cgroup == EXPECTED_CGROUP
        ):
            host.write_cgroup_max(before.scanner_cgroup, cgroup)
        after = capture(host)
    except MaintenanceFailure:
        raise
    except Exception as exc:
        raise MaintenanceFailure(f"ROLLBACK UNVERIFIED / HOLD: {exc}", unverified=True) from exc
    problems: list[str] = []
    if after.scanner_mib != intent.get("before_mib"):
        problems.append("MemoryMax")
    if isinstance(intent.get("cgroup_max"), str) and after.cgroup_max != intent.get("cgroup_max"):
        problems.append("cgroup memory.max")
    if after.our_dropin != (our_dropin if isinstance(our_dropin, str) else None):
        problems.append("etc drop-in")
    for name, text in runtime.items():
        if after.runtime_files.get(name) != text:
            problems.append(f"runtime {name}")
    continuity = (
        ("scanner pid", after.scanner_pid, "scanner_pid"),
        ("scanner restarts", after.scanner_restarts, "scanner_restarts"),
        ("scanner state", after.scanner_active, "scanner_active"),
        ("futures pid", after.futures_pid, "futures_pid"),
        ("futures restarts", after.futures_restarts, "futures_restarts"),
        ("futures state", after.futures_active, "futures_active"),
        ("futures memory", after.futures_memory_raw, "futures_memory_raw"),
        ("health", after.health_http, "health_http"),
        ("scanner DropInPaths", after.scanner_dropin_paths, "scanner_dropin_paths"),
        ("futures DropInPaths", after.futures_dropin_paths, "futures_dropin_paths"),
    )
    for label, got, key in continuity:
        if got != baseline[key]:
            problems.append(label)
    if after.scanner_need_reload != "no":
        problems.append("scanner NeedDaemonReload")
    if after.futures_need_reload != "no":
        problems.append("futures NeedDaemonReload")
    if problems:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: mismatch: " + ", ".join(problems),
            unverified=True,
        )
    try:
        _close_intent(host, str(intent.get("stamp")), "restored")
        _append_history(
            host,
            {
                "action": "rollback",
                "after_mib": intent.get("before_mib"),
                "before_mib": before.scanner_mib,
                "closes": intent.get("stamp"),
                "result": "ok",
                "stamp": _unique_stamp(host, _load_history(host)),
                "undoes": intent.get("stamp"),
            },
        )
    except MaintenanceFailure:
        raise
    except Exception as exc:
        raise MaintenanceFailure(
            "ROLLBACK UNVERIFIED / HOLD: restored state could not be recorded",
            unverified=True,
        ) from exc
    _log(
        host,
        {
            "event": "finish",
            "command": command,
            "result": "ok",
            "approval": approval,
            "undoes": intent.get("stamp"),
            **_identity_log(before, after),
        },
    )
    sys.stdout.write(
        "\n".join(
            [
                "result=ok",
                f"undoes={intent.get('stamp')}",
                f"restored_mib={intent.get('before_mib')}",
                f"scanner_pid_unchanged={str(after.scanner_pid == baseline['scanner_pid']).lower()}",
                f"futures_pid_unchanged={str(after.futures_pid == baseline['futures_pid']).lower()}",
                "restarts_unchanged="
                + str(
                    after.scanner_restarts == baseline["scanner_restarts"]
                    and after.futures_restarts == baseline["futures_restarts"]
                ).lower(),
            ]
        )
        + "\n"
    )
    return 0


def do_rollback(host: Host, stamp: str | None) -> int:
    with host.exclusive_lock():
        rows = _load_history(host)
        intents = _open_intents(rows)
        if stamp is None and intents:
            return _recover_open_intent(host, intents[-1], None)
        if stamp is not None:
            matched = [row for row in intents if row.get("stamp") == stamp]
            if matched:
                return _recover_open_intent(host, matched[-1], stamp)
        if intents:
            raise MaintenanceFailure("incomplete maintenance record; HOLD")
        selected = _select_rollback(rows, stamp)
        target = selected.get("before_mib")
        if not isinstance(target, int) or not MIN_MIB <= target <= MAX_MIB:
            raise MaintenanceFailure("recorded rollback target is outside 350M..600M")
        before = capture(host)
        command = canonical(RollbackAction(stamp))
        _require_quiet_units(before)
        if before.scanner_mib != selected.get("after_mib"):
            raise MaintenanceFailure(
                "current MemoryMax does not match the recorded change; rollback was not applied"
            )
        approval = consume_matching_approval(host, command)
        intent_stamp = _record_intent(host, rows, command, before, target)
        _log(
            host,
            {
                "event": "start",
                "command": command,
                "result": "started",
                "approval": approval,
                "stamp": intent_stamp,
                "undoes": selected.get("stamp"),
            },
        )
        try:
            after = apply_target(host, before, target)
        except MaintenanceFailure as exc:
            if not exc.unverified:
                result = "unchanged" if (not exc.restored and "not changed" in str(exc)) else "restored"
                with contextlib.suppress(Exception):
                    _close_intent(host, intent_stamp, result)
            _log(
                host,
                {
                    "event": "finish",
                    "command": command,
                    "result": _failure_result(exc),
                    "error": str(exc),
                },
            )
            raise
        try:
            _append_history(
                host,
                {
                    "action": "rollback",
                    "after_mib": target,
                    "before_mib": before.scanner_mib,
                    "closes": intent_stamp,
                    "result": "ok",
                    "stamp": _unique_stamp(host, _load_history(host)),
                    "undoes": selected.get("stamp"),
                },
            )
        except Exception as exc:
            try:
                _revert_unrecorded(host, before, intent_stamp, exc)
            except MaintenanceFailure as failure:
                _log(
                    host,
                    {
                        "event": "finish",
                        "command": command,
                        "result": _failure_result(failure),
                        "error": str(failure),
                    },
                )
                raise
        _log(
            host,
            {
                "event": "finish",
                "command": command,
                "result": "ok",
                "undoes": selected.get("stamp"),
                **_identity_log(before, after),
            },
        )
        sys.stdout.write(
            "\n".join(
                [
                    "result=ok",
                    f"undoes={selected.get('stamp')}",
                    f"restored_mib={target}",
                    f"scanner_pid_unchanged={str(after.scanner_pid == before.scanner_pid).lower()}",
                    f"futures_pid_unchanged={str(after.futures_pid == before.futures_pid).lower()}",
                    f"restarts_unchanged={str(after.scanner_restarts == before.scanner_restarts and after.futures_restarts == before.futures_restarts).lower()}",
                ]
            )
            + "\n"
        )
        return 0


def execute(argv: list[str], host: Host) -> int:
    try:
        action = parse_argv(argv)
    except Denied as exc:
        sys.stderr.write(f"DENIED: {exc}\n")
        with contextlib.suppress(Exception):
            host.ensure_state_dirs()
            _log(host, {"event": "finish", "command": redact(" ".join(argv[1:])[:200]), "result": "denied"})
        return 126
    except UsageError as exc:
        sys.stderr.write(f"USAGE: {exc}\n")
        return 2
    if isinstance(action, HelpAction):
        sys.stdout.write(help_text() + "\n")
        return 0
    if isinstance(action, SelfCheckAction):
        return do_self_check()
    try:
        host.ensure_state_dirs()
        if isinstance(action, AuditAction):
            _log(host, {"event": "delegate", "command": action.name, "result": "delegated"})
            return host.exec_audit(action.name)
        if isinstance(action, ShowAction):
            return do_show(host)
        if isinstance(action, HistoryAction):
            return do_history(host)
        if isinstance(action, SetAction):
            return do_set(host, action.mib)
        if isinstance(action, RollbackAction):
            return do_rollback(host, action.stamp)
    except Denied as exc:
        sys.stderr.write(f"DENIED: {exc}\n")
        return 126
    except UsageError as exc:
        sys.stderr.write(f"USAGE: {exc}\n")
        return 2
    except MaintenanceFailure as exc:
        sys.stderr.write(f"FAILED: {exc}\n")
        return 1
    raise Denied("command is not in the maintenance allowlist")


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _rewrite_record(path: Path, previous: bytes) -> None:
    tmp = path.with_name(path.name + ".partial-restore")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o640)
    try:
        written = 0
        while written < len(previous):
            chunk = os.write(fd, previous[written:])
            if chunk <= 0:
                raise OSError("maintenance record restore was incomplete")
            written += chunk
        os.fsync(fd)
    except Exception:
        os.close(fd)
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    os.close(fd)
    os.replace(tmp, path)
    _fsync_directory(path.parent)


def _restore_record_bytes(fd: int, path: Path, prior: int, previous: bytes) -> bool:
    try:
        os.ftruncate(fd, prior)
        os.fsync(fd)
        return True
    except OSError:
        pass
    try:
        _rewrite_record(path, previous)
    except OSError:
        return False
    return True
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class ProductionHost(Host):
    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, check=False, capture_output=True, text=True)

    def systemctl_show(self, unit: str) -> dict[str, str]:
        result = self._run(build_systemctl("show", unit=unit))
        if result.returncode != 0:
            raise MaintenanceFailure(f"systemctl show failed for {unit}")
        props: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                props[key] = value
        return props

    def daemon_reload(self) -> None:
        result = self._run(build_systemctl("daemon-reload"))
        if result.returncode != 0:
            raise MaintenanceFailure("daemon-reload failed")

    def _cgroup_file(self, control_group: str, leaf: str) -> Path:
        if control_group != EXPECTED_CGROUP or leaf not in {
            "memory.max",
            "memory.current",
            "memory.swap.current",
        }:
            raise Denied("cgroup path is not available to maintenance")
        return CGROUP_ROOT / control_group.lstrip("/") / leaf

    def read_cgroup(self, control_group: str, leaf: str) -> str | None:
        path = self._cgroup_file(control_group, leaf)
        if not path.is_file() or path.is_symlink():
            return None
        return path.read_text(encoding="utf-8")

    def write_cgroup_max(self, control_group: str, value: str) -> None:
        if not value.isdigit():
            raise Denied("cgroup memory value must be a byte count")
        path = self._cgroup_file(control_group, "memory.max")
        if path.is_symlink():
            raise MaintenanceFailure("refusing to follow a cgroup symlink")
        path.write_text(value + "\n", encoding="utf-8")

    def read_base_unit(self) -> str:
        if BASE_UNIT.is_symlink():
            raise MaintenanceFailure("refusing to read a symlinked unit file")
        return BASE_UNIT.read_text(encoding="utf-8")

    def _regular_names(self, directory: Path) -> list[str]:
        if not directory.exists():
            return []
        if directory.is_symlink():
            raise MaintenanceFailure("refusing to list a symlinked drop-in directory")
        names: list[str] = []
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                raise MaintenanceFailure(f"refusing to touch symlink {entry.name}")
            if entry.is_file():
                names.append(entry.name)
        return names

    def etc_dropin_names(self) -> list[str]:
        return self._regular_names(ETC_DROPIN_DIR)

    def read_etc_dropin(self, name: str) -> str:
        self._check_name(name)
        return (ETC_DROPIN_DIR / name).read_text(encoding="utf-8")

    def write_our_dropin(self, text: str) -> None:
        if not _DROPIN.fullmatch(text):
            raise Denied("drop-in content is not a MemoryMax override")
        path = ETC_DROPIN_DIR / DROPIN_NAME
        self._write_exact(path, text, 0o644)

    def remove_our_dropin(self) -> None:
        path = ETC_DROPIN_DIR / DROPIN_NAME
        if path.is_symlink():
            raise MaintenanceFailure("refusing to remove a symlinked drop-in")
        if path.exists():
            path.unlink()

    def runtime_dropin_names(self) -> list[str]:
        return self._regular_names(RUNTIME_DROPIN_DIR)

    def read_runtime_dropin(self, name: str) -> str:
        self._check_name(name)
        return (RUNTIME_DROPIN_DIR / name).read_text(encoding="utf-8")

    def remove_runtime_dropin(self, name: str) -> None:
        self._check_name(name)
        path = RUNTIME_DROPIN_DIR / name
        if not path.exists():
            return
        text = path.read_text(encoding="utf-8")
        if not _memory_assignment_only(text):
            raise MaintenanceFailure("refusing to remove a runtime drop-in that is not MemoryMax")
        if path.is_symlink():
            raise MaintenanceFailure("refusing to remove a symlinked runtime drop-in")
        path.unlink()

    def write_runtime_dropin(self, name: str, text: str) -> None:
        self._check_name(name)
        if not _memory_assignment_only(text):
            raise Denied("runtime drop-in is not a MemoryMax override")
        self._write_exact(RUNTIME_DROPIN_DIR / name, text, 0o644)

    def restore_runtime_dropin(self, name: str, text: str) -> None:
        self._check_name(name)
        self._write_exact(RUNTIME_DROPIN_DIR / name, text, 0o644)

    def _control_dir(self, area: str) -> Path:
        if area == "etc":
            return ETC_CONTROL_DIR
        if area == "run":
            return RUN_CONTROL_DIR
        raise Denied("control directory is not available to maintenance")

    def control_dropin_names(self, area: str) -> list[str]:
        return self._regular_names(self._control_dir(area))

    def read_control_dropin(self, area: str, name: str) -> str:
        self._check_name(name)
        path = self._control_dir(area) / name
        if path.is_symlink():
            raise MaintenanceFailure("refusing to read a symlinked system.control file")
        return path.read_text(encoding="utf-8")

    def health_code(self) -> str:
        result = self._run(
            [
                "/usr/bin/curl",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--max-time",
                "5",
                "--proto",
                "=http",
                HEALTH_URL,
            ]
        )
        code = result.stdout.strip()
        if result.returncode != 0 or not code.isdigit():
            return "000"
        return code

    def read_optional(self, path: Path) -> str:
        self._check_log_path(path)
        if not path.exists():
            return ""
        if path.is_symlink():
            raise MaintenanceFailure("refusing to read a symlinked maintenance record")
        return path.read_text(encoding="utf-8")

    def append_line(self, path: Path, line: str) -> None:
        self._check_log_path(path)
        if path.is_symlink():
            raise MaintenanceFailure("refusing to append through a symlink")
        payload = (line + "\n").encode()
        previous = path.read_bytes() if path.exists() else b""
        created = False
        try:
            fd = os.open(path, os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            fd = os.open(
                path,
                os.O_APPEND | os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                0o640,
            )
            created = True
            previous = b""
        try:
            prior = 0 if created else os.lseek(fd, 0, os.SEEK_END)
            written = 0
            try:
                while written < len(payload):
                    chunk = os.write(fd, payload[written:])
                    if chunk <= 0:
                        raise MaintenanceFailure("maintenance record write was incomplete")
                    written += chunk
                os.fsync(fd)
            except Exception as original:
                if not _restore_record_bytes(fd, path, prior, previous):
                    raise MaintenanceFailure(
                        "ROLLBACK UNVERIFIED / HOLD: maintenance history may contain a partial record",
                        unverified=True,
                    ) from original
                raise
        finally:
            os.close(fd)
        if created:
            try:
                _fsync_directory(path.parent)
            except OSError as exc:
                raise MaintenanceFailure(
                    "ROLLBACK UNVERIFIED / HOLD: maintenance intent was written but its directory was not synced",
                    unverified=True,
                ) from exc

    def approval_names(self) -> list[str]:
        if not APPROVAL_DIR.exists():
            return []
        if APPROVAL_DIR.is_symlink():
            raise MaintenanceFailure("refusing to list a symlinked approval directory")
        return [
            entry.name
            for entry in sorted(APPROVAL_DIR.iterdir())
            if entry.is_file() or entry.is_symlink()
        ]

    def read_approval(self, name: str) -> str:
        self._check_approval_name(name)
        path = APPROVAL_DIR / name
        if path.is_symlink():
            raise Denied("approval is not a regular file")
        return path.read_text(encoding="utf-8")

    def approval_is_restricted(self, name: str) -> bool:
        self._check_approval_name(name)
        path = APPROVAL_DIR / name
        try:
            info = os.lstat(path)
        except OSError:
            return False
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022:
            return False
        return info.st_uid == 0

    def consume_approval(self, name: str) -> None:
        self._check_approval_name(name)
        if name.endswith(".consumed"):
            raise Denied("approval is already consumed")
        path = APPROVAL_DIR / name
        destination = APPROVAL_DIR / f"{name}.consumed"
        if path.is_symlink() or destination.is_symlink() or destination.exists():
            raise Denied("approval cannot be consumed")
        os.rename(path, destination)

    def ensure_state_dirs(self) -> None:
        if not SHARED_DIR.is_dir() or SHARED_DIR.is_symlink():
            raise MaintenanceFailure("shared directory is missing")
        for directory in (SHARED_DIR / "maintenance", STATE_DIR, APPROVAL_DIR, SHARED_DIR / "logs"):
            directory.mkdir(mode=0o750, exist_ok=True)

    @contextlib.contextmanager
    def exclusive_lock(self) -> Iterator[None]:
        self.ensure_state_dirs()
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def env(self, name: str) -> str:
        if name not in {"SSH_CONNECTION", "SSH_CLIENT", "SUDO_USER"}:
            return ""
        return os.environ.get(name, "")

    def exec_audit(self, token: str) -> int:
        if token not in AUDIT_COMMANDS:
            raise Denied("command is not in the maintenance allowlist")
        os.execv(AUDIT_PROGRAM, [str(AUDIT_PROGRAM), token])
        return 1

    def _check_approval_name(self, name: str) -> None:
        if name != Path(name).name or not _APPROVAL_NAME.fullmatch(name):
            raise Denied("approval name is not available to maintenance")

    def _check_name(self, name: str) -> None:
        if name != Path(name).name or not _SAFE_TEXT.fullmatch(name):
            raise Denied("drop-in name is not available to maintenance")

    def _check_log_path(self, path: Path) -> None:
        if path not in {LOG_PATH, HISTORY_PATH}:
            raise Denied("record path is not available to maintenance")

    def _write_exact(self, path: Path, text: str, mode: int) -> None:
        if path.is_symlink():
            raise MaintenanceFailure("refusing to write through a symlink")
        path.parent.mkdir(mode=0o755, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)
        os.chmod(tmp, mode)
        os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    try:
        return execute(argv if argv is not None else sys.argv, ProductionHost())
    except Denied as exc:
        sys.stderr.write(f"DENIED: {exc}\n")
        return 126
    except UsageError as exc:
        sys.stderr.write(f"USAGE: {exc}\n")
        return 2
    except MaintenanceFailure as exc:
        sys.stderr.write(f"FAILED: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
