"""ops/afs_watcher/archive_events.py — persistent copy of the watcher's tmpfs evidence."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "ops" / "afs_watcher" / "archive_events.py"


@pytest.fixture
def mod():
    spec = importlib.util.spec_from_file_location("archive_events", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _row(i: int) -> str:
    return json.dumps({"utc": f"2026-09-21T00:{i:02d}:00Z", "kind": "BLOCKED", "key": f"k{i}"}, sort_keys=True)


def test_appends_only_unseen_lines_and_copies_snapshots(mod, tmp_path):
    src, dst = tmp_path / "tmp", tmp_path / "arch"
    (src / "snapshots").mkdir(parents=True)
    (src / "events.jsonl").write_text(_row(1) + "\n" + _row(2) + "\n")
    (src / "snapshots" / "a.json").write_text("{}")

    assert mod.archive(src, dst) == (2, 1)
    # second pass: nothing new
    assert mod.archive(src, dst) == (0, 0)
    # a new row and a new snapshot arrive
    (src / "events.jsonl").write_text(_row(1) + "\n" + _row(2) + "\n" + _row(3) + "\n")
    (src / "snapshots" / "b.json").write_text("{}")
    assert mod.archive(src, dst) == (1, 1)
    assert (dst / "events.jsonl").read_text().splitlines() == [_row(1), _row(2), _row(3)]
    assert sorted(p.name for p in (dst / "snapshots").iterdir()) == ["a.json", "b.json"]


def test_tmpfs_reset_does_not_duplicate_or_lose(mod, tmp_path):
    src, dst = tmp_path / "tmp", tmp_path / "arch"
    src.mkdir()
    (src / "events.jsonl").write_text(_row(1) + "\n" + _row(2) + "\n")
    mod.archive(src, dst)
    # reboot: tmpfs empty, then a fresh row appears
    (src / "events.jsonl").write_text(_row(9) + "\n")
    assert mod.archive(src, dst) == (1, 0)
    assert (dst / "events.jsonl").read_text().splitlines() == [_row(1), _row(2), _row(9)]


def test_missing_source_is_a_noop(mod, tmp_path):
    assert mod.archive(tmp_path / "nope", tmp_path / "arch") == (0, 0)
    assert (tmp_path / "arch" / "events.jsonl").exists() is False


def test_never_writes_under_source(mod, tmp_path):
    src = tmp_path / "tmp"
    src.mkdir()
    assert mod.main(["--src", str(src), "--dst", str(src / "archive")]) == 2
    assert mod.main(["--src", str(src), "--dst", str(src)]) == 2
    assert not (src / "archive").exists()


def test_unit_files_reference_the_script():
    d = SCRIPT.parent
    assert "archive_events.py" in (d / "afs-watcher-archive.service").read_text()
    assert "afs-watcher-archive.service" in (d / "afs-watcher-archive.timer").read_text()
