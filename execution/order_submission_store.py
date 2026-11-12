"""Durable Tradovate submission intent.

Paper/demo safety only. This store is the source of truth for "an order may
already exist at the broker." It is not a journal note and it is not process
memory.

Location: ``AFS_ORDER_SUBMISSION_DIR`` when set, otherwise
``$LOG_DIR/order_submissions``. Both are read at call time. ``LOG_DIR`` is the
shared journal volume used by the release, so the store does not fall back to
a cwd-relative logs directory inside an immutable release tree. If neither
variable is set, construction fails closed. Writes use a temp file, ``fsync``,
and ``os.replace``, plus a directory ``fsync``. A lock file serializes creators
across processes; a threading lock serializes them inside one process.

Pre-send state AMBIGUOUS means the request may have left the process. A
restart does not prove it did not. AMBIGUOUS and RECONCILE_REQUIRED block
every new entry until identity-corroborated broker evidence resolves them.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

try:
    import fcntl
except ImportError:  # pragma: no cover - POSIX hosts have fcntl
    fcntl = None

SCHEMA_VERSION = 1

NEW = "NEW"
AMBIGUOUS = "AMBIGUOUS"
SUBMITTED = "SUBMITTED"
OPEN = "OPEN"
PARTIALLY_FILLED = "PARTIALLY_FILLED"
NO_FILL = "NO_FILL"
CANCELLED = "CANCELLED"
REJECTED = "REJECTED"
RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
CLOSED = "CLOSED"

# Unresolved for entry admission. OPEN is confirmed exposure and is left to
# the existing position gate; it is not unknown exposure.
BLOCKING_STATES = frozenset({AMBIGUOUS, RECONCILE_REQUIRED, SUBMITTED, PARTIALLY_FILLED})
TERMINAL_STATES = frozenset({REJECTED, NO_FILL, CANCELLED, CLOSED})

_TRANSITIONS = {
    NEW: {AMBIGUOUS},
    AMBIGUOUS: {SUBMITTED, REJECTED, NO_FILL, CANCELLED, RECONCILE_REQUIRED},
    SUBMITTED: {OPEN, PARTIALLY_FILLED, NO_FILL, CANCELLED, RECONCILE_REQUIRED},
    RECONCILE_REQUIRED: {
        SUBMITTED, OPEN, PARTIALLY_FILLED, NO_FILL, CANCELLED, RECONCILE_REQUIRED,
    },
    OPEN: {CLOSED},
    PARTIALLY_FILLED: {OPEN, RECONCILE_REQUIRED, CLOSED},
}

_LOCK = threading.Lock()
_LAST_ERROR_LIMIT = 300


class SubmissionIntegrityError(RuntimeError):
    """Same client id with a different request fingerprint. No send."""


class SubmissionDurabilityError(OSError):
    """The pre-send record could not be made durable. No send."""


class SubmissionStoreError(RuntimeError):
    """The store cannot be trusted. Entries stay blocked."""


def default_submission_dir() -> Path:
    raw = os.environ.get("AFS_ORDER_SUBMISSION_DIR", "").strip()
    if raw:
        return Path(raw)
    journal = os.environ.get("LOG_DIR", "").strip()
    if not journal:
        raise SubmissionStoreError("AFS_ORDER_SUBMISSION_DIR or LOG_DIR is required")
    return Path(journal) / "order_submissions"


def request_fingerprint(spec: dict) -> str:
    payload = {
        "account_env": spec.get("account_env"),
        "account_id": spec.get("account_id"),
        "contract": spec.get("contract"),
        "instrument": spec.get("instrument"),
        "direction": spec.get("direction"),
        "quantity": spec.get("quantity"),
        "entry": spec.get("entry"),
        "stop": spec.get("stop"),
        "target": spec.get("target"),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clip(text: Optional[str]) -> Optional[str]:
    if text is None:
        return None
    text = str(text)
    if len(text) > _LAST_ERROR_LIMIT:
        return text[:_LAST_ERROR_LIMIT]
    return text


class OrderSubmissionStore:
    def __init__(self, directory: Optional[Path] = None) -> None:
        self.directory = Path(directory) if directory is not None else default_submission_dir()
        self.records = self.directory / "records"
        self.lock_path = self.directory / "LOCK"

    def entry_block(self) -> Optional[dict]:
        """None when new entries are allowed. Otherwise a public block reason.

        Corrupt, unreadable, or unsupported records block every entry. The
        files are left in place.
        """
        try:
            records = self._load_all(strict=True)
        except SubmissionStoreError as exc:
            return {
                "reason": "store_untrusted",
                "error": _clip(str(exc)),
                "client_order_ids": [],
                "entries_blocked": True,
                "recovery_required": True,
            }
        blocking = [row for row in records if row.get("state") in BLOCKING_STATES]
        if not blocking:
            return None
        return {
            "reason": "unresolved_submission",
            "client_order_ids": [row["client_order_id"] for row in blocking],
            "states": [row.get("state") for row in blocking],
            "entries_blocked": True,
            "recovery_required": True,
        }

    def public_status(self) -> dict:
        """Dashboard view. No secrets and no raw broker payloads."""
        try:
            records = self._load_all(strict=True)
            trusted = True
            error = None
        except SubmissionStoreError as exc:
            records = []
            trusted = False
            error = _clip(str(exc))
        unresolved = [row for row in records if row.get("state") in BLOCKING_STATES]
        return {
            "recovery_required": (not trusted) or bool(unresolved),
            "entries_blocked": (not trusted) or bool(unresolved),
            "store_trusted": trusted,
            "error": error,
            "unresolved_count": len(unresolved),
            "unresolved": [
                {
                    "client_order_id": row.get("client_order_id"),
                    "state": row.get("state"),
                    "parent_id": row.get("parent_id"),
                    "stop_id": row.get("stop_id"),
                    "target_id": row.get("target_id"),
                    "last_error": row.get("last_error"),
                    "instrument": row.get("instrument"),
                }
                for row in unresolved
            ],
        }

    def get(self, client_order_id: str) -> Optional[dict]:
        path = self._path(client_order_id)
        if not path.is_file():
            return None
        return self._read_record(path)

    def reserve_initial_send(self, spec: dict) -> dict:
        """Persist AMBIGUOUS before any broker byte. ``may_send`` is true once.

        The record means the order may be sent, not that it was not sent.
        """
        client_id = str(spec["client_order_id"])
        fingerprint = spec.get("request_fingerprint") or request_fingerprint(spec)
        with self._exclusive():
            existing = self._peek(client_id)
            if existing is not None:
                if existing.get("request_fingerprint") != fingerprint:
                    raise SubmissionIntegrityError(
                        f"client_order_id {client_id} fingerprint mismatch"
                    )
                return {"may_send": False, "record": existing, "reason": existing.get("state")}
            record = self._blank(spec, fingerprint)
            record["state"] = AMBIGUOUS
            record["history"].append(
                {"ts": record["updated_at"], "from": NEW, "to": AMBIGUOUS, "evidence": "pre_send"}
            )
            self._write_record(record)
            return {"may_send": True, "record": record, "reason": AMBIGUOUS}

    def transition(self, client_order_id: str, new_state: str, **evidence) -> dict:
        with self._exclusive():
            current = self._peek(client_order_id)
            if current is None:
                raise SubmissionStoreError(f"missing submission {client_order_id}")
            old = current.get("state")
            allowed = _TRANSITIONS.get(old, set())
            if new_state not in allowed:
                raise SubmissionIntegrityError(f"illegal transition {old} -> {new_state}")
            current["state"] = new_state
            current["updated_at"] = _now()
            for key in (
                "parent_id", "stop_id", "target_id", "broker_status",
                "fill_evidence", "last_error", "resolution",
            ):
                if key in evidence:
                    value = evidence[key]
                    current[key] = _clip(value) if key == "last_error" else value
            if evidence.get("lookup"):
                current["last_lookup_at"] = _now()
            current["history"].append(
                {
                    "ts": current["updated_at"],
                    "from": old,
                    "to": new_state,
                    "evidence": _clip(evidence.get("evidence") or new_state),
                }
            )
            self._write_record(current)
            return current

    def mark_reconcile_required(self, client_order_id: str, error: str) -> Optional[dict]:
        current = self.get(client_order_id)
        if current is None:
            return None
        if current.get("state") == RECONCILE_REQUIRED:
            return self.transition(
                client_order_id, RECONCILE_REQUIRED, last_error=error,
                evidence="lookup_failed", lookup=True,
            )
        if current.get("state") not in _TRANSITIONS or RECONCILE_REQUIRED not in _TRANSITIONS[current["state"]]:
            if current.get("state") in BLOCKING_STATES:
                return current
            return current
        return self.transition(
            client_order_id, RECONCILE_REQUIRED, last_error=error,
            evidence="uncertainty", lookup=True,
        )

    def claim_alert(self, client_order_id: str) -> bool:
        """True the first time an alert should fire. Failure to alert must not
        clear the record; callers keep the block either way."""
        with self._exclusive():
            current = self._peek(client_order_id)
            if current is None or current.get("alerted_at"):
                return False
            current["alerted_at"] = _now()
            current["updated_at"] = current["alerted_at"]
            self._write_record(current)
            return True

    def reconcile_with_evidence(self, client_order_id: str, evidence: Optional[dict]) -> dict:
        """Apply one identity-scoped read. Inconclusive evidence never clears.

        ``evidence`` keys: error, status, parent_id, stop_id, target_id,
        filled_qty, requested_qty, position_linked, position_checked,
        children_resolved, protection_verified, identity_linked,
        order_found, order_identity_checked, fills_checked,
        executions_checked, absence_proven, no_order, closed_round_trip,
        rejection_linked.

        ``no_order`` clears only when absence was proven from the order
        identity lookup, fill history, execution history, and position read.
        An empty order list by itself stays unresolved.
        """
        current = self.get(client_order_id)
        if current is None:
            raise SubmissionStoreError(f"missing submission {client_order_id}")
        if current.get("state") in TERMINAL_STATES and not (evidence or {}).get("contradicts_terminal"):
            return current
        if current.get("state") == OPEN and not (evidence or {}).get("closed_round_trip"):
            return current
        if not evidence or evidence.get("error") or evidence.get("identity_linked") is False:
            return self.mark_reconcile_required(
                client_order_id, (evidence or {}).get("error") or "identity_not_corroborated",
            ) or current
        status = str(evidence.get("status") or "").lower()
        filled = evidence.get("filled_qty")
        requested = evidence.get("requested_qty", current.get("quantity"))
        ids = {
            "parent_id": evidence.get("parent_id", current.get("parent_id")),
            "stop_id": evidence.get("stop_id", current.get("stop_id")),
            "target_id": evidence.get("target_id", current.get("target_id")),
            "broker_status": status or None,
            "fill_evidence": {
                "filled_qty": filled,
                "position_linked": evidence.get("position_linked"),
                "children_resolved": evidence.get("children_resolved"),
            },
            "lookup": True,
            "evidence": "broker_read",
        }
        dead = status in {"rejected", "canceled", "cancelled", "expired"}
        if evidence.get("rejection_linked") and filled in (0, None) and not evidence.get("position_linked"):
            target = REJECTED if current.get("state") == AMBIGUOUS else NO_FILL
            if target not in _TRANSITIONS.get(current.get("state"), set()):
                target = RECONCILE_REQUIRED
            return self.transition(client_order_id, target, resolution="identity_linked_rejection", **ids)
        if (
            evidence.get("closed_round_trip")
            and evidence.get("order_found") is True
            and evidence.get("children_resolved")
            and evidence.get("position_checked") is True
            and filled not in (None, 0)
        ):
            state = current.get("state")
            if state == AMBIGUOUS:
                self.transition(client_order_id, SUBMITTED, **ids)
                self.transition(client_order_id, OPEN, **ids)
            elif state in {RECONCILE_REQUIRED, SUBMITTED, PARTIALLY_FILLED}:
                if state != OPEN:
                    self.transition(client_order_id, OPEN, **ids)
            return self.transition(client_order_id, CLOSED, resolution="filled_and_closed", **ids)
        if (
            status == "filled"
            and filled == requested
            and evidence.get("position_linked")
            and evidence.get("protection_verified")
            and evidence.get("position_checked")
        ):
            state = current.get("state")
            if state == AMBIGUOUS:
                self.transition(client_order_id, SUBMITTED, **ids)
            return self.transition(client_order_id, OPEN, resolution="filled", **ids)
        if filled not in (None, 0) and requested not in (None, 0) and filled < requested:
            state = current.get("state")
            if state == AMBIGUOUS:
                self.transition(client_order_id, SUBMITTED, **ids)
            if (
                evidence.get("children_resolved")
                and evidence.get("position_linked")
                and evidence.get("protection_verified")
            ):
                self.transition(client_order_id, PARTIALLY_FILLED, **ids)
                return self.transition(client_order_id, OPEN, resolution="partial_adopted", **ids)
            if state != PARTIALLY_FILLED:
                return self.transition(client_order_id, PARTIALLY_FILLED, **ids)
            return self.transition(client_order_id, RECONCILE_REQUIRED, last_error="partial_protection_unknown", **ids)
        absence_proven = (
            evidence.get("absence_proven") is True
            and evidence.get("no_order") is True
            and evidence.get("order_found") is False
            and evidence.get("order_identity_checked") is True
            and evidence.get("fills_checked") is True
            and evidence.get("executions_checked") is True
            and evidence.get("position_checked") is True
            and filled in (0, None)
            and evidence.get("position_linked") is False
        )
        if absence_proven and evidence.get("children_resolved", True):
            state = current.get("state")
            target = NO_FILL if NO_FILL in _TRANSITIONS.get(state, set()) else RECONCILE_REQUIRED
            return self.transition(client_order_id, target, resolution="no_order_created", **ids)
        if (
            dead
            and evidence.get("order_found") is True
            and filled == 0
            and evidence.get("position_linked") is False
            and evidence.get("position_checked") is True
            and evidence.get("fills_checked") is True
            and evidence.get("executions_checked") is True
            and evidence.get("children_resolved")
        ):
            state = current.get("state")
            target = CANCELLED if status in {"canceled", "cancelled"} else NO_FILL
            if target not in _TRANSITIONS.get(state, set()):
                target = NO_FILL if NO_FILL in _TRANSITIONS.get(state, set()) else RECONCILE_REQUIRED
            return self.transition(client_order_id, target, resolution="zero_fill", **ids)
        return self.mark_reconcile_required(client_order_id, "inconclusive") or current

    def _blank(self, spec: dict, fingerprint: str) -> dict:
        stamp = _now()
        return {
            "schema_version": SCHEMA_VERSION,
            "client_order_id": str(spec["client_order_id"]),
            "account_env": spec.get("account_env"),
            "account_id": spec.get("account_id"),
            "contract": spec.get("contract"),
            "instrument": spec.get("instrument"),
            "direction": spec.get("direction"),
            "quantity": spec.get("quantity"),
            "entry": spec.get("entry"),
            "stop": spec.get("stop"),
            "target": spec.get("target"),
            "request_fingerprint": fingerprint,
            "created_at": stamp,
            "updated_at": stamp,
            "state": NEW,
            "parent_id": None,
            "stop_id": None,
            "target_id": None,
            "broker_status": None,
            "fill_evidence": None,
            "last_error": None,
            "last_lookup_at": None,
            "resolution": None,
            "alerted_at": None,
            "history": [],
        }

    def _load_all(self, strict: bool) -> list[dict]:
        if not self.records.exists():
            return []
        found = []
        for path in sorted(self.records.glob("*.json")):
            try:
                found.append(self._read_record(path))
            except SubmissionStoreError:
                if strict:
                    raise
        return found

    def _read_record(self, path: Path) -> dict:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise SubmissionStoreError(f"unreadable submission {path.name}: {exc}") from exc
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
            raise SubmissionStoreError(f"unsupported submission {path.name}")
        if not data.get("client_order_id") or not data.get("state"):
            raise SubmissionStoreError(f"incomplete submission {path.name}")
        return data

    def _peek(self, client_order_id: str) -> Optional[dict]:
        path = self._path(client_order_id)
        if not path.is_file():
            return None
        return self._read_record(path)

    def _path(self, client_order_id: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(client_order_id))
        return self.records / f"{safe}.json"

    def _write_record(self, record: dict) -> None:
        path = self._path(record["client_order_id"])
        try:
            self.records.mkdir(parents=True, exist_ok=True)
            blob = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            tmp = path.with_suffix(".json.tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                os.write(fd, blob)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, path)
            dir_fd = os.open(self.records, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError as exc:
            raise SubmissionDurabilityError(str(exc)) from exc

    def _exclusive(self):
        return _StoreLock(self)


class _StoreLock:
    def __init__(self, store: OrderSubmissionStore) -> None:
        self.store = store
        self._file = None

    def __enter__(self):
        _LOCK.acquire()
        self.store.directory.mkdir(parents=True, exist_ok=True)
        self._file = open(self.store.lock_path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(self._file.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._file is not None and fcntl is not None:
                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        finally:
            if self._file is not None:
                self._file.close()
            _LOCK.release()
        return False
