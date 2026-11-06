"""One-time GO tokens for promote and baseline-reset.

The operator creates a token as root. It expires two hours after creation,
binds to one SHA, and is consumed on the first successful authorization check.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOKEN_TTL = timedelta(hours=2)
_SHA = re.compile(r"[0-9a-f]{40}")
_LEDGER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,80}")
_TOKEN_ID = re.compile(r"[0-9a-f]{32}")


class TokenError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TokenStore:
    def __init__(self, root: Path, clock: Callable[[], datetime] | None = None) -> None:
        self.root = Path(root)
        self.clock = clock or _utcnow

    def authorize(self, sha: str, ledger_id: str) -> str:
        if not _SHA.fullmatch(sha) or not _LEDGER.fullmatch(ledger_id):
            raise TokenError("malformed")
        now = self.clock()
        token_id = secrets.token_hex(16)
        payload = {
            "token_id": token_id,
            "sha": sha,
            "ledger_id": ledger_id,
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
        matches = [row["token_id"] for row in self._open_records() if row.get("sha") == sha]
        if len(matches) != 1:
            raise TokenError("missing" if not matches else "ambiguous")
        return matches[0]

    def find_single_open(self) -> dict:
        matches = self._open_records()
        if len(matches) != 1:
            raise TokenError("missing" if not matches else "ambiguous")
        return matches[0]

    def _open_records(self) -> list[dict]:
        if not self.root.is_dir():
            return []
        now = self.clock()
        records: list[dict] = []
        for path in sorted(self.root.glob("*.json")):
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("consumed_at"):
                continue
            expires = datetime.fromisoformat(data["expires_at"])
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if now > expires:
                continue
            records.append(data)
        return records

    def read(self, token_id: str) -> dict:
        self._check_id(token_id)
        path = self.root / f"{token_id}.json"
        consumed = self.root / "consumed" / f"{token_id}.json"
        if consumed.is_file() and not path.is_file():
            raise TokenError("reused")
        if not path.is_file():
            raise TokenError("missing")
        return json.loads(path.read_text(encoding="utf-8"))

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
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("consumed_at"):
            raise TokenError("reused")
        expires = datetime.fromisoformat(data["expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if self.clock() > expires:
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
