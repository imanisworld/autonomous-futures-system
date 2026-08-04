"""ops/project_check.py — read-only repo/process safety routines.

Three manually-invoked routines. No cron, no daemon, no background service —
every subcommand runs once, prints a report, and exits.

  session-start   Repo + branch + worktree + local runtime-config snapshot at
                   the start of a work session. Also records session state to
                   logs/.project_check_session_state.json for `precommit` to
                   compare against later.
  precommit       Fail-closed drift check before commit/push. READ ONLY —
                   never commits, pushes, pulls, resets, rebases, checks out,
                   deletes a branch/worktree, drops a stash, or edits a file.
  promotion       Strategy promotion proof-gate audit against an EXISTING
                   evidence artifact (scripts/*_canonical_evidence*.json or
                   similar). Does not itself run ReplayEngine, DecisionEngine,
                   RiskEngine, or PaperBroker — it audits evidence already
                   produced through that path.
  daily           Daily reconciliation: PR/branch/worktree hygiene, evidence
                   preservation, deployed-state drift, strategy-inventory
                   drift, and trade-chain integrity — all in one read-only
                   pass.

Every subcommand is pure Python + `git`/`gh` subprocess calls with argument
lists (never shell=True), so there is no Bash/zsh word-splitting hazard to
guard against. No SSH, no broker calls, no journal writes.

Reuses rather than duplicates:
  ops.live_box_guard          deployed-state / runtime-override drift report
  ops.proof_30_mnq            journal read/pairing primitives, outcome classification
  ops.reconciler_outcome_audit reconciler-touched outcome classification

Run with `python -m ops.project_check <subcommand> --help` for options.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ops.live_box_guard import live_box_drift_report
from ops.proof_30_mnq import (
    classify_outcome,
    load_json_url,
    pair_resolved_trades,
    parse_proof_ts,
    read_journal_entries,
)
from ops.reconciler_outcome_audit import build_audit_report as build_reconciler_audit_report

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_STATE_PATH = REPO_ROOT / "logs" / ".project_check_session_state.json"
DAILY_CHECKPOINT_PATH = REPO_ROOT / "logs" / ".project_check_daily_checkpoint.json"
UNKNOWN = "UNKNOWN"


# ─────────────────────────── shared helpers ───────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(repo_root: Path, *args: str, timeout: float = 6.0) -> tuple[str | None, str | None]:
    """Run a read-only git command. Returns (stdout, error) — never raises."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - report, never crash a read-only check
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or "").strip() or f"git {' '.join(args)} exited {result.returncode}"
    return result.stdout.strip(), None


def _git_lines(repo_root: Path, *args: str) -> list[str]:
    out, _ = _git(repo_root, *args)
    if not out:
        return []
    return [line for line in out.splitlines() if line.strip()]


def _ref_exists(repo_root: Path, ref: str) -> bool:
    _, err = _git(repo_root, "show-ref", "--verify", "--quiet", ref)
    return err is None


def repo_root_of(start: Path) -> Path:
    out, _ = _git(start, "rev-parse", "--show-toplevel")
    return Path(out) if out else start


def current_branch(repo_root: Path) -> str:
    out, err = _git(repo_root, "branch", "--show-current")
    if err is not None:
        return UNKNOWN
    return out if out else "(detached HEAD)"


def list_worktrees(repo_root: Path) -> list[dict[str, Any]]:
    out, err = _git(repo_root, "worktree", "list", "--porcelain")
    if out is None:
        return []
    worktrees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                worktrees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[len("worktree "):].strip(), "branch": None}
        elif line.startswith("HEAD "):
            current["head"] = line[len("HEAD "):].strip()
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            current["branch"] = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    if current:
        worktrees.append(current)
    return worktrees


def _worktree_dirty(wt: dict[str, Any]) -> bool | str:
    path = wt.get("path")
    if not path or not Path(path).exists():
        return UNKNOWN
    out, err = _git(Path(path), "status", "--porcelain")
    if err is not None:
        return UNKNOWN
    return bool(out)


def rev_list_relationship(repo_root: Path, left: str, right: str) -> str:
    """IN_SYNC / AHEAD / BEHIND / DIVERGED of `left` relative to `right`."""
    out, err = _git(repo_root, "rev-list", "--left-right", "--count", f"{left}...{right}")
    if err is not None or not out:
        return UNKNOWN
    parts = out.split()
    if len(parts) != 2:
        return UNKNOWN
    try:
        ahead, behind = int(parts[0]), int(parts[1])
    except ValueError:
        return UNKNOWN
    if ahead == 0 and behind == 0:
        return "IN_SYNC"
    if ahead > 0 and behind == 0:
        return "AHEAD"
    if ahead == 0 and behind > 0:
        return "BEHIND"
    return "DIVERGED"


def working_tree_status(repo_root: Path) -> dict[str, list[str]]:
    return {
        "staged": _git_lines(repo_root, "diff", "--cached", "--name-only"),
        "dirty_tracked": _git_lines(repo_root, "diff", "--name-only"),
        "untracked": _git_lines(repo_root, "ls-files", "--others", "--exclude-standard"),
    }


def branch_tracking_report(repo_root: Path) -> dict[str, list[str]]:
    out, err = _git(
        repo_root,
        "for-each-ref",
        "--format=%(refname:short)%09%(upstream:short)%09%(upstream:track)",
        "refs/heads/",
    )
    gone: list[str] = []
    local_only: list[str] = []
    tracking_ok: list[str] = []
    if err is None and out:
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            name, upstream, track = parts
            if not upstream:
                local_only.append(name)
            elif "[gone]" in track:
                gone.append(name)
            else:
                tracking_ok.append(name)
    return {"tracking_deleted_remote": gone, "local_only": local_only, "tracking_ok": tracking_ok}


