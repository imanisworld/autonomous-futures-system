"""Operator signature for a baseline reset.

The signed record binds the release SHA, the canonical manifest digest, the
ticket, and an expiry. ``ssh-keygen -Y`` checks it in the namespace
``afs-release-approval`` against a fixed allowed_signers file. That file is
not taken from ``--shared-dir`` and is not inside the staged release tree.

No private key is created or stored here. The operator signs on their own
machine and installs only the public key.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

NAMESPACE = "afs-release-approval"
ALLOWED_SIGNERS_PATH = Path("/root/afs-shared/baseline_allowed_signers")
NONCE_LEDGER_PATH = Path("/root/afs-shared/baseline_approval_nonces")
PRIVATE_DIR = Path("/root/afs-shared/approval-private")
INIT_MARKER = "INITIALIZED"
LEDGER_NAME = "ledger"
CLAIMED = "CLAIMED"
RESET_COMMITTED = "RESET_COMMITTED"
RESET_FAILED = "RESET_FAILED"
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
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


def verify_signature(
    message: bytes,
    signature_path: Path,
    signers: bytes,
    private_dir: Path,
    *,
    trust_private: bool = True,
    fstat=os.fstat,
) -> str:
    """Verify against a private mkdtemp copy, then delete that directory."""
    try:
        principals = _principals(signers.decode("utf-8"))
        signature_path.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ApprovalError("approval signature unreadable") from exc
    if not principals:
        raise ApprovalError("allowed_signers empty")
    if trust_private:
        checked = walk_dir(private_dir, fstat=fstat, exact_mode=0o700)
        os.close(checked)
    temp = tempfile.mkdtemp(prefix="afs-signers-", dir=str(private_dir))
    try:
        copy = Path(temp) / "allowed_signers"
        fd = os.open(copy, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            os.write(fd, signers)
            if os.fstat(fd).st_mode & 0o077:
                raise ApprovalError("allowed_signers copy is not private")
        finally:
            os.close(fd)
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
        shutil.rmtree(temp, ignore_errors=True)
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


def _dir_problem(st: object, *, exact_mode: int | None = None, store: bool = False) -> str | None:
    """StrictModes check of one directory fd."""
    mode = getattr(st, "st_mode", None)
    uid = getattr(st, "st_uid", None)
    label = "nonce store" if store else "ancestor"
    if not isinstance(mode, int) or not isinstance(uid, int):
        return f"{label} unreadable"
    if stat.S_ISLNK(mode):
        return f"{label} is a symlink"
    if not stat.S_ISDIR(mode):
        return f"{label} not a directory"
    if mode & 0o022:
        return f"{label} writable by group or other"
    if uid != 0:
        return f"{label} not root-owned"
    if exact_mode is not None and (mode & 0o777) != exact_mode:
        return "nonce store must be mode 0700"
    return None


def walk_dir(path: Path, *, fstat=os.fstat, exact_mode: int | None = None) -> int:
    """Open ``path`` from ``/`` with openat and return its directory fd.

    Every ancestor is fstat'd on the fd just opened. The caller closes the
    fd. A later rename of a path component does not retarget an fd already
    held.
    """
    if not path.is_absolute() or ".." in path.parts:
        raise ApprovalError("ancestor unreadable")
    dirfd = os.open("/", _DIR_FLAGS)
    try:
        problem = _dir_problem(fstat(dirfd))
        if problem:
            raise ApprovalError(problem)
        parts = [part for part in path.parts[1:] if part and part != "/"]
        for index, part in enumerate(parts):
            if part in (".", ".."):
                raise ApprovalError("ancestor is a symlink")
            try:
                nextfd = os.open(part, _DIR_FLAGS, dir_fd=dirfd)
            except OSError as exc:
                if exc.errno == errno.ELOOP:
                    raise ApprovalError("ancestor is a symlink") from exc
                raise ApprovalError("ancestor unreadable") from exc
            os.close(dirfd)
            dirfd = nextfd
            last = index == len(parts) - 1
            problem = _dir_problem(
                fstat(dirfd),
                exact_mode=exact_mode if last else None,
                store=bool(exact_mode) and last,
            )
            if problem:
                raise ApprovalError(problem)
        return dirfd
    except Exception:
        os.close(dirfd)
        raise


def read_signers_walk(path: Path, *, fstat=os.fstat) -> bytes:
    """Read the signers file through the directory fd. No pathname reopen."""
    dirfd = walk_dir(path.parent, fstat=fstat)
    try:
        try:
            filefd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
        except FileNotFoundError as exc:
            raise ApprovalError("allowed_signers missing") from exc
        except OSError as exc:
            raise ApprovalError("allowed_signers unreadable") from exc
        try:
            problem = signer_stat_problem(fstat(filefd))
            if problem:
                raise ApprovalError(problem)
            return _read_fd(filefd)
        finally:
            os.close(filefd)
    finally:
        os.close(dirfd)


def _read_fd(fd: int) -> bytes:
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


def require_store_fd(dirfd: int, *, fstat=os.fstat, trust: bool = True) -> None:
    """Refuse a nonce-store fd that is 0777, group-writable, or not root-owned."""
    st = fstat(dirfd)
    mode = getattr(st, "st_mode", None)
    uid = getattr(st, "st_uid", None)
    if not isinstance(mode, int) or not isinstance(uid, int) or not stat.S_ISDIR(mode):
        raise ApprovalError("nonce store unreadable")
    if mode & 0o022:
        raise ApprovalError("nonce store writable by group or other")
    if trust and uid != 0:
        raise ApprovalError("nonce store not root-owned")
    if trust and (mode & 0o777) != 0o700:
        raise ApprovalError("nonce store must be mode 0700")


def _open_store(path: Path, *, trust: bool, fstat=os.fstat) -> int:
    try:
        if trust:
            dirfd = walk_dir(path, fstat=fstat, exact_mode=0o700)
        else:
            dirfd = os.open(path, _DIR_FLAGS)
    except FileNotFoundError as exc:
        raise ApprovalError("nonce store missing") from exc
    except ApprovalError:
        raise
    except OSError as exc:
        raise ApprovalError("nonce store unreadable") from exc
    try:
        require_store_fd(dirfd, fstat=fstat, trust=trust)
    except Exception:
        os.close(dirfd)
        raise
    return dirfd


def init_nonce_store(path: Path, *, trust: bool = True, fstat=os.fstat) -> None:
    """Create the init marker and an empty ledger in a 0700 store.

    An existing ledger is left untouched. A marker with no ledger is refused
    so a deleted ledger is not replaced with an empty one.
    """
    if not path.is_absolute() or path.is_symlink():
        raise ApprovalError("nonce store unreadable")
    if not path.exists():
        if trust:
            parent = walk_dir(path.parent, fstat=fstat)
        else:
            parent = os.open(path.parent, _DIR_FLAGS)
        try:
            os.mkdir(path.name, 0o700, dir_fd=parent)
            store = os.open(path.name, _DIR_FLAGS, dir_fd=parent)
        finally:
            os.close(parent)
    else:
        store = _open_store(path, trust=trust, fstat=fstat)
    try:
        require_store_fd(store, fstat=fstat, trust=trust)
        try:
            marker = os.open(INIT_MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store)
        except FileNotFoundError:
            marker = None
        else:
            os.close(marker)
        try:
            ledger = os.open(LEDGER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=store)
        except FileNotFoundError:
            ledger = None
        else:
            os.close(ledger)
        if ledger is not None:
            raise ApprovalError("nonce store already exists")
        if marker is not None:
            raise ApprovalError("nonce ledger missing")
        _create_file(store, INIT_MARKER, b"initialized\n")
        _create_file(store, LEDGER_NAME, b"")
        os.fsync(store)
    finally:
        os.close(store)


def _create_file(dirfd: int, name: str, data: bytes) -> None:
    fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_ledger(dirfd: int) -> str:
    try:
        marker = os.open(INIT_MARKER, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
    except OSError as exc:
        raise ApprovalError("nonce store uninitialized") from exc
    else:
        os.close(marker)
    try:
        fd = os.open(LEDGER_NAME, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dirfd)
    except OSError as exc:
        raise ApprovalError("nonce ledger missing") from exc
    try:
        raw = _read_fd(fd)
    finally:
        os.close(fd)
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise ApprovalError("nonce ledger unreadable") from exc


def _nonce_state(text: str, nonce: str) -> str | None:
    state = None
    for line in text.splitlines():
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApprovalError("nonce ledger unreadable") from exc
        if isinstance(row, dict) and row.get("nonce") == nonce:
            state = row.get("state")
    return state if isinstance(state, str) else None


def _append_state(dirfd: int, nonce: str, state: str, ticket: str) -> None:
    if state not in {CLAIMED, RESET_COMMITTED, RESET_FAILED}:
        raise ApprovalError("approval nonce refused")
    line = json.dumps(
        {"nonce": nonce, "state": state, "ticket": ticket},
        separators=(",", ":"),
    ) + "\n"
    fd = os.open(LEDGER_NAME, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW, dir_fd=dirfd)
    try:
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    os.fsync(dirfd)


def _check_nonce(nonce: str) -> None:
    if len(nonce) != 32 or any(char not in "0123456789abcdef" for char in nonce):
        raise ApprovalError("approval nonce refused")


def claim_nonce(
    ledger: Path,
    nonce: str,
    *,
    ticket: str,
    dry_run: bool,
    trust: bool = True,
    fstat=os.fstat,
) -> None:
    """Record CLAIMED. Dry-run checks the ledger and does not append.

    CLAIMED, RESET_COMMITTED, and RESET_FAILED all burn the nonce. A missing
    ledger after init is refused and is not recreated.
    """
    _check_nonce(nonce)
    dirfd = _open_store(ledger, trust=trust, fstat=fstat)
    try:
        state = _nonce_state(_read_ledger(dirfd), nonce)
        if state is not None:
            raise ApprovalError("approval nonce burned")
        if dry_run:
            return
        _append_state(dirfd, nonce, CLAIMED, ticket)
    finally:
        os.close(dirfd)


def finish_nonce(
    ledger: Path,
    nonce: str,
    outcome: str,
    ticket: str,
    *,
    trust: bool = True,
) -> None:
    """Append RESET_COMMITTED or RESET_FAILED after CLAIMED."""
    if outcome not in {RESET_COMMITTED, RESET_FAILED}:
        raise ApprovalError("approval nonce refused")
    dirfd = _open_store(ledger, trust=trust)
    try:
        if _nonce_state(_read_ledger(dirfd), nonce) != CLAIMED:
            raise ApprovalError("approval nonce burned")
        _append_state(dirfd, nonce, outcome, ticket)
    finally:
        os.close(dirfd)


def _parse_expiry(value: str) -> datetime:
    if len(value) != 20 or not value.endswith("Z"):
        raise ApprovalError("approval expiry refused")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ApprovalError("approval expiry refused") from exc
    return parsed.replace(tzinfo=timezone.utc)


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
        signers = read_signers_walk(allowed_signers, fstat=fstat)
    elif signer_stat is None:
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
        raw,
        signature_path,
        signers,
        private_dir or PRIVATE_DIR,
        trust_private=trust_ancestors,
        fstat=fstat,
    )
    claim_nonce(
        nonce_ledger or NONCE_LEDGER_PATH,
        fields["nonce"],
        ticket=ticket,
        dry_run=dry_run,
        trust=trust_ancestors,
        fstat=fstat,
    )
    return principal
