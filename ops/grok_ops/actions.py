"""Privileged grok-ops verbs.

Gates run before any sanctioned script. promote and baseline-reset consume a
GO token only after the gates, the SHA ancestry check, and the dependency
check have passed. This module never writes the trading env and never passes
--force-lock.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops.grok_ops.allowlist import CommandRejected, ParsedCommand, parse_command
from ops.grok_ops.dispatcher import _ACTOR
from ops.grok_ops.auditlog import AuditLog
from ops.grok_ops.gates import FlatSnapshot, evaluate_gates, parse_env
from ops.grok_ops.tokens import TokenError, TokenStore

BASELINE_RESET_REL = "scripts/ops/baseline_reset.sh"
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
    calls: list = field(default_factory=list)


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
    if not _is_ancestor(ctx, sha):
        raise ActionError("ancestor")
    release_env = _load_release_env(ctx.release_env)
    token_id = _resolve_promote_token(ctx, token_id, sha)
    record = _consume(ctx, token_id, sha)
    argv = promote_argv(str(ctx.atomic_release), sha)
    return _invoke(ctx, parsed, record, argv, release_env)


def _baseline(ctx: ActionContext, parsed: ParsedCommand, token_id: str, *, dry_run: bool) -> int:
    script = _baseline_script(ctx)
    if dry_run:
        argv = [str(script), "--dry-run"]
        return _invoke(ctx, parsed, {"token_id": "", "ledger_id": "", "sha": ""}, argv, _runner_env({}))
    try:
        if token_id:
            peeked = ctx.token_store.read(token_id)
        else:
            peeked = ctx.token_store.find_single_open()
            token_id = str(peeked["token_id"])
    except TokenError as exc:
        raise ActionError(exc.reason) from exc
    sha = str(peeked.get("sha") or "")
    if not _is_ancestor(ctx, sha):
        raise ActionError("ancestor")
    record = _consume(ctx, token_id, sha)
    argv = [
        str(script),
        "--sha",
        sha,
        "--ledger-id",
        str(record.get("ledger_id") or ""),
    ]
    return _invoke(ctx, parsed, record, argv, _runner_env({}))


def _verify(ctx: ActionContext, parsed: ParsedCommand, token_id: str) -> int:
    env_text, unreadable = _read_text(ctx.env_file)
    if unreadable or env_text is None:
        raise ActionError("verify-unreadable")
    sha = parse_env(env_text).get("EXPECTED_LIVE_COMMIT", "")
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise ActionError("verify-unreadable")
    release_env = _load_release_env(ctx.release_env)
    argv = [str(ctx.atomic_release), "verify", sha]
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
    argv = [str(ctx.atomic_release), "rollback"]
    return _invoke(ctx, parsed, {"token_id": token_id, "ledger_id": "", "sha": ""}, argv, release_env)


def _backup(ctx: ActionContext, parsed: ParsedCommand, token_id: str) -> int:
    release_env = _load_release_env(ctx.release_env)
    if "LOG_DIR" not in release_env or "PROOF_BACKUP_DIR" not in release_env:
        raise ActionError("backup-unconfigured")
    argv = [sys.executable, "-B", "-m", "scripts.backup_proof_data"]
    return _invoke(ctx, parsed, {"token_id": token_id, "ledger_id": "", "sha": ""}, argv, release_env)


def _is_ancestor(ctx: ActionContext, sha: str) -> bool:
    try:
        return bool(ctx.git_ancestor(sha))
    except Exception as exc:
        raise ActionError("ancestor") from exc


def _baseline_script(ctx: ActionContext) -> Path:
    root = ctx.repo_root.resolve()
    script = (root / BASELINE_RESET_REL).resolve()
    if not script.is_file() or not script.is_relative_to(root):
        raise ActionError("dependency-missing")
    return script


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
    env = dict(values)
    env["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _read_text(path: Path) -> tuple[str | None, bool]:
    try:
        return path.read_text(encoding="utf-8"), False
    except OSError:
        return None, True


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
    path = Path(os.environ.get("AFS_OPS_FLAT_FILE", "/var/lib/afs-ops/flat.json"))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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


def _default_runner(argv: list[str], env: dict[str, str]) -> int:
    completed = subprocess.run(argv, env=env, check=False)
    return completed.returncode


def _default_ancestor(sha: str) -> bool:
    repo = os.environ.get("AFS_OPS_GIT_REPO", "/root/autonomous-futures-system")
    try:
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", sha, "origin/main"],
            cwd=repo,
            check=False,
            capture_output=True,
        )
    except OSError:
        return False
    return result.returncode == 0


def context_from_env() -> ActionContext:
    shared = Path(os.environ.get("AFS_SHARED_DIR", "/root/afs-shared"))
    current = Path(os.environ.get("AFS_CURRENT_LINK", "/root/autonomous-futures-system"))
    return ActionContext(
        repo_root=current,
        token_store=TokenStore(Path(os.environ.get("AFS_OPS_TOKEN_DIR", "/var/lib/afs-ops/tokens"))),
        audit=AuditLog(Path(os.environ.get("AFS_OPS_AUDIT_LOG", "/var/lib/afs-ops/audit.log"))),
        env_file=Path(os.environ.get("AFS_OPS_ENV_FILE", shared / ".env")),
        lock_dir=Path(os.environ.get("AFS_OPS_LOCK_DIR", shared / "deploy.lock")),
        variants_file=current / "research" / "trend_r3_forward" / "trend_variants_shadow.py",
        previous_release_file=Path(os.environ.get("AFS_OPS_PREVIOUS_FILE", shared / "current.previous")),
        atomic_release=Path(os.environ.get("AFS_OPS_ATOMIC_RELEASE", current / "scripts" / "atomic_release.sh")),
        release_env=Path(os.environ.get("AFS_OPS_RELEASE_ENV", "/etc/afs-ops/release.env")),
        flat_reader=_default_flat_reader,
        runner=_default_runner,
        actor=os.environ.get("AFS_OPS_ACTOR", "grok-ops"),
        git_ancestor=_default_ancestor,
    )


def main() -> int:
    verb = os.environ.get("AFS_OPS_VERB", "")
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
    ctx = context_from_env()
    actor = payload.get("actor")
    if isinstance(actor, str) and _ACTOR.fullmatch(actor):
        ctx.actor = actor
    return run_verb(parsed, ctx, token_id=str(payload.get("token_id") or ""))


if __name__ == "__main__":
    raise SystemExit(main())