def stash_list(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "stash", "list")


def archive_tags(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, "tag", "-l", "archive/*")


def unique_evidence_without_archive_tag(
    repo_root: Path, *, default_branch: str = "main", exclude_branches: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Local branches with commits not on `default_branch` and no matching
    `archive/<branch>*` tag. This does NOT know PR-closed state on its own —
    cross-reference with the `github` section (if `gh` is available) before
    treating a branch as a real cleanup candidate. `exclude_branches` should
    be every branch currently checked out in any worktree (active WIP is
    never "closed", regardless of tag state)."""
    if not _ref_exists(repo_root, f"refs/heads/{default_branch}"):
        return []
    tags = archive_tags(repo_root)
    branches = [
        b for b in _git_lines(repo_root, "for-each-ref", "--format=%(refname:short)", "refs/heads/")
        if b != default_branch and b not in exclude_branches
    ]
    flagged = []
    for branch in branches:
        ahead_out, err = _git(repo_root, "rev-list", "--count", f"{default_branch}..{branch}")
        ahead = int(ahead_out) if (err is None and ahead_out and ahead_out.isdigit()) else 0
        if ahead <= 0:
            continue
        has_tag = any(tag.startswith(f"archive/{branch}") for tag in tags)
        if not has_tag:
            flagged.append({"branch": branch, "unique_commits_ahead_of_main": ahead, "archive_tag": None})
    return flagged


def gh_available() -> bool:
    return shutil.which("gh") is not None


def gh_pr_list(repo_root: Path, *, state: str = "all", limit: int = 200) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Best-effort read-only PR listing via the `gh` CLI, if installed and
    authenticated. Never treated as required — callers must degrade to
    UNKNOWN when this returns (None, error)."""
    if not gh_available():
        return None, "gh CLI not found on PATH"
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list",
                "--state", state,
                "--limit", str(limit),
                "--json", "number,title,state,isDraft,createdAt,closedAt,mergedAt,headRefName,baseRefName,url",
            ],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)
    if result.returncode != 0:
        return None, (result.stderr or "").strip() or "gh pr list failed"
    try:
        return json.loads(result.stdout), None
    except json.JSONDecodeError as exc:
        return None, f"gh pr list returned invalid JSON: {exc}"


def _pr_summary(pr: dict[str, Any]) -> dict[str, Any]:
    return {k: pr.get(k) for k in ("number", "title", "state", "isDraft", "createdAt", "closedAt", "mergedAt", "headRefName", "baseRefName", "url")}


