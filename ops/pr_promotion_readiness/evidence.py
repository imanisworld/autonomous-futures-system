"""Read-only evidence collection through the existing ``gh`` CLI.

Every ``gh`` invocation must match an allowlisted read-only shape
(``_is_read_only_gh_command``) -- the same defense-in-depth idea as
``ops.project_check.gitutil``. Nothing here can merge, push, comment,
label, close, or dispatch a workflow. All calls use argument lists, never a
shell string. Any failure is recorded in ``collection_errors`` and left
unknown; nothing is guessed.
"""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .models import CheckResult, PromotionEvidence, ReviewThread, TestEvidence

GH_TIMEOUT_S = 60.0
Runner = Callable[[list[str]], tuple[int, str, str]]

_PR_VIEW_FIELDS = (
    "number,url,title,author,state,isDraft,labels,headRefName,headRefOid,"
    "baseRefName,mergeable,mergeStateStatus,reviewDecision,files,statusCheckRollup"
)
_REVIEW_THREADS_QUERY = (
    "query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){"
    "pullRequest(number:$n){reviewThreads(first:100){nodes{isResolved path "
    "comments(first:1){nodes{author{login} body}}}}}}}"
)
_JOB_ID_RE = re.compile(r"/job/(\d+)")
_SUMMARY_TOKEN_RE = re.compile(r"(\d+)\s+(passed|failed|skipped|errors?|xfailed|xpassed|warnings?|deselected)\b")


def _is_read_only_gh_command(args: list[str]) -> bool:
    if not args:
        return False
    if args[:2] == ["repo", "view"]:
        return "--json" in args
    if args[:2] == ["pr", "view"]:
        return len(args) >= 5 and args[3] == "--json"
    if args[:2] == ["run", "view"]:
        return len(args) == 5 and args[2] == "--job" and args[4] == "--log"
    if args[:1] == ["api"]:
        if any(a in ("-X", "--method", "-f", "--field", "-F", "--raw-field", "--input") for a in args[2:]) and args[1] != "graphql":
            return False
        if args[1] == "graphql":
            query = next((args[i + 1] for i, a in enumerate(args) if a == "-f" and i + 1 < len(args)), "")
            return query.startswith("query=query") and "mutation" not in query.lower()
        path = args[1]
        return path.startswith("repos/") and (
            "/branches/" in path or "/compare/" in path or "/actions/jobs/" in path
        )
    return False


