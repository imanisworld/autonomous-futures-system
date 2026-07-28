"""Read-only developer routines: session safety, promotion proof gate, and
daily reconciliation + trade-chain integrity.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy <name>
    python -m ops.project_check daily

All four subcommands are read-only against the repo and journal. None of
them commit, push, pull, fetch, reset, rebase, checkout/switch, cherry-pick,
delete branches/worktrees/stashes, or create/delete tags. The only file this
module ever writes is its own small state snapshot under ``logs/`` (already
gitignored) so `precommit` can compare against the state `session-start`
recorded — the same pattern `execution/live_preflight.py` uses for its own
state file.

Reuses existing machinery rather than duplicating it:
  - ``ops.live_box_guard.live_box_drift_report`` for the runtime/deployed
    posture snapshot (branch/commit, risk_rules hash, proof-critical runtime
    overrides including ENTRY_FILL_MODEL and entry-tolerance envs).
  - ``config.settings.load_config`` for the resolved entry fill model, entry
    tolerance ticks, and contract caps.
  - ``risk_rules.yaml``'s ``strategy_permission_gate`` for active
    paper-forward strategy lanes.
  - ``docs/strategy-rules/Strategy_Inventory.md`` and
    ``docs/BRANCH_ARCHIVE_INDEX.md`` as the strategy/evidence source of truth.
  - ``ops.fill_realism``, ``ops.journal_label_audit``,
    ``ops.strategy_intent_audit``, and ``ops.reconciler_outcome_audit`` for
    journal-derived fill/candidate/reconciler accounting (trade-chain
    integrity and the promotion gate's execution funnel).

Shell safety: every subprocess call passes an argv list (never shell=True),
so it behaves identically under bash or zsh and never depends on word
splitting of a shell variable.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import yaml

from config.settings import load_config
from ops.fill_realism import pair_resolved_attempts
from ops.journal_label_audit import build_audit as build_label_audit
from ops.live_box_guard import live_box_drift_report
from ops.proof_30_mnq import DEFAULT_JOURNAL_DIR
from ops.reconciler_outcome_audit import build_audit_report as build_reconciler_audit
from ops.strategy_intent_audit import build_audit as build_intent_audit

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_STATE_PATH = REPO_ROOT / "logs" / "project_check_session_state.json"
DAILY_CHECKPOINT_PATH = REPO_ROOT / "logs" / "project_check_daily_checkpoint.json"
STRATEGY_INVENTORY_PATH = REPO_ROOT / "docs" / "strategy-rules" / "Strategy_Inventory.md"
OPERATOR_OVERRIDES_PATH = REPO_ROOT / "docs" / "proof-operator-overrides.md"
SIGNAL_ENGINE_PATH = REPO_ROOT / "strategy" / "signal_engine.py"

UNKNOWN = "UNKNOWN"

# This module only ever invokes read-only git subcommands: rev-parse, status,
# worktree list, branch -vv/-r, stash list, tag --list, for-each-ref,
# rev-list, diff --name-only, merge-base --is-ancestor. It never calls
# commit/push/pull/fetch/reset/rebase/checkout/switch/cherry-pick/branch
# -d/worktree remove/stash drop/tag -d, and it never passes a shell string
# (every subprocess call takes an argv list, so bash/zsh word-splitting never
# applies).


# --------------------------------------------------------------------- git


def _run(args: list[str], cwd: Path, timeout: float = 10.0) -> str | None:
    try:
        result = subprocess.run(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _git(root: Path, *args: str, timeout: float = 10.0) -> str | None:
    out = _run(["git", *args], root, timeout=timeout)
    return out.strip() if out is not None else None


def _git_lines(root: Path, *args: str, timeout: float = 10.0) -> list[str] | None:
    out = _run(["git", *args], root, timeout=timeout)
    if out is None:
        return None
    return [line for line in out.splitlines() if line.strip()]


def _repo_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    root = _git(REPO_ROOT, "rev-parse", "--show-toplevel")
    return Path(root).resolve() if root else REPO_ROOT


def _current_branch(root: Path) -> str | None:
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        return None  # detached HEAD
    return branch


def _head_sha(root: Path) -> str | None:
    return _git(root, "rev-parse", "HEAD")


def _upstream(root: Path) -> str | None:
    return _git(root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")


def _ref_sha(root: Path, ref: str) -> str | None:
    return _git(root, "rev-parse", ref)


def _sync_relationship(root: Path, local_ref: str, remote_ref: str) -> dict[str, Any]:
    local = _ref_sha(root, local_ref)
    remote = _ref_sha(root, remote_ref)
    if local is None or remote is None:
        return {
            "status": UNKNOWN,
            "local_ref": local_ref, "remote_ref": remote_ref,
            "local_sha": local, "remote_sha": remote,
            "note": "one or both refs could not be resolved locally (no fetch is performed by this tool)",
        }
    if local == remote:
        status = "IN_SYNC"
        ahead = behind = 0
    else:
        counts = _git(root, "rev-list", "--left-right", "--count", f"{local_ref}...{remote_ref}")
        if counts is None:
            return {
                "status": UNKNOWN, "local_ref": local_ref, "remote_ref": remote_ref,
                "local_sha": local, "remote_sha": remote,
                "note": "rev-list failed",
            }
        ahead_s, behind_s = counts.split()
        ahead, behind = int(ahead_s), int(behind_s)
        status = "DIVERGED" if ahead and behind else "AHEAD" if ahead else "BEHIND"
    return {
        "status": status, "local_ref": local_ref, "remote_ref": remote_ref,
        "local_sha": local, "remote_sha": remote,
        "ahead": ahead, "behind": behind,
        "note": "computed from local refs only; run `git fetch` first if origin/main may be stale",
    }


def _worktrees(root: Path) -> list[dict[str, Any]] | None:
    out = _run(["git", "worktree", "list", "--porcelain"], root)
    if out is None:
        return None
    trees: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                trees.append(current)
                current = {}
            continue
        if line.startswith("worktree "):
            current["path"] = line.split(" ", 1)[1]
        elif line.startswith("HEAD "):
            current["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
        elif line == "bare":
            current["bare"] = True
        elif line == "detached":
            current["detached"] = True
        elif line.startswith("locked"):
            current["locked"] = True
    if current:
        trees.append(current)
    return trees


def _status_porcelain(root: Path) -> list[str] | None:
    out = _run(["git", "status", "--porcelain=v1"], root)
    if out is None:
        return None
    return out.splitlines()


def _classify_status(lines: list[str] | None) -> dict[str, list[str]]:
    staged: list[str] = []
    dirty_unstaged: list[str] = []
    untracked: list[str] = []
    for line in lines or []:
        if not line:
            continue
        x, y, path = line[0], line[1], line[3:]
        if x == "?" and y == "?":
            untracked.append(path)
            continue
        if x not in (" ", "?"):
            staged.append(path)
        if y not in (" ", "?"):
            dirty_unstaged.append(path)
    return {"staged": staged, "dirty_unstaged": dirty_unstaged, "untracked": untracked}


def _stash_list(root: Path) -> list[str] | None:
    return _git_lines(root, "stash", "list")


def _branches_tracking_gone(root: Path) -> list[str] | None:
    out = _run(["git", "branch", "-vv"], root)
    if out is None:
        return None
    gone = []
    for line in out.splitlines():
        if ": gone]" in line:
            name = line.lstrip("* ").split()[0]
            gone.append(name)
    return gone


def _local_only_branches(root: Path) -> list[str] | None:
    out = _run(
        ["git", "for-each-ref", "--format=%(refname:short)\t%(upstream:short)", "refs/heads/"],
        root,
    )
    if out is None:
        return None
    result = []
    for line in out.splitlines():
        parts = line.split("\t", 1)
        name = parts[0]
        upstream = parts[1] if len(parts) > 1 else ""
        if not upstream.strip():
            result.append(name)
    return result


def _archive_tags(root: Path) -> list[str] | None:
    return _git_lines(root, "tag", "--list", "archive/*")


def _remote_branches(root: Path) -> list[str] | None:
    out = _git_lines(root, "branch", "-r")
    if out is None:
        return None
    return [
        b.strip().split(" ->")[0].strip()
        for b in out
        if "HEAD ->" not in b
    ]


def _gh_pr_list(root: Path, extra_args: Iterable[str] = ()) -> list[dict[str, Any]] | None:
    """Best-effort, read-only PR listing via the gh CLI. Returns None (not an
    empty list) when gh is unavailable/unauthenticated so callers can tell
    "no PRs" apart from "couldn't check"."""
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--json",
             "number,title,headRefName,state,isDraft,updatedAt,url", *extra_args],
            cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


# ------------------------------------------------------------- state file


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# ------------------------------------------------------- runtime snapshot


def _strategy_permission_gate(root: Path) -> dict[str, Any]:
    try:
        rules = yaml.safe_load((root / "risk_rules.yaml").read_text(encoding="utf-8")) or {}
    except OSError:
        return {"ok": False, "note": "risk_rules.yaml not readable"}
    gate = rules.get("strategy_permission_gate") or {}
    statuses = gate.get("strategy_status") or {}
    paper_eligible = sorted(k for k, v in statuses.items() if v == "PAPER_ELIGIBLE")
    shadow_only = sorted(k for k, v in statuses.items() if v == "SHADOW_ONLY")
    return {
        "enabled": gate.get("enabled"),
        "default_status": gate.get("default_status"),
        "paper_eligible_lanes": paper_eligible,
        "shadow_only_lanes": shadow_only,
        "strategy_status": statuses,
    }


def _runtime_config_snapshot(root: Path) -> dict[str, Any]:
    try:
        config = load_config(str(root / "risk_rules.yaml"))
    except Exception as exc:  # noqa: BLE001 - report, never raise, in a read-only tool
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "ok": True,
        "entry_fill_model": config.entry_fill_model,
        "entry_tolerance_ticks_by_root": config.entry_tolerance_ticks_by_root,
        "max_contracts_hard_cap": config.max_contracts_hard_cap,
        "max_contracts_per_instrument": config.max_contracts_per_instrument,
    }


def _evidence_epoch_dates(strategy_names: Iterable[str]) -> dict[str, str]:
    """Best-effort: most recent date cited in each strategy's Strategy_Inventory
    profile section (e.g. "(2026-07-26 canonical evidence study)"). This is a
    convenience read of existing prose, not a formal tracked concept in the
    repo — treat UNKNOWN/absent dates as exactly that, not as "no evidence"."""
    try:
        text = STRATEGY_INVENTORY_PATH.read_text(encoding="utf-8")
    except OSError:
        return {}
    profiles = re.split(r"\n### ", text)[1:]
    out: dict[str, str] = {}
    for block in profiles:
        header, _, body = block.partition("\n")
        norm_header = _normalize_name(header)
        for name in strategy_names:
            if _normalize_name(name) and _normalize_name(name) in norm_header:
                dates = re.findall(r"\d{4}-\d{2}-\d{2}", body)
                if dates:
                    out[name] = max(dates)
    return out


def build_runtime_snapshot(root: Path) -> dict[str, Any]:
    drift = live_box_drift_report(repo_root=root)
    gate = _strategy_permission_gate(root)
    config_snapshot = _runtime_config_snapshot(root)
    epochs = _evidence_epoch_dates(gate.get("paper_eligible_lanes", []))
    return {
        "live_box_drift": drift,
        "strategy_permission_gate": gate,
        "resolved_config": config_snapshot,
        "evidence_epoch_by_strategy": epochs or UNKNOWN,
        "note": (
            "deployed-release identity comes from live_box_drift_report's "
            "EXPECTED_LIVE_BRANCH/EXPECTED_LIVE_COMMIT env pins; unset pins "
            "report as missing, never guessed."
        ),
    }


# ------------------------------------------------------------ session start


def build_session_start_report(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = _repo_root(repo_root)
    branch = _current_branch(root)
    head = _head_sha(root)
    upstream = _upstream(root)
    origin_main = _sync_relationship(root, "main", "origin/main")
    status_lines = _status_porcelain(root)
    classified = _classify_status(status_lines)
    worktrees = _worktrees(root)
    gone = _branches_tracking_gone(root)
    local_only = _local_only_branches(root)
    archive_tags = _archive_tags(root)
    stashes = _stash_list(root)
    prs = _gh_pr_list(root)
    remote_branches = _remote_branches(root)

    report = {
        "generated_at": None,  # filled by caller with a real timestamp (see main())
        "repo_root": str(root),
        "current_branch": branch or UNKNOWN,
        "head_sha": head or UNKNOWN,
        "origin_main": origin_main,
        "upstream": upstream or UNKNOWN,
        "current_worktree": str(root),
        "all_worktrees": worktrees if worktrees is not None else UNKNOWN,
        "dirty_tracked_files": classified["dirty_unstaged"] if status_lines is not None else UNKNOWN,
        "staged_files": classified["staged"] if status_lines is not None else UNKNOWN,
        "untracked_files": classified["untracked"] if status_lines is not None else UNKNOWN,
        "branches_tracking_deleted_remotes": gone if gone is not None else UNKNOWN,
        "local_only_branches": local_only if local_only is not None else UNKNOWN,
        "remote_branches": remote_branches if remote_branches is not None else UNKNOWN,
        "open_prs": prs if prs is not None else f"{UNKNOWN} (gh CLI unavailable or unauthenticated)",
        "archive_tags": archive_tags if archive_tags is not None else UNKNOWN,
        "stash_count": len(stashes) if stashes is not None else UNKNOWN,
        "stash_labels": stashes if stashes is not None else UNKNOWN,
        "runtime_snapshot": build_runtime_snapshot(root),
    }

    branch_after = _current_branch(root)
    report["branch_changed_during_check"] = (branch_after != branch)
    if report["branch_changed_during_check"]:
        report["fail_closed"] = True
        report["fail_reason"] = (
            f"checked-out branch changed mid-check ({branch!r} -> {branch_after!r}); "
            "treat repo state as UNSAFE TO WORK until re-run"
        )
    return report


def _persist_session_state(report: dict[str, Any], state_path: Path) -> None:
    _write_json_atomic(state_path, {
        "recorded_at_head_sha": report["head_sha"],
        "branch": report["current_branch"],
        "worktree": report["current_worktree"],
        "repo_root": report["repo_root"],
    })


# --------------------------------------------------------------- precommit


def build_precommit_report(
    repo_root: str | Path | None = None,
    state_path: str | Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    state_file = Path(state_path) if state_path else SESSION_STATE_PATH
    prior = _read_json(state_file)

    branch = _current_branch(root)
    head = _head_sha(root)
    upstream = _upstream(root)
    status_lines = _status_porcelain(root)
    classified = _classify_status(status_lines)
    worktrees = _worktrees(root)
    sync = _sync_relationship(root, "HEAD", upstream) if upstream else {
        "status": UNKNOWN, "note": "no upstream configured"
    }

    fail_reasons: list[str] = []
    if prior is None:
        fail_reasons.append(
            f"no session-start snapshot found at {state_file} — run "
            "`python -m ops.project_check session-start` first; repository "
            "state cannot be verified against a known-good baseline"
        )
    else:
        if prior.get("repo_root") != str(root):
            fail_reasons.append(
                f"repo root differs from session-start ({prior.get('repo_root')} -> {root})"
            )
        if prior.get("branch") and branch != prior.get("branch"):
            fail_reasons.append(
                f"branch differs from session-start ({prior.get('branch')!r} -> {branch!r})"
            )
        if prior.get("worktree") and str(root) != prior.get("worktree"):
            fail_reasons.append(
                f"worktree differs from session-start ({prior.get('worktree')} -> {root})"
            )

    if branch is None:
        fail_reasons.append("HEAD is detached — ambiguous branch state")
    if worktrees is None:
        fail_reasons.append("could not enumerate worktrees (ambiguous repository state)")
    else:
        owners = [
            wt for wt in worktrees
            if wt.get("branch", "").endswith(f"/{branch}") and Path(wt.get("path", "")) != root
        ]
        if branch and owners:
            fail_reasons.append(
                f"branch {branch!r} appears checked out in another worktree: "
                f"{[o.get('path') for o in owners]}"
            )

    branch_after = _current_branch(root)
    if branch_after != branch:
        fail_reasons.append(f"branch moved during the check ({branch!r} -> {branch_after!r})")

    ok = not fail_reasons
    return {
        "read_only": True,
        "repo_root": str(root),
        "current_branch": branch or UNKNOWN,
        "head_sha": head or UNKNOWN,
        "session_start_branch": (prior or {}).get("branch", UNKNOWN),
        "session_start_worktree": (prior or {}).get("worktree", UNKNOWN),
        "current_worktree": str(root),
        "upstream": upstream or UNKNOWN,
        "ahead_behind": sync,
        "changed_files": classified["dirty_unstaged"] + classified["staged"] if status_lines is not None else UNKNOWN,
        "staged_files": classified["staged"] if status_lines is not None else UNKNOWN,
        "untracked_files": classified["untracked"] if status_lines is not None else UNKNOWN,
        "ok": ok,
        "status": "PASS" if ok else "FAIL_CLOSED",
        "fail_reasons": fail_reasons,
    }


# ------------------------------------------------------- strategy helpers


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    name = re.sub(r"\([^)]*\)", "", name)  # drop "(MES)"/"(MNQ)" parentheticals
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _parse_strategy_inventory_table(text: str) -> list[dict[str, str]]:
    rows = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("| Strategy |"):
            in_table = True
            continue
        if in_table:
            if not line.startswith("|"):
                break
            if set(line.replace("|", "").strip()) <= {"-", " "}:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 9:
                continue
            rows.append({
                "strategy": cells[0],
                "verdict": re.sub(r"\*\*", "", cells[8]).strip(),
            })
    return rows


def _match_inventory_row(strategy: str, rows: list[dict[str, str]]) -> dict[str, str] | None:
    target = _normalize_name(strategy)
    if not target:
        return None
    candidates = [r for r in rows if target in _normalize_name(r["strategy"]) or _normalize_name(r["strategy"]) in target]
    if not candidates:
        return None
    candidates.sort(key=lambda r: -len(_normalize_name(r["strategy"])))
    return candidates[0]


def _strategy_wired_in_signal_engine(strategy: str) -> dict[str, Any]:
    try:
        text = SIGNAL_ENGINE_PATH.read_text(encoding="utf-8")
    except OSError:
        return {"checked": False, "note": f"{SIGNAL_ENGINE_PATH} not readable"}
    hits = [ln for ln in text.splitlines() if strategy in ln]
    return {
        "checked": True,
        "wired": bool(hits),
        "matching_lines": len(hits),
        "note": (
            "wired into strategy/signal_engine.py (candidate real executable path)"
            if hits else
            "NOT referenced in strategy/signal_engine.py — if evidence for this "
            "strategy lives only under research/, it has never gone through the "
            "live ReplayEngine/DecisionEngine/RiskEngine/PaperBroker path"
        ),
    }


def _entry_strategies(entry: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    setup = entry.get("setup")
    if isinstance(setup, dict) and setup.get("strategy"):
        names.add(setup["strategy"])
    for cand in entry.get("shadow_candidates") or []:
        if isinstance(cand, dict) and cand.get("strategy"):
            names.add(cand["strategy"])
    for cand in entry.get("candidate_audit") or []:
        if isinstance(cand, dict) and cand.get("strategy"):
            names.add(cand["strategy"])
    return names


def _read_all_journal_entries(journal_dir: Path) -> list[dict[str, Any]]:
    entries = []
    for path in sorted(journal_dir.glob("journal_*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except (TypeError, ValueError):
                continue
            entry["_file"] = str(path)
            entries.append(entry)
    entries.sort(key=lambda e: e.get("ts") or "")
    return entries


def _pnl_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    filled = [r for r in rows if r.get("result") in {"WIN", "LOSS", "BREAKEVEN"}]
    wins = [r for r in filled if r.get("result") == "WIN"]
    losses = [r for r in filled if r.get("result") == "LOSS"]
    # pair_resolved_attempts doesn't carry pnl_dollars today; report what it
    # gives us (counts/win-rate) and flag pnl as unavailable rather than
    # inventing it from another source.
    return {
        "filled": len(filled),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(filled) - len(wins) - len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(filled), 1) if filled else None,
        "note": "pnl_dollars not exposed by ops.fill_realism.pair_resolved_attempts; see journal OUTCOME rows directly for $ figures",
    }


# ----------------------------------------------------------- promotion gate


def build_promotion_report(
    strategy: str,
    *,
    repo_root: str | Path | None = None,
    journal_dir: str | Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    jdir = Path(journal_dir) if journal_dir else DEFAULT_JOURNAL_DIR

    # -- IDENTITY / PARITY
    inventory_rows = []
    try:
        inventory_rows = _parse_strategy_inventory_table(STRATEGY_INVENTORY_PATH.read_text(encoding="utf-8"))
    except OSError:
        pass
    inventory_row = _match_inventory_row(strategy, inventory_rows)
    wiring = _strategy_wired_in_signal_engine(strategy)
    gate = _strategy_permission_gate(root)
    permission_status = gate.get("strategy_status", {}).get(strategy, gate.get("default_status", UNKNOWN))

    identity_parity = {
        "raw_candidate_count": UNKNOWN,
        "strategy_inventory_row": inventory_row or UNKNOWN,
        "wired_into_live_signal_engine": wiring,
        "risk_rules_permission_status": permission_status,
        "note": (
            "This gate does not re-run detectors or replay — it reads what the "
            "real system has already produced (journal entries from the live "
            "ReplayEngine/DecisionEngine/RiskEngine/PaperBroker path) rather than "
            "duplicating strategy/risk logic. Candidate identity/direction/entry/"
            "stop/target parity against the standalone research module must be "
            "checked by a human diffing the two, not inferred here."
        ),
    }

    # -- GATE ATTRITION (from journal candidate_audit / failed_gates, all real
    #    decisions the live engine actually made for this strategy's candidates)
    entries = _read_all_journal_entries(jdir)
    strategy_entries = [e for e in entries if strategy in _entry_strategies(e)]
    gate_counter: Counter = Counter()
    for e in strategy_entries:
        for cand in (e.get("candidate_audit") or []):
            if isinstance(cand, dict) and cand.get("strategy") == strategy:
                for g in (cand.get("failed_gates") or []):
                    gate_counter[g] += 1
        if e.get("decision") == "RISK_REJECTED" and (e.get("setup") or {}).get("strategy") == strategy:
            gate_counter[f"RISK_REJECTED: {e.get('reason')}"] += 1
    gate_attrition = {
        "candidates_seen_in_journal": len(strategy_entries),
        "failed_gate_counts": dict(gate_counter.most_common()),
        "note": "counts are journal-observed gate failures, not a re-derivation of risk_engine.py's logic",
    }

    # -- EXECUTION funnel (real path: journal TRADE decisions this strategy
    #    reached RiskEngine/PaperBroker for)
    trade_rows, unresolved = pair_resolved_attempts(jdir.glob("journal_*.jsonl") if jdir.exists() else [])
    trade_rows = [r for r in trade_rows if r.get("strategy") == strategy]
    fills = [r for r in trade_rows if not r.get("no_fill") and r.get("result") in {"WIN", "LOSS", "BREAKEVEN"}]
    no_fills = [r for r in trade_rows if r.get("no_fill")]
    other_cancelled = [r for r in trade_rows if r.get("result") == "CANCELLED" and not r.get("no_fill")]
    attempts = len(trade_rows)
    execution = {
        "candidates_reaching_risk_engine": UNKNOWN if not wiring.get("wired") else "see gate_attrition.candidates_seen_in_journal",
        "attempts": attempts,
        "fills": len(fills),
        "no_fills": len(no_fills),
        "other_cancellations": len(other_cancelled),
        "accounting_identity_attempts_eq_fills_plus_cancellations": (
            attempts == len(fills) + len(no_fills) + len(other_cancelled)
        ),
        "zero_executable_fills": len(fills) == 0,
        "note": (
            "derived from ops.fill_realism.pair_resolved_attempts over the full "
            "journal directory; 'legitimately open' positions are TRADE rows "
            "without a paired OUTCOME yet and are not distinguishable here from "
            "a pairing gap across a day boundary — see limitations."
        ),
    }

    performance = {
        **_pnl_stats(trade_rows),
        "instrument_split": dict(Counter(r.get("instrument") for r in trade_rows)),
        "direction_split": dict(Counter(r.get("direction") for r in trade_rows)),
    }

    # -- CLASSIFICATION (a cross-check against the existing taxonomy, not a
    #    new independent verdict — /futures-strategy-audit and
    #    Strategy_Inventory.md remain the source of truth)
    inventory_verdict = (inventory_row or {}).get("verdict")
    if execution["zero_executable_fills"] and wiring.get("wired"):
        suggested = "WAIT"
        reason = "wired into the live path but zero resolved fills in the journal — no paper-forward evidence exists yet"
    elif not wiring.get("wired"):
        suggested = "PROMISING BUT UNPROVEN" if inventory_verdict not in {"BROKEN", "RETIRE"} else inventory_verdict
        reason = "not wired into strategy/signal_engine.py — all existing evidence is standalone research, per the Miyagi/60M 3-2-2 precedent this is not proof"
    elif inventory_verdict:
        suggested = inventory_verdict
        reason = "deferring to docs/strategy-rules/Strategy_Inventory.md's current verdict; this gate only checks parity/funnel/accounting, not edge quality"
    else:
        suggested = UNKNOWN
        reason = "no Strategy_Inventory.md row matched — cannot cross-check verdict"

    classification = {
        "research_result": inventory_verdict or UNKNOWN,
        "runtime_parity": "REAL_PATH" if wiring.get("wired") else "STANDALONE_RESEARCH_ONLY",
        "paper_forward_evidence": (
            "NONE" if execution["zero_executable_fills"] else f"{execution['fills']} resolved fills"
        ),
        "suggested_classification": suggested,
        "reason": reason,
        "rules": [
            "no rescue/tuning variant is proposed here",
            "no automatic runtime change, merge, deployment, or config edit is performed",
            "legitimate account risk controls (RiskEngine gates) are reported, never bypassed",
        ],
    }

    return {
        "read_only": True,
        "strategy": strategy,
        "identity_parity": identity_parity,
        "gate_attrition": gate_attrition,
        "execution": execution,
        "performance": performance,
        "execution_context": {
            "entry_fill_model": _runtime_config_snapshot(root).get("entry_fill_model", UNKNOWN),
            "entry_tolerance_ticks_by_root": _runtime_config_snapshot(root).get("entry_tolerance_ticks_by_root", UNKNOWN),
            "max_contracts_per_instrument": _runtime_config_snapshot(root).get("max_contracts_per_instrument", UNKNOWN),
            "risk_rules_permission_status": permission_status,
        },
        "classification": classification,
        "limitations": [
            "does not re-run ReplayEngine/DecisionEngine/RiskEngine/PaperBroker; reads what the real system already journaled",
            "pnl_dollars is not surfaced (upstream helper doesn't expose it); read journal OUTCOME rows directly for $ detail",
            "'legitimately open' vs 'pairing gap across a day boundary' cannot be distinguished from this data alone",
            f"journal_dir={jdir} — if this isn't a mirror of the box's logs/, execution/performance numbers are incomplete, not zero",
        ],
    }


# --------------------------------------------------------------- daily


def _evidence_preservation_check(root: Path) -> dict[str, Any]:
    remotes = _remote_branches(root) or []
    tags = _archive_tags(root) or []
    tag_shas = {}
    for tag in tags:
        sha = _git(root, "rev-parse", tag)
        if sha:
            tag_shas[sha] = tag

    blockers = []
    reviewed = []
    for rb in remotes:
        name = rb.split("/", 1)[-1] if "/" in rb else rb
        if name in {"main", "HEAD"}:
            continue
        # `merge-base --is-ancestor` exits 0 (merged) or 1 (not an ancestor);
        # _run returns None on any non-zero exit, so this is the merged check.
        merged_ok = _run(["git", "merge-base", "--is-ancestor", rb, "origin/main"], root) is not None
        tip = _ref_sha(root, rb)
        diff = _git_lines(root, "diff", "--name-only", f"origin/main...{rb}") or []
        has_unique_files = bool(diff)
        archive_tag = tag_shas.get(tip) if tip else None
        row = {
            "branch": rb,
            "tip_sha": tip or UNKNOWN,
            "merged_into_origin_main": merged_ok,
            "unique_files_vs_main": len(diff),
            "archive_tag_at_tip": archive_tag,
        }
        if merged_ok:
            continue
        if has_unique_files and not archive_tag:
            row["flag"] = "BLOCKER"
            blockers.append(row)
        else:
            row["flag"] = "OK" if archive_tag else "NO_UNIQUE_CONTENT"
            reviewed.append(row)
    return {
        "archive_tags_found": tags,
        "unmerged_remote_branches_checked": len(blockers) + len(reviewed),
        "blockers": blockers,
        "reviewed_ok": reviewed,
        "note": (
            "a BLOCKER means a remote branch is not an ancestor of origin/main, "
            "carries a unique file diff, and has no archive/* tag at its exact "
            "tip commit — never auto-tagged or auto-deleted by this tool"
        ),
    }


def _strategy_source_of_truth(root: Path) -> dict[str, Any]:
    try:
        inventory_rows = _parse_strategy_inventory_table(STRATEGY_INVENTORY_PATH.read_text(encoding="utf-8"))
    except OSError:
        inventory_rows = []
    gate = _strategy_permission_gate(root)
    statuses = gate.get("strategy_status", {})

    flags = []
    matched = []
    concerning_verdicts = {"BROKEN", "RETIRE", "RESEARCH ONLY", "WAIT"}
    for config_key, lane in statuses.items():
        row = _match_inventory_row(config_key, inventory_rows)
        if row is None:
            continue
        matched.append({"config_key": config_key, "lane": lane, "inventory_row": row["strategy"], "verdict": row["verdict"]})
        verdict_upper = row["verdict"].upper()
        if lane == "PAPER_ELIGIBLE" and any(v in verdict_upper for v in concerning_verdicts):
            flags.append({
                "config_key": config_key,
                "lane": lane,
                "inventory_verdict": row["verdict"],
                "issue": "strategy is PAPER_ELIGIBLE in risk_rules.yaml but Strategy_Inventory.md verdict is not a validated/promising status",
            })
    matched_keys = {m["config_key"] for m in matched}
    unmatched_config_keys = sorted(set(statuses) - matched_keys)
    return {
        "matched": matched,
        "unmatched_config_keys": unmatched_config_keys,
        "flags": flags,
        "note": (
            "name matching between risk_rules.yaml keys and Strategy_Inventory.md "
            "row titles is best-effort normalization (case/punctuation/instrument-"
            "suffix insensitive substring match); unmatched keys need manual "
            "cross-check, not an assumed pass"
        ),
    }


def _trade_chain_overlaps(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    open_by_instrument: dict[str, str] = {}
    overlaps = []
    for e in entries:
        if e.get("decision") == "TRADE" and isinstance(e.get("setup"), dict):
            inst = e.get("instrument")
            if inst in open_by_instrument:
                overlaps.append({
                    "instrument": inst,
                    "first_ts": open_by_instrument[inst],
                    "second_ts": e.get("ts"),
                })
            else:
                open_by_instrument[inst] = e.get("ts")
        elif e.get("type") == "OUTCOME":
            open_by_instrument.pop(e.get("instrument"), None)
    return overlaps, list(open_by_instrument.items())


def _trade_chain_integrity(journal_dir: Path, since_date: str | None) -> dict[str, Any]:
    entries = _read_all_journal_entries(journal_dir)
    if since_date:
        entries = [e for e in entries if (e.get("ts") or "") >= since_date]

    paths = sorted(journal_dir.glob("journal_*.jsonl"))
    if since_date:
        paths = [p for p in paths if p.stem.removeprefix("journal_") >= since_date]

    trade_rows, unresolved = pair_resolved_attempts(paths)
    fills = [r for r in trade_rows if not r.get("no_fill") and r.get("result") in {"WIN", "LOSS", "BREAKEVEN"}]
    no_fills = [r for r in trade_rows if r.get("no_fill")]
    other_cancelled = [r for r in trade_rows if r.get("result") == "CANCELLED" and not r.get("no_fill")]
    overlaps, still_open = _trade_chain_overlaps(entries)

    attempts = len(trade_rows)
    label_audit = build_label_audit(journal_dir=journal_dir)
    intent_audit = build_intent_audit(journal_dir=journal_dir)
    overrides_doc = OPERATOR_OVERRIDES_PATH if OPERATOR_OVERRIDES_PATH.exists() else None
    reconciler_audit = build_reconciler_audit(
        journal_dir=journal_dir, overrides_doc=overrides_doc, from_date=since_date,
    )
    duplicate_identity_count = len(overlaps)

    passed = (
        duplicate_identity_count == 0
        and label_audit["summary"]["issues_by_severity"].get("error", 0) == 0
        and intent_audit["summary"]["rows_missing_candidate_audit"] == 0
        and reconciler_audit["summary"]["unaudited"] == 0
    )

    return {
        "read_only": True,
        "since_date": since_date or UNKNOWN,
        "attempts": attempts,
        "fills": len(fills),
        "no_fills": len(no_fills),
        "other_cancellations": len(other_cancelled),
        "resolved": len(fills),
        "legitimate_opens_or_pairing_gaps": unresolved,
        "orphans": 0,  # not independently derivable without broker state; see limitations
        "stale_orders": UNKNOWN,
        "duplicate_identities": overlaps,
        "still_open_at_window_end": still_open,
        "broker_journal_parity": (
            f"{UNKNOWN} (requires the running service's own broker session; "
            "out of scope for an offline read-only script). Reconciler-touched "
            f"outcomes in window: {reconciler_audit['summary']['total_touched']} "
            f"({reconciler_audit['summary']['classified']} classified via operator "
            f"override, {reconciler_audit['summary']['unaudited']} unaudited)."
        ),
        "accounting_identity_attempts_eq_fills_plus_cancellations": (
            attempts == len(fills) + len(no_fills) + len(other_cancelled)
        ),
        "label_audit_issue_count": label_audit["summary"]["issue_count"],
        "label_audit_issues_by_severity": label_audit["summary"]["issues_by_severity"],
        "signal_decision_coverage": {
            "decision_rows": intent_audit["summary"]["decision_rows"],
            "rows_missing_candidate_audit": intent_audit["summary"]["rows_missing_candidate_audit"],
            "note": "every TRADE/NO_TRADE/RISK_REJECTED row should carry a candidate_audit trail; missing rows lack attribution for why a candidate was or wasn't taken",
        },
        "reconciler_touched": {
            "total": reconciler_audit["summary"]["total_touched"],
            "classified": reconciler_audit["summary"]["classified"],
            "unaudited": reconciler_audit["summary"]["unaudited"],
        },
        "status": "PASS" if passed else "REVIEW",
        "limitations": [
            "orphans/stale-orders detection would require live broker-state cross-reference; not attempted offline",
            "'legitimate_opens_or_pairing_gaps' cannot distinguish a real still-open position from a pairing gap across a day boundary",
        ],
    }


def build_daily_report(
    repo_root: str | Path | None = None,
    journal_dir: str | Path | None = None,
    checkpoint_path: str | Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    jdir = Path(journal_dir) if journal_dir else DEFAULT_JOURNAL_DIR
    checkpoint_file = Path(checkpoint_path) if checkpoint_path else DAILY_CHECKPOINT_PATH
    prior_checkpoint = _read_json(checkpoint_file) or {}
    since_date = prior_checkpoint.get("last_checkpoint_date")

    prs = _gh_pr_list(root)
    today = date.today().isoformat()
    prs_today = [p for p in prs if (p.get("updatedAt") or "")[:10] == today] if prs else UNKNOWN

    github_section = {
        "open_prs": prs if prs is not None else f"{UNKNOWN} (gh CLI unavailable or unauthenticated)",
        "prs_touched_today": prs_today,
        "note": "gh CLI does not distinguish opened/merged/closed-today without extra calls; open_prs plus updatedAt is the read-only approximation",
    }

    hygiene_section = {
        "worktrees": _worktrees(root) or UNKNOWN,
        "stash_count": len(_stash_list(root) or []),
        "branches_tracking_deleted_remotes": _branches_tracking_gone(root) or [],
        "local_only_branches": _local_only_branches(root) or [],
        "local_main_vs_origin_main": _sync_relationship(root, "main", "origin/main"),
        "remote_branches": _remote_branches(root) or UNKNOWN,
    }

    evidence_preservation = _evidence_preservation_check(root)
    deployed_state = build_runtime_snapshot(root)
    strategy_source_of_truth = _strategy_source_of_truth(root)
    trade_chain = _trade_chain_integrity(jdir, since_date)

    report = {
        "generated_for_date": today,
        "since_checkpoint": since_date or UNKNOWN,
        "github": github_section,
        "branches_and_worktrees": hygiene_section,
        "evidence_preservation": evidence_preservation,
        "deployed_state": deployed_state,
        "strategy_source_of_truth": strategy_source_of_truth,
        "trade_chain_integrity": trade_chain,
    }
    return report


def _persist_daily_checkpoint(checkpoint_path: Path, for_date: str) -> None:
    _write_json_atomic(checkpoint_path, {"last_checkpoint_date": for_date})


# --------------------------------------------------------------- formatting


def _fmt_trade_chain(tc: dict[str, Any]) -> str:
    if tc["status"] == "PASS":
        return (
            f"TRADE CHAIN: PASS\n"
            f"{tc['attempts']} attempts\n"
            f"{tc['fills']} fills\n"
            f"{tc['no_fills']} no-fills\n"
            f"{tc['resolved']} resolved\n"
            f"{tc['legitimate_opens_or_pairing_gaps']} legitimate opens\n"
            f"0 orphans\n"
            f"0 duplicate identities\n"
            f"0 journal label issues, 0 missing candidate_audit rows, "
            f"{tc['reconciler_touched']['total']} reconciler-touched (all classified)\n"
            f"broker/journal parity: UNKNOWN (requires the running service's own broker session)"
        )
    lines = [f"TRADE CHAIN: REVIEW ({tc['attempts']} attempts, {tc['fills']} fills)"]
    if tc["duplicate_identities"]:
        lines.append(f"- duplicate/overlapping order identities: {tc['duplicate_identities']}")
    if tc["label_audit_issue_count"]:
        lines.append(f"- journal label audit issues: {tc['label_audit_issues_by_severity']}")
    if tc["signal_decision_coverage"]["rows_missing_candidate_audit"]:
        lines.append(
            f"- rows missing candidate_audit: {tc['signal_decision_coverage']['rows_missing_candidate_audit']} "
            f"of {tc['signal_decision_coverage']['decision_rows']} decision rows"
        )
    if tc["reconciler_touched"]["unaudited"]:
        lines.append(
            f"- reconciler-touched outcomes with no operator ruling: {tc['reconciler_touched']['unaudited']} "
            f"(of {tc['reconciler_touched']['total']} total)"
        )
    if not tc["accounting_identity_attempts_eq_fills_plus_cancellations"]:
        lines.append("- accounting identity attempts = fills + cancellations FAILED")
    return "\n".join(lines)


def format_session_start(report: dict[str, Any]) -> str:
    lines = [
        "SESSION SAFETY + RUNTIME SNAPSHOT",
        f"repo_root: {report['repo_root']}",
        f"current_branch: {report['current_branch']}  head: {report['head_sha']}",
        f"upstream: {report['upstream']}",
        f"local main vs origin/main: {report['origin_main']['status']} ({report['origin_main'].get('note', '')})",
        f"dirty tracked files: {report['dirty_tracked_files']}",
        f"staged files: {report['staged_files']}",
        f"untracked files: {report['untracked_files']}",
        f"stash: {report['stash_count']} ({report['stash_labels']})",
        f"local-only branches: {report['local_only_branches']}",
        f"branches tracking deleted remotes: {report['branches_tracking_deleted_remotes']}",
        f"open PRs: {report['open_prs']}",
        f"archive tags: {report['archive_tags']}",
        "",
        "RUNTIME SNAPSHOT",
        f"live_box_drift status: {report['runtime_snapshot']['live_box_drift']['status']}",
        f"live_box_drift summary: {report['runtime_snapshot']['live_box_drift']['summary']}",
        f"paper-eligible lanes: {report['runtime_snapshot']['strategy_permission_gate']['paper_eligible_lanes']}",
        f"shadow-only lanes: {report['runtime_snapshot']['strategy_permission_gate']['shadow_only_lanes']}",
        f"entry_fill_model: {report['runtime_snapshot']['resolved_config'].get('entry_fill_model')}",
        f"entry_tolerance_ticks_by_root: {report['runtime_snapshot']['resolved_config'].get('entry_tolerance_ticks_by_root')}",
        f"max_contracts_hard_cap: {report['runtime_snapshot']['resolved_config'].get('max_contracts_hard_cap')}",
        f"max_contracts_per_instrument: {report['runtime_snapshot']['resolved_config'].get('max_contracts_per_instrument')}",
        f"evidence_epoch_by_strategy: {report['runtime_snapshot']['evidence_epoch_by_strategy']}",
    ]
    if report.get("branch_changed_during_check"):
        lines.append(f"\n*** {report['fail_reason']} ***")
    return "\n".join(lines)


def format_precommit(report: dict[str, Any]) -> str:
    lines = [
        f"PRECOMMIT: {report['status']}",
        f"repo_root: {report['repo_root']}",
        f"current_branch: {report['current_branch']} (session-start: {report['session_start_branch']})",
        f"current_worktree: {report['current_worktree']} (session-start: {report['session_start_worktree']})",
        f"upstream: {report['upstream']}  ahead/behind: {report['ahead_behind']}",
        f"staged: {report['staged_files']}",
        f"changed: {report['changed_files']}",
        f"untracked: {report['untracked_files']}",
    ]
    if report["fail_reasons"]:
        lines.append("FAIL_CLOSED reasons:")
        lines.extend(f"  - {r}" for r in report["fail_reasons"])
    return "\n".join(lines)


def format_promotion(report: dict[str, Any]) -> str:
    c = report["classification"]
    lines = [
        f"STRATEGY PROMOTION PROOF GATE: {report['strategy']}",
        "",
        "IDENTITY / PARITY",
        f"  strategy_inventory_row: {report['identity_parity']['strategy_inventory_row']}",
        f"  wired_into_live_signal_engine: {report['identity_parity']['wired_into_live_signal_engine']}",
        f"  risk_rules_permission_status: {report['identity_parity']['risk_rules_permission_status']}",
        "",
        "GATE ATTRITION",
        f"  candidates_seen_in_journal: {report['gate_attrition']['candidates_seen_in_journal']}",
        f"  failed_gate_counts: {report['gate_attrition']['failed_gate_counts']}",
        "",
        "EXECUTION",
        f"  attempts={report['execution']['attempts']} fills={report['execution']['fills']} "
        f"no_fills={report['execution']['no_fills']} other_cancellations={report['execution']['other_cancellations']}",
        f"  accounting identity holds: {report['execution']['accounting_identity_attempts_eq_fills_plus_cancellations']}",
        f"  zero_executable_fills: {report['execution']['zero_executable_fills']}",
        "",
        "PERFORMANCE",
        f"  filled={report['performance']['filled']} wins={report['performance']['wins']} "
        f"losses={report['performance']['losses']} win_rate_pct={report['performance']['win_rate_pct']}",
        f"  instrument_split={report['performance']['instrument_split']}",
        f"  direction_split={report['performance']['direction_split']}",
        "",
        "EXECUTION CONTEXT",
        f"  entry_fill_model={report['execution_context']['entry_fill_model']}",
        f"  entry_tolerance_ticks_by_root={report['execution_context']['entry_tolerance_ticks_by_root']}",
        f"  max_contracts_per_instrument={report['execution_context']['max_contracts_per_instrument']}",
        "",
        "CLASSIFICATION",
        f"  RESEARCH RESULT: {c['research_result']}",
        f"  RUNTIME PARITY: {c['runtime_parity']}",
        f"  PAPER FORWARD EVIDENCE: {c['paper_forward_evidence']}",
        f"  SUGGESTED: {c['suggested_classification']} — {c['reason']}",
        "",
        "LIMITATIONS",
    ]
    lines.extend(f"  - {item}" for item in report["limitations"])
    return "\n".join(lines)


def format_daily(report: dict[str, Any]) -> str:
    lines = [
        f"DAILY RECONCILIATION — {report['generated_for_date']} (since {report['since_checkpoint']})",
        "",
        "GITHUB",
        f"  open PRs: {report['github']['open_prs']}",
        "",
        "BRANCHES / WORKTREES",
        f"  worktrees: {report['branches_and_worktrees']['worktrees']}",
        f"  stash_count: {report['branches_and_worktrees']['stash_count']}",
        f"  branches_tracking_deleted_remotes: {report['branches_and_worktrees']['branches_tracking_deleted_remotes']}",
        f"  local_only_branches: {report['branches_and_worktrees']['local_only_branches']}",
        f"  local main vs origin/main: {report['branches_and_worktrees']['local_main_vs_origin_main']['status']}",
        "",
        "EVIDENCE PRESERVATION",
        f"  blockers: {len(report['evidence_preservation']['blockers'])}",
    ]
    for b in report["evidence_preservation"]["blockers"]:
        lines.append(f"    BLOCKER: {b['branch']} @ {b['tip_sha'][:12]} — {b['unique_files_vs_main']} unique files, no archive tag")
    lines += [
        "",
        "DEPLOYED STATE",
        f"  live_box_drift status: {report['deployed_state']['live_box_drift']['status']}",
        f"  paper-eligible lanes: {report['deployed_state']['strategy_permission_gate']['paper_eligible_lanes']}",
        "",
        "STRATEGY SOURCE OF TRUTH",
        f"  flags: {report['strategy_source_of_truth']['flags'] or 'none'}",
        f"  unmatched_config_keys: {report['strategy_source_of_truth']['unmatched_config_keys']}",
        "",
        _fmt_trade_chain(report["trade_chain_integrity"]),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------- cli


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo-root", default=None)
    p.add_argument("--json", action="store_true", help="print raw JSON instead of the formatted report")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_session = sub.add_parser("session-start", help="repo + worktree + runtime snapshot; persists state for precommit")
    _add_common_args(p_session)
    p_session.add_argument("--state-path", default=None)

    p_pre = sub.add_parser("precommit", help="read-only fail-closed check against the session-start snapshot")
    _add_common_args(p_pre)
    p_pre.add_argument("--state-path", default=None)

    p_promo = sub.add_parser("promotion", help="strategy promotion proof gate")
    _add_common_args(p_promo)
    p_promo.add_argument("--strategy", required=True)
    p_promo.add_argument("--journal-dir", default=None)

    p_daily = sub.add_parser("daily", help="daily reconciliation + trade-chain integrity")
    _add_common_args(p_daily)
    p_daily.add_argument("--journal-dir", default=None)
    p_daily.add_argument("--checkpoint-path", default=None)
    p_daily.add_argument("--no-checkpoint-update", action="store_true", help="don't advance the daily checkpoint file")

    args = parser.parse_args(argv)

    if args.command == "session-start":
        report = build_session_start_report(args.repo_root)
        report["generated_at"] = datetime.now(timezone.utc).isoformat()
        state_path = Path(args.state_path) if args.state_path else SESSION_STATE_PATH
        _persist_session_state(report, state_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else format_session_start(report))
        return 0

    if args.command == "precommit":
        report = build_precommit_report(args.repo_root, args.state_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else format_precommit(report))
        return 0 if report["ok"] else 1

    if args.command == "promotion":
        report = build_promotion_report(args.strategy, repo_root=args.repo_root, journal_dir=args.journal_dir)
        print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else format_promotion(report))
        return 0

    if args.command == "daily":
        report = build_daily_report(args.repo_root, args.journal_dir, args.checkpoint_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else format_daily(report))
        if not args.no_checkpoint_update:
            checkpoint_path = Path(args.checkpoint_path) if args.checkpoint_path else DAILY_CHECKPOINT_PATH
            _persist_daily_checkpoint(checkpoint_path, report["generated_for_date"])
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