def _days_since(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    dt = parse_proof_ts(iso_ts)
    if dt is None:
        return None
    return (datetime.now(timezone.utc) - dt).days


# ─────────────────────────── session state file ───────────────────────────

def write_session_state(repo_root: Path, worktree: Path) -> dict[str, Any]:
    branch = current_branch(repo_root)
    head, _ = _git(repo_root, "rev-parse", "HEAD")
    upstream, _ = _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    state = {
        "generated_at": _now_iso(),
        "repo_root": str(repo_root),
        "worktree": str(worktree),
        "branch": branch,
        "head_sha": head or UNKNOWN,
        "upstream": upstream or UNKNOWN,
    }
    SESSION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SESSION_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def read_session_state() -> dict[str, Any] | None:
    if not SESSION_STATE_PATH.exists():
        return None
    try:
        return json.loads(SESSION_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ─────────────────────────── runtime snapshot (session-start) ─────────────

def build_runtime_snapshot(repo_root: Path) -> dict[str, Any]:
    """Local repo + local process view only. This is NOT a deployed-box read
    — it cannot see what's actually running on a remote box unless this
    process happens to be running on that box with the EXPECTED_* pins set
    (see ops.live_box_guard)."""
    rules_path = repo_root / "risk_rules.yaml"
    rules: dict[str, Any] = {}
    rules_error = None
    if rules_path.exists():
        try:
            rules = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            rules_error = str(exc)
    else:
        rules_error = "risk_rules.yaml not found"

    strategy_cfg = rules.get("strategy") or {}
    gate_cfg = rules.get("strategy_permission_gate") or {}
    gate_statuses = gate_cfg.get("strategy_status") or {}
    enabled_concepts = strategy_cfg.get("enabled_concepts") or []
    active_paper_lanes = [
        c for c in enabled_concepts
        if gate_statuses.get(c, gate_cfg.get("default_status")) == "PAPER_ELIGIBLE"
    ]
    position_rules = rules.get("position_rules") or {}

    env_overrides = {
        name: os.getenv(name)
        for name in (
            "ENTRY_FILL_MODEL",
            "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
            "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
            "MAX_CONTRACTS_HARD_CAP",
        )
    }

    drift = live_box_drift_report(repo_root=repo_root)

    return {
        "source_scope": "local repo + local process environment only — NOT a deployed-box read",
        "risk_rules_error": rules_error,
        "active_paper_eligible_lanes": active_paper_lanes,
        "strategy_permission_gate_default_status": gate_cfg.get("default_status", UNKNOWN),
        "enabled_concepts": enabled_concepts,
        "disabled_concepts_per_instrument": strategy_cfg.get("disabled_concepts_per_instrument") or {},
        "committed_fill_model": rules.get("fill_model") or UNKNOWN,
        "entry_fill_model_env_overrides": env_overrides,
        "quantity_contract_caps": position_rules.get("max_contracts_per_instrument") or UNKNOWN,
        "max_open_positions": position_rules.get("max_open_positions", UNKNOWN),
        "risk_rules_sha256": drift.get("risk_rules_sha256", UNKNOWN),
        "deployed_state_pins": {
            "expected_live_branch": os.getenv("EXPECTED_LIVE_BRANCH") or UNKNOWN,
            "expected_live_commit": os.getenv("EXPECTED_LIVE_COMMIT") or UNKNOWN,
            "expected_risk_rules_sha256": os.getenv("EXPECTED_RISK_RULES_SHA256") or UNKNOWN,
            "note": "unset unless this process is running ON the deployed box with those env vars pinned",
        },
        "evidence_epoch": UNKNOWN,
        "evidence_epoch_note": (
            "no single global 'evidence epoch' identifier exists in this repo; closest "
            "proxies are risk_rules_sha256 above and each strategy's own evidence "
            "artifact under scripts/*_canonical_evidence_results.json — see "
            "`promotion --strategy <name>`"
        ),
    }


# ─────────────────────────── session-start ─────────────────────────────────

def cmd_session_start(args: argparse.Namespace) -> int:
    repo_root = repo_root_of(Path.cwd())
    worktree_before = Path.cwd().resolve()
    branch_before = current_branch(repo_root)

    head, _ = _git(repo_root, "rev-parse", "HEAD")
    origin_main, _ = _git(repo_root, "rev-parse", "origin/main")
    main_relationship = UNKNOWN
    if origin_main:
        left_ref = "main" if _ref_exists(repo_root, "refs/heads/main") else branch_before
        main_relationship = rev_list_relationship(repo_root, left_ref, "origin/main")

    upstream, _ = _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    worktrees = list_worktrees(repo_root)
    wt_status = working_tree_status(repo_root)
    tracking = branch_tracking_report(repo_root)
    stashes = stash_list(repo_root)
    tags = archive_tags(repo_root)
    checked_out_branches = frozenset(wt.get("branch") for wt in worktrees if wt.get("branch"))
    unique_no_tag = unique_evidence_without_archive_tag(repo_root, exclude_branches=checked_out_branches)

    prs, pr_err = (None, "skipped (--skip-github)") if args.skip_github else gh_pr_list(repo_root)

    write_session_state(repo_root, worktree_before)
    branch_after = current_branch(repo_root)

    report = {
        "routine": "session-start",
        "generated_at": _now_iso(),
        "repo": {
            "repo_root": str(repo_root),
            "current_branch": branch_before,
            "head_sha": head or UNKNOWN,
            "origin_main_sha": origin_main or UNKNOWN,
            "local_main_relationship_to_origin": main_relationship,
            "upstream": upstream or UNKNOWN,
            "current_worktree": str(worktree_before),
            "worktrees": worktrees,
            "dirty_tracked_files": wt_status["dirty_tracked"],
            "staged_files": wt_status["staged"],
            "untracked_files": wt_status["untracked"],
            "branches_tracking_deleted_remotes": tracking["tracking_deleted_remote"],
            "local_only_branches": tracking["local_only"],
            "closed_unmerged_branches_missing_archive_tag": unique_no_tag,
            "archive_tags": tags,
            "stash_count": len(stashes),
            "stashes": stashes,
            "branch_changed_during_check": branch_after != branch_before,
        },
        "github": {
            "available": prs is not None,
            "error": pr_err,
            "open_prs": [_pr_summary(p) for p in prs if p.get("state") == "OPEN"] if prs is not None else UNKNOWN,
        },
        "runtime_snapshot": build_runtime_snapshot(repo_root),
        "session_state_file": str(SESSION_STATE_PATH),
    }
    _emit(report, args)
    return 0


# ─────────────────────────── precommit ─────────────────────────────────────

def cmd_precommit(args: argparse.Namespace) -> int:
    repo_root = repo_root_of(Path.cwd())
    worktree_now = Path.cwd().resolve()
    branch_now = current_branch(repo_root)
    head_now, head_err = _git(repo_root, "rev-parse", "HEAD")
    upstream_now, _ = _git(repo_root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")

    state = read_session_state()
    problems: list[str] = []

    if state is None:
        problems.append("no session-start state on disk — run `session-start` first; session-start state cannot be verified")
    else:
        if state.get("branch") != branch_now:
            problems.append(f"branch differs from session-start: was {state.get('branch')!r}, now {branch_now!r}")
        if state.get("worktree") != str(worktree_now):
            problems.append(f"worktree differs from session-start: was {state.get('worktree')!r}, now {worktree_now!r}")
        if state.get("repo_root") != str(repo_root):
            problems.append(f"repo root differs from session-start: was {state.get('repo_root')!r}, now {str(repo_root)!r}")

    worktrees = list_worktrees(repo_root)
    owner_conflict = next(
        (wt for wt in worktrees if wt.get("branch") == branch_now and Path(wt.get("path", "")).resolve() != worktree_now),
        None,
    )
    if owner_conflict:
        problems.append(f"branch {branch_now!r} is also checked out in another worktree: {owner_conflict.get('path')}")

    ahead_behind = rev_list_relationship(repo_root, branch_now, upstream_now) if upstream_now else UNKNOWN

    wt_status = working_tree_status(repo_root)

    if head_err is not None or branch_now == UNKNOWN:
        problems.append("repository state is ambiguous — could not resolve HEAD/branch cleanly")

    ok = not problems
    report = {
        "routine": "precommit",
        "generated_at": _now_iso(),
        "read_only": True,
        "repo_root": str(repo_root),
        "current_branch": branch_now,
        "current_head": head_now or UNKNOWN,
        "session_start_branch": (state or {}).get("branch", UNKNOWN),
        "session_start_worktree": (state or {}).get("worktree", UNKNOWN),
        "current_worktree": str(worktree_now),
        "upstream": upstream_now or UNKNOWN,
        "ahead_behind_upstream": ahead_behind,
        "changed_files": sorted(set(wt_status["dirty_tracked"]) | set(wt_status["staged"]) | set(wt_status["untracked"])),
        "staged_files": wt_status["staged"],
        "untracked_files": wt_status["untracked"],
        "verdict": "PASS" if ok else "FAIL_CLOSED",
        "problems": problems,
    }
    _emit(report, args)
    return 0 if ok else 1


# ─────────────────────────── promotion ─────────────────────────────────────

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "raw_candidate_count": ("raw_candidates", "candidate_count", "n_candidates", "total_candidates"),
    "attempts": ("attempts", "entry_attempts", "order_attempts"),
    "fills": ("fills", "filled", "fill_count"),
    "cancellations": ("cancellations", "cancelled", "no_fills", "cancelled_count"),
    "rejects": ("rejects", "rejected", "reject_count"),
    "resolved": ("resolved", "resolved_count", "resolved_trades"),
    "legitimately_open": ("open", "open_positions", "legitimately_open"),
    "net_pnl": ("net_pnl", "total_pnl", "pnl_dollars", "net"),
    "profit_factor": ("profit_factor", "pf"),
    "expectancy": ("expectancy", "expectancy_per_trade"),
    "win_rate": ("win_rate", "wr"),
    "entry_fill_model": ("entry_fill_model", "fill_model"),
    "entry_tolerance": ("entry_tolerance_ticks", "tolerance_ticks", "entry_tolerance"),
    "verdict": ("verdict", "classification"),
}

# Priority order matters: worse classifications are matched first.
INVENTORY_VERDICT_MAP: dict[str, str] = {
    "BROKEN": "BROKEN",
    "RETIRE": "BROKEN",
    "WAIT": "WAIT",
    "RESEARCH ONLY": "WAIT",
    "VALIDATED": "VALIDATED",
    "PAPER PROOF": "PROMISING BUT UNPROVEN",
    "PROMISING BUT UNPROVEN": "PROMISING BUT UNPROVEN",
}


def _find_first(obj: Any, keys: tuple[str, ...], *, _depth: int = 0) -> Any:
    """Depth-limited breadth-first search for the first matching key
    (case-insensitive) anywhere in a nested dict. Never guesses across
    ambiguous structures beyond a shallow depth."""
    if _depth > 4 or not isinstance(obj, dict):
        return None
    for k, v in obj.items():
        if str(k).lower() in keys:
            return v
    for v in obj.values():
        if isinstance(v, dict):
            found = _find_first(v, keys, _depth=_depth + 1)
            if found is not None:
                return found
    return None


def extract_evidence_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {name: _find_first(payload, aliases) for name, aliases in FIELD_ALIASES.items()}


def check_accounting_identity(fields: dict[str, Any]) -> dict[str, Any]:
    attempts, fills = fields.get("attempts"), fields.get("fills")
    cancellations, rejects = fields.get("cancellations"), fields.get("rejects")
    resolved, open_ = fields.get("resolved"), fields.get("legitimately_open")

    checks = []
    if all(isinstance(x, (int, float)) for x in (attempts, fills, cancellations, rejects)):
        rhs = fills + cancellations + rejects
        checks.append({"identity": "attempts = fills + cancellations + rejects", "lhs": attempts, "rhs": rhs, "ok": attempts == rhs})
    if all(isinstance(x, (int, float)) for x in (fills, resolved, open_)):
        rhs = resolved + open_
        checks.append({"identity": "fills = resolved + legitimately_open", "lhs": fills, "rhs": rhs, "ok": fills == rhs})
    return {
        "checks": checks,
        "computable": bool(checks),
        "all_pass": (all(c["ok"] for c in checks) if checks else None),
    }


def strategy_gate_status(rules: dict[str, Any], strategy: str) -> dict[str, Any]:
    gate = rules.get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    enabled_concepts = (rules.get("strategy") or {}).get("enabled_concepts") or []
    disabled_per_instrument = (rules.get("strategy") or {}).get("disabled_concepts_per_instrument") or {}
    return {
        "permission_status": statuses.get(strategy, gate.get("default_status", UNKNOWN)),
        "in_enabled_concepts": strategy in enabled_concepts,
        "disabled_per_instrument": {
            inst: strategy in (concepts or []) for inst, concepts in disabled_per_instrument.items()
        },
    }


def parse_inventory_table(repo_root: Path) -> list[dict[str, str]]:
    path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not path.exists():
        return []
    rows: list[dict[str, str]] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("| Strategy |"):
            in_table = True
            continue
        if in_table and line.startswith("|---"):
            continue
        if in_table:
            if not line.startswith("|"):
                break
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            rows.append({"name": cells[0], "verdict": cells[-1]})
    return rows


def _normalize_strategy_name(s: str) -> str:
    return s.lower().replace("_", " ").replace("-", " ")


def find_inventory_row(repo_root: Path, strategy: str) -> dict[str, Any]:
    rows = parse_inventory_table(repo_root)
    if not rows:
        return {"found": False, "reason": "Strategy_Inventory.md not found or has no parseable table"}
    target = _normalize_strategy_name(strategy)
    matches = [r for r in rows if target in _normalize_strategy_name(r["name"])]
    if not matches:
        return {"found": False, "reason": f"no Strategy_Inventory.md row matched {strategy!r}"}
    return {"found": True, "matches": matches}


def classify_promotion(
    *, evidence_found: bool, accounting: dict[str, Any], gate: dict[str, Any],
    inventory: dict[str, Any], evidence_fields: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not evidence_found:
        reasons.append("no evidence artifact located for this strategy")
        return "WAIT", reasons

    if accounting["computable"] and accounting["all_pass"] is False:
        reasons.append(
            "accounting identity mismatch in the evidence artifact "
            "(attempts/fills/cancellations/rejects or fills/resolved/open do not reconcile) "
            "— do not trust reported counts until reconciled"
        )
        return "UNSAFE", reasons

    fills = evidence_fields.get("fills")
    if isinstance(fills, (int, float)) and fills == 0:
        reasons.append("zero executable fills reported in evidence")
        return "WAIT", reasons

    if inventory.get("found"):
        verdicts_upper = {m["verdict"].upper() for m in inventory["matches"]}
        for label, mapped in INVENTORY_VERDICT_MAP.items():
            if any(label in v for v in verdicts_upper):
                reasons.append(f"Strategy_Inventory.md records a {label} verdict for a matching row -> {mapped}")
                return mapped, reasons

    if gate.get("permission_status") == "PAPER_ELIGIBLE" and gate.get("in_enabled_concepts"):
        reasons.append("evidence located, no accounting mismatch detected, currently PAPER_ELIGIBLE and enabled at runtime")
    else:
        reasons.append("evidence located but strategy is not currently PAPER_ELIGIBLE/enabled at runtime")
    reasons.append(
        "defaults to PROMISING BUT UNPROVEN pending manual verification of the real "
        "ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker path "
        "(OVERFIT is never auto-assigned by this tool — it requires a walk-forward "
        "split comparison specific to the strategy's own evidence script)"
    )
    return "PROMISING BUT UNPROVEN", reasons


def _locate_evidence(repo_root: Path, strategy: str) -> tuple[Path | None, list[Path]]:
    all_matches: list[Path] = []
    scripts_dir = repo_root / "scripts"
    if scripts_dir.exists():
        for path in sorted(scripts_dir.glob(f"*{strategy}*.json")):
            if "raw_trades" in path.name:
                continue
            if "results" in path.name or "evidence" in path.name:
                all_matches.append(path)
    canonical = [p for p in all_matches if "canonical_evidence" in p.name]
    pool = canonical or all_matches
    if len(pool) == 1:
        return pool[0], all_matches
    return None, all_matches


def cmd_promotion(args: argparse.Namespace) -> int:
    repo_root = repo_root_of(Path.cwd())
    strategy = args.strategy

    if args.evidence:
        evidence_path: Path | None = Path(args.evidence)
        all_matches = [evidence_path]
    else:
        evidence_path, all_matches = _locate_evidence(repo_root, strategy)

    payload: dict[str, Any] | None = None
    load_error: str | None = None
    if evidence_path and evidence_path.exists():
        try:
            payload = json.loads(evidence_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            load_error = str(exc)
    elif evidence_path is None and len(all_matches) > 1:
        load_error = f"ambiguous: {len(all_matches)} candidate evidence files found, pass --evidence explicitly"
    elif evidence_path is None:
        load_error = "no evidence artifact found under scripts/*<strategy>*results*.json or *canonical_evidence*.json"
    elif not evidence_path.exists():
        load_error = f"{evidence_path} does not exist"

    rules_path = repo_root / "risk_rules.yaml"
    rules = (yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}) if rules_path.exists() else {}

    evidence_fields = extract_evidence_fields(payload) if isinstance(payload, dict) else {k: None for k in FIELD_ALIASES}
    accounting = check_accounting_identity(evidence_fields)
    gate = strategy_gate_status(rules, strategy)
    inventory = find_inventory_row(repo_root, strategy)

    classification, reasons = classify_promotion(
        evidence_found=payload is not None,
        accounting=accounting,
        gate=gate,
        inventory=inventory,
        evidence_fields=evidence_fields,
    )

    report = {
        "routine": "promotion",
        "generated_at": _now_iso(),
        "strategy": strategy,
        "read_only": True,
        "note": (
            "Audits an EXISTING evidence artifact for accounting-identity consistency and "
            "cross-checks it against risk_rules.yaml and Strategy_Inventory.md. Does NOT "
            "execute ReplayEngine, DecisionEngine, RiskEngine, or PaperBroker itself. If no "
            "evidence artifact exists yet, produce one via the strategy's canonical evidence "
            "script (scripts/*_canonical_evidence*.py) through the real executable path, "
            "then re-run this gate against its output."
        ),
        "evidence_artifact": str(evidence_path) if evidence_path else None,
        "evidence_candidates_considered": [str(p) for p in all_matches],
        "evidence_load_error": load_error,
        "identity_parity": {
            "note": "fields absent from the evidence artifact are reported as null — never invented",
            "raw_candidate_count": evidence_fields.get("raw_candidate_count"),
        },
        "execution": {
            "attempts": evidence_fields.get("attempts"),
            "fills": evidence_fields.get("fills"),
            "cancellations": evidence_fields.get("cancellations"),
            "rejects": evidence_fields.get("rejects"),
            "resolved": evidence_fields.get("resolved"),
            "legitimately_open": evidence_fields.get("legitimately_open"),
            "accounting_identity": accounting,
        },
        "performance": {
            "net_pnl": evidence_fields.get("net_pnl"),
            "profit_factor": evidence_fields.get("profit_factor"),
            "expectancy": evidence_fields.get("expectancy"),
            "win_rate": evidence_fields.get("win_rate"),
        },
        "execution_context": {
            "entry_fill_model_in_evidence": evidence_fields.get("entry_fill_model"),
            "entry_tolerance_in_evidence": evidence_fields.get("entry_tolerance"),
            "committed_fill_model_risk_rules": rules.get("fill_model") or UNKNOWN,
            "runtime_entry_fill_model_env": os.getenv("ENTRY_FILL_MODEL") or UNKNOWN,
            "quantity_contract_caps": (rules.get("position_rules") or {}).get("max_contracts_per_instrument") or UNKNOWN,
        },
        "runtime_gate_status": gate,
        "strategy_inventory_cross_check": inventory,
        "classification": classification,
        "classification_reasons": reasons,
        "rules_applied": [
            "no rescue/tuning variant applied during this pass",
            "no automatic runtime change",
            "no automatic merge",
            "no deployment",
            "no config edit",
        ],
    }
    _emit(report, args)
    return 0 if classification != "UNSAFE" else 1


# ─────────────────────────── daily ─────────────────────────────────────────

def strategy_source_of_truth(repo_root: Path) -> dict[str, Any]:
    rules_path = repo_root / "risk_rules.yaml"
    rules = (yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}) if rules_path.exists() else {}
    gate = rules.get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    enabled_concepts = (rules.get("strategy") or {}).get("enabled_concepts") or []
    inventory_rows = parse_inventory_table(repo_root)

    drift: list[dict[str, Any]] = []
    for concept in sorted(set(enabled_concepts) | set(statuses.keys())):
        permission = statuses.get(concept, gate.get("default_status", UNKNOWN))
        target = _normalize_strategy_name(concept)
        matches = [r for r in inventory_rows if target in _normalize_strategy_name(r["name"])]
        active_at_runtime = concept in enabled_concepts and permission == "PAPER_ELIGIBLE"

        if not matches:
            if active_at_runtime:
                drift.append({
                    "strategy": concept,
                    "issue": "missing_from_inventory",
                    "detail": "active at runtime (enabled + PAPER_ELIGIBLE) but no Strategy_Inventory.md row matched",
                })
            continue

        verdicts = {m["verdict"].upper() for m in matches}
        stale = any(bad in v for v in verdicts for bad in ("BROKEN", "RETIRE", "WAIT", "RESEARCH ONLY"))
        if active_at_runtime and stale:
            drift.append({
                "strategy": concept,
                "issue": "active_but_inventory_says_not_ready",
                "inventory_verdicts": sorted(verdicts),
                "runtime_permission": permission,
            })
        ready = any("VALIDATED" in v or "PAPER PROOF" in v for v in verdicts)
        if not active_at_runtime and ready:
            drift.append({
                "strategy": concept,
                "issue": "inventory_says_ready_but_not_active_or_not_paper_eligible",
                "inventory_verdicts": sorted(verdicts),
                "runtime_permission": permission,
                "in_enabled_concepts": concept in enabled_concepts,
            })

    return {
        "enabled_concepts": enabled_concepts,
        "strategy_permission_gate_default_status": gate.get("default_status", UNKNOWN),
        "strategy_status_table": statuses,
        "inventory_rows_found": len(inventory_rows),
        "drift": drift,
    }


def _after_freeze(ts_value: Any, freeze_ts: datetime | None) -> bool:
    if freeze_ts is None:
        return True
    ts = parse_proof_ts(ts_value)
    return ts is not None and ts >= freeze_ts


def _load_broker_snapshot(broker_json: Path | None, api_base: str | None) -> tuple[dict[str, Any] | None, str | None]:
    if broker_json:
        try:
            return json.loads(broker_json.read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as exc:
            return None, str(exc)
    if api_base:
        return load_json_url(f"{api_base.rstrip('/')}/status/broker-account")
    return None, None


def trade_chain_integrity(
    journal_dir: Path, *, since_date: str | None, broker_json: Path | None, api_base: str | None,
) -> dict[str, Any]:
    entries = read_journal_entries(journal_dir)
    freeze_ts = parse_proof_ts(f"{since_date}T00:00:00Z") if since_date else None

    instruments = sorted({str(e.get("instrument")).upper() for e in entries if e.get("instrument")})
    if not instruments:
        instruments = ["MNQ", "MES"]

    per_instrument: dict[str, Any] = {}
    totals = {"attempts": 0, "fills": 0, "cancellations_no_fill": 0, "needs_broker_verification": 0, "orphan_outcomes": 0}

    for inst in instruments:
        resolved, unmatched = pair_resolved_trades(entries, instrument=inst, freeze_ts=freeze_ts, limit=1_000_000)
        attempts = sum(
            1 for e in entries
            if str(e.get("instrument", "")).upper() == inst
            and e.get("decision") == "TRADE"
            and (e.get("risk_check") or {}).get("result") == "APPROVED"
            and _after_freeze(e.get("ts"), freeze_ts)
        )
        categories = [classify_outcome(t.outcome_body) for t in resolved]
        filled_wl = categories.count("filled_win_loss")
        breakeven = categories.count("breakeven")
        cancelled = categories.count("cancelled_nofill")
        needs_verification = categories.count("reconciler_touched")
        other = categories.count("other")
        fills = filled_wl + breakeven
        accounted = fills + cancelled + needs_verification + other
        legitimately_open = 1 if attempts > accounted else 0

        per_instrument[inst] = {
            "attempts": attempts,
            "fills": fills,
            "breakeven_count": breakeven,
            "cancellations_no_fill": cancelled,
            "needs_broker_verification_reconciler_touched": needs_verification,
            "other_outcome": other,
            "resolved": fills,
            "legitimately_open": legitimately_open,
            "orphan_outcomes_no_matching_attempt": len(unmatched),
            "accounting_identity_ok": attempts == accounted + legitimately_open,
        }
        totals["attempts"] += attempts
        totals["fills"] += fills
        totals["cancellations_no_fill"] += cancelled
        totals["needs_broker_verification"] += needs_verification
        totals["orphan_outcomes"] += len(unmatched)

    reconciler_audit = build_reconciler_audit_report(journal_dir=journal_dir, from_date=since_date)

    open_position: Any = None
    try:
        from journal.journal_logger import JournalLogger
        open_position = JournalLogger(str(journal_dir)).get_open_position()
    except Exception as exc:  # noqa: BLE001
        open_position = {"error": str(exc)}

    broker_parity: dict[str, Any] = {
        "available": False,
        "note": "no broker snapshot provided; pass --broker-json or --api-base to cross-check journal vs broker state",
    }
    if broker_json or api_base:
        payload, err = _load_broker_snapshot(broker_json, api_base)
        if payload is not None:
            broker_has_position = bool(payload.get("positions") or payload.get("open_position"))
            journal_has_position = bool(open_position) and not (isinstance(open_position, dict) and open_position.get("error"))
            broker_parity = {
                "available": True,
                "journal_has_open_position": journal_has_position,
                "broker_reports_open_position": broker_has_position,
                "parity_ok": journal_has_position == broker_has_position,
            }
        else:
            broker_parity = {"available": False, "error": err}

    accounting_mismatches = [inst for inst, data in per_instrument.items() if not data["accounting_identity_ok"]]
    passed = (
        totals["orphan_outcomes"] == 0
        and not accounting_mismatches
        and reconciler_audit["summary"]["unaudited"] == 0
        and (not broker_parity.get("available") or broker_parity.get("parity_ok", True))
    )

    return {
        "since_date": since_date or "(entire journal history — no checkpoint set; pass --since-date to scope)",
        "window_limitation": (
            "date-scoped pairing can flag a position opened before the window and resolved "
            "inside it as an apparent orphan outcome — treat any orphan here as needing manual "
            "confirmation against the prior day's journal, not an automatic incident"
        ),
        "instruments_scanned": instruments,
        "per_instrument": per_instrument,
        "totals": totals,
        "reconciler_touched_outcomes_audit": reconciler_audit["summary"],
        "reconciler_touched_unaudited_detail": reconciler_audit["unaudited"],
        "current_open_position": open_position,
        "broker_parity": broker_parity,
        "protective_bracket_and_broker_order_state": {
            "available": False,
            "note": (
                "naked-position / stale-child-order / duplicate-order-identity checks require "
                "live broker order-book data this local, read-only, no-SSH routine does not have "
                "access to in this environment. UNKNOWN — do not infer."
            ),
        },
        "accounting_mismatches": accounting_mismatches,
        "pass": passed,
    }


def _daily_checkpoint_read() -> str | None:
    if not DAILY_CHECKPOINT_PATH.exists():
        return None
    try:
        return json.loads(DAILY_CHECKPOINT_PATH.read_text(encoding="utf-8")).get("last_checkpoint_date")
    except (OSError, json.JSONDecodeError):
        return None


def _daily_checkpoint_write(checkpoint_date: str) -> None:
    DAILY_CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    DAILY_CHECKPOINT_PATH.write_text(
        json.dumps({"last_checkpoint_date": checkpoint_date, "updated_at": _now_iso()}, indent=2) + "\n",
        encoding="utf-8",
    )


def cmd_daily(args: argparse.Namespace) -> int:
    repo_root = repo_root_of(Path.cwd())
    journal_dir = Path(args.journal_dir) if args.journal_dir else Path(os.getenv("LOG_DIR", str(repo_root / "logs")))

    since_date = args.since_date or _daily_checkpoint_read()
    today_str = date.today().isoformat()

    # A. GitHub / repo reconciliation
    prs, pr_err = (None, "skipped (--skip-github)") if args.skip_github else gh_pr_list(repo_root)
    tracking = branch_tracking_report(repo_root)
    worktrees = list_worktrees(repo_root)
    stashes = stash_list(repo_root)
    checked_out_branches = frozenset(wt.get("branch") for wt in worktrees if wt.get("branch"))
    unique_no_tag = unique_evidence_without_archive_tag(repo_root, exclude_branches=checked_out_branches)
    main_rel = rev_list_relationship(repo_root, "main", "origin/main") if _ref_exists(repo_root, "refs/heads/main") else UNKNOWN

    github_section: dict[str, Any]
    if prs is not None:
        open_prs = [p for p in prs if p.get("state") == "OPEN"]
        stale_prs = [p for p in open_prs if (_days_since(p.get("createdAt")) or 0) >= args.stale_pr_days]
        github_section = {
            "available": True,
            "error": None,
            "prs_opened_today": [_pr_summary(p) for p in prs if str(p.get("createdAt", "")).startswith(today_str)],
            "prs_merged_today": [_pr_summary(p) for p in prs if str(p.get("mergedAt") or "").startswith(today_str)],
            "prs_closed_unmerged_today": [
                _pr_summary(p) for p in prs
                if p.get("state") == "CLOSED" and str(p.get("closedAt") or "").startswith(today_str) and not p.get("mergedAt")
            ],
            "open_prs": [_pr_summary(p) for p in open_prs],
            "stale_open_prs_over_days": args.stale_pr_days,
            "stale_prs": [_pr_summary(p) for p in stale_prs],
        }
    else:
        github_section = {"available": False, "error": pr_err, "prs_opened_today": UNKNOWN, "prs_merged_today": UNKNOWN,
                           "prs_closed_unmerged_today": UNKNOWN, "open_prs": UNKNOWN, "stale_prs": UNKNOWN}

    branch_worktree_section = {
        "worktrees": worktrees,
        "dirty_worktrees": [{"path": wt.get("path"), "branch": wt.get("branch")} for wt in worktrees if _worktree_dirty(wt) is True],
        "branches_tracking_deleted_remotes": tracking["tracking_deleted_remote"],
        "local_only_branches": tracking["local_only"],
        "local_main_relationship_to_origin": main_rel,
        "stash_count": len(stashes),
    }

    evidence_preservation = {
        "closed_unmerged_branches_missing_archive_tag": unique_no_tag,
        "blocker": bool(unique_no_tag),
        "note": "never auto-tagged; see docs/BRANCH_ARCHIVE_INDEX.md for the tagging convention",
    }

    deployed_state = {
        "context": (
            "reuses ops.live_box_guard.live_box_drift_report as-is. Its EXPECTED_* pins and "
            "security-runtime checks are meant to run ON the deployed box; in a local dev/CI "
            "checkout an 'error'/'warn' status from missing pins or a dirty worktree is "
            "expected, not itself an incident — treat this section as informative unless it "
            "is actually being run on the box."
        ),
        **live_box_drift_report(repo_root=repo_root),
    }
    strategy_truth = strategy_source_of_truth(repo_root)
    trade_chain = trade_chain_integrity(
        journal_dir,
        since_date=since_date,
        broker_json=Path(args.broker_json) if args.broker_json else None,
        api_base=args.api_base,
    )

    if not args.dry_run:
        _daily_checkpoint_write(today_str)

    report = {
        "routine": "daily",
        "generated_at": _now_iso(),
        "read_only": True,
        "since_date": since_date,
        "github_repo_reconciliation": github_section,
        "branch_worktree_hygiene": branch_worktree_section,
        "evidence_preservation": evidence_preservation,
        "deployed_state": deployed_state,
        "strategy_source_of_truth": strategy_truth,
        "trade_chain_integrity": trade_chain,
    }
    _emit_daily(report, args)
    return 0


# ─────────────────────────── output ─────────────────────────────────────────

def _print_kv(d: Any, indent: int = 0) -> None:
    pad = "  " * indent
    if not isinstance(d, dict):
        print(f"{pad}{d}")
        return
    for k, v in d.items():
        if isinstance(v, dict):
            print(f"{pad}{k}:")
            _print_kv(v, indent + 1)
        elif isinstance(v, list):
            if not v:
                print(f"{pad}{k}: []")
            elif all(isinstance(item, (str, int, float, bool)) or item is None for item in v):
                print(f"{pad}{k}: {v}")
            else:
                print(f"{pad}{k}: ({len(v)} items)")
                for item in v[:20]:
                    if isinstance(item, dict):
                        print(f"{pad}  -")
                        _print_kv(item, indent + 2)
                    else:
                        print(f"{pad}  - {item}")
        else:
            print(f"{pad}{k}: {v}")


def _emit(report: dict[str, Any], args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, default=str))
        return
    _print_kv(report)


def _print_trade_chain_summary(tc: dict[str, Any]) -> None:
    totals = tc["totals"]
    if tc["pass"]:
        print("TRADE CHAIN: PASS")
        print(f"{totals['attempts']} attempts")
        print(f"{totals['fills']} fills")
        print(f"{totals['cancellations_no_fill']} no-fills")
        print(f"{totals['needs_broker_verification']} reconciler-touched (needs broker verification)")
        print(f"{totals['orphan_outcomes']} orphans")
        parity = tc["broker_parity"]
        parity_line = "PASS" if (not parity.get("available") or parity.get("parity_ok", True)) else "FAIL"
        print(f"broker/journal parity: {parity_line}" + (" (not checked — no broker snapshot given)" if not parity.get("available") else ""))
    else:
        print("TRADE CHAIN: FAIL — see detail below")
        _print_kv(tc)


def _emit_daily(report: dict[str, Any], args: argparse.Namespace) -> None:
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, default=str))
        return
    for key in ("github_repo_reconciliation", "branch_worktree_hygiene", "evidence_preservation", "deployed_state", "strategy_source_of_truth"):
        print(f"=== {key} ===")
        _print_kv(report[key])
        print()
    print("=== trade_chain_integrity ===")
    _print_trade_chain_summary(report["trade_chain_integrity"])