def _default_runner(args: list[str]) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["gh", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=GH_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def run_gh(args: list[str], *, runner: Optional[Runner] = None) -> tuple[Optional[str], Optional[str]]:
    """Run one allowlisted read-only gh command -> (stdout, error)."""
    if not _is_read_only_gh_command(args):
        raise ValueError(f"gh command shape not in read-only allowlist: {args!r}")
    code, out, err = (runner or _default_runner)(args)
    if code != 0:
        return None, (err or "").strip() or f"gh {' '.join(args[:2])} exited {code}"
    return out, None


def _gh_json(args: list[str], *, runner: Optional[Runner]) -> tuple[Any, Optional[str]]:
    out, err = run_gh(args, runner=runner)
    if err is not None:
        return None, err
    try:
        return json.loads(out or ""), None
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON from gh {' '.join(args[:2])}: {exc}"


def parse_pytest_summary(text: str) -> Optional[dict[str, int]]:
    """Counts from the last pytest summary line, or None when there is none.

    A line like ``4507 passed, 6 skipped, 1 warning in 54.37s`` -> {passed:
    4507, failed: 0, skipped: 6, errors: 0, ...}. ``no tests ran`` -> None.
    """
    summary: Optional[dict[str, int]] = None
    for line in text.splitlines():
        if " in " not in line or not _SUMMARY_TOKEN_RE.search(line):
            continue
        if "no tests ran" in line:
            continue
        counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0, "xfailed": 0, "xpassed": 0, "warnings": 0, "deselected": 0}
        matched = False
        for value, key in _SUMMARY_TOKEN_RE.findall(line):
            matched = True
            if key.startswith("error"):
                key = "errors"
            elif key.startswith("warning"):
                key = "warnings"
            counts[key] = int(value)
        if matched:
            summary = counts
    return summary


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_pr_evidence(
    pr_number: int,
    *,
    runner: Optional[Runner] = None,
    operator_tests: tuple[TestEvidence, ...] = (),
    now: Optional[str] = None,
) -> PromotionEvidence:
    """Collect everything the verdict needs. Never raises on gh failures."""
    errors: list[str] = []
    collected_at = now or _now_iso()

    repo_json, err = _gh_json(["repo", "view", "--json", "nameWithOwner"], runner=runner)
    repo = str((repo_json or {}).get("nameWithOwner", "")) if isinstance(repo_json, dict) else ""
    if err:
        errors.append(f"repo: {err}")

    pr, err = _gh_json(["pr", "view", str(pr_number), "--json", _PR_VIEW_FIELDS], runner=runner)
    if err or not isinstance(pr, dict):
        errors.append(f"pr view: {err or 'no data'}")
        return PromotionEvidence(pr_number=pr_number, collected_at=collected_at, repo=repo, collection_errors=tuple(errors))

    head_sha = pr.get("headRefOid") or None
    base_ref = str(pr.get("baseRefName") or "")

    base_sha: Optional[str] = None
    if repo and base_ref:
        branch_json, err = _gh_json(["api", f"repos/{repo}/branches/{base_ref}"], runner=runner)
        if err:
            errors.append(f"base branch tip: {err}")
        else:
            base_sha = ((branch_json or {}).get("commit") or {}).get("sha") or None

    merge_base_sha: Optional[str] = None
    behind: Optional[int] = None
    ahead: Optional[int] = None
    if repo and base_ref and head_sha:
        cmp_json, err = _gh_json(["api", f"repos/{repo}/compare/{base_ref}...{head_sha}"], runner=runner)
        if err:
            errors.append(f"compare with base: {err}")
        else:
            merge_base_sha = ((cmp_json or {}).get("merge_base_commit") or {}).get("sha") or None
            behind = (cmp_json or {}).get("behind_by")
            ahead = (cmp_json or {}).get("ahead_by")

    threads: list[ReviewThread] = []
    if repo:
        owner, _, name = repo.partition("/")
        threads_json, err = _gh_json(
            ["api", "graphql", "-f", f"query={_REVIEW_THREADS_QUERY}", "-F", f"o={owner}", "-F", f"r={name}", "-F", f"n={pr_number}"],
            runner=runner,
        )
        if err:
            errors.append(f"review threads: {err}")
        else:
            nodes = (((((threads_json or {}).get("data") or {}).get("repository") or {}).get("pullRequest") or {}).get("reviewThreads") or {}).get("nodes") or []
            for node in nodes:
                first = ((node.get("comments") or {}).get("nodes") or [{}])[0]
                threads.append(
                    ReviewThread(
                        path=str(node.get("path") or ""),
                        author=str((first.get("author") or {}).get("login") or ""),
                        excerpt=str(first.get("body") or "").strip().splitlines()[0] if first.get("body") else "",
                        resolved=bool(node.get("isResolved")),
                    )
                )

    checks: list[CheckResult] = []
    for raw in pr.get("statusCheckRollup") or []:
        checks.append(
            CheckResult(
                name=str(raw.get("name") or raw.get("context") or ""),
                conclusion=str(raw.get("conclusion") or raw.get("state") or "").upper(),
                workflow=str(raw.get("workflowName") or ""),
                details_url=str(raw.get("detailsUrl") or raw.get("targetUrl") or ""),
            )
        )

    tests: list[TestEvidence] = list(operator_tests)
    for check in checks:
        if check.name != "tests" or check.conclusion != "SUCCESS":
            continue
        match = _JOB_ID_RE.search(check.details_url)
        if not match or not repo:
            errors.append("tests check has no job id; CI test counts unavailable")
            continue
        job_id = match.group(1)
        job_json, err = _gh_json(["api", f"repos/{repo}/actions/jobs/{job_id}"], runner=runner)
        if err:
            errors.append(f"tests job metadata: {err}")
            continue
        job_sha = (job_json or {}).get("head_sha") or None
        log, err = run_gh(["run", "view", "--job", job_id, "--log"], runner=runner)
        if err:
            errors.append(f"tests job log: {err}")
            continue
        counts = parse_pytest_summary(log or "")
        if counts is None:
            errors.append(f"tests job {job_id} log has no pytest summary line")
            continue
        tests.append(
            TestEvidence(
                kind="full",
                source=f"ci tests job {job_id}",
                sha=job_sha,
                passed=counts["passed"],
                failed=counts["failed"],
                skipped=counts["skipped"],
                errors=counts["errors"],
                command="pytest -q (CI)",
            )
        )

    return PromotionEvidence(
        pr_number=pr_number,
        collected_at=collected_at,
        repo=repo,
        url=str(pr.get("url") or ""),
        title=str(pr.get("title") or ""),
        author=str((pr.get("author") or {}).get("login") or ""),
        state=str(pr.get("state") or ""),
        is_draft=pr.get("isDraft") if isinstance(pr.get("isDraft"), bool) else None,
        labels=tuple(str(l.get("name") or "") for l in pr.get("labels") or []),
        branch=str(pr.get("headRefName") or ""),
        head_sha=head_sha,
        base_ref=base_ref,
        base_sha=base_sha,
        merge_base_sha=merge_base_sha,
        behind_base_by=behind if isinstance(behind, int) else None,
        ahead_of_base_by=ahead if isinstance(ahead, int) else None,
        mergeable=str(pr.get("mergeable") or ""),
        merge_state=str(pr.get("mergeStateStatus") or ""),
        review_decision=str(pr.get("reviewDecision") or ""),
        review_threads=tuple(threads),
        changed_files=tuple(str(f.get("path") or "") for f in pr.get("files") or []),
        checks=tuple(checks),
        tests=tuple(tests),
        collection_errors=tuple(errors),
    )


def load_operator_test_evidence(path: str) -> tuple[TestEvidence, ...]:
    """Operator-supplied test results (e.g. a targeted run) as JSON:
    ``[{"kind": "targeted", "source": "...", "sha": "...", "passed": N,
    "failed": N, "skipped": N, "errors": N, "command": "..."}]``.
    Missing counts stay None and produce a HOLD downstream."""
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    items = raw if isinstance(raw, list) else [raw]
    out: list[TestEvidence] = []
    for item in items:
        out.append(
            TestEvidence(
                kind=str(item.get("kind") or "targeted"),
                source=str(item.get("source") or path),
                sha=item.get("sha") or None,
                passed=item.get("passed") if isinstance(item.get("passed"), int) else None,
                failed=item.get("failed") if isinstance(item.get("failed"), int) else None,
                skipped=item.get("skipped") if isinstance(item.get("skipped"), int) else None,
                errors=item.get("errors") if isinstance(item.get("errors"), int) else None,
                command=str(item.get("command") or ""),
            )
        )
    return tuple(out)
