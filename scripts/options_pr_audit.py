#!/usr/bin/env python3
"""scripts/options_pr_audit.py

Read-only, OPTIONS-lane pull-request audit report.

Mechanically reproduces the boilerplate checks every options_manager PR has
gone through by hand: current branch, base/head SHA, changed files, diff
stat, sensitive-path hits, forbidden-identifier hits (order verbs, banned
broker modules, live-flag flips), a broker/execution/config touch summary,
and -- for any changed test files -- an actual pytest run with its result
summary. Produces a *candidate* verdict only; it never authorizes a merge,
push, or deploy, and it never performs one.

Hard limits (by design, not by convention):
  - Never runs `git push`, `git merge`, or any git write beyond local reads.
  - Never edits a repository file unless --out is passed, and even then
    only writes the report text to that one path.
  - Never imports or calls anything under options_manager.adapters,
    an MCP client, a Robinhood/Polygon network client, execution/, or
    risk_engine.py. This script only shells out to `git` (read-only
    subcommands) and, if a changed test file is present, to `pytest`.
  - Never reads or prints an environment variable, credential, or secret.

CI status is best-effort only: if --pr is given and the `gh` CLI is on
PATH, this attempts one read-only `gh pr checks` call with a short timeout;
any failure (missing binary, network error, timeout) degrades to
"unavailable" rather than raising.

Usage:
  python3 scripts/options_pr_audit.py --base origin/main
  python3 scripts/options_pr_audit.py --base origin/main --pr 216 --out report.md
  python3 scripts/options_pr_audit.py --base origin/main --skip-tests
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Files that define the banned vocabulary below are not themselves a use of
# it -- excluded from forbidden-identifier / sensitive-path line scanning so
# this tool does not flag its own source (and its own test file) every time
# it audits the PR that introduces or changes it.
_SELF_PATHS = (
    "scripts/options_pr_audit.py",
    "tests/test_options_pr_audit.py",
)

_OPTIONS_LANE_PREFIXES = (
    "options_manager/",
    "tests/test_options_",
)

_SENSITIVE_PATH_HINTS = (
    "options_manager/config.py",
    "options_manager/live_lock.py",
    "options_manager/broker_boundary.py",
    "options_manager/order_ticket.py",
    "options_manager/human_confirm.py",
    ".github/workflows/",
    "alert_ranker/",
    "execution/",
    "risk_engine.py",
    "webhook",
    "options_companion",
    "requirements.txt",
    "pyproject.toml",
    "setup.py",
    "atomic_release.sh",
    "deploy_lock.sh",
)

_CREDENTIAL_PATH_HINTS = ("secret", "credential", ".env", ".pem", ".key")

_FORBIDDEN_DIFF_IDENTIFIERS = (
    "place_order",
    "submit_order",
    "cancel_order",
    "replace_order",
    "execute_order",
    "live_order",
    "review_option_order",
    "robin_stocks",
    "ib_insync",
    "ibapi",
    "mcp__robinhood",
    "mcp__polygon",
)

_LIVE_FLAG_FLIP_HINTS = (
    "LIVE_OPTIONS_TRADING_ENABLED = True",
    "LIVE_OPTIONS_TRADING_ENABLED=True",
)

GitRunner = Callable[[list[str]], str]


class GitAuditError(RuntimeError):
    """A required git read-only operation failed."""


class GitRepo:
    """Thin, read-only wrapper over `git`. Every method is a read; nothing
    here ever writes, pushes, merges, or mutates repository state. The
    runner is injectable so tests can supply canned output instead of
    shelling out to a real repository."""

    def __init__(self, cwd: Optional[Path] = None, runner: Optional[GitRunner] = None) -> None:
        self.cwd = cwd
        self._runner = runner or self._default_runner

    def _default_runner(self, args: list[str]) -> str:
        result = subprocess.run(
            ["git", *args], cwd=self.cwd, capture_output=True, text=True, timeout=60
        )
        if result.returncode != 0:
            raise GitAuditError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout

    def current_branch(self) -> str:
        return self._runner(["rev-parse", "--abbrev-ref", "HEAD"]).strip()

    def head_sha(self) -> str:
        return self._runner(["rev-parse", "HEAD"]).strip()

    def merge_base(self, base_ref: str) -> str:
        return self._runner(["merge-base", base_ref, "HEAD"]).strip()

    def changed_files(self, base_sha: str, head_sha: str) -> list[str]:
        raw = self._runner(["diff", "--name-only", f"{base_sha}..{head_sha}"])
        return [line for line in raw.splitlines() if line.strip()]

    def diff_stat(self, base_sha: str, head_sha: str) -> str:
        return self._runner(["diff", "--stat", f"{base_sha}..{head_sha}"]).strip()

    def added_lines(self, base_sha: str, head_sha: str, path: str) -> list[str]:
        raw = self._runner(["diff", f"{base_sha}..{head_sha}", "--", path])
        return [
            line[1:]
            for line in raw.splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]


@dataclass(kw_only=True)
class PrAuditReport:
    lane: str
    current_branch: str
    base_sha: str
    head_sha: str
    changed_files: list[str]
    diff_stat: str
    sensitive_path_hits: list[tuple[str, str]]
    forbidden_identifier_hits: list[tuple[str, str, str]]
    touches_execution: bool
    touches_config: bool
    touches_ci: bool
    touches_deploy: bool
    test_commands_run: list[str]
    test_result_summary: str
    ci_status: str
    runtime_impact: str
    deploy_needed: str
    verdict_candidate: str
    stop_condition: str = (
        "This report is a candidate signal only. It does not authorize merge, "
        "push, or deploy. A human must independently review the full diff and "
        "issue the final Verdict before any merge."
    )


def classify_lane(changed_files: list[str]) -> str:
    if not changed_files:
        return "EMPTY_DIFF"
    outside = [
        f
        for f in changed_files
        if f not in _SELF_PATHS and not any(f.startswith(p) for p in _OPTIONS_LANE_PREFIXES)
    ]
    if outside:
        return "MIXED (outside options lane: " + ", ".join(sorted(outside)) + ")"
    return "OPTIONS"


def find_sensitive_path_hits(changed_files: list[str]) -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for f in changed_files:
        for hint in _SENSITIVE_PATH_HINTS + _CREDENTIAL_PATH_HINTS:
            if hint in f:
                hits.append((f, hint))
    return hits


def find_forbidden_identifier_hits(
    repo: GitRepo, base_sha: str, head_sha: str, changed_files: list[str]
) -> list[tuple[str, str, str]]:
    hits: list[tuple[str, str, str]] = []
    forbidden = _FORBIDDEN_DIFF_IDENTIFIERS + _LIVE_FLAG_FLIP_HINTS
    for f in changed_files:
        if f in _SELF_PATHS:
            continue
        for line in repo.added_lines(base_sha, head_sha, f):
            for identifier in forbidden:
                if identifier in line:
                    hits.append((f, identifier, line.strip()))
    return hits


def broker_execution_config_touch_status(changed_files: list[str]) -> dict[str, bool]:
    return {
        "touches_execution": any(
            f.startswith("execution/") or "broker" in f for f in changed_files
        ),
        "touches_config": any(f.endswith("config.py") for f in changed_files),
        "touches_ci": any(f.startswith(".github/workflows/") for f in changed_files),
        "touches_deploy": any(
            f in ("atomic_release.sh", "deploy_lock.sh") or "deploy" in f for f in changed_files
        ),
    }


def run_pytest(test_files: list[str], cwd: Optional[Path] = None) -> tuple[str, str]:
    """Runs pytest against exactly `test_files`. Returns (command, summary).
    Never invoked unless at least one changed file is a test file, and
    never runs anything beyond the test files actually in the diff."""
    cmd = [sys.executable, "-m", "pytest", *test_files, "-q"]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
    summary_lines = [
        line
        for line in (result.stdout.splitlines() + result.stderr.splitlines())
        if line.strip() and ("passed" in line or "failed" in line or "error" in line)
    ]
    summary = summary_lines[-1] if summary_lines else result.stdout.strip()[-300:]
    return " ".join(cmd), summary


def check_ci_status(pr_number: Optional[int]) -> str:
    if pr_number is None:
        return "not requested (no --pr given)"
    try:
        result = subprocess.run(
            ["gh", "pr", "checks", str(pr_number), "--json", "name,state,conclusion"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable (gh CLI not found or timed out)"
    if result.returncode != 0:
        return "unavailable (gh CLI call failed)"
    return result.stdout.strip() or "unavailable (empty response)"


def assess_runtime_impact(changed_files: list[str]) -> str:
    live_surface = (
        "options_manager/scanner/",
        "options_manager/strategies/",
        "options_manager/app.py",
        "options_manager/http_api.py",
    )
    if any(f.startswith(p) or f == p for f in changed_files for p in live_surface):
        return "possible -- scanner/app/http surface touched; reviewer must confirm no live-path change"
    if changed_files and all(
        f.startswith("options_manager/adapters/")
        or f.startswith("options_manager/validation/")
        or f.startswith("tests/")
        or f.startswith("docs/")
        or f.startswith("scripts/options_pr_audit")
        for f in changed_files
    ):
        return "none expected -- adapter/validation/test/doc-only change"
    return "reviewer must assess -- unclassified file type touched"


def assess_deploy_needed(touch_status: dict[str, bool]) -> str:
    if touch_status["touches_ci"] or touch_status["touches_deploy"] or touch_status["touches_config"]:
        return "reviewer must confirm -- deploy-adjacent file touched"
    return "no"


def build_verdict_candidate(
    lane: str,
    sensitive_path_hits: list[tuple[str, str]],
    forbidden_identifier_hits: list[tuple[str, str, str]],
    tests_failed: bool,
    tests_ran: bool,
) -> str:
    credential_hits = [h for h in sensitive_path_hits if h[1] in _CREDENTIAL_PATH_HINTS]
    if forbidden_identifier_hits or credential_hits:
        return "BLOCK CANDIDATE -- forbidden identifier or credential-shaped path hit; do not merge without explicit human review"
    if tests_failed:
        return "HOLD CANDIDATE -- tests failed"
    if lane != "OPTIONS":
        return "HOLD CANDIDATE -- diff touches files outside the options lane"
    if sensitive_path_hits:
        return "REVIEW CANDIDATE -- sensitive path touched; human review required"
    tests_clause = "tests passed" if tests_ran else "no tests changed in this diff"
    return f"CLEAN CANDIDATE -- no forbidden identifiers, no sensitive-path hits, lane confined to options, {tests_clause}"


def build_report(
    repo: GitRepo,
    base_ref: str,
    pr_number: Optional[int] = None,
    skip_tests: bool = False,
) -> PrAuditReport:
    current_branch = repo.current_branch()
    head_sha = repo.head_sha()
    base_sha = repo.merge_base(base_ref)
    changed = repo.changed_files(base_sha, head_sha)

    lane = classify_lane(changed)
    sensitive_hits = find_sensitive_path_hits(changed)
    forbidden_hits = find_forbidden_identifier_hits(repo, base_sha, head_sha, changed)
    touch_status = broker_execution_config_touch_status(changed)

    test_files = [f for f in changed if f.startswith("tests/") and f.endswith(".py")]
    test_commands: list[str] = []
    tests_failed = False
    if skip_tests:
        test_summary = "skipped (--skip-tests passed)"
    elif not test_files:
        test_summary = "no test files changed in this diff -- no tests run"
    else:
        cmd_str, test_summary = run_pytest(test_files, cwd=repo.cwd)
        test_commands.append(cmd_str)
        tests_failed = "failed" in test_summary or "error" in test_summary.lower()

    ci_status = check_ci_status(pr_number)
    runtime_impact = assess_runtime_impact(changed)
    deploy_needed = assess_deploy_needed(touch_status)
    verdict_candidate = build_verdict_candidate(
        lane, sensitive_hits, forbidden_hits, tests_failed, tests_ran=bool(test_commands)
    )

    return PrAuditReport(
        lane=lane,
        current_branch=current_branch,
        base_sha=base_sha,
        head_sha=head_sha,
        changed_files=changed,
        diff_stat=repo.diff_stat(base_sha, head_sha),
        sensitive_path_hits=sensitive_hits,
        forbidden_identifier_hits=forbidden_hits,
        touches_execution=touch_status["touches_execution"],
        touches_config=touch_status["touches_config"],
        touches_ci=touch_status["touches_ci"],
        touches_deploy=touch_status["touches_deploy"],
        test_commands_run=test_commands,
        test_result_summary=test_summary,
        ci_status=ci_status,
        runtime_impact=runtime_impact,
        deploy_needed=deploy_needed,
        verdict_candidate=verdict_candidate,
    )


def render_report(report: PrAuditReport) -> str:
    out: list[str] = []
    out.append("# options-pr-audit report")
    out.append("")
    out.append(f"Lane: {report.lane}")
    out.append(f"Current branch: {report.current_branch}")
    out.append(f"Base SHA: {report.base_sha}")
    out.append(f"Head SHA: {report.head_sha}")
    out.append("")
    out.append(f"Changed files ({len(report.changed_files)}):")
    for f in report.changed_files:
        out.append(f"  - {f}")
    out.append("")
    out.append("Diff stat:")
    out.append("```")
    out.append(report.diff_stat)
    out.append("```")
    out.append("")
    out.append(f"Sensitive path hits ({len(report.sensitive_path_hits)}):")
    for f, hint in report.sensitive_path_hits:
        out.append(f"  - {f} (matched: {hint})")
    if not report.sensitive_path_hits:
        out.append("  - none")
    out.append("")
    out.append(f"Forbidden identifier hits ({len(report.forbidden_identifier_hits)}):")
    for f, identifier, line in report.forbidden_identifier_hits:
        out.append(f"  - {f}: {identifier!r} in added line: {line}")
    if not report.forbidden_identifier_hits:
        out.append("  - none")
    out.append("")
    out.append("Broker/execution/config touch status:")
    out.append(f"  - touches_execution: {report.touches_execution}")
    out.append(f"  - touches_config: {report.touches_config}")
    out.append(f"  - touches_ci: {report.touches_ci}")
    out.append(f"  - touches_deploy: {report.touches_deploy}")
    out.append("")
    out.append("Test commands run:")
    if report.test_commands_run:
        for cmd in report.test_commands_run:
            out.append(f"  - {cmd}")
    else:
        out.append("  - none")
    out.append(f"Test result summary: {report.test_result_summary}")
    out.append("")
    out.append(f"CI status: {report.ci_status}")
    out.append("")
    out.append(f"Runtime impact: {report.runtime_impact}")
    out.append(f"Deploy needed: {report.deploy_needed}")
    out.append("")
    out.append(f"Verdict candidate: {report.verdict_candidate}")
    out.append("")
    out.append(f"Stop condition: {report.stop_condition}")
    out.append("")
    return "\n".join(out)


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="origin/main", help="base ref to diff against (default origin/main)")
    ap.add_argument("--pr", type=int, default=None, help="PR number, used only for a best-effort `gh pr checks` read")
    ap.add_argument("--skip-tests", action="store_true", help="do not run pytest even if test files changed")
    ap.add_argument("--out", default=None, help="write the report here instead of stdout (the only write this tool ever performs)")
    return ap.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    args = parse_args(argv)
    repo = GitRepo()
    report = build_report(repo, args.base, pr_number=args.pr, skip_tests=args.skip_tests)
    text = render_report(report)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    main()
