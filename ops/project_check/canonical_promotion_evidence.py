"""Promotion facts derived from canonical evidence bundles (U5).

Promotion and demo qualification must not trust author-supplied counts or
booleans when canonical U1–U4 evidence can prove them. This module:

* accepts ``canonical_evidence.bundles`` (repo-relative bundle directories)
  from an evidence-facts packet;
* requires every bundle to classify ``PROMOTION_QUALITY`` under the U4
  identity gate;
* derives execution accounting, per-fill contract quantities, instruments,
  research result, execution model, evaluation partitions and replay-path
  facts from the bundles' candidate-arm ``trade_execution`` rows;
* reports any supplied fact that contradicts the derived value as a blocker.

Facts canonical evidence cannot prove (live/replay identity parity,
lookahead freedom, gap/IOC modelling, stress tests) stay attested and are
listed as such in the report. Read-only; zero promotion authority.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from ops import evidence_identity
from ops import evidence_row as evidence_contract

FUTURES_REPLAY_SETUP_TYPE = "futures_replay"
ATTESTED_ONLY_FACTS = (
    "identity_parity.candidate_identity_parity",
    "identity_parity.direction_parity",
    "identity_parity.entry_stop_target_parity",
    "identity_parity.timeframe_parity",
    "identity_parity.lookahead_or_partial_bar_dependency",
    "runtime_parity.replay_live_logic_confirmed",
)
FLOAT_TOLERANCE = 0.01
RATE_TOLERANCE = 1e-9


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _num_equal(a: Any, b: Any, tol: float) -> bool:
    try:
        fa, fb = float(a), float(b)
    except (TypeError, ValueError):
        return False
    return math.isfinite(fa) and math.isfinite(fb) and abs(fa - fb) <= tol


def _futures_replay_model(assumptions: Mapping[str, Any]) -> dict[str, Any] | None:
    from ops.research_experiment_adapters.futures_replay import (
        FuturesReplayAdapterError,
        parse_execution_assumptions,
    )

    try:
        model = parse_execution_assumptions(assumptions)
    except FuturesReplayAdapterError:
        return None
    return {
        "entry_fill_model": model.entry_fill_model,
        "pessimistic_same_bar": model.pessimistic_both_hit,
        "adverse_slippage_ticks": model.slippage_ticks,
        "commission_round_turn_dollars_per_contract": round(
            2 * (model.commission_per_contract_per_side + model.fees_per_contract_per_side), 6
        ),
    }


def derive_canonical_facts(root: Path, canonical: Any) -> dict[str, Any]:
    """Derive promotion facts from the listed canonical bundles."""
    blockers: list[str] = []
    result: dict[str, Any] = {
        "supplied": canonical if canonical is not None else None,
        "bundles": [],
        "derived": None,
        "attested_only_facts": list(ATTESTED_ONLY_FACTS),
        "blockers": blockers,
    }
    bundles = canonical.get("bundles") if isinstance(canonical, Mapping) else None
    if not isinstance(bundles, list) or not bundles or not all(
        isinstance(item, str) and item.strip() for item in bundles
    ):
        blockers.append(
            "canonical evidence missing: canonical_evidence.bundles must list "
            "PROMOTION_QUALITY bundle directories (docs/research-evidence/<trial_id>)"
        )
        return result
    if len(set(bundles)) != len(bundles):
        blockers.append("canonical_evidence.bundles lists a bundle more than once")

    first_rows = evidence_identity.ledger_first_rows(root)
    rows: list[dict[str, Any]] = []
    partitions: set[str] = set()
    code_shas: set[str] = set()
    strategy_identities: set[str] = set()
    setup_types: set[str] = set()
    models: list[dict[str, Any] | None] = []
    execution_model_ids: set[str] = set()
    entry_models: set[str] = set()
    for rel in bundles:
        bundle_dir = root / rel
        identity = evidence_identity.classify_evidence_bundle(root, bundle_dir, first_rows=first_rows)
        result["bundles"].append(identity.to_dict())
        if identity.status != evidence_identity.PROMOTION_QUALITY:
            blockers.append(
                f"canonical bundle {rel} is {identity.status}, not PROMOTION_QUALITY: "
                + "; ".join(identity.reasons)
            )
            continue
        spec = _load(bundle_dir / "experiment_spec.json")
        candidate = _load(bundle_dir / "candidate_raw.json")
        members = candidate.get("members") if isinstance(candidate, dict) else None
        if not isinstance(members, list) or not members:
            blockers.append(f"canonical bundle {rel} candidate arm has no members")
            continue
        for index, row in enumerate(members):
            try:
                evidence_contract.validate_trade_execution_row(
                    row,
                    expected_execution_model_id=identity.identity.get("execution_model_id"),
                    expected_data_fingerprint=identity.identity.get("data_identity"),
                )
            except evidence_contract.EvidenceContractError as exc:
                blockers.append(f"canonical bundle {rel} candidate member[{index}]: {exc}")
        rows.extend(members)
        partitions.add(str(identity.identity.get("evaluation_partition")))
        code_shas.add(str(identity.identity.get("code_sha")))
        strategy_identities.add(str(identity.identity.get("strategy_identity")))
        execution_model_ids.add(str(identity.identity.get("execution_model_id")))
        setup_types.add(str(spec.get("setup_type")))
        assumptions = spec.get("execution_assumptions") or {}
        entry_models.add(str(assumptions.get("entry_fill_model") or ""))
        models.append(
            _futures_replay_model(assumptions)
            if spec.get("setup_type") == FUTURES_REPLAY_SETUP_TYPE
            else None
        )

    if blockers:
        return result

    filled = [r for r in rows if str(r.get("fill_state")).upper() == evidence_contract.FILL_STATE_FILLED]
    cancelled = [
        r for r in rows
        if str(r.get("fill_state")).upper() == evidence_contract.FILL_STATE_NO_FILL
        and r.get("no_fill_reason") not in (None, "")
    ]
    risk_rejected = [
        r for r in rows
        if str(r.get("fill_state")).upper() == evidence_contract.FILL_STATE_NO_FILL
        and r.get("reject_reason") not in (None, "")
        and r.get("no_fill_reason") in (None, "")
    ]
    quantities = [r.get("contracts") for r in filled]
    if any(isinstance(q, bool) or not isinstance(q, int) or q <= 0 for q in quantities):
        blockers.append(
            "canonical FILLED rows must each carry a positive integer contracts quantity"
        )
    net = [float(r["net_pnl"]) for r in filled]
    r_values = [float(r["r_multiple"]) for r in filled]
    wins = sum(1 for value in r_values if value > 0)
    gains = sum(value for value in net if value > 0)
    losses = -sum(value for value in net if value < 0)
    unique_models = {json.dumps(m, sort_keys=True) for m in models}
    model = models[0] if len(unique_models) == 1 else None
    if len(execution_model_ids) != 1:
        blockers.append("canonical bundles disagree on execution_model_id")
    if len(code_shas) != 1:
        blockers.append("canonical bundles disagree on code_sha")
    if len(strategy_identities) != 1:
        blockers.append(
            "canonical bundles disagree on strategy_identity "
            f"{sorted(strategy_identities)}; promotion evidence must be one strategy"
        )

    result["derived"] = {
        "execution": {
            "candidates_reaching_risk_engine": len(rows),
            "entry_attempts": len(filled) + len(cancelled),
            "fills": len(filled),
            "cancellations": len(cancelled),
            "rejects_or_known_no_fills": 0,
            "risk_rejected": len(risk_rejected),
            "resolved_outcomes": len(filled),
            "legitimately_open": 0,
        },
        "filled_contract_quantities": quantities,
        "instruments": sorted({str(r.get("instrument")) for r in filled}),
        "research_result": {
            "net_pnl": round(sum(net), 2),
            "win_rate": (wins / len(filled)) if filled else None,
            "profit_factor": (gains / losses) if losses > 0 else None,
            "sample": len(filled),
        },
        "evaluation_partitions": sorted(partitions),
        "untouched_oos_proven": "untouched_oos" in partitions,
        "code_sha": next(iter(code_shas)) if len(code_shas) == 1 else None,
        "strategy_identity": next(iter(strategy_identities)) if len(strategy_identities) == 1 else None,
        "execution_model_id": next(iter(execution_model_ids)) if len(execution_model_ids) == 1 else None,
        "entry_fill_model": next(iter(entry_models)) if len(entry_models) == 1 else None,
        "futures_replay_path": setup_types == {FUTURES_REPLAY_SETUP_TYPE},
        "execution_model": model,
        "causal_data_availability": True,
    }
    return result


def contradictions(
    supplied: Mapping[str, Any],
    derived: Mapping[str, Any],
    *,
    prefix: str,
    tolerance: float = 0.0,
) -> list[str]:
    """Blockers for supplied values that disagree with derived values."""
    problems: list[str] = []
    for key, value in derived.items():
        if key not in supplied or supplied.get(key) is None:
            continue
        given = supplied[key]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not _num_equal(given, value, tolerance) or isinstance(given, bool):
                problems.append(
                    f"supplied {prefix}.{key}={given!r} contradicts canonical evidence {value!r}"
                )
        elif given != value:
            problems.append(
                f"supplied {prefix}.{key}={given!r} contradicts canonical evidence {value!r}"
            )
    return problems
