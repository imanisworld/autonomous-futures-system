"""AFS Experiment Runner — fail-closed execution against approved experiment specs.

Governance / research measurement only. This module does not promote strategies,
modify production or paper configuration, merge, deploy, or submit broker orders.

Contract basis:
  docs/research-experiment-spec.schema.json
  docs/research-experiment-spec-2026-09-25.md
"""
from __future__ import annotations

import hashlib
import json
import platform
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator

RUNNER_VERSION = "1.0.0"
SCHEMA_REL = "docs/research-experiment-spec.schema.json"
SPECS_DIR_REL = "docs/research-experiment-specs"
LEDGER_REL = "docs/research-trial-ledger.jsonl"
EXAMPLES_SUBDIR = "examples"

EXECUTABLE_STATUS = "APPROVED"
LIVE_STATUSES = frozenset({"DRAFT", "APPROVED", "REVOKED", "SUPERSEDED"})

# Optional adapters keyed by setup_type. Empty by design until a real approved
# experiment registers one in a follow-up change. Missing adapter => BLOCKED.
ExecutionAdapter = Callable[["ExperimentContext"], "ArmRawResult"]
EXECUTION_ADAPTERS: dict[str, ExecutionAdapter] = {}


@dataclass
class CheckResult:
    name: str
    passed: bool
    evidence: str


@dataclass
class ArmRawResult:
    """Normalized population members plus optional raw blobs for an arm."""

    arm: str
    commit_sha: str | None
    members: list[dict[str, Any]]
    raw: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ExperimentContext:
    root: Path
    spec: dict[str, Any]
    spec_path: Path
    spec_hash: str
    runner_version: str = RUNNER_VERSION


@dataclass
class RunnerReport:
    status: str  # VALID | INVALID | BLOCKED
    experiment_id: str | None
    trial_id: str | None
    baseline_sha: str | None
    candidate_sha: str | None
    population: str | None
    changed_variables: list[dict[str, Any]]
    held_constant: list[str]
    integrity_checks: list[CheckResult]
    baseline_metrics: dict[str, Any] | None
    candidate_metrics: dict[str, Any] | None
    delta: dict[str, Any] | None
    coverage_funnel: dict[str, Any] | None
    concentration: dict[str, Any] | None
    evidence_classification: dict[str, list[str]]
    result: str  # SUPPORTED... | NOT SUPPORTED | INCONCLUSIVE | INVALID EXPERIMENT | BLOCKED
    artifacts: dict[str, Any]
    qa_handoff: list[str]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["integrity_checks"] = [
            {"name": c.name, "result": "PASS" if c.passed else "FAIL", "evidence": c.evidence}
            for c in self.integrity_checks
        ]
        return payload


