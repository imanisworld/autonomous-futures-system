"""Operator-authorized futures baseline reset.

Records one exact release SHA, sets only the six Path A posture pins, and
can restore those pins to the values they had before the reset. It does not
edit broker routing, risk limits, or LIVE flags, and it does not restart
units. Track B files and trend-shadow history are not opened.

The promote gate reads the authorization file through
``ops.baseline_authorization``. This module is the only writer.

Stable reset argv, which other callers align to::

    reset --target-sha --release-fingerprint --ticket --approval --signature --status-file [--shared-dir]
"""

from __future__ import annotations

import argparse
import getpass
import hmac
import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from execution.broker_interface import BracketOrder, Fill
from execution.order_submission_store import OrderSubmissionStore
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from ops.release_approval import (
    ALLOWED_SIGNERS_PATH,
    ApprovalError,
    verify_reset_approval,
)
from ops.baseline_authorization import (
    AUDIT_FILENAME,
    AUTH_FILENAME,
    FINGERPRINT_RE,
    PATH_A_PINS,
    RELEASE_PIN_KEYS,
    SHA_RE,
    TICKET_RE,
    apply_pin_updates,
    env_assignments,
    env_values,
    non_pin_assignments,
    path_a_configured,
    sha256_bytes,
)

JOURNAL_NAMES = ("errors.log", "track_b_daily_heartbeat.jsonl")
BLOCKING_SUBMISSION_STATES = frozenset({
    "AMBIGUOUS",
    "RECONCILE_REQUIRED",
    "SUBMITTED",
    "PARTIALLY_FILLED",
})
# Names are stable. #1258 aligns to this list. A rename or a new required
# flag is an interface change.
RESET_CLI_FLAGS = (
    "--target-sha",
    "--release-fingerprint",
    "--ticket",
    "--approval",
    "--signature",
    "--status-file",
    "--shared-dir",
)

TERMINAL_ORDER_STATUSES = frozenset({
    "filled",
    "canceled",
    "cancelled",
    "rejected",
    "expired",
})


class Refusal(Exception):
    """A guard refused the operation. Nothing the operator asked for was kept."""


def _step(on_step: Callable[[str], None] | None, name: str) -> None:
    if on_step is not None:
        on_step(name)


def _user(operator_user: str | None) -> str:
    if operator_user is not None:
        if not operator_user or any(char in operator_user for char in "\n\r"):
            raise Refusal("operator identity unreadable")
        return operator_user
    try:
        user = getpass.getuser()
    except Exception as exc:
        raise Refusal("operator identity unreadable") from exc
    if not user or any(char in user for char in "\n\r"):
        raise Refusal("operator identity unreadable")
    return user


def _approval_now(clock: Callable[[], datetime] | None) -> datetime:
    moment = clock() if clock is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise Refusal("clock must be timezone-aware")
    return moment


