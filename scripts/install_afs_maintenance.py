#!/usr/bin/env python3
"""Install the restricted maintenance interface. Does nothing without --apply.

The install changes three things, and nothing else:

1. Copy scripts/afs_maintenance.py to /usr/local/sbin/afs-maintenance.
2. Copy scripts/afs-maintenance.sudoers to /etc/sudoers.d/afs-maintenance.
3. On the existing grok-audit account, change only the command= restriction
   of the single key whose comment is exactly cursor-cloud-agent.

The key blob is not changed. No other authorized_keys line is changed.
No service is reloaded or restarted, and MemoryMax is not changed.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)
CURSOR_COMMENT = "cursor-cloud-agent"
AUDIT_OPTIONS = (
    'restrict,command="sudo -n /usr/local/sbin/afs-grok-audit \\"$SSH_ORIGINAL_COMMAND\\""'
)
MAINTENANCE_OPTIONS = (
    'restrict,command="sudo -n /usr/local/sbin/afs-maintenance run \\"$SSH_ORIGINAL_COMMAND\\""'
)


class InstallError(Exception):
    """The install cannot proceed without changing something it must not change."""


def _split_key_line(line: str) -> tuple[str, str, str, str]:
    bare = [key_type for key_type in KEY_TYPES if line.startswith(key_type + " ")]
    matches: list[int] = []
    for key_type in KEY_TYPES:
        marker = f" {key_type} "
        start = 0
        while True:
            found = line.find(marker, start)
            if found < 0:
                break
            matches.append(found)
            start = found + 1
    if len(bare) == 1 and not matches:
        options = ""
        rest = line.strip()
    elif len(matches) == 1 and not bare:
        index = matches[0]
        options = line[:index].strip()
        rest = line[index + 1 :].strip()
    else:
        raise InstallError("authorized_keys line does not have exactly one key type")
    parts = rest.split()
    if len(parts) < 3:
        raise InstallError("authorized_keys line is missing a comment")
    key_type, blob = parts[0], parts[1]
    comment = " ".join(parts[2:])
    return options, key_type, blob, comment


def rewrite_authorized_keys(text: str) -> str:
    """Return the file text with only the cursor-cloud-agent command changed."""

    if "\r" in text:
        raise InstallError("authorized_keys must use Unix newlines")
    ends_with_newline = text.endswith("\n")
    lines = text.split("\n")
    if ends_with_newline:
        if lines and lines[-1] == "":
            lines = lines[:-1]
    rewritten: list[str] = []
    seen = 0
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            rewritten.append(line)
            continue
        options, key_type, blob, comment = _split_key_line(line)
        if comment != CURSOR_COMMENT:
            rewritten.append(line)
            continue
        seen += 1
        if options not in {AUDIT_OPTIONS, MAINTENANCE_OPTIONS}:
            raise InstallError("cursor-cloud-agent command restriction is not the expected audit command")
        if key_type != "ssh-ed25519" or not blob:
            raise InstallError("cursor-cloud-agent key is not an ssh-ed25519 key")
        rewritten.append(f"{MAINTENANCE_OPTIONS} {key_type} {blob} {comment}")
        if rewritten[-1].split()[-2] != blob:
            raise InstallError("refusing to write a changed key blob")
    if seen != 1:
        raise InstallError("expected exactly one cursor-cloud-agent key")
    result = "\n".join(rewritten)
    if ends_with_newline:
        result += "\n"
    _assert_blobs_unchanged(text, result)
    return result


def _assert_blobs_unchanged(before: str, after: str) -> None:
    def blobs(text: str) -> list[str]:
        found = []
        for line in text.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            _options, _key_type, blob, _comment = _split_key_line(line)
            found.append(blob)
        return found

    if blobs(before) != blobs(after):
        raise InstallError("key material would change; refusing")


def authorized_keys_path(root: Path) -> Path:
    return root / "home" / "grok-audit" / ".ssh" / "authorized_keys"


def install_paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "usr" / "local" / "sbin" / "afs-maintenance",
        root / "etc" / "sudoers.d" / "afs-maintenance",
    )


def _owned_file(path: Path, data: bytes, uid: int, gid: int, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    if tmp.exists() or tmp.is_symlink():
        raise InstallError("authorized_keys temporary file already exists")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, data)
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
    except Exception:
        os.close(fd)
        tmp.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.replace(tmp, path)
    os.chown(path, uid, gid)
    os.chmod(path, mode)


def _replace_preserving_owner(path: Path, data: bytes) -> None:
    """Replace a file without changing the owner sshd will use to read it."""

    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise InstallError("authorized_keys is not a regular file")
    mode = stat.S_IMODE(info.st_mode)
    original = path.read_bytes()
    try:
        _owned_file(path, data, info.st_uid, info.st_gid, mode)
    except Exception as exc:
        with contextlib.suppress(Exception):
            _owned_file(path, original, info.st_uid, info.st_gid, mode)
        raise InstallError("authorized_keys owner was not preserved") from exc
    after = os.lstat(path)
    preserved = (
        not stat.S_ISLNK(after.st_mode)
        and stat.S_ISREG(after.st_mode)
        and after.st_uid == info.st_uid
        and after.st_gid == info.st_gid
        and stat.S_IMODE(after.st_mode) == mode
    )
    if preserved:
        return
    with contextlib.suppress(Exception):
        _owned_file(path, original, info.st_uid, info.st_gid, mode)
    raise InstallError("authorized_keys owner was not preserved")


def _write_file(path: Path, data: bytes, mode: int) -> None:
    if path.is_symlink():
        raise InstallError(f"refusing to replace symlink {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def check_sudoers(path: Path) -> None:
    visudo = shutil.which("visudo")
    if visudo is None:
        raise InstallError("visudo is required before the sudoers file can be installed")
    result = subprocess.run([visudo, "-cf", str(path)], check=False, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise InstallError(detail or "visudo rejected the sudoers file")


def run_install(
    *,
    root: Path,
    script_path: Path,
    sudoers_path: Path,
    apply: bool,
    sudoers_check=check_sudoers,
) -> str:
    root = root.resolve()
    program, sudoers_dest = install_paths(root)
    keys = authorized_keys_path(root)
    script = script_path.read_bytes()
    sudoers = sudoers_path.read_bytes()
    if b"NOPASSWD:ALL" in sudoers or b"/bin/bash" in sudoers or b"/bin/sh" in sudoers:
        raise InstallError("sudoers fragment grants more than the maintenance program")
    if apply and root == Path("/") and os.geteuid() != 0:
        raise InstallError("apply on the real root filesystem must run as root")
    if not keys.is_file() or keys.is_symlink():
        raise InstallError("grok-audit authorized_keys is missing")
    sudoers_check(sudoers_path)
    original = keys.read_text(encoding="utf-8")
    updated = rewrite_authorized_keys(original)
    action = "unchanged" if updated == original else "command-restriction-updated"
    if not apply:
        return "\n".join(
            [
                "dry-run",
                f"program={program}",
                f"sudoers={sudoers_dest}",
                f"authorized_keys={keys}",
                f"cursor_cloud_agent={action}",
                "memory_max=not-changed",
                "services=not-restarted",
                "daemon_reload=not-run",
            ]
        )
    _write_file(program, script, 0o755)
    if not stat.S_ISREG(program.stat().st_mode):
        raise InstallError("maintenance program was not installed as a regular file")
    _write_file(sudoers_dest, sudoers, 0o440)
    sudoers_check(sudoers_dest)
    if updated != original:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = keys.with_name(f"authorized_keys.bak-afs-maintenance-{stamp}")
        if backup.exists():
            raise InstallError("authorized_keys backup already exists")
        _write_file(backup, original.encode(), 0o600)
        _replace_preserving_owner(keys, updated.encode())
    return "\n".join(
        [
            "installed",
            f"program={program}",
            f"sudoers={sudoers_dest}",
            f"cursor_cloud_agent={action}",
            "memory_max=not-changed",
            "services=not-restarted",
            "daemon_reload=not-run",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Install the restricted AFS maintenance interface")
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--script", type=Path, default=Path(__file__).with_name("afs_maintenance.py"))
    parser.add_argument("--sudoers", type=Path, default=Path(__file__).with_name("afs-maintenance.sudoers"))
    parser.add_argument("--apply", action="store_true", help="write the program, sudoers file, and command restriction")
    args = parser.parse_args(argv)
    try:
        sys.stdout.write(run_install(
            root=args.root,
            script_path=args.script,
            sudoers_path=args.sudoers,
            apply=args.apply,
        ) + "\n")
    except (OSError, InstallError) as exc:
        sys.stderr.write(f"NOT INSTALLED: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
