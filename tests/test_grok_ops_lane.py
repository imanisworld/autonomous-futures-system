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
import stat
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from io import StringIO
from pathlib import Path

import pytest

from ops.grok_ops.actions import ActionContext, production_context, run_verb
from ops.grok_ops.paths import (
    TOKEN_DIR,
    OwnershipError,
    assert_directory,
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
    atomic = tmp_path / "atomic_release.sh"
    atomic.write_text("#!/bin/sh\nexit 0\n")
    atomic.chmod(0o755)
    calls = []

    def _runner(argv, env):
        calls.append((argv, env))
        return runner(argv, env)

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
    assert ctx.calls[0][0][:3] == [str(ctx.atomic_release), "promote", SHA_A]
    assert "--force-lock" not in ctx.calls[0][0]
    assert "AFS_BOX" in ctx.calls[0][1]
    with pytest.raises(TokenError) as reused:
        ctx.token_store.consume(token, SHA_A)
    assert reused.value.reason == "reused"
    other = ctx.token_store.authorize(SHA_B, "AFS-9", FP)
    rc = run_verb(parse_command(f"promote {SHA_B}"), ctx, token_id=other)
    assert rc != 0
    assert "ancestor" in ctx.audit.path.read_text()
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
    assert "--shared-dir" in dry_argv
    assert "--status-file" in dry_argv
    phrase = f"AUTHORIZE BASELINE RESET {SHA_A} {FP} LEDGER-1"
    assert phrase in dry_argv
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
    (ctx.repo_root / "scripts" / "ops" / "baseline_reset.sh").unlink()
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


def test_self_test_is_34_of_34():
    passed, total, failures = run_self_test()
    assert total == 34
    assert failures == []
    assert passed == 34


def test_production_context_ignores_agent_environment(monkeypatch):
    monkeypatch.setenv("AFS_OPS_TOKEN_DIR", "/tmp/pwned-tokens")
    monkeypatch.setenv("AFS_OPS_ENV_FILE", "/tmp/pwned.env")
    monkeypatch.setenv("AFS_CURRENT_LINK", "/tmp/pwned-link")
    monkeypatch.setenv("AFS_OPS_VERB", "promote")
    monkeypatch.setenv("AFS_SHARED_DIR", "/tmp/pwned-shared")
    source = inspect.getsource(production_context)
    assert "AFS_OPS_" not in source
    assert "os.environ" not in source
    with pytest.raises(OwnershipError):
        production_context()
    assert not Path("/tmp/pwned-tokens").exists()
    assert TOKEN_DIR == "/var/lib/afs-ops/tokens"


def test_ownership_guard_rejects_group_writable_and_non_root(tmp_path):
    directory = tmp_path / "tokens"
    directory.mkdir(mode=0o755)
    assert_directory(str(directory), uid=os.getuid())
    directory.chmod(0o775)
    with pytest.raises(OwnershipError):
        assert_directory(str(directory), uid=os.getuid())
    directory.chmod(0o755)
    with pytest.raises(OwnershipError):
        assert_directory(str(directory), uid=0)


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
    prefix = tmp_path / "afs-releases"
    good = _release_tree(tmp_path, "good")
    evil = _release_tree(tmp_path, "evil")
    parent = tmp_path / "links"
    parent.mkdir(mode=0o755)
    link = parent / "current"
    link.symlink_to(good)
    pinned = pin_release(str(link), str(prefix), uid=uid)
    assert pinned.real == str(good.resolve())
    link.unlink()
    link.symlink_to(evil)
    with pytest.raises(OwnershipError) as swapped:
        pinned.reverify(uid=uid)
    assert swapped.value.code == "release-toctou"
    outside = tmp_path / "outside.sh"
    outside.write_text("#!/bin/sh\nexit 0\n")
    swapped_script = good / "scripts" / "ops" / "baseline_reset.sh"
    swapped_script.unlink()
    swapped_script.symlink_to(outside)
    with pytest.raises(OwnershipError):
        open_verified(str(good), "scripts/ops/baseline_reset.sh", uid=uid)
    good.chmod(0o775)
    fresh = parent / "fresh"
    fresh.symlink_to(good)
    with pytest.raises(OwnershipError):
        pin_release(str(fresh), str(prefix), uid=uid)


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
    assert "/usr/bin/python3" in promote
    assert "AFS_OPS_" not in promote
    assert "ops.grok_ops.actions promote" in promote
    proc = subprocess.run(["bash", str(self_test)], check=True, env=env, capture_output=True, text=True)
    assert "34/34" in proc.stdout
    audit = root / "var" / "lib" / "afs-ops" / "audit.log"
    audit.write_text('{"kept":true}\n')
    subprocess.run(["bash", str(uninstall)], check=True, env=env)
    assert not (root / "etc" / "sudoers.d" / "grok-ops").exists()
    assert audit.read_text() == '{"kept":true}\n'
