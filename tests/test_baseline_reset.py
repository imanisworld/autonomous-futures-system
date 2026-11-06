"""Guards for the operator baseline reset, restore, backup, and recovery proof.

Each refusal test starts from an otherwise valid request. Deleting that guard
lets the request through, so the assertion that nothing was written fails.
"""
from __future__ import annotations

import inspect
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from execution.broker_interface import Fill
from ops.baseline_authorization import PATH_A_PINS, check_release, staged_manifest_digest
from ops.release_approval import canonical_approval
from ops.baseline_reset import (
    RESET_CLI_FLAGS,
    Refusal,
    backup,
    classify_recovery,
    enforce_runtime_guards,
    exposure_from_broker,
    main,
    place_recovery_order,
    preflight_report,
    require_demo_posture,
    require_flat,
    require_lock_clear,
    require_operator_token,
    require_sha,
    require_ticket,
    require_variants_inactive,
    reset,
    restore,
)

SECRET = "super-secret-value"
SHA = "a" * 40
OTHER = "b" * 40
FP = "f" * 64
OTHER_FP = "e" * 64
TICKET = "AFS-0508"
WHEN = datetime(2026, 11, 6, 18, 0, tzinfo=timezone.utc)
FLAT = {
    "has_open_position": False,
    "open_orders": [],
    "order_submissions": {
        "store_trusted": True,
        "entries_blocked": False,
        "recovery_required": False,
        "unresolved": [],
    },
}


def _env(log_dir: Path, *, live="false", tradovate="demo", schedule="current", extra=""):
    text = "\n".join([
        f"LIVE_TRADING_ENABLED={live}",
        f"TRADOVATE_ENV={tradovate}",
        "BROKER=tradovate",
        "MAX_CONTRACTS_HARD_CAP=1",
        f"SCHEDULE_MODE={schedule}",
        f"EXPECTED_PROOF_SCHEDULE_MODE={schedule}",
        "HTF_DIRECTION_MODE=off",
        "EXPECTED_PROOF_HTF_DIRECTION_MODE=off",
        "EXIT_MODE=runner_shadow" if schedule == "current" else "static",
        "EXPECTED_PROOF_EXIT_MODE=runner_shadow" if schedule == "current" else "static",
        "EXPECTED_LIVE_COMMIT=" + "c" * 40,
        "EXPECTED_RELEASE_FINGERPRINT=" + "d" * 64,
        "EXPECTED_LIVE_BRANCH=main",
        "EXPECTED_RISK_RULES_SHA256=" + "e" * 64,
        f"TRADOVATE_PASSWORD={SECRET}",
        f"LOG_DIR={log_dir}",
        extra,
    ])
    return text.strip() + "\n"


def _layout(tmp_path: Path):
    shared = tmp_path / "shared"
    log_dir = tmp_path / "logs"
    submissions = log_dir / "order_submissions"
    (submissions / "records").mkdir(parents=True)
    (submissions / "LOCK").write_text("")
    (submissions / "records" / "one.json").write_text(json.dumps({"account_id": SECRET, "state": "CLOSED"}))
    log_dir.mkdir(exist_ok=True)
    (log_dir / "journal_2026-11-06.jsonl").write_text('{"event":"keep"}\n')
    (log_dir / "errors.log").write_text("err\n")
    (log_dir / "track_b_daily_heartbeat.jsonl").write_text("track-b-row\n")
    state = log_dir / "track_b_heartbeat_state"
    state.mkdir()
    (state / "session.json").write_text('{"kept":true}\n')
    shadow = shared / "trend_shadow_history.json"
    shared.mkdir()
    shadow.write_text("shadow-history")
    (shared / ".env").write_text(_env(log_dir))
    return shared, log_dir, shadow


def _files(root: Path):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


class _RootFile:
    """Stat stand-in for a root-owned mode 0444 regular file."""

    st_mode = stat.S_IFREG | 0o444
    st_uid = 0


def _sign(sha=SHA, digest=FP, ticket=TICKET, expires="2099-01-01T00:00:00Z", key_name="op"):
    directory = Path(tempfile.mkdtemp(prefix="afs-approval-"))
    key_path = directory / key_name
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-q"],
        check=True,
    )
    approval = directory / "approval.txt"
    approval.write_bytes(canonical_approval(
        target_sha=sha, manifest_digest=digest, ticket=ticket, expires=expires,
    ))
    subprocess.run(
        [
            "ssh-keygen", "-Y", "sign", "-f", str(key_path),
            "-n", "afs-release-approval", str(approval),
        ],
        check=True,
    )
    kind, material, *_rest = key_path.with_suffix(".pub").read_text().split()
    allowed = directory / "allowed_signers"
    allowed.write_text(
        f'imani@mac namespaces="afs-release-approval" {kind} {material}\n'
    )
    return approval, Path(str(approval) + ".sig"), allowed


