#!/usr/bin/env python3
"""Fail when this research branch touches a path outside its approved scope."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


ALLOWED_PREFIXES = (
    "docs/strategy-rules/",
    "research/",
    "scripts/",
    "tests/",
)


def is_allowed(path: str) -> bool:
    return any(path.startswith(prefix) for prefix in ALLOWED_PREFIXES)


def _git_lines(repo: Path, *args: str) -> set[str]:
    result = subprocess.run(
        ("git", *args),
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def changed_paths(repo: Path, base: str) -> set[str]:
    paths = set()
    paths |= _git_lines(repo, "diff", "--name-only", f"{base}...HEAD")
    paths |= _git_lines(repo, "diff", "--name-only")
    paths |= _git_lines(repo, "diff", "--cached", "--name-only")
    paths |= _git_lines(repo, "ls-files", "--others", "--exclude-standard")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args()
    paths = changed_paths(args.repo.resolve(), args.base)
    prohibited = sorted(path for path in paths if not is_allowed(path))
    if prohibited:
        print("PROHIBITED PATHS CHANGED:")
        for path in prohibited:
            print(path)
        return 1
    print(f"scope check passed: {len(paths)} changed path(s), all research-only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
