"""Operator signature checks. Keys exist only in the test temporary directory."""
from __future__ import annotations

import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ops.release_approval import (
    ALLOWED_SIGNERS_PATH,
    canonical_approval,
    require_allowed_signers,
    signer_stat_problem,
    verify_reset_approval,
)
from tests.test_baseline_reset import FP, SHA, TICKET, WHEN, _layout, _reset, _sign

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
    approval, signature, allowed = _sign(expires="2000-01-01T00:00:00Z")
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


def test_canonical_bytes_round_trip():
    raw = canonical_approval(
        target_sha=SHA, manifest_digest=FP, ticket=TICKET, expires="2099-01-01T00:00:00Z",
    )
    assert raw.endswith(b"\n")
    assert b"manifest_digest=" + FP.encode() in raw
    assert datetime(2099, 1, 1, tzinfo=timezone.utc)
