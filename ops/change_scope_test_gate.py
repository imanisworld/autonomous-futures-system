"""Change-scope -> required-tests advisory (Session Safety addendum).

Not a new routine -- a smaller check folded into Session Safety + Runtime
Snapshot. Given a set of changed file paths, classifies which risk categories
were touched (strategy/risk/execution/replay/webhook/docs) and whether the
SAME diff also touched a plausible test file for that category. This is a
proxy signal, not proof the right test was *run*: it catches the failure mode
where execution/risk/strategy code changes but the diff carries no matching
test file at all. Read-only; changes nothing, blocks nothing by itself -- it
only reports a status a human or CI step can act on.
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Any

# (path prefix, category name, required-test glob patterns, human proof description)
CATEGORY_RULES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "strategy/", "strategy",
        ("tests/test_*strat*.py", "tests/test_*detector*.py", "tests/test_signal_engine*.py", "tests/test_*replay*parity*.py", "tests/test_*parity*.py"),
        "detector/unit tests + replay parity test required",
    ),
    (
        "risk/", "risk",
        ("tests/test_risk_engine*.py", "tests/test_*risk*.py", "tests/test_*rejection*.py"),
        "RiskEngine tests + rejection-path tests required",
    ),
    (
        "execution/", "execution",
        ("tests/test_*broker*.py", "tests/test_*order*.py", "tests/test_*fill*.py", "tests/test_execution_*.py"),
        "broker/order lifecycle tests + fill-model tests required",
    ),
    (
        "replay/", "replay",
        ("tests/test_*replay*.py", "tests/test_*parity*.py"),
        "replay/live parity tests required",
    ),
    (
        "research/replay_", "replay",
        ("tests/test_*replay*.py", "tests/test_*parity*.py"),
        "replay/live parity tests required",
    ),
    (
        "webhook/", "webhook",
        ("tests/test_webhook*.py", "tests/test_runner*.py", "tests/test_*payload*.py", "tests/test_*routing*.py"),
        "payload validation + routing tests required",
    ),
)

_TEST_PATH_PREFIX = "tests/"


def _normalize(path: str) -> str:
    return path.replace("\\", "/").lstrip("./")


def classify_change_scope(changed_files: list[str]) -> dict[str, Any]:
    """Return the risk categories touched by `changed_files` and, for each,
    the test globs required to consider it covered."""
    changed = [_normalize(p) for p in changed_files]
    categories: dict[str, dict[str, Any]] = {}
    for path in changed:
        if path.startswith(_TEST_PATH_PREFIX):
            continue
        for prefix, category, globs, description in CATEGORY_RULES:
            if path.startswith(prefix):
                entry = categories.setdefault(
                    category, {"required_test_globs": list(globs), "description": description, "changed_files": []}
                )
                entry["changed_files"].append(path)
                break

    non_test_changed = [p for p in changed if not p.startswith(_TEST_PATH_PREFIX)]
    docs_only = bool(changed) and all(
        p.startswith("docs/") or p.startswith(_TEST_PATH_PREFIX) or p == "AGENT_HANDOFF.md"
        for p in changed
    )

    return {
        "changed_categories": sorted(categories.keys()),
        "category_detail": categories,
        "docs_only": docs_only,
        "non_test_changed_count": len(non_test_changed),
    }


def evaluate_test_coverage(changed_files: list[str]) -> dict[str, Any]:
    """Compare required categories against test files present in the SAME
    diff. status:
      - NOT_APPLICABLE: no files changed, or every changed file is docs/tests
      - PASS: every touched risk category has a matching test file in the diff
      - FAIL: at least one touched risk category has zero matching test files
    This never inspects whether a test actually ran or passed (that is CI's
    job) -- only whether the diff plausibly carries the right test file.
    """
    scope = classify_change_scope(changed_files)
    changed = [_normalize(p) for p in changed_files]
    test_files = [p for p in changed if p.startswith(_TEST_PATH_PREFIX)]

    if not changed or (scope["docs_only"] and not scope["changed_categories"]):
        return {
            "status": "NOT_APPLICABLE",
            "changed_categories": [],
            "missing_required_tests": [],
            "detail": "no runtime-relevant files changed (docs/tests only or empty diff)",
        }

    missing: list[str] = []
    for category, info in scope["category_detail"].items():
        matched = any(
            fnmatch.fnmatch(test_file, pattern)
            for pattern in info["required_test_globs"]
            for test_file in test_files
        )
        if not matched:
            missing.append(category)

    status = "FAIL" if missing else "PASS"
    return {
        "status": status,
        "changed_categories": scope["changed_categories"],
        "missing_required_tests": missing,
        "detail": (
            "all touched categories have a matching test file in this diff" if status == "PASS"
            else f"no matching test file in this diff for: {', '.join(missing)}"
        ),
    }


def _git_diff_files(root: Path, base_ref: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=str(root), check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main", help="base ref to diff against (default origin/main)")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    changed = _git_diff_files(root, args.base)
    result = evaluate_test_coverage(changed)

    print(f"Changed files vs {args.base}: {len(changed)}")
    print(f"Status: {result['status']}")
    print(f"Categories touched: {result['changed_categories'] or 'none'}")
    if result["missing_required_tests"]:
        print(f"Missing required tests for: {', '.join(result['missing_required_tests'])}")
    print(result["detail"])
    return 1 if result["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
