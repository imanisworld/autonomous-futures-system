"""Exact-SHA fault-injection proof for promotion (U10).

The fault-injection (FI) suite lives in ``tests/fault_injection/``. This module

* names the formally defined scenarios (``REQUIRED_SCENARIOS``), derived from
  the suite's own test names, and the referenced-but-undefined ones
  (``UNDEFINED_SCENARIOS``) — those are reported, never invented;
* ``generate_manifest`` runs ONLY the FI suite on a clean checkout and writes a
  machine-readable manifest bound to the exact commit SHA and to a fingerprint
  of the FI suite files at that SHA, with a per-scenario result;
* ``verify_manifest`` refuses a manifest that is missing, malformed, for a
  different SHA (stale), for a different FI suite, missing a required
  scenario, or reporting any required scenario other than PASS. A general
  green pytest run cannot satisfy it: proof is per scenario, from the FI
  suite, at the qualified SHA.

No production fault injection. The suite runs against in-memory fakes only.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import tarfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

SCHEMA_VERSION = "1.0.0"
GENERATOR = "ops.fault_injection_gate"
SUITE_DIR = "tests/fault_injection"
SUITE_TIMEOUT_SECONDS = 300

# Formally defined FI scenarios (test function names in tests/fault_injection).
REQUIRED_SCENARIOS: tuple[str, ...] = (
    "FI-1", "FI-2", "FI-3", "FI-4", "FI-5", "FI-6", "FI-7", "FI-8", "FI-9",
    "FI-10", "FI-11", "FI-13", "FI-15", "FI-16", "FI-17", "FI-18", "JW", "LW",
)
# Referenced in planning but with no formal definition anywhere in the repo.
UNDEFINED_SCENARIOS: dict[str, str] = {
    "FI-12": "numbering gap: no test, doc or audit entry defines FI-12",
    "FI-14": "numbering gap: no test, doc or audit entry defines FI-14",
    "AUTH_TOKEN_LOSS": "no FI scenario defines broker auth/token loss",
    "ROLL_MISMATCH": "no FI scenario defines contract-roll mismatch (U8 unit tests cover routing only)",
}

_SCENARIO_RE = re.compile(r"^test_(fi\d+|jwc|jw|lw)", re.IGNORECASE)


class FaultInjectionGateError(RuntimeError):
    """FI proof cannot be generated safely."""


def scenario_for_test(name: str) -> Optional[str]:
    """Map an FI test function name to its scenario id (FI-5a → FI-5, JWC → JW)."""
    base = name.split("[", 1)[0]
    match = _SCENARIO_RE.match(base)
    if not match:
        return None
    token = match.group(1).lower()
    if token.startswith("fi"):
        return f"FI-{int(token[2:])}"
    return "JW" if token in ("jw", "jwc", "jwt") else "LW"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=False)


def _suite_files_at(root: Path, sha: str) -> list[str]:
    listing = _git(root, "ls-tree", "-r", "--name-only", sha, "--", SUITE_DIR)
    if listing.returncode != 0:
        raise FaultInjectionGateError(f"cannot list {SUITE_DIR} at {sha}: {listing.stderr.strip()}")
    files = sorted(p for p in listing.stdout.splitlines() if p.endswith(".py"))
    if not files:
        raise FaultInjectionGateError(f"no FI suite files at {sha}")
    return files


def suite_fingerprint_at(root: Path, sha: str) -> str:
    """SHA-256 over every FI suite Python file as committed at ``sha``."""
    digest = hashlib.sha256()
    for rel in _suite_files_at(root, sha):
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{rel}"], capture_output=True, check=False
        )
        if blob.returncode != 0:
            raise FaultInjectionGateError(f"cannot read {rel} at {sha}")
        digest.update(rel.encode() + b"\0" + hashlib.sha256(blob.stdout).hexdigest().encode() + b"\n")
    return digest.hexdigest()


def discovered_scenarios_at(root: Path, sha: str) -> set[str]:
    """Discover scenario ids from committed FI test functions at an exact SHA."""
    found: set[str] = set()
    for rel in _suite_files_at(root, sha):
        if not Path(rel).name.startswith("test_"):
            continue
        shown = _git(root, "show", f"{sha}:{rel}")
        if shown.returncode != 0:
            raise FaultInjectionGateError(f"cannot read {rel} at {sha}")
        try:
            tree = ast.parse(shown.stdout)
        except SyntaxError as exc:
            raise FaultInjectionGateError(f"cannot parse {rel} at {sha}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                scenario = scenario_for_test(node.name)
                if scenario:
                    found.add(scenario)
    return found


def committed_fi_tests_at(root: Path, sha: str) -> set[str]:
    """Mapped FI test function names committed at the requested SHA."""
    tests: set[str] = set()
    for rel in _suite_files_at(root, sha):
        if not Path(rel).name.startswith("test_"):
            continue
        shown = _git(root, "show", f"{sha}:{rel}")
        if shown.returncode != 0:
            raise FaultInjectionGateError(f"cannot read {rel} at {sha}")
        try:
            tree = ast.parse(shown.stdout)
        except SyntaxError as exc:
            raise FaultInjectionGateError(f"cannot parse {rel} at {sha}: {exc}") from exc
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and scenario_for_test(node.name):
                tests.add(node.name)
    return tests


def _verify_archived_suite(root: Path, sha: str, checkout: Path) -> None:
    """Prove git archive did not omit or alter any committed FI suite file."""
    expected = _suite_files_at(root, sha)
    suite_root = checkout / SUITE_DIR
    actual = sorted(
        path.relative_to(checkout).as_posix()
        for path in suite_root.rglob("*.py")
        if path.is_file()
    ) if suite_root.is_dir() else []
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise FaultInjectionGateError(
            f"exact-SHA archive FI suite mismatch: missing={missing}, extra={extra}"
        )
    for rel in expected:
        shown = subprocess.run(
            ["git", "-C", str(root), "show", f"{sha}:{rel}"],
            capture_output=True,
            check=False,
        )
        if shown.returncode != 0 or (checkout / rel).read_bytes() != shown.stdout:
            raise FaultInjectionGateError(f"exact-SHA archive changed FI suite file {rel}")


def _exact_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def scenario_results(testcases: Iterable[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """Fold (test name, outcome) pairs into per-scenario results."""
    out: dict[str, dict[str, Any]] = {}
    for name, outcome in testcases:
        scenario = scenario_for_test(name)
        if scenario is None:
            continue
        entry = out.setdefault(scenario, {"passed": 0, "failed": 0, "skipped": 0, "tests": []})
        entry[outcome] += 1
        entry["tests"].append({"name": name, "outcome": outcome})
    for entry in out.values():
        entry["result"] = (
            "FAIL" if entry["failed"] else "NOT_PROVEN" if entry["skipped"] or not entry["passed"] else "PASS"
        )
    return out


def _junit_cases(path: Path) -> list[tuple[str, str]]:
    cases = []
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "failed"
        elif case.find("skipped") is not None:
            outcome = "skipped"  # includes xfail: a known defect is not proof
        else:
            outcome = "passed"
        cases.append((case.get("name") or "", outcome))
    return cases


def run_suite_at(
    root: Path,
    sha: str,
    *,
    python: str = sys.executable,
    timeout_seconds: int = SUITE_TIMEOUT_SECONDS,
) -> tuple[int, list[tuple[str, str]]]:
    """Run the committed FI suite from an isolated exact-SHA archive."""
    if not _exact_nonnegative_int(timeout_seconds) or timeout_seconds <= 0:
        raise FaultInjectionGateError("FI suite timeout must be a positive integer")
    archived = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", sha],
        capture_output=True, check=False,
    )
    if archived.returncode != 0:
        raise FaultInjectionGateError(f"cannot archive qualified SHA {sha}")
    with tempfile.TemporaryDirectory(prefix="afs-fi-exact-") as tmp:
        checkout = Path(tmp) / "checkout"
        checkout.mkdir()
        try:
            with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
                archive.extractall(checkout, filter="data")
        except (tarfile.TarError, OSError) as exc:
            raise FaultInjectionGateError(f"cannot materialize qualified SHA {sha}: {exc}") from exc

        _verify_archived_suite(Path(root), sha, checkout)
        expected_tests = committed_fi_tests_at(Path(root), sha)
        config = checkout / ".fi-pytest.ini"
        config.write_text("[pytest]\naddopts =\n", encoding="utf-8")
        junit = Path(tmp) / "fi.xml"
        env = os.environ.copy()
        for key in tuple(env):
            if key.startswith("PYTEST_"):
                env.pop(key, None)
        env.pop("PYTHONPATH", None)
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        try:
            proc = subprocess.run(
                [
                    python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                    "-c", str(config), SUITE_DIR, f"--junitxml={junit}",
                ],
                cwd=checkout,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise FaultInjectionGateError(
                f"exact-SHA FI suite exceeded {timeout_seconds}s timeout"
            ) from exc
        if not junit.is_file():
            raise FaultInjectionGateError(
                f"exact-SHA FI suite produced no results: {proc.stderr[-500:]}"
            )
        cases = _junit_cases(junit)
        executed = {
            name.split("[", 1)[0]
            for name, _ in cases
            if scenario_for_test(name) is not None
        }
        if executed != expected_tests:
            missing = sorted(expected_tests - executed)
            extra = sorted(executed - expected_tests)
            raise FaultInjectionGateError(
                f"exact-SHA FI test collection mismatch: missing={missing}, extra={extra}"
            )
    return proc.returncode, cases


def _safe_output_path(root: Path, out: Path) -> Path:
    """Require generated proof to stay in-repo without overwriting tracked files."""
    root = root.resolve()
    target = out.resolve()
    try:
        rel = target.relative_to(root)
    except ValueError as exc:
        raise FaultInjectionGateError("FI proof output must resolve inside the repository root") from exc
    if rel.parts and rel.parts[0] == ".git":
        raise FaultInjectionGateError("FI proof output may not be written inside .git")
    tracked = _git(root, "ls-files", "--error-unmatch", "--", rel.as_posix())
    if tracked.returncode == 0:
        raise FaultInjectionGateError("FI proof output may not overwrite a tracked repository file")
    return target

def generate_manifest(root: Path, *, python: str = sys.executable) -> dict[str, Any]:
    """Run only the FI suite on a clean checkout; return the bound manifest."""
    root = Path(root)
    head = _git(root, "rev-parse", "HEAD")
    sha = head.stdout.strip().lower()
    if head.returncode != 0 or len(sha) != 40:
        raise FaultInjectionGateError("cannot resolve HEAD")
    dirty = _git(root, "status", "--porcelain", "--untracked-files=all")
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise FaultInjectionGateError(
            "checkout is not clean (tracked or untracked files present); FI proof needs an exact SHA"
        )
    discovered = discovered_scenarios_at(root, sha)
    if discovered != set(REQUIRED_SCENARIOS):
        raise FaultInjectionGateError(
            "FI scenario inventory drift: "
            f"committed suite={sorted(discovered)}, required={sorted(REQUIRED_SCENARIOS)}"
        )
    exit_code, cases = run_suite_at(root, sha, python=python)
    results = scenario_results(cases)
    return {
        "schema_version": SCHEMA_VERSION,
        "generator": GENERATOR,
        "code_sha": sha,
        "suite_dir": SUITE_DIR,
        "suite_fingerprint": suite_fingerprint_at(root, sha),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "pytest_exit_code": exit_code,
        "required_scenarios": list(REQUIRED_SCENARIOS),
        "discovered_scenarios": sorted(discovered),
        "undefined_scenarios": dict(UNDEFINED_SCENARIOS),
        "scenarios": results,
        "overall": "PASS"
        if exit_code == 0 and all(results.get(s, {}).get("result") == "PASS" for s in REQUIRED_SCENARIOS)
        else "FAIL",
    }


def verify_manifest(
    root: Path,
    manifest: Any,
    *,
    code_sha: Optional[str],
    required: Iterable[str] = REQUIRED_SCENARIOS,
) -> list[str]:
    """Blockers for using ``manifest`` as FI proof of ``code_sha``."""
    if not isinstance(manifest, dict):
        return ["fault-injection manifest missing or not an object"]
    blockers: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("generator") != GENERATOR:
        blockers.append("fault-injection manifest was not produced by the FI gate generator")
    required = tuple(required)
    required_set = set(required)
    sha = str(code_sha or "").strip().lower()
    if len(sha) != 40:
        blockers.append("fault-injection proof needs the exact code SHA being qualified")
    elif str(manifest.get("code_sha") or "").lower() != sha:
        blockers.append(
            f"fault-injection manifest is stale: proves {manifest.get('code_sha')!r}, "
            f"qualified code is {sha!r}"
        )
    else:
        try:
            expected = suite_fingerprint_at(Path(root), sha)
            discovered = discovered_scenarios_at(Path(root), sha)
        except FaultInjectionGateError as exc:
            blockers.append(f"cannot inspect the FI suite at {sha}: {exc}")
        else:
            if manifest.get("suite_fingerprint") != expected:
                blockers.append("fault-injection manifest does not match the FI suite at the qualified SHA")
            if discovered != required_set:
                blockers.append(
                    "fault-injection scenario inventory drift at qualified SHA: "
                    f"suite={sorted(discovered)}, required={sorted(required_set)}"
                )
            manifest_required = manifest.get("required_scenarios")
            if not isinstance(manifest_required, list) or set(manifest_required) != required_set:
                blockers.append("fault-injection manifest required_scenarios does not match the gate")
            manifest_discovered = manifest.get("discovered_scenarios")
            if not isinstance(manifest_discovered, list) or set(manifest_discovered) != discovered:
                blockers.append("fault-injection manifest discovered_scenarios does not match the suite")
    scenarios = manifest.get("scenarios") if isinstance(manifest.get("scenarios"), dict) else {}
    for scenario in required:
        entry = scenarios.get(scenario)
        if not isinstance(entry, dict):
            blockers.append(f"fault-injection scenario {scenario} has no result")
            continue
        counts = {name: entry.get(name) for name in ("passed", "failed", "skipped")}
        if not all(_exact_nonnegative_int(value) for value in counts.values()):
            blockers.append(f"fault-injection scenario {scenario} has invalid non-integer counts")
            continue
        tests = entry.get("tests")
        if not isinstance(tests, list) or sum(counts.values()) != len(tests):
            blockers.append(f"fault-injection scenario {scenario} count/test detail mismatch")
        if entry.get("result") != "PASS" or counts["passed"] <= 0 or counts["failed"] or counts["skipped"]:
            blockers.append(f"fault-injection scenario {scenario} is {entry.get('result')!r}, not PASS")
    pytest_exit = manifest.get("pytest_exit_code")
    if not _exact_nonnegative_int(pytest_exit) or pytest_exit != 0:
        blockers.append("fault-injection suite run did not exit cleanly")
    if not blockers and len(sha) == 40:
        try:
            exact_exit, exact_cases = run_suite_at(Path(root), sha)
        except FaultInjectionGateError as exc:
            blockers.append(f"cannot mechanically rerun FI suite at {sha}: {exc}")
        else:
            exact_results = scenario_results(exact_cases)
            if exact_exit != 0:
                blockers.append("mechanical exact-SHA FI suite rerun did not exit cleanly")
            for scenario in required:
                if exact_results.get(scenario, {}).get("result") != "PASS":
                    blockers.append(
                        f"mechanical exact-SHA fault-injection scenario {scenario} is "
                        f"{exact_results.get(scenario, {}).get('result')!r}, not PASS"
                    )
    return blockers


def load_manifest(path: Path) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate or verify exact-SHA fault-injection proof.")
    sub = parser.add_subparsers(dest="command", required=True)
    gen = sub.add_parser("generate")
    gen.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    gen.add_argument("--out", type=Path, required=True)
    ver = sub.add_parser("verify")
    ver.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    ver.add_argument("--manifest", type=Path, required=True)
    ver.add_argument("--code-sha", required=True)
    args = parser.parse_args(argv)
    if args.command == "generate":
        try:
            target = _safe_output_path(args.repo_root, args.out)
            manifest = generate_manifest(args.repo_root)
        except FaultInjectionGateError as exc:
            print(f"FI PROOF BLOCKED: {exc}", file=sys.stderr)
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps({"overall": manifest["overall"], "code_sha": manifest["code_sha"]}))
        return 0 if manifest["overall"] == "PASS" else 1
    blockers = verify_manifest(args.repo_root, load_manifest(args.manifest), code_sha=args.code_sha)
    print(json.dumps({"ok": not blockers, "blockers": blockers}, indent=2))
    return 0 if not blockers else 1


if __name__ == "__main__":
    raise SystemExit(main())
