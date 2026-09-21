#!/usr/bin/env python3
"""Copy the watcher's tmpfs evidence to persistent disk.

/tmp/afs_watcher is tmpfs by design (state.json must not survive a reboot — see
ops/watcher_memory_guard.py), and run_ro.sh remounts /root read-only inside the
watcher's mount namespace, so the watcher itself CANNOT write anywhere durable.
The cost surfaced 2026-09-21: events.jsonl — the watcher's own record of every
BLOCKED raise, REBASELINED adoption and DAILY verdict — only reached back to the
last reboot (43 rows), and the 2026-09-14 feed outage that motivated the ACTION
REQUIRED cards had no on-box evidence left at all.

This runs OUTSIDE the namespace (root, from a systemd timer) and only reads
/tmp/afs_watcher.  It appends lines it has not archived yet to a persistent
events.jsonl and mirrors the snapshots that BLOCKED rows point at.  Dedupe is by
exact line: every row carries its own UTC stamp, so a tmpfs reset (reboot) or a
supervisor restart cannot double-append.  Nothing under /tmp is ever written.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEFAULT_SRC = Path("/tmp/afs_watcher")
DEFAULT_DST = Path("/root/afs-shared/afs_watcher_archive")


def archive(src: Path, dst: Path) -> tuple[int, int]:
    """Append unseen events.jsonl lines and copy unseen snapshots. Returns (lines, snapshots)."""
    src_events = src / "events.jsonl"
    dst_events = dst / "events.jsonl"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "snapshots").mkdir(exist_ok=True)

    seen: set[str] = set()
    if dst_events.exists():
        seen = {ln for ln in dst_events.read_text(encoding="utf-8").splitlines() if ln}

    new_lines: list[str] = []
    if src_events.exists():
        for ln in src_events.read_text(encoding="utf-8").splitlines():
            if ln and ln not in seen:
                new_lines.append(ln)
                seen.add(ln)
    if new_lines:
        with dst_events.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(new_lines) + "\n")

    copied = 0
    src_snaps = src / "snapshots"
    if src_snaps.is_dir():
        for p in sorted(src_snaps.iterdir()):
            if p.is_file() and not (dst / "snapshots" / p.name).exists():
                shutil.copy2(p, dst / "snapshots" / p.name)
                copied += 1
    return len(new_lines), copied


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC)
    ap.add_argument("--dst", type=Path, default=DEFAULT_DST)
    args = ap.parse_args(argv)
    if args.dst.resolve() == args.src.resolve() or args.src.resolve() in args.dst.resolve().parents:
        print("refusing: destination must not be inside the tmpfs source", file=sys.stderr)
        return 2
    lines, snaps = archive(args.src, args.dst)
    print(f"archived events={lines} snapshots={snaps} -> {args.dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