def _stamp(clock: Callable[[], datetime] | None) -> str:
    moment = clock() if clock is not None else datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise Refusal("clock must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_compact(stamp: str) -> str:
    return stamp.replace("-", "").replace(":", "")


def require_sha(sha: str) -> str:
    if not isinstance(sha, str) or SHA_RE.fullmatch(sha) is None:
        raise Refusal("target sha refused")
    return sha


def require_fingerprint(fingerprint: str) -> str:
    if not isinstance(fingerprint, str) or FINGERPRINT_RE.fullmatch(fingerprint) is None:
        raise Refusal("release fingerprint refused")
    return fingerprint


def require_ticket(ticket: str) -> str:
    if not isinstance(ticket, str) or TICKET_RE.fullmatch(ticket) is None:
        raise Refusal("ticket refused")
    return ticket


def require_operator_token(token: str, expected: str) -> None:
    if not isinstance(token, str) or not token:
        raise Refusal("operator token refused")
    if not hmac.compare_digest(token, expected):
        raise Refusal("operator token refused")


def require_demo_posture(env_text: str) -> None:
    live_values = [value for key, value in env_assignments(env_text) if key == "LIVE_TRADING_ENABLED"]
    demo_values = [value for key, value in env_assignments(env_text) if key == "TRADOVATE_ENV"]
    if live_values != ["false"]:
        raise Refusal("LIVE_TRADING_ENABLED must be false")
    if demo_values != ["demo"]:
        raise Refusal("TRADOVATE_ENV must be demo")


def require_trusted_shared_dir(path: Path) -> Path:
    """Optional shared dir: absolute, no ``..``, and no symlink component.

    The default is ``AFS_SHARED_DIR`` or ``/root/afs-shared``. A symlink,
    including a symlink in a parent directory, is refused so the path cannot
    be redirected onto another tree. The allowed_signers file is not loaded
    from this directory.
    """
    if not path.is_absolute():
        raise Refusal("shared-dir must be absolute")
    if any(part == ".." for part in path.parts):
        raise Refusal("shared-dir must not contain ..")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise Refusal("shared-dir must not be a symlink")
    if not path.is_dir():
        raise Refusal("shared-dir unreadable")
    return path


def require_lock_clear(shared: Path) -> None:
    if (shared / "deploy.lock").exists():
        raise Refusal("deploy lock held")


def require_flat(status: object) -> None:
    if not isinstance(status, dict):
        raise Refusal("exposure unreadable")
    if "has_open_position" not in status or "open_orders" not in status:
        raise Refusal("exposure unreadable")
    if status.get("has_open_position") is not False:
        raise Refusal("open position")
    orders = status.get("open_orders")
    if not isinstance(orders, list):
        raise Refusal("exposure unreadable")
    if orders:
        raise Refusal("open order")
    submissions = status.get("order_submissions")
    if not isinstance(submissions, dict):
        raise Refusal("exposure unreadable")
    if submissions.get("store_trusted") is not True:
        raise Refusal("exposure unreadable")
    if submissions.get("entries_blocked") is not False or submissions.get("recovery_required") is not False:
        raise Refusal("unresolved submission")


def require_variants_inactive(env_text: str, environ: Mapping[str, str]) -> None:
    values = []
    parsed = env_values(env_text)
    if "VARIANTS_START" in parsed:
        values.append(parsed["VARIANTS_START"])
    if "VARIANTS_START" in environ:
        values.append(environ["VARIANTS_START"])
    for value in values:
        if str(value).strip() not in {"", "None"}:
            raise Refusal("shadow variants active")


def enforce_runtime_guards(
    *,
    env_text: str,
    shared: Path,
    status: object,
    environ: Mapping[str, str],
) -> None:
    require_demo_posture(env_text)
    require_lock_clear(shared)
    require_flat(status)
    require_variants_inactive(env_text, environ)


def _read_env(path: Path) -> str:
    try:
        return path.read_text()
    except OSError as exc:
        raise Refusal("env unreadable") from exc


def _load_status(path: Path) -> dict:
    try:
        if str(path) == "-":
            raw = sys.stdin.read()
        else:
            raw = path.read_text()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal("exposure unreadable") from exc
    if not isinstance(payload, dict):
        raise Refusal("exposure unreadable")
    return payload


def _limited(before: str, after: str) -> None:
    allowed = set(PATH_A_PINS)
    if non_pin_assignments(before, allowed) != non_pin_assignments(after, allowed):
        raise Refusal("refusing to edit non-posture keys")


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    except Exception:
        os.close(fd)
        temporary.unlink(missing_ok=True)
        raise
    os.close(fd)
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _append_audit(path: Path, record: dict) -> None:
    line = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, line)
        os.fsync(fd)
    finally:
        os.close(fd)


def _truncate(path: Path, size: int) -> None:
    if size == 0 and not path.exists():
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        os.ftruncate(fd, size)
        os.fsync(fd)
    finally:
        os.close(fd)