def _reset(shared, log_dir, **overrides):
    approval, signature, allowed = _sign()
    args = dict(
        shared=shared,
        env_text=_env(log_dir),
        status=json.loads(json.dumps(FLAT)),
        target_sha=SHA,
        release_fingerprint=FP,
        ticket=TICKET,
        approval_path=approval,
        signature_path=signature,
        allowed_signers=allowed,
        signer_stat=_RootFile(),
        operator_user="imani",
        clock=lambda: WHEN,
        environ={},
    )
    args.update(overrides)
    return reset(**args)


def _untouched(shared, log_dir, shadow):
    assert (log_dir / "track_b_daily_heartbeat.jsonl").read_text() == "track-b-row\n"
    assert (log_dir / "track_b_heartbeat_state" / "session.json").read_text() == '{"kept":true}\n'
    assert shadow.read_text() == "shadow-history"
    assert (log_dir / "journal_2026-11-06.jsonl").read_text() == '{"event":"keep"}\n'
    assert SECRET in (shared / ".env").read_text()


def test_guard_sources_are_called_by_each_mutation():
    for fn in (reset, restore, backup, place_recovery_order):
        assert "enforce_runtime_guards(" in inspect.getsource(fn)
    source = inspect.getsource(enforce_runtime_guards)
    for name in (
        "require_demo_posture(",
        "require_lock_clear(",
        "require_flat(",
        "require_variants_inactive(",
    ):
        assert name in source
    assert "execute_bracket" not in inspect.getsource(reset)
    assert "verify_reset_approval(" in inspect.getsource(reset)
    assert "AUTHORIZE BASELINE RESET" not in inspect.getsource(reset)
    assert "systemctl" not in inspect.getsource(reset)
    assert "execute_bracket" in inspect.getsource(place_recovery_order)


def test_require_sha_and_ticket_and_token_reject_blanks():
    with pytest.raises(Refusal):
        require_sha("F8B044BE")
    with pytest.raises(Refusal):
        require_sha(SHA[:12])
    with pytest.raises(Refusal):
        require_ticket("")
    with pytest.raises(Refusal):
        require_ticket("has space")
    with pytest.raises(Refusal):
        require_operator_token("", f"AUTHORIZE BASELINE RESET {SHA} {FP} {TICKET}")
    with pytest.raises(Refusal):
        require_operator_token(
            f"AUTHORIZE BASELINE RESET {OTHER} {FP} {TICKET}",
            f"AUTHORIZE BASELINE RESET {SHA} {FP} {TICKET}",
        )
    with pytest.raises(Refusal):
        require_operator_token(
            f"AUTHORIZE BASELINE RESET {SHA} {FP} AFS-1",
            f"AUTHORIZE BASELINE RESET {SHA} {FP} {TICKET}",
        )


@pytest.mark.parametrize("live,tradovate", [
    ("true", "demo"),
    ("false", "live"),
    ("false", "Demo"),
    ("FALSE", "demo"),
])
def test_demo_posture_refuses_anything_but_exact_pins(live, tradovate):
    with pytest.raises(Refusal):
        require_demo_posture(_env(Path("/tmp/logs"), live=live, tradovate=tradovate))


def test_missing_live_or_duplicate_live_refuses():
    text = _env(Path("/tmp/logs")).replace("LIVE_TRADING_ENABLED=false\n", "")
    with pytest.raises(Refusal):
        require_demo_posture(text)
    doubled = "LIVE_TRADING_ENABLED=false\n" + _env(Path("/tmp/logs"))
    with pytest.raises(Refusal):
        require_demo_posture(doubled)


