"""Daily backup of the irreplaceable proof/trade data to a dir OUTSIDE the repo.

The journal (``journal_*.jsonl``) and ledgers (``*.sqlite``) are the source-of-truth
trade record + go-live proof — they cannot be regenerated, and a stray ``git clean``
in the repo working tree would wipe them. This copies them to ``PROOF_BACKUP_DIR``
(default ``~/afs-backups``, outside the repo) so an accidental wipe is recoverable.

    cd /root/autonomous-futures-system && PROOF_BACKUP_DIR=/root/afs-backups \
        PYTHONPATH=. .venv/bin/python -m scripts.backup_proof_data

- Immutable date-named files (journal/bars/adaptive) are copied if missing or
  changed size (rsync-like, cheap).
- Mutating SQLite ledgers are backed up consistently into date-named snapshots.
  Keep exactly two on-server snapshots per active ledger; Mac off-server
  retention is managed separately (30 days). The old KEEP_DAYS knob is rejected.

Read-only w.r.t. live source data; fail-closed on disk or copy errors.
"""

from __future__ import annotations

import os
import re
import shutil
import json
import sqlite3
import tempfile
from contextlib import closing
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



# ── I/O (fail-closed; never write to the live source) ────────────────────────
KEEP_SNAPSHOTS = 2
RESERVE_BYTES = 512 * 1024 * 1024


def snapshots_to_prune(
    names: list[str], active_stems: set[str], keep: int = KEEP_SNAPSHOTS
) -> list[str]:
    """Only prune dated snapshots of active ledgers; keep newest N per stem.

    Unknown files, invalid dates, and other ledgers are never deletion targets.
    """
    if keep < 1:
        raise ValueError("keep must be positive")
    by_stem: dict[str, list[tuple[date, str]]] = {}
    for name in names:
        m = re.fullmatch(r"(.+)\.(\d{4}-\d{2}-\d{2})\.sqlite", name)
        if m is None or m.group(1) not in active_stems:
            continue
        try:
            when = date.fromisoformat(m.group(2))
        except ValueError:
            continue
        by_stem.setdefault(m.group(1), []).append((when, name))
    to_prune: list[str] = []
    for entries in by_stem.values():
        entries.sort(reverse=True)
        to_prune.extend(name for _, name in entries[keep:])
    return sorted(to_prune)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _disk_free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _sqlite_quick_check(path: Path) -> None:
    # immutable=1 guarantees no WAL/SHM writes to the snapshot itself.
    with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro&immutable=1", uri=True) as db:
        result = db.execute("PRAGMA quick_check(1)").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"snapshot failed SQLite quick_check: {path.name}")