def _dump(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()


def _prior_pins(env_text: str) -> dict[str, str]:
    values = env_values(env_text)
    return {key: values.get(key, "") for key in PATH_A_PINS}


def _backup_dir(shared: Path, stamp: str) -> Path:
    return shared / "baseline_reset_backups" / _stamp_compact(stamp)


def _rollback_files(
    *,
    env_path: Path,
    auth_path: Path,
    audit_path: Path,
    prior_env: bytes,
    prior_auth: bytes | None,
    audit_size: int,
) -> None:
    atomic_write_bytes(env_path, prior_env)
    if prior_auth is None:
        auth_path.unlink(missing_ok=True)
    else:
        atomic_write_bytes(auth_path, prior_auth)
    _truncate(audit_path, audit_size)


def reset(
    *,
    shared: Path,
    env_text: str,
    status: dict,
    env_path: Path | None = None,
    target_sha: str,
    release_fingerprint: str,
    ticket: str,
    approval_path: Path,
    signature_path: Path,
    allowed_signers: Path = ALLOWED_SIGNERS_PATH,
    signer_stat: object | None = None,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    operator_user: str | None = None,
    clock: Callable[[], datetime] | None = None,
    on_step: Callable[[str], None] | None = None,
) -> dict:
    require_trusted_shared_dir(shared)
    sha = require_sha(target_sha)
    fingerprint = require_fingerprint(release_fingerprint)
    ticket_id = require_ticket(ticket)
    try:
        signer = verify_reset_approval(
            approval_path=approval_path,
            signature_path=signature_path,
            allowed_signers=allowed_signers,
            target_sha=sha,
            manifest_digest=fingerprint,
            ticket=ticket_id,
            now=_approval_now(clock),
            signer_stat=signer_stat,
        )
    except ApprovalError as exc:
        raise Refusal(str(exc)) from exc
    enforce_runtime_guards(
        env_text=env_text,
        shared=shared,
        status=status,
        environ=os.environ if environ is None else environ,
    )
    if path_a_configured(env_text):
        raise Refusal("path_a_already_configured")
    updated = apply_pin_updates(env_text, PATH_A_PINS)
    _limited(env_text, updated)
    if not path_a_configured(updated):
        raise Refusal("path a pins would not be exact")
    auth_path = shared / AUTH_FILENAME
    prior_auth = auth_path.read_bytes() if auth_path.is_file() else None
    prior_hash = sha256_bytes(prior_auth) if prior_auth is not None else ""
    if dry_run:
        return {
            "dry_run": "reset",
            "target_sha": sha,
            "release_fingerprint": fingerprint,
            "ticket_id": ticket_id,
            "prior_baseline_sha256": prior_hash or "none",
            "writes": "none",
        }
    user = _user(operator_user)
    stamp = _stamp(clock)
    record = {
        "schema_version": 1,
        "action": "reset",
        "target_sha": sha,
        "release_fingerprint": fingerprint,
        "ticket_id": ticket_id,
        "operator_user": user,
        "signer": signer,
        "recorded_at": stamp,
        "prior_baseline_sha256": prior_hash,
        "prior_pins": _prior_pins(env_text),
        "prior_authorization_backup": "",
    }
    env_path = shared / ".env" if env_path is None else env_path
    audit_path = shared / AUDIT_FILENAME
    prior_env = env_text.encode()
    audit_size = audit_path.stat().st_size if audit_path.exists() else 0
    backup = _backup_dir(shared, stamp)
    try:
        backup.mkdir(parents=True, exist_ok=False)
        atomic_write_bytes(backup / ".env", prior_env)
        if prior_auth is not None:
            backup_name = f"baseline_authorization.json.bak-{_stamp_compact(stamp)}"
            atomic_write_bytes(shared / backup_name, prior_auth)
            atomic_write_bytes(backup / AUTH_FILENAME, prior_auth)
            record["prior_authorization_backup"] = backup_name
        _step(on_step, "backed_up")
        auth_bytes = _dump(record)
        atomic_write_bytes(auth_path, auth_bytes)
        _step(on_step, "authorization_written")
        atomic_write_bytes(env_path, updated.encode())
        _step(on_step, "env_written")
        _append_audit(audit_path, {
            "action": "reset",
            "operator_user": user,
            "signer": signer,
            "recorded_at": stamp,
            "target_sha": sha,
            "release_fingerprint": fingerprint,
            "ticket_id": ticket_id,
            "prior_baseline_sha256": prior_hash,
        })
        _step(on_step, "audit_written")
    except Exception:
        _rollback_files(
            env_path=env_path,
            auth_path=auth_path,
            audit_path=audit_path,
            prior_env=prior_env,
            prior_auth=prior_auth,
            audit_size=audit_size,
        )
        raise
    return {
        "baseline_reset": "ok",
        "target_sha": sha,
        "release_fingerprint": fingerprint,
        "ticket_id": ticket_id,
        "operator_user": user,
        "recorded_at": stamp,
        "prior_baseline_sha256": prior_hash or "none",
        "authorization_sha256": sha256_bytes(auth_bytes),
        "audit": "appended",
    }


def restore(
    *,
    shared: Path,
    env_text: str,
    status: dict,
    env_path: Path | None = None,
    ticket: str,
    operator_token: str,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    operator_user: str | None = None,
    clock: Callable[[], datetime] | None = None,
    on_step: Callable[[str], None] | None = None,
) -> dict:
    ticket_id = require_ticket(ticket)
    require_operator_token(operator_token, f"AUTHORIZE BASELINE RESTORE {ticket_id}")
    enforce_runtime_guards(
        env_text=env_text,
        shared=shared,
        status=status,
        environ=os.environ if environ is None else environ,
    )
    auth_path = shared / AUTH_FILENAME
    try:
        current_bytes = auth_path.read_bytes()
        current = json.loads(current_bytes.decode())
    except (OSError, json.JSONDecodeError) as exc:
        raise Refusal("no baseline reset to restore") from exc
    if not isinstance(current, dict) or current.get("schema_version") != 1:
        raise Refusal("baseline authorization unreadable")
    if current.get("ticket_id") != ticket_id:
        raise Refusal("ticket does not match the baseline record")
    prior_pins = current.get("prior_pins")
    if not isinstance(prior_pins, dict) or set(prior_pins) != set(PATH_A_PINS):
        raise Refusal("baseline authorization unreadable")
    updates = {key: str(prior_pins[key]) for key in PATH_A_PINS}
    updated = apply_pin_updates(env_text, updates)
    _limited(env_text, updated)
    backup_name = str(current.get("prior_authorization_backup") or "")
    prior_hash = str(current.get("prior_baseline_sha256") or "")
    prior_auth: bytes | None
    if prior_hash:
        if not backup_name:
            raise Refusal("prior baseline backup missing")
        backup_path = shared / backup_name
        try:
            prior_auth = backup_path.read_bytes()
        except OSError as exc:
            raise Refusal("prior baseline backup missing") from exc
        if sha256_bytes(prior_auth) != prior_hash:
            raise Refusal("prior baseline backup mismatch")
    else:
        prior_auth = None
    if dry_run:
        return {
            "dry_run": "restore",
            "ticket_id": ticket_id,
            "prior_baseline_sha256": prior_hash or "none",
            "writes": "none",
        }
    user = _user(operator_user)
    stamp = _stamp(clock)
    env_path = shared / ".env" if env_path is None else env_path
    audit_path = shared / AUDIT_FILENAME
    prior_env = env_text.encode()
    audit_size = audit_path.stat().st_size if audit_path.exists() else 0
    try:
        _step(on_step, "restore_started")
        atomic_write_bytes(env_path, updated.encode())
        _step(on_step, "env_written")
        if prior_auth is None:
            auth_path.unlink(missing_ok=True)
        else:
            atomic_write_bytes(auth_path, prior_auth)
        _step(on_step, "authorization_restored")
        _append_audit(audit_path, {
            "action": "restore",
            "operator_user": user,
            "recorded_at": stamp,
            "target_sha": str(current.get("target_sha") or ""),
            "ticket_id": ticket_id,
            "prior_baseline_sha256": prior_hash,
        })
        _step(on_step, "audit_written")
    except Exception:
        _rollback_files(
            env_path=env_path,
            auth_path=auth_path,
            audit_path=audit_path,
            prior_env=prior_env,
            prior_auth=current_bytes,
            audit_size=audit_size,
        )
        raise
    return {
        "baseline_restore": "ok",
        "ticket_id": ticket_id,
        "operator_user": user,
        "recorded_at": stamp,
        "prior_baseline_sha256": prior_hash or "none",
        "audit": "appended",
    }


def _journal_files(log_dir: Path) -> list[Path]:
    if log_dir.is_symlink() or not log_dir.is_dir():
        raise Refusal("journals unreadable")
    found = sorted(path for path in log_dir.glob("journal_*.jsonl") if path.is_file())
    for name in JOURNAL_NAMES:
        candidate = log_dir / name
        if candidate.is_symlink():
            raise Refusal("journals unreadable")
        if candidate.is_file():
            found.append(candidate)
    state = log_dir / "track_b_heartbeat_state"
    if state.exists():
        if state.is_symlink() or not state.is_dir():
            raise Refusal("journals unreadable")
        for path in sorted(state.rglob("*")):
            if path.is_symlink():
                raise Refusal("journals unreadable")
            if path.is_file():
                found.append(path)
    return found


def _submission_dir(env_text: str) -> Path:
    values = env_values(env_text)
    raw = values.get("AFS_ORDER_SUBMISSION_DIR", "").strip()
    if not raw:
        log_dir = values.get("LOG_DIR", "").strip()
        if not log_dir:
            raise Refusal("order_submissions unreadable")
        raw = str(Path(log_dir) / "order_submissions")
    path = Path(raw)
    if path.is_symlink() or not path.is_dir() or not (path / "records").is_dir():
        raise Refusal("order_submissions unreadable")
    return path


def _copy_regular_tree(src: Path, dest: Path) -> list[tuple[str, str]]:
    copied = []
    for path in sorted(src.rglob("*")):
        if path.is_symlink():
            raise Refusal("refusing symlink in backup source")
        if not path.is_file():
            continue
        relative = path.relative_to(src).as_posix()
        target = dest / relative
        data = path.read_bytes()
        atomic_write_bytes(target, data)
        copied.append((relative, sha256_bytes(data)))
    return copied


def _pin_snapshot(env_text: str) -> str:
    values = env_values(env_text)
    lines = []
    for key in list(PATH_A_PINS) + list(RELEASE_PIN_KEYS):
        if key in values:
            lines.append(f"{key}={values[key]}")
        else:
            lines.append(f"{key}=ABSENT")
    return "\n".join(lines) + "\n"


def backup(
    *,
    shared: Path,
    env_text: str,
    status: dict,
    ticket: str,
    operator_token: str,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    operator_user: str | None = None,
    clock: Callable[[], datetime] | None = None,
    on_step: Callable[[str], None] | None = None,
) -> dict:
    ticket_id = require_ticket(ticket)
    require_operator_token(operator_token, f"AUTHORIZE PREDEPLOY BACKUP {ticket_id}")
    enforce_runtime_guards(
        env_text=env_text,
        shared=shared,
        status=status,
        environ=os.environ if environ is None else environ,
    )
    log_raw = env_values(env_text).get("LOG_DIR", "").strip()
    if not log_raw:
        raise Refusal("journals unreadable")
    log_dir = Path(log_raw)
    journals = _journal_files(log_dir)
    submissions = _submission_dir(env_text)
    auth_path = shared / AUTH_FILENAME
    if auth_path.exists() and not auth_path.is_file():
        raise Refusal("baseline unreadable")
    if dry_run:
        return {
            "dry_run": "backup",
            "ticket_id": ticket_id,
            "order_submissions": "present",
            "journals": str(len(journals)),
            "baseline": "present" if auth_path.is_file() else "absent",
            "writes": "none",
        }
    user = _user(operator_user)
    stamp = _stamp(clock)
    name = f"predeploy-{_stamp_compact(stamp)}-{ticket_id}"
    final = shared / "backups" / name
    temporary = shared / "backups" / f".tmp-{name}-{os.getpid()}"
    if final.exists():
        raise Refusal("backup destination exists")
    published = False
    try:
        temporary.mkdir(parents=True, exist_ok=False)
        _step(on_step, "backup_started")
        submission_files = _copy_regular_tree(submissions, temporary / "order_submissions")
        journal_rows = []
        for path in journals:
            data = path.read_bytes()
            relative = Path("journals") / path.relative_to(log_dir)
            atomic_write_bytes(temporary / relative, data)
            journal_rows.append((relative.as_posix(), sha256_bytes(data)))
        pin_text = _pin_snapshot(env_text)
        atomic_write_bytes(temporary / "release_pins.txt", pin_text.encode())
        baseline_hash = ""
        if auth_path.is_file():
            data = auth_path.read_bytes()
            atomic_write_bytes(temporary / AUTH_FILENAME, data)
            baseline_hash = sha256_bytes(data)
        manifest = {
            "schema_version": 1,
            "ticket_id": ticket_id,
            "operator_user": user,
            "created_at": stamp,
            "order_submissions": submission_files,
            "journals": journal_rows,
            "release_pins_sha256": sha256_bytes(pin_text.encode()),
            "baseline_sha256": baseline_hash or "absent",
        }
        atomic_write_bytes(temporary / "manifest.json", _dump(manifest))
        _step(on_step, "backup_ready")
        temporary.rename(final)
        published = True
        _step(on_step, "backup_published")
    except Exception:
        if not published:
            shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "backup": "ok",
        "path": str(final),
        "ticket_id": ticket_id,
        "operator_user": user,
        "recorded_at": stamp,
        "journals": str(len(journals)),
        "baseline": "present" if baseline_hash else "absent",
    }


