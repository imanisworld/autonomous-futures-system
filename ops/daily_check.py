"""Daily Reconciliation + Trade Chain Integrity.

One daily read-only source-of-truth pass, composed entirely from existing
machinery plus the new shared helpers in ``ops.git_state`` and
``ops.trade_chain``:

  - GITHUB / REPO RECONCILIATION -- best-effort via the ``gh`` CLI if present
    on PATH; UNKNOWN (never fabricated) if it is not.
  - BRANCHES / WORKTREES        -- ops.git_state
  - EVIDENCE PRESERVATION       -- unique-commit/unique-file check vs main
    plus the archive/* tag convention (docs/BRANCH_ARCHIVE_INDEX.md). Never
    creates, deletes, or presumes a tag; only flags what a human should
    review.
  - DEPLOYED STATE              -- ops.live_box_guard.live_box_drift_report
  - STRATEGY SOURCE OF TRUTH    -- docs/strategy-rules/Strategy_Inventory.md
    cross-referenced against risk_rules.yaml and ops.evidence_lane_health.
    Heuristic name matching only; flags are review prompts, not proof.
  - TRADE CHAIN INTEGRITY       -- ops.trade_chain.trade_chain_report

Never cancels an order, flattens a position, modifies a broker order,
repairs a journal, retries an execution, submits an order, deletes a
branch/worktree/stash, or creates/deletes a tag. Report / fail closed only.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ops.evidence_lane_health import build_snapshot as evidence_lane_snapshot
from ops.git_state import (
    archive_tags,
    current_branch,
    git_state_report,
    porcelain_status,
    unique_commits,
    unique_files,
    unmerged_branches,
)
from ops.live_box_guard import live_box_drift_report
from ops.promotion_gate import parse_master_table
from ops.trade_chain import format_trade_chain_summary, trade_chain_report

STALE_PR_DAYS = 14
DEFAULT_INVENTORY_PATH = Path("docs/strategy-rules/Strategy_Inventory.md")


def _gh_json(repo_root: Path, args: list[str], timeout: float = 15.0) -> tuple[Any, str | None]:
    if shutil.which("gh") is None:
        return None, "gh CLI not found on PATH"
    try:
        result = subprocess.run(
            ["gh", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if result.returncode != 0:
        detail = (result.stderr or "").strip() or f"gh {' '.join(args)} exited {result.returncode}"
        return None, detail
    try:
        return json.loads(result.stdout), None
    except ValueError as exc:
        return None, f"gh returned non-JSON output: {exc}"


def _pr_ref(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "head": pr.get("headRefName"),
        "created_at": pr.get("createdAt"),
        "updated_at": pr.get("updatedAt"),
        "merged_at": pr.get("mergedAt"),
        "closed_at": pr.get("closedAt"),
        "draft": pr.get("isDraft"),
    }


def github_reconciliation(repo_root: Path, *, today: date) -> dict[str, Any]:
    prs, error = _gh_json(
        repo_root,
        [
            "pr", "list", "--state", "all", "--limit", "200",
            "--json", "number,title,state,createdAt,updatedAt,mergedAt,closedAt,isDraft,headRefName",
        ],
    )
    if error:
        return {"status": "UNKNOWN", "reason": error}

    today_str = today.isoformat()
    opened_today = [p for p in prs if str(p.get("createdAt") or "")[:10] == today_str]
    merged_today = [p for p in prs if p.get("mergedAt") and str(p["mergedAt"])[:10] == today_str]
    closed_unmerged_today = [
        p for p in prs
        if p.get("state") == "CLOSED" and not p.get("mergedAt")
        and str(p.get("closedAt") or "")[:10] == today_str
    ]
    open_prs = [p for p in prs if p.get("state") == "OPEN"]
    stale_cutoff = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) - timedelta(days=STALE_PR_DAYS)
    stale_prs = []
    for pr in open_prs:
        updated_raw = pr.get("updatedAt")
        try:
            updated = datetime.fromisoformat(str(updated_raw).replace("Z", "+00:00")) if updated_raw else None
        except ValueError:
            updated = None
        if updated and updated < stale_cutoff:
            stale_prs.append(pr)

    return {
        "status": "OK",
        "opened_today": [_pr_ref(p) for p in opened_today],
        "merged_today": [_pr_ref(p) for p in merged_today],
        "closed_unmerged_today": [_pr_ref(p) for p in closed_unmerged_today],
        "open_prs": [_pr_ref(p) for p in open_prs],
        "stale_prs": [_pr_ref(p) for p in stale_prs],
        "stale_threshold_days": STALE_PR_DAYS,
    }


def evidence_preservation(repo_root: Path, *, base_branch: str = "main") -> dict[str, Any]:
    """Flag closed-unmerged local branches with unique evidence and no archive tag."""
    active_branch = current_branch(repo_root)
    candidates = [b for b in unmerged_branches(repo_root, base_branch) if b not in (base_branch, active_branch)]
    tags = archive_tags(repo_root)

    findings = []
    for branch in candidates:
        commits = unique_commits(repo_root, branch, base_branch)
        files = unique_files(repo_root, branch, base_branch)
        has_unique_evidence = bool(commits) or bool(files)
        slug = branch.replace("/", "-")
        short_name = branch.split("/")[-1]
        matching_tags = sorted(tag for tag in tags if slug in tag or short_name in tag)
        if not has_unique_evidence:
            status = "OK_NO_UNIQUE_EVIDENCE"
        elif matching_tags:
            status = "PRESERVED"
        else:
            status = "BLOCKER"
        findings.append(
            {
                "branch": branch,
                "unique_commit_count": len(commits),
                "unique_file_count": len(files),
                "has_unique_evidence": has_unique_evidence,
                "matching_archive_tags": matching_tags,
                "status": status,
            }
        )

    return {
        "base_branch": base_branch,
        "candidates_checked": len(findings),
        "findings": findings,
        "blockers": [f for f in findings if f["status"] == "BLOCKER"],
        "note": (
            "Local branches only, disposition method mirrors docs/BRANCH_ARCHIVE_INDEX.md "
            "(unique commits vs base, unique files vs base). Closed-unmerged branches that "
            "exist only on the remote require cross-checking the GITHUB section manually. "
            "This routine never creates or deletes archive tags or branches."
        ),
    }


def deployed_state(repo_root: Path, *, log_dir: str | Path = "logs") -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
    lanes = evidence_lane_snapshot(log_dir)
    return {
        "status": drift["status"],
        "summary": drift["summary"],
        "identity_source": drift["identity_source"],
        "branch": drift["branch"],
        "commit": drift["commit"],
        "risk_rules_sha256": drift["risk_rules_sha256"],
        "missing_pins": drift["missing_pins"],
        "mismatches": drift["mismatches"],
        "active_runtime_overrides": drift["active_runtime_overrides"],
        "unpinned_runtime_overrides": drift["unpinned_runtime_overrides"],
        "lane_modes": {lane["lane"]: {"instrument": lane["instrument"], "mode": lane["mode"], "status": lane["status"]} for lane in lanes["lanes"]},
    }


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if t and t not in {"mes", "mnq", "the", "a"}}


def strategy_source_of_truth(repo_root: Path, *, log_dir: str | Path = "logs", inventory_path: str | Path = DEFAULT_INVENTORY_PATH) -> dict[str, Any]:
    full_inventory_path = repo_root / inventory_path if not Path(inventory_path).is_absolute() else Path(inventory_path)
    risk_rules_path = repo_root / "risk_rules.yaml"
    if not full_inventory_path.exists():
        return {"status": "UNKNOWN", "reason": f"{full_inventory_path} not found"}
    if not risk_rules_path.exists():
        return {"status": "UNKNOWN", "reason": f"{risk_rules_path} not found"}

    rows = parse_master_table(full_inventory_path.read_text(encoding="utf-8"))
    try:
        rules = yaml.safe_load(risk_rules_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        return {"status": "UNKNOWN", "reason": f"risk_rules.yaml could not be parsed: {exc}"}

    strategy_cfg = rules.get("strategy") or {}
    enabled_concepts = list(strategy_cfg.get("enabled_concepts") or [])
    enabled_tokens = {token for name in enabled_concepts for token in _tokens(str(name))}

    lanes = evidence_lane_snapshot(log_dir)["lanes"]
    lane_tokens = {lane["lane"]: _tokens(lane["lane"]) | _tokens(lane["instrument"]) for lane in lanes}

    flags: list[dict[str, Any]] = []
    for row in rows:
        name = row.get("Strategy", "")
        verdict = re.sub(r"\*\*", "", row.get("Verdict", "")).strip()
        row_tokens = _tokens(name)
        if not row_tokens:
            continue
        looks_enabled = bool(row_tokens & enabled_tokens)
        blocked_lanes = [
            lane["lane"] for lane in lanes
            if row_tokens & lane_tokens.get(lane["lane"], set()) and lane["status"] in ("BLOCKED", "STARVED")
        ]
        if verdict.upper() in ("BROKEN", "RETIRE") and looks_enabled:
            flags.append(
                {
                    "strategy": name,
                    "documented_verdict": verdict,
                    "flag": "DOCUMENTED_BROKEN_BUT_TOKEN_MATCH_IN_ENABLED_CONCEPTS",
                    "detail": "Heuristic name-token match only; verify manually before trusting this flag.",
                }
            )
        if verdict.upper() in ("VALIDATED", "PAPER PROOF") and blocked_lanes:
            flags.append(
                {
                    "strategy": name,
                    "documented_verdict": verdict,
                    "flag": "DOCUMENTED_PROMISING_BUT_LIVE_LANE_BLOCKED_OR_STARVED",
                    "matched_lanes": blocked_lanes,
                }
            )

    return {
        "status": "OK",
        "inventory_path": str(full_inventory_path),
        "risk_rules_path": str(risk_rules_path),
        "rows_checked": len(rows),
        "enabled_concepts": enabled_concepts,
        "flags": flags,
        "matching_method": (
            "Heuristic token overlap between Strategy_Inventory.md row names and "
            "risk_rules.yaml enabled_concepts / evidence-lane names. Not a proof; "
            "surfaces candidates for human review only."
        ),
    }


def build_daily_report(
    *,
    repo_root: str | Path,
    log_dir: str | Path = "logs",
    journal_dir: str | Path | None = None,
    base_branch: str = "main",
    today: date | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    today = today or date.today()
    journal_root = Path(journal_dir) if journal_dir else root / log_dir

    repo = git_state_report(root, base_branch=base_branch)
    github = github_reconciliation(root, today=today)
    preservation = evidence_preservation(root, base_branch=base_branch)
    deployed = deployed_state(root, log_dir=log_dir)
    strategy_truth = strategy_source_of_truth(root, log_dir=log_dir)
    chain = trade_chain_report(journal_root, to_date=today.isoformat())

    dirty_worktrees = []
    for wt in repo["worktrees"]:
        path = wt.get("path")
        if not path or wt.get("bare"):
            continue
        status_lines = porcelain_status(Path(path))
        if status_lines:
            dirty_worktrees.append({"path": path, "branch": wt.get("branch"), "dirty_file_count": len(status_lines)})

    return {
        "date": today.isoformat(),
        "read_only": True,
        "github": github,
        "branches_worktrees": {
            "current_branch": repo["current_branch"],
            "local_main_relationship": repo["local_main_relationship"],
            "worktrees": repo["worktrees"],
            "dirty_worktrees": dirty_worktrees,
            "branches_tracking_deleted_remotes": repo["branches_tracking_deleted_remotes"],
            "local_only_branches": repo["local_only_branches"],
            "stash_count": repo["stash_count"],
        },
        "evidence_preservation": preservation,
        "deployed_state": deployed,
        "strategy_source_of_truth": strategy_truth,
        "trade_chain": chain,
    }


def format_daily_report(report: dict[str, Any]) -> str:
    lines = [f"DAILY RECONCILIATION | {report['date']}", ""]

    github = report["github"]
    if github["status"] == "UNKNOWN":
        lines.append(f"GITHUB: UNKNOWN ({github['reason']})")
    else:
        lines.append(
            f"GITHUB: opened={len(github['opened_today'])} merged={len(github['merged_today'])} "
            f"closed_unmerged={len(github['closed_unmerged_today'])} open={len(github['open_prs'])} "
            f"stale={len(github['stale_prs'])}"
        )
    lines.append("")

    bw = report["branches_worktrees"]
    lines.append(
        f"BRANCHES/WORKTREES: branch={bw['current_branch']} vs_base={bw['local_main_relationship']} "
        f"worktrees={len(bw['worktrees'])} local_only={len(bw['local_only_branches'])} "
        f"tracking_deleted_remote={len(bw['branches_tracking_deleted_remotes'])} stash={bw['stash_count']}"
    )
    lines.append("")

    preservation = report["evidence_preservation"]
    lines.append(f"EVIDENCE PRESERVATION: checked={preservation['candidates_checked']} blockers={len(preservation['blockers'])}")
    for finding in preservation["blockers"]:
        lines.append(f"  BLOCKER: {finding['branch']} has unique evidence, no archive/* tag found")
    lines.append("")

    deployed = report["deployed_state"]
    lines.append(f"DEPLOYED STATE: {deployed['status']} - {deployed['summary']}")
    lines.append("")

    truth = report["strategy_source_of_truth"]
    if truth["status"] == "UNKNOWN":
        lines.append(f"STRATEGY SOURCE OF TRUTH: UNKNOWN ({truth['reason']})")
    else:
        lines.append(f"STRATEGY SOURCE OF TRUTH: rows_checked={truth['rows_checked']} flags={len(truth['flags'])}")
        for flag in truth["flags"]:
            lines.append(f"  {flag['flag']}: {flag['strategy']} ({flag['documented_verdict']})")
    lines.append("")

    lines.append(format_trade_chain_summary(report["trade_chain"]))
    return "\n".join(lines)
