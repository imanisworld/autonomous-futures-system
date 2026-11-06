"""Append-only audit log for grok-ops actions.

Records who, when, verb, SHA, token id, and result. The writer opens the
file with O_APPEND and has no truncate method.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o640)
            os.close(fd)

    def append(
        self,
        *,
        actor: str,
        verb: str,
        sha: str,
        token_id: str,
        result: str,
        ledger_id: str = "",
    ) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "verb": verb,
            "sha": sha or "",
            "token_id": token_id or "",
            "ledger_id": ledger_id or "",
            "result": result,
        }
        line = json.dumps(record, separators=(",", ":")) + "\n"
        fd = os.open(self.path, os.O_APPEND | os.O_WRONLY)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
