"""Strategy Promotion Proof Gate — read-only.

Precedent: Miyagi and 60M 3-2-2 both looked profitable in standalone
research/backtest form, but the real executable path (ReplayEngine ->
DecisionEngine -> RiskEngine -> PaperBroker) rejected most/all of the
population. This gate does not re-run that pipeline itself -- the repo
already has a family of bespoke per-strategy canonical-evidence scripts
that do (scripts/*_canonical_evidence*.py, scripts/strat_*_evidence*.py)
and each strategy's population/config/isolation is different enough that
re-implementing a generic runner here would duplicate that machinery badly.

Instead this gate defines the evidence CONTRACT those runs must produce to
be promotion-eligible, and mechanically checks it:
  - every required section is present (missing -> reported, never guessed)
  - the two accounting identities hold
  - the "no rescue variant / no silent risk exemption / state zero fills"
    rules are satisfied
  - the final classification is one of the allowed values and is not
    silently upgraded past what the numbers support

It never runs a strategy, never edits risk_rules.yaml or any strategy
module, never enables anything, and never merges/deploys. Feed it the JSON
your canonical-evidence run already produces (or hand-assemble one) via
--evidence; run() reads that file only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MISSING = "MISSING"

ALLOWED_CLASSIFICATIONS = {
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
}

REQUIRED_IDENTITY_FIELDS = (
    "raw_candidate_count",
    "candidate_identity_parity",
    "direction_parity",
    "entry_stop_target_parity",
    "timeframe_parity",
    "causal_data_availability",
    "lookahead_or_partial_bar_dependency",
)

REQUIRED_EXECUTION_FIELDS = (
    "candidates_reaching_risk_engine",
    "candidates_approved",
    "entry_attempts",
    "fills",
    "cancellations",
    "rejects_or_known_no_fills",
    "resolved_outcomes",
    "legitimately_open_positions",
)

REQUIRED_EXECUTION_CONTEXT_FIELDS = (
    "actual_entry_model",
    "actual_effective_tolerance",
    "actual_commission_slippage_assumptions",
    "contract_quantity",
    "account_risk_caps",
)

REQUIRED_PERFORMANCE_FIELDS = (
    "net_pnl",
    "profit_factor",
    "expectancy",
    "win_rate",
)

REQUIRED_CLASSIFICATION_FIELDS = (
    "research_result",
    "runtime_parity",
    "paper_forward_evidence",
    "final",
)

REQUIRED_ATTESTATION_FIELDS = (
    "rescue_or_tuning_variant_in_same_pass",
    "risk_controls_exempted_to_reproduce_research",
    "zero_executable_fills",
)


def _get(d: dict[str, Any], key: str) -> Any:
    return d.get(key, MISSING)


@dataclass
class Blocker:
    code: str
    detail: str


@dataclass
class PromotionGateReport:
    strategy: str
    evidence_path: str
    missing_fields: list[str] = field(default_factory=list)
    blockers: list[Blocker] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    accounting: dict[str, Any] = field(default_factory=dict)
    declared_classification: dict[str, Any] = field(default_factory=dict)
    gate_verdict: str = "UNKNOWN"

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate_name": "strategy_promotion_proof_gate",
            "read_only": True,
            "strategy": self.strategy,
            "evidence_path": self.evidence_path,
            "missing_fields": self.missing_fields,
            "blockers": [b.__dict__ for b in self.blockers],
            "warnings": self.warnings,
            "accounting": self.accounting,
            "declared_classification": self.declared_classification,
            "gate_verdict": self.gate_verdict,
            "no_automatic_action": (
                "This gate never enables a strategy, edits config/risk rules, "
                "merges, or deploys. A passing gate_verdict authorizes nothing "
                "by itself -- it only says the evidence artifact is internally "
                "consistent and complete enough to hand to a human reviewer."
            ),
        }


def _check_required(section: dict[str, Any] | Any, fields: tuple[str, ...], prefix: str) -> list[str]:
    if not isinstance(section, dict):
        return [f"{prefix}.{f}" for f in fields] + [prefix]
    return [f"{prefix}.{f}" for f in fields if f not in section]


def _accounting_check(execution: dict[str, Any]) -> dict[str, Any]:
    """attempts = fills + cancellations + rejects; fills = resolved + open."""
    if not isinstance(execution, dict):
        return {"checked": False, "reason": "execution section missing or not an object"}
    required = REQUIRED_EXECUTION_FIELDS
    if any(f not in execution for f in required):
        return {"checked": False, "reason": "one or more required execution fields missing"}
    try:
        attempts = int(execution["entry_attempts"])
        fills = int(execution["fills"])
        cancellations = int(execution["cancellations"])
        rejects = int(execution["rejects_or_known_no_fills"])
        resolved = int(execution["resolved_outcomes"])
        open_pos = int(execution["legitimately_open_positions"])
    except (TypeError, ValueError):
        return {"checked": False, "reason": "one or more execution fields are not integers"}

    attempts_identity_ok = attempts == (fills + cancellations + rejects)
    fills_identity_ok = fills == (resolved + open_pos)
    return {
        "checked": True,
        "attempts": attempts,
        "fills": fills,
        "cancellations": cancellations,
        "rejects_or_known_no_fills": rejects,
        "resolved_outcomes": resolved,
        "legitimately_open_positions": open_pos,
        "attempts_identity_ok": attempts_identity_ok,
        "attempts_identity": f"{attempts} == {fills} + {cancellations} + {rejects}",
        "fills_identity_ok": fills_identity_ok,
        "fills_identity": f"{fills} == {resolved} + {open_pos}",
        "ok": attempts_identity_ok and fills_identity_ok,
    }


def evaluate_promotion_evidence(evidence: dict[str, Any], *, strategy: str | None = None) -> dict[str, Any]:
    declared_strategy = str(evidence.get("strategy") or "")
    report = PromotionGateReport(
        strategy=declared_strategy or (strategy or MISSING),
        evidence_path="(in-memory)",
    )

    if strategy and declared_strategy and strategy != declared_strategy:
        report.blockers.append(Blocker(
            "strategy_name_mismatch",
            f"--strategy {strategy!r} does not match evidence.strategy {declared_strategy!r}",
        ))

    missing: list[str] = []
    missing += _check_required(evidence.get("identity_parity"), REQUIRED_IDENTITY_FIELDS, "identity_parity")
    missing += _check_required(evidence.get("execution"), REQUIRED_EXECUTION_FIELDS, "execution")
    missing += _check_required(
        evidence.get("execution_context"), REQUIRED_EXECUTION_CONTEXT_FIELDS, "execution_context"
    )
    missing += _check_required(evidence.get("performance"), REQUIRED_PERFORMANCE_FIELDS, "performance")
    missing += _check_required(
        evidence.get("classification"), REQUIRED_CLASSIFICATION_FIELDS, "classification"
    )
    missing += _check_required(
        evidence.get("attestation"), REQUIRED_ATTESTATION_FIELDS, "attestation"
    )
    if "gate_attrition" not in evidence:
        missing.append("gate_attrition")
    report.missing_fields = sorted(set(missing))

    accounting = _accounting_check(evidence.get("execution") or {})
    report.accounting = accounting
    if accounting.get("checked") and not accounting.get("ok"):
        report.blockers.append(Blocker(
            "accounting_identity_violation",
            f"attempts identity ok={accounting['attempts_identity_ok']} "
            f"({accounting['attempts_identity']}); "
            f"fills identity ok={accounting['fills_identity_ok']} "
            f"({accounting['fills_identity']})",
        ))

    attestation = evidence.get("attestation") or {}
    if isinstance(attestation, dict):
        if attestation.get("rescue_or_tuning_variant_in_same_pass") is True:
            report.blockers.append(Blocker(
                "rescue_variant_in_same_pass",
                "attestation declares a rescue/tuning variant was run in the same validation pass",
            ))
        if attestation.get("risk_controls_exempted_to_reproduce_research") is True:
            report.blockers.append(Blocker(
                "risk_controls_exempted",
                "attestation declares legitimate account risk controls were exempted to "
                "reproduce research numbers",
            ))
        if attestation.get("zero_executable_fills") is True:
            report.warnings.append("zero executable fills -- stated explicitly per gate requirement")

    classification = evidence.get("classification") or {}
    report.declared_classification = classification if isinstance(classification, dict) else {}
    final = report.declared_classification.get("final")
    if final is not None and final not in ALLOWED_CLASSIFICATIONS:
        report.blockers.append(Blocker(
            "invalid_classification_value",
            f"classification.final={final!r} is not one of {sorted(ALLOWED_CLASSIFICATIONS)}",
        ))

    if accounting.get("checked") and accounting.get("ok"):
        fills = accounting.get("fills", 0)
        if fills == 0 and final == "VALIDATED":
            report.blockers.append(Blocker(
                "validated_with_zero_fills",
                "classification.final=VALIDATED but execution.fills=0 -- zero executable "
                "fills cannot be classified VALIDATED",
            ))

    if report.blockers:
        report.gate_verdict = "BLOCKED"
    elif report.missing_fields:
        report.gate_verdict = "INCOMPLETE"
    elif final is None:
        report.gate_verdict = "INCOMPLETE"
    else:
        report.gate_verdict = f"EVIDENCE_CONSISTENT ({final})"

    return report.as_dict()


def run(evidence_path: str | Path, *, strategy: str | None = None) -> dict[str, Any]:
    path = Path(evidence_path)
    if not path.exists():
        return PromotionGateReport(
            strategy=strategy or MISSING,
            evidence_path=str(path),
            blockers=[Blocker("evidence_file_not_found", f"{path} does not exist")],
            gate_verdict="BLOCKED",
        ).as_dict()
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return PromotionGateReport(
            strategy=strategy or MISSING,
            evidence_path=str(path),
            blockers=[Blocker("evidence_file_unreadable", str(exc))],
            gate_verdict="BLOCKED",
        ).as_dict()
    if not isinstance(evidence, dict):
        return PromotionGateReport(
            strategy=strategy or MISSING,
            evidence_path=str(path),
            blockers=[Blocker("evidence_file_not_object", "top-level JSON must be an object")],
            gate_verdict="BLOCKED",
        ).as_dict()
    result = evaluate_promotion_evidence(evidence, strategy=strategy)
    result["evidence_path"] = str(path)
    return result


def format_report(report: dict[str, Any]) -> str:
    lines = [
        "STRATEGY PROMOTION PROOF GATE",
        f"strategy: {report['strategy']}",
        f"evidence: {report['evidence_path']}",
        f"gate_verdict: {report['gate_verdict']}",
        "",
    ]
    if report["missing_fields"]:
        lines.append(f"Missing required fields ({len(report['missing_fields'])}):")
        for f in report["missing_fields"]:
            lines.append(f"  - {f}")
    else:
        lines.append("Missing required fields: none")
    lines.append("")
    acc = report["accounting"]
    if acc.get("checked"):
        lines.append(
            f"Accounting: attempts identity {'OK' if acc['attempts_identity_ok'] else 'FAIL'} "
            f"({acc['attempts_identity']}); "
            f"fills identity {'OK' if acc['fills_identity_ok'] else 'FAIL'} ({acc['fills_identity']})"
        )
    else:
        lines.append(f"Accounting: not checked ({acc.get('reason')})")
    lines.append("")
    if report["blockers"]:
        lines.append(f"BLOCKERS ({len(report['blockers'])}):")
        for b in report["blockers"]:
            lines.append(f"  - {b['code']}: {b['detail']}")
    else:
        lines.append("Blockers: none")
    if report["warnings"]:
        lines.append("Warnings:")
        for w in report["warnings"]:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append(f"Declared classification: {report['declared_classification']}")
    lines.append("")
    lines.append(report["no_automatic_action"])
    return "\n".join(lines)