def _order_status(row: object) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("ordStatus") or row.get("orderStatus") or row.get("status") or "")


def _order_type(row: object) -> str:
    if not isinstance(row, dict):
        return ""
    return str(row.get("orderType") or row.get("type") or "")


def order_is_open(row: object) -> bool:
    status = _order_status(row).strip().lower()
    if status in TERMINAL_ORDER_STATUSES:
        return False
    return True


def exposure_from_broker(broker: object, submissions: dict) -> dict:
    try:
        confirmed, position = broker.get_position_snapshot()
    except Exception as exc:
        raise Refusal("exposure unreadable") from exc
    if confirmed is not True:
        raise Refusal("exposure unreadable")
    try:
        orders, error = broker._read_broker_list("/order/list")
    except Exception as exc:
        raise Refusal("exposure unreadable") from exc
    if error or not isinstance(orders, list):
        raise Refusal("exposure unreadable")
    open_orders = []
    stop_working = False
    for row in orders:
        if not order_is_open(row):
            continue
        kind = _order_type(row)
        status = _order_status(row) or "unknown"
        open_orders.append({"orderType": kind, "ordStatus": status})
        if "stop" in kind.lower():
            stop_working = True
    has_open = bool(position is not None and getattr(position, "open", False))
    if not isinstance(submissions, dict):
        raise Refusal("exposure unreadable")
    return {
        "has_open_position": has_open,
        "open_orders": open_orders,
        "protective_stop_working": has_open and stop_working,
        "order_submissions": submissions,
    }