# ─────────────────────────── CLI ────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ops.project_check",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_session = sub.add_parser("session-start", help="Repo/branch/worktree/runtime snapshot at session start.")
    p_session.add_argument("--json", action="store_true")
    p_session.add_argument("--skip-github", action="store_true", help="Skip gh CLI PR lookup even if gh is available.")
    p_session.set_defaults(func=cmd_session_start)

    p_pre = sub.add_parser("precommit", help="Fail-closed drift check before commit/push. READ ONLY.")
    p_pre.add_argument("--json", action="store_true")
    p_pre.set_defaults(func=cmd_precommit)

    p_promo = sub.add_parser("promotion", help="Strategy promotion proof-gate audit against an existing evidence artifact.")
    p_promo.add_argument("--strategy", required=True)
    p_promo.add_argument("--evidence", help="Explicit path to an evidence JSON artifact (skips auto-discovery).")
    p_promo.add_argument("--json", action="store_true")
    p_promo.set_defaults(func=cmd_promotion)

    p_daily = sub.add_parser("daily", help="Daily reconciliation + trade-chain integrity pass.")
    p_daily.add_argument("--journal-dir", default=None)
    p_daily.add_argument("--since-date", default=None, help="YYYY-MM-DD. Defaults to the last daily checkpoint, else unscoped.")
    p_daily.add_argument("--broker-json", default=None, help="Path to a broker-account snapshot JSON for journal/broker parity.")
    p_daily.add_argument("--api-base", default=None, help="Base URL exposing /status/broker-account, used if --broker-json is absent.")
    p_daily.add_argument("--stale-pr-days", type=int, default=14)
    p_daily.add_argument("--skip-github", action="store_true")
    p_daily.add_argument("--dry-run", action="store_true", help="Do not advance the daily checkpoint file.")
    p_daily.add_argument("--json", action="store_true")
    p_daily.set_defaults(func=cmd_daily)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
