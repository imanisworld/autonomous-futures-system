"""authorized_keys forced-command line for grok-ops.

The guard requires every restriction on its own. Dropping one option, or
pointing command= at a different path, is a failure.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DISPATCHER_PATH = "/usr/local/sbin/afs-grok-ops"
_KEY_TYPES = frozenset({"ssh-ed25519", "ssh-rsa", "ecdsa-sha2-nistp256", "sk-ssh-ed25519@openssh.com"})
_REQUIRED_FLAGS = (
    "restrict",
    "no-pty",
    "no-port-forwarding",
    "no-agent-forwarding",
    "no-X11-forwarding",
)
_ALLOWED_OPTIONS = frozenset((*_REQUIRED_FLAGS, "command"))
_KEY_MATERIAL = re.compile(r"[A-Za-z0-9+/=]+")
_COMMENT = re.compile(r"[A-Za-z0-9_.@+-]+")


class KeyGuardError(Exception):
    pass


def render_authorized_keys_line(public_key: str) -> str:
    parts = public_key.strip().split()
    if len(parts) < 2 or parts[0] not in _KEY_TYPES:
        raise KeyGuardError("public key must be a bare ssh key")
    if parts[0].startswith("command=") or parts[0] in _REQUIRED_FLAGS:
        raise KeyGuardError("public key must not include options")
    if not _KEY_MATERIAL.fullmatch(parts[1]):
        raise KeyGuardError("public key material is not base64")
    comment = parts[2] if len(parts) > 2 and _COMMENT.fullmatch(parts[2]) else "grok-ops"
    options = ",".join(
        (
            "restrict",
            f'command="{DISPATCHER_PATH}"',
            "no-pty",
            "no-port-forwarding",
            "no-agent-forwarding",
            "no-X11-forwarding",
        )
    )
    return f"{options} {parts[0]} {parts[1]} {comment}"


def _option_map(blob: str) -> dict[str, str | None]:
    tokens: list[str] = []
    current: list[str] = []
    in_quote = False
    for char in blob:
        if char == '"':
            in_quote = not in_quote
            current.append(char)
            continue
        if char == "," and not in_quote:
            tokens.append("".join(current))
            current = []
            continue
        current.append(char)
    if in_quote:
        raise KeyGuardError("unbalanced quotes in key options")
    if current:
        tokens.append("".join(current))
    parsed: dict[str, str | None] = {}
    for token in tokens:
        if not token:
            raise KeyGuardError("empty key option")
        if "=" in token:
            name, value = token.split("=", 1)
            if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                value = value[1:-1]
            parsed[name] = value
        else:
            parsed[token] = None
    return parsed


def validate_authorized_key_line(line: str) -> None:
    parts = line.strip().split()
    if len(parts) < 3:
        raise KeyGuardError("authorized_keys line is incomplete")
    options = _option_map(parts[0])
    if set(options) != _ALLOWED_OPTIONS:
        raise KeyGuardError("authorized_keys options do not match the forced-command set")
    for flag in _REQUIRED_FLAGS:
        if options.get(flag) is not None:
            raise KeyGuardError(f"{flag} must be a bare flag")
    if options.get("command") != DISPATCHER_PATH:
        raise KeyGuardError("command= must be the grok-ops dispatcher")
    if parts[1] not in _KEY_TYPES:
        raise KeyGuardError("unsupported key type")
    if not _KEY_MATERIAL.fullmatch(parts[2]):
        raise KeyGuardError("public key material is not base64")
    if len(parts) > 3 and not _COMMENT.fullmatch(parts[3]):
        raise KeyGuardError("key comment contains unsupported characters")
    if len(parts) > 4:
        raise KeyGuardError("authorized_keys line has trailing fields")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "--public-key":
        print("usage: python -m ops.grok_ops.keys --public-key KEYFILE", file=sys.stderr)
        return 2
    public_key = Path(args[1]).read_text(encoding="utf-8")
    try:
        line = render_authorized_keys_line(public_key)
        validate_authorized_key_line(line)
    except KeyGuardError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    sys.stdout.write(line + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