def _atomic_mirror(source: Path, target: Path) -> None:
    fd, tmpname = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
    os.close(fd)
    tmp = Path(tmpname)
    try:
        initial_bytes = source.stat().st_size
        shutil.copy2(source, tmp)
        os.chmod(tmp, 0o600)
        if tmp.stat().st_size != initial_bytes or source.stat().st_size != initial_bytes:
            raise RuntimeError(f"source changed during mirror copy: {source.name}")
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _snapshot_sqlite_atomic(source: Path, target: Path) -> None:
    """Use SQLite's online backup API; copying an actively written DB is unsafe."""
    fd, tmpname = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=target.parent)
    os.close(fd)
    tmp = Path(tmpname)
    try:
        # SQLite must create the target schema through the backup API.
        with closing(sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True, timeout=30)) as live:
            with closing(sqlite3.connect(str(tmp), timeout=30)) as snapshot:
                live.backup(snapshot, pages=1024, sleep=0.05)
        _sqlite_quick_check(tmp)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def _write_receipt_atomic(dest: Path, receipt: dict) -> None:
    target = dest / "backup_proof_data_latest.json"
    fd, tmpname = tempfile.mkstemp(prefix=".backup_proof_data_latest.", suffix=".part", dir=dest)
    tmp = Path(tmpname)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(receipt, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: Optional[list[str]] = None) -> int:
    src_dir = Path(os.getenv("LOG_DIR", "logs"))
    dest = Path(os.getenv("PROOF_BACKUP_DIR", os.path.expanduser("~/afs-backups")))
    snap_dir = dest / "snapshots"

    # The old cron sets KEEP_DAYS=3. It MUST be changed before resumption;
    # age-based retention cannot enforce exactly two server snapshots.
    if "PROOF_BACKUP_KEEP_DAYS" in os.environ:
        print("[backup] FAIL: obsolete PROOF_BACKUP_KEEP_DAYS; use PROOF_BACKUP_KEEP_SNAPSHOTS=2")
        return 1
    if os.getenv("PROOF_BACKUP_KEEP_SNAPSHOTS", "2") != "2":
        print("[backup] FAIL: VPS retention policy requires exactly two snapshots")
        return 1
    if not src_dir.is_dir() or src_dir.resolve() == dest.resolve():
        print("[backup] FAIL: missing source directory or unsafe source/destination")
        return 1
    try:
        dest.mkdir(parents=True, exist_ok=True)
        snap_dir.mkdir(parents=True, exist_ok=True)
        receipt_path = dest / "backup_proof_data_latest.json"
        # An interrupted/failed new run must not leave an apparently fresh
        # successful receipt from the same day.
        receipt_path.unlink(missing_ok=True)

        # Inventory before any writes, rejecting links into unexpected paths.
        src_files = {}
        for f in src_dir.iterdir():
            if f.name.startswith(_COPY_PATTERNS):
                if f.is_symlink():
                    raise RuntimeError(f"symlinked source rejected: {f.name}")
                if f.is_file():
                    src_files[f.name] = f.stat().st_size
        ledgers = sorted(src_dir.glob("*.sqlite"))
        if any(p.is_symlink() or not p.is_file() for p in ledgers):
            raise RuntimeError("symlinked or invalid SQLite source")
        if not src_files and not ledgers:
            raise RuntimeError("no journal or SQLite sources found")

        dst_files = {f.name: f.stat().st_size for f in dest.iterdir() if f.is_file()}
        mirrors = files_to_copy(src_files, dst_files)
        today = _today_utc()
        needs_snapshot: list[tuple[Path, Path]] = []
        for source in ledgers:
            target = snap_dir / f"{source.stem}.{today.isoformat()}.sqlite"
            if target.is_symlink():
                raise RuntimeError(f"symlinked snapshot target rejected: {target.name}")
            if target.is_file():
                _sqlite_quick_check(target)
            else:
                needs_snapshot.append((source, target))

        planned = sum(src_files[name] for name in mirrors) + sum(
            source.stat().st_size for source, _ in needs_snapshot
        )
        # Account for SQLite page/WAL growth and leave a bounded safety floor.
        headroom = max(RESERVE_BYTES, planned // 4)
        free = _disk_free_bytes(dest)
        if free < planned + headroom:
            raise RuntimeError(
                f"insufficient free space: free={free} required={planned + headroom}"
            )

        for name in mirrors:
            _atomic_mirror(src_dir / name, dest / name)
        for source, target in needs_snapshot:
            _snapshot_sqlite_atomic(source, target)

        existing = [f.name for f in snap_dir.iterdir() if f.is_file()]
        prune = snapshots_to_prune(
            existing, {source.stem for source in ledgers}, keep=KEEP_SNAPSHOTS
        )
        # No prune on failed snapshot; unknown names are never touched.
        for name in prune:
            (snap_dir / name).unlink()

        _write_receipt_atomic(dest, {
            "job": "backup_proof_data",
            "status": "OK",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "evidence_dir": str(dest),
            "copied": len(mirrors),
            "ledger_snapshots": len(needs_snapshot),
            "pruned": len(prune),
            "retained_snapshots_per_ledger": KEEP_SNAPSHOTS,
        })
        print(
            f"[backup] {today} -> {dest}: {len(mirrors)} copied, "
            f"{len(needs_snapshot)} snapshot(s), {len(prune)} pruned; OK"
        )
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        # Failures are surfaced to cron/ops; never declare the backup green.
        print(f"[backup] FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