def test_lock_flat_and_variants_guards(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    require_lock_clear(shared)
    (shared / "deploy.lock").mkdir()
    with pytest.raises(Refusal, match="deploy lock"):
        require_lock_clear(shared)
    with pytest.raises(Refusal):
        require_flat(["nope"])
    with pytest.raises(Refusal):
        require_flat({"has_open_position": False})
    with pytest.raises(Refusal):
        require_flat({**FLAT, "has_open_position": True})
    with pytest.raises(Refusal):
        require_flat({**FLAT, "open_orders": [{"ordStatus": "Working"}]})
    with pytest.raises(Refusal):
        require_flat({**FLAT, "order_submissions": {**FLAT["order_submissions"], "recovery_required": True}})
    with pytest.raises(Refusal):
        require_flat({**FLAT, "order_submissions": {**FLAT["order_submissions"], "store_trusted": False}})
    with pytest.raises(Refusal):
        require_variants_inactive("VARIANTS_START=V-A\n", {})
    with pytest.raises(Refusal):
        require_variants_inactive("", {"VARIANTS_START": "V-B"})
    require_variants_inactive("VARIANTS_START=None\n", {"VARIANTS_START": ""})
    # The lock guard is exercised against a real directory below.


def test_each_reset_refusal_writes_nothing(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    before = _files(tmp_path)
    cases = [
        dict(target_sha="abc"),
        dict(release_fingerprint="abc"),
        dict(ticket="bad ticket"),
        dict(env_text=_env(log_dir, live="true")),
        dict(env_text=_env(log_dir, tradovate="live")),
        dict(status={**FLAT, "has_open_position": True}),
        dict(status={**FLAT, "open_orders": [{"ordStatus": "Working"}]}),
        dict(status={**FLAT, "order_submissions": {**FLAT["order_submissions"], "entries_blocked": True, "recovery_required": True}}),
        dict(status=None),
        dict(environ={"VARIANTS_START": "V-A"}),
        dict(env_text=_env(log_dir) + "VARIANTS_START=V-C\n"),
    ]
    (shared / "deploy.lock").mkdir()
    with pytest.raises(Refusal, match="deploy lock"):
        _reset(shared, log_dir)
    (shared / "deploy.lock").rmdir()
    for overrides in cases:
        with pytest.raises(Refusal):
            _reset(shared, log_dir, **overrides)
    assert _files(tmp_path) == before
    _untouched(shared, log_dir, shadow)


def test_dry_run_reset_writes_nothing_and_hides_secrets(tmp_path, capsys):
    shared, log_dir, shadow = _layout(tmp_path)
    _status_file(tmp_path)
    before = _files(tmp_path)
    result = _reset(shared, log_dir, dry_run=True)
    assert result["writes"] == "none"
    assert result["dry_run"] == "reset"
    assert _files(tmp_path) == before
    approval, signature, _allowed = _sign()
    code = main([
        "reset", "--shared-dir", str(shared), "--status-file", _status_file(tmp_path),
        "--target-sha", SHA, "--release-fingerprint", FP, "--ticket", TICKET,
        "--approval", str(approval), "--signature", str(signature),
        "--dry-run",
    ])
    captured = capsys.readouterr()
    assert code == 1
    assert "allowed_signers missing" in captured.err
    assert "writes=none" not in captured.out
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert _files(tmp_path) == before
    _untouched(shared, log_dir, shadow)


def _status_file(tmp_path: Path) -> str:
    path = tmp_path / "status.json"
    path.write_text(json.dumps(FLAT))
    return str(path)


def test_reset_is_atomic_and_backs_up_the_prior_baseline(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    prior = b'{"target_sha": "%s"}\n' % OTHER.encode()
    (shared / "baseline_authorization.json").write_bytes(prior)
    (shared / ".env").write_text(
        "SCHEDULE_MODE=current\n" + _env(log_dir)
    )
    result = _reset(shared, log_dir, env_text=(shared / ".env").read_text())
    assert result["baseline_reset"] == "ok"
    assert result["operator_user"] == "imani"
    assert result["recorded_at"] == "2026-11-06T18:00:00Z"
    assert result["prior_baseline_sha256"] != "none"
    auth = json.loads((shared / "baseline_authorization.json").read_text())
    assert auth["target_sha"] == SHA
    assert auth["release_fingerprint"] == FP
    assert auth["ticket_id"] == TICKET
    assert auth["operator_user"] == "imani"
    assert auth["prior_baseline_sha256"] == result["prior_baseline_sha256"]
    assert auth["prior_pins"]["SCHEDULE_MODE"] == "current"
    backup = shared / auth["prior_authorization_backup"]
    assert backup.read_bytes() == prior
    env_text = (shared / ".env").read_text()
    for key, value in PATH_A_PINS.items():
        assert f"{key}={value}" in env_text.splitlines()
    assert "BROKER=tradovate" in env_text
    assert "MAX_CONTRACTS_HARD_CAP=1" in env_text
    assert "LIVE_TRADING_ENABLED=false" in env_text
    assert "TRADOVATE_ENV=demo" in env_text
    assert f"TRADOVATE_PASSWORD={SECRET}" in env_text
    assert "EXPECTED_LIVE_COMMIT=" + "c" * 40 in env_text
    audit = (shared / "baseline_audit.jsonl").read_text().strip().splitlines()
    row = json.loads(audit[-1])
    assert row["action"] == "reset"
    assert row["operator_user"] == "imani"
    assert row["recorded_at"] == "2026-11-06T18:00:00Z"
    assert row["target_sha"] == SHA
    assert row["release_fingerprint"] == FP
    assert row["prior_baseline_sha256"] == auth["prior_baseline_sha256"]
    _untouched(shared, log_dir, shadow)


def test_reset_failure_rolls_back_authorization_and_env(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)
    original = (shared / ".env").read_text()

    def boom(name):
        if name == "env_written":
            raise RuntimeError("disk")

    with pytest.raises(RuntimeError, match="disk"):
        _reset(shared, log_dir, on_step=boom)
    assert (shared / ".env").read_text() == original
    assert not (shared / "baseline_authorization.json").exists()
    assert not (shared / "baseline_audit.jsonl").exists()


def test_reset_refuses_when_path_a_is_already_configured(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    text = _env(log_dir, schedule="always_on_shadow")
    # The helper above still emits runner pins when schedule isn't current.
    # Build the exact six-tuple explicitly.
    lines = [
        "LIVE_TRADING_ENABLED=false",
        "TRADOVATE_ENV=demo",
        "BROKER=tradovate",
        f"TRADOVATE_PASSWORD={SECRET}",
    ]
    lines.extend(f"{key}={value}" for key, value in PATH_A_PINS.items())
    lines.append(f"LOG_DIR={log_dir}")
    text = "\n".join(lines) + "\n"
    (shared / ".env").write_text(text)
    before = _files(tmp_path)
    code, report = preflight_report(text, shared)
    assert code == 0
    assert "path_a_configured=yes" in report
    assert "reset_needed=no" in report
    with pytest.raises(Refusal, match="path_a_already_configured"):
        _reset(shared, log_dir, env_text=text)
    assert _files(tmp_path) == before
    _untouched(shared, log_dir, shadow)


def test_preflight_reports_when_path_a_is_absent(tmp_path, capsys):
    shared, log_dir, _shadow = _layout(tmp_path)
    code, report = preflight_report((shared / ".env").read_text(), shared)
    assert code == 10
    assert "path_a_configured=no" in report
    assert "reset_needed=yes" in report
    assert "authorized_sha=none" in report
    exit_code = main(["preflight", "--shared-dir", str(shared)])
    captured = capsys.readouterr()
    assert exit_code == 10
    assert "path_a_configured=no" in captured.out
    assert SECRET not in captured.out
    assert SECRET not in captured.err


def test_restore_returns_prior_pins_and_prior_baseline(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    prior = b'{"target_sha": "%s", "schema_version": 1}\n' % OTHER.encode()
    (shared / "baseline_authorization.json").write_bytes(prior)
    original = (shared / ".env").read_text()
    _reset(shared, log_dir, env_text=original)
    restored = restore(
        shared=shared,
        env_text=(shared / ".env").read_text(),
        status=json.loads(json.dumps(FLAT)),
        ticket=TICKET,
        operator_token=f"AUTHORIZE BASELINE RESTORE {TICKET}",
        operator_user="imani",
        clock=lambda: WHEN,
        environ={},
    )
    assert restored["baseline_restore"] == "ok"
    assert (shared / ".env").read_text() == original
    assert (shared / "baseline_authorization.json").read_bytes() == prior
    audit = [json.loads(line) for line in (shared / "baseline_audit.jsonl").read_text().splitlines()]
    assert audit[-1]["action"] == "restore"
    assert audit[-1]["operator_user"] == "imani"
    assert audit[-1]["ticket_id"] == TICKET
    assert audit[-1]["prior_baseline_sha256"]
    _untouched(shared, log_dir, shadow)


def test_restore_removes_authorization_when_there_was_no_prior(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)
    original = (shared / ".env").read_text()
    _reset(shared, log_dir, env_text=original)
    restore(
        shared=shared,
        env_text=(shared / ".env").read_text(),
        status=json.loads(json.dumps(FLAT)),
        ticket=TICKET,
        operator_token=f"AUTHORIZE BASELINE RESTORE {TICKET}",
        operator_user="imani",
        clock=lambda: WHEN,
        environ={},
    )
    assert not (shared / "baseline_authorization.json").exists()
    assert (shared / ".env").read_text() == original


def test_restore_refusals_and_dry_run_write_nothing(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    original_env = (shared / ".env").read_text()
    _reset(shared, log_dir, env_text=original_env)
    current_env = (shared / ".env").read_text()
    current_auth = (shared / "baseline_authorization.json").read_bytes()
    kwargs = dict(
        shared=shared,
        env_text=current_env,
        status=json.loads(json.dumps(FLAT)),
        ticket=TICKET,
        operator_token=f"AUTHORIZE BASELINE RESTORE {TICKET}",
        operator_user="imani",
        clock=lambda: WHEN,
        environ={},
    )
    dry = restore(**kwargs, dry_run=True)
    assert dry["writes"] == "none"
    assert (shared / ".env").read_text() == current_env
    assert (shared / "baseline_authorization.json").read_bytes() == current_auth
    with pytest.raises(Refusal):
        restore(**{**kwargs, "operator_token": "AUTHORIZE BASELINE RESTORE AFS-1", "ticket": "AFS-1"})
    with pytest.raises(Refusal):
        restore(**{**kwargs, "env_text": _env(log_dir, live="true")})
    (shared / "deploy.lock").mkdir()
    with pytest.raises(Refusal, match="deploy lock"):
        restore(**kwargs)
    (shared / "deploy.lock").rmdir()
    with pytest.raises(Refusal):
        restore(**{**kwargs, "status": {**FLAT, "open_orders": ["x"]}})
    with pytest.raises(Refusal):
        restore(**{**kwargs, "environ": {"VARIANTS_START": "on"}})
    assert (shared / ".env").read_text() == current_env
    assert (shared / "baseline_authorization.json").read_bytes() == current_auth
    _untouched(shared, log_dir, shadow)


def test_restore_failure_rolls_back(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)
    original = (shared / ".env").read_text()
    _reset(shared, log_dir, env_text=original)
    current_env = (shared / ".env").read_text()
    current_auth = (shared / "baseline_authorization.json").read_bytes()

    def boom(name):
        if name == "authorization_restored":
            raise RuntimeError("disk")

    with pytest.raises(RuntimeError):
        restore(
            shared=shared,
            env_text=current_env,
            status=json.loads(json.dumps(FLAT)),
            ticket=TICKET,
            operator_token=f"AUTHORIZE BASELINE RESTORE {TICKET}",
            clock=lambda: WHEN,
            environ={},
            on_step=boom,
        )
    assert (shared / ".env").read_text() == current_env
    assert (shared / "baseline_authorization.json").read_bytes() == current_auth


def test_backup_copies_store_journals_pins_and_baseline(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    (shared / "baseline_authorization.json").write_text('{"target_sha": "%s"}\n' % SHA)
    result = backup(
        shared=shared,
        env_text=(shared / ".env").read_text(),
        status=json.loads(json.dumps(FLAT)),
        ticket=TICKET,
        operator_token=f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
        operator_user="imani",
        clock=lambda: WHEN,
        environ={},
    )
    dest = Path(result["path"])
    assert (dest / "order_submissions" / "records" / "one.json").read_text().count(SECRET) == 1
    assert (dest / "journals" / "journal_2026-11-06.jsonl").read_text() == '{"event":"keep"}\n'
    assert (dest / "journals" / "track_b_daily_heartbeat.jsonl").read_text() == "track-b-row\n"
    assert (dest / "journals" / "track_b_heartbeat_state" / "session.json").read_text() == '{"kept":true}\n'
    pins = (dest / "release_pins.txt").read_text()
    assert "EXPECTED_LIVE_COMMIT=" + "c" * 40 in pins
    assert "SCHEDULE_MODE=current" in pins
    assert SECRET not in pins
    assert (dest / "baseline_authorization.json").is_file()
    manifest = json.loads((dest / "manifest.json").read_text())
    assert manifest["operator_user"] == "imani"
    assert manifest["baseline_sha256"] != "absent"
    assert not (dest / "trend_shadow_history.json").exists()
    assert shadow.read_text() == "shadow-history"


def test_backup_dry_run_and_unreadable_store_write_nothing(tmp_path, capsys):
    shared, log_dir, shadow = _layout(tmp_path)
    before = _files(tmp_path)
    dry = backup(
        shared=shared,
        env_text=(shared / ".env").read_text(),
        status=json.loads(json.dumps(FLAT)),
        ticket=TICKET,
        operator_token=f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
        dry_run=True,
        clock=lambda: WHEN,
        environ={},
    )
    assert dry["writes"] == "none"
    assert _files(tmp_path) == before
    code = main([
        "backup", "--shared-dir", str(shared), "--status-file", _status_file(tmp_path),
        "--ticket", TICKET,
        "--operator-token", f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
        "--dry-run",
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    missing = (shared / ".env").read_text().replace(f"LOG_DIR={log_dir}", "LOG_DIR=/tmp/not-a-journal-dir")
    with pytest.raises(Refusal, match="journals unreadable"):
        backup(
            shared=shared,
            env_text=missing,
            status=json.loads(json.dumps(FLAT)),
            ticket=TICKET,
            operator_token=f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
            environ={},
        )
    store = log_dir / "order_submissions"
    os.rename(store, tmp_path / "hidden-store")
    with pytest.raises(Refusal, match="order_submissions unreadable"):
        backup(
            shared=shared,
            env_text=(shared / ".env").read_text(),
            status=json.loads(json.dumps(FLAT)),
            ticket=TICKET,
            operator_token=f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
            environ={},
        )
    os.rename(tmp_path / "hidden-store", store)
    link = store / "records" / "alias.json"
    link.symlink_to(store / "records" / "one.json")
    with pytest.raises(Refusal):
        backup(
            shared=shared,
            env_text=(shared / ".env").read_text(),
            status=json.loads(json.dumps(FLAT)),
            ticket=TICKET,
            operator_token=f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
            environ={},
            clock=lambda: WHEN,
        )
    backups = shared / "backups"
    assert list(backups.glob("predeploy-*")) == []
    assert list(backups.glob(".tmp-*")) == []
    _untouched(shared, log_dir, shadow)


def test_backup_failure_removes_the_partial_directory(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)

    def boom(name):
        if name == "backup_started":
            raise RuntimeError("disk")

    with pytest.raises(RuntimeError):
        backup(
            shared=shared,
            env_text=(shared / ".env").read_text(),
            status=json.loads(json.dumps(FLAT)),
            ticket=TICKET,
            operator_token=f"AUTHORIZE PREDEPLOY BACKUP {TICKET}",
            clock=lambda: WHEN,
            environ={},
            on_step=boom,
        )
    backups = shared / "backups"
    assert not backups.exists() or list(backups.iterdir()) == []


def _payload(commit):
    return {"schema_version": 1, "repo": {"commit": commit}}


def test_check_release_uses_external_digest_not_the_staged_field():
    """The staged fingerprint_sha256 field is not the expected digest.

    A manifest whose field equals the approved digest, but whose payload
    does not, must be refused. Deleting the payload recomputation and
    comparing that field instead makes this assertion fail.
    """
    payload = _payload(SHA)
    digest = staged_manifest_digest(payload)
    assert digest != FP
    lying = {**payload, "fingerprint_sha256": FP}
    approved = json.dumps({"target_sha": SHA, "release_fingerprint": FP})
    assert check_release(SHA, approved, json.dumps(lying)) == 1
    honest = {**payload, "fingerprint_sha256": OTHER_FP}
    external = json.dumps({"target_sha": SHA, "release_fingerprint": digest})
    assert check_release(SHA, external, json.dumps(honest)) == 0
    assert check_release(SHA, approved, json.dumps(honest)) == 1
    other = _payload(OTHER)
    other_record = json.dumps({
        "target_sha": SHA,
        "release_fingerprint": staged_manifest_digest(other),
    })
    assert check_release(SHA, other_record, json.dumps(other)) == 1
    assert check_release(SHA, "{", json.dumps(honest)) == 1
    assert check_release(SHA, json.dumps({"target_sha": SHA}), json.dumps(honest)) == 1
    assert check_release(SHA, external, "{") == 1


def test_reset_cli_stable_flags_are_required(tmp_path, capsys):
    """The published reset argv is exactly these flags.

    Dropping one from the required set lets that invocation exit 0, so the
    assertion on argparse's exit code 2 fails.
    """
    shared, _log_dir, _shadow = _layout(tmp_path)
    status = _status_file(tmp_path)
    values = {
        "--target-sha": SHA,
        "--release-fingerprint": FP,
        "--ticket": TICKET,
        "--approval": str(_sign()[0]),
        "--signature": str(_sign()[1]),
        "--status-file": status,
        "--shared-dir": str(shared),
    }
    assert tuple(values) == RESET_CLI_FLAGS
    argv = ["reset"]
    for flag in RESET_CLI_FLAGS:
        argv.extend((flag, values[flag]))
    argv.append("--dry-run")
    assert main(argv) == 1
    refused = capsys.readouterr()
    assert "allowed_signers missing" in refused.err
    assert "--operator-token" not in refused.out
    with pytest.raises(SystemExit) as caught:
        main(["reset", "--help"])
    assert caught.value.code == 0
    help_text = capsys.readouterr().out
    for flag in RESET_CLI_FLAGS:
        assert flag in help_text
    assert "--operator-token" not in help_text
    assert "--allowed-signers" not in help_text
    required = tuple(flag for flag in RESET_CLI_FLAGS if flag != "--shared-dir")
    for flag in required:
        omitted = ["reset", "--dry-run", "--shared-dir", str(shared)]
        for other in required:
            if other == flag:
                continue
            omitted.extend((other, values[other]))
        with pytest.raises(SystemExit) as missing:
            main(omitted)
        assert missing.value.code == 2


def test_staged_digest_matches_release_integrity():
    from ops.release_integrity import manifest_fingerprint

    payload = _payload(SHA)
    payload["generated_at"] = "2026-11-06T00:00:00+00:00"
    payload["fingerprint_sha256"] = FP
    assert staged_manifest_digest(payload) == manifest_fingerprint(payload)


def test_exposure_census_fails_closed_and_strips_broker_rows():
    class Position:
        open = True

    class Broker:
        def __init__(self, confirmed=True, position=None, orders=None, error=None):
            self.confirmed = confirmed
            self.position = position
            self.orders = orders
            self.error = error

        def get_position_snapshot(self):
            return self.confirmed, self.position

        def _read_broker_list(self, path):
            assert path == "/order/list"
            return self.orders, self.error

    flat = exposure_from_broker(
        Broker(orders=[{"ordStatus": "Filled", "orderType": "Market", "accountId": SECRET}]),
        FLAT["order_submissions"],
    )
    assert flat["has_open_position"] is False
    assert flat["open_orders"] == []
    assert SECRET not in json.dumps(flat)
    open_position = exposure_from_broker(
        Broker(
            position=Position(),
            orders=[{"ordStatus": "Working", "orderType": "Stop", "accountId": SECRET}],
        ),
        FLAT["order_submissions"],
    )
    assert open_position["has_open_position"] is True
    assert open_position["protective_stop_working"] is True
    assert "accountId" not in json.dumps(open_position)
    with pytest.raises(Refusal, match="exposure unreadable"):
        exposure_from_broker(Broker(confirmed=False), FLAT["order_submissions"])
    with pytest.raises(Refusal, match="exposure unreadable"):
        exposure_from_broker(Broker(orders=None, error="down"), FLAT["order_submissions"])


def _fill(result):
    return Fill(
        instrument="MNQ",
        direction="LONG",
        contracts=1,
        entry_price=100.0,
        exit_price=None,
        exit_reason=None,
        result=result,
        pnl_ticks=None,
        pnl_dollars=None,
    )


class _Broker:
    def __init__(self, result="OPEN", explode=False):
        self.calls = []
        self.result = result
        self.explode = explode

    def execute_bracket(self, order):
        self.calls.append(order)
        if self.explode:
            raise RuntimeError("socket")
        return _fill(self.result)


def _place(shared, log_dir, broker, **overrides):
    args = dict(
        broker=broker,
        shared=shared,
        env_text=_env(log_dir),
        status=json.loads(json.dumps(FLAT)),
        ticket=TICKET,
        operator_token=f"AUTHORIZE DEMO RECOVERY PROOF {TICKET}",
        instrument="MNQ",
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        quantity=1,
        operator_user="imani",
        clock=lambda: WHEN,
        environ={},
    )
    args.update(overrides)
    return place_recovery_order(**args)


def test_recovery_place_refusals_do_not_call_the_broker(tmp_path):
    shared, log_dir, shadow = _layout(tmp_path)
    broker = _Broker()
    before = _files(tmp_path)
    cases = [
        dict(operator_token="nope"),
        dict(quantity=2),
        dict(instrument="ES"),
        dict(direction="LONG", stop=110.0),
        dict(env_text=_env(log_dir, live="true")),
        dict(env_text=_env(log_dir, tradovate="live")),
        dict(status={**FLAT, "has_open_position": True}),
        dict(status={**FLAT, "open_orders": [{"ordStatus": "Working"}]}),
        dict(environ={"VARIANTS_START": "V-A"}),
    ]
    (shared / "deploy.lock").mkdir()
    with pytest.raises(Refusal):
        _place(shared, log_dir, broker)
    (shared / "deploy.lock").rmdir()
    for overrides in cases:
        with pytest.raises(Refusal):
            _place(shared, log_dir, broker, **overrides)
    assert broker.calls == []
    assert _files(tmp_path) == before
    _untouched(shared, log_dir, shadow)


def test_recovery_dry_run_does_not_call_the_broker(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)
    broker = _Broker()
    before = _files(tmp_path)
    result = _place(shared, log_dir, broker, dry_run=True)
    assert result["broker_called"] is False
    assert result["writes"] == "none"
    assert broker.calls == []
    assert _files(tmp_path) == before


def test_recovery_place_sends_one_contract_and_check_classifies(tmp_path, capsys):
    shared, log_dir, shadow = _layout(tmp_path)
    broker = _Broker(result="UNKNOWN")
    result = _place(shared, log_dir, broker)
    assert len(broker.calls) == 1
    order = broker.calls[0]
    assert order.contracts == 1
    assert order.strategy == "demo_recovery_proof"
    assert order.client_order_id.startswith("AFSREC")
    assert result["do_not_retry"] == "1"
    assert SECRET not in json.dumps(result)
    evidence = json.loads((shared / "recovery_proof.json").read_text())
    assert evidence["quantity"] == 1
    assert "account" not in evidence
    adopted = {
        **FLAT,
        "has_open_position": True,
        "protective_stop_working": True,
    }
    blocked = {
        **FLAT,
        "order_submissions": {
            "store_trusted": True,
            "entries_blocked": True,
            "recovery_required": True,
            "unresolved": [{"state": "RECONCILE_REQUIRED"}],
        },
    }
    assert classify_recovery(adopted) == "ADOPTED"
    assert classify_recovery(blocked) == "BLOCKED"
    assert classify_recovery({**FLAT, "has_open_position": False}) == "INCONCLUSIVE"
    assert classify_recovery({"order_submissions": {"store_trusted": False}}) == "INCONCLUSIVE"
    status_path = tmp_path / "blocked.json"
    status_path.write_text(json.dumps(blocked))
    code = main([
        "recovery-proof", "check",
        "--shared-dir", str(shared),
        "--status-file", str(status_path),
        "--ticket", TICKET,
        "--operator-token", f"AUTHORIZE DEMO RECOVERY PROOF {TICKET}",
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out.strip() == "recovery_proof=BLOCKED"
    assert SECRET not in captured.out
    _untouched(shared, log_dir, shadow)


def test_recovery_broker_exception_writes_no_evidence(tmp_path):
    shared, log_dir, _shadow = _layout(tmp_path)
    broker = _Broker(explode=True)
    with pytest.raises(Refusal, match="unconfirmed"):
        _place(shared, log_dir, broker)
    assert not (shared / "recovery_proof.json").exists()


def test_shell_wrapper_preflight_prints_no_secrets(tmp_path):
    shared, _log_dir, _shadow = _layout(tmp_path)
    script = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "baseline_reset.sh"
    text = script.read_text()
    assert "systemctl" not in text
    assert not any(line.strip().startswith("source ") for line in text.splitlines())
    proc = subprocess.run(
        ["bash", str(script), "preflight", "--shared-dir", str(shared)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "AFS_PYTHON": sys.executable, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 10
    assert "path_a_configured=no" in proc.stdout
    assert SECRET not in proc.stdout
    assert SECRET not in proc.stderr
    subprocess.run(["bash", "-n", str(script)], check=True)


def test_exposure_census_checks_demo_before_constructing_a_broker():
    source = inspect.getsource(main)
    guard = source.index("require_demo_posture(env_text)")
    broker = source.index("TradovateBroker(")
    assert guard < broker
