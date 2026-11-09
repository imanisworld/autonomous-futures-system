"""Restricted grok-ops deploy lane.

The lane is a forced-command user plus exact-path sudoers. These tests are
the contract: injection is refused, GO tokens are one-time and SHA-bound,
and every hard refusal fails closed without printing .env values.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest

from ops.baseline_reset import RESET_CLI_FLAGS, main as baseline_reset_main
from ops.grok_ops.actions import (
    APPROVAL_FILE,
    SIGNATURE_FILE,
    ActionContext,
    ActionError,
    _invoke,
    _production_runner,
    _runner_env,
    baseline_reset_argv,
    production_context,
    prove_target_release,
    require_same_file,
    run_verb,
)
from ops.grok_ops.paths import (
    TOKEN_DIR,
    OwnershipError,
    PinnedRelease,
    _open_from,
    assert_directory,
    hold_directory,
    open_verified,
    pin_release,
)
from ops.grok_ops.allowlist import CommandRejected, parse_command
from ops.grok_ops.auditlog import AuditLog
from ops.grok_ops.authorize import main as authorize_main
from ops.grok_ops.dispatcher import record_refusal_main
from ops.grok_ops.gates import FlatSnapshot, evaluate_gates
from ops.grok_ops.keys import (
    DISPATCHER_PATH,
    KeyGuardError,
    render_authorized_keys_line,
    validate_authorized_key_line,
)
from ops.grok_ops.self_test import run_self_test
from ops.grok_ops.sudoers import (
    SUDO_PATHS,
    render_sudoers,
    validate_sudoers_text,
    visudo_check,
)
from ops.grok_ops.tokens import TOKEN_TTL, TokenError, TokenStore

SHA_A = "a" * 40
SHA_B = "b" * 40
FP = "ab" * 32
PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGrokOpsTestKeyMaterialOnly grok-ops"
ROOT = Path(__file__).resolve().parents[1]


def _seal_target(releases: Path, shared: Path, sha: str, source: Path) -> None:
    target = releases / sha
    for relative in (
        "scripts/atomic_release.sh",
        "scripts/deploy_lock.sh",
        "scripts/ops/baseline_reset.sh",
    ):
        src = source / relative
        dest = target / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file() and not src.is_symlink():
            dest.write_bytes(src.read_bytes())
        else:
            dest.write_text("#!/bin/sh\nexit 0\n")
        dest.chmod(0o755)
    manifest = {
        "repo": {"commit": sha},
        "fingerprint_sha256": FP,
    }
    (target / "release_manifest.json").write_text(json.dumps(manifest))
    (target / "release_manifest.json").chmod(0o644)
    complete = shared / "release-complete"
    complete.mkdir(parents=True, exist_ok=True)
    (complete / sha).write_text(FP + "\n")
    (complete / sha).chmod(0o644)
    for path in (releases, target, target / "scripts", target / "scripts" / "ops", shared, complete):
        path.chmod(0o755)
    _init_git(target)


def _init_git(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True, capture_output=True)


def _flat(**overrides) -> FlatSnapshot:
    base = dict(
        positions_readable=True,
        open_positions=0,
        orders_readable=True,
        working_orders=0,
    )
    base.update(overrides)
    return FlatSnapshot(**base)


def _quiet_env() -> str:
    return "LIVE_TRADING_ENABLED=false\nTRADOVATE_ENV=demo\n"


def _variants_inactive() -> str:
    return "VARIANTS_START = None\n"


def test_allowlist_accepts_each_verb():
    assert parse_command("preflight").verb == "preflight"
    assert parse_command("backup").verb == "backup"
    assert parse_command("baseline-reset-dry-run").verb == "baseline-reset-dry-run"
    assert parse_command("baseline-reset").verb == "baseline-reset"
    assert parse_command(f"promote {SHA_A}").sha == SHA_A
    assert parse_command("verify").verb == "verify"
    assert parse_command("rollback rel-1").release == "rel-1"
    assert parse_command("status").verb == "status"


@pytest.mark.parametrize(
    "raw",
    [
        "promote",
        f"promote {SHA_A} extra",
        "promote NOTASHA",
        "rollback",
        "rollback ../etc",
        "rollback -rf",
        "status now",
        "PREFLIGHT",
        "verify " + SHA_A,
        "",
        "   ",
    ],
)
def test_allowlist_rejects_malformed_verbs(raw):
    with pytest.raises(CommandRejected):
        parse_command(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "status; id",
        "status && id",
        "status `id`",
        "status $(id)",
        "promote $(echo " + SHA_A + ")",
        "status\nid",
        "status | id",
        "status > /tmp/x",
    ],
)
def test_dispatcher_rejects_shell_injection(raw):
    with pytest.raises(CommandRejected) as exc:
        parse_command(raw)
    assert exc.value.reason == "injection"


def test_unknown_verb_is_refused():
    with pytest.raises(CommandRejected) as exc:
        parse_command("shell")
    assert exc.value.reason == "unknown-verb"


def test_sudoers_is_exact_paths_and_passes_visudo():
    text = render_sudoers()
    validate_sudoers_text(text)
    visudo_check(text)
    for path in SUDO_PATHS:
        assert path in text
    assert "*" not in text
    assert "?" not in text
    assert "/bin/bash" not in text
    assert "/bin/sh" not in text
    assert "afs-ops-authorize" not in text


def test_sudoers_lint_rejects_wildcard_and_shell():
    text = render_sudoers()
    with pytest.raises(ValueError):
        validate_sudoers_text(text.replace(SUDO_PATHS[-1], SUDO_PATHS[-1] + " *"))
    with pytest.raises(ValueError):
        validate_sudoers_text(text + "grok-ops ALL=(root) NOPASSWD: /bin/bash\n")


def test_authorized_key_has_forced_command_restrictions():
    line = render_authorized_keys_line(PUBKEY)
    validate_authorized_key_line(line)
    for option in (
        "command=",
        "no-pty",
        "no-port-forwarding",
        "no-agent-forwarding",
        "no-X11-forwarding",
        "restrict",
    ):
        assert option in line
    assert DISPATCHER_PATH in line


@pytest.mark.parametrize(
    "mutate",
    [
        lambda line: line.replace(",no-pty", ""),
        lambda line: line.replace(",no-port-forwarding", ""),
        lambda line: line.replace(",no-agent-forwarding", ""),
        lambda line: line.replace(",no-X11-forwarding", ""),
        lambda line: line.replace("restrict,", ""),
        lambda line: line.replace(DISPATCHER_PATH, "/bin/bash"),
        lambda line: line.replace("command=", "command-disabled="),
    ],
)
def test_key_guard_is_mutation_sensitive(mutate):
    line = render_authorized_keys_line(PUBKEY)
    validate_authorized_key_line(line)
    with pytest.raises(KeyGuardError):
        validate_authorized_key_line(mutate(line))


def test_token_binds_sha_expires_and_is_single_use(tmp_path):
    start = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    store = TokenStore(tmp_path, clock=lambda: clock["now"])
    token_id = store.authorize(SHA_A, "AFS-0509", FP)
    record = store.consume(token_id, SHA_A)
    assert record["sha"] == SHA_A
    assert record["ledger_id"] == "AFS-0509"
    with pytest.raises(TokenError) as reused:
        store.consume(token_id, SHA_A)
    assert reused.value.reason == "reused"


def test_token_expiry_and_sha_mismatch(tmp_path):
    start = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    store = TokenStore(tmp_path, clock=lambda: clock["now"])
    token_id = store.authorize(SHA_A, "AFS-0509", FP)
    with pytest.raises(TokenError) as mismatch:
        store.consume(token_id, SHA_B)
    assert mismatch.value.reason == "mismatch"
    assert (tmp_path / f"{token_id}.json").is_file()
    clock["now"] = start + TOKEN_TTL + timedelta(seconds=1)
    with pytest.raises(TokenError) as expired:
        store.consume(token_id, SHA_A)
    assert expired.value.reason == "expired"


def test_authorize_cli_requires_root_and_exact_sha(tmp_path, capsys):
    code = authorize_main(["not-a-sha", "AFS-1", FP], euid=0, store_root=tmp_path)
    assert code == 2
    code = authorize_main([SHA_A, "AFS-1", FP], euid=1000, store_root=tmp_path)
    assert code == 1
    captured = capsys.readouterr()
    assert "root" in captured.err
    code = authorize_main([SHA_A, "AFS-1", "not-a-digest"], euid=0, store_root=tmp_path)
    assert code == 2
    code = authorize_main([SHA_A, "AFS-1", FP], euid=0, store_root=tmp_path)
    assert code == 0
    out = capsys.readouterr().out.strip()
    record = TokenStore(tmp_path).consume(out, SHA_A)
    assert record["ledger_id"] == "AFS-1"
    assert record["release_fingerprint"] == FP


def _ctx(tmp_path: Path, runner, flat=None, env_text=None, variants=None, lock=False):
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = tmp_path / "shared.env"
    env.write_text(env_text if env_text is not None else _quiet_env())
    variants_file = tmp_path / "trend_variants_shadow.py"
    variants_file.write_text(variants if variants is not None else _variants_inactive())
    lock_dir = tmp_path / "deploy.lock"
    if lock:
        lock_dir.mkdir()
    previous = tmp_path / "current.previous"
    previous.write_text(str(tmp_path / "releases" / "rel-1") + "\n")
    (tmp_path / "releases" / "rel-1").mkdir(parents=True, exist_ok=True)
    release_env = tmp_path / "release.env"
    release_env.write_text("AFS_BOX=box.example\nLOG_DIR=/var/log/afs\nPROOF_BACKUP_DIR=/var/backups/afs\n")
    repo = tmp_path / "repo"
    repo.mkdir()
    baseline = repo / "scripts" / "ops"
    baseline.mkdir(parents=True)
    script = baseline / "baseline_reset.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    atomic = repo / "scripts" / "atomic_release.sh"
    atomic.write_text("#!/bin/sh\nexit 0\n")
    atomic.chmod(0o755)
    deploy_lock = repo / "scripts" / "deploy_lock.sh"
    deploy_lock.write_text("#!/bin/sh\nexit 0\n")
    deploy_lock.chmod(0o755)
    calls = []

    def _runner(argv, env):
        calls.append((argv, env))
        return runner(argv, env)

    fd = os.open(repo, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    held = os.fstat(fd)
    pin = PinnedRelease(
        link="",
        real=str(repo),
        prefix=str(repo),
        dev=held.st_dev,
        ino=held.st_ino,
        dirfd=fd,
        uid=os.getuid(),
    )
    releases = tmp_path / "afs-releases"
    shared = tmp_path / "afs-shared"
    _init_git(repo)
    _seal_target(releases, shared, SHA_A, repo)
    ctx = ActionContext(
        repo_root=repo,
        token_store=TokenStore(tmp_path / "tokens"),
        audit=AuditLog(tmp_path / "audit.log"),
        env_file=env,
        lock_dir=lock_dir,
        variants_file=variants_file,
        previous_release_file=previous,
        atomic_release=atomic,
        release_env=release_env,
        flat_reader=lambda: flat if flat is not None else _flat(),
        runner=_runner,
        actor="grok-ops",
        git_ancestor=lambda sha: sha == SHA_A,
        expected_uid=os.getuid(),
        release_pin=pin,
        releases_root=str(releases),
        shared_root=str(shared),
        fstat=_fstat_ancestors(tmp_path, os.getuid()),
    )
    ctx.calls = calls
    return ctx


def test_every_hard_refusal_blocks_promote_without_printing_env(tmp_path):
    secret = "super-secret-env-value"
    cases = [
        ("LIVE_TRADING_ENABLED=true\nTRADOVATE_ENV=demo\nWEBHOOK_SECRET=" + secret + "\n", None, False, "posture-live"),
        ("LIVE_TRADING_ENABLED=false\nTRADOVATE_ENV=live\nWEBHOOK_SECRET=" + secret + "\n", None, False, "posture-env"),
        (_quiet_env(), _flat(open_positions=1), False, "position-open"),
        (_quiet_env(), _flat(working_orders=2), False, "order-open"),
        (_quiet_env(), _flat(positions_readable=False), False, "flat-unreadable"),
        (_quiet_env(), _flat(orders_readable=False), False, "flat-unreadable"),
        (_quiet_env(), None, True, "deploy-lock"),
        (_quiet_env() + "VARIANTS_START=2026-11-06\n", None, False, "shadow-active"),
    ]
    for index, (env_text, flat, lock, code) in enumerate(cases):
        variants = "VARIANTS_START = datetime(2026, 11, 6)\n" if code == "shadow-active" else None
        if code == "shadow-active":
            env_text = _quiet_env()
        ctx = _ctx(tmp_path / f"{index}-{code}", lambda argv, env: 0, flat=flat, env_text=env_text, variants=variants, lock=lock)
        token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
        parsed = parse_command(f"promote {SHA_A}")
        rc = run_verb(parsed, ctx, token_id=token)
        assert rc != 0
        blob = ctx.audit.path.read_text()
        assert secret not in blob
        assert code in blob
        assert ctx.calls == []
        # Token stays usable after a refusal that happens before consume,
        # except we still require the file to exist.
        assert (ctx.token_store.root / f"{token}.json").is_file()


def test_missing_flat_reader_fails_closed(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)

    def boom():
        raise OSError("broker down")

    ctx.flat_reader = boom
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc != 0
    assert "flat-unreadable" in ctx.audit.path.read_text()
    assert ctx.calls == []


def test_promote_consumes_token_and_requires_ancestor(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    token = ctx.token_store.authorize(SHA_A, "AFS-9", FP)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc == 0
    assert ctx.calls[0][0][1:] == ["promote", SHA_A]
    assert ctx.calls[0][0][0].endswith(f"/{SHA_A}/scripts/atomic_release.sh")
    assert "--force-lock" not in ctx.calls[0][0]
    assert "AFS_BOX" in ctx.calls[0][1]
    with pytest.raises(TokenError) as reused:
        ctx.token_store.consume(token, SHA_A)
    assert reused.value.reason == "reused"
    other = ctx.token_store.authorize(SHA_B, "AFS-9", FP)
    rc = run_verb(parse_command(f"promote {SHA_B}"), ctx, token_id=other)
    assert rc != 0
    assert "ancestor" in ctx.audit.path.read_text() or "record-mismatch" in ctx.audit.path.read_text()
    assert not any(call[0][2:3] == [SHA_B] for call in ctx.calls if len(call[0]) > 2)


def test_env_file_is_not_modified_or_echoed(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    secret = "webhook-secret-should-stay-unread"
    ctx.env_file.write_text(_quiet_env() + f"WEBHOOK_SECRET={secret}\n")
    original = ctx.env_file.read_bytes()
    token = ctx.token_store.authorize(SHA_A, "AFS-9", FP)
    run_verb(parse_command("status"), ctx)
    run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert ctx.env_file.read_bytes() == original
    logged = ctx.audit.path.read_text()
    assert secret not in logged
    assert "LIVE_TRADING_ENABLED=" not in logged
    assert "TRADOVATE_ENV=" not in logged


def test_baseline_reset_calls_sibling_script_and_dry_run_skips_token(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    token = ctx.token_store.authorize(SHA_A, "LEDGER-1", FP)
    dry = run_verb(parse_command("baseline-reset-dry-run"), ctx, token_id=token)
    assert dry == 0
    dry_argv = ctx.calls[0][0]
    assert dry_argv[0].endswith("scripts/ops/baseline_reset.sh")
    assert dry_argv[1] == "reset"
    assert "--dry-run" in dry_argv
    assert "--target-sha" in dry_argv
    assert "--release-fingerprint" in dry_argv
    assert dry_argv[dry_argv.index("--release-fingerprint") + 1] == FP
    assert "--ticket" in dry_argv
    assert "--approval" in dry_argv
    assert dry_argv[dry_argv.index("--approval") + 1] == APPROVAL_FILE
    assert "--signature" in dry_argv
    assert dry_argv[dry_argv.index("--signature") + 1] == SIGNATURE_FILE
    assert "--shared-dir" in dry_argv
    assert "--status-file" in dry_argv
    assert "--operator-token" not in dry_argv
    assert "--env-file" not in dry_argv
    script_bytes = Path(dry_argv[0]).read_bytes()
    assert hashlib.sha256(script_bytes).hexdigest() != FP
    assert (ctx.token_store.root / f"{token}.json").is_file()
    rc = run_verb(parse_command("baseline-reset"), ctx, token_id=token)
    assert rc == 0
    reset_argv = ctx.calls[1][0]
    assert "--dry-run" not in reset_argv
    assert "--target-sha" in reset_argv
    assert SHA_A in reset_argv
    assert "--status-file" in reset_argv
    with pytest.raises(TokenError):
        ctx.token_store.consume(token, SHA_A)


def test_baseline_reset_missing_script_is_a_documented_dependency(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    (Path(ctx.releases_root) / SHA_A / "scripts" / "ops" / "baseline_reset.sh").unlink()
    token = ctx.token_store.authorize(SHA_A, "LEDGER-1", FP)
    rc = run_verb(parse_command("baseline-reset"), ctx, token_id=token)
    assert rc == 78
    assert "dependency-missing" in ctx.audit.path.read_text()
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_rollback_must_name_the_recorded_previous_release(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    assert run_verb(parse_command("rollback rel-1"), ctx) == 0
    assert ctx.calls[0][0] == [str(ctx.atomic_release), "rollback"]
    assert run_verb(parse_command("rollback other"), ctx) != 0
    assert len(ctx.calls) == 1


def test_unreadable_env_and_shadow_file_fail_closed(tmp_path):
    status = evaluate_gates(
        env_text=None,
        env_unreadable=True,
        lock_exists=False,
        variants_text=None,
        variants_unreadable=True,
        flat=_flat(),
    )
    assert "posture-unreadable" in status.refusals
    assert "shadow-unreadable" in status.refusals
    assert status.ok is False


def test_refusal_recorder_accepts_only_fixed_reason_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sys,
        "stdin",
        StringIO(json.dumps({"reason": "injection", "actor": "grok-ops@203.0.113.8"})),
    )
    assert record_refusal_main(audit_path=str(tmp_path / "audit.log"), euid=0) == 0
    row = json.loads((tmp_path / "audit.log").read_text())
    assert row["verb"] == "INVALID"
    assert row["result"] == "injection"
    assert row["actor"] == "grok-ops@203.0.113.8"
    monkeypatch.setattr(sys, "stdin", StringIO(json.dumps({"reason": "id", "actor": "root"})))
    assert record_refusal_main(audit_path=str(tmp_path / "audit.log"), euid=0) == 2
    assert (tmp_path / "audit.log").read_text().count("\n") == 1


def test_audit_log_is_append_only(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    log.append(actor="grok-ops", verb="status", sha="", token_id="", result="ok")
    log.append(actor="grok-ops", verb="promote", sha=SHA_A, token_id="t1", result="ok", ledger_id="AFS-1")
    rows = [json.loads(line) for line in (tmp_path / "audit.log").read_text().splitlines()]
    assert [row["verb"] for row in rows] == ["status", "promote"]
    assert rows[1]["sha"] == SHA_A
    assert rows[1]["token_id"] == "t1"
    assert "actor" in rows[0] and "ts" in rows[0]
    mode = (tmp_path / "audit.log").stat().st_mode
    assert not (mode & stat.S_IXUSR)


def test_package_does_not_import_broker_or_risk():
    forbidden = ("execution.tradovate_broker", "execution.paper_broker", "risk_engine", "webhook.runner")
    for path in (ROOT / "ops" / "grok_ops").glob("*.py"):
        text = path.read_text()
        for name in forbidden:
            assert name not in text


def test_self_test_count_matches_the_guard_list():
    passed, total, failures = run_self_test()
    assert total == 35
    assert failures == []
    assert passed == 35


def test_production_context_ignores_agent_environment(monkeypatch):
    monkeypatch.setenv("AFS_OPS_TOKEN_DIR", "/tmp/pwned-tokens")
    monkeypatch.setenv("AFS_OPS_ENV_FILE", "/tmp/pwned.env")
    monkeypatch.setenv("AFS_CURRENT_LINK", "/tmp/pwned-link")
    monkeypatch.setenv("AFS_OPS_VERB", "promote")
    monkeypatch.setenv("AFS_SHARED_DIR", "/tmp/pwned-shared")
    source = inspect.getsource(production_context)
    assert "AFS_OPS_" not in source
    assert "os.environ" not in source
    assert ".resolve(" not in source
    with pytest.raises(OwnershipError):
        production_context()
    assert not Path("/tmp/pwned-tokens").exists()
    assert TOKEN_DIR == "/var/lib/afs-ops/tokens"


class _Owned:
    def __init__(self, mode: int, uid: int, dev: int, ino: int) -> None:
        self.st_mode = mode
        self.st_uid = uid
        self.st_dev = dev
        self.st_ino = ino


def _fstat_ancestors(root: Path, uid: int):
    """Present directories above ``root`` as owned by ``uid`` and mode 0755.

    ``/tmp`` is world-writable, so a real walk stops there. Tests of a temp
    tree still see the real mode and owner of every fd inside ``root``.
    """
    real_fstat = os.fstat
    root_text = str(root)

    def fstat(fd: int):
        current = real_fstat(fd)
        try:
            link = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            return current
        if link.startswith(root_text):
            return current
        if stat.S_ISDIR(current.st_mode):
            return _Owned(stat.S_IFDIR | 0o755, uid, current.st_dev, current.st_ino)
        return current

    return fstat


def test_world_writable_ancestor_is_refused_without_a_fake(tmp_path):
    with pytest.raises(OwnershipError) as refused:
        assert_directory(str(tmp_path), uid=0)
    assert refused.value.code == "ownership"


def test_ownership_guard_rejects_group_writable_and_non_root(tmp_path):
    directory = tmp_path / "tokens"
    directory.mkdir(mode=0o755)
    uid = os.getuid()
    assert_directory(str(directory), uid=uid, fstat=_fstat_ancestors(tmp_path, uid))
    directory.chmod(0o775)
    with pytest.raises(OwnershipError) as writable:
        assert_directory(str(directory), uid=uid, fstat=_fstat_ancestors(tmp_path, uid))
    assert writable.value.code == "ownership"
    directory.chmod(0o755)
    with pytest.raises(OwnershipError) as not_root:
        assert_directory(str(directory), uid=0, fstat=_fstat_ancestors(tmp_path, 0))
    assert not_root.value.code == "ownership"


def _release_tree(tmp_path: Path, name: str, *, mode: int = 0o755) -> Path:
    root = tmp_path / "afs-releases" / name
    script_dir = root / "scripts" / "ops"
    script_dir.mkdir(parents=True)
    script = script_dir / "baseline_reset.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    for path in (tmp_path / "afs-releases", root, root / "scripts", script_dir, script):
        path.chmod(mode if path.is_dir() else 0o755)
    return root


def test_release_pin_detects_symlink_swap_and_writable_tree(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    prefix = tmp_path / "afs-releases"
    good = _release_tree(tmp_path, "good")
    evil = _release_tree(tmp_path, "evil")
    parent = tmp_path / "links"
    parent.mkdir(mode=0o755)
    link = parent / "current"
    link.symlink_to(good)
    pinned = pin_release(str(link), str(prefix), uid=uid, fstat=fstat)
    assert pinned.real == str(good)
    link.unlink()
    link.symlink_to(evil)
    with pytest.raises(OwnershipError) as swapped:
        pinned.reverify()
    assert swapped.value.code == "release-toctou"
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\nexit 0\n")
    outside.chmod(0o755)
    swapped_script = good / "scripts" / "ops" / "baseline_reset.sh"
    swapped_script.unlink()
    swapped_script.symlink_to(outside)
    with pytest.raises(OwnershipError) as linked:
        open_verified(str(good), "scripts/ops/baseline_reset.sh", uid=uid, fstat=fstat)
    assert linked.value.code == "release-symlink"
    pinned.close()
    good.chmod(0o775)
    fresh = parent / "fresh"
    fresh.symlink_to(good)
    with pytest.raises(OwnershipError) as writable:
        pin_release(str(fresh), str(prefix), uid=uid, fstat=fstat)
    assert writable.value.code == "ownership"


def test_zero_and_expired_tokens_refuse_without_typeerror(tmp_path):
    store = TokenStore(tmp_path)
    with pytest.raises(TokenError) as missing:
        store.find_single_open()
    assert missing.value.reason == "missing"
    assert not isinstance(missing.value, TypeError)
    (tmp_path / "broken.json").write_text('{"expires_at": null}')
    with pytest.raises(TokenError) as still_missing:
        store.find_open_for_sha(SHA_A)
    assert still_missing.value.reason == "missing"
    start = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    timed = TokenStore(tmp_path / "timed", clock=lambda: clock["now"])
    token_id = timed.authorize(SHA_A, "AFS-1", FP)
    clock["now"] = start + TOKEN_TTL + timedelta(seconds=1)
    with pytest.raises(TokenError) as expired:
        timed.find_single_open()
    assert expired.value.reason == "missing"
    assert not (timed.root / f"{token_id}.json").exists()
    dead = json.loads((timed.root / "expired" / f"{token_id}.json").read_text())
    assert dead["expired_at"]
    ctx = _ctx(tmp_path / "verb", lambda argv, env: 0)
    rc = run_verb(parse_command("baseline-reset"), ctx)
    assert rc == 3
    assert "missing" in ctx.audit.path.read_text()
    assert ctx.calls == []


def test_sudoers_rejects_setenv_mutation():
    text = render_sudoers()
    assert "SETENV" not in text
    with pytest.raises(ValueError):
        validate_sudoers_text(text.replace("env_reset", "env_reset, SETENV"))


def test_install_refuses_without_root_or_prefix(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text(PUBKEY + "\n")
    env = os.environ.copy()
    env.pop("AFS_OPS_ROOT", None)
    install = ROOT / "scripts" / "ops" / "install_grok_ops.sh"
    result = subprocess.run(
        ["bash", str(install), "--public-key", str(key)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "root" in result.stderr.lower()


def test_install_and_uninstall_round_trip(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text(PUBKEY + "\n")
    env = os.environ.copy()
    env["AFS_OPS_ROOT"] = str(tmp_path / "root")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    install = ROOT / "scripts" / "ops" / "install_grok_ops.sh"
    uninstall = ROOT / "scripts" / "ops" / "uninstall_grok_ops.sh"
    self_test = ROOT / "scripts" / "ops" / "grok_ops_self_test.sh"
    subprocess.run(["bash", str(install), "--public-key", str(key)], check=True, env=env)
    root = tmp_path / "root"
    sudoers = (root / "etc" / "sudoers.d" / "grok-ops").read_text()
    validate_sudoers_text(sudoers)
    visudo_check(sudoers)
    line = (root / "var" / "lib" / "afs-ops" / ".ssh" / "authorized_keys").read_text().strip()
    validate_authorized_key_line(line)
    assert "grok-ops" in (root / "etc" / "passwd").read_text()
    assert "/usr/sbin/nologin" in (root / "etc" / "passwd").read_text()
    promote = (root / "usr" / "local" / "sbin" / "afs-ops-promote").read_text()
    assert "env -i" in promote
    assert "cd /" in promote
    assert " -P " in promote
    assert "/usr/bin/python3" in promote
    assert "AFS_OPS_" not in promote
    assert "ops.grok_ops.actions promote" in promote
    proc = subprocess.run(["bash", str(self_test)], check=True, env=env, capture_output=True, text=True)
    assert "35/35" in proc.stdout
    audit = root / "var" / "lib" / "afs-ops" / "audit.log"
    audit.write_text('{"kept":true}\n')
    subprocess.run(["bash", str(uninstall)], check=True, env=env)
    assert not (root / "etc" / "sudoers.d" / "grok-ops").exists()
    assert audit.read_text() == '{"kept":true}\n'


def test_install_ignores_env_interpreter_and_relative_cwd(tmp_path):
    key = tmp_path / "id.pub"
    key.write_text(PUBKEY + "\n")
    marker = tmp_path / "evil-ran"
    evil = tmp_path / "evil-python"
    evil.write_text("#!/bin/sh\n" + f"touch {marker}\n" + "exit 99\n")
    evil.chmod(0o755)
    env = os.environ.copy()
    env["AFS_OPS_ROOT"] = str(tmp_path / "root")
    env["AFS_OPS_PYTHON"] = str(evil)
    env["AFS_PYTHON"] = str(evil)
    env["PYTHONPATH"] = "/tmp/not-the-repo"
    install = ROOT / "scripts" / "ops" / "install_grok_ops.sh"
    relative = subprocess.run(
        ["bash", "scripts/ops/install_grok_ops.sh", "--public-key", str(key)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert relative.returncode != 0
    assert "cwd" in relative.stderr
    assert not marker.exists()
    linked = tmp_path / "install-link.sh"
    linked.symlink_to(install)
    via_link = subprocess.run(
        ["bash", str(linked), "--public-key", str(key)],
        env=env,
        capture_output=True,
        text=True,
    )
    assert via_link.returncode != 0
    assert "symlink" in via_link.stderr
    assert not marker.exists()
    subprocess.run(["bash", str(install), "--public-key", str(key)], check=True, env=env)
    assert not marker.exists()
    promote = (tmp_path / "root" / "usr" / "local" / "sbin" / "afs-ops-promote").read_text()
    assert "env -i" in promote
    assert "/usr/bin/python3" in promote
    assert str(evil) not in promote
    text = install.read_text()
    assert "AFS_OPS_PYTHON:-" not in text
    assert "pwd" not in text
    root_branch = text.split("if [[ \"$(id -u)\" -eq 0 ]]; then", 1)[1].split("else", 1)[0]
    assert "unset AFS_OPS_ROOT" in root_branch


def test_null_and_expired_token_reads_are_refusals(tmp_path):
    store = TokenStore(tmp_path / "tokens")
    token_id = "ab" * 16
    (store.root).mkdir()
    (store.root / f"{token_id}.json").write_text("null\n")
    with pytest.raises(TokenError) as null_token:
        store.read(token_id)
    assert null_token.value.reason == "malformed"
    (store.root / f"{token_id}.json").write_text("[]\n")
    with pytest.raises(TokenError) as listed:
        store.read(token_id)
    assert listed.value.reason == "malformed"
    ctx = _ctx(tmp_path / "verb", lambda argv, env: 0)
    ctx.token_store = store
    rc = run_verb(parse_command("baseline-reset"), ctx, token_id=token_id)
    assert rc == 3
    assert "malformed" in ctx.audit.path.read_text()
    assert ctx.calls == []
    start = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    timed = TokenStore(tmp_path / "timed", clock=lambda: clock["now"])
    live = timed.authorize(SHA_A, "AFS-1", FP)
    clock["now"] = start + TOKEN_TTL + timedelta(seconds=1)
    with pytest.raises(TokenError) as expired:
        timed.read(live)
    assert expired.value.reason == "expired"
    assert not (timed.root / f"{live}.json").exists()
    link_target = tmp_path / "other.json"
    link_target.write_text('{"token_id":"%s"}\n' % ("cd" * 16))
    alias = "cd" * 16
    (timed.root / f"{alias}.json").symlink_to(link_target)
    with pytest.raises(TokenError):
        timed.read(alias)
    expired_ctx = _ctx(tmp_path / "expired-verb", lambda argv, env: 0)
    expired_ctx.token_store = timed
    rc = run_verb(parse_command("baseline-reset"), expired_ctx, token_id=live)
    assert rc == 3
    assert "expired" in expired_ctx.audit.path.read_text() or "missing" in expired_ctx.audit.path.read_text()
    assert expired_ctx.calls == []


def test_lane_reset_argv_parses_with_merged_baseline_reset(tmp_path, monkeypatch):
    argv = baseline_reset_argv(
        "scripts/ops/baseline_reset.sh",
        sha=SHA_A,
        ledger_id="LEDGER-1",
        release_fingerprint=FP,
        dry_run=False,
    )
    flags = [item for item in argv[1:] if item.startswith("--")]
    assert flags == list(RESET_CLI_FLAGS)
    captured: dict = {}

    def fake_reset(**kwargs):
        captured.update(kwargs)
        return {"dry_run": True}

    monkeypatch.setattr(
        "ops.baseline_reset._read_env",
        lambda path: "LIVE_TRADING_ENABLED=false\nTRADOVATE_ENV=demo\n",
    )
    monkeypatch.setattr(
        "ops.baseline_reset._load_status",
        lambda path: {"has_open_position": False, "open_orders": []},
    )
    monkeypatch.setattr("ops.baseline_reset.reset", fake_reset)
    code = baseline_reset_main(argv[1:])
    assert code == 0
    assert captured["target_sha"] == SHA_A
    assert captured["release_fingerprint"] == FP
    assert captured["ticket"] == "LEDGER-1"
    assert captured["approval_path"] == Path(APPROVAL_FILE)
    assert captured["signature_path"] == Path(SIGNATURE_FILE)
    assert "operator_token" not in captured
    for path in (ROOT / "ops" / "grok_ops").glob("*.py"):
        text = path.read_text()
        assert "ssh-keygen -Y" not in text
        assert "canonical_approval(" not in text


def test_pinned_inode_binds_a_later_open(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    good = _release_tree(tmp_path, "good")
    pinned = hold_directory(str(good), uid=uid, fstat=fstat)
    opened = open_verified(
        pinned.real,
        "scripts/ops/baseline_reset.sh",
        uid=uid,
        pin=pinned,
        fstat=fstat,
    )
    assert opened.endswith("scripts/ops/baseline_reset.sh")
    os.close(pinned.dirfd)
    pinned.dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    with pytest.raises(OwnershipError) as moved:
        open_verified(pinned.real, "scripts/ops/baseline_reset.sh", uid=uid, pin=pinned, fstat=fstat)
    assert moved.value.code == "release-toctou"
    pinned.close()


def test_python_dash_p_ignores_hostile_cwd_module(tmp_path):
    hostile = tmp_path / "cwd"
    hostile.mkdir()
    (hostile / "json.py").write_text("raise SystemExit('shadowed')\n")
    isolated = subprocess.run(
        ["/usr/bin/python3", "-B", "-P", "-c", "import json; print(json.dumps({'ok': True}))"],
        cwd=hostile,
        capture_output=True,
        text=True,
    )
    assert isolated.returncode == 0
    assert "shadowed" not in isolated.stderr
    assert "true" in isolated.stdout
    shadowed = subprocess.run(
        ["/usr/bin/python3", "-B", "-c", "import json; print(json.dumps({'ok': True}))"],
        cwd=hostile,
        capture_output=True,
        text=True,
    )
    assert shadowed.returncode != 0


def test_sudoers_rejects_empty_command_path():
    text = render_sudoers() + "grok-ops ALL=(root) NOPASSWD: \n"
    with pytest.raises(ValueError, match="empty"):
        validate_sudoers_text(text)
    for path in SUDO_PATHS:
        assert f"{path} \"\"" in render_sudoers()
    bare = render_sudoers().replace(' ""', "", 1)
    with pytest.raises(ValueError, match="arguments"):
        validate_sudoers_text(bare)


def test_token_ttl_is_bound_to_created_at(tmp_path):
    start = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    store = TokenStore(tmp_path, clock=lambda: clock["now"])
    token_id = store.authorize(SHA_A, "AFS-1", FP)
    path = store.root / f"{token_id}.json"
    payload = json.loads(path.read_text())
    payload["expires_at"] = (start + TOKEN_TTL + timedelta(hours=5)).isoformat()
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(TokenError) as stretched:
        store.read(token_id)
    assert stretched.value.reason == "malformed"
    payload["expires_at"] = (start + TOKEN_TTL).isoformat()
    payload["ledger_id"] = ""
    path.write_text(json.dumps(payload) + "\n")
    with pytest.raises(TokenError) as blank:
        store.read(token_id)
    assert blank.value.reason == "malformed"


def test_mark_expired_oserror_still_refuses(tmp_path, monkeypatch):
    start = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    store = TokenStore(tmp_path, clock=lambda: clock["now"])
    token_id = store.authorize(SHA_A, "AFS-1", FP)
    clock["now"] = start + TOKEN_TTL + timedelta(seconds=1)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(TokenError) as expired:
        store.read(token_id)
    assert expired.value.reason == "expired"


def test_require_same_file_rejects_a_different_inode(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    installed = tmp_path / "installed.py"
    running = tmp_path / "running.py"
    installed.write_text("print('installed')\n")
    running.write_text("print('running')\n")
    with pytest.raises(OwnershipError) as mismatch:
        require_same_file(str(running), str(installed), uid=uid, fstat=fstat)
    assert mismatch.value.code == "code-path"
    require_same_file(str(installed), str(installed), uid=uid, fstat=fstat)
    assert "require_same_file(" in inspect.getsource(production_context)


def test_folder_name_is_not_authorization(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    sha = "c" * 40
    releases = tmp_path / "afs-releases"
    shared = tmp_path / "afs-shared"
    target = releases / sha
    (target / "scripts").mkdir(parents=True)
    manifest = {"repo": {"commit": sha}, "fingerprint_sha256": FP}
    (target / "release_manifest.json").write_text(json.dumps(manifest))
    (target / "release_manifest.json").chmod(0o644)
    complete = shared / "release-complete"
    complete.mkdir(parents=True)
    (complete / sha).write_text(("d" * 64) + "\n")
    (complete / sha).chmod(0o644)
    for path in (releases, target, shared, complete):
        path.chmod(0o755)
    with pytest.raises(ActionError) as mismatch:
        prove_target_release(str(releases), str(shared), sha, uid=uid, fstat=fstat)
    assert mismatch.value.code == "record-mismatch"


def test_symlinked_release_root_is_refused(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    sha = "e" * 40
    real = tmp_path / "real-releases" / sha
    real.mkdir(parents=True)
    link_root = tmp_path / "linked-releases"
    link_root.symlink_to(tmp_path / "real-releases")
    with pytest.raises(OwnershipError) as ancestor:
        hold_directory(str(link_root / sha), uid=uid, fstat=fstat)
    assert ancestor.value.code == "release-symlink"
    releases = tmp_path / "afs-releases"
    releases.mkdir(mode=0o755)
    (releases / sha).symlink_to(real)
    with pytest.raises(OwnershipError) as leaf:
        hold_directory(str(releases / sha), uid=uid, fstat=fstat)
    assert leaf.value.code == "release-symlink"


@pytest.mark.parametrize("verb", ["verify", "rollback", "promote"])
def test_symlink_atomic_release_never_runs(tmp_path, verb):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    outside = tmp_path / "outside.sh"
    marker = tmp_path / "pwned"
    outside.write_text(f"#!/bin/sh\ntouch {marker}\n")
    outside.chmod(0o777)
    if verb == "promote":
        script = Path(ctx.releases_root) / SHA_A / "scripts" / "atomic_release.sh"
        token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
        command = f"promote {SHA_A}"
    else:
        script = ctx.repo_root / "scripts" / "atomic_release.sh"
        token = ""
        ctx.env_file.write_text(_quiet_env() + f"EXPECTED_LIVE_COMMIT={SHA_A}\n")
        command = "verify" if verb == "verify" else "rollback rel-1"
    script.unlink()
    script.symlink_to(outside)
    rc = run_verb(parse_command(command), ctx, token_id=token)
    assert rc == 3
    assert not marker.exists()
    assert "release-symlink" in ctx.audit.path.read_text()
    assert ctx.calls == []
    if verb == "promote":
        assert (ctx.token_store.root / f"{token}.json").is_file()


def test_promote_b_while_running_a(tmp_path):
    """Stub only. B's script is a two-line shell file, not scripts/atomic_release.sh.

    It shows the runner executes the target pin rather than the running pin.
    A git-less copy of the real script is refused before the token is consumed.
    """
    sha_b = "b" * 40
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    running_marker = tmp_path / "from-a"
    (ctx.repo_root / "scripts" / "atomic_release.sh").write_text(
        f"#!/bin/sh\ntouch {running_marker}\n"
    )
    _seal_target(Path(ctx.releases_root), Path(ctx.shared_root), sha_b, ctx.repo_root)
    target_script = Path(ctx.releases_root) / sha_b / "scripts" / "atomic_release.sh"
    target_marker = tmp_path / "from-b"
    target_script.write_text(f"#!/bin/sh\necho B > {target_marker}\n")
    target_script.chmod(0o755)
    ctx.runner = _production_runner(ctx)
    token = ctx.token_store.authorize(sha_b, "AFS-9", FP)
    rc = run_verb(parse_command(f"promote {sha_b}"), ctx, token_id=token)
    assert rc == 0
    assert target_marker.read_text().strip() == "B"
    assert not running_marker.exists()


def test_group_writable_file_is_refused(tmp_path):
    uid = os.getuid()
    root = tmp_path / "tree"
    root.mkdir(mode=0o755)
    leaf = root / "leaf.sh"
    leaf.write_text("#!/bin/sh\n")
    leaf.chmod(0o666)
    with pytest.raises(OwnershipError) as refused:
        open_verified(str(root), "leaf.sh", uid=uid, fstat=_fstat_ancestors(tmp_path, uid))
    assert refused.value.code == "ownership"


def test_pin_release_restats_the_symlink(tmp_path, monkeypatch):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    prefix = tmp_path / "afs-releases"
    good = _release_tree(tmp_path, "good")
    parent = tmp_path / "links"
    parent.mkdir(mode=0o755)
    link = parent / "current"
    link.symlink_to(good)
    real_stat = os.stat
    seen = {"n": 0}

    def counting(path, *args, dir_fd=None, follow_symlinks=True, **kwargs):
        result = real_stat(path, *args, dir_fd=dir_fd, follow_symlinks=follow_symlinks, **kwargs)
        if path == "current" and follow_symlinks is False:
            seen["n"] += 1
            if seen["n"] >= 2:
                swapped = type("Swapped", (), {})()
                swapped.st_mode = result.st_mode
                swapped.st_ino = result.st_ino + 1
                swapped.st_dev = result.st_dev
                swapped.st_uid = result.st_uid
                swapped.st_gid = result.st_gid
                return swapped
        return result

    monkeypatch.setattr(os, "stat", counting)
    with pytest.raises(OwnershipError) as raced:
        pin_release(str(link), str(prefix), uid=uid, fstat=fstat)
    assert raced.value.code == "release-toctou"
    assert seen["n"] >= 2


def test_open_verified_rechecks_the_release_symlink(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    prefix = tmp_path / "afs-releases"
    good = _release_tree(tmp_path, "good")
    evil = _release_tree(tmp_path, "evil")
    parent = tmp_path / "links"
    parent.mkdir(mode=0o755)
    link = parent / "current"
    link.symlink_to(good)
    pinned = pin_release(str(link), str(prefix), uid=uid, fstat=fstat)
    link.unlink()
    link.symlink_to(evil)
    with pytest.raises(OwnershipError) as swapped:
        open_verified(pinned.real, "scripts/ops/baseline_reset.sh", uid=uid, pin=pinned, fstat=fstat)
    assert swapped.value.code == "release-toctou"
    pinned.close()


def test_open_from_rechecks_the_held_directory(tmp_path):
    uid = os.getuid()
    good = _release_tree(tmp_path, "held")
    fd = os.open(good, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    held = os.fstat(fd)
    pin = PinnedRelease(
        link="",
        real=str(good),
        prefix=str(good),
        dev=held.st_dev,
        ino=held.st_ino,
        dirfd=fd,
        uid=uid,
    )
    os.close(pin.dirfd)
    pin.dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    with pytest.raises(OwnershipError) as moved:
        _open_from(pin, "scripts/ops/baseline_reset.sh")
    assert moved.value.code == "release-toctou"
    pin.close()


def test_runner_env_refuses_afs_ops_keys():
    with pytest.raises(ActionError) as refused:
        _runner_env({"AFS_OPS_TOKEN_DIR": "/tmp/not-used"})
    assert refused.value.code == "env-forbidden"


def test_release_env_refuses_unknown_keys(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    ctx.release_env.write_text("AFS_BOX=box.example\nEVIL=1\n")
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc == 3
    assert "release-env-forbidden" in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_runner_refuses_program_outside_the_pin(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    evil = tmp_path / "evil.sh"
    marker = tmp_path / "ran"
    evil.write_text(f"#!/bin/sh\ntouch {marker}\n")
    evil.chmod(0o755)
    ctx.runner = _production_runner(ctx)
    code = ctx.runner([str(evil)], {})
    assert code == 3
    assert not marker.exists()


def test_runner_rechecks_the_pin_before_exec(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    script = ctx.repo_root / "scripts" / "atomic_release.sh"
    marker = tmp_path / "ran"
    script.write_text(f"#!/bin/sh\ntouch {marker}\n")
    script.chmod(0o755)
    ctx.runner = _production_runner(ctx)
    os.close(ctx.release_pin.dirfd)
    ctx.release_pin.dirfd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    with pytest.raises(OwnershipError):
        ctx.runner([str(script)], {})
    assert not marker.exists()


def test_backup_argv_uses_isolated_python(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    assert run_verb(parse_command("backup"), ctx) == 0
    assert ctx.calls[0][0][:4] == [ctx.python, "-B", "-P", "-m"]


def test_relative_program_is_refused(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    with pytest.raises(ActionError) as refused:
        _invoke(ctx, parse_command("status"), {}, ["scripts/atomic_release.sh"], {})
    assert refused.value.code == "relative-program"
    assert ctx.calls == []


def test_gitless_real_atomic_release_refuses_before_consume(tmp_path):
    """Unmocked. The sealed files are the repo's atomic_release.sh and deploy_lock.sh.

    The production runner's cwd is that git-less tree, so the real script exits 128.
    The lane refuses before consume and the GO token stays open.
    """
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    target = Path(ctx.releases_root) / SHA_A
    shutil.rmtree(target / ".git")
    for name in ("atomic_release.sh", "deploy_lock.sh"):
        src = ROOT / "scripts" / name
        dest = target / "scripts" / name
        dest.write_bytes(src.read_bytes())
        dest.chmod(0o755)
    script = str(target / "scripts" / "atomic_release.sh")
    pinned = hold_directory(str(target), uid=os.getuid(), fstat=ctx.fstat)
    ctx.exec_pin = pinned
    try:
        code = _production_runner(ctx)(
            [script, "promote", SHA_A],
            {"AFS_BOX": "box.example", "PATH": "/usr/bin:/bin"},
        )
    finally:
        ctx.exec_pin = None
        pinned.close()
    assert code == 128
    ctx.runner = _production_runner(ctx)
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc == 3
    assert "unsupported-release-model" in ctx.audit.path.read_text()
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_gitless_verify_refuses_before_consume(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    shutil.rmtree(ctx.repo_root / ".git")
    ctx.env_file.write_text(_quiet_env() + f"EXPECTED_LIVE_COMMIT={SHA_A}\n")
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command("verify"), ctx, token_id=token)
    assert rc == 3
    assert "unsupported-release-model" in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_gitless_rollback_refuses_before_consume(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    shutil.rmtree(ctx.repo_root / ".git")
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command("rollback rel-1"), ctx, token_id=token)
    assert rc == 3
    assert "unsupported-release-model" in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_nested_repo_target_is_refused_before_consume(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    target = Path(ctx.releases_root) / SHA_A
    subprocess.run(
        ["git", "config", "core.worktree", str(tmp_path)],
        cwd=target,
        check=True,
        capture_output=True,
    )
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc == 3
    assert "unsupported-release-model" in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_baseline_fingerprint_must_match_the_manifest(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    other = "cd" * 32
    token = ctx.token_store.authorize(SHA_A, "LEDGER-1", other)
    rc = run_verb(parse_command("baseline-reset"), ctx, token_id=token)
    assert rc == 3
    assert "record-mismatch" in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_token_fingerprint_must_match_the_manifest(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    other = "cd" * 32
    token = ctx.token_store.authorize(SHA_A, "AFS-1", other)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc == 3
    assert "record-mismatch" in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_manifest_commit_must_be_the_requested_sha(tmp_path):
    uid = os.getuid()
    fstat = _fstat_ancestors(tmp_path, uid)
    sha = "c" * 40
    other = "d" * 40
    releases = tmp_path / "afs-releases"
    shared = tmp_path / "afs-shared"
    target = releases / sha
    (target / "scripts").mkdir(parents=True)
    manifest = {"repo": {"commit": other}, "fingerprint_sha256": FP}
    (target / "release_manifest.json").write_text(json.dumps(manifest))
    (target / "release_manifest.json").chmod(0o644)
    complete = shared / "release-complete"
    complete.mkdir(parents=True)
    (complete / sha).write_text(FP + "\n")
    (complete / sha).chmod(0o644)
    for path in (releases, target, shared, complete):
        path.chmod(0o755)
    with pytest.raises(ActionError) as mismatch:
        prove_target_release(str(releases), str(shared), sha, uid=uid, fstat=fstat)
    assert mismatch.value.code == "record-mismatch"


def test_symlinked_release_is_audited_as_release_symlink(tmp_path):
    ctx = _ctx(tmp_path, lambda argv, env: 0)
    linked = tmp_path / "linked-releases"
    linked.symlink_to(Path(ctx.releases_root))
    ctx.releases_root = str(linked)
    token = ctx.token_store.authorize(SHA_A, "AFS-1", FP)
    rc = run_verb(parse_command(f"promote {SHA_A}"), ctx, token_id=token)
    assert rc == 3
    assert "release-symlink" in ctx.audit.path.read_text()
    assert "refused:ancestor" not in ctx.audit.path.read_text()
    assert ctx.calls == []
    assert (ctx.token_store.root / f"{token}.json").is_file()


def test_expected_uid_is_required_and_paths_do_not_resolve():
    from dataclasses import MISSING

    assert ActionContext.__dataclass_fields__["expected_uid"].default is MISSING
    source = (ROOT / "ops" / "grok_ops" / "paths.py").read_text()
    assert "realpath" not in source
    assert ".resolve(" not in source
    actions = (ROOT / "ops" / "grok_ops" / "actions.py").read_text()
    assert "Path.resolve" not in actions
    assert "AFS_OPS_PYTHON" not in actions
