"""Sudoers drop-in for grok-ops.

Each verb is one absolute path with no arguments, no wildcard, and no shell.
visudo -c is the syntax check; validate_sudoers_text is the policy check.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

VERB_PATHS = (
    "/usr/local/sbin/afs-ops-preflight",
    "/usr/local/sbin/afs-ops-backup",
    "/usr/local/sbin/afs-ops-baseline-reset-dry-run",
    "/usr/local/sbin/afs-ops-baseline-reset",
    "/usr/local/sbin/afs-ops-promote",
    "/usr/local/sbin/afs-ops-verify",
    "/usr/local/sbin/afs-ops-rollback",
    "/usr/local/sbin/afs-ops-status",
)
REFUSAL_PATH = "/usr/local/sbin/afs-ops-record-refusal"
SUDO_PATHS = VERB_PATHS + (REFUSAL_PATH,)

_SHELLS = ("/bin/sh", "/bin/bash", "/usr/bin/bash", "/bin/dash", "/usr/bin/sh", "sudoedit")
_LINE = "grok-ops ALL=(root) NOPASSWD: {path}"


def render_sudoers() -> str:
    lines = [
        "# grok-ops deploy lane. Exact script paths. No shell.",
        "Defaults:grok-ops env_reset",
        'Defaults:grok-ops secure_path="/usr/sbin:/usr/bin:/sbin:/bin"',
    ]
    lines.extend(_LINE.format(path=path) for path in SUDO_PATHS)
    lines.append("")
    return "\n".join(lines)


def validate_sudoers_text(text: str) -> None:
    if "*" in text or "?" in text:
        raise ValueError("sudoers contains a wildcard")
    upper = text.upper()
    if "SETENV" in upper or "ENV_KEEP" in upper:
        raise ValueError("sudoers keeps the caller environment")
    lowered = text.lower()
    for shell in _SHELLS:
        if shell in text:
            raise ValueError("sudoers grants a shell")
    if "nopasswd: all" in lowered or "afs-ops-authorize" in text:
        raise ValueError("sudoers grants more than the verb scripts")
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("Defaults"):
            continue
        prefix = "grok-ops ALL=(root) NOPASSWD:"
        if not stripped.startswith(prefix):
            raise ValueError("sudoers command line is not an exact grok-ops path")
        path = stripped[len(prefix):].strip()
        if path == "":
            raise ValueError("sudoers path is empty")
        if path not in SUDO_PATHS or " " in path:
            raise ValueError("sudoers path is not an allowlisted script")
        found.append(path)
    if tuple(found) != SUDO_PATHS:
        raise ValueError("sudoers verb paths do not match the allowlist")


def visudo_check(text: str, visudo: str = "visudo") -> None:
    validate_sudoers_text(text)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".sudoers", delete=False) as handle:
        handle.write(text)
        path = handle.name
    try:
        result = subprocess.run(
            [visudo, "-c", "-f", path],
            check=False,
            capture_output=True,
            text=True,
        )
    finally:
        Path(path).unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise ValueError(detail or "visudo rejected the sudoers drop-in")


def main() -> int:
    text = render_sudoers()
    try:
        validate_sudoers_text(text)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
