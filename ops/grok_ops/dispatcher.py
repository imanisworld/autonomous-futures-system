"""Forced command for the grok-ops SSH user.

Reads SSH_ORIGINAL_COMMAND, applies the verb allowlist, and execs sudo with
one exact script path. The client command is never passed to a shell.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

from ops.grok_ops.allowlist import VERBS, CommandRejected, parse_command
from ops.grok_ops.auditlog import AuditLog
from ops.grok_ops.sudoers import REFUSAL_PATH, VERB_PATHS

_VERB_PATH = dict(zip(VERBS, VERB_PATHS, strict=True))
_REFUSAL_REASONS = frozenset({"injection", "unknown-verb", "malformed"})
_ACTOR = re.compile(r"grok-ops(@[0-9A-Fa-f.:]+)?")


def _actor() -> str:
    client = os.environ.get("SSH_CLIENT", "")
    first = client.split()[0] if client else ""
    if first and all(char in "0123456789abcdefABCDEF.:" for char in first):
        return f"grok-ops@{first}"
    return "grok-ops"


def _refuse(reason: str) -> int:
    payload = json.dumps({"reason": reason, "actor": _actor()})
    completed = subprocess.run(
        ["sudo", "-n", REFUSAL_PATH],
        input=payload,
        text=True,
        check=False,
    )
    print(reason, file=sys.stderr)
    if completed.returncode != 0:
        return 1
    return 2


def record_refusal_main() -> int:
    """Root-only writer for a rejected SSH command. Accepts a fixed reason code."""
    if os.geteuid() != 0 and os.environ.get("AFS_OPS_ALLOW_NONROOT") != "1":
        print("refusal recorder must run as root", file=sys.stderr)
        return 1
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 2
    reason = payload.get("reason")
    actor = payload.get("actor")
    if reason not in _REFUSAL_REASONS:
        return 2
    if not isinstance(actor, str) or not _ACTOR.fullmatch(actor):
        actor = "grok-ops"
    try:
        AuditLog(os.environ.get("AFS_OPS_AUDIT_LOG", "/var/lib/afs-ops/audit.log")).append(
            actor=actor,
            verb="INVALID",
            sha="",
            token_id="",
            result=reason,
        )
    except OSError:
        print("audit log unavailable", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("AFS_OPS_RECORD_REFUSAL") == "1":
        return record_refusal_main()
    if os.environ.get("AFS_OPS_DISPATCH_ARGV") == "1":
        raw = " ".join(sys.argv[1:] if argv is None else argv)
    else:
        raw = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    try:
        parsed = parse_command(raw)
    except CommandRejected as exc:
        return _refuse(exc.reason)
    path = _VERB_PATH[parsed.verb]
    payload = {
        "verb": parsed.verb,
        "sha": parsed.sha or "",
        "release": parsed.release or "",
        "actor": _actor(),
    }
    completed = subprocess.run(
        ["sudo", "-n", path],
        input=json.dumps(payload),
        text=True,
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
