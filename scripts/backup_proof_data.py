"""Daily backup of the irreplaceable proof/trade data to a dir OUTSIDE the repo.

The journal (``journal_*.jsonl``) and ledgers (``*.sqlite``) are the source-of-truth
trade record + go-live proof — they cannot be regenerated, and a stray ``git clean``
in the repo working tree would wipe them. This copies them to ``PROOF_BACKUP_DIR``
(default ``~/afs-backups``, outside the repo) so an accidental wipe is recoverable.

    cd /root/autonomous-futures-system && PROOF_BACKUP_DIR=/root/afs-backups \
        PYTHONPATH=. .venv/bin/python -m scripts.backup_proof_data

- Immutable date-named files (journal/bars/adaptive) are copied if missing or
  changed size (rsync-like, cheap).
- Mutating ``*.sqlite`` ledgers are snapshotted per-day (``name.YYYY-MM-DD.sqlite``)
  and pruned after ``PROOF_BACKUP_KEEP_DAYS`` (default 120).

Read-only w.r.t. the live system; fail-soft.
"""

from __future__ import annotations

import os
import re
import shutil
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

_COPY_PATTERNS = ("journal_", "bars_", "adaptive_review_", "weekly_review_")
_SNAP_DATE = re.compile(r"\.(\d{4}-\d{2}-\d{2})\.sqlite$")


# ── pure ───────────────────────────────────────────────────────────────────
def files_to_copy(src: dict[str, int], dst: dict[str, int]) -> list[str]:
    """Names present in src that are missing from dst or differ in size."""
    return sorted(n for n, sz in src.items() if dst.get(n) != sz)


def expired_snapshots(names: list[str], today: date, keep_days: int) -> list[str]:
    """Snapshot filenames (name.YYYY-MM-DD.sqlite) older than keep_days."""
    out = []
    for n in names:
        m = _SNAP_DATE.search(n)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if (today - d).days > keep_days:
            out.append(n)
    return out


# ── I/O (fail-soft) ────────────────────────────────────────────────────────
def main(argv: Optional[list[str]] = None) -> int:
    src_dir = Path(os.getenv("LOG_DIR", "logs"))
    dest = Path(os.getenv("PROOF_BACKUP_DIR", os.path.expanduser("~/afs-backups")))
    keep_days = int(os.getenv("PROOF_BACKUP_KEEP_DAYS", "120"))
    today = datetime.now(timezone.utc).date()

    if not src_dir.exists():
        print(f"[backup] source {src_dir} missing — nothing to do")
        return 0
    snap_dir = dest / "snapshots"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        snap_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"[backup] cannot create {dest}: {e}")
        return 1

    # 1) immutable date-named files -> flat mirror (copy if new/changed)
    src_files = {
        f.name: f.stat().st_size
        for f in src_dir.iterdir()
        if f.is_file() and f.name.startswith(_COPY_PATTERNS)
    }
    dst_files = {f.name: f.stat().st_size for f in dest.iterdir() if f.is_file()}
    copied = 0
    for name in files_to_copy(src_files, dst_files):
        try:
            shutil.copy2(src_dir / name, dest / name)
            copied += 1
        except OSError as e:
            print(f"[backup] copy failed {name}: {e}")

    # 2) mutating ledgers -> per-day snapshot
    snapped = 0
    for f in src_dir.glob("*.sqlite"):
        target = snap_dir / f"{f.stem}.{today.isoformat()}.sqlite"
        try:
            shutil.copy2(f, target)
            snapped += 1
        except OSError as e:
            print(f"[backup] snapshot failed {f.name}: {e}")

    # 3) prune old snapshots
    pruned = 0
    existing_snaps = [f.name for f in snap_dir.iterdir() if f.is_file()]
    for name in expired_snapshots(existing_snaps, today, keep_days):
        try:
            (snap_dir / name).unlink()
            pruned += 1
        except OSError:
            pass

    print(f"[backup] {today} -> {dest}: {copied} copied, {snapped} ledger snapshot(s), {pruned} pruned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
