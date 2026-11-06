"""Operator signature for a baseline reset.

The signed record binds the release SHA, the canonical manifest digest, the
ticket, and an expiry. ``ssh-keygen -Y`` checks it in the namespace
``afs-release-approval`` against a fixed allowed_signers file. That file is
not taken from ``--shared-dir`` and is not inside the staged release tree.

No private key is created or stored here. The operator signs on their own
machine and installs only the public key.
"""

from __future__ import annotations

import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

NAMESPACE = "afs-release-approval"
ALLOWED_SIGNERS_PATH = Path("/root/afs-shared/baseline_allowed_signers")
_FIELDS = ("target_sha", "manifest_digest", "ticket", "expires")


class ApprovalError(Exception):
    """The approval is missing, unsafe, or not a valid operator signature."""


def canonical_approval(
    *,
    target_sha: str,
    manifest_digest: str,
    ticket: str,
    expires: str,
) -> bytes:
    """Exact bytes the operator signs. Field order is part of the signature."""
    lines = [
        "afs-release-approval",
        "version=1",
        f"target_sha={target_sha}",
        f"manifest_digest={manifest_digest}",
        f"ticket={ticket}",
        f"expires={expires}",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def parse_approval(raw: bytes) -> dict[str, str]:
    """Accept only the canonical record. Extra bytes fail closed."""
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ApprovalError("approval unreadable") from exc
    lines = text.splitlines()
    if text != "\n".join(lines) + "\n":
        raise ApprovalError("approval is not canonical")
    if len(lines) != 6 or lines[0] != "afs-release-approval" or lines[1] != "version=1":
        raise ApprovalError("approval is not canonical")
    fields: dict[str, str] = {}
    for line, name in zip(lines[2:], _FIELDS):
        prefix = f"{name}="
        if not line.startswith(prefix) or line == prefix:
            raise ApprovalError("approval is not canonical")
        fields[name] = line[len(prefix):]
    rebuilt = canonical_approval(
        target_sha=fields["target_sha"],
        manifest_digest=fields["manifest_digest"],
        ticket=fields["ticket"],
        expires=fields["expires"],
    )
    if rebuilt != raw:
        raise ApprovalError("approval is not canonical")
    return fields


def signer_stat_problem(st: object) -> str | None:
    """None when the allowed_signers stat is a root-owned regular file.

    Group and other write bits are refused. Owner write is allowed so root
    can install a public key.
    """
    mode = getattr(st, "st_mode", None)
    uid = getattr(st, "st_uid", None)
    if not isinstance(mode, int) or not isinstance(uid, int):
        return "allowed_signers unreadable"
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        return "allowed_signers not a regular file"
    if uid != 0:
        return "allowed_signers not root-owned"
    if mode & 0o022:
        return "allowed_signers writable by group or other"
    return None


def require_allowed_signers(path: Path, st: object | None = None) -> None:
    """Refuse a missing, symlinked, non-root, or group/other-writable file."""
    if st is None:
        try:
            listed = path.lstat()
        except OSError as exc:
            raise ApprovalError("allowed_signers missing") from exc
        if stat.S_ISLNK(listed.st_mode):
            raise ApprovalError("allowed_signers is a symlink")
        st = listed
    problem = signer_stat_problem(st)
    if problem:
        raise ApprovalError(problem)


def _principals(text: str) -> list[str]:
    found: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for principal in stripped.split()[0].split(","):
            if principal:
                found.append(principal)
    return found


def verify_signature(message: bytes, signature_path: Path, allowed_path: Path) -> str:
    """Return the allowed principal whose key signed ``message``."""
    try:
        principals = _principals(allowed_path.read_text(encoding="utf-8"))
        signature_path.read_bytes()
    except OSError as exc:
        raise ApprovalError("approval signature unreadable") from exc
    if not principals:
        raise ApprovalError("allowed_signers empty")
    for principal in principals:
        result = subprocess.run(
            [
                "ssh-keygen", "-Y", "verify",
                "-f", str(allowed_path),
                "-I", principal,
                "-n", NAMESPACE,
                "-s", str(signature_path),
            ],
            input=message,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return principal
    raise ApprovalError("approval signature refused")


def _parse_expiry(value: str) -> datetime:
    if len(value) != 20 or not value.endswith("Z"):
        raise ApprovalError("approval expiry refused")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ApprovalError("approval expiry refused") from exc
    return parsed.replace(tzinfo=timezone.utc)


def verify_reset_approval(
    *,
    approval_path: Path,
    signature_path: Path,
    allowed_signers: Path,
    target_sha: str,
    manifest_digest: str,
    ticket: str,
    now: datetime,
    signer_stat: object | None = None,
) -> str:
    """Verify the signed record and return the signer principal.

    ``signer_stat`` is only for tests of the ownership predicate. The command
    line does not accept it. ``now`` must be timezone-aware.
    """
    if now.tzinfo is None:
        raise ApprovalError("approval expiry refused")
    require_allowed_signers(allowed_signers, signer_stat)
    try:
        raw = approval_path.read_bytes()
    except OSError as exc:
        raise ApprovalError("approval unreadable") from exc
    fields = parse_approval(raw)
    if fields["target_sha"] != target_sha:
        raise ApprovalError("approval target sha does not match")
    if fields["manifest_digest"] != manifest_digest:
        raise ApprovalError("approval manifest digest does not match")
    if fields["ticket"] != ticket:
        raise ApprovalError("approval ticket does not match")
    expires = _parse_expiry(fields["expires"])
    if now >= expires:
        raise ApprovalError("approval expired")
    return verify_signature(raw, signature_path, allowed_signers)
