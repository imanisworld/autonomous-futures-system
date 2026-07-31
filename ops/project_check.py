"""Single CLI entry point for the three repo/process safety routines.

    python -m ops.project_check session-start
    python -m ops.project_check precommit
    python -m ops.project_check promotion --strategy <name> [--instrument MNQ]
    python -m ops.project_check daily

Every subcommand is read-only over git/journal/config state (see the module
docstrings in ops.session_snapshot, ops.promotion_gate, and
ops.daily_reconciliation for exactly what each reads and never touches).
`precommit` additionally fails closed: it exits non-zero the moment repo
state looks ambiguous or drifted, rather than guessing.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.daily_reconciliation import build_daily_report
from ops.promotion_gate import build_promotion_report
from ops.session_snapshot import build_precommit_report, build_session_start_report


def _print_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)


def _summary_session_start(report: dict[str, Any]) -> str:
    repo = report["repo"]
    lines = [
        f"SESSION START | {report['generated_at']}",
        f"repo={repo['repo_root']} branch={repo['branch']} head={repo['head_sha'][:12]}",
        f"local main vs origin/main: {repo['main_sync']['relationship']}",
        f"worktree={report['current_worktree']}  worktrees={len(report['worktrees'])}  stashes={report['stash_count']}",
        f"dirty={len(report['dirty_tracked_files'])} staged={len(report['staged_files'])} untracked={len(report['untracked_files'])}",
        f"branches tracking deleted remotes: {report['branches_tracking_deleted_remotes'] or 'none'}",
        f"local-only branches: {report['local_only_branches'] or 'none'}",
        f"open PRs: {report['open_prs'].get('prs') if report['open_prs']['available'] else 'UNKNOWN (' + str(report['open_prs']['reason']) + ')'}",
        f"branch changed during check: {report['branch_changed_during_check']}",
        "",
        "RUNTIME SNAPSHOT",
        f"  deployed_release_sha={report['runtime_snapshot']['deployed_release_sha']}",
        f"  live_box_drift status={report['runtime_snapshot']['live_box_drift']['status']}",
        f"  active_runtime_overrides={report['runtime_snapshot']['active_runtime_overrides']}",
        f"  unpinned_runtime_overrides={report['runtime_snapshot']['unpinned_runtime_overrides']}",
        f"  automation jobs: {[(j['job'], j['status']) for j in report['runtime_snapshot']['automation_evidence']['jobs']]}",
        f"session_state_recorded={report['session_state_recorded']} at {report['session_state_path']}",
    ]
    return "\n".join(lines)


def _summary_precommit(report: dict[str, Any]) -> str:
    lines = [
        f"PRECOMMIT | {'OK' if report['ok'] else 'FAIL CLOSED'}",
        f"branch={report['current_branch']} head={report['current_head_sha'][:12]} worktree={report['current_worktree']}",
        f"session-start branch={report['session_start_branch']} worktree={report['session_start_worktree']}",
        f"changed={len(report['changed_files'])} staged={len(report['staged_files'])} untracked={len(report['untracked_files'])}",
    ]
    for reason in report["fail_reasons"]:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


def _summary_promotion(report: dict[str, Any]) -> str:
    perf = report["performance"]
    exe = report["execution"]
    cls = report["classification"]
    lines = [
        f"PROMOTION GATE | strategy={report['strategy']} instruments={report['instruments_scoped']}",
        f"filled={perf['filled_count']} wins={perf['wins']} losses={perf['losses']} net=${perf['net_pnl_dollars']} "
        f"PF={perf['profit_factor']} distinct_days={perf['distinct_days']}",
        f"accounting: approved={exe['candidates_approved']} filled={exe['filled']} cancelled={exe['cancellations']} "
        f"rejects/unclassified={exe['rejects_or_unclassified']} open={exe['legitimately_open']} "
        f"identity_holds={exe['accounting_identity_holds']}",
        f"unaudited reconciler rows={len(exe['unaudited_reconciler_touched_rows'])} "
        f"mislabeled-fill suspects={len(exe['mislabeled_fill_suspects'])}",
        f"CLASSIFICATION: {cls['classification']} ({cls['policy']})",
    ]
    for reason in cls["reasons"]:
        lines.append(f"  - {reason}")
    return "\n".join(lines)


def _summary_daily(report: dict[str, Any]) -> str:
    gh = report["github"]
    br = report["branches_worktrees"]
    ep = report["evidence_preservation"]
    ds = report["deployed_state"]
    lines = [
        f"DAILY RECONCILIATION | checkpoint={report['checkpoint']}",
        "",
        "GITHUB",
        f"  open={gh.get('open_pr_count', 'UNKNOWN')} opened_today={len(gh['opened_today']) if isinstance(gh.get('opened_today'), list) else 'UNKNOWN'} "
        f"merged_today={len(gh['merged_today']) if isinstance(gh.get('merged_today'), list) else 'UNKNOWN'} "
        f"closed_unmerged_today={len(gh['closed_unmerged_today']) if isinstance(gh.get('closed_unmerged_today'), list) else 'UNKNOWN'} "
        f"stale={len(gh['stale_open_prs']) if isinstance(gh.get('stale_open_prs'), list) else 'UNKNOWN'}",
        "BRANCHES/WORKTREES",
        f"  stale_merged_local={br['stale_merged_local_branches']}",
        f"  dirty_worktrees={[w['path'] for w in br['dirty_worktrees']]}",
        f"  main_sync={br['main_sync']['relationship']}  stash_count={br['stash_count']}",
        "EVIDENCE PRESERVATION",
        f"  blockers={ep.get('blockers') if ep.get('available') else ep.get('reason')}",
        "DEPLOYED STATE",
        f"  deployed_sha={ds['deployed_sha']} matches_intended={ds['deployed_sha_matches_intended_release']}",
        f"  active_paper_forward_lanes={ds['active_paper_forward_lanes']}",
        "",
        report["trade_chain_integrity"]["summary_line"],
    ]
    return "\n".join(lines)


def _cmd_session_start(args: argparse.Namespace) -> int:
    report = build_session_start_report(args.repo_root, log_dir=args.log_dir)
    print(_print_json(report) if args.json else _summary_session_start(report))
    return 0


def _cmd_precommit(args: argparse.Namespace) -> int:
    report = build_precommit_report(args.repo_root, log_dir=args.log_dir)
    print(_print_json(report) if args.json else _summary_precommit(report))
    return 0 if report["ok"] else 1


def _cmd_promotion(args: argparse.Namespace) -> int:
    report = build_promotion_report(
        journal_dir=args.journal_dir,
        strategy=args.strategy,
        instrument=args.instrument,
        research_evidence_path=args.research_evidence,
        overrides_doc=args.overrides_doc,
    )
    print(_print_json(report) if args.json else _summary_promotion(report))
    return 0


def _cmd_daily(args: argparse.Namespace) -> int:
    report = build_daily_report(
        repo_root=args.repo_root,
        journal_dir=args.journal_dir,
        log_dir=args.log_dir,
        overrides_doc=args.overrides_doc,
    )
    print(_print_json(report) if args.json else _summary_daily(report))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ops.project_check", description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--journal-dir", type=Path, default=ROOT / "logs")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "logs")
    parser.add_argument("--json", action="store_true", help="print raw JSON only (default output is already JSON; this flag suppresses the stderr human summary)")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("session-start", help="Session Safety + Runtime Snapshot: session start")
    sub.add_parser("precommit", help="Session Safety + Runtime Snapshot: precommit/prepush drift guard (read-only, fails closed)")

    promotion = sub.add_parser("promotion", help="Strategy Promotion Proof Gate")
    promotion.add_argument("--strategy", required=True, help="strategy name as recorded in setup.strategy in the journal")
    promotion.add_argument("--instrument", default=None, help="restrict to one instrument (default: both MNQ and MES)")
    promotion.add_argument("--research-evidence", type=Path, default=None, help="optional JSON file from the strategy's own research/replay scripts")
    promotion.add_argument("--overrides-doc", type=Path, default=None, help="path to docs/proof-operator-overrides.md (default: <repo-root>/docs/proof-operator-overrides.md)")

    daily = sub.add_parser("daily", help="Daily Reconciliation + Trade Chain Integrity")
    daily.add_argument("--overrides-doc", type=Path, default=None)

    args = parser.parse_args(argv)
    if getattr(args, "overrides_doc", None) is None and args.command in ("promotion", "daily"):
        args.overrides_doc = args.repo_root / "docs" / "proof-operator-overrides.md"

    handlers = {
        "session-start": _cmd_session_start,
        "precommit": _cmd_precommit,
        "promotion": _cmd_promotion,
        "daily": _cmd_daily,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
