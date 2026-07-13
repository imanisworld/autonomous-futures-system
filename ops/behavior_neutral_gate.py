"""Behavior-neutral release-diff gate for operational-only promotions.

Determines whether the file-level diff between the currently-live commit and
a candidate commit could only affect deploy/observability tooling -- never
trading decisions (signals, risk, sizing, exits, security, order placement).
Used by scripts/atomic_release.sh to allow promoting an operational-only
release without resetting the box's approved trading posture (SCHEDULE_MODE /
EXIT_MODE / HTF_DIRECTION_MODE), so a strategy-observation window doesn't
have to restart for a deploy-tooling or dashboard fix.

Default-deny: any changed path that isn't under an explicitly safe prefix,
and isn't the one specially-handled file (webhook/app.py, checked at the
function level), fails the check.
"""
from __future__ import annotations

import ast
import subprocess
from dataclasses import dataclass, field

SAFE_PATH_PREFIXES = (
    "scripts/", "ops/", "tests/", "docs/", "research/",
    # Separate manual stock/ETF paper-advisory lane. It is not imported by the
    # futures webhook service and cannot place a futures order.
    "stocks_advisory/", "data/stocks_advisory_paper_proof/",
)
SAFE_EXACT_PATHS = frozenset({".gitignore"})

APP_PY_PATH = "webhook/app.py"

# Functions in webhook/app.py that can influence alert decisions, order
# placement, position mutation, or request security. Their source must be
# byte-for-byte identical between the live release and a candidate for the
# candidate to qualify as behavior-neutral. Everything else in that file
# (dashboard/status rendering, diagnostics) may change freely.
APP_PY_PROTECTED_FUNCTIONS = frozenset({
    "receive_alert",
    "manual_action",
    "_handle_alert_blocking",
    "_process_alert_async",
    "_verify_webhook_secret",
    "_resolve_inbound_secret",
    "_accepted_webhook_secrets",
    "_configured_webhook_secret",
    "_allow_secret_in_query",
    "_enforce_rate_limit",
    "_enforce_ip_allowlist",
    "_allowed_tradingview_ips",
    "_client_ip",
    "_rate_bucket_for_path",
    "_evict_stale_rate_buckets",
    "_manual_close_all",
    "_broker_status",
    "_record_latest_webhook",
    "_payload_to_dict",
    "_security_headers_middleware",
    "_site_access_gate_middleware",
    "_public_demo_gate_middleware",
    "_rate_limit_middleware",
    "_lifespan",
})


@dataclass
class BehaviorNeutralResult:
    is_behavior_neutral: bool
    blocking_reasons: list[str] = field(default_factory=list)


def top_level_defs(source: str) -> dict[str, str]:
    """Map top-level function/class name -> exact source text."""
    tree = ast.parse(source)
    defs: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            segment = ast.get_source_segment(source, node)
            if segment is not None:
                defs[node.name] = segment
    return defs


def app_py_change_is_safe(baseline_source: str, candidate_source: str) -> tuple[bool, list[str]]:
    """Check a webhook/app.py change against the protected-function allowlist.

    Fails closed: any new or removed top-level definition is treated as
    unvetted and blocks the fast path, even if its name isn't in the
    protected set -- new code hasn't been reviewed under this gate before.
    """
    reasons: list[str] = []
    baseline_defs = top_level_defs(baseline_source)
    candidate_defs = top_level_defs(candidate_source)

    added = set(candidate_defs) - set(baseline_defs)
    removed = set(baseline_defs) - set(candidate_defs)
    if added:
        reasons.append(f"{APP_PY_PATH}: new top-level definitions not vetted: {sorted(added)}")
    if removed:
        reasons.append(f"{APP_PY_PATH}: removed top-level definitions: {sorted(removed)}")

    for name in APP_PY_PROTECTED_FUNCTIONS:
        in_baseline = name in baseline_defs
        in_candidate = name in candidate_defs
        if in_baseline and in_candidate:
            if baseline_defs[name] != candidate_defs[name]:
                reasons.append(f"{APP_PY_PATH}: protected function changed: {name}")
        elif in_baseline != in_candidate:
            reasons.append(f"{APP_PY_PATH}: protected function added/removed: {name}")

    return (len(reasons) == 0), reasons


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
        if path == APP_PY_PATH:
            if app_py_sources is None:
                reasons.append(f"{APP_PY_PATH}: changed but sources not provided for review")
                continue
            ok, why = app_py_change_is_safe(*app_py_sources)
            if not ok:
                reasons.extend(why)
            continue
        if path in SAFE_EXACT_PATHS or any(path.startswith(prefix) for prefix in SAFE_PATH_PREFIXES):
            continue
        reasons.append(f"{path}: not an operational-safe path")
    return BehaviorNeutralResult(is_behavior_neutral=(len(reasons) == 0), blocking_reasons=reasons)


def _git(repo_root: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo_root, *args],
        check=True, capture_output=True, text=True,
    ).stdout


def check_behavior_neutral(repo_root: str, baseline_sha: str, candidate_sha: str) -> BehaviorNeutralResult:
    """Git-backed entry point: diff two commits and evaluate the change set."""
    changed = [
        line for line in _git(repo_root, "diff", "--name-only", baseline_sha, candidate_sha).splitlines()
        if line.strip()
    ]
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
