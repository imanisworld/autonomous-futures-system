"""Startup release-integrity gate: verify deployed source against the release manifest.

Companion to ops/release_manifest.py. The manifest is built once, from a clean
checkout of canonical main, and shipped with the release. At service startup
this module re-hashes every file the manifest lists and scans the runtime
package dirs for first-party modules the manifest does NOT list. Any mismatch,
missing file, unexpected extra module, or missing out-of-band fingerprint pin
refuses startup. Enforcement is on unless RELEASE_INTEGRITY_ENFORCED is
explicitly false, 0, no, or off.

Deliberately git-free at runtime: the live box's git worktree is not a release
identifier (it is permanently dirty by deploy history), so verification relies
only on the manifest's SHA-256 entries. See
docs/incident-2026-07-01-direction-and-phantom-fills.md, follow-up #8.

Any ``__pycache__`` file inside the release fails verification. ``-B`` and
``PYTHONDONTWRITEBYTECODE=1`` stop the interpreter from writing new bytecode;
they do not stop it from loading a ``.pyc`` that is already on disk. The
Operator has to add ``-B`` on the unit ``ExecStart`` and keep the release tree
free of ``__pycache__``. This module does not edit the unit.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_MANIFEST_NAME = "release_manifest.json"
ENFORCE_ENV = "RELEASE_INTEGRITY_ENFORCED"
MANIFEST_PATH_ENV = "RELEASE_MANIFEST_PATH"
FINGERPRINT_PIN_ENV = "EXPECTED_RELEASE_FINGERPRINT"

# First-party import roots. A .py/.yaml file here that the manifest does not
# list is treated as drift: either an undeployed leftover that can shadow a
# release module, or a hand-copied hotfix that bypassed the release path.
RUNTIME_DIRS = (
    "webhook",
    "strategy",
    "execution",
    "risk",
    "context",
    "notifications",
    "adaptive",
    "sources",
    "journal",
    "config",
    "ops",
    "options_companion",
    "replay",
)
RUNTIME_SUFFIXES = (".py", ".yaml", ".yml")
# Extension modules, stray bytecode, and path hooks can shadow a shipped
# module or inject import-time code. Any file under __pycache__ is refused:
# a timestamp-valid .pyc can execute while its .py still matches the manifest.
EXTRA_RISK_SUFFIXES = (".so", ".pyc", ".pth")
CUSTOMIZE_MODULE_NAMES = {"sitecustomize.py", "usercustomize.py"}
PYCACHE_DIR_NAME = "__pycache__"


def _is_stray_env_file(name: str) -> bool:
    """True for secret env files. The tracked template .env.example is not one."""
    if name == ".env.example":
        return False
    return name == ".env" or name.startswith(".env.")


def _flag_unexpected_file(path: Path) -> bool:
    name = path.name
    if name in CUSTOMIZE_MODULE_NAMES:
        return True
    if _is_stray_env_file(name):
        return True
    if path.suffix in EXTRA_RISK_SUFFIXES or path.suffix in RUNTIME_SUFFIXES:
        return True
    return False


def _scan_directories(listed: set[str]) -> list[str]:
    """Directories whose unlisted modules can execute.

    ``RUNTIME_DIRS`` is the historical floor. It does not name every import
    root the service actually loads (``agent``, ``alert_ranker``,
    ``integrations``, ``options_manager``, ``quotes``, ``research``, and
    later packages). Those roots are taken from the manifest: every top-level
    directory that ships a ``.py`` file. A hardcoded addition would drift the
    same way ``RUNTIME_DIRS`` already did.
    """
    derived = set(RUNTIME_DIRS)
    for rel in listed:
        if not rel.endswith(".py") or "/" not in rel:
            continue
        top = rel.split("/", 1)[0]
        if top and not top.startswith("."):
            derived.add(top)
    return sorted(derived)


def _is_pycache(rel: Path) -> bool:
    """True for any file inside a release ``__pycache__`` directory."""
    return PYCACHE_DIR_NAME in rel.parts


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """Recompute the deterministic fingerprint over the manifest payload."""
    payload = {
        key: value
        for key, value in manifest.items()
        if key not in ("generated_at", "fingerprint_sha256")
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _runtime_extras(root: Path, listed: set[str]) -> list[str]:
    extras: list[str] = []

    def consider(path: Path) -> None:
        if not path.is_file():
            return
        rel_path = path.relative_to(root)
        rel = rel_path.as_posix()
        # Refuse every __pycache__ file, including a .pyc whose .py is in the
        # manifest. Python will load that bytecode when the header timestamp
        # or source hash matches, without the bytecode matching the source.
        if _is_pycache(rel_path):
            extras.append(rel)
            return
        if not _flag_unexpected_file(path):
            return
        if rel not in listed:
            extras.append(rel)

    # Root-level files (non-recursive): a stray repo-root module, .so, .pth,
    # sitecustomize, or .env can shadow imports or change startup. The live
    # box accumulated several root .py files from pre-release deploy history.
    for path in root.iterdir():
        consider(path)
    for dirname in _scan_directories(listed):
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            consider(path)
    extras.extend(_venv_site_hook_extras(root))
    return sorted(set(extras))


def _recorded_site_paths(venv: Path) -> set[str]:
    """Absolute paths of files named by installed ``*.dist-info/RECORD`` entries.

    ``python3 -m venv`` plus ``pip install -r requirements.txt`` (see
    ``scripts/atomic_release.sh``) records legitimate ``.pth`` files here.
    ``distutils-precedence.pth`` from setuptools is one. Editable installs
    record their ``__editable__.*.pth`` the same way. ``sitecustomize.py`` and
    ``usercustomize.py`` are not accepted from a RECORD: those names run at
    interpreter start and are not part of a normal wheel.
    """
    allowed: set[str] = set()
    for record in venv.rglob("RECORD"):
        if "site-packages" not in record.parts or not record.parent.name.endswith(".dist-info"):
            continue
        site_packages = record.parent.parent
        try:
            text = record.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for row in csv.reader(text.splitlines()):
            if not row:
                continue
            raw = row[0].strip()
            if not raw or raw.startswith("../"):
                continue
            allowed.add((site_packages / raw).resolve().as_posix())
    return allowed


def _venv_site_hook_extras(root: Path) -> list[str]:
    """Flag venv site hooks the release manifest does not install.

    The release venv is ``$RELEASES/$sha/.venv``. A ``*.pth``,
    ``sitecustomize.py``, or ``usercustomize.py`` under its ``site-packages``
    executes at interpreter start and is outside the manifest file list.
    A ``.pth`` is allowed only when an installed dist RECORD names it.
    """
    venv = root / ".venv"
    if not venv.is_dir():
        return []
    allowed = _recorded_site_paths(venv)
    flagged: list[str] = []
    for path in venv.rglob("*"):
        if not path.is_file() or "site-packages" not in path.parts:
            continue
        name = path.name
        if name not in CUSTOMIZE_MODULE_NAMES and path.suffix != ".pth":
            continue
        if name in CUSTOMIZE_MODULE_NAMES or path.resolve().as_posix() not in allowed:
            flagged.append(path.relative_to(root).as_posix())
    return flagged


def verify_release(
    repo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Hash-verify the deployed tree against the release manifest.

    Returns a report dict; report["ok"] is True only when the manifest is
    present and internally consistent, every listed file matches its SHA-256,
    and no unlisted first-party module exists in the runtime dirs.
    """
    root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    manifest_file = Path(
        manifest_path
        or os.getenv(MANIFEST_PATH_ENV)
        or root / DEFAULT_MANIFEST_NAME
    )
    if not manifest_file.is_absolute():
        manifest_file = root / manifest_file

    report: dict[str, Any] = {
        "ok": False,
        "manifest_path": str(manifest_file),
        "manifest_present": manifest_file.is_file(),
        "release_commit": None,
        "release_branch": None,
        "fingerprint": None,
        "fingerprint_ok": None,
        "files_checked": 0,
        "mismatched": [],
        "missing": [],
        "unreadable": [],
        "extra_runtime_files": [],
        "problems": [],
        "status": "FAIL",
        "fingerprint_pinned": False,
    }

    if not report["manifest_present"]:
        report["problems"].append(f"release manifest not found at {manifest_file}")
        return report

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        report["problems"].append(f"release manifest unreadable: {exc}")
        return report

    repo_info = manifest.get("repo") or {}
    report["release_commit"] = repo_info.get("commit")
    report["release_branch"] = repo_info.get("branch")

    recorded_fingerprint = manifest.get("fingerprint_sha256")
    recomputed = manifest_fingerprint(manifest)
    report["fingerprint"] = recorded_fingerprint
    report["fingerprint_ok"] = recorded_fingerprint == recomputed
    if not report["fingerprint_ok"]:
        report["problems"].append(
            "manifest fingerprint mismatch (manifest edited after build)"
        )

    pinned = (os.getenv(FINGERPRINT_PIN_ENV) or "").strip()
    report["fingerprint_pinned"] = bool(pinned)
    if pinned and pinned != recorded_fingerprint:
        report["problems"].append(
            f"{FINGERPRINT_PIN_ENV} does not match manifest fingerprint"
        )

    source_files = manifest.get("source_files") or {}
    for rel_name, expected_sha in sorted(source_files.items()):
        path = root / rel_name
        if not path.is_file():
            report["missing"].append(rel_name)
            continue
        observed = _sha256(path)
        if observed is None:
            report["unreadable"].append(rel_name)
        elif observed != expected_sha:
            report["mismatched"].append(rel_name)
    report["files_checked"] = len(source_files)

    report["extra_runtime_files"] = _runtime_extras(root, set(source_files))

    for label, entries in (
        ("hash mismatch", report["mismatched"]),
        ("missing from deploy", report["missing"]),
        ("unreadable", report["unreadable"]),
        ("not in release manifest", report["extra_runtime_files"]),
    ):
        if entries:
            shown = ", ".join(entries[:8])
            more = f" (+{len(entries) - 8} more)" if len(entries) > 8 else ""
            report["problems"].append(f"{label}: {shown}{more}")

    if report["problems"]:
        report["ok"] = False
        report["status"] = "FAIL"
    elif not report["fingerprint_pinned"]:
        # A self-consistent manifest is not a release pin. Editing a file,
        # updating its manifest hash, and recomputing the fingerprint still
        # produces a consistent tree. Only the out-of-band pin distinguishes
        # that from the reviewed release, so an unpinned tree is UNPINNED.
        report["ok"] = False
        report["status"] = "UNPINNED"
    else:
        report["ok"] = True
        report["status"] = "OK"
    return report


