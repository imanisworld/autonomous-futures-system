"""Behavior-neutral release-diff gate for operational-only promotions.

Determines whether the file-level diff between the currently-live commit and
a candidate commit could only affect deploy/observability tooling -- never
trading decisions (signals, risk, sizing, exits, security, order placement).
Used by scripts/atomic_release.sh to allow promoting an operational-only
release without resetting the box's approved trading posture (SCHEDULE_MODE /
EXIT_MODE / HTF_DIRECTION_MODE), so a strategy-observation window doesn't
have to restart for a deploy-tooling or dashboard fix.

Default-deny throughout. Three layers, checked in order:

1. NEVER_SAFE_FILES -- files that constitute or verify the promotion
   mechanism itself (this gate, the promotion script, fingerprint/integrity
   tooling, the drift guard, the shadow-evidence writer that webhook/runner.py
   imports directly). These can never use the fast path, regardless of
   directory, so this gate can never approve a change to its own logic or to
   the mechanism that verifies what actually got deployed.
2. An explicit per-file allowlist (SAFE_OPERATIONAL_FILES) plus a small set
   of whole-directory prefixes confirmed to have zero import path into the
   live trading-decision code (strategy/risk/adaptive/execution/
   webhook.runner). New files are NOT safe by merely landing in scripts/ or
   ops/ -- they must be added here after review.
3. webhook/app.py specially: safe only if the diff is entirely contained
   inside a small, call-graph-checked set of dashboard/status *helper*
   functions (not routes, not anything security/broker/admin/mutation-
   related). Everything else in the file -- imports, module-level
   constants, decorators, middleware registration, route handlers, class
   defs, any function not on that list -- must be byte-for-byte identical.
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field

# Files that constitute or verify the promotion mechanism itself. Never
# eligible for the fast path, checked before anything else -- a change here
# must always go through the full baseline-reset path, never be
# self-approved by this same gate.
NEVER_SAFE_FILES = frozenset({
    "scripts/atomic_release.sh",
    "scripts/deploy_lock.sh",
    "scripts/install_timers.sh",
    "ops/behavior_neutral_gate.py",
    "ops/release_manifest.py",
    "ops/release_integrity.py",
    "ops/live_box_guard.py",
    "ops/runner_shadow_evidence.py",  # imported directly by webhook/runner.py
})

# Whole directories confirmed (via import-graph check) to have zero path
# into the live trading-decision code.
SAFE_DIRECTORY_PREFIXES = (
    "tests/", "docs/", "research/",
    # Separate manual stock/ETF paper-advisory lane. Not imported by the
    # futures webhook service and cannot place a futures order.
    "stocks_advisory/", "data/stocks_advisory_paper_proof/",
)
SAFE_EXACT_PATHS = frozenset({".gitignore"})

APP_PY_PATH = "webhook/app.py"

# Individual files under scripts/ and ops/, reviewed and confirmed to be
# standalone offline analytics/reporting/monitoring tools with no import
# path into strategy/risk/adaptive/execution/webhook.runner (verified via
# `grep` for `import ops.<mod>` / `import scripts.<mod>` outside tests/).
# Landing a new file in these directories does NOT make it safe -- it must
# be added here deliberately after the same review.
SAFE_OPERATIONAL_FILES = frozenset({
    "ops/audit_plain_cancelled.py",
    "ops/automation_evidence.py",
    "ops/build_honest_baseline.py",
    "ops/evidence_readiness.py",
    "ops/evidence_report.py",
    "ops/fill_realism.py",
    "ops/journal_label_audit.py",
    "ops/proof_30_mnq.py",
    "ops/project_check.py",
    "ops/reconciler_outcome_audit.py",
    "ops/strategy_intent_audit.py",
    "scripts/backup_proof_data.py",
    "scripts/continuation_scorecard.py",
    "scripts/csv_to_htf.py",
    "scripts/csv_to_replay.py",
    "scripts/doctor.py",
    "scripts/entry_detached_sweep_622d.py",
    "scripts/evidence_report.py",
    "scripts/feed_watchdog.py",
    "scripts/fill_realism_report.py",
    "scripts/health_digest.py",
    "scripts/ioc_baseline_622d_analysis.py",
    "scripts/journal_autopsy.py",
    "scripts/journal_label_audit.py",
    "scripts/mes_mnq_mechanical_research.py",
    "scripts/mfe_study.py",
    "scripts/missed_move_gate_sweep_622d.py",
    "scripts/mnq_5m_impulse_pullback_continuation_study.py",
    "scripts/mnq_5m_ipc_short_validation.py",
    "scripts/mnq_entry_refresh_study.py",
    "scripts/options_pr_audit.py",
    "scripts/orb_breakout_entry_study.py",
    "scripts/orb_entry_fill_ab_report.py",
    "scripts/orb_market_entry_study.py",
    "scripts/polygon_backfill.py",
    "scripts/polygon_stocks_backfill.py",
    "scripts/polygon_to_replay.py",
    "scripts/project_check.py",
    "scripts/proof_30_mnq.py",
    "scripts/reconciler_outcome_audit.py",
    "scripts/replay_comparison.py",
    "scripts/replay_single_alert.py",
    "scripts/retest_scorecard.py",
    "scripts/run_replay_batch.py",
    "scripts/run_stocks_advisory_paper.py",
    "scripts/run_stocks_csv_backtest.py",
    "scripts/runner_ab.py",
    "scripts/session_audit.py",
    "scripts/shadow_candidate_counts.py",
    "scripts/shadow_gate_choke_sweep_622d.py",
    "scripts/stocks_advisory_robustness_audit.py",
    "scripts/stop_rule_sweep.py",
    "scripts/strat_122_stop_study.py",
    "scripts/strategy_intent_audit.py",
    "scripts/structural_level_5m_study.py",
    "scripts/test_webhook.sh",
    "scripts/trend_modifier_candidate_isolation.py",
    "scripts/weekly_review.py",
})

# webhook/app.py: only these top-level helper functions may change freely.
# All are payload/formatting helpers reached FROM route handlers -- never
# routes themselves, never anything security/auth/rate-limit/broker/admin/
# mutation-related. Call-graph-checked: none of these is called by, or
# calls into logic that would let it silently defang, receive_alert,
# manual_action, the secret/rate-limit/IP-allowlist functions, the site
# access gate, _manual_close_all, or any middleware. Route handlers
# (dashboard, status_today, ...) stay in the protected residual specifically
# so a change here can never remove their call to a sanitizer.
APP_PY_SAFE_FUNCTIONS = frozenset({
    "_dashboard_payload",
    "_dashboard_init",
    "_render_dashboard",
    "_strategy_payload",
    "_gex_shadow_analysis_payload",
    "_instrument_breakdown",
    "_public_entry",
    "_explain_outcome",
    "_counter_items",
    "_load_committee_panel",
    "_format_generated_age",
    "_diag_slug",
    "_diagnostic",
    "_range_observe_diagnostic",
    "_active_configured_windows",
    "_hhmm_to_minutes",
    "_quality_gate_summary",
    "_timeframe_mismatch_state",
    "_shadow_feed_status",
    "_feed_window_active",
    "_diagnostics_payload",
    "_safe_live_preflight_status",
    "_execution_mode_info",
    "_escape",
})

# webhook/app.py: module-level constants confirmed to be plain, inert data
# (not executable logic, not a security/allowlist/secret value) that dashboard
# fixes legitimately need to touch. Verified via AST that each is a single
# ast.Constant string assignment, and via grep that it's only ever consumed
# by a safe helper (string .replace/.format for template rendering).
APP_PY_SAFE_CONSTANTS = frozenset({
    "_DASHBOARD_HTML",
})


@dataclass
class BehaviorNeutralResult:
    is_behavior_neutral: bool
    blocking_reasons: list[str] = field(default_factory=list)


def _node_span(node: ast.AST) -> tuple[int, int]:
    """Start/end line (1-indexed, inclusive) for a top-level node, including
    decorators for function defs -- ast.FunctionDef.lineno points at the
    `def` keyword, not the earliest decorator, so a decorator-only change
    (e.g. a different route path) would otherwise be invisible."""
    start = node.lineno
    for deco in getattr(node, "decorator_list", []) or []:
        start = min(start, deco.lineno)
    return start, node.end_lineno


def _is_safe_constant_assign(node: ast.AST) -> str | None:
    """Return the assigned name if `node` is `NAME = <plain string literal>`
    with NAME in APP_PY_SAFE_CONSTANTS, else None. Requires the *value* to
    still be a plain constant -- renaming a dynamic/computed expression onto
    a safe constant's name does not make it safe."""
    if not isinstance(node, ast.Assign) or len(node.targets) != 1:
        return None
    target = node.targets[0]
    if not isinstance(target, ast.Name) or target.id not in APP_PY_SAFE_CONSTANTS:
        return None
    if not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
        return None
    return target.id


