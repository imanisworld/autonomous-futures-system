"""Regression tests for the watcher's bounded tmpfs log sink."""
from __future__ import annotations

import importlib.util
import io
from pathlib import Path


ROOT = Path(__file__).parent.parent
MODULE_PATH = ROOT / "ops" / "afs_watcher" / "bounded_log_pipe.py"


def _load():
    spec = importlib.util.spec_from_file_location("afs_bounded_log_pipe_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


logpipe = _load()


def test_stream_is_bounded_and_keeps_newest_lines(tmp_path):
    path = tmp_path / "watcher.stdout.log"
    rows = [f"{i:05d}-" + ("x" * 90) + "\n" for i in range(300)]
    source = io.BytesIO("".join(rows).encode())

    logpipe.stream_to_bounded_log(
        source,
        path,
        max_bytes=8192,
        keep_bytes=4096,
    )

    data = path.read_text()
    assert path.stat().st_size <= 8192
    assert data.endswith(rows[-1])
    assert rows[0] not in data
    assert rows[-10] in data


def test_existing_log_is_also_bounded_when_new_data_arrives(tmp_path):
    path = tmp_path / "watcher.stdout.log"
    path.write_bytes((b"old-line\n" * 2000))
    source = io.BytesIO(b"newest-line\n")

    logpipe.stream_to_bounded_log(
        source,
        path,
        max_bytes=4096,
        keep_bytes=2048,
    )

    assert path.stat().st_size <= 4096
    assert path.read_bytes().endswith(b"newest-line\n")


def test_trim_aligns_to_line_boundary(tmp_path):
    path = tmp_path / "watcher.stdout.log"
    source = io.BytesIO(
        b"first-line\n" + b"A" * 3000 + b"\nlast-complete-line\n"
    )

    logpipe.stream_to_bounded_log(
        source,
        path,
        max_bytes=2048,
        keep_bytes=1024,
    )

    data = path.read_bytes()
    assert not data.startswith(b"A")
    assert data.endswith(b"last-complete-line\n")


def test_invalid_caps_fail_closed(tmp_path):
    path = tmp_path / "watcher.stdout.log"
    for max_bytes, keep_bytes in ((0, 1), (10, 0), (10, 10), (10, 11)):
        try:
            logpipe.stream_to_bounded_log(
                io.BytesIO(b"x"),
                path,
                max_bytes=max_bytes,
                keep_bytes=keep_bytes,
            )
        except ValueError:
            pass
        else:
            raise AssertionError((max_bytes, keep_bytes))


def test_oversized_existing_log_is_trimmed_even_with_empty_input(tmp_path):
    path = tmp_path / "watcher.stdout.log"
    path.write_bytes((b"old-line\n" * 2000))

    logpipe.stream_to_bounded_log(
        io.BytesIO(b""),
        path,
        max_bytes=4096,
        keep_bytes=2048,
    )

    assert path.stat().st_size <= 4096