def submission_public_status() -> dict:
    try:
        return OrderSubmissionStore().public_status()
    except Exception as exc:
        raise Refusal("exposure unreadable") from exc


def classify_recovery(status: object) -> str:
    """ADOPTED or BLOCKED are the only passing proofs. Anything else is not."""
    if not isinstance(status, dict):
        return "INCONCLUSIVE"
    submissions = status.get("order_submissions")
    if not isinstance(submissions, dict) or submissions.get("store_trusted") is not True:
        return "INCONCLUSIVE"
    if submissions.get("entries_blocked") is True or submissions.get("recovery_required") is True:
        return "BLOCKED"
    if (
        status.get("has_open_position") is True
        and status.get("protective_stop_working") is True
        and submissions.get("entries_blocked") is False
        and submissions.get("recovery_required") is False
    ):
        return "ADOPTED"
    return "INCONCLUSIVE"


def _bracket_ok(direction: str, entry: float, stop: float, target: float) -> bool:
    if direction == "LONG":
        return stop < entry < target
    if direction == "SHORT":
        return target < entry < stop
    return False


def recovery_client_id(ticket: str, stamp: str) -> str:
    digest = sha256_bytes(f"{ticket}|{stamp}".encode())[:12]
    compact = "".join(char for char in ticket if char.isalnum())[:12]
    return f"AFSREC{compact}{digest}"


