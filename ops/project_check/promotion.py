"""Routine 2: Strategy Promotion Proof Gate.

This is NOT a strategy backtester and NOT a re-implementation of the
per-strategy canonical-evidence pipelines that already exist under
scripts/*_canonical_evidence*.py (those already run the real
ReplayEngine -> DecisionEngine -> RiskEngine -> PaperBroker path and produce
strategy-specific results.json files with wildly different internal shapes
-- no two of the ones inspected during this build share a schema, so a
generic parser over them would be guesswork, not proof).

What this module IS: a strategy-agnostic accounting-identity and safety-gate
validator that sits on top of an explicit, small "evidence facts" file the
operator (or an LLM doing the qualitative read, e.g. via
`/futures-strategy-audit`) fills in after reading that strategy's canonical
evidence. It:

  - enforces the two required accounting identities mechanically
  - always live-verifies EXECUTION CONTEXT (entry fill model, effective
    tolerance, contract cap) from the CURRENT runtime/risk_rules.yaml rather
    than trusting whatever the evidence doc claims was used -- this is the
    specific lesson this routine exists to encode: entry model and effective
    tolerance must be checked against live runtime, not just asserted in the
    evidence packet
  - applies hard, deterministic safety caps (zero fills, accounting mismatch,
    lookahead/parity defects) that no classification may bypass
  - never invents a VALIDATED/BROKEN/etc. verdict from raw numbers alone --
    the qualitative classification is either taken from the evidence file's
    own `stated_classification` (subject to the caps above overriding it
    downward) or left REQUIRES_OPERATOR_CLASSIFICATION

Evidence facts file schema (JSON), all keys optional -- anything omitted is
reported UNKNOWN, never guessed:

{
  "strategy": "orb_breakout",
  "identity_parity": {
    "raw_candidate_count": 60,
    "candidate_identity_parity": true,
    "direction_parity": true,
    "entry_stop_target_parity": true,
    "timeframe_parity": true,
    "causal_data_availability": true,
    "lookahead_or_partial_bar_dependency": false
  },
  "gate_attrition": [{"gate": "market_condition", "candidates_remaining": 60}, ...],
  "execution": {
    "candidates_reaching_risk_engine": 60,
    "approved": 45,
    "entry_attempts": 45,
    "fills": 21,
    "cancellations": 22,
    "rejects_or_known_no_fills": 2,
    "resolved_outcomes": 20,
    "legitimately_open": 1
  },
  "research_result": {"net_pnl": 1595.70, "profit_factor": 10.36, "win_rate": 0.529, "sample": 34},
  "runtime_parity": {"replay_live_logic_confirmed": true, "notes": "..."},
  "paper_forward_evidence": {"filled_trades": 0, "notes": "no paper-forward fills yet"},
  "execution_context_claimed": {
    "entry_fill_model": "ioc_limit",
    "entry_tolerance_ticks": 32,
    "contract_qty": 1,
    "commission_slippage_assumptions": "1.48 round trip, 1 tick adverse"
  },
  "stated_classification": "PROMISING BUT UNPROVEN",
  "notes": "free text"
}
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ops.project_check.runtime import runtime_snapshot

VALID_CLASSIFICATIONS = {
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
}
UNKNOWN = "UNKNOWN"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_evidence_facts(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"evidence facts file not found: {path}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not parse evidence facts file: {exc}"
    if not isinstance(data, dict):
        return None, "evidence facts file must contain a JSON object"
    return data, None


def _check_accounting_identities(execution: dict[str, Any]) -> dict[str, Any]:
    attempts = execution.get("entry_attempts")
    fills = execution.get("fills")
    cancellations = execution.get("cancellations")
    rejects = execution.get("rejects_or_known_no_fills")
    resolved = execution.get("resolved_outcomes")
    open_ = execution.get("legitimately_open")

    identity_1 = None
    if None not in (attempts, fills, cancellations, rejects):
        identity_1 = {
            "identity": "attempts = fills + cancellations + rejects/known_no_fills",
            "lhs": attempts,
            "rhs": fills + cancellations + rejects,
            "holds": attempts == fills + cancellations + rejects,
        }

    identity_2 = None
    if None not in (fills, resolved, open_):
        identity_2 = {
            "identity": "fills = resolved + legitimately_open",
            "lhs": fills,
            "rhs": resolved + open_,
            "holds": fills == resolved + open_,
        }

    checked = [i for i in (identity_1, identity_2) if i is not None]
    return {
        "identity_attempts": identity_1,
        "identity_fills": identity_2,
        "all_checkable_identities_hold": all(i["holds"] for i in checked) if checked else None,
        "identities_checkable": bool(checked),
    }


def _execution_context_check(*, repo_root: Path, claimed: dict[str, Any]) -> dict[str, Any]:
    live = runtime_snapshot(repo_root=repo_root)
    live_view = {
        "entry_fill_model": live.get("entry_fill_model"),
        "entry_tolerance_ticks": live.get("entry_tolerance_ticks"),
        "quantity_caps": live.get("quantity_caps"),
    }
    mismatches = []
    claimed_fill_model = claimed.get("entry_fill_model")
    if claimed_fill_model and live_view["entry_fill_model"] not in (UNKNOWN, None):
        if str(claimed_fill_model) != str(live_view["entry_fill_model"]):
            mismatches.append(
                f"claimed entry_fill_model={claimed_fill_model!r} != live runtime "
                f"entry_fill_model={live_view['entry_fill_model']!r}"
            )
    live_tol = live_view["entry_tolerance_ticks"] or {}
    claimed_tolerance = claimed.get("entry_tolerance_ticks")
    if claimed_tolerance is not None:
        replay_paper_values = [
            v.get("effective_replay_paper")
            for k, v in live_tol.items()
            if isinstance(v, dict) and "effective_replay_paper" in v
        ]
        def _as_float(value: Any) -> float | None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        claimed_float = _as_float(claimed_tolerance)
        replay_paper_floats = [_as_float(v) for v in replay_paper_values]
        if replay_paper_values and (claimed_float is None or claimed_float not in replay_paper_floats):
            mismatches.append(
                f"claimed entry_tolerance_ticks={claimed_tolerance!r} does not match any live "
                f"runtime replay/paper-path tolerance {live_tol}"
            )
    for root, info in live_tol.items():
        if isinstance(info, dict) and info.get("diverges"):
            mismatches.append(
                f"entry tolerance for {root} is unpinned (env unset): replay/paper path would "
                f"use {info.get('effective_replay_paper')} ticks but the live Tradovate broker "
                f"path would use {info.get('effective_live_broker')} ticks -- pin "
                f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{root} before treating this as promotion evidence"
            )
    return {
        "claimed": claimed,
        "live_verified": live_view,
        "mismatches": mismatches,
        "parity_ok": not mismatches,
    }


def _safety_caps(
    *,
    identity_parity: dict[str, Any],
    accounting: dict[str, Any],
    execution: dict[str, Any],
    execution_context: dict[str, Any],
    stated_classification: str | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    fills = execution.get("fills")
    if fills == 0:
        blockers.append("zero executable fills: cannot be classified VALIDATED")
    elif fills is None:
        warnings.append("fills count not supplied -- cannot confirm any executable fills occurred")

    if accounting.get("identities_checkable") and accounting.get("all_checkable_identities_hold") is False:
        blockers.append(
            "execution accounting identity does not hold (attempts/fills/cancellations/"
            "rejects or fills/resolved/open) -- the counts are not internally consistent"
        )

    if identity_parity.get("lookahead_or_partial_bar_dependency") is True:
        blockers.append("lookahead or partial-bar dependency reported present")

    for field in (
        "candidate_identity_parity",
        "direction_parity",
        "entry_stop_target_parity",
        "timeframe_parity",
        "causal_data_availability",
    ):
        if identity_parity.get(field) is False:
            blockers.append(f"identity/parity defect: {field} is False")

    if not execution_context.get("parity_ok", True):
        blockers.append(
            "execution-context parity defect: " + "; ".join(execution_context.get("mismatches", []))
        )

    capped = bool(blockers)
    effective = stated_classification
    override_reason = None
    if capped and stated_classification == "VALIDATED":
        effective = "PROMISING BUT UNPROVEN"
        override_reason = (
            "stated_classification was VALIDATED but one or more hard safety caps triggered; "
            "downgraded automatically -- see blockers"
        )
    if stated_classification is None:
        effective = "REQUIRES_OPERATOR_CLASSIFICATION"

    if stated_classification is not None and stated_classification not in VALID_CLASSIFICATIONS:
        warnings.append(
            f"stated_classification {stated_classification!r} is not one of {sorted(VALID_CLASSIFICATIONS)}"
        )

    return {
        "blockers": blockers,
        "warnings": warnings,
        "stated_classification": stated_classification,
        "effective_classification": effective,
        "override_reason": override_reason,
    }


def build_promotion_report(
    *,
    strategy: str,
    repo_root: str | Path,
    evidence_path: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root)
    evidence: dict[str, Any] = {}
    evidence_error = None
    if evidence_path is not None:
        loaded, evidence_error = load_evidence_facts(Path(evidence_path))
        if loaded is not None:
            evidence = loaded

    identity_parity = evidence.get("identity_parity") or {}
    gate_attrition = evidence.get("gate_attrition") or []
    execution = evidence.get("execution") or {}
    research_result = evidence.get("research_result") or {}
    runtime_parity = evidence.get("runtime_parity") or {}
    paper_forward_evidence = evidence.get("paper_forward_evidence") or {}
    execution_context_claimed = evidence.get("execution_context_claimed") or {}
    stated_classification = evidence.get("stated_classification")

    accounting = _check_accounting_identities(execution)
    execution_context = _execution_context_check(repo_root=root, claimed=execution_context_claimed)
    caps = _safety_caps(
        identity_parity=identity_parity,
        accounting=accounting,
        execution=execution,
        execution_context=execution_context,
        stated_classification=stated_classification,
    )

    return {
        "ok": evidence_error is None,
        "routine": "promotion-proof-gate",
        "generated_at": _now_iso(),
        "strategy": strategy,
        "evidence_path": str(evidence_path) if evidence_path else None,
        "evidence_load_error": evidence_error,
        "evidence_supplied": bool(evidence),
        "identity_parity": {
            k: identity_parity.get(k, None)
            for k in (
                "raw_candidate_count",
                "candidate_identity_parity",
                "direction_parity",
                "entry_stop_target_parity",
                "timeframe_parity",
                "causal_data_availability",
                "lookahead_or_partial_bar_dependency",
            )
        },
        "gate_attrition": gate_attrition,
        "execution": execution,
        "accounting_identities": accounting,
        "research_result": research_result,
        "runtime_parity": runtime_parity,
        "paper_forward_evidence": paper_forward_evidence,
        "execution_context": execution_context,
        "classification": caps,
        "notes": evidence.get("notes"),
        "forbidden_actions_reminder": (
            "This routine never modifies strategy code, risk config, or runtime state, "
            "never enables/disables/tunes a strategy, and never authorizes a rescue/"
            "tuning variant in the same pass. It only classifies and reports why."
        ),
    }
