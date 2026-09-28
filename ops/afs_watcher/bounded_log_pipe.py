#!/usr/bin/env python3
"""Bound a single append-only diagnostic log while streaming stdin into it.

This helper is intentionally narrow: one process owns the destination file, so
trimming cannot race with a second writer holding the old inode open. It is
used only for non-durable watcher stdout under /tmp/afs_watcher.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


DEFAULT_MAX_BYTES = 4 * 1024 * 1024
DEFAULT_KEEP_BYTES = 2 * 1024 * 1024
CHUNK_BYTES = 64 * 1024


def _validate(max_bytes: int, keep_bytes: int) -> None:
    if max_bytes <= 0:
        raise ValueError("max-bytes must be positive")
    if keep_bytes <= 0 or keep_bytes >= max_bytes:
        raise ValueError("keep-bytes must be positive and smaller than max-bytes")


def _trim(path: Path, keep_bytes: int) -> None:
    """Replace path with its newest keep_bytes, aligned to the next newline."""
    size = path.stat().st_size
    start = max(0, size - keep_bytes)
    with path.open("rb") as src:
        src.seek(start)
        data = src.read()
    if start:
        newline = data.find(b"\n")
        if newline >= 0:
            data = data[newline + 1 :]

    tmp = path.with_name(f".{path.name}.trim.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)


def stream_to_bounded_log(
    source,
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    keep_bytes: int = DEFAULT_KEEP_BYTES,
) -> None:
    _validate(max_bytes, keep_bytes)
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.stat().st_size > max_bytes:
        _trim(path, keep_bytes)

    sink = path.open("ab", buffering=0)
    try:
        while True:
            chunk = source.read(CHUNK_BYTES)
            if not chunk:
                break
            sink.write(chunk)
            if os.fstat(sink.fileno()).st_size > max_bytes:
                sink.close()
                _trim(path, keep_bytes)
                sink = path.open("ab", buffering=0)
    finally:
        if not sink.closed:
            sink.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--keep-bytes", type=int, default=DEFAULT_KEEP_BYTES)
    args = parser.parse_args(argv)
    try:
        stream_to_bounded_log(
            sys.stdin.buffer,
            args.path,
            max_bytes=args.max_bytes,
            keep_bytes=args.keep_bytes,
        )
    except (OSError, ValueError) as exc:
        print(f"bounded log sink failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
