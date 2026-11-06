"""Operator signature checks. Keys exist only in the test temporary directory."""
from __future__ import annotations

import json
import multiprocessing
import os
import stat
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops.release_approval import (
    ALLOWED_SIGNERS_PATH,
    ApprovalError,
    canonical_approval,
    claim_nonce,
    finish_nonce,
    init_nonce_store,
    read_signers_walk,
    require_allowed_signers,
    signer_stat_problem,
    verify_reset_approval,
)
from tests.test_baseline_reset import (
    FP, HOST, SHA, TICKET, WHEN, _layout, _nonce_store, _reset, _sign,
)

NAMESPACE = "afs-release-approval"


class _Stat:
    def __init__(self, mode: int, uid: int):
        self.st_mode = mode
        self.st_uid = uid


def _verify(approval, signature, allowed, **overrides):
    args = dict(
        approval_path=approval,
        signature_path=signature,
        allowed_signers=allowed,
        target_sha=SHA,
        manifest_digest=FP,
        ticket=TICKET,
        now=WHEN,
        signer_stat=_Stat(stat.S_IFREG | 0o444, 0),
        nonce_ledger=_nonce_store(),
        expected_host_id=HOST,
        private_dir=Path(tempfile.mkdtemp(prefix="afs-private-")),
        trust_ancestors=False,
    )
    args.update(overrides)
    return verify_reset_approval(**args)


def test_signed_approval_names_the_operator_key():
    approval, signature, allowed = _sign()
    assert _verify(approval, signature, allowed) == "imani@mac"


def test_bad_signature_is_refused():
    approval, signature, allowed = _sign()
    signature.write_text(signature.read_text().replace("A", "B", 1))
    with pytest.raises(Exception, match="approval signature refused"):
        _verify(approval, signature, allowed)


def test_wrong_signer_is_refused(tmp_path):
    approval, signature, _allowed = _sign()
    other = tmp_path / "other"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(other), "-N", "", "-q"],
        check=True,
    )
    kind, material, *_rest = other.with_suffix(".pub").read_text().split()
    allowed = tmp_path / "allowed_signers"
    allowed.write_text(f'other@mac namespaces="{NAMESPACE}" {kind} {material}\n')
    with pytest.raises(Exception, match="approval signature refused"):
        _verify(approval, signature, allowed)


@pytest.mark.parametrize("field", ["target_sha", "manifest_digest", "ticket"])
def test_tampered_binding_is_refused(field):
    approval, signature, allowed = _sign()
    text = approval.read_text()
    if field == "target_sha":
        text = text.replace(SHA, "b" * 40)
    elif field == "manifest_digest":
        text = text.replace(FP, "e" * 64)
    else:
        text = text.replace(f"ticket={TICKET}", "ticket=AFS-9999")
    approval.write_text(text)
    with pytest.raises(Exception, match="approval"):
        _verify(approval, signature, allowed)


def test_expired_approval_is_refused_even_when_the_signature_is_valid():
    approval, signature, allowed = _sign(
        issued_at="2000-01-01T00:00:00Z", expires="2000-01-01T12:00:00Z",
    )
    with pytest.raises(Exception, match="approval expired"):
        _verify(approval, signature, allowed)


def test_cli_arguments_must_match_the_signed_record():
    approval, signature, allowed = _sign()
    with pytest.raises(Exception, match="manifest digest does not match"):
        _verify(approval, signature, allowed, manifest_digest="e" * 64)
    with pytest.raises(Exception, match="target sha does not match"):
        _verify(approval, signature, allowed, target_sha="b" * 40)
    with pytest.raises(Exception, match="ticket does not match"):
        _verify(approval, signature, allowed, ticket="AFS-1")


def test_allowed_signers_stat_checks_are_independent():
    assert signer_stat_problem(_Stat(stat.S_IFREG | 0o444, 1000)) == "allowed_signers not root-owned"
    assert signer_stat_problem(_Stat(stat.S_IFREG | 0o664, 0)) == "allowed_signers writable by group or other"
    assert signer_stat_problem(_Stat(stat.S_IFREG | 0o606, 0)) == "allowed_signers writable by group or other"
    assert signer_stat_problem(_Stat(stat.S_IFREG | 0o444, 0)) is None


def test_allowed_signers_file_must_be_present_and_not_a_symlink(tmp_path):
    missing = tmp_path / "missing"
    with pytest.raises(Exception, match="allowed_signers missing"):
        require_allowed_signers(missing)
    target = tmp_path / "real"
    target.write_text("imani@mac ssh-ed25519 AAAA\n")
    link = tmp_path / "link"
    link.symlink_to(target)
    with pytest.raises(Exception, match="allowed_signers is a symlink"):
        require_allowed_signers(link)
    owned = tmp_path / "local"
    owned.write_text("imani@mac ssh-ed25519 AAAA\n")
    owned.chmod(0o444)
    with pytest.raises(Exception, match="allowed_signers not root-owned"):
        require_allowed_signers(owned)


