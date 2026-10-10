"""Production dependency version-lock checks (U11).

``requirements.lock`` is the one authoritative production lock. This module
checks, without network access:

* every lock line is an exact ``name==version`` pin, with no duplicates;
* the lock satisfies every direct requirement in ``requirements.txt``;
* a post-install ``pip freeze`` equals the lock exactly (no missing, extra or
  drifted distribution) — used by ``scripts/atomic_release.sh build``;
* every third-party module imported by production code resolves to a locked
  distribution (tests).
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
LOCK_REL = "requirements.lock"
REQUIREMENTS_REL = "requirements.txt"
INSTALLER_DISTS = frozenset({"pip", "setuptools", "wheel"})
LOCK_PYTHON = (3, 13)

# Directories that are not part of the futures release's Python runtime.
NON_PRODUCTION_DIRS = frozenset(
    {"tests", ".git", ".venv", "__pycache__", "interactive-course", "site", "share"}
)
# Components with their own, separate dependency manifest and service unit.
SEPARATE_COMPONENTS = {
    "ops/push_relay": "own requirements.txt + afs-push-relay.service; not installed into the release venv",
    "ops/afs_watcher": (
        "persistent read-only watcher service; synchronized separately from the release "
        "and runs outside the release venv"
    ),
}

# A few historical/operator files are executed directly and intentionally use
# sibling absolute imports. Keep that exception explicit and narrow so ordinary
# package code cannot hide a third-party import behind a same-named sibling.
SCRIPT_STYLE_LOCAL_PREFIXES = (
    "scripts/",
    "research/sd_zone_round4/frozen_round4_code/",
)

_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9][A-Za-z0-9.+!_-]*)$")


class LockError(ValueError):
    """The lock or an install does not match the lock contract."""


def python_version_problem(version_info=None) -> Optional[str]:
    observed = tuple((version_info or sys.version_info)[:2])
    if observed != LOCK_PYTHON:
        return (
            f"dependency lock requires Python {LOCK_PYTHON[0]}.{LOCK_PYTHON[1]}, "
            f"observed {observed[0]}.{observed[1]}"
        )
    return None

def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.match(line)
        if not match:
            raise LockError(f"lock line {lineno} is not an exact name==version pin: {line!r}")
        name = canonical(match.group(1))
        if name in pins:
            raise LockError(f"lock pins {name} more than once")
        pins[name] = match.group(2)
    if not pins:
        raise LockError("lock pins nothing")
    return pins


def parse_freeze(text: str) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _PIN.match(line)
        if not match:
            raise LockError(f"freeze line is not a name==version pin: {line!r}")
        name = canonical(match.group(1))
        if name in pins:
            raise LockError(f"freeze lists {name} more than once")
        pins[name] = match.group(2)
    return pins


def freeze_problems(lock: Mapping[str, str], freeze: Mapping[str, str]) -> list[str]:
    problems: list[str] = []
    installed = {k: v for k, v in freeze.items() if k not in INSTALLER_DISTS}
    for name, version in sorted(lock.items()):
        if name not in installed:
            problems.append(f"locked {name}=={version} is not installed")
        elif installed[name] != version:
            problems.append(f"{name} installed {installed[name]} but lock pins {version}")
    for name in sorted(set(installed) - set(lock)):
        problems.append(f"{name}=={installed[name]} is installed but not in the lock")
    return problems


def _requirement_lines(path: Path, seen: Optional[set[Path]] = None) -> list[str]:
    seen = seen or set()
    path = path.resolve()
    if path in seen:
        return []
    seen.add(path)
    lines: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith(("-r ", "--requirement ")):
            lines.extend(_requirement_lines(path.parent / line.split(None, 1)[1], seen))
            continue
        if line.startswith("-"):
            raise LockError(f"unsupported requirements option: {line!r}")
        lines.append(line)
    return lines


def requirement_problems(requirements: Path, lock: Mapping[str, str]) -> list[str]:
    try:
        from packaging.requirements import Requirement
    except ImportError:  # release operator host: pip's vendored copy
        from pip._vendor.packaging.requirements import Requirement

    problems: list[str] = []
    for line in _requirement_lines(requirements):
        try:
            req = Requirement(line)
        except Exception as exc:
            raise LockError(f"invalid requirement {line!r}: {exc}") from exc
        if req.marker is not None and not req.marker.evaluate():
            continue
        name = canonical(req.name)
        if name not in lock:
            problems.append(f"requirement {line!r} is not in the lock")
        elif not req.specifier.contains(lock[name], prereleases=True):
            problems.append(f"lock pins {name}=={lock[name]}, which does not satisfy {line!r}")
    return problems


def _local_module_names(root: Path) -> set[str]:
    """Top-level names importable from the repository root.

    A nested file stem is not automatically importable as a top-level module.
    Treating every nested foo.py as local could hide a real third-party
    import foo from the dependency lock audit.
    """
    names: set[str] = set()
    for child in root.iterdir():
        if child.name in NON_PRODUCTION_DIRS:
            continue
        if child.is_file() and child.suffix == ".py":
            names.add(child.stem)
        elif child.is_dir() and any(path.suffix == ".py" for path in child.rglob("*.py")):
            names.add(child.name)
    return names

def _module_exists_at(base: Path, name: str) -> bool:
    if (base / f"{name}.py").is_file():
        return True
    package = base / name
    return package.is_dir() and any(path.suffix == ".py" for path in package.rglob("*.py"))


def _script_style_local_import(root: Path, path: Path, name: str) -> bool:
    rel = path.relative_to(root).as_posix()
    if not any(rel.startswith(prefix) for prefix in SCRIPT_STYLE_LOCAL_PREFIXES):
        return False
    return _module_exists_at(path.parent, name)

def production_third_party_imports(root: Path) -> dict[str, set[str]]:
    """Top-level third-party module -> files importing it (production code)."""
    root = Path(root)
    local = _local_module_names(root)
    found: dict[str, set[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in NON_PRODUCTION_DIRS for part in rel.parts):
            continue
        if any(rel.as_posix().startswith(prefix + "/") for prefix in SEPARATE_COMPONENTS):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError) as exc:
            raise LockError(f"cannot parse {rel}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".", 1)[0]
                if (
                    top == "__future__"
                    or top in sys.stdlib_module_names
                    or top in local
                    or _script_style_local_import(root, path, top)
                ):
                    continue
                found.setdefault(top, set()).add(rel.as_posix())
    return found


def import_problems(
    imports: Mapping[str, Iterable[str]],
    lock: Mapping[str, str],
    module_to_dists: Mapping[str, Iterable[str]],
) -> list[str]:
    problems: list[str] = []
    for module, files in sorted(imports.items()):
        dists = {canonical(d) for d in module_to_dists.get(module, [])}
        if dists and dists <= INSTALLER_DISTS:
            continue  # pip/setuptools/wheel exist in every venv by construction
        if not dists:
            problems.append(
                f"production import {module!r} ({sorted(files)[0]}) maps to no known distribution"
            )
        elif not dists & set(lock):
            problems.append(
                f"production import {module!r} needs {sorted(dists)} which is not in the lock"
            )
    return problems


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Production dependency lock checks.")
    sub = parser.add_subparsers(dest="command", required=True)
    chk = sub.add_parser("check-freeze", help="Fail unless an installed freeze equals the lock")
    chk.add_argument("--lock", type=Path, required=True)
    chk.add_argument("--freeze", type=Path, required=True)
    req = sub.add_parser("check-requirements", help="Fail unless the lock satisfies requirements")
    req.add_argument("--lock", type=Path, default=ROOT / LOCK_REL)
    req.add_argument("--requirements", type=Path, default=ROOT / REQUIREMENTS_REL)
    sub.add_parser("check-python", help="Fail unless the interpreter matches the lock's Python minor")
    args = parser.parse_args(argv)

    if args.command == "check-python":
        problem = python_version_problem()
        if problem:
            print(f"DEPENDENCY LOCK BLOCKED: {problem}", file=sys.stderr)
            return 1
        return 0

    try:
        lock = parse_lock(args.lock.read_text(encoding="utf-8"))
        if args.command == "check-freeze":
            problems = freeze_problems(lock, parse_freeze(args.freeze.read_text(encoding="utf-8")))
        else:
            problems = requirement_problems(args.requirements, lock)
    except (LockError, OSError) as exc:
        print(f"DEPENDENCY LOCK BLOCKED: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(f"DEPENDENCY LOCK BLOCKED: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
