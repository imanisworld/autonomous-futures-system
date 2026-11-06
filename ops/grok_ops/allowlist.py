"""Fixed verb allowlist for the grok-ops forced command.

Anything outside the verb grammar is refused before a privileged script runs.
Shell metacharacters are rejected as injection, not parsed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

VERBS = (
    "preflight",
    "backup",
    "baseline-reset-dry-run",
    "baseline-reset",
    "promote",
    "verify",
    "rollback",
    "status",
)

_SAFE_TEXT = re.compile(r"[A-Za-z0-9._ -]*")
_SHA = re.compile(r"[0-9a-f]{40}")
_RELEASE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class CommandRejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ParsedCommand:
    verb: str
    sha: str | None = None
    release: str | None = None


def parse_command(raw: str) -> ParsedCommand:
    if not isinstance(raw, str) or not _SAFE_TEXT.fullmatch(raw):
        raise CommandRejected("injection")
    parts = raw.split()
    if not parts:
        raise CommandRejected("malformed")
    verb = parts[0]
    if verb not in VERBS:
        raise CommandRejected("unknown-verb")
    if verb == "promote":
        if len(parts) != 2 or not _SHA.fullmatch(parts[1]):
            raise CommandRejected("malformed")
        return ParsedCommand(verb="promote", sha=parts[1])
    if verb == "rollback":
        if len(parts) != 2 or not _RELEASE.fullmatch(parts[1]) or ".." in parts[1]:
            raise CommandRejected("malformed")
        return ParsedCommand(verb="rollback", release=parts[1])
    if len(parts) != 1:
        raise CommandRejected("malformed")
    return ParsedCommand(verb=verb)
