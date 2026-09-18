"""Backtest -> demo qualification gate (read-only).

This gate answers one narrow question:

    Is a strategy-only change proven well enough by canonical replay that the
    long duplicate internal-paper phase may be skipped and the change may move
    to Tradovate DEMO, subject to a separate runtime/release safety check?

It does NOT run a backtest, place an order, change config, deploy, restart a
service, or authorize live trading. It consumes an explicit evidence-facts JSON
file plus the existing project_check promotion gate and fails closed on missing
proof.

The direct-to-demo path is intentionally narrow. It is available only when:
- the change is strategy/parameter-only; risk/execution/broker/session-contract
  semantics are explicitly unchanged, and the git diff is mechanically limited
  to strategy/tests/docs paths;
- canonical replay uses the real engine path and matches runtime logic;
- candidate/direction/entry-stop-target/timeframe/causal parity is proven;
- the replay dataset/manifest, code SHA and risk_rules hash are pinned;
- contract/roll identity, futures session-day identity and feed integrity are
  proven;
- IOC/no-fill behavior, adverse slippage, commission, gap handling and
  pessimistic same-bar resolution are modeled;
- validation uses an untouched window, multiple months, walk-forward evidence,
  a pre-registered sample requirement of at least 30 resolved fills in every
  required validation cell, drawdown limits and concentration checks;
- a frozen golden-parity fixture set covers no-fill, same-bar ambiguity, gap,
  session-boundary and roll-boundary cases.

A PASS means "DEMO EVIDENCE ELIGIBLE", not "deploy now" and never "live
eligible". Runtime/deployed-state reconciliation remains mandatory before any
DEMO activation.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ops.project_check import gitutil
from ops.project_check.promotion import build_promotion_report, load_evidence_facts

MIN_RESOLVED_FILLS_PER_CELL = 30
REQUIRED_SLIPPAGE_STRESS_TICKS = {2, 3}
DEMO_CLASSIFICATIONS = {"VALIDATED", "PROMISING BUT UNPROVEN"}
DIRECT_TO_DEMO_ALLOWED_DIFF_PREFIXES = ("strategy/", "tests/", "docs/")


def _sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _resolve_path(root: Path, raw: Any) -> Path | None:
    if raw in (None, ""):
        return None
    path = Path(str(raw)).expanduser()
    return path if path.is_absolute() else root / path


def _require_true(section: dict[str, Any], key: str, blockers: list[str], prefix: str) -> None:
    if section.get(key) is not True:
        blockers.append(f"{prefix}.{key} must be explicitly true")


def _require_false(section: dict[str, Any], key: str, blockers: list[str], prefix: str) -> None:
    if section.get(key) is not False:
        blockers.append(f"{prefix}.{key} must be explicitly false")


def _require_nonempty(section: dict[str, Any], key: str, blockers: list[str], prefix: str) -> None:
    value = section.get(key)
    if value is None or not str(value).strip():
        blockers.append(f"{prefix}.{key} is required")


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _check_change_scope(root: Path, evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    scope = evidence.get("change_scope") or {}
    _require_true(scope, "strategy_or_parameter_only", blockers, "change_scope")
    for key in (
        "risk_policy_unchanged",
        "execution_path_unchanged",
        "broker_routing_unchanged",
        "session_contract_semantics_unchanged",
    ):
        _require_true(scope, key, blockers, "change_scope")
    _require_nonempty(scope, "base_sha", blockers, "change_scope")

    base_sha = str(scope.get("base_sha") or "").strip()
    current_head = gitutil.head_sha(root)
    changed_files: list[str] = []
    strategy_files: list[str] = []
    non_strategy_scope_files: list[str] = []
    if current_head is None:
        blockers.append("change_scope current repository HEAD could not be resolved")
    elif base_sha:
        if base_sha == current_head:
            blockers.append("change_scope.base_sha must be the pre-change commit, not current HEAD")
        out, error = gitutil.run_git(
            [
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                "--name-only",
                f"{base_sha}...{current_head}",
            ],
            cwd=root,
        )
        if error is not None or out is None:
            blockers.append(
                "change_scope could not mechanically enumerate the base_sha...HEAD diff: "
                + (error or "unknown git error")
            )
        else:
            changed_files = [line.strip() for line in out.splitlines() if line.strip()]
            if not changed_files:
                blockers.append("change_scope base_sha...HEAD diff is empty; no strategy change is proven")
            strategy_files = [path for path in changed_files if path.startswith("strategy/")]
            if not strategy_files:
                blockers.append("change_scope diff contains no strategy/ file")
            non_strategy_scope_files = [
                path
                for path in changed_files
                if not path.startswith(DIRECT_TO_DEMO_ALLOWED_DIFF_PREFIXES)
            ]
            if non_strategy_scope_files:
                blockers.append(
                    "change_scope git diff is not strategy-only; direct-to-demo disallows: "
                    + ", ".join(non_strategy_scope_files)
                )
    return {
        **scope,
        "current_head": current_head,
        "changed_files": changed_files,
        "strategy_files": strategy_files,
        "non_strategy_scope_files": non_strategy_scope_files,
    }


def _check_canonical_replay(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    replay = evidence.get("canonical_replay") or {}
    for key in (
        "real_replay_engine",
        "real_decision_engine",
        "real_risk_engine",
        "real_paper_broker",
        "replay_live_logic_confirmed",
        "same_strategy_formula_confirmed",
    ):
        _require_true(replay, key, blockers, "canonical_replay")
    return replay


def _check_identity_parity(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    parity = evidence.get("identity_parity") or {}
    for key in (
        "candidate_identity_parity",
        "direction_parity",
        "entry_stop_target_parity",
        "timeframe_parity",
        "causal_data_availability",
    ):
        _require_true(parity, key, blockers, "identity_parity")
    _require_false(parity, "lookahead_or_partial_bar_dependency", blockers, "identity_parity")
    return parity


def _check_data_integrity(root: Path, evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    data = evidence.get("data_integrity") or {}
    for key in (
        "dataset_frozen",
        "contract_roll_identity_proven",
        "session_day_identity_proven",
        "feed_integrity_proven",
    ):
        _require_true(data, key, blockers, "data_integrity")

    _require_nonempty(data, "dataset_manifest_path", blockers, "data_integrity")
    _require_nonempty(data, "dataset_manifest_sha256", blockers, "data_integrity")

    manifest_path = _resolve_path(root, data.get("dataset_manifest_path"))
    expected = str(data.get("dataset_manifest_sha256") or "").strip().lower()
    actual = _sha256(manifest_path) if manifest_path is not None else None
    if manifest_path is not None and actual is None:
        blockers.append(f"data_integrity.dataset_manifest_path is unreadable: {manifest_path}")
    elif actual is not None and expected and actual != expected:
        blockers.append(
            "data_integrity.dataset_manifest_sha256 does not match the current manifest bytes"
        )
    return {
        **data,
        "resolved_manifest_path": str(manifest_path) if manifest_path else None,
        "actual_manifest_sha256": actual,
    }


def _check_execution_realism(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    realism = evidence.get("execution_realism") or {}
    for key in (
        "ioc_no_fill_modeled",
        "pessimistic_same_bar",
        "gap_through_modeled",
        "slippage_included",
        "commission_included",
        "slippage_stress_pass",
    ):
        _require_true(realism, key, blockers, "execution_realism")

    baseline_slippage = _as_float(realism.get("baseline_adverse_slippage_ticks"))
    if baseline_slippage is None or baseline_slippage < 1.0:
        blockers.append(
            "execution_realism.baseline_adverse_slippage_ticks must be >= 1 for direct-to-demo evidence"
        )

    commission = _as_float(realism.get("commission_round_turn_dollars"))
    if commission is None or commission <= 0:
        blockers.append(
            "execution_realism.commission_round_turn_dollars must be a positive explicit cost"
        )

    stress = realism.get("slippage_stress_ticks")
    stress_values: set[int] = set()
    if isinstance(stress, list):
        for item in stress:
            parsed = _as_int(item)
            if parsed is not None:
                stress_values.add(parsed)
    if not REQUIRED_SLIPPAGE_STRESS_TICKS.issubset(stress_values):
        blockers.append(
            "execution_realism.slippage_stress_ticks must include both 2-tick and 3-tick adverse stress"
        )
    return realism


def _check_validation(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    validation = evidence.get("validation") or {}
    for key in (
        "untouched_validation_window",
        "multiple_months_covered",
        "walk_forward_pass",
        "sample_requirement_pre_registered",
        "drawdown_within_pre_registered_limit",
        "concentration_check_pass",
        "session_filters_respected",
        "direction_coverage_requirement_met",
    ):
        _require_true(validation, key, blockers, "validation")

    required_per_cell = _as_int(validation.get("required_resolved_fills_per_cell"))
    minimum_cell = _as_int(validation.get("minimum_resolved_fills_in_required_cells"))
    if required_per_cell is None or required_per_cell < MIN_RESOLVED_FILLS_PER_CELL:
        blockers.append(
            "validation.required_resolved_fills_per_cell must be pre-registered at "
            f">= {MIN_RESOLVED_FILLS_PER_CELL}"
        )
    if minimum_cell is None or minimum_cell < 0:
        blockers.append(
            "validation.minimum_resolved_fills_in_required_cells must be a non-negative integer"
        )
    elif required_per_cell is not None and minimum_cell < required_per_cell:
        blockers.append(
            "validation.minimum_resolved_fills_in_required_cells="
            f"{minimum_cell} is below the pre-registered per-cell requirement {required_per_cell}"
        )
    return validation


def _check_golden_parity(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    golden = evidence.get("golden_parity") or {}
    for key in (
        "fixture_set_frozen",
        "runtime_vs_replay_candidate_parity",
        "runtime_vs_replay_risk_parity",
        "runtime_vs_replay_order_intent_parity",
        "no_fill_case_covered",
        "same_bar_ambiguity_case_covered",
        "gap_case_covered",
        "session_boundary_case_covered",
        "roll_boundary_case_covered",
    ):
        _require_true(golden, key, blockers, "golden_parity")
    return golden


def _check_replay_provenance(root: Path, evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    provenance = evidence.get("replay_provenance") or {}
    _require_nonempty(provenance, "code_sha", blockers, "replay_provenance")
    _require_nonempty(provenance, "risk_rules_sha256", blockers, "replay_provenance")

    current_head = gitutil.head_sha(root)
    claimed_head = str(provenance.get("code_sha") or "").strip()
    if current_head is None:
        blockers.append("replay_provenance current repository HEAD could not be resolved")
    elif claimed_head and current_head != claimed_head:
        blockers.append(
            f"replay_provenance.code_sha={claimed_head} does not match current HEAD {current_head}"
        )

    risk_path = root / "risk_rules.yaml"
    actual_risk_hash = _sha256(risk_path)
    claimed_risk_hash = str(provenance.get("risk_rules_sha256") or "").strip().lower()
    if actual_risk_hash is None:
        blockers.append("replay_provenance current risk_rules.yaml could not be hashed")
    elif claimed_risk_hash and actual_risk_hash != claimed_risk_hash:
        blockers.append(
            "replay_provenance.risk_rules_sha256 does not match current risk_rules.yaml"
        )
    return {
        **provenance,
        "current_head": current_head,
        "actual_risk_rules_sha256": actual_risk_hash,
    }


def _check_execution_claims(evidence: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    claimed = evidence.get("execution_context_claimed") or {}
    for key in (
        "instrument",
        "entry_fill_model",
        "entry_tolerance_ticks",
        "contract_qty",
        "commission_slippage_assumptions",
    ):
        _require_nonempty(claimed, key, blockers, "execution_context_claimed")
    return claimed


def build_demo_qualification_report(
    *,
    strategy: str,
    repo_root: str | Path,
    evidence_path: str | Path,
) -> dict[str, Any]:
    """Build a fail-closed direct-to-DEMO evidence qualification report."""
    root = Path(repo_root)
    path = Path(evidence_path)
    evidence, load_error = load_evidence_facts(path)
    if evidence is None:
        return {
            "ok": False,
            "gate_pass": False,
            "demo_evidence_eligible": False,
            "strategy": strategy,
            "evidence_path": str(path),
            "evidence_load_error": load_error,
            "blockers": [load_error or "evidence facts unavailable"],
            "long_internal_paper_phase_waived": False,
            "fallback_to_existing_validation_path": True,
            "runtime_release_reconciliation_required": True,
            "demo_forward_validation_required": True,
            "live_trading_authorized": False,
        }

    blockers: list[str] = []
    promotion = build_promotion_report(
        strategy=strategy,
        repo_root=root,
        evidence_path=path,
    )
    if not promotion.get("gate_pass"):
        blockers.append("base promotion proof gate did not pass")
        blockers.extend(promotion.get("classification", {}).get("blockers") or [])

    effective_classification = promotion.get("classification", {}).get("effective_classification")
    if effective_classification not in DEMO_CLASSIFICATIONS:
        blockers.append(
            "effective classification must be VALIDATED or PROMISING BUT UNPROVEN for direct-to-demo"
        )

    scope = _check_change_scope(root, evidence, blockers)
    canonical = _check_canonical_replay(evidence, blockers)
    identity = _check_identity_parity(evidence, blockers)
    data = _check_data_integrity(root, evidence, blockers)
    realism = _check_execution_realism(evidence, blockers)
    validation = _check_validation(evidence, blockers)
    golden = _check_golden_parity(evidence, blockers)
    provenance = _check_replay_provenance(root, evidence, blockers)
    execution_claims = _check_execution_claims(evidence, blockers)

    blockers = list(dict.fromkeys(blockers))
    gate_pass = not blockers
    return {
        "ok": gate_pass,
        "gate_pass": gate_pass,
        "demo_evidence_eligible": gate_pass,
        "strategy": strategy,
        "evidence_path": str(path),
        "evidence_load_error": load_error,
        "effective_classification": effective_classification,
        "blockers": blockers,
        "base_promotion_gate_pass": bool(promotion.get("gate_pass")),
        "change_scope": scope,
        "canonical_replay": canonical,
        "identity_parity": identity,
        "data_integrity": data,
        "execution_realism": realism,
        "validation": validation,
        "golden_parity": golden,
        "replay_provenance": provenance,
        "execution_context_claimed": execution_claims,
        "execution_context_live_check": promotion.get("execution_context"),
        "accounting_identities": promotion.get("accounting_identities"),
        "long_internal_paper_phase_waived": gate_pass,
        "fallback_to_existing_validation_path": not gate_pass,
        "runtime_release_reconciliation_required": True,
        "demo_forward_validation_required": True,
        "live_trading_authorized": False,
        "verdict": "DEMO_EVIDENCE_ELIGIBLE" if gate_pass else "BLOCKED",
        "safety_boundary": (
            "A PASS waives only the long duplicate internal-paper evidence phase for a strategy-only "
            "change. It does not deploy, restart, arm, route to a broker, or authorize live trading. "
            "A separate current runtime/release safety reconciliation is still required before DEMO."
        ),
    }
