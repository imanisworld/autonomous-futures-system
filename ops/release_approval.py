"""Operator signature for a baseline reset.

The signed record binds the release SHA, the canonical manifest digest, the
ticket, and an expiry. ``ssh-keygen -Y`` checks it in the namespace
``afs-release-approval`` against a fixed allowed_signers file. That file is
not taken from ``--shared-dir`` and is not inside the staged release tree.

No private key is created or stored here. The operator signs on their own
machine and installs only the public key.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

NAMESPACE = "afs-release-approval"
ALLOWED_SIGNERS_PATH = Path("/root/afs-shared/baseline_allowed_signers")
NONCE_LEDGER_PATH = Path("/root/afs-shared/baseline_approval_nonces")
PRIVATE_DIR = Path("/root/afs-shared/approval-private")
MAX_LIFETIME = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=5)
_FIELDS = (
    "target_sha",
    "manifest_digest",
    "ticket",
    "nonce",
    "host_id",
    "issued_at",
    "expires",
)


class ApprovalError(Exception):
    """The approval is missing, unsafe, or not a valid operator signature."""


def local_host_id() -> str:
    """sha256 of /etc/machine-id, or of the hostname when that file is absent."""
    machine = Path("/etc/machine-id")
    try:
        raw = machine.read_text(encoding="utf-8").strip()
    except OSError:
        raw = os.uname().nodename
    if not raw:
        raw = os.uname().nodename
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def canonical_approval(
    *,
    target_sha: str,
    manifest_digest: str,
    ticket: str,
    nonce: str,
    host_id: str,
    issued_at: str,
    expires: str,
) -> bytes:
    """Exact bytes the operator signs. Field order is part of the signature."""
    lines = [
        "afs-release-approval",
        "version=1",
        f"target_sha={target_sha}",
        f"manifest_digest={manifest_digest}",
        f"ticket={ticket}",
        f"nonce={nonce}",
        f"host_id={host_id}",
        f"issued_at={issued_at}",
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
    if len(lines) != 9 or lines[0] != "afs-release-approval" or lines[1] != "version=1":
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
        nonce=fields["nonce"],
        host_id=fields["host_id"],
        issued_at=fields["issued_at"],
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


def read_signers_fd(path: Path, fstat=os.fstat) -> bytes:
    """Read the signers file from one O_NOFOLLOW fd.

    A later replacement of the path does not change the bytes returned from
    this descriptor.
    """
    flags = os.O_RDONLY
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow:
        flags |= nofollow
    try:
        fd = os.open(path, flags)
    except FileNotFoundError as exc:
        raise ApprovalError("allowed_signers missing") from exc
    except OSError as exc:
        raise ApprovalError("allowed_signers unreadable") from exc
    try:
        problem = signer_stat_problem(fstat(fd))
        if problem:
            raise ApprovalError(problem)
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(fd, 65536)
            if not block:
                break
            total += len(block)
            if total > 1_000_000:
                raise ApprovalError("allowed_signers unreadable")
            chunks.append(block)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _private_copy(data: bytes, private_dir: Path) -> Path:
    private_dir.mkdir(parents=True, exist_ok=True)
    copy = private_dir / f"allowed_signers.{os.getpid()}"
    fd = os.open(str(copy), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    return copy


def verify_signature(message: bytes, signature_path: Path, signers: bytes, private_dir: Path) -> str:
    """Verify against a private copy of the signers bytes, not the original path."""
    try:
        principals = _principals(signers.decode("utf-8"))
        signature_path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ApprovalError("approval signature unreadable") from exc
    if not principals:
        raise ApprovalError("allowed_signers empty")
    copy = _private_copy(signers, private_dir)
    try:
        if copy.stat().st_mode & 0o077:
            raise ApprovalError("allowed_signers copy is not private")
        for principal in principals:
            result = subprocess.run(
                [
                    "ssh-keygen", "-Y", "verify",
                    "-f", str(copy),
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
    finally:
        copy.unlink(missing_ok=True)
    raise ApprovalError("approval signature refused")


def directory_ancestor_problem(st: object, *, require_root: bool) -> str | None:
    """Refuse a directory that is group/other-writable, and optionally not root-owned."""
    mode = getattr(st, "st_mode", None)
    uid = getattr(st, "st_uid", None)
    if not isinstance(mode, int) or not isinstance(uid, int):
        return "ancestor unreadable"
    if mode & 0o022:
        return "ancestor writable by group or other"
    if require_root and uid != 0:
        return "ancestor not root-owned"
    return None


def consume_nonce(nonce: str, ledger: Path, *, dry_run: bool) -> None:
    """Refuse a reused nonce. Dry-run looks but does not record it."""
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ApprovalError("approval nonce refused")
    slot = ledger / nonce
    if slot.exists():
        raise ApprovalError("approval nonce reused")
    if dry_run:
        return
    ledger.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(slot), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ApprovalError("approval nonce reused") from exc
    else:
        os.close(fd)


def _parse_expiry(value: str) -> datetime:
    if len(value) != 20 or not value.endswith("Z"):
        raise ApprovalError("approval expiry refused")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ApprovalError("approval expiry refused") from exc
    return parsed.replace(tzinfo=timezone.utc)


def require_writable_ancestors(path: Path) -> None:
    """Refuse when any existing directory on ``path`` is group- or other-writable."""
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if not cursor.exists():
            continue
        if cursor.is_symlink():
            raise ApprovalError("ancestor is a symlink")
        if cursor.is_dir() and cursor.stat().st_mode & 0o022:
            raise ApprovalError("ancestor writable by group or other")


def _check_lifetime(issued_at: datetime, expires: datetime, now: datetime) -> None:
    if issued_at > now + CLOCK_SKEW:
        raise ApprovalError("approval issued_at is in the future")
    if expires <= issued_at or expires - issued_at > MAX_LIFETIME:
        raise ApprovalError("approval lifetime exceeds 24h")
    if now >= expires:
        raise ApprovalError("approval expired")


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
    nonce_ledger: Path | None = None,
    expected_host_id: str | None = None,
    private_dir: Path | None = None,
    dry_run: bool = False,
    trust_ancestors: bool = True,
    fstat=os.fstat,
) -> str:
    """Verify the signed record and return the signer principal.

    ``signer_stat`` and ``fstat`` are test seams. The command line does not
    accept them. The signature check uses a private copy of the bytes read
    from the signers fd, not the original path.
    """
    if now.tzinfo is None:
        raise ApprovalError("approval expiry refused")
    if trust_ancestors:
        require_writable_ancestors(allowed_signers.parent)
    if signer_stat is None:
        signers = read_signers_fd(allowed_signers, fstat=fstat)
    else:
        require_allowed_signers(allowed_signers, signer_stat)
        signers = read_signers_fd(allowed_signers, fstat=lambda _fd: signer_stat)
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
    host = expected_host_id if expected_host_id is not None else local_host_id()
    if fields["host_id"] != host:
        raise ApprovalError("approval host mismatch")
    issued_at = _parse_expiry(fields["issued_at"])
    expires = _parse_expiry(fields["expires"])
    _check_lifetime(issued_at, expires, now)
    principal = verify_signature(
        raw, signature_path, signers, private_dir or PRIVATE_DIR,
    )
    consume_nonce(fields["nonce"], nonce_ledger or NONCE_LEDGER_PATH, dry_run=dry_run)
    return principal
