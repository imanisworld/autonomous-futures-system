"""One-time GO tokens for promote and baseline-reset.

The operator creates a token as root. It expires two hours after creation,
binds to one SHA, and is consumed on the first successful authorization check.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOKEN_TTL = timedelta(hours=2)
_SHA = re.compile(r"[0-9a-f]{40}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_LEDGER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,80}")
_TOKEN_ID = re.compile(r"[0-9a-f]{32}")


class TokenError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_regular(path: Path) -> bool:
    try:
        current = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(current.st_mode)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TokenStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.root = Path(root)
        self.clock = clock or _utcnow

    def authorize(self, sha: str, ledger_id: str, release_fingerprint: str) -> str:
        if (
            not _SHA.fullmatch(sha)
            or not _LEDGER.fullmatch(ledger_id)
            or not _FINGERPRINT.fullmatch(release_fingerprint)
        ):
            raise TokenError("malformed")
        now = self.clock()
        token_id = secrets.token_hex(16)
        payload = {
            "token_id": token_id,
            "sha": sha,
            "ledger_id": ledger_id,
            "release_fingerprint": release_fingerprint,
            "created_at": now.isoformat(),
            "expires_at": (now + TOKEN_TTL).isoformat(),
            "consumed_at": None,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / f"{token_id}.json"
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
            handle.write("\n")
        return token_id

    def find_open_for_sha(self, sha: str) -> str:
        try:
            matches = [
                row["token_id"]
                for row in self._open_records()
                if row.get("sha") == sha and isinstance(row.get("token_id"), str)
            ]
        except (TypeError, ValueError, KeyError, OSError) as exc:
            raise TokenError("missing") from exc
        if len(matches) != 1:
            raise TokenError("missing" if not matches else "ambiguous")
        return matches[0]

    def find_single_open(self) -> dict:
        try:
            matches = self._open_records()
        except (TypeError, ValueError, KeyError, OSError) as exc:
            raise TokenError("missing") from exc
        if len(matches) != 1:
            raise TokenError("missing" if not matches else "ambiguous")
        return matches[0]

    def _open_records(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        records: list[dict] = []
        for path in sorted(self.root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                continue
            data = self._read_record(path)
            if data is None or data.get("consumed_at") or data.get("expired_at"):
                continue
            if self._is_expired(data):
                self._mark_expired(path, data)
                continue
            if not isinstance(data.get("token_id"), str):
                continue
            records.append(data)
        return records

    def _read_record(self, path: Path) -> dict | None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        try:
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                data = json.loads(handle.read())
        except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return data

    def _is_expired(self, data: dict) -> bool:
        raw = data.get("expires_at")
        if not isinstance(raw, str):
            return True
        try:
            expires = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return self.clock() > expires

    def _mark_expired(self, path: Path, data: dict) -> None:
        token_id = data.get("token_id")
        name = f"{token_id}.json" if isinstance(token_id, str) and _TOKEN_ID.fullmatch(token_id) else path.name
        marked = dict(data)
        marked["expired_at"] = self.clock().isoformat()
        dest_dir = self.root / "expired"
        dest_dir.mkdir(mode=0o700, exist_ok=True)
        temporary = self.root / f".{name}.expired"
        temporary.write_text(json.dumps(marked) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, dest_dir / name)
        path.unlink(missing_ok=True)

    def read(self, token_id: str) -> dict:
        self._check_id(token_id)
        path = self.root / f"{token_id}.json"
        consumed = self.root / "consumed" / f"{token_id}.json"
        if _is_regular(consumed) and not _is_regular(path):
            raise TokenError("reused")
        data = self._read_checked(path)
        if self._is_expired(data):
            self._mark_expired(path, data)
            raise TokenError("expired")
        return data

    def _read_checked(self, path: Path) -> dict:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError as exc:
            raise TokenError("missing") from exc
        except OSError as exc:
            raise TokenError("missing") from exc
        try:
            current = os.fstat(fd)
            if stat.S_ISLNK(current.st_mode):
                raise TokenError("missing")
            if not stat.S_ISREG(current.st_mode):
                raise TokenError("missing")
            listed = os.lstat(path)
            if stat.S_ISLNK(listed.st_mode) or (listed.st_dev, listed.st_ino) != (current.st_dev, current.st_ino):
                raise TokenError("missing")
            raw = os.read(fd, 1_000_000).decode("utf-8")
        except TokenError:
            raise
        except (OSError, UnicodeError) as exc:
            raise TokenError("missing") from exc
        finally:
            os.close(fd)
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeError, TypeError, ValueError) as exc:
            raise TokenError("malformed") from exc
        if not isinstance(data, dict):
            raise TokenError("malformed")
        return data

    def consume(self, token_id: str, sha: str) -> dict:
        self._check_id(token_id)
        if not _SHA.fullmatch(sha):
            raise TokenError("mismatch")
        lock = self.root / f"{token_id}.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise TokenError("reused") from exc
        try:
            return self._consume_locked(token_id, sha)
        finally:
            os.close(fd)
            lock.unlink(missing_ok=True)

    def _consume_locked(self, token_id: str, sha: str) -> dict:
        path = self.root / f"{token_id}.json"
        consumed = self.root / "consumed" / f"{token_id}.json"
        if consumed.is_file() and not path.is_file():
            raise TokenError("reused")
        if not path.is_file():
            raise TokenError("missing")
        data = self._read_record(path)
        if not isinstance(data, dict):
            raise TokenError("missing")
        if data.get("consumed_at"):
            raise TokenError("reused")
        if self._is_expired(data):
            self._mark_expired(path, data)
            raise TokenError("expired")
        if data.get("sha") != sha:
            raise TokenError("mismatch")
        data["consumed_at"] = self.clock().isoformat()
        consumed.parent.mkdir(parents=True, exist_ok=True)
        tmp = consumed.with_suffix(".tmp")
        tmp.write_text(json.dumps(data) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, consumed)
        path.unlink()
        return data

    @staticmethod
    def _check_id(token_id: str) -> None:
        if not _TOKEN_ID.fullmatch(token_id):
            raise TokenError("missing")
