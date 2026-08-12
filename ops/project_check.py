"""Read-only, manually-invoked repo/process routines.

Three routines, one entry point:

  python -m ops.project_check session-start   # repo + runtime snapshot
  python -m ops.project_check precommit       # fail-closed drift check
  python -m ops.project_check promotion --strategy <name>
  python -m ops.project_check daily           # reconciliation + trade-chain integrity

None of these routines commit, push, pull, fetch, reset, rebase, checkout,
delete branches/tags, drop stashes, cancel orders, flatten positions, or
otherwise mutate git, broker, or journal state. The only state they write is
their own bookkeeping under logs/ (session-start's session marker, daily's
last-checkpoint date) so precommit and daily can compare against a prior run.

This module composes existing machinery rather than re-implementing it:
ops.live_box_guard for drift, ops.evidence_readiness / ops.evidence_report /
docs/strategy-rules/Strategy_Inventory.md for promotion evidence, and
ops.proof_30_mnq / ops.journal_label_audit / ops.strategy_intent_audit /
ops.reconciler_outcome_audit for trade-chain integrity. Where a fact isn't
available from existing artifacts, the report says UNKNOWN rather than guess.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.proof_30_mnq import (  # noqa: E402
    DEFAULT_JOURNAL_DIR,
    classify_outcome,
    read_journal_entries,
)

UNKNOWN = "UNKNOWN"
SESSION_STATE_PATH = ROOT / "logs" / "project_check_session_state.json"
DAILY_CHECKPOINT_PATH = ROOT / "logs" / "project_check_daily_checkpoint.json"

# Command allowlists: every git subprocess call in this module comes from one
# of these two functions, and every argument list here is read-only. Nothing
# in this module calls fetch/pull/checkout/reset/rebase/tag(-d)/branch(-d)/
# stash(drop|pop)/worktree(remove|add).
_READ_ONLY_GIT_SUBCOMMANDS = {
    "rev-parse", "status", "branch", "worktree", "stash", "tag",
    "rev-list", "for-each-ref", "show", "log", "diff",
}


# ─────────────────────────── shell-out helpers ───────────────────────────

def _run(cmd: list[str], cwd: Path | None = None, timeout: float = 10.0) -> tuple[int, str, str]:
    """Run a subprocess; never raises. Returns (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        # rstrip only: git status --porcelain lines are column-position-sensitive
        # (leading space is the "index unchanged" marker) -- a leading .strip()
        # would silently misalign every column parser downstream.
        return result.returncode, result.stdout.rstrip("\n"), result.stderr.rstrip("\n")
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def _git(root: Path, *args: str) -> str | None:
    if not args or args[0] not in _READ_ONLY_GIT_SUBCOMMANDS:
        raise ValueError(f"refusing non-read-only git subcommand: {args[:1]!r}")
    rc, out, _err = _run(["git", *args], cwd=root)
    return out if rc == 0 else None


def _git_lines(root: Path, *args: str) -> list[str]:
    out = _git(root, *args)
    return [line for line in (out or "").splitlines() if line.strip()]


# ─────────────────────────── repo identity/state ───────────────────────────

def discover_repo_root(start: Path | None = None) -> Path:
    candidate = _git(start or Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(candidate).resolve() if candidate else (start or ROOT).resolve()


def _relationship(ahead: int | None, behind: int | None) -> str:
    if ahead is None or behind is None:
        return UNKNOWN
    if ahead == 0 and behind == 0:
        return "IN_SYNC"
    if ahead > 0 and behind == 0:
        return "AHEAD"
    if ahead == 0 and behind > 0:
        return "BEHIND"
    return "DIVERGED"


def main_vs_origin_main(root: Path) -> dict[str, Any]:
    local_main = _git(root, "rev-parse", "main")
    origin_main = _git(root, "rev-parse", "origin/main")
    ahead = behind = None
    if local_main and origin_main:
        counts = _git(root, "rev-list", "--left-right", "--count", "main...origin/main")
        if counts and "\t" in counts:
            try:
                ahead, behind = (int(part) for part in counts.split("\t"))
            except ValueError:
                pass
    return {
        "local_main_sha": local_main or UNKNOWN,
        "origin_main_sha": origin_main or UNKNOWN,
        "relationship": _relationship(ahead, behind),
        "note": "compared against the last locally-known origin/main ref; this "
        "tool never fetches, so the remote-tracking ref may be stale",
    }


def worktrees(root: Path) -> list[dict[str, Any]]:
    raw = _git(root, "worktree", "list", "--porcelain") or ""
    entries: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            if current:
                entries.append(current)
            current = {"path": line[len("worktree "):], "locked": False, "bare": False, "detached": False}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            current["branch"] = line[len("branch "):].removeprefix("refs/heads/")
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
    if current:
        entries.append(current)
    return entries


def current_worktree(root: Path, all_worktrees: list[dict[str, Any]]) -> str:
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top:
        return UNKNOWN
    top_resolved = str(Path(top).resolve())
    for wt in all_worktrees:
        if str(Path(wt.get("path", "")).resolve()) == top_resolved:
            return wt["path"]
    return top


def working_tree_status(root: Path) -> dict[str, list[str]]:
    lines = _git_lines(root, "status", "--porcelain=v1", "--untracked-files=all")
    staged, dirty, untracked = [], [], []
    for line in lines:
        if len(line) < 3:
            continue
        index_flag, worktree_flag, path = line[0], line[1], line[3:]
        if line.startswith("??"):
            untracked.append(path)
            continue
        if index_flag not in (" ", "?"):
            staged.append(path)
        if worktree_flag not in (" ", "?"):
            dirty.append(path)
    return {"staged": staged, "dirty": dirty, "untracked": untracked}


def stash_list(root: Path) -> list[str]:
    return _git_lines(root, "stash", "list")


def local_branches(root: Path) -> list[str]:
    return _git_lines(root, "branch", "--format=%(refname:short)")


def remote_branches(root: Path) -> list[str]:
    return _git_lines(root, "branch", "--remotes", "--format=%(refname:short)")


def branches_tracking_deleted_remotes(root: Path) -> list[str]:
    lines = _git_lines(root, "branch", "-vv")
    out = []
    for line in lines:
        if ": gone]" in line:
            out.append(line.strip().lstrip("* ").split()[0])
    return out


def local_only_branches(root: Path, local: list[str], remote: list[str]) -> list[str]:
    remote_names = {name.split("/", 1)[1] for name in remote if "/" in name}
    return [name for name in local if name not in remote_names]


def archive_tags(root: Path) -> list[str]:
    return _git_lines(root, "tag", "-l", "archive/*")


def tags_pointing_at(root: Path, sha: str) -> list[str]:
    return _git_lines(root, "tag", "--points-at", sha)


def unique_commits_vs_main(root: Path, branch: str) -> int | None:
    out = _git(root, "rev-list", "--count", f"origin/main..{branch}")
    try:
        return int(out) if out is not None else None
    except ValueError:
        return None


def evidence_preservation_report(root: Path) -> list[dict[str, Any]]:
    """Local-only/unmerged branches with unique history and no archive tag.

    Fully computable from git alone (no gh dependency): flags any local
    branch, other than the current one and main, that carries commits not
    reachable from origin/main and has no archive/* tag pinned to its tip.
    Never deletes or tags anything -- report only.
    """
    root_branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or ""
    local = local_branches(root)
    tags = archive_tags(root)
    out = []
    for branch in local:
        if branch in ("main", root_branch):
            continue
        unique = unique_commits_vs_main(root, branch)
        tip = _git(root, "rev-parse", branch)
        pointing = tags_pointing_at(root, tip) if tip else []
        has_archive = any(tag in tags for tag in pointing)
        out.append({
            "branch": branch,
            "unique_commits_vs_origin_main": unique if unique is not None else UNKNOWN,
            "archive_tag": pointing[0] if pointing and has_archive else None,
            "blocker": bool(unique) and unique > 0 and not has_archive,
        })
    return out


def try_gh(args: list[str], timeout: float = 8.0) -> tuple[bool, Any]:
    """Best-effort read-only `gh` call. Returns (ok, parsed_json_or_None)."""
    rc, out, _err = _run(["gh", *args], timeout=timeout)
    if rc != 0 or not out:
        return False, None
    try:
        return True, json.loads(out)
    except json.JSONDecodeError:
        return True, out


def open_prs(root: Path) -> Any:
    ok, data = try_gh(["pr", "list", "--json", "number,title,headRefName,isDraft,updatedAt"])
    return data if ok else UNKNOWN


def collect_repo_state() -> dict[str, Any]:
    root = discover_repo_root()
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD") or UNKNOWN
    head = _git(root, "rev-parse", "HEAD") or UNKNOWN
    upstream = _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or UNKNOWN
    all_worktrees = worktrees(root)
    status = working_tree_status(root)
    local = local_branches(root)
    remote = remote_branches(root)
    return {
        "repo_root": str(root),
        "current_branch": branch,
        "head_sha": head,
        "upstream": upstream,
        "current_worktree": current_worktree(root, all_worktrees),
        "worktrees": all_worktrees,
        "main_vs_origin_main": main_vs_origin_main(root),
        "dirty_files": status["dirty"],
        "staged_files": status["staged"],
        "untracked_files": status["untracked"],
        "branches_tracking_deleted_remotes": branches_tracking_deleted_remotes(root),
        "local_only_branches": local_only_branches(root, local, remote),
        "archive_tags": archive_tags(root),
        "stash_count": len(stash_list(root)),
        "stash_labels": stash_list(root),
        "open_prs": open_prs(root),
        "evidence_preservation": evidence_preservation_report(root),
    }


# ─────────────────────────── runtime snapshot ───────────────────────────

def _deployed_release_sha(root: Path) -> dict[str, Any]:
    manifest_path = root / "release_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            repo_info = manifest.get("repo") or {}
            return {
                "source": "release_manifest.json",
                "commit": repo_info.get("commit") or UNKNOWN,
                "branch": repo_info.get("branch") or UNKNOWN,
                "generated_at": manifest.get("generated_at") or UNKNOWN,
            }
        except (OSError, ValueError):
            pass
    return {
        "source": "git_head (no release_manifest.json found -- not a deployed release checkout)",
        "commit": _git(root, "rev-parse", "HEAD") or UNKNOWN,
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD") or UNKNOWN,
        "generated_at": UNKNOWN,
    }


def _evidence_epochs() -> dict[str, Any]:
    epochs = {}
    try:
        from ops.build_honest_baseline import NO_FILL_TAXONOMY_DEPLOY_TS
        epochs["no_fill_taxonomy_deploy_ts"] = NO_FILL_TAXONOMY_DEPLOY_TS
    except ImportError:
        epochs["no_fill_taxonomy_deploy_ts"] = UNKNOWN
    return {
        "epochs": epochs,
        "note": "no repo-wide 'evidence epoch' key exists; these are the ad hoc "
        "taxonomy-deploy timestamps ops/build_honest_baseline.py and "
        "ops/audit_plain_cancelled.py already pin to",
    }


def _active_lanes(root: Path) -> dict[str, Any]:
    try:
        import yaml
        risk_rules = yaml.safe_load((root / "risk_rules.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return {"error": UNKNOWN}

    gate = (risk_rules or {}).get("strategy_permission_gate") or {}
    default_status = gate.get("default_status", UNKNOWN)
    statuses = gate.get("strategy_status") or {}
    active = {name: status for name, status in statuses.items() if status == "PAPER_ELIGIBLE"}

    config_summary = UNKNOWN
    try:
        from config.settings import load_config
        cfg = load_config()
        config_summary = {
            "entry_fill_model": cfg.entry_fill_model,
            "entry_tolerance_ticks_by_root": cfg.entry_tolerance_ticks_by_root,
            "max_contracts_hard_cap": cfg.max_contracts_hard_cap,
        }
    except Exception as exc:  # pragma: no cover - defensive, config load is best-effort
        config_summary = {"error": f"{type(exc).__name__}: {exc}"}

    return {
        "default_status": default_status,
        "paper_eligible_strategies": active,
        "all_strategy_status": statuses,
        "max_contracts_per_instrument": (risk_rules or {}).get("position_rules", {}).get(
            "max_contracts_per_instrument", UNKNOWN
        ),
        "effective_execution_context": config_summary,
    }


def collect_runtime_snapshot() -> dict[str, Any]:
    root = discover_repo_root()
    drift: dict[str, Any]
    try:
        from ops.live_box_guard import live_box_drift_report
        drift = live_box_drift_report(repo_root=root)
    except Exception as exc:  # pragma: no cover - defensive
        drift = {"error": f"{type(exc).__name__}: {exc}"}
    return {
        "deployed_release": _deployed_release_sha(root),
        "evidence_epochs": _evidence_epochs(),
        "active_lanes": _active_lanes(root),
        "drift_vs_pinned_expectations": drift,
    }


# ─────────────────────────── session-start ───────────────────────────

def session_start_report() -> dict[str, Any]:
    repo = collect_repo_state()
    runtime = collect_runtime_snapshot()
    report = {
        "routine": "session-start",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": repo,
        "runtime_snapshot": runtime,
    }
    save_session_state({
        "captured_at": report["generated_at"],
        "repo_root": repo["repo_root"],
        "branch": repo["current_branch"],
        "worktree": repo["current_worktree"],
        "head_sha": repo["head_sha"],
    })
    return report


def format_session_start(report: dict[str, Any]) -> str:
    repo = report["repo"]
    runtime = report["runtime_snapshot"]
    lines = [
        "SESSION START -- repo + runtime snapshot",
        f"repo root:        {repo['repo_root']}",
        f"branch:           {repo['current_branch']}",
        f"HEAD:             {repo['head_sha']}",
        f"upstream:         {repo['upstream']}",
        f"current worktree: {repo['current_worktree']}",
        f"worktrees ({len(repo['worktrees'])}):",
    ]
    for wt in repo["worktrees"]:
        lines.append(f"  - {wt.get('path')} [{wt.get('branch') or ('detached' if wt.get('detached') else '?')}]")
    mvo = repo["main_vs_origin_main"]
    lines += [
        f"local main vs origin/main: {mvo['relationship']} "
        f"(local={mvo['local_main_sha'][:12] if mvo['local_main_sha'] != UNKNOWN else UNKNOWN}, "
        f"origin={mvo['origin_main_sha'][:12] if mvo['origin_main_sha'] != UNKNOWN else UNKNOWN})",
        f"dirty tracked files:   {len(repo['dirty_files'])}",
        f"staged files:          {len(repo['staged_files'])}",
        f"untracked files:       {len(repo['untracked_files'])}",
        f"branches tracking deleted remotes: {repo['branches_tracking_deleted_remotes'] or 'none'}",
        f"local-only branches:   {repo['local_only_branches'] or 'none'}",
        f"archive tags:          {len(repo['archive_tags'])}",
        f"stash count:           {repo['stash_count']}",
        f"open PRs:              {repo['open_prs'] if repo['open_prs'] != UNKNOWN else 'UNKNOWN (gh not available)'}",
    ]
    blockers = [item for item in repo["evidence_preservation"] if item["blocker"]]
    lines.append(f"evidence-preservation BLOCKERs: {len(blockers)}")
    for item in blockers:
        lines.append(f"  - {item['branch']}: {item['unique_commits_vs_origin_main']} unique commits, no archive tag")
    lines += [
        "",
        "RUNTIME SNAPSHOT",
        f"deployed release: {runtime['deployed_release']}",
        f"evidence epochs:  {runtime['evidence_epochs']['epochs']} ({runtime['evidence_epochs']['note']})",
        f"active (paper-eligible) lanes: {runtime['active_lanes'].get('paper_eligible_strategies')}",
        f"execution context: {runtime['active_lanes'].get('effective_execution_context')}",
        f"drift vs pinned expectations: status={runtime['drift_vs_pinned_expectations'].get('status', UNKNOWN)}",
    ]
    return "\n".join(lines)


# ─────────────────────────── precommit / prepush ───────────────────────────

def save_session_state(state: dict[str, Any]) -> None:
    SESSION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_session_state() -> dict[str, Any] | None:
    if not SESSION_STATE_PATH.exists():
        return None
    try:
        return json.loads(SESSION_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def precommit_report() -> dict[str, Any]:
    session_state = load_session_state()
    repo = collect_repo_state()
    root = Path(repo["repo_root"])

    reasons: list[str] = []
    if session_state is None:
        reasons.append("session-start state cannot be verified (no prior `session-start` run found)")
    else:
        if session_state.get("branch") and session_state["branch"] != repo["current_branch"]:
            reasons.append(
                f"branch differs from session-start branch: "
                f"was {session_state['branch']!r}, now {repo['current_branch']!r}"
            )
        if session_state.get("worktree") and session_state["worktree"] != repo["current_worktree"]:
            reasons.append(
                f"worktree differs from session-start worktree: "
                f"was {session_state['worktree']!r}, now {repo['current_worktree']!r}"
            )

    owning_worktree = None
    for wt in repo["worktrees"]:
        if wt.get("branch") == repo["current_branch"] and wt.get("path") != repo["current_worktree"]:
            owning_worktree = wt.get("path")
    if owning_worktree:
        reasons.append(f"branch {repo['current_branch']!r} is checked out in another worktree: {owning_worktree}")

    ok = not reasons
    return {
        "routine": "precommit",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "ok": ok,
        "fail_closed_reasons": reasons,
        "repo_root": repo["repo_root"],
        "current_branch": repo["current_branch"],
        "current_head": repo["head_sha"],
        "session_start_branch": (session_state or {}).get("branch", UNKNOWN),
        "session_start_worktree": (session_state or {}).get("worktree", UNKNOWN),
        "current_worktree": repo["current_worktree"],
        "session_start_worktree_recorded_at": (session_state or {}).get("captured_at", UNKNOWN),
        "upstream": repo["upstream"],
        "changed_files": sorted(set(repo["dirty_files"]) | set(repo["staged_files"])),
        "staged_files": repo["staged_files"],
        "untracked_files": repo["untracked_files"],
    }


def format_precommit(report: dict[str, Any]) -> str:
    lines = [
        f"PRECOMMIT -- {'PASS' if report['ok'] else 'FAIL CLOSED'}",
        f"repo root:      {report['repo_root']}",
        f"branch:         {report['current_branch']} (session-start: {report['session_start_branch']})",
        f"worktree:       {report['current_worktree']} (session-start: {report['session_start_worktree']})",
        f"HEAD:           {report['current_head']}",
        f"upstream:       {report['upstream']}",
        f"staged files:   {len(report['staged_files'])}",
        f"untracked:      {len(report['untracked_files'])}",
    ]
    if not report["ok"]:
        lines.append("reasons:")
        for reason in report["fail_closed_reasons"]:
            lines.append(f"  - {reason}")
    return "\n".join(lines)


# ─────────────────────────── strategy promotion proof gate ───────────────────────────

def _strategy_inventory_row(strategy: str) -> dict[str, Any] | None:
    path = ROOT / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    needle = strategy.replace("_", " ").lower()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not cells or cells[0] in ("Strategy", "---"):
            continue
        if needle in cells[0].lower() or strategy.lower() in cells[0].lower().replace(" ", "_"):
            return {
                "row_label": cells[0],
                "verdict": cells[-1] if len(cells) > 1 else UNKNOWN,
                "raw_cells": cells,
            }
    return None


def _risk_rules_permission(strategy: str) -> dict[str, Any]:
    try:
        import yaml
        risk_rules = yaml.safe_load((ROOT / "risk_rules.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return {"status": UNKNOWN}
    gate = (risk_rules or {}).get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    return {
        "status": statuses.get(strategy, gate.get("default_status", UNKNOWN)),
        "explicit_entry": strategy in statuses,
    }


def _evidence_report_rows(strategy: str) -> dict[str, Any]:
    try:
        from ops.evidence_report import build_evidence_report
    except ImportError as exc:  # pragma: no cover - defensive
        return {"error": str(exc)}
    report = build_evidence_report(DEFAULT_JOURNAL_DIR)
    real = [row for row in report.get("real", []) if row.get("strategy") == strategy]
    shadow = [row for row in report.get("shadow", []) if row.get("strategy") == strategy]
    return {"real": real, "shadow": shadow}


def _accounting_identity(strategy: str) -> dict[str, Any]:
    entries = read_journal_entries(DEFAULT_JOURNAL_DIR)
    attempts = [
        entry for entry in entries
        if entry.get("decision") == "TRADE"
        and ((entry.get("setup") or {}).get("strategy") == strategy)
    ]
    # ops.proof_30_mnq.pair_resolved_trades pairs by a single instrument; do the
    # strategy-scoped pairing directly here instead, since a promotion check
    # spans both MES and MNQ.
    pending: dict[str, dict[str, Any]] = {}
    resolved: list[dict[str, Any]] = []
    for entry in entries:
        instrument = entry.get("instrument")
        if entry.get("decision") == "TRADE" and (entry.get("setup") or {}).get("strategy") == strategy:
            pending[instrument] = entry
        elif entry.get("type") == "OUTCOME" and isinstance(entry.get("outcome"), dict):
            trade = pending.pop(instrument, None)
            if trade is not None:
                resolved.append({"trade": trade, "outcome": entry, "category": classify_outcome(entry["outcome"])})

    by_category: dict[str, int] = {}
    for item in resolved:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1

    fills = by_category.get("filled_win_loss", 0) + by_category.get("breakeven", 0)
    cancellations = by_category.get("cancelled_nofill", 0)
    needs_verification = by_category.get("reconciler_touched", 0)
    unresolved = len(attempts) - len(resolved)

    return {
        "attempts": len(attempts),
        "resolved": len(resolved),
        "unresolved_or_open": unresolved,
        "by_category": by_category,
        "fills": fills,
        "cancellations_no_fill": cancellations,
        "reconciler_touched_needs_verification": needs_verification,
        "accounting_identity_holds": (fills + cancellations + needs_verification + unresolved) == len(attempts),
    }


def promotion_report(strategy: str) -> dict[str, Any]:
    inventory_row = _strategy_inventory_row(strategy)
    permission = _risk_rules_permission(strategy)
    evidence_rows = _evidence_report_rows(strategy)
    accounting = _accounting_identity(strategy)

    execution_context = UNKNOWN
    try:
        from config.settings import load_config
        cfg = load_config()
        execution_context = {
            "entry_fill_model": cfg.entry_fill_model,
            "entry_tolerance_ticks_by_root": cfg.entry_tolerance_ticks_by_root,
            "max_contracts_hard_cap": cfg.max_contracts_hard_cap,
        }
    except Exception as exc:  # pragma: no cover - defensive
        execution_context = {"error": f"{type(exc).__name__}: {exc}"}

    zero_fills = accounting["fills"] == 0

    classification_inputs = {
        "research_result": {
            "strategy_inventory_verdict": (inventory_row or {}).get("verdict", "NOT FOUND IN Strategy_Inventory.md"),
        },
        "runtime_parity": {
            "note": "NOT computed by this tool -- identity/direction/entry/stop/target "
            "parity, causal-data availability, and lookahead checks require the "
            "futures-live-replay-parity-audit skill; run it and attach the result "
            "before treating this as a parity pass.",
        },
        "paper_forward_evidence": {
            "real_trade_rows": evidence_rows.get("real", UNKNOWN),
            "shadow_rows": evidence_rows.get("shadow", UNKNOWN),
            "accounting": accounting,
            "zero_executable_fills": zero_fills,
        },
    }

    if zero_fills:
        draft_classification = "UNSAFE" if accounting["attempts"] > 0 else "WAIT"
        draft_reason = (
            "zero executable fills in journal evidence "
            f"({accounting['attempts']} attempt(s), {accounting['cancellations_no_fill']} no-fill)"
        )
    elif permission.get("status") not in ("PAPER_ELIGIBLE",):
        draft_classification = "WAIT"
        draft_reason = f"risk_rules.yaml strategy_permission_gate status is {permission.get('status')!r}, not PAPER_ELIGIBLE"
    else:
        draft_classification = "PROMISING BUT UNPROVEN"
        draft_reason = (
            "fills exist and permission gate is PAPER_ELIGIBLE, but this tool does not "
            "independently verify gate attrition, parity, or performance robustness -- "
            "treat as a starting point, not a verdict"
        )

    return {
        "routine": "promotion",
        "strategy": strategy,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "identity_parity": {
            "note": "IDENTITY/PARITY and GATE ATTRITION sections require replaying the "
            "candidate set through ReplayEngine -> DecisionEngine -> RiskEngine -> "
            "PaperBroker; this tool does not re-run that pipeline. Use the "
            "futures-strategy-audit and futures-live-replay-parity-audit skills for "
            "those sections and attach their output alongside this report.",
        },
        "gate_attrition": {"note": "NOT computed -- see identity_parity note above."},
        "execution": accounting,
        "performance": evidence_rows,
        "execution_context": execution_context,
        "strategy_inventory_row": inventory_row,
        "risk_rules_permission_gate": permission,
        "classification_inputs": classification_inputs,
        "draft_classification": draft_classification,
        "draft_classification_reason": draft_reason,
        "draft_classification_caveat": (
            "DRAFT ONLY -- not authoritative. No rescue/tuning variant, no automatic "
            "runtime change, no automatic merge, no deployment, no config edit follows "
            "from this report. A human must confirm IDENTITY/PARITY and GATE ATTRITION "
            "before treating any status here as final."
        ),
    }


def format_promotion(report: dict[str, Any]) -> str:
    acc = report["execution"]
    lines = [
        f"STRATEGY PROMOTION PROOF GATE -- {report['strategy']}",
        f"Strategy_Inventory.md row: {report['strategy_inventory_row']}",
        f"risk_rules.yaml permission gate: {report['risk_rules_permission_gate']}",
        "",
        "EXECUTION (journal-derived accounting identity)",
        f"  attempts={acc['attempts']} resolved={acc['resolved']} "
        f"unresolved_or_open={acc['unresolved_or_open']}",
        f"  fills={acc['fills']} cancellations_no_fill={acc['cancellations_no_fill']} "
        f"reconciler_touched_needs_verification={acc['reconciler_touched_needs_verification']}",
        f"  accounting_identity_holds={acc['accounting_identity_holds']}",
        "",
        f"execution context: {report['execution_context']}",
        "",
        f"IDENTITY/PARITY: {report['identity_parity']['note']}",
        f"GATE ATTRITION:  {report['gate_attrition']['note']}",
        "",
        f"DRAFT CLASSIFICATION: {report['draft_classification']}",
        f"  reason: {report['draft_classification_reason']}",
        f"  caveat: {report['draft_classification_caveat']}",
    ]
    return "\n".join(lines)


# ─────────────────────────── daily reconciliation + trade chain ───────────────────────────

def load_daily_checkpoint() -> str | None:
    if not DAILY_CHECKPOINT_PATH.exists():
        return None
    try:
        data = json.loads(DAILY_CHECKPOINT_PATH.read_text(encoding="utf-8"))
        return data.get("last_checked_date")
    except (OSError, ValueError):
        return None


def save_daily_checkpoint(checked_date: str) -> None:
    DAILY_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_CHECKPOINT_PATH.write_text(
        json.dumps({"last_checked_date": checked_date, "saved_at": datetime.now(timezone.utc).isoformat()}, indent=2) + "\n",
        encoding="utf-8",
    )


def _github_pr_activity_today(root: Path) -> dict[str, Any]:
    today = date.today().isoformat()
    ok, opened = try_gh(["pr", "list", "--search", f"created:>={today}", "--json", "number,title"])
    ok2, merged = try_gh(["pr", "list", "--state", "merged", "--search", f"merged:>={today}", "--json", "number,title"])
    ok3, closed = try_gh(["pr", "list", "--state", "closed", "--search", f"closed:>={today} -is:merged", "--json", "number,title"])
    return {
        "opened_today": opened if ok else UNKNOWN,
        "merged_today": merged if ok2 else UNKNOWN,
        "closed_unmerged_today": closed if ok3 else UNKNOWN,
    }


def _strategy_source_of_truth_drift() -> list[str]:
    """Compare Strategy_Inventory.md verdicts against risk_rules.yaml gate status.

    Flags only the unambiguous drift: a strategy the permission gate marks
    PAPER_ELIGIBLE (i.e. described as active) whose Strategy_Inventory verdict
    says BROKEN/RETIRE/WAIT, or a strategy present in the gate with no row in
    the inventory at all. Does not edit either file.
    """
    drift = []
    try:
        import yaml
        risk_rules = yaml.safe_load((ROOT / "risk_rules.yaml").read_text(encoding="utf-8"))
    except (OSError, ValueError, ImportError):
        return ["UNKNOWN: could not read risk_rules.yaml"]
    statuses = ((risk_rules or {}).get("strategy_permission_gate") or {}).get("strategy_status") or {}
    for strategy, status in statuses.items():
        if status != "PAPER_ELIGIBLE":
            continue
        row = _strategy_inventory_row(strategy)
        if row is None:
            drift.append(f"{strategy}: PAPER_ELIGIBLE in risk_rules.yaml but no matching row in Strategy_Inventory.md")
            continue
        verdict = row["verdict"].upper()
        if any(bad in verdict for bad in ("BROKEN", "RETIRE", "WAIT")):
            drift.append(
                f"{strategy}: risk_rules.yaml says PAPER_ELIGIBLE but "
                f"Strategy_Inventory.md verdict is {row['verdict']!r}"
            )
    return drift


def _trade_chain_window(since_date: str, through_date: str) -> list[Path]:
    return sorted(
        path for path in DEFAULT_JOURNAL_DIR.glob("journal_*.jsonl")
        if since_date <= path.stem.removeprefix("journal_") <= through_date
    )


def _trade_chain_report(since_date: str, through_date: str) -> dict[str, Any]:
    paths = _trade_chain_window(since_date, through_date)
    entries: list[dict[str, Any]] = []
    for path in paths:
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                entries.append({"type": "READ_ERROR", "_path": str(path), "_line": line_no})
                continue
            entry.setdefault("_path", str(path))
            entry.setdefault("_line", line_no)
            entries.append(entry)

    read_errors = [entry for entry in entries if entry.get("type") == "READ_ERROR"]

    pending: dict[str, dict[str, Any]] = {}
    resolved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for entry in entries:
        instrument = entry.get("instrument")
        decision = entry.get("decision")
        if decision == "TRADE":
            pending[instrument] = entry
        elif decision in ("NO_TRADE", "RISK_REJECTED"):
            rejected.append(entry)
        elif entry.get("type") == "OUTCOME" and isinstance(entry.get("outcome"), dict):
            trade = pending.pop(instrument, None)
            resolved.append({
                "trade": trade,
                "outcome": entry,
                "category": classify_outcome(entry["outcome"]),
                "orphan": trade is None,
            })

    attempts = sum(1 for e in entries if e.get("decision") == "TRADE")
    by_category: dict[str, int] = {}
    for item in resolved:
        by_category[item["category"]] = by_category.get(item["category"], 0) + 1
    fills = by_category.get("filled_win_loss", 0) + by_category.get("breakeven", 0)
    cancellations = by_category.get("cancelled_nofill", 0)
    needs_verification = by_category.get("reconciler_touched", 0)
    legitimately_open = len(pending)
    orphans = [item for item in resolved if item["orphan"]]

    rejected_missing_reason = [e for e in rejected if not (e.get("risk_check") or {}).get("reason") and not e.get("reason")]

    identity_holds = (fills + cancellations + needs_verification + legitimately_open) == attempts

    label_errors = strategy_intent_issues = reconciler_touched_count = 0
    label_summary = strategy_summary = reconciler_summary = UNKNOWN
    try:
        from ops.journal_label_audit import build_audit as label_audit
        label_report = label_audit(paths=paths) if paths else None
        if label_report is not None:
            # Mirror scripts/journal_label_audit.py's own exit-code convention:
            # only "error" severity fails the chain; "warning" (e.g. a TRADE row
            # with no structured risk_check on older journal formats) is surfaced
            # but does not flip TRADE CHAIN to FAIL on its own.
            label_errors = label_report["summary"]["issues_by_severity"].get("error", 0)
            label_summary = label_report["summary"]
    except ImportError:
        pass
    try:
        from ops.strategy_intent_audit import build_audit as intent_audit
        intent_report = intent_audit(paths=paths) if paths else None
        if intent_report is not None:
            strategy_intent_issues = intent_report["summary"]["issue_count"]
            strategy_summary = intent_report["summary"]
    except ImportError:
        pass
    try:
        from ops.reconciler_outcome_audit import build_audit_report as reconciler_audit
        reconciler_report = reconciler_audit(
            journal_dir=DEFAULT_JOURNAL_DIR,
            overrides_doc=ROOT / "docs" / "proof-operator-overrides.md",
            from_date=since_date,
            to_date=through_date,
        )
        reconciler_touched_count = reconciler_report["summary"]["total_touched"]
        reconciler_summary = reconciler_report["summary"]
    except ImportError:
        pass

    passed = (
        not read_errors
        and identity_holds
        and not orphans
        and not rejected_missing_reason
        and label_errors == 0
        and strategy_intent_issues == 0
    )

    return {
        "window": {"since": since_date, "through": through_date, "files": [str(p) for p in paths]},
        "read_only": True,
        "pass": passed,
        "attempts": attempts,
        "resolved": len(resolved),
        "fills": fills,
        "cancellations_no_fill": cancellations,
        "reconciler_touched_needs_verification": needs_verification,
        "legitimately_open": legitimately_open,
        "orphans": len(orphans),
        "orphan_detail": orphans,
        "rejected_candidates": len(rejected),
        "rejected_missing_reason": len(rejected_missing_reason),
        "accounting_identity_holds": identity_holds,
        "journal_read_errors": len(read_errors),
        "journal_label_audit": label_summary,
        "strategy_intent_audit": strategy_summary,
        "reconciler_outcome_audit": reconciler_summary,
    }


def daily_report(*, save_checkpoint: bool = True) -> dict[str, Any]:
    repo = collect_repo_state()
    runtime = collect_runtime_snapshot()
    github_activity = _github_pr_activity_today(discover_repo_root())
    strategy_drift = _strategy_source_of_truth_drift()

    through_date = date.today().isoformat()
    since_date = load_daily_checkpoint() or (date.today() - timedelta(days=1)).isoformat()
    trade_chain = _trade_chain_report(since_date, through_date)

    if save_checkpoint:
        save_daily_checkpoint(through_date)

    return {
        "routine": "daily",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "github": github_activity,
        "branches_worktrees": {
            "stale_merged_branches_hint": "not computed (would require checking each local "
            "branch's merge-base against origin/main; see local_only_branches + "
            "evidence_preservation below for the read-only equivalent)",
            "worktrees": repo["worktrees"],
            "dirty_worktree_paths": repo["dirty_files"] or repo["staged_files"],
            "branches_tracking_deleted_remotes": repo["branches_tracking_deleted_remotes"],
            "local_only_branches": repo["local_only_branches"],
            "main_vs_origin_main": repo["main_vs_origin_main"],
            "stash_count": repo["stash_count"],
        },
        "evidence_preservation": repo["evidence_preservation"],
        "deployed_state": runtime,
        "strategy_source_of_truth_drift": strategy_drift,
        "trade_chain": trade_chain,
    }


def format_daily(report: dict[str, Any]) -> str:
    chain = report["trade_chain"]
    lines = ["DAILY RECONCILIATION", ""]
    lines.append("GITHUB")
    lines.append(f"  {report['github']}")
    lines.append("")
    lines.append("BRANCHES / WORKTREES")
    bw = report["branches_worktrees"]
    lines.append(f"  local main vs origin/main: {bw['main_vs_origin_main']['relationship']}")
    lines.append(f"  branches tracking deleted remotes: {bw['branches_tracking_deleted_remotes'] or 'none'}")
    lines.append(f"  local-only branches: {bw['local_only_branches'] or 'none'}")
    lines.append(f"  stash count: {bw['stash_count']}")
    lines.append("")
    blockers = [item for item in report["evidence_preservation"] if item["blocker"]]
    lines.append(f"EVIDENCE PRESERVATION -- {len(blockers)} BLOCKER(s)")
    for item in blockers:
        lines.append(f"  - {item['branch']}: {item['unique_commits_vs_origin_main']} unique commits, no archive tag")
    lines.append("")
    lines.append("STRATEGY SOURCE OF TRUTH")
    if report["strategy_source_of_truth_drift"]:
        for item in report["strategy_source_of_truth_drift"]:
            lines.append(f"  - {item}")
    else:
        lines.append("  no drift detected")
    lines.append("")
    if chain["pass"]:
        lines.append(
            f"TRADE CHAIN: PASS\n"
            f"{chain['attempts']} attempts\n"
            f"{chain['fills']} fills\n"
            f"{chain['cancellations_no_fill']} no-fills\n"
            f"{chain['resolved']} resolved\n"
            f"{chain['legitimately_open']} legitimate opens\n"
            f"0 orphans\n"
            f"{chain['journal_read_errors']} journal read errors\n"
            f"reconciler-touched needing verification: {chain['reconciler_touched_needs_verification']}"
        )
    else:
        lines.append("TRADE CHAIN: FAIL -- details:")
        lines.append(json.dumps(chain, indent=2, default=str))
    return "\n".join(lines)


# ─────────────────────────── CLI ───────────────────────────

def _print(report: dict[str, Any], formatted: str, as_json: bool) -> None:
    print(json.dumps(report, indent=2, sort_keys=True, default=str) if as_json else formatted)


def cmd_session_start(args: argparse.Namespace) -> int:
    report = session_start_report()
    _print(report, format_session_start(report), args.json)
    return 0


def cmd_precommit(args: argparse.Namespace) -> int:
    report = precommit_report()
    _print(report, format_precommit(report), args.json)
    return 0 if report["ok"] else 1


def cmd_promotion(args: argparse.Namespace) -> int:
    report = promotion_report(args.strategy)
    _print(report, format_promotion(report), args.json)
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    report = daily_report(save_checkpoint=not args.no_save_checkpoint)
    _print(report, format_daily(report), args.json)
    return 0 if report["trade_chain"]["pass"] else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ops.project_check",
        description="Read-only session-safety, strategy-promotion, and daily-reconciliation routines.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_session = sub.add_parser("session-start", help="Repo + runtime snapshot at the start of a session.")
    p_session.add_argument("--json", action="store_true")
    p_session.set_defaults(func=cmd_session_start)

    p_precommit = sub.add_parser("precommit", help="Read-only fail-closed drift check before committing/pushing.")
    p_precommit.add_argument("--json", action="store_true")
    p_precommit.set_defaults(func=cmd_precommit)

    p_promotion = sub.add_parser("promotion", help="Strategy promotion proof-gate report.")
    p_promotion.add_argument("--strategy", required=True, help="Strategy key as used in risk_rules.yaml (e.g. orb_breakout).")
    p_promotion.add_argument("--json", action="store_true")
    p_promotion.set_defaults(func=cmd_promotion)

    p_daily = sub.add_parser("daily", help="Daily reconciliation + trade-chain integrity.")
    p_daily.add_argument("--json", action="store_true")
    p_daily.add_argument(
        "--no-save-checkpoint", action="store_true",
        help="Do not advance the daily checkpoint (dry run against the same window next time).",
    )
    p_daily.set_defaults(func=cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
