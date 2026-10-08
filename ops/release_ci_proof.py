"""Exact-SHA CI proof for releases (U11).

``scripts/atomic_release.sh build`` refuses to build unless a CI proof for the
exact release SHA verifies:

* the proof lists GitHub check runs for exactly that commit SHA;
* every required check (``REQUIRED_CHECKS``) was produced by the
  ``github-actions`` app, and its LATEST run for that name is
  ``completed`` / ``success`` on that SHA.

``fetch`` reads the public GitHub REST API without any credential (the
repository is public); ``verify`` works on a saved proof file, so the release
host never needs a token. Neither stores or prints a secret.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

SCHEMA_VERSION = "1.1.0"
GENERATOR = "ops.release_ci_proof"
REPO = "imanisworld/autonomous-futures-system"
REQUIRED_CHECKS: tuple[str, ...] = ("tests", "Analyze (python)", "Analyze (actions)")
REQUIRED_APP = "github-actions"
_SHA = re.compile(r"^[0-9a-f]{40}$")


class CIProofError(RuntimeError):
    """The CI proof cannot be obtained or does not prove the release SHA."""


def fetch_proof(sha: str, *, repo: str = REPO, opener=urllib.request.urlopen) -> dict[str, Any]:
    if not _SHA.match(sha or ""):
        raise CIProofError("release CI proof needs an exact 40-character lowercase SHA")
    runs: list[dict[str, Any]] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=100&page={page}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "autonomous-futures-system-release-proof",
            },
        )
        try:
            with opener(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - any fetch failure fails closed
            raise CIProofError(f"could not fetch check runs for {sha}: {exc}") from exc
        batch = payload.get("check_runs") if isinstance(payload, dict) else None
        if not isinstance(batch, list):
            raise CIProofError("GitHub check-runs response has no check_runs list")
        runs.extend(batch)
        if len(runs) >= int(payload.get("total_count") or 0) or not batch:
            break
        page += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "repo": repo,
        "sha": sha,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "check_runs": [
            {
                "id": run.get("id"),
                "name": run.get("name"),
                "head_sha": run.get("head_sha"),
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "app": (run.get("app") or {}).get("slug"),
                "started_at": run.get("started_at"),
                "completed_at": run.get("completed_at"),
            }
            for run in runs
        ],
    }


def _latest_required_run(proof: dict[str, Any], name: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    runs = proof.get("check_runs") if isinstance(proof.get("check_runs"), list) else []
    candidates = [
        run for run in runs
        if isinstance(run, dict) and run.get("name") == name and run.get("app") == REQUIRED_APP
    ]
    if not candidates:
        return None, f"required check {name!r} from {REQUIRED_APP} is absent"
    valid: list[dict[str, Any]] = []
    for run in candidates:
        run_id = run.get("id")
        if isinstance(run_id, bool) or not isinstance(run_id, int) or run_id <= 0:
            return None, f"required check {name!r} has no valid numeric run id"
        valid.append(run)
    # GitHub check-run IDs are monotonic. Use the newest run ID, not
    # completed_at: an in-progress rerun has no completed_at and must block.
    return max(valid, key=lambda run: int(run["id"])), None


def _required_snapshot(proof: dict[str, Any], required: tuple[str, ...]) -> dict[str, tuple[Any, ...]]:
    snapshot: dict[str, tuple[Any, ...]] = {}
    for name in required:
        run, error = _latest_required_run(proof, name)
        if error or run is None:
            continue
        snapshot[name] = (
            run.get("id"),
            run.get("head_sha"),
            run.get("status"),
            run.get("conclusion"),
            run.get("app"),
        )
    return snapshot


def verify_proof(proof: Any, *, sha: str, required: tuple[str, ...] = REQUIRED_CHECKS) -> list[str]:
    if not _SHA.match(sha or ""):
        return ["release CI proof needs an exact 40-character lowercase SHA"]
    if (
        not isinstance(proof, dict)
        or proof.get("schema_version") != SCHEMA_VERSION
        or proof.get("generator") != GENERATOR
    ):
        return ["release CI proof missing or not produced by ops.release_ci_proof"]
    problems: list[str] = []
    if proof.get("sha") != sha:
        problems.append(f"release CI proof is for {proof.get('sha')!r}, not {sha!r}")
    if proof.get("repo") != REPO:
        problems.append(f"release CI proof is for repo {proof.get('repo')!r}")
    for name in required:
        latest, error = _latest_required_run(proof, name)
        if error or latest is None:
            problems.append(error or f"required check {name!r} is absent")
            continue
        if latest.get("head_sha") != sha:
            problems.append(f"required check {name!r} ran on {latest.get('head_sha')!r}, not {sha!r}")
        if latest.get("status") != "completed" or latest.get("conclusion") != "success":
            problems.append(
                f"required check {name!r} latest run is "
                f"{latest.get('status')}/{latest.get('conclusion')}"
            )
    return problems


def verify_live_proof(
    proof: Any,
    *,
    sha: str,
    required: tuple[str, ...] = REQUIRED_CHECKS,
    opener=urllib.request.urlopen,
) -> list[str]:
    """Verify the saved artifact and re-query GitHub for the exact SHA.

    The saved JSON is an audit artifact, not an authority. A fabricated or
    stale file cannot authorize a build because current GitHub check runs are
    fetched again and the latest required run IDs must match the artifact.
    """
    problems = verify_proof(proof, sha=sha, required=required)
    if problems:
        return problems
    try:
        live = fetch_proof(sha, opener=opener)
    except CIProofError as exc:
        return [f"live GitHub CI verification failed: {exc}"]
    live_problems = verify_proof(live, sha=sha, required=required)
    if live_problems:
        return [f"live GitHub CI: {problem}" for problem in live_problems]
    if _required_snapshot(proof, required) != _required_snapshot(live, required):
        return [
            "release CI proof is stale relative to current GitHub required-check runs; refetch it"
        ]
    return []


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Exact-SHA CI proof for releases.")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--sha", required=True)
    fetch.add_argument("--out", type=Path, required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--sha", required=True)
    verify.add_argument("--proof", type=Path, required=True)
    verify_live = sub.add_parser("verify-live")
    verify_live.add_argument("--sha", required=True)
    verify_live.add_argument("--proof", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "fetch":
        try:
            proof = fetch_proof(args.sha)
        except CIProofError as exc:
            print(f"RELEASE CI PROOF BLOCKED: {exc}", file=sys.stderr)
            return 2
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        problems = verify_proof(proof, sha=args.sha)
    else:
        try:
            proof = json.loads(args.proof.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            proof = None
        if args.command == "verify-live":
            problems = verify_live_proof(proof, sha=args.sha)
        else:
            problems = verify_proof(proof, sha=args.sha)

    for problem in problems:
        print(f"RELEASE CI PROOF BLOCKED: {problem}", file=sys.stderr)
    if not problems:
        suffix = " with live GitHub recheck" if args.command == "verify-live" else ""
        print(f"release CI proof verified for {args.sha}{suffix}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