def repo_root(start: Path | None = None) -> Path:
    here = (start or Path(__file__).resolve()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / SCHEMA_REL).exists() and (candidate / LEDGER_REL).exists():
            return candidate
    raise FileNotFoundError("cannot locate repository root with experiment schema + trial ledger")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_schema(root: Path) -> dict[str, Any]:
    schema = json.loads((root / SCHEMA_REL).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def load_spec(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: experiment spec must be a JSON object")
    return payload


def ledger_first_rows(root: Path) -> dict[str, dict[str, Any]]:
    first: dict[str, dict[str, Any]] = {}
    path = root / LEDGER_REL
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip():
            continue
        row = json.loads(raw)
        first.setdefault(row["trial_id"], row)
    return first


def discover_specs(
    root: Path,
    *,
    status: str | None = EXECUTABLE_STATUS,
    include_examples: bool = False,
) -> list[Path]:
    specs_dir = root / SPECS_DIR_REL
    if not specs_dir.exists():
        return []
    found: list[Path] = []
    for path in sorted(specs_dir.rglob("*.json")):
        is_example = EXAMPLES_SUBDIR in path.parts
        if is_example and not include_examples:
            continue
        if not is_example and path.parent != specs_dir:
            continue
        spec = load_spec(path)
        if status is not None and spec.get("status") != status:
            continue
        found.append(path)
    return found


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )


def git_sha_resolves(root: Path, sha: str | None) -> tuple[bool, str]:
    if not sha:
        return False, "commit_sha is null"
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        return False, "commit_sha is not 40-hex"
    probe = _git(root, "cat-file", "-e", f"{sha}^{{commit}}")
    if probe.returncode != 0:
        return False, f"commit {sha} is not resolvable in this repository"
    return True, f"commit {sha} resolves"


def schema_validate(spec: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(validator.iter_errors(spec), key=lambda e: list(e.path))
    ]


def validate_linkage(
    root: Path,
    spec: dict[str, Any],
    *,
    first_rows: Mapping[str, dict[str, Any]] | None = None,
) -> list[CheckResult]:
    checks: list[CheckResult] = []
    status = spec.get("status")
    if status == "EXAMPLE":
        checks.append(
            CheckResult(
                "ledger_linkage",
                True,
                "EXAMPLE specs are exempt from ledger linkage",
            )
        )
        return checks

    rows = first_rows if first_rows is not None else ledger_first_rows(root)
    trial_id = spec.get("trial_id")
    if trial_id not in rows:
        checks.append(CheckResult("ledger_linkage", False, f"trial_id {trial_id} missing from ledger"))
        return checks

    row = rows[trial_id]
    if row.get("event") not in {"PLANNED", "ADOPTED"}:
        checks.append(
            CheckResult(
                "ledger_first_event",
                False,
                f"first event must be PLANNED or ADOPTED, got {row.get('event')!r}",
            )
        )
    else:
        checks.append(
            CheckResult("ledger_first_event", True, f"first event={row.get('event')}")
        )

    if spec.get("prereg_path") != row.get("prereg_path"):
        checks.append(
            CheckResult(
                "prereg_path_match",
                False,
                f"spec prereg_path {spec.get('prereg_path')!r} != ledger {row.get('prereg_path')!r}",
            )
        )
    else:
        checks.append(CheckResult("prereg_path_match", True, "matches ledger frozen prereg_path"))

    if spec.get("population") != row.get("population"):
        checks.append(
            CheckResult(
                "population_match",
                False,
                "spec population does not match ledger frozen population",
            )
        )
    else:
        checks.append(CheckResult("population_match", True, "matches ledger frozen population"))

    prereg = root / str(spec.get("prereg_path", ""))
    checks.append(
        CheckResult(
            "prereg_exists",
            prereg.is_file(),
            f"{prereg.as_posix()} {'exists' if prereg.is_file() else 'missing'}",
        )
    )

    expected_evidence = f"docs/research-evidence/{trial_id}/"
    checks.append(
        CheckResult(
            "evidence_path",
            spec.get("evidence_path") == expected_evidence,
            f"expected {expected_evidence}, got {spec.get('evidence_path')!r}",
        )
    )

    variant = row.get("variant_set") or {}
    if isinstance(variant, dict) and int(variant.get("count") or 0) > 1:
        ok = spec.get("variant_manifest") == variant.get("manifest")
        checks.append(
            CheckResult(
                "variant_manifest",
                ok,
                f"expected {variant.get('manifest')!r}, got {spec.get('variant_manifest')!r}",
            )
        )
    else:
        checks.append(CheckResult("variant_manifest", True, "single-variant trial; manifest optional"))

    return checks


def validate_dataset_identity(spec: dict[str, Any], root: Path) -> list[CheckResult]:
    data = spec.get("data") or {}
    checks: list[CheckResult] = []
    source = data.get("source")
    dataset_id = data.get("dataset_id")
    window = data.get("window") or {}
    dataset_hash = data.get("dataset_hash")

    identity_ok = bool(source and dataset_id and window.get("start") and window.get("end"))
    checks.append(
        CheckResult(
            "dataset_identity_fields",
            identity_ok,
            "source/dataset_id/window present" if identity_ok else "missing source/dataset_id/window",
        )
    )

    # If dataset_id points at a repo-relative path, require it to exist and,
    # when a hash is declared, match it. Opaque URIs are accepted as identity
    # pins only; coverage verification is adapter/execution responsibility.
    candidate_path = root / str(dataset_id)
    if dataset_id and candidate_path.exists():
        checks.append(CheckResult("dataset_path_exists", True, f"{dataset_id} exists on disk"))
        if dataset_hash:
            if candidate_path.is_file():
                digest = sha256_file(candidate_path)
                checks.append(
                    CheckResult(
                        "dataset_hash",
                        digest == dataset_hash,
                        f"sha256={digest} expected={dataset_hash}",
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        "dataset_hash",
                        False,
                        "dataset_hash declared but dataset_id is a directory; refuse without file-level hash",
                    )
                )
    elif dataset_hash:
        checks.append(
            CheckResult(
                "dataset_hash",
                True,
                "dataset_hash declared for non-path dataset_id; execution adapters must verify coverage",
            )
        )
    else:
        checks.append(
            CheckResult(
                "dataset_hash",
                True,
                "dataset_hash absent; execution must still prove coverage before results are trusted",
            )
        )
    return checks


def validate_commit_arms(root: Path, spec: dict[str, Any], *, for_execution: bool) -> list[CheckResult]:
    checks: list[CheckResult] = []
    baseline = (spec.get("baseline") or {}).get("commit_sha")
    candidate = (spec.get("candidate") or {})
    cand_sha = candidate.get("commit_sha")
    cand_change = candidate.get("change")

    base_ok, base_ev = git_sha_resolves(root, baseline)
    if for_execution:
        checks.append(CheckResult("baseline_sha_resolves", base_ok, base_ev))
    else:
        checks.append(
            CheckResult(
                "baseline_sha_format",
                isinstance(baseline, str) and len(baseline) == 40,
                f"baseline.commit_sha format check ({baseline!r})",
            )
        )

    if cand_sha is None and not cand_change:
        checks.append(CheckResult("candidate_arm", False, "candidate needs commit_sha and/or change"))
    else:
        checks.append(
            CheckResult(
                "candidate_arm",
                True,
                "candidate declares commit_sha and/or change",
            )
        )

    if for_execution:
        if cand_sha is not None:
            ok, ev = git_sha_resolves(root, cand_sha)
            checks.append(CheckResult("candidate_sha_resolves", ok, ev))
        else:
            checks.append(
                CheckResult(
                    "candidate_sha_resolves",
                    False,
                    "execution requires candidate.commit_sha (change-only candidate is not runnable)",
                )
            )
    return checks


def validate_experiment(
    root: Path,
    spec_path: Path,
    *,
    for_execution: bool = False,
    first_rows: Mapping[str, dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], list[CheckResult], list[str]]:
    schema = load_schema(root)
    spec = load_spec(spec_path)
    errors = schema_validate(spec, schema)
    checks = [
        CheckResult(
            "schema",
            not errors,
            "schema valid" if not errors else "; ".join(errors[:5]),
        )
    ]
    if spec_path.stem != spec.get("experiment_id"):
        checks.append(
            CheckResult(
                "filename_stem",
                False,
                f"filename stem {spec_path.stem!r} != experiment_id {spec.get('experiment_id')!r}",
            )
        )
    else:
        checks.append(CheckResult("filename_stem", True, "filename stem matches experiment_id"))

    checks.extend(validate_linkage(root, spec, first_rows=first_rows))
    checks.extend(validate_dataset_identity(spec, root))
    checks.extend(validate_commit_arms(root, spec, for_execution=for_execution))

    if for_execution:
        status = spec.get("status")
        checks.append(
            CheckResult(
                "approved_status",
                status == EXECUTABLE_STATUS,
                f"status={status!r}; execution requires APPROVED",
            )
        )
        setup = spec.get("setup_type")
        has_adapter = setup in EXECUTION_ADAPTERS
        checks.append(
            CheckResult(
                "execution_adapter",
                has_adapter,
                (
                    f"adapter registered for setup_type={setup!r}"
                    if has_adapter
                    else f"no execution adapter registered for setup_type={setup!r}"
                ),
            )
        )

    return spec, checks, errors


def _safe_div(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return num / den


def compute_metrics(members: Sequence[Mapping[str, Any]], required: Sequence[str]) -> dict[str, Any]:
    detected = [m for m in members if m.get("setup_detected")]
    evaluated = [m for m in detected if m.get("evaluated")]
    rejected = [m for m in evaluated if m.get("rejection_reason")]
    activated = [m for m in evaluated if m.get("activated")]
    entered = [m for m in activated if m.get("entered")]
    completed = [m for m in entered if m.get("completed")]

    results = [float(m["result"]) for m in completed if m.get("result") is not None]
    winners = [r for r in results if r > 0]
    losers = [r for r in results if r < 0]
    rejection_counts: dict[str, int] = {}
    for m in rejected:
        reason = str(m.get("rejection_reason"))
        rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    mae_vals = [float(m["mae"]) for m in completed if m.get("mae") is not None]
    mfe_vals = [float(m["mfe"]) for m in completed if m.get("mfe") is not None]
    hold_vals = [
        float(m["hold_time_seconds"])
        for m in completed
        if m.get("hold_time_seconds") is not None
    ]
    target_hits = sum(1 for m in completed if m.get("exit_reason") == "target")
    stop_hits = sum(1 for m in completed if m.get("exit_reason") == "stop")

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in results:
        equity += r
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    metrics: dict[str, Any] = {
        "population_size": {"count": len(members)},
        "setups_evaluated": {"count": len(evaluated), "of": len(detected)},
        "rejection_counts_by_reason": {
            "counts": rejection_counts,
            "total": len(rejected),
            "of_evaluated": len(evaluated),
        },
        "activation_count": {
            "count": len(activated),
            "rate": _safe_div(len(activated), len(evaluated)),
            "of": len(evaluated),
        },
        "entry_count": {
            "count": len(entered),
            "rate": _safe_div(len(entered), len(activated)),
            "of": len(activated),
        },
        "completed_trades": {"count": len(completed), "of": len(entered)},
        "win_rate": {
            "count": len(winners),
            "rate": _safe_div(len(winners), len(results)),
            "of": len(results),
        },
        "loss_rate": {
            "count": len(losers),
            "rate": _safe_div(len(losers), len(results)),
            "of": len(results),
        },
        "expectancy": {
            "value": (sum(results) / len(results)) if results else None,
            "of": len(results),
        },
        "median_result": {
            "value": (sorted(results)[len(results) // 2] if results else None),
            "of": len(results),
        },
        "total_result": {"value": sum(results) if results else 0.0, "of": len(results)},
        "average_winner": {
            "value": (sum(winners) / len(winners)) if winners else None,
            "of": len(winners),
        },
        "average_loser": {
            "value": (sum(losers) / len(losers)) if losers else None,
            "of": len(losers),
        },
        "payoff_ratio": {
            "value": (
                abs((sum(winners) / len(winners)) / (sum(losers) / len(losers)))
                if winners and losers
                else None
            ),
            "winners": len(winners),
            "losers": len(losers),
        },
        "mae": {
            "mean": (sum(mae_vals) / len(mae_vals)) if mae_vals else None,
            "of": len(mae_vals),
        },
        "mfe": {
            "mean": (sum(mfe_vals) / len(mfe_vals)) if mfe_vals else None,
            "of": len(mfe_vals),
        },
        "target_hit_rate": {
            "count": target_hits,
            "rate": _safe_div(target_hits, len(completed)),
            "of": len(completed),
        },
        "stop_hit_rate": {
            "count": stop_hits,
            "rate": _safe_div(stop_hits, len(completed)),
            "of": len(completed),
        },
        "drawdown": {"max_drawdown": max_dd, "of": len(results)},
        "exposure": {
            "completed_trades": len(completed),
            "population": len(members),
            "rate": _safe_div(len(completed), len(members)),
        },
        "average_hold_time": {
            "seconds": (sum(hold_vals) / len(hold_vals)) if hold_vals else None,
            "of": len(hold_vals),
        },
    }

    missing = [name for name in required if name not in metrics]
    metrics["_missing_required"] = missing
    return metrics


def coverage_funnel(members: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    detected = [m for m in members if m.get("setup_detected")]
    evaluated = [m for m in detected if m.get("evaluated")]
    rejected = [m for m in evaluated if m.get("rejection_reason")]
    by_reason: dict[str, int] = {}
    for m in rejected:
        reason = str(m.get("rejection_reason"))
        by_reason[reason] = by_reason.get(reason, 0) + 1
    activated = [m for m in evaluated if m.get("activated")]
    entered = [m for m in activated if m.get("entered")]
    completed = [m for m in entered if m.get("completed")]
    return {
        "detected": {"count": len(detected)},
        "evaluated": {"count": len(evaluated), "of": len(detected)},
        "rejected_by_reason": {"counts": by_reason, "total": len(rejected), "of": len(evaluated)},
        "activated": {"count": len(activated), "of": len(evaluated)},
        "entered": {"count": len(entered), "of": len(activated)},
        "completed": {"count": len(completed), "of": len(entered)},
    }


def concentration(members: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    def _count(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in members:
            label = str(m.get(key) or "UNKNOWN")
            out[label] = out.get(label, 0) + 1
        return out

    return {
        "ticker": _count("instrument"),
        "timeframe": _count("timeframe"),
        "time": _count("time_of_day"),
        "regime": _count("regime"),
        "setup_type": _count("setup_type"),
    }


def _metric_primary(value: Mapping[str, Any]) -> Any:
    for key in ("value", "rate", "mean", "count", "max_drawdown", "seconds"):
        if key in value:
            return value[key]
    return None


def metric_delta(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    for key in sorted(set(baseline) | set(candidate)):
        if key.startswith("_"):
            continue
        b = baseline.get(key)
        c = candidate.get(key)
        if not isinstance(b, dict) or not isinstance(c, dict):
            continue
        b_val = _metric_primary(b)
        c_val = _metric_primary(c)
        if isinstance(b_val, (int, float)) and isinstance(c_val, (int, float)):
            abs_delta = c_val - b_val
            pct = None if b_val == 0 else (abs_delta / abs(b_val))
            delta[key] = {
                "baseline": b_val,
                "candidate": c_val,
                "absolute": abs_delta,
                "percent": pct,
                "baseline_denominator": b.get("of"),
                "candidate_denominator": c.get("of"),
            }
    return delta


def write_evidence_bundle(
    evidence_dir: Path,
    *,
    report: RunnerReport,
    spec: dict[str, Any],
    baseline_raw: dict[str, Any] | None,
    candidate_raw: dict[str, Any] | None,
    reproduction_command: str,
) -> dict[str, str]:
    evidence_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}

    def _write(name: str, payload: Any) -> str:
        path = evidence_dir / name
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
            path.write_text(text, encoding="utf-8")
        else:
            path.write_text(str(payload), encoding="utf-8")
        paths[name] = path.as_posix()
        paths[f"{name}.sha256"] = sha256_bytes(path.read_bytes())
        return path.as_posix()

    _write(
        "experiment_spec.json",
        spec,
    )
    _write(
        "runner_report.json",
        report.to_dict(),
    )
    _write(
        "reproduction.json",
        {
            "command": reproduction_command,
            "runner_version": RUNNER_VERSION,
            "python": sys.version,
            "platform": platform.platform(),
            "recorded_at": utc_now(),
        },
    )
    if baseline_raw is not None:
        _write("baseline_raw.json", baseline_raw)
    if candidate_raw is not None:
        _write("candidate_raw.json", candidate_raw)
    return paths


def run_validation(
    root: Path,
    spec_path: Path,
    *,
    for_execution: bool = False,
    first_rows: Mapping[str, dict[str, Any]] | None = None,
) -> RunnerReport:
    spec, checks, schema_errors = validate_experiment(
        root, spec_path, for_execution=for_execution, first_rows=first_rows
    )
    failed = [c for c in checks if not c.passed]
    errors = list(schema_errors)
    errors.extend(f"{c.name}: {c.evidence}" for c in failed)

    status = "VALID"
    result = "INCONCLUSIVE"
    if failed or schema_errors:
        if for_execution and any(c.name == "approved_status" and not c.passed for c in checks):
            status = "BLOCKED"
            result = "BLOCKED"
        elif for_execution and any(c.name == "execution_adapter" and not c.passed for c in checks):
            status = "BLOCKED"
            result = "BLOCKED"
        else:
            status = "INVALID"
            result = "INVALID EXPERIMENT"

    if not for_execution and spec.get("status") != EXECUTABLE_STATUS and not failed and not schema_errors:
        # Structural validation can succeed for EXAMPLE/DRAFT without approving execution.
        status = "VALID"
        result = "INCONCLUSIVE"

    return RunnerReport(
        status=status,
        experiment_id=spec.get("experiment_id"),
        trial_id=spec.get("trial_id"),
        baseline_sha=(spec.get("baseline") or {}).get("commit_sha"),
        candidate_sha=(spec.get("candidate") or {}).get("commit_sha"),
        population=spec.get("population"),
        changed_variables=list(spec.get("changed_variables") or []),
        held_constant=list(spec.get("held_constant") or []),
        integrity_checks=checks,
        baseline_metrics=None,
        candidate_metrics=None,
        delta=None,
        coverage_funnel=None,
        concentration=None,
        evidence_classification={
            "VERIFIED": [c.name for c in checks if c.passed],
            "INFERENCE": [],
            "UNKNOWN": [
                "strategy edge / promotion readiness",
                "live or paper execution behavior",
            ],
        },
        result=result,
        artifacts={
            "spec_path": str(spec_path.relative_to(root)) if spec_path.is_relative_to(root) else str(spec_path),
            "spec_hash": sha256_file(spec_path),
            "runner_version": RUNNER_VERSION,
            "validated_at": utc_now(),
            "repository_sha": _git(root, "rev-parse", "HEAD").stdout.strip() or None,
        },
        qa_handoff=[
            "Confirm population identity was not outcome-selected.",
            "Confirm only authorized changed_variables differ between arms.",
            "Confirm dataset coverage and absence of leakage independently.",
        ],
        errors=errors,
    )


def _normalize_criteria(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            if not isinstance(item, str):
                return []
            text = item.strip()
            if text:
                out.append(text)
        return out
    return []


_CMP_RE = re.compile(
    r"^(?P<left>baseline|candidate)\.(?P<lmetric>[A-Za-z0-9_]+)\.(?P<lfield>[A-Za-z0-9_]+)"
    r"\s*(?P<op>>=|<=|==|!=|>|<)\s*"
    r"(?:"
    r"(?P<right>baseline|candidate)\.(?P<rmetric>[A-Za-z0-9_]+)\.(?P<rfield>[A-Za-z0-9_]+)"
    r"|"
    r"(?P<literal>-?\d+(?:\.\d+)?)"
    r")$"
)

_NAMED_REJECTION = {
    "population_size_differs": "population_size_differs",
    "population size differs between arms": "population_size_differs",
    "required_metric_missing": "required_metric_missing",
    "required metric missing for either arm": "required_metric_missing",
}

# Named predicates that invalidate the experiment itself (integrity), rather than
# counting as evidence against the candidate (performance/research rejection).
_INTEGRITY_REJECTION_NAMES = frozenset(
    {
        "population_size_differs",
        "required_metric_missing",
    }
)


def _metric_field(metrics: Mapping[str, Any], metric: str, field_name: str) -> Any:
    block = metrics.get(metric)
    if not isinstance(block, dict):
        return None
    return block.get(field_name)


def _compare(left: Any, op: str, right: Any) -> bool | None:
    if left is None or right is None:
        return None
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
        return None
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    return None


def evaluate_criterion(
    expression: str,
    *,
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
    kind: str,
) -> tuple[str, bool | None, str]:
    """Return (status, value, evidence) for one criterion expression."""
    text = expression.strip()
    named = _NAMED_REJECTION.get(text.lower())
    if named == "population_size_differs":
        b = _metric_field(baseline_metrics, "population_size", "count")
        c = _metric_field(candidate_metrics, "population_size", "count")
        if b is None or c is None:
            return "unparseable", None, "population_size.count unavailable on an arm"
        fired = b != c
        return (
            "ok",
            fired if kind == "rejection" else (not fired),
            f"population_size baseline={b} candidate={c}",
        )
    if named == "required_metric_missing":
        missing = sorted(
            set(baseline_metrics.get("_missing_required") or [])
            | set(candidate_metrics.get("_missing_required") or [])
        )
        fired = bool(missing)
        return (
            "ok",
            fired if kind == "rejection" else (not fired),
            f"missing={missing}" if missing else "no missing required metrics",
        )

    match = _CMP_RE.fullmatch(text)
    if not match:
        return "unparseable", None, f"not a mechanical criterion: {text!r}"

    arms = {"baseline": baseline_metrics, "candidate": candidate_metrics}
    left = _metric_field(arms[match.group("left")], match.group("lmetric"), match.group("lfield"))
    if match.group("literal") is not None:
        right: Any = float(match.group("literal"))
        if right.is_integer():
            right = int(right)
    else:
        right = _metric_field(
            arms[match.group("right")], match.group("rmetric"), match.group("rfield")
        )
    compared = _compare(left, match.group("op"), right)
    if compared is None:
        return "unparseable", None, f"cannot compare {left!r} {match.group('op')} {right!r}"
    return "ok", compared, f"{text} => {compared}"


def classify_experiment_result(
    spec: Mapping[str, Any],
    baseline_metrics: Mapping[str, Any],
    candidate_metrics: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Mechanically classify against preregistered acceptance/rejection criteria.

    Returns one of:
      SUPPORTED BY THIS EXPERIMENT | NOT SUPPORTED | INCONCLUSIVE | INVALID EXPERIMENT

    Integrity failures (population mismatch, missing required metrics) are
    INVALID EXPERIMENT — not evidence against the candidate.

    Classification is measurement-only. It never authorizes promotion, merge,
    deploy, or strategy changes.
    """
    acceptance = _normalize_criteria(spec.get("acceptance_criteria"))
    rejection = _normalize_criteria(spec.get("rejection_criteria"))
    details: dict[str, Any] = {
        "acceptance_criteria": [],
        "rejection_criteria": [],
        "integrity_checks": [],
        "authority": "classification only; zero promotion/merge/deploy/strategy authority",
    }

    # Automatic integrity gates — always INVALID when they fail.
    b_pop = _metric_field(baseline_metrics, "population_size", "count")
    c_pop = _metric_field(candidate_metrics, "population_size", "count")
    if b_pop is not None and c_pop is not None and b_pop != c_pop:
        details["integrity_checks"].append(
            {
                "name": "population_size_differs",
                "passed": False,
                "evidence": f"population_size baseline={b_pop} candidate={c_pop}",
            }
        )
        details["reason"] = "population mismatch between arms"
        return "INVALID EXPERIMENT", details
    details["integrity_checks"].append(
        {
            "name": "population_size_match",
            "passed": True,
            "evidence": f"population_size baseline={b_pop} candidate={c_pop}",
        }
    )

    missing = sorted(
        set(baseline_metrics.get("_missing_required") or [])
        | set(candidate_metrics.get("_missing_required") or [])
    )
    if missing:
        details["integrity_checks"].append(
            {
                "name": "required_metric_missing",
                "passed": False,
                "evidence": f"missing={missing}",
            }
        )
        details["reason"] = "required metrics missing"
        return "INVALID EXPERIMENT", details
    details["integrity_checks"].append(
        {
            "name": "required_metrics_present",
            "passed": True,
            "evidence": "no missing required metrics",
        }
    )

    if not acceptance and not rejection:
        details["reason"] = "no preregistered acceptance/rejection criteria"
        return "INCONCLUSIVE", details

    unparseable = False
    performance_rejection_fired = False
    for expr in rejection:
        named = _NAMED_REJECTION.get(expr.strip().lower())
        status, value, evidence = evaluate_criterion(
            expr,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            kind="rejection",
        )
        entry = {
            "expression": expr,
            "status": status,
            "value": value,
            "evidence": evidence,
            "class": "integrity" if named in _INTEGRITY_REJECTION_NAMES else "performance",
        }
        details["rejection_criteria"].append(entry)
        if status != "ok":
            unparseable = True
            continue
        if not value:
            continue
        if named in _INTEGRITY_REJECTION_NAMES:
            details["reason"] = f"integrity rejection fired: {named}"
            return "INVALID EXPERIMENT", details
        performance_rejection_fired = True

    acceptance_failed = False
    acceptance_all_true = bool(acceptance)
    for expr in acceptance:
        status, value, evidence = evaluate_criterion(
            expr,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            kind="acceptance",
        )
        details["acceptance_criteria"].append(
            {"expression": expr, "status": status, "value": value, "evidence": evidence}
        )
        if status != "ok":
            unparseable = True
            acceptance_all_true = False
        elif not value:
            acceptance_failed = True
            acceptance_all_true = False

    if unparseable:
        details["reason"] = "one or more criteria are not mechanically evaluable"
        return "INCONCLUSIVE", details
    if performance_rejection_fired:
        details["reason"] = "one or more performance/research rejection criteria fired"
        return "NOT SUPPORTED", details
    if acceptance and acceptance_failed:
        details["reason"] = "one or more acceptance criteria failed"
        return "NOT SUPPORTED", details
    if acceptance and acceptance_all_true:
        details["reason"] = (
            "all acceptance criteria passed; no performance rejection criteria fired"
        )
        return "SUPPORTED BY THIS EXPERIMENT", details

    details["reason"] = "no acceptance criteria to support a positive claim"
    return "INCONCLUSIVE", details


def execute_experiment(
    root: Path,
    spec_path: Path,
    *,
    write_evidence: bool = True,
    first_rows: Mapping[str, dict[str, Any]] | None = None,
    evidence_dir: Path | None = None,
) -> RunnerReport:
    """Execute an APPROVED experiment via a registered setup_type adapter.

    Fail-closed: non-APPROVED, unresolved SHAs, missing adapter, or incomplete
    required metrics => no promotional claim and no silent continuation.
    """
    validation = run_validation(
        root, spec_path, for_execution=True, first_rows=first_rows
    )
    if validation.status != "VALID":
        return validation

    spec = load_spec(spec_path)
    setup = spec["setup_type"]
    adapter = EXECUTION_ADAPTERS[setup]
    ctx = ExperimentContext(
        root=root,
        spec=spec,
        spec_path=spec_path,
        spec_hash=sha256_file(spec_path),
    )

    baseline_arm, candidate_arm = _run_both_arms(adapter, ctx)

    required = list(spec.get("required_metrics") or [])
    baseline_metrics = compute_metrics(baseline_arm.members, required)
    candidate_metrics = compute_metrics(candidate_arm.members, required)
    missing = sorted(
        set(baseline_metrics.get("_missing_required") or [])
        | set(candidate_metrics.get("_missing_required") or [])
    )
    checks = list(validation.integrity_checks)
    checks.append(
        CheckResult(
            "required_metrics",
            not missing,
            "all required metrics present" if not missing else f"missing {missing}",
        )
    )
    if missing:
        return RunnerReport(
            status="INVALID",
            experiment_id=spec.get("experiment_id"),
            trial_id=spec.get("trial_id"),
            baseline_sha=baseline_arm.commit_sha,
            candidate_sha=candidate_arm.commit_sha,
            population=spec.get("population"),
            changed_variables=list(spec.get("changed_variables") or []),
            held_constant=list(spec.get("held_constant") or []),
            integrity_checks=checks,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            delta=None,
            coverage_funnel={
                "baseline": coverage_funnel(baseline_arm.members),
                "candidate": coverage_funnel(candidate_arm.members),
            },
            concentration={
                "baseline": concentration(baseline_arm.members),
                "candidate": concentration(candidate_arm.members),
            },
            evidence_classification={
                "VERIFIED": [],
                "INFERENCE": [],
                "UNKNOWN": ["comparison incomplete because required metrics are missing"],
            },
            result="INVALID EXPERIMENT",
            artifacts=validation.artifacts,
            qa_handoff=validation.qa_handoff,
            errors=[f"missing required metrics: {missing}"],
            warnings=baseline_arm.warnings + candidate_arm.warnings,
        )

    delta = metric_delta(baseline_metrics, candidate_metrics)
    result_label, classification = classify_experiment_result(
        spec, baseline_metrics, candidate_metrics
    )
    report = RunnerReport(
        status="VALID",
        experiment_id=spec.get("experiment_id"),
        trial_id=spec.get("trial_id"),
        baseline_sha=baseline_arm.commit_sha,
        candidate_sha=candidate_arm.commit_sha,
        population=spec.get("population"),
        changed_variables=list(spec.get("changed_variables") or []),
        held_constant=list(spec.get("held_constant") or []),
        integrity_checks=checks,
        baseline_metrics=baseline_metrics,
        candidate_metrics=candidate_metrics,
        delta=delta,
        coverage_funnel={
            "baseline": coverage_funnel(baseline_arm.members),
            "candidate": coverage_funnel(candidate_arm.members),
        },
        concentration={
            "baseline": concentration(baseline_arm.members),
            "candidate": concentration(candidate_arm.members),
        },
        evidence_classification={
            "VERIFIED": [
                "baseline and candidate metrics computed from this run's population members",
                "integrity checks listed under integrity_checks",
                "result classification is mechanical against preregistered criteria only",
            ],
            "INFERENCE": [
                "Any claim that metric deltas imply a durable edge beyond this experiment",
            ],
            "UNKNOWN": [
                "Whether the candidate should be promoted, merged, or deployed",
                "Out-of-sample performance beyond the declared window",
            ],
        },
        result=result_label,
        artifacts=dict(validation.artifacts),
        qa_handoff=validation.qa_handoff
        + [
            "Falsify identical-population claim between arms.",
            "Falsify that held_constant fields were actually held constant.",
            "Attempt to reproduce metrics from raw arm outputs alone.",
            "Attempt to falsify the mechanical criteria evaluation.",
        ],
        warnings=baseline_arm.warnings + candidate_arm.warnings,
    )
    report.artifacts["criteria_classification"] = classification
    report.evidence_classification["VERIFIED"].append(
        "runner has zero promotion/merge/deploy/strategy-change authority"
    )

    repro = (
        f"python scripts/afs_experiment_runner.py run --spec "
        f"{spec_path if not spec_path.is_relative_to(root) else spec_path.relative_to(root)}"
    )
    if write_evidence:
        out_dir = evidence_dir or (root / spec["evidence_path"])
        artifact_paths = write_evidence_bundle(
            out_dir,
            report=report,
            spec=spec,
            baseline_raw={"members": baseline_arm.members, "raw": baseline_arm.raw},
            candidate_raw={"members": candidate_arm.members, "raw": candidate_arm.raw},
            reproduction_command=repro,
        )
        report.artifacts["evidence_dir"] = str(out_dir)
        report.artifacts["files"] = artifact_paths
        report.artifacts["reproduction_command"] = repro

    return report


def _run_both_arms(
    adapter: ExecutionAdapter,
    ctx: ExperimentContext,
) -> tuple[ArmRawResult, ArmRawResult]:
    """Call adapter twice with an explicit arm marker in spec copy."""
    baseline_spec = dict(ctx.spec)
    baseline_spec["_runner_arm"] = "baseline"
    candidate_spec = dict(ctx.spec)
    candidate_spec["_runner_arm"] = "candidate"
    baseline = adapter(
        ExperimentContext(
            root=ctx.root,
            spec=baseline_spec,
            spec_path=ctx.spec_path,
            spec_hash=ctx.spec_hash,
            runner_version=ctx.runner_version,
        )
    )
    candidate = adapter(
        ExperimentContext(
            root=ctx.root,
            spec=candidate_spec,
            spec_path=ctx.spec_path,
            spec_hash=ctx.spec_hash,
            runner_version=ctx.runner_version,
        )
    )
    return baseline, candidate


def register_execution_adapter(setup_type: str, adapter: ExecutionAdapter) -> None:
    """Test/helper hook. Production registrations belong in explicit follow-ups."""
    EXECUTION_ADAPTERS[setup_type] = adapter


def clear_execution_adapters() -> None:
    EXECUTION_ADAPTERS.clear()


def format_report_text(report: RunnerReport) -> str:
    lines = [
        f"Experiment: {report.experiment_id}",
        f"Status: {report.status}",
        f"Baseline SHA: {report.baseline_sha}",
        f"Candidate SHA: {report.candidate_sha}",
        f"Population: {report.population}",
        f"Changed variable(s): {json.dumps(report.changed_variables)}",
        f"Everything held constant: {json.dumps(report.held_constant)}",
        "",
        "Integrity checks:",
    ]
    for check in report.integrity_checks:
        mark = "PASS" if check.passed else "FAIL"
        lines.append(f"- {check.name}: {mark} — {check.evidence}")
    lines.extend(
        [
            "",
            "Baseline:",
            json.dumps(report.baseline_metrics, indent=2, sort_keys=True)
            if report.baseline_metrics
            else "- (not executed)",
            "",
            "Candidate:",
            json.dumps(report.candidate_metrics, indent=2, sort_keys=True)
            if report.candidate_metrics
            else "- (not executed)",
            "",
            "Delta:",
            json.dumps(report.delta, indent=2, sort_keys=True) if report.delta else "- (n/a)",
            "",
            "Coverage funnel:",
            json.dumps(report.coverage_funnel, indent=2, sort_keys=True)
            if report.coverage_funnel
            else "- (n/a)",
            "",
            "Concentration:",
            json.dumps(report.concentration, indent=2, sort_keys=True)
            if report.concentration
            else "- (n/a)",
            "",
            "Evidence classification:",
            "VERIFIED:",
            *[f"- {x}" for x in report.evidence_classification.get("VERIFIED", [])],
            "INFERENCE:",
            *[f"- {x}" for x in report.evidence_classification.get("INFERENCE", [])],
            "UNKNOWN:",
            *[f"- {x}" for x in report.evidence_classification.get("UNKNOWN", [])],
            "",
            f"Result: {report.result}",
            "",
            "Artifacts:",
            json.dumps(report.artifacts, indent=2, sort_keys=True),
            "",
            "QA handoff:",
            *[f"- {x}" for x in report.qa_handoff],
        ]
    )
    if report.errors:
        lines.extend(["", "Errors:", *[f"- {e}" for e in report.errors]])
    if report.warnings:
        lines.extend(["", "Warnings:", *[f"- {w}" for w in report.warnings]])
    return "\n".join(lines) + "\n"
