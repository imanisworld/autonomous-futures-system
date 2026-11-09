"""Privileged grok-ops verbs.

Gates run before any sanctioned script. promote and baseline-reset consume a
GO token only after the gates, the target-release record check, the token
fingerprint check, and the dependency check have passed. promote, verify, and
rollback refuse a git-less tree before that token is consumed, because
scripts/atomic_release.sh cannot run there. This module never writes the
trading env and never passes --force-lock.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.grok_ops.allowlist import CommandRejected, ParsedCommand, parse_command
from ops.grok_ops.auditlog import AuditLog
from ops.grok_ops.dispatcher import _ACTOR
from ops.grok_ops.gates import FlatSnapshot, evaluate_gates, parse_env
from ops.grok_ops.paths import (
    APPROVAL_FILE,
    ATOMIC_REL,
    AUDIT_LOG,
    BASELINE_REL,
    CURRENT_LINK,
    DEPLOY_LOCK_REL,
    ENV_FILE,
    EXPOSURE_FILE,
    FLAT_FILE,
    INSTALLED_CODE,
    LOCK_DIR,
    PREVIOUS_FILE,
    PYTHON,
    RELEASES_PREFIX,
    RELEASE_ENV,
    SHARED_DIR,
    SIGNATURE_FILE,
    TOKEN_DIR,
    VARIANTS_REL,
    OwnershipError,
    PinnedRelease,
    assert_directory,
    file_identity,
    hold_directory,
    open_verified,
    pin_release,
    read_verified,
)
from ops.grok_ops.tokens import TokenError, TokenStore

BASELINE_RESET_REL = BASELINE_REL
_RELEASE_ENV_KEYS = frozenset(
    {
        "AFS_BOX",
        "AFS_SHARED_DIR",
        "AFS_RELEASES_DIR",
        "AFS_CURRENT_LINK",
        "AFS_SERVICE",
        "RELEASE_CI_PROOF",
        "AFS_BOX_PYTHON",
        "LOG_DIR",
        "PROOF_BACKUP_DIR",
    }
)
_FORBIDDEN_ENV_PARTS = (
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "LIVE_TRADING",
    "TRADOVATE",
    "BROKER",
)


class ActionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class ActionContext:
    repo_root: Path
    token_store: TokenStore
    audit: AuditLog
    env_file: Path
    lock_dir: Path
    variants_file: Path
    previous_release_file: Path
    atomic_release: Path
    release_env: Path
    flat_reader: Callable[[], FlatSnapshot]
    runner: Callable[[list[str], dict[str, str]], int]
    actor: str
    git_ancestor: Callable[[str], bool]
    expected_uid: int
    release_pin: PinnedRelease
    calls: list = field(default_factory=list)
    python: str = PYTHON
    release_prefix: str = RELEASES_PREFIX
    releases_root: str = RELEASES_PREFIX
    shared_root: str = SHARED_DIR
    exec_pin: PinnedRelease | None = None
    fstat: Callable = os.fstat


def baseline_reset_argv(
    script: str,
    *,
    sha: str,
    ledger_id: str,
    release_fingerprint: str,
    dry_run: bool,
    approval: str = APPROVAL_FILE,
    signature: str = SIGNATURE_FILE,
) -> list[str]:
    # The digest and the signed files come from the operator. Do not hash the
    # tree and do not build or sign an approval record here.
    argv = [
        script,
        "reset",
        "--target-sha",
        sha,
        "--release-fingerprint",
        release_fingerprint,
        "--ticket",
        ledger_id,
        "--approval",
        approval,
        "--signature",
        signature,
        "--status-file",
        EXPOSURE_FILE,
        "--shared-dir",
        SHARED_DIR,
    ]
    if dry_run:
        argv.append("--dry-run")
    return argv


def promote_argv(atomic: str, sha: str) -> list[str]:
    argv = [atomic, "promote", sha]
    if "--force-lock" in argv:
        raise ActionError("force-lock")
    return argv


def run_verb(parsed: ParsedCommand, ctx: ActionContext, token_id: str = "") -> int:
    try:
        return _run(parsed, ctx, token_id)
    except ActionError as exc:
        _audit(ctx, parsed, token_id, f"refused:{exc.code}", "")
        if exc.code == "dependency-missing":
            return 78
        return 3
    except TokenError as exc:
        _audit(ctx, parsed, token_id, f"refused:{exc.reason}", "")
        return 3


def _run(parsed: ParsedCommand, ctx: ActionContext, token_id: str) -> int:
    status = _gate_status(ctx)
    if not status.ok:
        raise ActionError(",".join(status.refusals))
    if parsed.verb in {"preflight", "status"}:
        _audit(ctx, parsed, token_id, "ok", "")
        return 0
    if parsed.verb == "backup":
        return _backup(ctx, parsed, token_id)
    if parsed.verb == "verify":
        return _verify(ctx, parsed, token_id)
    if parsed.verb == "rollback":
        return _rollback(ctx, parsed, token_id)
    if parsed.verb == "baseline-reset-dry-run":
        return _baseline(ctx, parsed, token_id, dry_run=True)
    if parsed.verb == "baseline-reset":
        return _baseline(ctx, parsed, token_id, dry_run=False)
    if parsed.verb == "promote":
        return _promote(ctx, parsed, token_id)
    raise ActionError("unknown-verb")


def _gate_status(ctx: ActionContext):
    env_text, env_unreadable = _read_text(ctx.env_file)
    variants_text, variants_unreadable = _read_text(ctx.variants_file)
    try:
        lock_exists: bool | None = ctx.lock_dir.exists()
    except OSError:
        lock_exists = None
    try:
        flat = ctx.flat_reader()
    except Exception:
        flat = None
    return evaluate_gates(
        env_text=env_text,
        env_unreadable=env_unreadable,
        lock_exists=lock_exists,
        variants_text=variants_text,
        variants_unreadable=variants_unreadable,
        flat=flat,
    )


def _promote(ctx: ActionContext, parsed: ParsedCommand, token_id: str) -> int:
    sha = parsed.sha or ""
    target = prove_target_release(
        ctx.releases_root, ctx.shared_root, sha, uid=ctx.expected_uid, fstat=ctx.fstat
    )
    ctx.exec_pin = target
    try:
        atomic = _pinned_script(ctx, ATOMIC_REL)
        _pinned_script(ctx, DEPLOY_LOCK_REL)
        release_env = _load_release_env(ctx.release_env)
        token_id = _resolve_promote_token(ctx, token_id, sha)
        _require_token_fingerprint(ctx, token_id)
        _require_supported_release_model(ctx)
        record = _consume(ctx, token_id, sha)
        argv = promote_argv(atomic, sha)
        return _invoke(ctx, parsed, record, argv, release_env)
    finally:
        ctx.exec_pin = None
        target.close()


def _baseline(ctx: ActionContext, parsed: ParsedCommand, token_id: str, *, dry_run: bool) -> int:
    try:
        peeked = _peek_token(ctx, token_id)
    except TokenError as exc:
        raise ActionError(exc.reason) from exc
    sha = str(peeked.get("sha") or "")
    ledger_id = str(peeked.get("ledger_id") or "")
    fingerprint = peeked.get("release_fingerprint")
    token_id = str(peeked.get("token_id") or token_id)
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ActionError("fingerprint-missing")
    if any(char not in "0123456789abcdef" for char in fingerprint):
        raise ActionError("fingerprint-missing")
    target = prove_target_release(
        ctx.releases_root, ctx.shared_root, sha, uid=ctx.expected_uid, fstat=ctx.fstat
    )
    ctx.exec_pin = target
    try:
        script = _baseline_script(ctx)
        _require_token_fingerprint(ctx, token_id, expected=fingerprint)
        argv = baseline_reset_argv(
            str(script),
            sha=sha,
            ledger_id=ledger_id,
            release_fingerprint=fingerprint,
            dry_run=dry_run,
            approval=APPROVAL_FILE,
            signature=SIGNATURE_FILE,
        )
        if dry_run:
            return _invoke(ctx, parsed, peeked, argv, _runner_env({}))
        record = _consume(ctx, token_id, sha)
        return _invoke(ctx, parsed, record, argv, _runner_env({}))
    finally:
        ctx.exec_pin = None
        target.close()


def _verify(ctx: ActionContext, parsed: ParsedCommand, token_id: str) -> int:
    env_text, unreadable = _read_text(ctx.env_file)
    if unreadable or env_text is None:
        raise ActionError("verify-unreadable")
    sha = parse_env(env_text).get("EXPECTED_LIVE_COMMIT", "")
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ActionError("verify-unreadable")
    release_env = _load_release_env(ctx.release_env)
    atomic = _pinned_script(ctx, ATOMIC_REL)
    _pinned_script(ctx, DEPLOY_LOCK_REL)
    _require_supported_release_model(ctx)
    argv = [atomic, "verify", sha]
    if "--force-lock" in argv:
        raise ActionError("force-lock")
    return _invoke(ctx, parsed, {"token_id": token_id, "ledger_id": "", "sha": sha}, argv, release_env)


def _rollback(ctx: ActionContext, parsed: ParsedCommand, token_id: str) -> int:
    text, unreadable = _read_text(ctx.previous_release_file)
    if unreadable or text is None:
        raise ActionError("rollback-unreadable")
    recorded = Path(text.strip()).name
    if recorded != parsed.release:
        raise ActionError("rollback-mismatch")
    release_env = _load_release_env(ctx.release_env)
    atomic = _pinned_script(ctx, ATOMIC_REL)
    _pinned_script(ctx, DEPLOY_LOCK_REL)
    _require_supported_release_model(ctx)
    argv = [atomic, "rollback"]
    return _invoke(ctx, parsed, {"token_id": token_id, "ledger_id": "", "sha": ""}, argv, release_env)


def _backup(ctx: ActionContext, parsed: ParsedCommand, token_id: str) -> int:
    release_env = _load_release_env(ctx.release_env)
    if "LOG_DIR" not in release_env or "PROOF_BACKUP_DIR" not in release_env:
        raise ActionError("backup-unconfigured")
    root = ctx.release_pin.real.rstrip("/")
    env = dict(release_env)
    env["PYTHONPATH"] = root
    argv = [ctx.python, "-B", "-P", "-m", "scripts.backup_proof_data"]
    return _invoke(ctx, parsed, {"token_id": token_id, "ledger_id": "", "sha": ""}, argv, env)


def _peek_token(ctx: ActionContext, token_id: str) -> dict:
    if token_id:
        return ctx.token_store.read(token_id)
    return ctx.token_store.find_single_open()


def _manifest_fingerprint(ctx: ActionContext) -> str:
    pin = _active_pin(ctx)
    try:
        raw = read_verified(
            pin.real,
            "release_manifest.json",
            uid=ctx.expected_uid,
            pin=pin,
            fstat=ctx.fstat,
        )
        manifest = json.loads(raw)
    except (FileNotFoundError, OwnershipError, json.JSONDecodeError, UnicodeError) as exc:
        if isinstance(exc, OwnershipError) and exc.code == "release-symlink":
            raise ActionError("release-symlink") from exc
        raise ActionError("record-mismatch") from exc
    fingerprint = manifest.get("fingerprint_sha256") if isinstance(manifest, dict) else None
    if not isinstance(fingerprint, str):
        raise ActionError("record-mismatch")
    return fingerprint


def _require_token_fingerprint(
    ctx: ActionContext,
    token_id: str,
    expected: str | None = None,
) -> None:
    """Bind the GO token's fingerprint to the target manifest before consume."""
    if expected is None:
        try:
            peeked = _peek_token(ctx, token_id)
        except TokenError as exc:
            raise ActionError(exc.reason) from exc
        expected = peeked.get("release_fingerprint")
    if not isinstance(expected, str) or expected != _manifest_fingerprint(ctx):
        raise ActionError("record-mismatch")


