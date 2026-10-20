#!/usr/bin/python3 -I
"""Root-owned, allowlisted systemd MemoryMax maintenance; never deploys or restarts.

Install ONLY behind an existing forced-command SSH dispatcher + exact sudoers argv.
Mutating calls additionally require a recent root-owned approval file.
"""
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

UNIT = "options-scanner.service"
MIB = 1024 * 1024
LOW = 350 * MIB
HIGH = 600 * MIB
APPROVAL = Path("/etc/afs-maintenance/options-memory-approval.json")
RECEIPT = Path("/var/lib/afs-maintenance/options-memory-receipt.json")
AUDIT = Path("/var/log/afs-options-memory-audit.jsonl")
LOCK = Path("/run/lock/afs-options-memory.lock")
SYSTEMCTL = "/usr/bin/systemctl"
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "SYSTEMD_PAGER": "cat", "SYSTEMD_COLORS": "0"}


class GateError(Exception):
    pass


def secure_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise GateError(f"unsafe ownership/mode for {path}")
        if s.st_size > 8192:
            raise GateError("oversized approval/receipt")
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            return json.load(fh)
    finally:
        if fd >= 0:
            os.close(fd)


def approved(action, *, now=None, source=APPROVAL):
    try:
        a = secure_json(source)
        if set(a) != {"schema", "unit", "allowed_actions", "expires_utc"}:
            raise GateError("approval schema mismatch")
        if a["schema"] != 1 or a["unit"] != UNIT or a["allowed_actions"] != ["apply-600", "rollback-350"]:
            raise GateError("approval scope mismatch")
        expiry = dt.datetime.fromisoformat(a["expires_utc"].replace("Z", "+00:00"))
        now = now or dt.datetime.now(dt.timezone.utc)
        if expiry.tzinfo is None or now >= expiry:
            raise GateError("maintenance approval expired")
        if action not in a["allowed_actions"]:
            raise GateError("action not approved")
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise GateError(f"invalid or missing approval: {type(exc).__name__}") from exc