def _residual_source(source: str) -> str:
    """Full module source with APP_PY_SAFE_FUNCTIONS bodies (and their
    decorators) and APP_PY_SAFE_CONSTANTS values replaced by opaque markers.
    Everything else -- imports, other module-level constants/expressions,
    class defs, every other function including its decorators, and the
    order/count of all of this -- is left intact, so any change to it
    changes the residual and fails the check.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    chunks: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in APP_PY_SAFE_FUNCTIONS:
            chunks.append(f"<<SAFE_FUNCTION:{node.name}>>")
            continue
        safe_const_name = _is_safe_constant_assign(node)
        if safe_const_name is not None:
            chunks.append(f"<<SAFE_CONSTANT:{safe_const_name}>>")
            continue
        start, end = _node_span(node)
        chunks.append("\n".join(lines[start - 1:end]))
    return "\n".join(chunks)


def app_py_change_is_safe(baseline_source: str, candidate_source: str) -> tuple[bool, list[str]]:
    """A webhook/app.py change is safe only if it's entirely contained
    inside the named safe helper functions. Fails closed on parse errors and
    on any change outside that allowlist -- new functions, changed imports,
    changed module-level constants, changed decorators/route paths, changed
    middleware, anything.
    """
    try:
        baseline_residual = _residual_source(baseline_source)
        candidate_residual = _residual_source(candidate_source)
    except SyntaxError as exc:
        return False, [f"{APP_PY_PATH}: could not parse a revision ({exc})"]

    if baseline_residual != candidate_residual:
        return False, [
            f"{APP_PY_PATH}: change touches code outside the explicit safe-function "
            f"allowlist ({sorted(APP_PY_SAFE_FUNCTIONS)})"
        ]
    return True, []


def evaluate_changed_files(
    changed_paths: list[str],
    *,
    app_py_sources: tuple[str, str] | None = None,
) -> BehaviorNeutralResult:
    """Pure logic: given the list of changed paths (and, if webhook/app.py is
    among them, its (baseline_source, candidate_source) pair), decide whether
    the release is behavior-neutral. No git or filesystem access.
    """
    reasons: list[str] = []
    for path in changed_paths:
        if path in NEVER_SAFE_FILES:
            reasons.append(f"{path}: never eligible for the operational fast path")
            continue
        if path == APP_PY_PATH:
            if app_py_sources is None:
                reasons.append(f"{APP_PY_PATH}: changed but sources not provided for review")
                continue
            ok, why = app_py_change_is_safe(*app_py_sources)
            if not ok:
                reasons.extend(why)
            continue
        if path in SAFE_EXACT_PATHS or path in SAFE_OPERATIONAL_FILES:
            continue
        if any(path.startswith(prefix) for prefix in SAFE_DIRECTORY_PREFIXES):
            continue
        reasons.append(f"{path}: not an explicitly reviewed operational-safe file")
    return BehaviorNeutralResult(is_behavior_neutral=(len(reasons) == 0), blocking_reasons=reasons)


def _git(repo_root: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        check=True, capture_output=True, text=True,
    ).stdout


def check_behavior_neutral(repo_root: str, baseline_sha: str, candidate_sha: str) -> BehaviorNeutralResult:
    """Git-backed entry point: diff two commits and evaluate the change set."""
    try:
        changed = [
            line for line in _git(repo_root, "diff", "--name-only", baseline_sha, candidate_sha).splitlines()
            if line.strip()
        ]
    except subprocess.CalledProcessError as exc:
        return BehaviorNeutralResult(
            is_behavior_neutral=False,
            blocking_reasons=[f"could not diff {baseline_sha}..{candidate_sha}: {exc}"],
        )

    app_py_sources = None
    if APP_PY_PATH in changed:
        try:
            baseline_src = _git(repo_root, "show", f"{baseline_sha}:{APP_PY_PATH}")
            candidate_src = _git(repo_root, "show", f"{candidate_sha}:{APP_PY_PATH}")
        except subprocess.CalledProcessError as exc:
            return BehaviorNeutralResult(
                is_behavior_neutral=False,
                blocking_reasons=[f"{APP_PY_PATH}: could not read one revision ({exc})"],
            )
        app_py_sources = (baseline_src, candidate_src)
    return evaluate_changed_files(changed, app_py_sources=app_py_sources)


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--baseline-sha", required=True)
    parser.add_argument("--candidate-sha", required=True)
    args = parser.parse_args()

    result = check_behavior_neutral(args.repo_root, args.baseline_sha, args.candidate_sha)
    print(json.dumps({
        "is_behavior_neutral": result.is_behavior_neutral,
        "blocking_reasons": result.blocking_reasons,
    }, indent=2))
    return 0 if result.is_behavior_neutral else 1


if __name__ == "__main__":
    raise SystemExit(main())
