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
  - requires per-entry-attempt observed contract quantities from the canonical
    execution evidence and reconciles them against both the attempt count and
    the claimed contract quantity; a cap-compatible claim alone is not proof
  - applies hard, deterministic safety caps (zero fills, accounting mismatch,
    lookahead/parity defects) that no classification may bypass
  - never invents a VALIDATED/BROKEN/etc. verdict from raw numbers alone --
    the qualitative classification is either taken from the evidence file's
    own `stated_classification` (subject to the caps above overriding it
    downward) or left REQUIRES_OPERATOR_CLASSIFICATION

U5: the packet must list canonical evidence bundles
(`"canonical_evidence": {"bundles": ["docs/research-evidence/<trial_id>"]}`)
that classify PROMOTION_QUALITY under ops/evidence_identity.py. Execution
accounting, per-fill quantities, instrument, research result, entry fill
model and causal timing are then derived from those bundles and replace the
packet values. Quantity proof must cover every canonical entry attempt; a
cancellation/no-fill without canonical quantity evidence blocks promotion.
A contradicting packet value is also a blocker. Facts the bundles
cannot prove (identity parity, lookahead, runtime parity) remain attested. Any
blocker sets the effective classification to BLOCKED_BY_HARD_CAP.

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
    "entry_attempts": 3,
    "entry_attempt_contract_quantities": [1, 1, 1],
    "fills": 2,
    "cancellations": 1,
    "rejects_or_known_no_fills": 0,
    "resolved_outcomes": 2,
    "legitimately_open": 0
  },
  "research_result": {"net_pnl": 1595.70, "profit_factor": 10.36, "win_rate": 0.529, "sample": 34},
  "runtime_parity": {"replay_live_logic_confirmed": true, "notes": "..."},
  "paper_forward_evidence": {"filled_trades": 0, "notes": "no paper-forward fills yet"},
  "execution_context_claimed": {
    "instrument": "MNQ",
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

from ops.project_check.canonical_promotion_evidence import (
    FLOAT_TOLERANCE,
    RATE_TOLERANCE,
    contradictions,
    derive_canonical_facts,
)
from ops.project_check.runtime import runtime_snapshot
from ops.project_check.daily import STRATEGY_NAME_ALIASES, _normalize as _normalize_strategy_name

VALID_CLASSIFICATIONS = {
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
}
UNKNOWN = "UNKNOWN"
POSITIVE_CLASSIFICATIONS = frozenset({"VALIDATED", "PROMISING BUT UNPROVEN"})
HARD_CAP_CLASSIFICATION = "BLOCKED_BY_HARD_CAP"


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
    mismatches: list[str] = []

    def _as_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _as_positive_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str):
            raw = value.strip()
            if raw.isdigit():
                parsed = int(raw)
                return parsed if parsed > 0 else None
        return None

    instrument_raw = claimed.get("instrument")
    instrument = str(instrument_raw).strip().upper() if instrument_raw not in (None, "") else None
    claimed_tolerance = claimed.get("entry_tolerance_ticks")
    claimed_qty = claimed.get("contract_qty")
    if (claimed_tolerance is not None or claimed_qty is not None) and instrument is None:
        mismatches.append(
            "execution_context_claimed.instrument is required when entry_tolerance_ticks "
            "or contract_qty is claimed"
        )

    claimed_fill_model = claimed.get("entry_fill_model")
    if claimed_fill_model and live_view["entry_fill_model"] not in (UNKNOWN, None):
        if str(claimed_fill_model) != str(live_view["entry_fill_model"]):
            mismatches.append(
                f"claimed entry_fill_model={claimed_fill_model!r} != live runtime "
                f"entry_fill_model={live_view['entry_fill_model']!r}"
            )

    live_tol = live_view["entry_tolerance_ticks"] or {}
    if claimed_tolerance is not None and instrument is not None:
        info = live_tol.get(instrument)
        if not isinstance(info, dict) or "effective_replay_paper" not in info:
            mismatches.append(
                f"no live runtime replay/paper-path tolerance is available for instrument {instrument}"
            )
        else:
            claimed_float = _as_float(claimed_tolerance)
            live_float = _as_float(info.get("effective_replay_paper"))
            if claimed_float is None or live_float is None or claimed_float != live_float:
                mismatches.append(
                    f"claimed entry_tolerance_ticks={claimed_tolerance!r} for {instrument} != "
                    f"live runtime replay/paper-path tolerance {info.get('effective_replay_paper')!r}"
                )

    roots_to_check = [instrument] if instrument is not None else [
        root for root, info in live_tol.items() if isinstance(info, dict)
    ]
    for root in roots_to_check:
        info = live_tol.get(root)
        if isinstance(info, dict) and info.get("diverges"):
            mismatches.append(
                f"entry tolerance for {root} is unpinned (env unset): replay/paper path would "
                f"use {info.get('effective_replay_paper')} ticks but the live Tradovate broker "
                f"path would use {info.get('effective_live_broker')} ticks -- pin "
                f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{root} before treating this as promotion evidence"
            )

    if claimed_qty is not None and instrument is not None:
        claimed_qty_int = _as_positive_int(claimed_qty)
        if claimed_qty_int is None:
            mismatches.append(f"claimed contract_qty={claimed_qty!r} is not a positive integer")
        else:
            caps = live_view["quantity_caps"] or {}
            per_instrument = caps.get("max_contracts_per_instrument_config")
            per_cap = per_instrument.get(instrument) if isinstance(per_instrument, dict) else None
            hard_cap = caps.get("hard_cap_env")
            known_caps = [
                cap
                for cap in (_as_positive_int(per_cap), _as_positive_int(hard_cap))
                if cap is not None
            ]
            if not known_caps:
                mismatches.append(
                    f"could not resolve a live contract cap for instrument {instrument}"
                )
            else:
                effective_cap = min(known_caps)
                if claimed_qty_int > effective_cap:
                    mismatches.append(
                        f"claimed contract_qty={claimed_qty_int} for {instrument} exceeds "
                        f"live effective contract cap {effective_cap}"
                    )

    return {
        "claimed": claimed,
        "live_verified": live_view,
        "quantity_check_semantics": (
            "two_layer: claimed contract_qty is checked against configured caps and "
            "promotion additionally requires per-entry-attempt observed quantities to "
            "match that claim"
        ),
        "mismatches": mismatches,
        "parity_ok": not mismatches,
    }