def test_production_signers_path_is_fixed_outside_shared_dir():
    assert ALLOWED_SIGNERS_PATH == Path("/root/afs-shared/baseline_allowed_signers")
    assert "baseline_allowed_signers" not in "shared-dir"


def test_dry_run_refuses_a_bad_signature_and_writes_nothing(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)
    before = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    approval, _signature, allowed = _sign()
    bogus = tmp_path / "bad.sig"
    bogus.write_text("not-a-signature\n")
    before.add("bad.sig")
    with pytest.raises(Exception, match="approval signature refused"):
        _reset(
            shared,
            log_dir,
            dry_run=True,
            approval_path=approval,
            signature_path=bogus,
            allowed_signers=allowed,
        )
    after = {path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_shared_dir_rejects_symlink_and_relative_paths(tmp_path):
    from ops.baseline_reset import Refusal, require_trusted_shared_dir

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(Refusal, match="symlink"):
        require_trusted_shared_dir(link)
    with pytest.raises(Refusal, match="absolute"):
        require_trusted_shared_dir(Path("shared"))
    with pytest.raises(Refusal, match="\\.\\."):
        require_trusted_shared_dir(tmp_path / ".." / tmp_path.name)


def test_signers_read_uses_the_opened_inode_after_path_swap(tmp_path, monkeypatch):
    """Replacing the path after open must not change the bytes ssh-keygen sees.

    A second open of the path would observe the swapped file. Deleting the
    single-fd read does that, and this assertion fails.
    """
    from ops.release_approval import read_signers_fd

    path = tmp_path / "signers"
    path.write_text("GOOD\n")
    evil = tmp_path / "evil"
    evil.write_text("EVIL\n")
    real_open = os.open

    def wrapped(opened, flags, *args, **kwargs):
        fd = real_open(opened, flags, *args, **kwargs)
        if Path(opened) == path and path.exists():
            path.unlink()
            evil.rename(path)
        return fd

    monkeypatch.setattr(os, "open", wrapped)
    data = read_signers_fd(path, fstat=lambda _fd: _Stat(stat.S_IFREG | 0o444, 0))
    assert data == b"GOOD\n"
    assert path.read_text() == "EVIL\n"


def test_shared_dir_rejects_a_world_writable_parent(tmp_path):
    from ops.baseline_reset import Refusal, require_trusted_shared_dir

    parent = tmp_path / "open"
    parent.mkdir()
    parent.chmod(0o777)
    child = parent / "shared"
    child.mkdir()
    with pytest.raises(Refusal, match="writable"):
        require_trusted_shared_dir(child)


def test_replay_is_refused_and_dry_run_does_not_consume(tmp_path):
    approval, signature, allowed = _sign()
    ledger = tmp_path / "ledger"
    init_nonce_store(ledger, trust=False)
    common = dict(nonce_ledger=ledger, trust_ancestors=False)
    assert _verify(approval, signature, allowed, dry_run=True, **common)
    assert "CLAIMED" not in (ledger / "ledger").read_text()
    assert _verify(approval, signature, allowed, dry_run=False, **common)
    assert "CLAIMED" in (ledger / "ledger").read_text()
    with pytest.raises(Exception, match="approval nonce burned"):
        _verify(approval, signature, allowed, dry_run=True, **common)


def test_parent_swap_to_non_root_ancestor_is_refused(tmp_path, monkeypatch):
    """A parent replaced by a non-root directory after it is opened must be refused.

    The walk fstats the fd from openat. Faking every other directory as
    root-owned makes the swapped parent the only real non-root directory.
    """
    base = tmp_path / "base"
    base.mkdir()
    real = base / "parent"
    real.mkdir()
    (real / "signers").write_text("GOOD\n")
    planted = base / "planted"
    planted.mkdir()
    (planted / "signers").write_text("EVIL\n")
    planted_ino = planted.stat().st_ino
    swapped = {"done": False}
    real_fstat = os.fstat
    real_open = os.open

    def fstat(fd):
        st = real_fstat(fd)
        if st.st_ino == planted_ino:
            return st
        if stat.S_ISDIR(st.st_mode):
            return _Stat(stat.S_IFDIR | 0o755, 0)
        return _Stat(stat.S_IFREG | 0o444, 0)

    def wrapped(opened, flags, *args, **kwargs):
        fd = real_open(opened, flags, *args, **kwargs)
        if not swapped["done"]:
            try:
                link = os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                link = ""
            if link == str(base):
                swapped["done"] = True
                real.rename(base / "real-saved")
                planted.rename(base / "parent")
        return fd

    monkeypatch.setattr(os, "open", wrapped)
    with pytest.raises(ApprovalError, match="not root-owned"):
        read_signers_walk(base / "parent" / "signers", fstat=fstat)
    assert swapped["done"] is True


def test_mode_0777_nonce_dir_is_refused(tmp_path):
    store = tmp_path / "nonces"
    store.mkdir()
    store.chmod(0o777)
    with pytest.raises(ApprovalError, match="nonce store writable"):
        claim_nonce(store, "cd" * 16, ticket=TICKET, dry_run=True, trust=False)


def test_deleted_ledger_is_not_treated_as_empty(tmp_path):
    store = tmp_path / "nonces"
    init_nonce_store(store, trust=False)
    (store / "ledger").unlink()
    with pytest.raises(ApprovalError, match="nonce ledger missing"):
        claim_nonce(store, "cd" * 16, ticket=TICKET, dry_run=True, trust=False)
    assert not (store / "ledger").exists()


def test_claimed_nonce_without_outcome_stays_burned(tmp_path):
    store = tmp_path / "nonces"
    init_nonce_store(store, trust=False)
    claim_nonce(store, "cd" * 16, ticket=TICKET, dry_run=False, trust=False)
    text = (store / "ledger").read_text()
    assert "CLAIMED" in text
    assert "RESET_COMMITTED" not in text
    with pytest.raises(ApprovalError, match="approval nonce burned"):
        claim_nonce(store, "cd" * 16, ticket=TICKET, dry_run=True, trust=False)


def _claim_worker(store: str, nonce: str, ticket: str, queue) -> None:
    try:
        claim_nonce(Path(store), nonce, ticket=ticket, dry_run=False, trust=False)
    except ApprovalError as exc:
        queue.put(str(exc))
    else:
        queue.put("ok")


def _finish_worker(store: str, nonce: str, ticket: str, queue) -> None:
    try:
        finish_nonce(Path(store), nonce, "RESET_COMMITTED", ticket, trust=False)
    except ApprovalError as exc:
        queue.put(str(exc))
    else:
        queue.put("ok")


def _run_workers(target, store: Path, nonce: str, ticket: str, count: int) -> list[str]:
    ctx = multiprocessing.get_context("spawn")
    queue = ctx.Queue()
    procs = [
        ctx.Process(target=target, args=(str(store), nonce, ticket, queue))
        for _ in range(count)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(30)
        assert proc.exitcode == 0
    return [queue.get(timeout=5) for _ in range(count)]


def test_concurrent_claims_have_one_winner(tmp_path):
    """Eight processes, 32 trials: one CLAIMED and one terminal record."""
    store = tmp_path / "nonces"
    init_nonce_store(store, trust=False)
    workers = 8
    for trial in range(32):
        nonce = f"{trial:032x}"
        claimed = _run_workers(_claim_worker, store, nonce, TICKET, workers)
        assert claimed.count("ok") == 1
        assert claimed.count("approval nonce burned") == workers - 1
        finished = _run_workers(_finish_worker, store, nonce, TICKET, workers)
        assert finished.count("ok") == 1
        assert finished.count("approval nonce burned") == workers - 1
        rows = [
            json.loads(line)
            for line in (store / "ledger").read_text().splitlines()
            if line
        ]
        mine = [row for row in rows if row["nonce"] == nonce]
        assert mine == [
            {"nonce": nonce, "state": "CLAIMED", "ticket": TICKET},
            {"nonce": nonce, "state": "RESET_COMMITTED", "ticket": TICKET},
        ]


def test_cross_host_approval_is_refused():
    approval, signature, allowed = _sign(host_id="a" * 64)
    with pytest.raises(Exception, match="approval host mismatch"):
        _verify(approval, signature, allowed)


def test_expiry_more_than_24h_after_issue_is_refused():
    issued = WHEN
    expires = (issued + timedelta(hours=24, seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    approval, signature, allowed = _sign(
        issued_at=issued.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires=expires,
    )
    with pytest.raises(Exception, match="approval lifetime exceeds 24h"):
        _verify(approval, signature, allowed)


def test_canonical_bytes_round_trip():
    raw = canonical_approval(
        target_sha=SHA, manifest_digest=FP, ticket=TICKET, nonce="cd" * 16,
        host_id=HOST, issued_at="2026-11-06T18:00:00Z", expires="2026-11-07T12:00:00Z",
    )
    assert raw.endswith(b"\n")
    assert b"manifest_digest=" + FP.encode() in raw
    assert datetime(2099, 1, 1, tzinfo=timezone.utc)
