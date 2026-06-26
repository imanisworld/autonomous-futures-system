"""Read-only live-box drift guard."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_EXPECTED_EVIDENCE_SOURCE = "active_box_journal_and_status"
DEFAULT_EXPECTED_STATUS_PATHS = ("/status/today", "/status/broker-account")


@dataclass(frozen=True)
class Comparison:
    name: str
    observed: str | None
    expected: str | None
    ok: bool
    required: bool = True
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "observed": self.observed,
            "expected": self.expected,
            "ok": self.ok,
            "required": self.required,
            "detail": self.detail,
        }


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _git(repo_root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2.0,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _short(value: str | None, chars: int = 12) -> str:
    return value[:chars] if value else "unknown"


def _cmp(name: str, observed: str | None, expected: str | None, *, required: bool = True) -> Comparison:
    if expected is None:
        return Comparison(
            name=name,
            observed=observed,
            expected=None,
            ok=not required,
            required=required,
            detail="expected value is not pinned",
        )
    return Comparison(
        name=name,
        observed=observed,
        expected=expected,
        ok=observed == expected,
        required=required,
        detail="matches" if observed == expected else "mismatch",
    )


def _path_cmp(name: str, observed: str | Path | None, expected: str | None, *, required: bool = True) -> Comparison:
    observed_str = str(Path(observed).resolve()) if observed else None
    expected_str = str(Path(expected).resolve()) if expected else None
    return _cmp(name, observed_str, expected_str, required=required)


def live_box_drift_report(
    *,
    repo_root: str | Path | None = None,
    risk_rules_path: str | Path = "risk_rules.yaml",
    log_dir: str | Path = "logs",
    for_date: date | None = None,
) -> dict[str, Any]:
    root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    risk_path = Path(risk_rules_path)
    if not risk_path.is_absolute():
        risk_path = root / risk_path
    risk_path = risk_path.resolve()
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path
    log_path = log_path.resolve()
    journal_path = log_path / f"journal_{(for_date or date.today()).isoformat()}.jsonl"

    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _git(root, "rev-parse", "HEAD")
    config_sha = _sha256(risk_path)
    dirty = _git(root, "status", "--porcelain")
    dirty_state = "dirty" if dirty else "clean"

    expected_branch = _env("EXPECTED_LIVE_BRANCH")
    expected_commit = _env("EXPECTED_LIVE_COMMIT")
    expected_config_sha = _env("EXPECTED_RISK_RULES_SHA256")
    expected_repo_root = _env("EXPECTED_LIVE_REPO_ROOT")
    expected_evidence_source = _env("EXPECTED_RUNTIME_EVIDENCE_SOURCE") or DEFAULT_EXPECTED_EVIDENCE_SOURCE
    expected_journal_dir = _env("EXPECTED_RUNTIME_JOURNAL_DIR")
    expected_status_paths = tuple(
        part.strip()
        for part in (_env("EXPECTED_RUNTIME_STATUS_PATHS") or ",".join(DEFAULT_EXPECTED_STATUS_PATHS)).split(",")
        if part.strip()
    )

    comparisons = [
        _cmp("branch", branch, expected_branch),
        _cmp("commit", commit, expected_commit),
        _cmp("risk_rules_sha256", config_sha, expected_config_sha),
        _path_cmp("repo_root", root, expected_repo_root),
        _path_cmp("journal_dir", log_path, expected_journal_dir),
        _cmp("runtime_evidence_source", _env("RUNTIME_EVIDENCE_SOURCE") or DEFAULT_EXPECTED_EVIDENCE_SOURCE, expected_evidence_source),
        _cmp("status_paths", ",".join(expected_status_paths), ",".join(DEFAULT_EXPECTED_STATUS_PATHS), required=False),
    ]

    failed = [item for item in comparisons if item.required and not item.ok]
    missing_pins = [item.name for item in comparisons if item.required and item.expected is None]
    mismatches = [item.name for item in failed if item.expected is not None]
    dirty_problem = dirty_state != "clean"
    ok = not failed and not dirty_problem
    status = "ok" if ok else "warn" if missing_pins and not mismatches else "error"

    if status == "ok":
        summary = (
            f"Live box guard verified branch {branch}, commit {_short(commit)}, "
            f"risk_rules {_short(config_sha)}, and evidence journal {journal_path}."
        )
    elif missing_pins and not mismatches:
        summary = f"Live box guard cannot fully verify drift; missing expected pin(s): {', '.join(missing_pins)}."
    else:
        bits = []
        if mismatches:
            bits.append(f"mismatch: {', '.join(mismatches)}")
        if missing_pins:
            bits.append(f"missing pin: {', '.join(missing_pins)}")
        if dirty_problem:
            bits.append("git worktree is dirty")
        summary = "Live box drift guard failed: " + "; ".join(bits) + "."

    return {
        "ok": ok,
        "status": status,
        "summary": summary,
        "repo_root": str(root),
        "branch": branch,
        "commit": commit,
        "risk_rules_path": str(risk_path),
        "risk_rules_sha256": config_sha,
        "git_dirty": dirty_problem,
        "git_dirty_count": len(dirty.splitlines()) if dirty else 0,
        "runtime_evidence_source": _env("RUNTIME_EVIDENCE_SOURCE") or DEFAULT_EXPECTED_EVIDENCE_SOURCE,
        "journal_dir": str(log_path),
        "journal_path": str(journal_path),
        "status_paths": list(expected_status_paths),
        "comparisons": [item.as_dict() for item in comparisons],
        "missing_pins": missing_pins,
        "mismatches": mismatches,
        "next_step": (
            "Set EXPECTED_LIVE_BRANCH, EXPECTED_LIVE_COMMIT, and "
            "EXPECTED_RISK_RULES_SHA256 on the active box after config freeze; "
            "run doctor/status before live preflight."
        ),
    }
