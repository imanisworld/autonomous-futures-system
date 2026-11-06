"""Root-only GO token mint.

Installed as afs-ops-authorize. It is not in the grok-ops sudoers drop-in,
so the deploy user cannot mint a token for itself.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ops.grok_ops.allowlist import parse_command
from ops.grok_ops.tokens import TokenError, TokenStore


def main(argv: list[str] | None = None, *, euid: int | None = None, store_root: Path | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    uid = os.geteuid() if euid is None else euid
    if uid != 0:
        print("afs-ops-authorize must run as root", file=sys.stderr)
        return 1
    if len(args) != 2:
        print("usage: afs-ops-authorize <sha> <ledger-id>", file=sys.stderr)
        return 2
    try:
        parsed = parse_command(f"promote {args[0]}")
    except Exception:
        print("usage: afs-ops-authorize <sha> <ledger-id>", file=sys.stderr)
        return 2
    root = Path(store_root) if store_root is not None else Path(
        os.environ.get("AFS_OPS_TOKEN_DIR", "/var/lib/afs-ops/tokens")
    )
    try:
        token_id = TokenStore(root).authorize(parsed.sha or "", args[1])
    except TokenError:
        print("usage: afs-ops-authorize <sha> <ledger-id>", file=sys.stderr)
        return 2
    print(token_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