def _release_integrity_enforced() -> bool:
    """Enforcement is on unless the operator explicitly turns it off.

    Unset or blank is on. Only 0, false, no, and off disable the gate.
    """
    raw = os.getenv(ENFORCE_ENV)
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def enforce_release_integrity(
    repo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Startup gate. On by default.

    Set RELEASE_INTEGRITY_ENFORCED=false to skip. When enforced, any integrity
    problem, including an unpinned fingerprint, raises SystemExit so the
    service never comes up on drifted source (systemd will mark the unit failed).
    """
    if not _release_integrity_enforced():
        return None
    report = verify_release(repo_root=repo_root, manifest_path=manifest_path)
    if report["ok"]:
        print(
            "release integrity OK: "
            f"{report['files_checked']} files match release "
            f"{(report['release_commit'] or 'unknown')[:12]}",
            file=sys.stderr,
        )
        return report
    detail = "; ".join(report["problems"]) or str(report.get("status") or "not ok")
    if report.get("status") == "UNPINNED":
        detail = (
            "UNPINNED: tree matches the manifest but "
            "EXPECTED_RELEASE_FINGERPRINT is not set; this is not OK"
        )
    raise SystemExit(
        f"RELEASE INTEGRITY FAILURE — refusing to start: {detail}. "
        f"Manifest: {report['manifest_path']}. Redeploy the release or "
        f"rebuild the manifest via ops/release_manifest.py."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--manifest", default=None)
    parser.add_argument("--json", action="store_true", help="emit full JSON report")
    args = parser.parse_args()

    report = verify_release(repo_root=args.repo_root, manifest_path=args.manifest)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        status = str(report.get("status") or ("OK" if report["ok"] else "FAIL"))
        print(
            f"release integrity: {status} — {report['files_checked']} files checked, "
            f"release {(report['release_commit'] or 'unknown')[:12]}"
        )
        if status == "UNPINNED":
            print(
                "  ✗ UNPINNED: manifest matches the tree, but "
                "EXPECTED_RELEASE_FINGERPRINT is not set. This is not OK."
            )
        for problem in report["problems"]:
            print(f"  ✗ {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