def _check_quantity_evidence(
    *,
    execution: dict[str, Any],
    execution_context_claimed: dict[str, Any],
) -> dict[str, Any]:
    """Verify observed quantity evidence for every recorded entry attempt.

    The evidence-facts packet must carry one observed quantity per entry
    attempt, copied from the canonical execution evidence. This does not parse
    broker journals itself; it prevents a promotion PASS from relying only on a
    cap-compatible claimed quantity.
    """
    problems: list[str] = []

    def _strict_positive_int(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value > 0 else None
        if isinstance(value, str):
            raw = value.strip()
            if raw.isdigit():
                parsed = int(raw)
                return parsed if parsed > 0 else None
        return None

    attempts_raw = execution.get("entry_attempts")
    attempts = _strict_positive_int(attempts_raw)
    if attempts is None:
        problems.append("execution.entry_attempts must be a positive integer for quantity proof")

    claimed_raw = execution_context_claimed.get("contract_qty")
    claimed_qty = _strict_positive_int(claimed_raw)
    if claimed_qty is None:
        problems.append("execution_context_claimed.contract_qty must be a positive integer")

    quantities_raw = execution.get("entry_attempt_contract_quantities")
    quantities: list[int] = []
    if not isinstance(quantities_raw, list):
        problems.append(
            "execution.entry_attempt_contract_quantities must be a list with one observed "
            "quantity per entry attempt"
        )
    else:
        for index, raw in enumerate(quantities_raw):
            parsed = _strict_positive_int(raw)
            if parsed is None:
                problems.append(
                    f"execution.entry_attempt_contract_quantities[{index}] must be a positive integer"
                )
            else:
                quantities.append(parsed)

        if attempts is not None and len(quantities_raw) != attempts:
            problems.append(
                "execution.entry_attempt_contract_quantities count "
                f"{len(quantities_raw)} does not match entry_attempts {attempts}"
            )

        if claimed_qty is not None and quantities and any(qty != claimed_qty for qty in quantities):
            observed = sorted(set(quantities))
            problems.append(
                "observed entry-attempt contract quantities "
                f"{observed} do not all match claimed contract_qty {claimed_qty}"
            )

    return {
        "entry_attempts": attempts_raw,
        "observed_contract_quantities": quantities_raw,
        "claimed_contract_qty": claimed_raw,
        "verified": not problems,
        "problems": problems,
    }


def _safety_caps(
    *,
    identity_parity: dict[str, Any],
    accounting: dict[str, Any],
    execution: dict[str, Any],
    execution_context: dict[str, Any],
    quantity_evidence: dict[str, Any],
    runtime_parity: dict[str, Any],
    execution_context_claimed: dict[str, Any],
    stated_classification: str | None,
    canonical_blockers: list[str] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = list(canonical_blockers or [])
    warnings: list[str] = []

    fills = execution.get("fills")
    if fills is None:
        blockers.append("fills count not supplied -- executable fills are unverified")
    else:
        try:
            fills_value = int(fills)
        except (TypeError, ValueError):
            blockers.append(f"fills count is not an integer: {fills!r}")
        else:
            if fills_value <= 0:
                blockers.append("zero executable fills: promotion proof requires at least one fill")

    # Both accounting identities are mandatory proof, not best-effort checks.
    # A partial packet must never pass simply because the missing identity was
    # skipped.
    if accounting.get("identity_attempts") is None or accounting.get("identity_fills") is None:
        blockers.append(
            "execution accounting identities are incomplete -- both attempts=fill/cancel/reject "
            "and fills=resolved/open must be checkable"
        )
    elif accounting.get("all_checkable_identities_hold") is not True:
        blockers.append(
            "execution accounting identity does not hold (attempts/fills/cancellations/"
            "rejects or fills/resolved/open) -- the counts are not internally consistent"
        )

    # UNKNOWN is unsafe for promotion. These fields must be explicitly proven,
    # rather than only blocking when an operator happened to write False.
    if identity_parity.get("lookahead_or_partial_bar_dependency") is not False:
        blockers.append(
            "lookahead/partial-bar safety is unverified -- "
            "lookahead_or_partial_bar_dependency must be explicitly false"
        )

    for field in (
        "candidate_identity_parity",
        "direction_parity",
        "entry_stop_target_parity",
        "timeframe_parity",
        "causal_data_availability",
    ):
        if identity_parity.get(field) is not True:
            blockers.append(f"identity/parity proof missing or failed: {field} must be explicitly true")

    if runtime_parity.get("replay_live_logic_confirmed") is not True:
        blockers.append(
            "replay/live logic parity is unverified -- "
            "runtime_parity.replay_live_logic_confirmed must be explicitly true"
        )

    for field in (
        "instrument",
        "entry_fill_model",
        "entry_tolerance_ticks",
        "contract_qty",
        "commission_slippage_assumptions",
    ):
        value = execution_context_claimed.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            blockers.append(f"execution context proof missing: execution_context_claimed.{field}")

    if not execution_context.get("parity_ok", True):
        blockers.append(
            "execution-context parity defect: " + "; ".join(execution_context.get("mismatches", []))
        )

    if quantity_evidence.get("verified") is not True:
        problems = quantity_evidence.get("problems") or ["quantity evidence is missing"]
        blockers.append(
            "entry-attempt contract quantity evidence is unverified -- " + "; ".join(problems)
        )

    capped = bool(blockers)
    effective = stated_classification
    override_reason = None
    # U5: no positive classification survives a hard cap. Previously only
    # VALIDATED was downgraded (to PROMISING BUT UNPROVEN), so a stated
    # PROMISING BUT UNPROVEN rode through parity/lookahead/evidence blockers.
    if capped and stated_classification in POSITIVE_CLASSIFICATIONS:
        effective = HARD_CAP_CLASSIFICATION
        override_reason = (
            f"stated_classification was {stated_classification} but one or more hard "
            "safety caps triggered; blocked automatically -- see blockers"
        )
    if stated_classification is None:
        effective = "REQUIRES_OPERATOR_CLASSIFICATION"

    if stated_classification is None:
        blockers.append("stated_classification is required for promotion proof")
    elif stated_classification not in VALID_CLASSIFICATIONS:
        blockers.append(
            f"stated_classification {stated_classification!r} is not one of {sorted(VALID_CLASSIFICATIONS)}"
        )

    return {
        "blockers": blockers,
        "warnings": warnings,
        "stated_classification": stated_classification,
        "effective_classification": effective,
        "override_reason": override_reason,
    }


def resolve_strategy_identity(name: Any) -> str | None:
    """Resolve a promotion target to a canonical strategy identity.

    Confirmed repo-owned aliases (``STRATEGY_NAME_ALIASES``) normalize to their
    canonical key. Any other non-empty string is treated literally and can pass
    only if it exactly equals the canonical evidence strategy_identity. Nothing
    is fuzzily matched or inferred from packet text.
    """
    if not isinstance(name, str) or not name.strip():
        return None
    return STRATEGY_NAME_ALIASES.get(_normalize_strategy_name(name), name.strip())


def _bind_strategy_identity(
    strategy: str,
    *,
    packet_strategy: Any,
    derived: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """The promotion target must be the strategy the canonical rows prove."""
    blockers: list[str] = []
    target = resolve_strategy_identity(strategy)
    canonical = derived.get("strategy_identity")
    if target is None:
        blockers.append("promotion target strategy is empty")
    elif canonical is None:
        blockers.append("canonical evidence has no single strategy_identity to bind")
    elif target != canonical:
        blockers.append(
            f"promotion target strategy {strategy!r} (resolved {target!r}) does not match "
            f"canonical evidence strategy_identity {canonical!r}"
        )
    if packet_strategy not in (None, ""):
        packet_target = resolve_strategy_identity(packet_strategy)
        if packet_target != target:
            blockers.append(
                f"evidence packet strategy {packet_strategy!r} contradicts promotion target "
                f"{strategy!r}"
            )
    binding = {
        "requested": strategy,
        "resolved": target,
        "canonical_strategy_identity": canonical,
        "packet_strategy": packet_strategy,
        "bound": not blockers,
    }
    return binding, blockers


def _apply_canonical_facts(
    derived: dict[str, Any],
    *,
    execution: dict[str, Any],
    identity_parity: dict[str, Any],
    research_result: dict[str, Any],
    execution_context_claimed: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    """Replace author-supplied facts with canonical ones; contradictions block."""
    blockers: list[str] = []
    derived_execution = derived["execution"]
    blockers.extend(contradictions(execution, derived_execution, prefix="execution"))
    blockers.extend(
        contradictions(research_result, {"net_pnl": derived["research_result"]["net_pnl"]},
                       prefix="research_result", tolerance=FLOAT_TOLERANCE)
    )
    blockers.extend(
        contradictions(
            research_result,
            {
                key: derived["research_result"][key]
                for key in ("win_rate", "profit_factor", "sample")
                if derived["research_result"][key] is not None
            },
            prefix="research_result",
            tolerance=RATE_TOLERANCE,
        )
    )

    parity = dict(identity_parity)
    if parity.get("causal_data_availability") is False:
        blockers.append(
            "supplied identity_parity.causal_data_availability=false contradicts canonical "
            "row timing; resolve before promotion"
        )
    else:
        parity["causal_data_availability"] = derived["causal_data_availability"]

    instruments = derived["instruments"]
    claimed_instrument = execution_context_claimed.get("instrument")
    if len(instruments) != 1:
        blockers.append(
            f"canonical evidence spans instruments {instruments}; promotion proof must be "
            "for exactly one instrument"
        )
    elif claimed_instrument not in (None, "") and str(claimed_instrument).strip().upper() != instruments[0]:
        blockers.append(
            f"claimed instrument {claimed_instrument!r} contradicts canonical evidence "
            f"instrument {instruments[0]!r}"
        )
    evidence_model = derived.get("entry_fill_model")
    claimed_model = execution_context_claimed.get("entry_fill_model")
    if not evidence_model:
        blockers.append("canonical bundles disagree on (or omit) the entry_fill_model assumption")
    elif claimed_model not in (None, "") and str(claimed_model) != evidence_model:
        blockers.append(
            f"claimed entry_fill_model {claimed_model!r} contradicts the canonical evidence "
            f"execution assumption {evidence_model!r}"
        )
    merged_execution = dict(execution)
    merged_execution.update(derived_execution)
    merged_research = dict(research_result)
    merged_research.update(derived["research_result"])
    return merged_execution, parity, merged_research, blockers


def _check_canonical_quantity(
    derived: dict[str, Any],
    *,
    execution_context_claimed: dict[str, Any],
) -> dict[str, Any]:
    """Quantity proof from canonical FILLED rows (one observed quantity per fill)."""
    problems: list[str] = []
    claimed_raw = execution_context_claimed.get("contract_qty")
    claimed = (
        claimed_raw
        if isinstance(claimed_raw, int) and not isinstance(claimed_raw, bool) and claimed_raw > 0
        else None
    )
    if claimed is None:
        problems.append("execution_context_claimed.contract_qty must be a positive integer")
    quantities = derived["filled_contract_quantities"]
    attempts = (derived.get("execution") or {}).get("entry_attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts <= 0:
        problems.append("canonical execution.entry_attempts must be a positive integer")
    if not quantities:
        problems.append("canonical evidence has no FILLED rows to prove contract quantity")
    elif isinstance(attempts, int) and not isinstance(attempts, bool) and len(quantities) != attempts:
        problems.append(
            "canonical quantity coverage is incomplete: "
            f"{len(quantities)} observed quantities for {attempts} entry attempts; "
            "every entry attempt, including cancellations/no-fills, requires quantity proof"
        )
    if claimed is not None and quantities and any(q != claimed for q in quantities):
        problems.append(
            f"canonical filled contract quantities {sorted(set(quantities))} do not all "
            f"match claimed contract_qty {claimed}"
        )
    return {
        "source": "canonical_evidence",
        "observed_contract_quantities": quantities,
        "claimed_contract_qty": claimed_raw,
        "verified": not problems,
        "problems": problems,
    }


def _check_fault_injection(root: Path, supplied: Any, derived: dict[str, Any] | None) -> dict[str, Any]:
    """Exact-SHA FI proof for the code SHA derived from canonical evidence."""
    from ops.fault_injection_gate import load_manifest, verify_manifest

    manifest_ref = supplied.get("manifest") if isinstance(supplied, dict) else None
    manifest = None
    path_blocker = None
    if isinstance(manifest_ref, str) and manifest_ref.strip():
        candidate = Path(manifest_ref)
        candidate = candidate if candidate.is_absolute() else root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            path_blocker = "fault-injection manifest must resolve inside the repository root"
        else:
            manifest = load_manifest(resolved)
    code_sha = (derived or {}).get("code_sha")
    blockers: list[str] = []
    if path_blocker:
        blockers.append(f"fault-injection proof: {path_blocker}")
    blockers.extend(
        f"fault-injection proof: {problem}"
        for problem in verify_manifest(root, manifest, code_sha=code_sha)
    )
    return {
        "manifest": manifest_ref,
        "code_sha": code_sha,
        "verified": not blockers,
        "blockers": blockers,
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

    canonical = derive_canonical_facts(root, evidence.get("canonical_evidence"))
    canonical_blockers = list(canonical["blockers"])
    derived = canonical.get("derived")
    fault_injection = _check_fault_injection(root, evidence.get("fault_injection"), derived)
    canonical_blockers.extend(fault_injection["blockers"])
    if derived is not None:
        execution, identity_parity, research_result, extra = _apply_canonical_facts(
            derived,
            execution=execution,
            identity_parity=identity_parity,
            research_result=research_result,
            execution_context_claimed=execution_context_claimed,
        )
        canonical_blockers.extend(extra)
        strategy_binding, binding_blockers = _bind_strategy_identity(
            strategy, packet_strategy=evidence.get("strategy"), derived=derived
        )
        canonical_blockers.extend(binding_blockers)
    else:
        strategy_binding = {"requested": strategy, "bound": False}

    accounting = _check_accounting_identities(execution)
    execution_context = _execution_context_check(repo_root=root, claimed=execution_context_claimed)
    if derived is not None:
        quantity_evidence = _check_canonical_quantity(
            derived, execution_context_claimed=execution_context_claimed
        )
    else:
        quantity_evidence = _check_quantity_evidence(
            execution=execution,
            execution_context_claimed=execution_context_claimed,
        )
    caps = _safety_caps(
        identity_parity=identity_parity,
        accounting=accounting,
        execution=execution,
        execution_context=execution_context,
        quantity_evidence=quantity_evidence,
        runtime_parity=runtime_parity,
        execution_context_claimed=execution_context_claimed,
        stated_classification=stated_classification,
        canonical_blockers=canonical_blockers,
    )

    evidence_supplied = bool(evidence)
    gate_pass = (
        evidence_error is None
        and evidence_supplied
        and not caps["blockers"]
        and execution_context["parity_ok"]
    )

    return {
        "ok": gate_pass,
        "gate_pass": gate_pass,
        "promotion_eligible": gate_pass,
        "routine": "promotion-proof-gate",
        "generated_at": _now_iso(),
        "strategy": strategy,
        "strategy_identity_binding": strategy_binding,
        "evidence_path": str(evidence_path) if evidence_path else None,
        "evidence_load_error": evidence_error,
        "evidence_supplied": evidence_supplied,
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
        "quantity_evidence": quantity_evidence,
        "canonical_evidence": canonical,
        "fault_injection": fault_injection,
        "classification": caps,
        "notes": evidence.get("notes"),
        "forbidden_actions_reminder": (
            "This routine never modifies strategy code, risk config, or runtime state, "
            "never enables/disables/tunes a strategy, and never authorizes a rescue/"
            "tuning variant in the same pass. It only classifies and reports why."
        ),
    }
