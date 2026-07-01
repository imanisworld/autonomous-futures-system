"""Startup release-integrity gate: verify deployed source against the release manifest.

Companion to ops/release_manifest.py. The manifest is built once, from a clean
checkout of canonical main, and shipped with the release. At service startup
this module re-hashes every file the manifest lists and scans the runtime
package dirs for first-party modules the manifest does NOT list. Any mismatch,
missing file, or unexpected extra module refuses startup when
RELEASE_INTEGRITY_ENFORCED is set.

Deliberately git-free at runtime: the live box's git worktree is not a release
identifier (it is permanently dirty by deploy history), so verification relies
only on the manifest's SHA-256 entries. See
docs/incident-2026-07-01-direction-and-phantom-fills.md, follow-up #8.
"""

from __future__ import annotations

import argparse
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
IGNORED_DIR_NAMES = {"__pycache__"}


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _env_truthy(name: str) -> bool:
    value = os.getenv(name)
    return bool(value and value.strip().lower() in {"1", "true", "yes"})


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
    for dirname in RUNTIME_DIRS:
        base = root / dirname
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in RUNTIME_SUFFIXES:
                continue
            if IGNORED_DIR_NAMES.intersection(path.relative_to(root).parts):
                continue
            rel = path.relative_to(root).as_posix()
            if rel not in listed:
                extras.append(rel)
    return sorted(extras)


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

    pinned = os.getenv(FINGERPRINT_PIN_ENV)
    if pinned and pinned.strip() and pinned.strip() != recorded_fingerprint:
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

    report["ok"] = not report["problems"]
    return report


def enforce_release_integrity(
    repo_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Startup gate. No-op unless RELEASE_INTEGRITY_ENFORCED is truthy.

    When enforced, any integrity problem raises SystemExit so the service
    never comes up on drifted source (systemd will mark the unit failed).
    """
    if not _env_truthy(ENFORCE_ENV):
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
    detail = "; ".join(report["problems"])
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
        status = "OK" if report["ok"] else "FAIL"
        print(
            f"release integrity: {status} — {report['files_checked']} files checked, "
            f"release {(report['release_commit'] or 'unknown')[:12]}"
        )
        for problem in report["problems"]:
            print(f"  ✗ {problem}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
