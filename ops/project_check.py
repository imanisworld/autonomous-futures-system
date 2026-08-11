"""ops/project_check.py — three manually-invoked, read-only-by-default routines.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy <name> --candles <dir-or-manifest.json>
    python -m ops.project_check daily

This module is glue, not a new subsystem. Every non-trivial check delegates to
existing, already-reviewed machinery instead of re-implementing it:

  git/gh primitives        -> ops.project_check_git (this package)
  deployed-state drift     -> ops.live_box_guard.live_box_drift_report
  active lane enablement   -> execution.mnq_strat_evidence / mes_trend_consolidation_break_evidence
  lane-level daily health  -> ops.evidence_lane_health
  the real executable path -> replay.replay_engine.ReplayEngine (config-driven
                               DecisionEngine + RiskEngine + PaperBroker)
  gate-by-gate attrition   -> ops.strategy_intent_audit.build_audit
  journal pairing/buckets  -> ops.proof_30_mnq (classify_outcome, pair_resolved_trades)
  reconciler exceptions    -> ops.reconciler_outcome_audit
  no-fill marketability    -> ops.audit_plain_cancelled
  decision/risk label QA   -> ops.journal_label_audit
  open-position drift      -> ops.block_visibility

Nothing in this file trades, deploys, mutates git state, or writes to a
tracked repo file. The only files it writes are its own ephemeral session
bookkeeping under ``.git/project_check/`` (never committed, never read by
anything else) and, for the ``promotion`` command, a fresh replay journal
under whatever ``--log-dir`` the caller picked (default: an untracked
``logs/`` subdirectory, matching every other replay tool in this repo).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ops import project_check_git as pcgit

REPO_ROOT = Path(__file__).resolve().parents[1]
SESSION_STATE_PATH = REPO_ROOT / ".git" / "project_check" / "session_start.json"
DAILY_CHECKPOINT_PATH = REPO_ROOT / ".git" / "project_check" / "daily_checkpoint.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ─────────────────────────────────────────────────────────────────────────
# Shared: runtime snapshot (deployed SHA / lanes / entry model / tolerance)
# ─────────────────────────────────────────────────────────────────────────

def _runtime_snapshot(repo_root: Path, log_dir: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}

    try:
        from ops.live_box_guard import live_box_drift_report
        guard = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
        overrides_by_name = {o["name"]: o for o in guard["proof_critical_runtime_overrides"]}

        def _override(name: str) -> str:
            row = overrides_by_name.get(name)
            if row is None:
                return "UNKNOWN"
            if row["observed"] is not None:
                return row["observed"]
            return "UNSET"

        snapshot["deployed_state"] = {
            "status": guard["status"],
            "summary": guard["summary"],
            "identity_source": guard["identity_source"],
            "branch": guard["branch"],
            "commit": guard["commit"],
            "expected_commit_pin": next(
                (c["expected"] for c in guard["comparisons"] if c["name"] == "commit"), None
            ),
            "runtime_evidence_source": guard["runtime_evidence_source"],
            "missing_pins": guard["missing_pins"],
            "mismatches": guard["mismatches"],
            "active_unpinned_runtime_overrides": guard["unpinned_runtime_overrides"],
        }
        snapshot["entry_fill_model"] = _override("ENTRY_FILL_MODEL")
        snapshot["entry_tolerance_ticks"] = {
            "default": _override("ENTRY_SLIPPAGE_TOLERANCE_TICKS"),
            "MES": _override("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES"),
            "MNQ": _override("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ"),
        }
        snapshot["max_contracts_hard_cap"] = _override("MAX_CONTRACTS_HARD_CAP")
        snapshot["execution_mode"] = _override("TRADOVATE_ENTRY_EXECUTION_MODE")
    except Exception as exc:  # noqa: BLE001 - report, never crash the snapshot
        snapshot["deployed_state_error"] = f"{type(exc).__name__}: {exc}"

    lanes: list[dict[str, Any]] = []
    try:
        from execution.mnq_strat_evidence import LANES as MNQ_LANES, lane_mode as mnq_lane_mode
        for key in MNQ_LANES:
            lanes.append({"instrument": "MNQ", "lane": key, "mode": mnq_lane_mode(key)})
    except Exception as exc:  # noqa: BLE001
        snapshot["mnq_lanes_error"] = f"{type(exc).__name__}: {exc}"
    try:
        from execution.mes_trend_consolidation_break_evidence import LANE as MES_LANE, lane_mode as mes_lane_mode
        lanes.append({"instrument": "MES", "lane": MES_LANE, "mode": mes_lane_mode()})
    except Exception as exc:  # noqa: BLE001
        snapshot["mes_lane_error"] = f"{type(exc).__name__}: {exc}"
    snapshot["active_lanes"] = lanes

    try:
        from ops.evidence_lane_health import build_snapshot
        health = build_snapshot(log_dir=log_dir, day=_now().date(), now=_now())
        snapshot["lane_health"] = health
    except Exception as exc:  # noqa: BLE001
        snapshot["lane_health_error"] = (
            f"{type(exc).__name__}: {exc} (lane health needs today's live/replay journal; "
            "UNKNOWN outside an active runtime)"
        )

    # "Evidence epoch" is requested by spec but is not an existing concept
    # anywhere in this repo (grepped: no match). Report UNKNOWN rather than
    # invent a value or a new field name unilaterally.
    snapshot["evidence_epoch"] = "UNKNOWN (no 'evidence epoch' concept exists in this repo yet)"
    return snapshot


# ─────────────────────────────────────────────────────────────────────────
# 1a. session-start
# ─────────────────────────────────────────────────────────────────────────

def _repo_report(repo_root: Path) -> dict[str, Any]:
    branch = pcgit.current_branch(repo_root)
    head = pcgit.head_sha(repo_root)
    fetched_ok, fetch_err = pcgit.fetch_origin(repo_root)
    origin_main = pcgit.ref_sha("origin/main", repo_root)
    local_main = pcgit.ref_sha("main", repo_root)
    upstream = pcgit.upstream_ref(repo_root)
    status = pcgit.working_tree_status(repo_root)
    worktrees = pcgit.worktree_list(repo_root)

    main_sync = "UNKNOWN"
    if local_main and origin_main:
        main_sync = pcgit.sync_state("main", "origin/main", repo_root)

    gone = pcgit.branches_tracking_deleted_remotes(repo_root)
    local_only = pcgit.local_only_branches(repo_root)
    tags = pcgit.archive_tags(repo_root)
    stashes = pcgit.stash_list(repo_root)

    open_prs, open_prs_err = pcgit.gh_json(
        ["pr", "list", "--state", "open", "--json", "number,title,headRefName,updatedAt,url", "--limit", "100"]
    )
    closed_unmerged, closed_err = pcgit.gh_json(
        [
            "pr", "list", "--state", "closed",
            "--json", "number,title,headRefName,closedAt,url,mergedAt",
            "--limit", "100",
        ]
    )
    if closed_unmerged is not None:
        closed_unmerged = [pr for pr in closed_unmerged if not pr.get("mergedAt")]

    unmerged_local = pcgit.unmerged_branches("main", repo_root) if local_main else []
    archive_commits = {
        tag: pcgit.ref_sha(f"{tag}^{{commit}}", repo_root) for tag in tags
    }
    closed_unmerged_missing_archive: list[dict[str, Any]] = []
    if closed_unmerged is not None:
        for pr in closed_unmerged:
            branch_name = pr.get("headRefName")
            if not branch_name:
                continue
            tip = pcgit.ref_sha(f"origin/{branch_name}", repo_root) or pcgit.ref_sha(branch_name, repo_root)
            unique_commits = pcgit.branch_unique_commits(branch_name, "main", repo_root) if tip else -1
            has_archive = tip is not None and tip in archive_commits.values()
            if tip is not None and unique_commits > 0 and not has_archive:
                closed_unmerged_missing_archive.append(
                    {
                        "branch": branch_name,
                        "pr": pr.get("url"),
                        "tip_sha": tip,
                        "unique_commits_vs_main": unique_commits,
                        "archive_tag_found": False,
                        "severity": "BLOCKER",
                    }
                )

    return {
        "repo_root": str(repo_root),
        "current_branch": branch,
        "head_sha": head,
        "origin_main_sha": origin_main,
        "local_main_sha": local_main,
        "local_main_vs_origin_main": main_sync,
        "origin_fetch_ok": fetched_ok,
        "origin_fetch_error": fetch_err,
        "upstream": upstream,
        "current_worktree": pcgit.repo_root(Path.cwd()),
        "worktrees": worktrees,
        "dirty_tracked_files": status["dirty"],
        "staged_files": status["staged"],
        "untracked_files": status["untracked"],
        "branches_tracking_deleted_remotes": gone,
        "local_only_branches": local_only,
        "unmerged_into_main": unmerged_local,
        "archive_tags": tags,
        "stash_count": len(stashes),
        "stashes": stashes,
        "open_prs": open_prs,
        "open_prs_error": open_prs_err,
        "closed_unmerged_prs": closed_unmerged,
        "closed_unmerged_prs_error": closed_err,
        "closed_unmerged_missing_archive_tag": closed_unmerged_missing_archive,
        "gh_available": pcgit.gh_available(),
    }


def cmd_session_start(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(pcgit.repo_root() or REPO_ROOT)
    report = _repo_report(repo_root)
    report["runtime_snapshot"] = _runtime_snapshot(repo_root, Path(args.log_dir))

    branch_before = report["current_branch"]
    # Detect whether the checked-out branch changed *during* this check (the
    # only git-mutating call above is `fetch`, which cannot move HEAD, but a
    # concurrent process could -- so verify rather than assume).
    branch_after = pcgit.current_branch(repo_root)
    report["branch_changed_during_check"] = branch_before != branch_after
    if report["branch_changed_during_check"]:
        report["current_branch"] = branch_after

    _write_json(
        SESSION_STATE_PATH,
        {
            "recorded_at": _now().isoformat(),
            "repo_root": str(repo_root),
            "branch": report["current_branch"],
            "head_sha": report["head_sha"],
            "worktree": report["current_worktree"],
        },
    )
    report["session_state_written_to"] = str(SESSION_STATE_PATH)
    return report


def _format_session_start(report: dict[str, Any]) -> str:
    lines = ["SESSION START — REPO", "=" * 60]
    lines.append(f"repo root:        {report['repo_root']}")
    lines.append(f"current branch:   {report['current_branch']}")
    lines.append(f"HEAD sha:         {report['head_sha']}")
    lines.append(f"origin/main sha:  {report['origin_main_sha']}")
    lines.append(f"local main sha:   {report['local_main_sha']}")
    lines.append(f"main vs origin:   {report['local_main_vs_origin_main']}")
    if not report["origin_fetch_ok"]:
        lines.append(f"  (origin fetch failed: {report['origin_fetch_error']}; sync state may be stale)")
    lines.append(f"upstream:         {report['upstream']}")
    lines.append(f"current worktree: {report['current_worktree']}")
    lines.append(f"branch changed mid-check: {report['branch_changed_during_check']}")
    lines.append("")
    lines.append(f"worktrees ({len(report['worktrees'])}):")
    for wt in report["worktrees"]:
        lines.append(f"  - {wt.get('path')}  branch={wt.get('branch', 'DETACHED')}  head={wt.get('head')}")
    lines.append("")
    lines.append(f"dirty tracked files ({len(report['dirty_tracked_files'])}): {report['dirty_tracked_files']}")
    lines.append(f"staged files ({len(report['staged_files'])}): {report['staged_files']}")
    lines.append(f"untracked files ({len(report['untracked_files'])}): {report['untracked_files']}")
    lines.append(f"branches tracking deleted remotes: {report['branches_tracking_deleted_remotes']}")
    lines.append(f"local-only branches: {report['local_only_branches']}")
    lines.append(f"unmerged into main: {report['unmerged_into_main']}")
    lines.append(f"archive/* tags: {report['archive_tags']}")
    lines.append(f"stash count: {report['stash_count']}")
    lines.append("")
    if report["gh_available"]:
        open_prs = report["open_prs"]
        lines.append(f"open PRs: {len(open_prs) if open_prs is not None else 'UNKNOWN (' + str(report['open_prs_error']) + ')'}")
        blockers = report["closed_unmerged_missing_archive_tag"]
        if blockers:
            lines.append(f"BLOCKER — closed-unmerged branches with unique evidence and no archive tag: {len(blockers)}")
            for b in blockers:
                lines.append(f"  - {b['branch']} ({b['pr']}) tip={b['tip_sha']} unique_commits={b['unique_commits_vs_main']}")
    else:
        lines.append("gh CLI not available — open PR / closed-unmerged-PR state is UNKNOWN.")
    lines.append("")
    lines.append("RUNTIME SNAPSHOT")
    lines.append("-" * 60)
    snap = report["runtime_snapshot"]
    deployed = snap.get("deployed_state", {})
    lines.append(f"deployed state status: {deployed.get('status', 'UNKNOWN')} — {deployed.get('summary', '')}")
    lines.append(f"entry fill model: {snap.get('entry_fill_model')}")
    lines.append(f"entry tolerance (ticks): {snap.get('entry_tolerance_ticks')}")
    lines.append(f"max contracts hard cap: {snap.get('max_contracts_hard_cap')}")
    lines.append(f"execution mode: {snap.get('execution_mode')}")
    lines.append(f"evidence epoch: {snap.get('evidence_epoch')}")
    lines.append("active paper-forward lanes:")
    for lane in snap.get("active_lanes", []):
        lines.append(f"  - {lane['instrument']} / {lane['lane']}: mode={lane['mode']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# 1b. precommit / prepush — strictly read-only, fails closed
# ─────────────────────────────────────────────────────────────────────────

def cmd_precommit(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(pcgit.repo_root() or REPO_ROOT)
    baseline = _read_json(SESSION_STATE_PATH)
    status = pcgit.working_tree_status(repo_root)
    current_branch = pcgit.current_branch(repo_root)
    current_worktree = pcgit.repo_root(Path.cwd())
    head = pcgit.head_sha(repo_root)
    upstream = pcgit.upstream_ref(repo_root)

    reasons: list[str] = []
    if baseline is None:
        reasons.append("no session-start baseline found; run `python -m ops.project_check session-start` first")
    else:
        if baseline.get("branch") != current_branch:
            reasons.append(
                f"branch differs from session-start ({baseline.get('branch')!r} -> {current_branch!r})"
            )
        if baseline.get("worktree") != current_worktree:
            reasons.append(
                f"worktree differs from session-start ({baseline.get('worktree')!r} -> {current_worktree!r})"
            )
        if baseline.get("repo_root") != str(repo_root):
            reasons.append(
                f"repo root differs from session-start ({baseline.get('repo_root')!r} -> {str(repo_root)!r})"
            )

    if current_branch is None or head is None:
        reasons.append("repository state is ambiguous: could not resolve current branch/HEAD")

    # A branch checked out in another worktree cannot safely be pushed to
    # from here without risking a collision.
    other_worktree_owns_branch = None
    for wt in pcgit.worktree_list(repo_root):
        wt_branch = (wt.get("branch") or "").removeprefix("refs/heads/")
        if wt_branch and wt_branch == current_branch and wt.get("path") != current_worktree:
            other_worktree_owns_branch = wt.get("path")
            reasons.append(f"branch {current_branch!r} is also checked out in worktree {wt.get('path')}")

    ok = not reasons

    ahead_behind = "UNKNOWN"
    if upstream:
        ahead_behind = pcgit.sync_state("HEAD", upstream, repo_root)

    return {
        "ok": ok,
        "fail_closed": not ok,
        "reasons": reasons,
        "repo_root": str(repo_root),
        "current_branch": current_branch,
        "head_sha": head,
        "session_start_branch": (baseline or {}).get("branch"),
        "session_start_worktree": (baseline or {}).get("worktree"),
        "current_worktree": current_worktree,
        "upstream": upstream,
        "ahead_behind_upstream": ahead_behind,
        "changed_files": status["dirty"],
        "staged_files": status["staged"],
        "untracked_files": status["untracked"],
        "other_worktree_owns_branch": other_worktree_owns_branch,
    }


def _format_precommit(report: dict[str, Any]) -> str:
    lines = ["PRECOMMIT / PREPUSH CHECK (read-only)", "=" * 60]
    lines.append(f"VERDICT: {'PASS' if report['ok'] else 'FAIL CLOSED'}")
    if report["reasons"]:
        lines.append("reasons:")
        for r in report["reasons"]:
            lines.append(f"  - {r}")
    lines.append(f"repo root:        {report['repo_root']}")
    lines.append(f"current branch:   {report['current_branch']}")
    lines.append(f"HEAD:             {report['head_sha']}")
    lines.append(f"session-start branch:   {report['session_start_branch']}")
    lines.append(f"session-start worktree: {report['session_start_worktree']}")
    lines.append(f"current worktree:       {report['current_worktree']}")
    lines.append(f"upstream:         {report['upstream']}")
    lines.append(f"ahead/behind:     {report['ahead_behind_upstream']}")
    lines.append(f"changed files ({len(report['changed_files'])}): {report['changed_files']}")
    lines.append(f"staged files ({len(report['staged_files'])}): {report['staged_files']}")
    lines.append(f"untracked files ({len(report['untracked_files'])}): {report['untracked_files']}")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# 2. promotion — strategy proof gate through the real executable path
# ─────────────────────────────────────────────────────────────────────────

def _load_candle_paths(candles_arg: str) -> tuple[list[str], Optional[str]]:
    path = Path(candles_arg)
    if path.is_file() and path.suffix == ".json":
        return [str(path)], "manifest"
    if path.is_dir():
        files = sorted(str(p) for p in path.glob("*.jsonl"))
        return files, "directory"
    return [], None


def _strategy_permission_status(strategy: str, repo_root: Path) -> dict[str, Any]:
    import yaml

    risk_path = repo_root / "risk_rules.yaml"
    try:
        rules = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    gate = rules.get("strategy_permission_gate") or {}
    status_map = gate.get("strategy_status") or {}
    return {
        "gate_enabled": bool(gate.get("enabled")),
        "default_status": gate.get("default_status"),
        "strategy_status": status_map.get(strategy),
        "effective_status": status_map.get(strategy) or gate.get("default_status"),
    }


def _accounting_from_entries(entries: list[dict[str, Any]], *, instrument: str, strategy: str) -> dict[str, Any]:
    from ops.proof_30_mnq import classify_outcome, pair_resolved_trades

    resolved, unmatched_outcomes = pair_resolved_trades(entries, instrument=instrument, limit=1_000_000)
    resolved_for_strategy = [r for r in resolved if (r.setup or {}).get("strategy") == strategy]

    approved_for_strategy = [
        e for e in entries
        if (e.get("instrument") or "").upper() == instrument.upper()
        and e.get("decision") == "TRADE"
        and (e.get("risk_check") or {}).get("result") == "APPROVED"
        and (e.get("setup") or {}).get("strategy") == strategy
    ]

    categories = [classify_outcome(r.outcome_body) for r in resolved_for_strategy]
    filled_wl = categories.count("filled_win_loss")
    breakeven = categories.count("breakeven")
    cancelled_nofill = categories.count("cancelled_nofill")
    reconciler_touched = categories.count("reconciler_touched")
    other = categories.count("other")

    attempts = len(approved_for_strategy)
    resolved_count = len(resolved_for_strategy)
    unresolved_at_boundary = attempts - resolved_count

    fills = filled_wl + breakeven
    cancellations = cancelled_nofill
    rejects_unknown = reconciler_touched + other

    identity_ok = (attempts == fills + cancellations + rejects_unknown + max(0, unresolved_at_boundary))

    return {
        "instrument": instrument,
        "attempts": attempts,
        "fills_resolved": fills,
        "fills_win_loss": filled_wl,
        "fills_breakeven": breakeven,
        "cancellations_no_fill": cancellations,
        "rejects_or_unclassified": rejects_unknown,
        "reconciler_touched_needs_manual_review": reconciler_touched,
        "unresolved_at_replay_boundary": max(0, unresolved_at_boundary),
        "accounting_identity_holds": identity_ok,
        "accounting_identity_formula": "attempts == fills_resolved + cancellations_no_fill + rejects_or_unclassified + unresolved_at_replay_boundary",
        "unmatched_outcomes_no_matching_trade": len(
            [o for o in unmatched_outcomes if (o.get("instrument") or "").upper() == instrument.upper()]
        ),
        "resolved_trades": resolved_for_strategy,
    }


def _performance_from_resolved(resolved_trades: list) -> dict[str, Any]:
    pnls = []
    for r in resolved_trades:
        body = r.outcome_body
        if body.get("result") not in ("WIN", "LOSS", "BREAKEVEN"):
            continue
        pnls.append(
            {
                "ts": r.outcome_ts,
                "pnl": float(body.get("pnl_dollars") or 0.0),
                "direction": (r.setup or {}).get("direction"),
                "instrument": r.trade.get("instrument"),
                "session": r.trade.get("session") or r.outcome.get("session"),
                "result": body.get("result"),
            }
        )
    pnls.sort(key=lambda p: p["ts"])
    n = len(pnls)
    if n == 0:
        return {"sample": 0, "note": "no resolved WIN/LOSS/BREAKEVEN trades"}

    net = sum(p["pnl"] for p in pnls)
    wins = [p for p in pnls if p["pnl"] > 0]
    losses = [p for p in pnls if p["pnl"] < 0]
    gross_win = sum(p["pnl"] for p in wins)
    gross_loss = abs(sum(p["pnl"] for p in losses))
    pf = (gross_win / gross_loss) if gross_loss > 0 else None
    win_rate = len(wins) / n
    expectancy = net / n

    half = n // 2
    h1, h2 = pnls[:half], pnls[half:]
    h1_pnl = sum(p["pnl"] for p in h1)
    h2_pnl = sum(p["pnl"] for p in h2)

    by_year: Counter = Counter()
    by_instrument: Counter = Counter()
    by_direction: Counter = Counter()
    by_session: Counter = Counter()
    for p in pnls:
        year = str(p["ts"])[:4] if p["ts"] else "UNKNOWN"
        by_year[year] += p["pnl"]
        by_instrument[p["instrument"] or "UNKNOWN"] += p["pnl"]
        by_direction[p["direction"] or "UNKNOWN"] += p["pnl"]
        by_session[p["session"] or "UNKNOWN"] += p["pnl"]

    recent_n = max(1, n // 5)
    recent = pnls[-recent_n:]
    recent_pnl = sum(p["pnl"] for p in recent)

    running = 0.0
    peak = 0.0
    max_dd = 0.0
    streak = 0
    max_loss_streak = 0
    for p in pnls:
        running += p["pnl"]
        peak = max(peak, running)
        max_dd = max(max_dd, peak - running)
        if p["pnl"] < 0:
            streak += 1
            max_loss_streak = max(max_loss_streak, streak)
        else:
            streak = 0
    recovered = running >= peak - 1e-9

    top_winner = max((p["pnl"] for p in wins), default=0.0)
    top_winner_concentration = (top_winner / gross_win) if gross_win > 0 else None

    return {
        "sample": n,
        "net_pnl_dollars": round(net, 2),
        "profit_factor": round(pf, 3) if pf is not None else None,
        "expectancy_dollars": round(expectancy, 2),
        "win_rate": round(win_rate, 4),
        "h1_pnl_dollars": round(h1_pnl, 2),
        "h2_pnl_dollars": round(h2_pnl, 2),
        "walk_forward_both_halves_positive": h1_pnl > 0 and h2_pnl > 0,
        "by_year_pnl_dollars": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "recent_period_sample": recent_n,
        "recent_period_pnl_dollars": round(recent_pnl, 2),
        "by_instrument_pnl_dollars": {k: round(v, 2) for k, v in by_instrument.items()},
        "by_direction_pnl_dollars": {k: round(v, 2) for k, v in by_direction.items()},
        "by_session_pnl_dollars": {k: round(v, 2) for k, v in by_session.items()},
        "slippage_sensitivity": (
            "NOT RUN in this pass — this gate does not re-run at +1/+2 tick slippage. "
            "See ops/fill_realism.py and scripts/*_slippage_sensitivity_*.py for a dedicated pass."
        ),
        "max_drawdown_dollars": round(max_dd, 2),
        "max_consecutive_losses": max_loss_streak,
        "recovered_to_new_equity_high": recovered,
        "top_winner_concentration_of_gross_win": round(top_winner_concentration, 4) if top_winner_concentration is not None else None,
    }


def _gate_attrition(audit: dict[str, Any], strategy: str) -> dict[str, Any]:
    candidates_for_strategy = [
        candidate
        for item in audit.get("decisions", [])
        for candidate in item.get("candidates", [])
        if candidate.get("strategy") == strategy
    ]
    attempted = [c for c in candidates_for_strategy if c.get("attempted")]
    selected = [c for c in candidates_for_strategy if c.get("selected")]
    winner = [c for c in candidates_for_strategy if c.get("winner")]
    reject_reasons = Counter(c.get("reject_code") for c in candidates_for_strategy if c.get("reject_code"))
    failed_gates = Counter(
        gate for c in candidates_for_strategy for gate in (c.get("failed_gates") or [])
    )
    return {
        "candidates_considered": len(candidates_for_strategy),
        "candidates_attempted": len(attempted),
        "candidates_selected": len(selected),
        "candidates_winner": len(winner),
        "reject_code_counts": dict(reject_reasons.most_common()),
        "failed_gate_counts": dict(failed_gates.most_common()),
        "note": (
            "Reject/gate names reflect whatever ops.strategy_intent_audit's candidate_audit "
            "rows recorded (reject_code / failed_gates from the real DecisionEngine run). "
            "This is not a bespoke replica of the gate list — it is the actual gate outcome."
        ),
    }


def cmd_promotion(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(pcgit.repo_root() or REPO_ROOT)
    strategy = args.strategy
    instrument = args.instrument.upper()
    log_dir = Path(args.log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    candle_paths, source_kind = _load_candle_paths(args.candles)
    if not candle_paths:
        return {
            "ok": False,
            "classification": "WAIT",
            "reason": f"no candle files found at --candles {args.candles!r} (expected a directory of .jsonl files or a *.json replay manifest)",
        }

    from config.settings import load_config
    from replay.replay_engine import ReplayEngine

    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "classification": "WAIT", "reason": f"config load failed: {type(exc).__name__}: {exc}"}

    engine = ReplayEngine(config=config, log_dir=str(log_dir))
    try:
        if source_kind == "manifest":
            multi = engine.run_manifest(candle_paths[0])
        else:
            multi = engine.run_many(candle_paths)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "classification": "WAIT", "reason": f"ReplayEngine run failed: {type(exc).__name__}: {exc}"}

    from ops.proof_30_mnq import read_journal_entries
    from ops.strategy_intent_audit import build_audit

    entries = read_journal_entries(log_dir)
    audit = build_audit(journal_dir=log_dir)
    accounting = _accounting_from_entries(entries, instrument=instrument, strategy=strategy)
    performance = _performance_from_resolved(accounting.pop("resolved_trades"))
    gates = _gate_attrition(audit, strategy)
    permission = _strategy_permission_status(strategy, repo_root)

    standalone = None
    if args.standalone_results:
        standalone = _read_json(Path(args.standalone_results))

    execution_context = {
        "entry_fill_model": config.entry_fill_model,
        "entry_tolerance_ticks_by_root": config.entry_tolerance_ticks_by_root,
        "fill_slippage_ticks": getattr(config, "fill_slippage_ticks", None),
        "fill_pessimistic_both_hit": getattr(config, "fill_pessimistic_both_hit", None),
        "max_contracts_hard_cap": config.max_contracts_hard_cap,
    }

    # ---- classification (transparent heuristic; never asserts VALIDATED
    #      without the permission gate actually being enabled, per the
    #      Miyagi / 60M 3-2-2 precedent: positive standalone numbers must not
    #      be mistaken for admission through the real gated system) ----
    reasons: list[str] = []
    if accounting["attempts"] == 0:
        classification = "WAIT"
        reasons.append("zero candidates for this strategy reached RiskEngine approval in this candle set")
    elif accounting["fills_resolved"] == 0 and accounting["unresolved_at_replay_boundary"] == 0:
        classification = "BROKEN"
        reasons.append("zero executable fills: every approved attempt was cancelled/rejected/unclassified")
    elif performance.get("sample", 0) < 30:
        classification = "PROMISING BUT UNPROVEN"
        reasons.append(f"resolved sample too small (n={performance.get('sample', 0)} < 30)")
    elif not permission.get("gate_enabled") or (permission.get("effective_status") in (None, "SHADOW_ONLY")):
        classification = "PROMISING BUT UNPROVEN"
        reasons.append(
            f"strategy_permission_gate effective_status={permission.get('effective_status')!r}: "
            "even a clean result here stays SHADOW_ONLY until an operator explicitly enables it"
        )
    elif performance.get("net_pnl_dollars", 0) <= 0:
        classification = "BROKEN"
        reasons.append("net P&L through the real executable path is not positive")
    elif not performance.get("walk_forward_both_halves_positive"):
        classification = "PROMISING BUT UNPROVEN"
        reasons.append("H1/H2 walk-forward split is not both-positive")
    else:
        classification = "VALIDATED"
        reasons.append("adequate sample, positive both-halves walk-forward, gate enabled, positive net P&L")
    if not accounting["accounting_identity_holds"]:
        reasons.append("ACCOUNTING IDENTITY FAILED — see execution section; treat classification as provisional")

    return {
        "ok": True,
        "strategy": strategy,
        "instrument": instrument,
        "candle_source": {"kind": source_kind, "paths": candle_paths},
        "identity_parity": {
            "raw_candidate_count_all_strategies": audit["summary"]["candidate_rows"],
            "standalone_results_supplied": bool(standalone),
            "standalone_results": standalone,
            "note": (
                "direction/entry/stop/target/timeframe parity and lookahead checks are only "
                "meaningful against a specific standalone evidence artifact — pass one with "
                "--standalone-results to diff it against this real-path run; otherwise this "
                "section only reports what the real engine itself produced."
            ),
        },
        "gate_attrition": gates,
        "execution": accounting,
        "performance": performance,
        "execution_context": execution_context,
        "strategy_permission_gate": permission,
        "multi_day_summary": multi.to_dict(),
        "classification": classification,
        "classification_reasons": reasons,
        "rules_applied": (
            "No rescue/tuning variant was run in this pass. No runtime, config, or risk_rules.yaml "
            "change was made. This report is not a merge, deploy, or enablement action."
        ),
        "journal_dir": str(log_dir),
    }


def _format_promotion(report: dict[str, Any]) -> str:
    if not report.get("ok"):
        return f"PROMOTION PROOF GATE — ABORTED\n{'=' * 60}\nreason: {report.get('reason')}"
    lines = [f"STRATEGY PROMOTION PROOF GATE — {report['strategy']} ({report['instrument']})", "=" * 60]
    lines.append(f"candle source: {report['candle_source']['kind']} ({len(report['candle_source']['paths'])} file(s))")
    lines.append("")
    lines.append("GATE ATTRITION")
    g = report["gate_attrition"]
    lines.append(f"  candidates considered: {g['candidates_considered']}")
    lines.append(f"  attempted: {g['candidates_attempted']}  selected: {g['candidates_selected']}  winner: {g['candidates_winner']}")
    lines.append(f"  reject codes: {g['reject_code_counts']}")
    lines.append(f"  failed gates: {g['failed_gate_counts']}")
    lines.append("")
    lines.append("EXECUTION")
    e = report["execution"]
    lines.append(
        f"  attempts={e['attempts']} fills={e['fills_resolved']} (win/loss={e['fills_win_loss']} be={e['fills_breakeven']}) "
        f"cancellations={e['cancellations_no_fill']} rejects/unclassified={e['rejects_or_unclassified']} "
        f"open_at_boundary={e['unresolved_at_replay_boundary']}"
    )
    lines.append(f"  accounting identity holds: {e['accounting_identity_holds']}  ({e['accounting_identity_formula']})")
    if e["reconciler_touched_needs_manual_review"]:
        lines.append(f"  ⚠ reconciler-touched rows in a replay run (unexpected): {e['reconciler_touched_needs_manual_review']}")
    lines.append("")
    lines.append("PERFORMANCE")
    p = report["performance"]
    if p.get("sample", 0) == 0:
        lines.append("  no resolved trades")
    else:
        lines.append(f"  sample={p['sample']} net_pnl=${p['net_pnl_dollars']} PF={p['profit_factor']} expectancy=${p['expectancy_dollars']} win_rate={p['win_rate']:.1%}")
        lines.append(f"  H1=${p['h1_pnl_dollars']} H2=${p['h2_pnl_dollars']} both_positive={p['walk_forward_both_halves_positive']}")
        lines.append(f"  max_drawdown=${p['max_drawdown_dollars']} max_consecutive_losses={p['max_consecutive_losses']} recovered={p['recovered_to_new_equity_high']}")
        lines.append(f"  {p['slippage_sensitivity']}")
    lines.append("")
    lines.append("EXECUTION CONTEXT")
    for k, v in report["execution_context"].items():
        lines.append(f"  {k}: {v}")
    lines.append("")
    perm = report["strategy_permission_gate"]
    lines.append(f"strategy_permission_gate: enabled={perm.get('gate_enabled')} effective_status={perm.get('effective_status')!r}")
    lines.append("")
    lines.append(f"CLASSIFICATION: {report['classification']}")
    for r in report["classification_reasons"]:
        lines.append(f"  - {r}")
    lines.append("")
    lines.append(report["rules_applied"])
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# 3. daily — reconciliation + trade chain integrity
# ─────────────────────────────────────────────────────────────────────────

def _github_repo_section(repo_root: Path) -> dict[str, Any]:
    today = _now().date().isoformat()
    open_prs, open_err = pcgit.gh_json(
        ["pr", "list", "--state", "open", "--json", "number,title,headRefName,createdAt,updatedAt,url", "--limit", "100"]
    )
    merged_prs, merged_err = pcgit.gh_json(
        ["pr", "list", "--state", "merged", "--json", "number,title,headRefName,mergedAt,url", "--limit", "100"]
    )
    closed_prs, closed_err = pcgit.gh_json(
        ["pr", "list", "--state", "closed", "--json", "number,title,headRefName,closedAt,mergedAt,url", "--limit", "100"]
    )
    merged_today = [pr for pr in (merged_prs or []) if str(pr.get("mergedAt", "")).startswith(today)]
    closed_unmerged = [pr for pr in (closed_prs or []) if not pr.get("mergedAt")]
    closed_unmerged_today = [pr for pr in closed_unmerged if str(pr.get("closedAt", "")).startswith(today)]
    stale_prs = []
    if open_prs is not None:
        for pr in open_prs:
            updated = pr.get("updatedAt")
            if not updated:
                continue
            try:
                age_days = (_now() - datetime.fromisoformat(updated.replace("Z", "+00:00"))).days
            except ValueError:
                continue
            if age_days >= 14:
                stale_prs.append({**pr, "age_days": age_days})

    return {
        "open_prs": open_prs,
        "open_prs_error": open_err,
        "merged_today": merged_today,
        "merged_today_error": merged_err,
        "closed_unmerged_today": closed_unmerged_today,
        "closed_unmerged_today_error": closed_err,
        "current_open_prs_count": len(open_prs) if open_prs is not None else None,
        "stale_prs_14d": stale_prs,
        "closed_unmerged_all_open": closed_unmerged,
    }


def _branches_worktrees_section(repo_root: Path) -> dict[str, Any]:
    worktrees = pcgit.worktree_list(repo_root)
    dirty_worktrees = []
    for wt in worktrees:
        path = wt.get("path")
        if not path or wt.get("bare"):
            continue
        status = pcgit.working_tree_status(Path(path))
        if status["dirty"] or status["staged"] or status["untracked"]:
            dirty_worktrees.append({"path": path, **status})

    local_main = pcgit.ref_sha("main", repo_root)
    origin_main = pcgit.ref_sha("origin/main", repo_root)
    main_sync = pcgit.sync_state("main", "origin/main", repo_root) if (local_main and origin_main) else "UNKNOWN"

    current_branch = pcgit.current_branch(repo_root)
    merged = pcgit.merged_branches("main", repo_root)
    stale_merged = [b for b in merged if b not in ("main", "master", current_branch)]

    remote_branches = pcgit.git("branch", "-r", "--format=%(refname:short)", cwd=repo_root) or ""
    remote_list = [b.strip() for b in remote_branches.splitlines() if b.strip() and "HEAD" not in b]

    return {
        "worktrees": worktrees,
        "dirty_worktrees": dirty_worktrees,
        "stale_merged_local_branches": stale_merged,
        "branches_tracking_deleted_remotes": pcgit.branches_tracking_deleted_remotes(repo_root),
        "local_only_branches": pcgit.local_only_branches(repo_root),
        "local_main_vs_origin_main": main_sync,
        "remote_branches": remote_list,
        "stash_count": len(pcgit.stash_list(repo_root)),
    }


def _evidence_preservation_section(repo_root: Path, github: dict[str, Any]) -> dict[str, Any]:
    tags = pcgit.archive_tags(repo_root)
    archive_commits = {tag: pcgit.ref_sha(f"{tag}^{{commit}}", repo_root) for tag in tags}
    archived_shas = set(archive_commits.values())

    blockers = []
    closed_unmerged = github.get("closed_unmerged_all_open") or []
    for pr in closed_unmerged:
        branch_name = pr.get("headRefName")
        if not branch_name:
            continue
        tip = pcgit.ref_sha(f"origin/{branch_name}", repo_root) or pcgit.ref_sha(branch_name, repo_root)
        if tip is None:
            continue
        unique_commits = pcgit.branch_unique_commits(branch_name, "main", repo_root)
        if unique_commits > 0 and tip not in archived_shas:
            blockers.append(
                {
                    "branch": branch_name,
                    "pr": pr.get("url"),
                    "tip_sha": tip,
                    "unique_commits_vs_main": unique_commits,
                    "severity": "BLOCKER",
                    "action": "never auto-created — operator must review and tag archive/<slug>-<date> per docs/BRANCH_ARCHIVE_INDEX.md convention",
                }
            )
    return {
        "archive_tags": tags,
        "closed_unmerged_branches_missing_archive_tag": blockers,
    }


def _strategy_source_of_truth_section(repo_root: Path) -> dict[str, Any]:
    import re
    import yaml

    inventory_path = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    rows: list[dict[str, str]] = []
    if inventory_path.exists():
        text = inventory_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if not line.startswith("|") or "---" in line or "Strategy" in line and "Verdict" in line:
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) < 2:
                continue
            verdict_cell = cells[-1]
            verdict_match = re.search(r"\*\*([A-Z ]+)\*\*", verdict_cell)
            if not verdict_match:
                continue
            rows.append({"strategy": cells[0], "verdict": verdict_match.group(1).strip()})

    risk_path = repo_root / "risk_rules.yaml"
    try:
        rules = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        rules = {}
    gate = rules.get("strategy_permission_gate") or {}
    status_map = gate.get("strategy_status") or {}

    lane_keys: set[str] = set()
    try:
        from execution.mnq_strat_evidence import LANES as MNQ_LANES
        lane_keys.update(MNQ_LANES.keys())
    except Exception:  # noqa: BLE001
        pass

    drift = []
    positive_verdicts = {"VALIDATED", "PAPER PROOF"}
    negative_verdicts = {"BROKEN", "WAIT", "OVERFIT", "RETIRE"}
    live_eligible_statuses = {"PAPER_ELIGIBLE"}
    for row in rows:
        name = row["strategy"]
        verdict = row["verdict"]
        matched_status_key = next((k for k in status_map if k.lower() in name.lower() or name.lower() in k.lower()), None)
        status = status_map.get(matched_status_key) if matched_status_key else None
        effective_status = status or gate.get("default_status")
        if verdict in positive_verdicts and (not gate.get("enabled") or status in (None, "SHADOW_ONLY")):
            drift.append(
                {
                    "strategy": name,
                    "issue": "described_active_but_fail_closed",
                    "inventory_verdict": verdict,
                    "strategy_status_key": matched_status_key,
                    "effective_status": effective_status,
                }
            )
        if verdict in negative_verdicts and gate.get("enabled") and effective_status in live_eligible_statuses:
            drift.append(
                {
                    "strategy": name,
                    "issue": "described_broken_but_runtime_eligible",
                    "inventory_verdict": verdict,
                    "strategy_status_key": matched_status_key,
                    "effective_status": effective_status,
                }
            )

    missing_lane_rows = [
        key for key in lane_keys
        if not any(key.lower() in row["strategy"].lower() for row in rows)
    ]

    return {
        "inventory_path": str(inventory_path) if inventory_path.exists() else None,
        "inventory_rows": rows,
        "gate_enabled": bool(gate.get("enabled")),
        "strategy_status_map": status_map,
        "drift": drift,
        "lane_keys_missing_from_inventory": missing_lane_rows,
        "note": (
            "matching between Strategy_Inventory.md row names and risk_rules.yaml "
            "strategy_status keys is a best-effort substring match; a miss here is a "
            "prompt to check by hand, not proof of drift."
        ),
    }


def _deployed_state_section(repo_root: Path, log_dir: Path) -> dict[str, Any]:
    return _runtime_snapshot(repo_root, log_dir)


def _trade_chain_section(repo_root: Path, journal_dir: Path, since: Optional[str], api_base: Optional[str]) -> dict[str, Any]:
    from ops.proof_30_mnq import classify_outcome, pair_resolved_trades, read_journal_entries, parse_proof_ts

    entries = read_journal_entries(journal_dir)
    since_ts = parse_proof_ts(since) if since else None
    if since_ts is not None:
        entries = [e for e in entries if (ts := parse_proof_ts(e.get("ts"))) is None or ts >= since_ts]

    instruments = sorted({(e.get("instrument") or "").upper() for e in entries if e.get("instrument")})
    per_instrument = {}
    orphans = []
    total_attempts = total_fills = total_cancellations = total_open = total_rejects = 0

    no_protective_bracket: list[dict[str, Any]] = []
    for instrument in instruments:
        resolved, unmatched_outcomes = pair_resolved_trades(entries, instrument=instrument, limit=1_000_000)
        approved = [
            e for e in entries
            if (e.get("instrument") or "").upper() == instrument
            and e.get("decision") == "TRADE"
            and (e.get("risk_check") or {}).get("result") == "APPROVED"
        ]
        categories = [classify_outcome(r.outcome_body) for r in resolved]
        fills = categories.count("filled_win_loss") + categories.count("breakeven")
        cancellations = categories.count("cancelled_nofill")
        rejects_unclassified = categories.count("reconciler_touched") + categories.count("other")
        open_positions = len(approved) - len(resolved)

        for u in unmatched_outcomes:
            if (u.get("instrument") or "").upper() == instrument:
                orphans.append({"instrument": instrument, "outcome_ts": u.get("ts"), "reason": "OUTCOME with no matching approved TRADE"})

        # Protective-bracket visibility: every FILLED trade (win/loss/breakeven)
        # should carry an order_ids record (same inline field audit_plain_cancelled
        # already keys off). A filled trade with none logged is a gap worth a
        # human look, not proof of a naked position (ops.block_visibility /
        # health_digest's live NAKED check is the authoritative point-in-time read).
        for r, category in zip(resolved, categories):
            if category in ("filled_win_loss", "breakeven"):
                has_order_ids = bool(r.trade.get("order_ids") or r.outcome.get("order_ids"))
                if not has_order_ids:
                    no_protective_bracket.append(
                        {"instrument": instrument, "trade_ts": r.trade_ts, "outcome_ts": r.outcome_ts, "strategy": (r.setup or {}).get("strategy")}
                    )

        per_instrument[instrument] = {
            "attempts": len(approved),
            "fills": fills,
            "cancellations_no_fill": cancellations,
            "rejects_or_unclassified": rejects_unclassified,
            "legitimately_open": max(0, open_positions),
            "resolved": len(resolved),
            "accounting_identity_holds": len(approved) == fills + cancellations + rejects_unclassified + max(0, open_positions),
        }
        total_attempts += len(approved)
        total_fills += fills
        total_cancellations += cancellations
        total_rejects += rejects_unclassified
        total_open += max(0, open_positions)

    label_report = None
    try:
        from ops.journal_label_audit import build_audit as label_build_audit
        label_report = label_build_audit(journal_dir=journal_dir)
    except Exception as exc:  # noqa: BLE001
        label_report = {"error": f"{type(exc).__name__}: {exc}"}

    reconciler_report = None
    try:
        from ops.reconciler_outcome_audit import build_audit_report
        overrides_doc = repo_root / "docs" / "proof-operator-overrides.md"
        reconciler_report = build_audit_report(
            journal_dir=journal_dir,
            overrides_doc=overrides_doc if overrides_doc.exists() else None,
        )
    except Exception as exc:  # noqa: BLE001
        reconciler_report = {"error": f"{type(exc).__name__}: {exc}"}

    plain_cancelled_report = None
    try:
        from ops.audit_plain_cancelled import build_audit as plain_cancelled_build_audit
        plain_cancelled_report = plain_cancelled_build_audit(journal_dir)
    except Exception as exc:  # noqa: BLE001
        plain_cancelled_report = {"error": f"{type(exc).__name__}: {exc}"}

    parity = {"status": "UNKNOWN", "reason": "no --api-base supplied; broker/journal parity requires a live status endpoint read"}
    if api_base:
        try:
            from ops.proof_30_mnq import load_json_url
            broker_payload, broker_err = load_json_url(f"{api_base.rstrip('/')}/status/broker-account")
            preflight_payload, preflight_err = load_json_url(f"{api_base.rstrip('/')}/status/live-preflight")
            parity = {
                "status": "READ" if broker_err is None else "ERROR",
                "broker_account": broker_payload,
                "broker_account_error": broker_err,
                "live_preflight": preflight_payload,
                "live_preflight_error": preflight_err,
            }
        except Exception as exc:  # noqa: BLE001
            parity = {"status": "ERROR", "reason": f"{type(exc).__name__}: {exc}"}

    total_resolved = sum(v["resolved"] for v in per_instrument.values())
    identity_holds_all = all(v["accounting_identity_holds"] for v in per_instrument.values()) if per_instrument else True

    order_id_counts: Counter = Counter(
        e.get("paper_order_id")
        for e in entries
        if e.get("decision") == "TRADE"
        and (e.get("risk_check") or {}).get("result") == "APPROVED"
        and e.get("paper_order_id")
    )
    duplicate_ids = {k: v for k, v in order_id_counts.items() if v > 1}

    if api_base:
        preflight = (parity or {}).get("live_preflight") or {}
        stale_orders = preflight.get("working_orders") or []
        stale_orders_note = None
    else:
        stale_orders = []
        stale_orders_note = "not checked — pass --api-base to read /status/live-preflight's working_orders"

    label_issue_count = (label_report or {}).get("summary", {}).get("issue_count", 0) if isinstance(label_report, dict) else "UNKNOWN"
    reconciler_unaudited = len((reconciler_report or {}).get("unaudited", []) or []) if isinstance(reconciler_report, dict) else 0
    plain_cancelled_suspects = sum(
        len((v or {}).get("suspect_rows", []) or [])
        for v in (plain_cancelled_report or {}).values()
        if isinstance(v, dict)
    ) if isinstance(plain_cancelled_report, dict) else 0

    overall_pass = (
        identity_holds_all
        and not orphans
        and not stale_orders
        and not duplicate_ids
        and not no_protective_bracket
        and (label_issue_count in (0, "UNKNOWN"))
        and reconciler_unaudited == 0
        and plain_cancelled_suspects == 0
    )

    return {
        "since": since or "no checkpoint set (first run) — reviewed the full journal directory",
        "instruments": instruments,
        "per_instrument": per_instrument,
        "totals": {
            "attempts": total_attempts,
            "fills": total_fills,
            "cancellations_no_fill": total_cancellations,
            "rejects_or_unclassified": total_rejects,
            "legitimately_open": total_open,
            "resolved": total_resolved,
            "orphans": len(orphans),
            "duplicate_order_identities": len(duplicate_ids),
            "stale_working_orders": len(stale_orders) if isinstance(stale_orders, list) else "UNKNOWN",
        },
        "orphans": orphans,
        "duplicate_order_ids": duplicate_ids,
        "stale_working_orders": stale_orders,
        "stale_working_orders_note": stale_orders_note,
        "no_protective_bracket_logged": no_protective_bracket,
        "signal_decision_label_audit": {
            "issue_count": label_issue_count,
            "issues_by_severity": (label_report or {}).get("summary", {}).get("issues_by_severity") if isinstance(label_report, dict) else None,
            "error": label_report.get("error") if isinstance(label_report, dict) else None,
        },
        "reconciler_touched_outcomes": {
            "touched_count": (reconciler_report or {}).get("summary", {}).get("total_touched") if isinstance(reconciler_report, dict) else None,
            "classified_count": (reconciler_report or {}).get("summary", {}).get("classified") if isinstance(reconciler_report, dict) else None,
            "unaudited_count": reconciler_unaudited,
            "error": reconciler_report.get("error") if isinstance(reconciler_report, dict) else None,
        },
        "plain_cancelled_marketability": {
            "mislabeled_fill_suspects": plain_cancelled_suspects,
            "error": plain_cancelled_report.get("error") if isinstance(plain_cancelled_report, dict) else None,
        },
        "accounting_identity_holds": identity_holds_all,
        "broker_journal_parity": parity,
        "pass": overall_pass,
    }


def cmd_daily(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(pcgit.repo_root() or REPO_ROOT)
    log_dir = Path(args.log_dir)

    github = _github_repo_section(repo_root)
    branches = _branches_worktrees_section(repo_root)
    evidence = _evidence_preservation_section(repo_root, github)
    deployed = _deployed_state_section(repo_root, log_dir)
    strategy_truth = _strategy_source_of_truth_section(repo_root)

    checkpoint = _read_json(DAILY_CHECKPOINT_PATH)
    since = args.since or (checkpoint or {}).get("last_run_at")
    trade_chain = _trade_chain_section(repo_root, log_dir, since, args.api_base)

    if not args.dry_run:
        _write_json(DAILY_CHECKPOINT_PATH, {"last_run_at": _now().isoformat()})

    return {
        "generated_at": _now().isoformat(),
        "github_repo": github,
        "branches_worktrees": branches,
        "evidence_preservation": evidence,
        "deployed_state": deployed,
        "strategy_source_of_truth": strategy_truth,
        "trade_chain": trade_chain,
    }


def _format_daily(report: dict[str, Any]) -> str:
    lines = ["DAILY RECONCILIATION + TRADE CHAIN INTEGRITY", "=" * 60, f"generated_at: {report['generated_at']}", ""]

    gh = report["github_repo"]
    lines.append("GITHUB / REPO")
    if gh["open_prs"] is not None:
        lines.append(f"  open PRs: {gh['current_open_prs_count']}  merged today: {len(gh['merged_today'])}  closed-unmerged today: {len(gh['closed_unmerged_today'])}")
        lines.append(f"  stale PRs (>=14d untouched): {len(gh['stale_prs_14d'])}")
    else:
        lines.append(f"  UNKNOWN — gh CLI unavailable or failed: {gh['open_prs_error']}")
    lines.append("")

    br = report["branches_worktrees"]
    lines.append("BRANCHES / WORKTREES")
    lines.append(f"  local main vs origin/main: {br['local_main_vs_origin_main']}")
    lines.append(f"  stale merged local branches: {br['stale_merged_local_branches']}")
    lines.append(f"  branches tracking deleted remotes: {br['branches_tracking_deleted_remotes']}")
    lines.append(f"  local-only branches: {br['local_only_branches']}")
    lines.append(f"  dirty worktrees: {[w['path'] for w in br['dirty_worktrees']]}")
    lines.append(f"  stash count: {br['stash_count']}")
    lines.append("")

    ev = report["evidence_preservation"]
    lines.append("EVIDENCE PRESERVATION")
    if ev["closed_unmerged_branches_missing_archive_tag"]:
        lines.append(f"  BLOCKER — {len(ev['closed_unmerged_branches_missing_archive_tag'])} closed-unmerged branch(es) with unique evidence, no archive tag:")
        for b in ev["closed_unmerged_branches_missing_archive_tag"]:
            lines.append(f"    - {b['branch']} ({b['pr']}) unique_commits={b['unique_commits_vs_main']}")
    else:
        lines.append("  none — every closed-unmerged branch with unique evidence has an archive tag (or gh is unavailable to check)")
    lines.append("")

    dep = report["deployed_state"]
    lines.append("DEPLOYED STATE")
    d = dep.get("deployed_state", {})
    lines.append(f"  status={d.get('status', 'UNKNOWN')}  {d.get('summary', '')}")
    lines.append(f"  entry_fill_model={dep.get('entry_fill_model')}  tolerance_ticks={dep.get('entry_tolerance_ticks')}  max_contracts={dep.get('max_contracts_hard_cap')}")
    lines.append("")

    st = report["strategy_source_of_truth"]
    lines.append("STRATEGY SOURCE OF TRUTH")
    if st["drift"]:
        lines.append(f"  DRIFT — {len(st['drift'])} strategy row(s) inconsistent with runtime enablement:")
        for d in st["drift"]:
            lines.append(f"    - {d['strategy']}: inventory={d['inventory_verdict']!r} but effective_status={d['effective_status']!r}")
    else:
        lines.append("  no drift detected between Strategy_Inventory.md and risk_rules.yaml enablement")
    if st["lane_keys_missing_from_inventory"]:
        lines.append(f"  lanes with no matching inventory row: {st['lane_keys_missing_from_inventory']}")
    lines.append("")

    tc = report["trade_chain"]
    lines.append("TRADE CHAIN INTEGRITY")
    if tc["pass"]:
        t = tc["totals"]
        lines.append("TRADE CHAIN: PASS")
        lines.append(f"{t['attempts']} attempts")
        lines.append(f"{t['fills']} fills")
        lines.append(f"{t['cancellations_no_fill']} no-fills")
        lines.append(f"{t['resolved']} resolved")
        lines.append(f"{t['legitimately_open']} legitimate opens")
        lines.append(f"{t['orphans']} orphans")
        lines.append(f"{t['stale_working_orders']} stale orders")
        lines.append(f"{t['duplicate_order_identities']} duplicate identities")
        lines.append(f"broker/journal parity {tc['broker_journal_parity']['status']}")
    else:
        lines.append("TRADE CHAIN: FAIL — details:")
        lines.append(json.dumps(tc, indent=2, default=str))
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit JSON instead of the text report")
    parser.add_argument("--log-dir", default="logs", help="journal/log directory to read runtime state from (default: logs)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ops.project_check", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("session-start", help="repo + runtime snapshot at the start of a work session")
    _add_common(p1)
    p1.set_defaults(func=cmd_session_start, formatter=_format_session_start)

    p2 = sub.add_parser("precommit", help="strictly read-only drift check before commit/push; fails closed")
    _add_common(p2)
    p2.set_defaults(func=cmd_precommit, formatter=_format_precommit)

    p3 = sub.add_parser("promotion", help="strategy promotion proof gate through the real executable path")
    p3.add_argument("--strategy", required=True, help="strategy name as it appears in setup.strategy / candidate_audit rows")
    p3.add_argument("--instrument", default="MNQ", help="instrument to evaluate (default: MNQ)")
    p3.add_argument("--candles", required=True, help="directory of .jsonl candle files, or a *.json ReplayManifest")
    p3.add_argument("--standalone-results", default=None, help="optional path to a standalone/research evidence JSON to diff against this real-path run")
    _add_common(p3)
    p3.set_defaults(func=cmd_promotion, formatter=_format_promotion)
    p3.set_defaults(log_dir="logs/promotion")

    p4 = sub.add_parser("daily", help="daily reconciliation + trade chain integrity (read-only)")
    p4.add_argument("--since", default=None, help="ISO timestamp; only journal rows at/after this are in scope for trade-chain integrity (default: last daily checkpoint, or full journal on first run)")
    p4.add_argument("--api-base", default=None, help="optional base URL (e.g. http://localhost:8000) to read /status/broker-account and /status/live-preflight for broker/journal parity; omitted by default (no network calls)")
    p4.add_argument("--dry-run", action="store_true", help="do not update the daily checkpoint file")
    _add_common(p4)
    p4.set_defaults(func=cmd_daily, formatter=_format_daily)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    report = args.func(args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print(args.formatter(report))

    if args.command == "precommit":
        return 0 if report.get("ok") else 1
    if args.command == "promotion":
        return 0 if report.get("ok") else 1
    if args.command == "daily":
        return 0 if report["trade_chain"].get("pass", False) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