def run_systemctl(*args):
    try:
        p = subprocess.run([SYSTEMCTL, *args], capture_output=True, text=True,
                           env=SAFE_ENV, cwd="/", timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError(f"systemctl unavailable: {type(exc).__name__}") from exc
    if p.returncode != 0:
        raise GateError(f"systemctl failed (exit={p.returncode})")
    return p.stdout


def properties(runner=run_systemctl):
    out = runner("show", UNIT, "--no-pager", "-p", "LoadState", "-p", "ActiveState",
                 "-p", "MemoryMax", "-p", "MemoryHigh", "-p", "MemoryCurrent",
                 "-p", "MemorySwapCurrent", "-p", "ControlGroup", "-p", "NRestarts")
    values = {}
    for line in out.splitlines():
        if "=" not in line:
            raise GateError("malformed systemctl show output")
        k, v = line.split("=", 1)
        if k in values:
            raise GateError("duplicate systemctl show field")
        values[k] = v
    required = {"LoadState", "ActiveState", "MemoryMax", "MemoryHigh", "MemoryCurrent", "MemorySwapCurrent", "ControlGroup", "NRestarts"}
    if set(values) != required:
        raise GateError("incomplete systemctl show output")
    if values["LoadState"] != "loaded" or values["ActiveState"] != "active":
        raise GateError("scanner not loaded and active")
    return values


def amount(s):
    if s in ("infinity", "max"):
        return None
    if not re.fullmatch(r"[0-9]+", s):
        raise GateError("invalid systemd memory amount")
    return int(s)


def mem_available(meminfo):
    m = re.search(r"^MemAvailable:\s+(\d+) kB$", meminfo, re.MULTILINE)
    if not m:
        raise GateError("MemAvailable unavailable")
    return int(m.group(1)) * 1024


def parent_caps(control_group, root=Path("/sys/fs/cgroup")):
    """Return ancestor cgroup v2 memory.max values; fail closed on missing files."""
    if not control_group.startswith("/") or any(t in ("", ".", "..") for t in control_group.split("/")[1:]):
        raise GateError("invalid control group")
    if not (root / "cgroup.controllers").is_file():
        raise GateError("cgroup v2 not verified")
    parts = control_group.strip("/").split("/")
    result = []
    for i in range(len(parts)):
        path = root.joinpath(*parts[:i], "memory.max")
        try:
            result.append(amount(path.read_text(encoding="ascii").strip()))
        except OSError as exc:
            raise GateError("unable to read cgroup memory cap") from exc
    return result


def preflight(p, *, meminfo=None, caps=None):
    if amount(p["MemoryMax"]) != LOW:
        raise GateError("apply requires current MemoryMax exactly 350MiB")
    high = amount(p["MemoryHigh"])
    if high is not None and high < HIGH:
        raise GateError("MemoryHigh below target, requires operator review")
    if meminfo is None:
        meminfo = Path("/proc/meminfo").read_text(encoding="ascii")
    if mem_available(meminfo) < 800 * MIB:
        raise GateError("insufficient host MemAvailable (<800MiB)")
    if caps is None:
        caps = parent_caps(p["ControlGroup"])
    if any(cap is not None and cap < HIGH + 128 * MIB for cap in caps):
        raise GateError("parent cgroup limit leaves insufficient headroom")


def write_root_json(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    s = path.parent.stat()
    if s.st_uid != 0 or s.st_mode & 0o022:
        raise GateError("unsafe state directory")
    fd, tmp = tempfile.mkstemp(prefix=".afs-options-memory-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(action, status, *, before=None, after=None):
    entry = {"utc": dt.datetime.now(dt.timezone.utc).isoformat(), "service": UNIT,
             "action": action, "status": status, "before": before, "after": after,
             "actor": os.getenv("SUDO_USER", "admin")}
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(AUDIT, flags, 0o600)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise GateError("unsafe audit log")
        os.write(fd, (json.dumps(entry, sort_keys=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    return entry


def execute(action, *, runner=run_systemctl, authorize=approved, getprops=properties,
            host_meminfo=None, caps=None, receipt=RECEIPT, audit=record):
    if action not in ("inspect", "apply-600", "rollback-350"):
        raise GateError("unrecognized action")
    p = getprops(runner)
    before = amount(p["MemoryMax"])
    if action == "inspect":
        return {"action": action, "status": "read_only", "service": UNIT,
                "MemoryMax": before, "MemoryCurrent": amount(p["MemoryCurrent"]),
                "MemorySwapCurrent": amount(p["MemorySwapCurrent"]),
                "MemoryHigh": amount(p["MemoryHigh"]), "NRestarts": p["NRestarts"]}
    if action == "apply-600":
        authorize(action)
        preflight(p, meminfo=host_meminfo, caps=caps)
        target, expected = HIGH, "600M"
        # Log intended mutation before executing. Fail closed if the audit log is not writable.
        audit(action, "attempt", before=before)
        try:
            runner("set-property", "--runtime", UNIT, "MemoryMax=" + expected)
            q = getprops(runner)
            after = amount(q["MemoryMax"])
            if after != target or q["NRestarts"] != p["NRestarts"]:
                raise GateError("post-set limit/restart verification failed")
            write_root_json(receipt, {"schema": 1, "unit": UNIT, "before": LOW, "after": HIGH,
                                      "utc": dt.datetime.now(dt.timezone.utc).isoformat()})
            audit(action, "verified", before=before, after=after)
        except Exception:
            # Approved paired safety recovery; no service restart. Report any failure.
            try:
                runner("set-property", "--runtime", UNIT, "MemoryMax=350M")
                restored = amount(getprops(runner)["MemoryMax"])
                audit(action, "auto_rollback" if restored == LOW else "rollback_unverified", before=before, after=restored)
            except Exception as rollback_exc:
                raise GateError("apply failed AND automatic rollback unverified; administrator required") from rollback_exc
            raise
    else:
        if before != HIGH:
            raise GateError("rollback requires current MemoryMax exactly 600MiB")
        try:
            receipt_data = secure_json(receipt)
            if receipt_data.get("schema") != 1 or receipt_data.get("unit") != UNIT or receipt_data.get("before") != LOW or receipt_data.get("after") != HIGH:
                raise GateError("no valid prior approved apply receipt")
        except OSError as exc:
            raise GateError("missing prior apply receipt; admin rollback required") from exc
        # Paired rollback remains permitted after approval expiry, but only with saved receipt.
        audit(action, "attempt", before=before)
        runner("set-property", "--runtime", UNIT, "MemoryMax=350M")
        q = getprops(runner)
        after = amount(q["MemoryMax"])
        verified = after == LOW and q["NRestarts"] == p["NRestarts"]
        audit(action, "verified" if verified else "verification_failed", before=before, after=after)
        if not verified:
            raise GateError("rollback verification failed")
    return {"action": action, "status": "verified", "service": UNIT, "before": before, "after": after,
            "runtime_only": True, "restart_performed": False}


def main(argv):
    if os.geteuid() != 0:
        raise GateError("requires root through exact sudoers command")
    if len(argv) != 1:
        raise GateError("exactly one fixed action required")
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = execute(argv[0])
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(fd)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (GateError, OSError, ValueError, BlockingIOError) as exc:
        # Never echo exceptions containing environment values or approval file content.
        print(json.dumps({"status": "HOLD", "reason": str(exc)}), file=sys.stderr)
        sys.exit(2)