def place_recovery_order(
    *,
    broker: object,
    shared: Path,
    env_text: str,
    status: dict,
    ticket: str,
    operator_token: str,
    instrument: str,
    direction: str,
    entry: float,
    stop: float,
    target: float,
    quantity: int = 1,
    dry_run: bool = False,
    environ: Mapping[str, str] | None = None,
    operator_user: str | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict:
    ticket_id = require_ticket(ticket)
    require_operator_token(operator_token, f"AUTHORIZE DEMO RECOVERY PROOF {ticket_id}")
    enforce_runtime_guards(
        env_text=env_text,
        shared=shared,
        status=status,
        environ=os.environ if environ is None else environ,
    )
    if quantity != 1:
        raise Refusal("quantity must be 1")
    if instrument not in {"MNQ", "MES"}:
        raise Refusal("instrument refused")
    if direction not in {"LONG", "SHORT"}:
        raise Refusal("direction refused")
    if not _bracket_ok(direction, entry, stop, target):
        raise Refusal("bracket refused")
    risk = abs(entry - stop)
    if risk <= 0:
        raise Refusal("bracket refused")
    if dry_run:
        return {
            "dry_run": "recovery-proof",
            "quantity": 1,
            "instrument": instrument,
            "direction": direction,
            "writes": "none",
            "broker_called": False,
        }
    user = _user(operator_user)
    stamp = _stamp(clock)
    client_id = recovery_client_id(ticket_id, stamp)
    order = BracketOrder(
        instrument=instrument,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=abs(target - entry) / risk,
        strategy="demo_recovery_proof",
        contracts=1,
        client_order_id=client_id,
    )
    try:
        fill = broker.execute_bracket(order)
    except Exception as exc:
        raise Refusal("recovery order unconfirmed") from exc
    if not isinstance(fill, Fill):
        raise Refusal("recovery order unconfirmed")
    result = str(fill.result or "")
    evidence = {
        "schema_version": 1,
        "ticket_id": ticket_id,
        "operator_user": user,
        "recorded_at": stamp,
        "client_order_id": client_id,
        "instrument": instrument,
        "direction": direction,
        "quantity": 1,
        "result": result,
        "do_not_retry": result == "UNKNOWN",
    }
    atomic_write_bytes(shared / "recovery_proof.json", _dump(evidence))
    return {
        "recovery_order_result": result or "UNCONFIRMED",
        "client_order_id": client_id,
        "quantity": 1,
        "instrument": instrument,
        "do_not_retry": "1" if result == "UNKNOWN" else "0",
    }


def preflight_report(env_text: str, shared: Path) -> tuple[int, list[str]]:
    configured = path_a_configured(env_text)
    auth_path = shared / AUTH_FILENAME
    authorized = "none"
    fingerprint = "none"
    if auth_path.is_file():
        try:
            payload = json.loads(auth_path.read_text())
            authorized = payload.get("target_sha") or "unreadable"
            fingerprint = payload.get("release_fingerprint") or "unreadable"
        except (OSError, json.JSONDecodeError):
            authorized = "unreadable"
            fingerprint = "unreadable"
        if not isinstance(authorized, str) or (authorized not in {"unreadable"} and SHA_RE.fullmatch(authorized) is None):
            authorized = "unreadable"
    lines = [
        f"path_a_configured={'yes' if configured else 'no'}",
        f"reset_needed={'no' if configured else 'yes'}",
        f"authorized_sha={authorized}",
        f"release_fingerprint={fingerprint}",
    ]
    values = env_values(env_text)
    for key in PATH_A_PINS:
        if key in values:
            lines.append(f"{key}={values[key]}")
        else:
            lines.append(f"{key}=ABSENT")
    return (0 if configured else 10), lines


def _emit(payload: dict) -> None:
    for key in (
        "dry_run", "baseline_reset", "baseline_restore", "backup",
        "target_sha", "release_fingerprint", "ticket_id", "prior_baseline_sha256",
        "authorization_sha256", "audit", "writes", "order_submissions",
        "journals", "baseline", "path", "quantity", "instrument",
        "direction", "broker_called", "recovery_order_result",
        "client_order_id", "do_not_retry", "recorded_at", "operator_user",
    ):
        if key in payload:
            print(f"{key}={payload[key]}")


def _run_mutation(action: Callable[[], dict]) -> int:
    try:
        _emit(action())
    except Refusal as exc:
        print(f"baseline_reset refused: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("baseline_reset failed: rolled back", file=sys.stderr)
        return 1
    return 0


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    shared = Path(args.shared_dir)
    env_path = Path(args.env_file) if args.env_file else shared / ".env"
    return shared, env_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="baseline_reset")
    sub = parser.add_subparsers(dest="command", required=True)
    root = argparse.ArgumentParser(add_help=False)
    root.add_argument("--shared-dir", default=os.environ.get("AFS_SHARED_DIR", "/root/afs-shared"))

    pre = sub.add_parser("preflight", parents=[root])
    pre.add_argument("--env-file", default=None)

    common = argparse.ArgumentParser(add_help=False, parents=[root])
    common.add_argument("--env-file", default=None)
    common.add_argument("--status-file", required=True)
    common.add_argument("--ticket", required=True)
    common.add_argument("--operator-token", required=True)
    common.add_argument("--dry-run", action="store_true")

    reset_parent = argparse.ArgumentParser(add_help=False, parents=[root])
    reset_parent.add_argument("--env-file", default=None)
    reset_parent.add_argument("--status-file", required=True)
    reset_parent.add_argument("--ticket", required=True)
    reset_parent.add_argument("--dry-run", action="store_true")
    reset_cmd = sub.add_parser(
        "reset",
        parents=[reset_parent],
        description=(
            "Stable interface: "
            + " ".join(RESET_CLI_FLAGS)
            + ". --shared-dir is optional. Approval is an ssh-keygen -Y signature, not a token."
        ),
    )
    reset_cmd.add_argument("--target-sha", required=True)
    reset_cmd.add_argument("--release-fingerprint", required=True)
    reset_cmd.add_argument("--approval", required=True)
    reset_cmd.add_argument("--signature", required=True)
    sub.add_parser("restore", parents=[common])
    sub.add_parser("backup", parents=[common])

    census = sub.add_parser("exposure-census", parents=[root])
    census.add_argument("--out", required=True)
    census.add_argument("--env-file", default=None)

    proof = sub.add_parser("recovery-proof", parents=[common])
    proof.add_argument("action", choices=("place", "check"))
    proof.add_argument("--instrument", default="")
    proof.add_argument("--direction", default="")
    proof.add_argument("--entry", type=float, default=None)
    proof.add_argument("--stop", type=float, default=None)
    proof.add_argument("--target-price", type=float, default=None, dest="target_price")
    proof.add_argument("--quantity", type=int, default=1)

    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            shared, env_path = _paths(args)
            code, lines = preflight_report(_read_env(env_path), shared)
            print("\n".join(lines))
            return code
        if args.command == "exposure-census":
            shared, env_path = _paths(args)
            env_text = _read_env(env_path)
            require_demo_posture(env_text)
            status = exposure_from_broker(
                TradovateBroker(config=TradovateConfig.from_env()),
                submission_public_status(),
            )
            atomic_write_bytes(Path(args.out), _dump(status))
            print(f"has_open_position={str(status['has_open_position']).lower()}")
            print(f"open_order_count={len(status['open_orders'])}")
            print("exposure_census=ok")
            return 0
        shared, env_path = _paths(args)
        env_text = _read_env(env_path)
        status = _load_status(Path(args.status_file))
        if args.command == "reset":
            return _run_mutation(lambda: reset(
                shared=shared,
                env_text=env_text,
                env_path=env_path,
                status=status,
                target_sha=args.target_sha,
                release_fingerprint=args.release_fingerprint,
                ticket=args.ticket,
                approval_path=Path(args.approval),
                signature_path=Path(args.signature),
                dry_run=args.dry_run,
            ))
        if args.command == "restore":
            return _run_mutation(lambda: restore(
                shared=shared,
                env_text=env_text,
                env_path=env_path,
                status=status,
                ticket=args.ticket,
                operator_token=args.operator_token,
                dry_run=args.dry_run,
            ))
        if args.command == "backup":
            return _run_mutation(lambda: backup(
                shared=shared,
                env_text=env_text,
                status=status,
                ticket=args.ticket,
                operator_token=args.operator_token,
                dry_run=args.dry_run,
            ))
        if args.command == "recovery-proof" and args.action == "check":
            require_ticket(args.ticket)
            require_operator_token(
                args.operator_token,
                f"AUTHORIZE DEMO RECOVERY PROOF {args.ticket}",
            )
            verdict = classify_recovery(status)
            print(f"recovery_proof={verdict}")
            return 0 if verdict in {"ADOPTED", "BLOCKED"} else 2
        if args.command == "recovery-proof" and args.action == "place":
            if args.entry is None or args.stop is None or args.target_price is None:
                raise Refusal("bracket refused")
            broker = None if args.dry_run else TradovateBroker(config=TradovateConfig.from_env())
            return _run_mutation(lambda: place_recovery_order(
                broker=broker,
                shared=shared,
                env_text=env_text,
                status=status,
                ticket=args.ticket,
                operator_token=args.operator_token,
                instrument=args.instrument,
                direction=args.direction,
                entry=args.entry,
                stop=args.stop,
                target=args.target_price,
                quantity=args.quantity,
                dry_run=args.dry_run,
            ))
    except Refusal as exc:
        print(f"baseline_reset refused: {exc}", file=sys.stderr)
        return 1
    print("unknown command", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