def _require_supported_release_model(ctx: ActionContext) -> None:
    """Refuse a tree scripts/atomic_release.sh cannot use as its git ROOT.

    That script assigns ROOT from ``git rev-parse --show-toplevel``. A git-less
    release exits 128 there. Refuse before a GO token is consumed. A directory
    nested inside some other repository is the same refusal: ROOT would not be
    this release.
    """
    pin = _active_pin(ctx)
    pin.reverify()
    try:
        git_fd = os.open(".git", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=pin.dirfd)
    except OSError as exc:
        raise ActionError("unsupported-release-model") from exc
    else:
        os.close(git_fd)
    try:
        result = subprocess.run(
            ["git", "-C", pin.real, "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            env=_runner_env({}),
        )
    except OSError as exc:
        raise ActionError("unsupported-release-model") from exc
    if result.returncode != 0:
        raise ActionError("unsupported-release-model")
    try:
        top = os.stat((result.stdout or "").strip(), follow_symlinks=True)
        here = os.stat(pin.real, follow_symlinks=True)
    except OSError as exc:
        raise ActionError("unsupported-release-model") from exc
    if (top.st_dev, top.st_ino) != (here.st_dev, here.st_ino):
        raise ActionError("unsupported-release-model")


def _active_pin(ctx: ActionContext) -> PinnedRelease:
    return ctx.exec_pin if ctx.exec_pin is not None else ctx.release_pin


def _pinned_script(ctx: ActionContext, relative: str) -> str:
    pin = _active_pin(ctx)
    try:
        return open_verified(
            pin.real,
            relative,
            uid=ctx.expected_uid,
            pin=pin,
        )
    except FileNotFoundError as exc:
        raise ActionError("dependency-missing") from exc
    except OwnershipError as exc:
        raise ActionError(exc.code) from exc


def _baseline_script(ctx: ActionContext) -> Path:
    return Path(_pinned_script(ctx, BASELINE_RESET_REL))


def prove_target_release(
    releases_root: str,
    shared_root: str,
    sha: str,
    *,
    uid: int,
    fstat=os.fstat,
) -> PinnedRelease:
    """Authorize sha from the build's release-complete record and manifest.

    The directory name and any caller environment are not evidence. A symlink
    in the release path or its ancestors is refused by the directory walk.
    """
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ActionError("ancestor")
    target = releases_root.rstrip("/") + "/" + sha
    try:
        pin = hold_directory(target, uid=uid, fstat=fstat)
    except OwnershipError as exc:
        if exc.code == "release-symlink":
            raise ActionError("release-symlink") from exc
        raise ActionError("ancestor") from exc
    try:
        try:
            raw = read_verified(pin.real, "release_manifest.json", uid=uid, pin=pin, fstat=fstat)
            manifest = json.loads(raw)
        except (FileNotFoundError, OwnershipError, json.JSONDecodeError, UnicodeError) as exc:
            if isinstance(exc, OwnershipError) and exc.code == "release-symlink":
                raise ActionError("release-symlink") from exc
            raise ActionError("record-mismatch") from exc
        if not isinstance(manifest, dict):
            raise ActionError("record-mismatch")
        repo = manifest.get("repo")
        commit = repo.get("commit") if isinstance(repo, dict) else None
        fingerprint = manifest.get("fingerprint_sha256")
        if commit != sha or not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise ActionError("record-mismatch")
        if any(char not in "0123456789abcdef" for char in fingerprint):
            raise ActionError("record-mismatch")
        try:
            shared = hold_directory(shared_root, uid=uid, fstat=fstat)
        except OwnershipError as exc:
            if exc.code == "release-symlink":
                raise ActionError("release-symlink") from exc
            raise ActionError("record-mismatch") from exc
        try:
            recorded = read_verified(
                shared.real,
                "release-complete/" + sha,
                uid=uid,
                pin=shared,
                fstat=fstat,
            )
        except (FileNotFoundError, OwnershipError) as exc:
            if isinstance(exc, OwnershipError) and exc.code == "release-symlink":
                raise ActionError("release-symlink") from exc
            raise ActionError("record-mismatch") from exc
        finally:
            shared.close()
        try:
            recorded_text = recorded.decode("utf-8")
        except UnicodeError as exc:
            raise ActionError("record-mismatch") from exc
        if recorded_text.strip() != fingerprint:
            raise ActionError("record-mismatch")
        return pin
    except Exception:
        pin.close()
        raise


def _resolve_promote_token(ctx: ActionContext, token_id: str, sha: str) -> str:
    if token_id:
        return token_id
    try:
        return ctx.token_store.find_open_for_sha(sha)
    except TokenError as exc:
        raise ActionError(exc.reason) from exc


def _consume(ctx: ActionContext, token_id: str, sha: str) -> dict:
    if not token_id:
        raise ActionError("token-missing")
    try:
        return ctx.token_store.consume(token_id, sha)
    except TokenError as exc:
        raise ActionError(exc.reason) from exc


def _invoke(ctx: ActionContext, parsed: ParsedCommand, record: dict, argv: list[str], env: dict[str, str]) -> int:
    if "--force-lock" in argv:
        raise ActionError("force-lock")
    if argv and not argv[0].startswith("/"):
        raise ActionError("relative-program")
    _active_pin(ctx).reverify()
    code = ctx.runner(argv, env)
    result = "ok" if code == 0 else "failed"
    _audit(
        ctx,
        parsed,
        str(record.get("token_id") or ""),
        result,
        str(record.get("ledger_id") or ""),
        sha=str(record.get("sha") or parsed.sha or ""),
    )
    return code


def _load_release_env(path: Path) -> dict[str, str]:
    text, unreadable = _read_text(path)
    if unreadable or text is None:
        raise ActionError("release-env-unreadable")
    values = parse_env(text)
    for key in values:
        upper = key.upper()
        if key not in _RELEASE_ENV_KEYS or any(part in upper for part in _FORBIDDEN_ENV_PARTS):
            raise ActionError("release-env-forbidden")
    if not values.get("AFS_BOX"):
        raise ActionError("release-env-incomplete")
    return _runner_env(values)


def _runner_env(values: dict[str, str]) -> dict[str, str]:
    env = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for key, value in values.items():
        if key.startswith("AFS_OPS_") or key in {"LD_PRELOAD", "PYTHONPATH", "BASH_ENV", "ENV"}:
            raise ActionError("env-forbidden")
        env[key] = value
    return env


def _read_text(path: Path) -> tuple[str | None, bool]:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None, True
    try:
        current = os.fstat(fd)
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            return None, True
        return os.read(fd, 1_000_000).decode("utf-8"), False
    except (OSError, UnicodeError):
        return None, True
    finally:
        os.close(fd)


def _audit(
    ctx: ActionContext,
    parsed: ParsedCommand,
    token_id: str,
    result: str,
    ledger_id: str,
    sha: str | None = None,
) -> None:
    ctx.audit.append(
        actor=ctx.actor,
        verb=parsed.verb,
        sha=parsed.sha if sha is None else sha,
        token_id=token_id,
        result=result,
        ledger_id=ledger_id,
    )


def _default_flat_reader() -> FlatSnapshot:
    try:
        text, unreadable = _read_text(Path(FLAT_FILE))
        if unreadable or text is None:
            raise OSError("flatness snapshot unreadable")
        data = json.loads(text)
        checked = datetime.fromisoformat(str(data["checked_at"]))
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - checked > timedelta(seconds=120):
            raise OSError("stale flatness snapshot")
        return FlatSnapshot(
            positions_readable=bool(data["positions_readable"]),
            open_positions=int(data["open_positions"]),
            orders_readable=bool(data["orders_readable"]),
            working_orders=int(data["working_orders"]),
        )
    except Exception as exc:
        raise OSError("flatness snapshot unreadable") from exc


def _ancestor_in(repo: str) -> Callable[[str], bool]:
    def _check(sha: str) -> bool:
        try:
            result = subprocess.run(
                ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
                cwd=repo,
                env=_runner_env({}),
                check=False,
                capture_output=True,
            )
        except OSError:
            return False
        return result.returncode == 0

    return _check


def require_same_file(running: str, installed: str, *, uid: int, fstat=os.fstat) -> None:
    """Refuse when the running file is not the installed file."""
    if file_identity(running, uid=uid, fstat=fstat) != file_identity(installed, uid=uid, fstat=fstat):
        raise OwnershipError("code-path")


def production_context() -> ActionContext:
    """Build the root context from fixed paths. Ignores the process environment."""
    code = __file__
    if not isinstance(code, str) or not code.startswith("/"):
        raise OwnershipError("code-path")
    installed = INSTALLED_CODE + "/ops/grok_ops/actions.py"
    require_same_file(code, installed, uid=0)
    assert_directory(INSTALLED_CODE, uid=0)
    release = pin_release(CURRENT_LINK, RELEASES_PREFIX, uid=0)
    try:
        release.reverify()
        assert_directory(TOKEN_DIR, uid=0)
        assert_directory(os.path.dirname(AUDIT_LOG), uid=0)
        for fixed in (ENV_FILE, RELEASE_ENV, FLAT_FILE, EXPOSURE_FILE, PREVIOUS_FILE):
            if os.path.lexists(fixed):
                assert_directory(os.path.dirname(fixed), uid=0)
                file_identity(fixed, uid=0)
        root = Path(release.real)
        ctx = ActionContext(
            repo_root=root,
            token_store=TokenStore(Path(TOKEN_DIR)),
            audit=AuditLog(Path(AUDIT_LOG)),
            env_file=Path(ENV_FILE),
            lock_dir=Path(LOCK_DIR),
            variants_file=root / VARIANTS_REL,
            previous_release_file=Path(PREVIOUS_FILE),
            atomic_release=root / ATOMIC_REL,
            release_env=Path(RELEASE_ENV),
            flat_reader=_default_flat_reader,
            runner=lambda argv, env: 3,
            actor="grok-ops",
            git_ancestor=_ancestor_in(release.real),
            expected_uid=0,
            release_pin=release,
            python=PYTHON,
            release_prefix=RELEASES_PREFIX,
        )
        ctx.runner = _production_runner(ctx)
        return ctx
    except Exception:
        release.close()
        raise


def _production_runner(ctx: ActionContext):
    def run(argv: list[str], env: dict[str, str]) -> int:
        pin = _active_pin(ctx)
        pin.reverify()
        if not argv or not argv[0].startswith("/"):
            return 3
        root = pin.real.rstrip("/")
        if argv[0] != ctx.python and not argv[0].startswith(root + "/"):
            return 3
        completed = subprocess.run(argv, env=env, cwd=root, check=False)
        return completed.returncode

    return run


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verb = args[0] if args else ""
    payload = json.loads(sys.stdin.read() or "{}")
    if payload.get("verb") not in (None, "", verb):
        print("verb mismatch", file=sys.stderr)
        return 2
    sha = str(payload.get("sha") or "")
    release = str(payload.get("release") or "")
    if verb == "promote":
        raw = f"promote {sha}"
    elif verb == "rollback":
        raw = f"rollback {release}"
    else:
        raw = verb
    try:
        parsed = parse_command(raw)
    except CommandRejected as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    try:
        ctx = production_context()
    except OwnershipError as exc:
        print(exc.code, file=sys.stderr)
        return 3
    actor = payload.get("actor")
    if isinstance(actor, str) and _ACTOR.fullmatch(actor):
        ctx.actor = actor
    return run_verb(parsed, ctx, token_id=str(payload.get("token_id") or ""))


if __name__ == "__main__":
    raise SystemExit(main())
