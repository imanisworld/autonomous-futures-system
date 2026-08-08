"""Strategy Promotion Proof Gate.

Enforces the rule already learned twice the hard way in this repo (Miyagi,
60M 3-2-2): a strategy may not be treated as paper-ready on the strength of a
standalone detector/replay result. It must have gone through the REAL
executable path — `replay/replay_engine.py` chaining
`strategy/signal_engine.py` (DecisionEngine) -> `risk/risk_engine.py`
(RiskEngine) -> `execution/paper_broker.py` (PaperBroker) — not a shortcut.

This module does not build a new proof pipeline. The existing canonical
evidence scripts (`scripts/*_canonical_evidence*.py`) already drive candidates
through that real path; this module is the *gate* that (a) refuses to report
anything for a strategy with no registered canonical-evidence artifact, (b)
assembles the RESEARCH RESULT / RUNTIME PARITY / PAPER FORWARD EVIDENCE
sections from existing read-only tools, (c) mechanically enforces the
non-negotiable rules (accounting identities, zero-fill must be stated, no
silently exempted risk controls), and (d) leaves the final
VALIDATED/PROMISING BUT UNPROVEN/BROKEN/OVERFIT/UNSAFE/WAIT classification as
an explicit judgment call for whoever reads the report — this tool does not
auto-declare a strategy safe.

Read only. Never edits risk_rules.yaml, never re-enables a strategy, never
deploys, never merges.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Strategy key -> canonical evidence artifact. A strategy with no entry here
# has NOT been proven through the real executable path by this repo's own
# standard, and the gate refuses to report anything but BLOCKED for it.
CANONICAL_EVIDENCE_REGISTRY: dict[str, dict[str, str]] = {
    "orb_breakout": {
        "instrument": "MNQ",
        "results_json": "scripts/orb_breakout_canonical_evidence_results.json",
        "script": "scripts/orb_breakout_canonical_evidence.py",
        "inventory_row": "ORB Breakout (MNQ)",
        "permission_gate_key": "orb_breakout",
    },
    "vwap_reclaim": {
        "instrument": "MNQ",
        "results_json": "scripts/vwap_reclaim_canonical_evidence_results.json",
        "script": "scripts/vwap_reclaim_canonical_evidence.py",
        "inventory_row": "VWAP Reclaim (MNQ NY)",
        "permission_gate_key": "vwap_reclaim",
    },
    "strat_212_122": {
        "instrument": "MNQ/MES",
        "results_json": "scripts/strat_212_122_canonical_evidence_results.json",
        "script": "scripts/strat_212_122_canonical_evidence_run.py",
        "inventory_row": "60M 3-2-2 First Live",
        "permission_gate_key": "strat_322_first_live",
    },
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _inventory_row(repo_root: Path, label: str) -> str | None:
    inv = repo_root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    if not inv.is_file():
        return None
    for line in inv.read_text(encoding="utf-8").splitlines():
        if line.startswith("|") and label in line:
            return line.strip()
    return None


def _permission_gate_status(repo_root: Path, key: str) -> str:
    """PAPER_ELIGIBLE / commented-out (not eligible) / UNKNOWN, from risk_rules.yaml text.

    Read as text, not YAML-parsed, because the disabled entries in
    risk_rules.yaml are YAML comments (`# key: PAPER_ELIGIBLE`) — a YAML
    parser would simply never see them, which is exactly the "silently
    treated as absent" failure this gate must not have.
    """
    path = repo_root / "risk_rules.yaml"
    if not path.is_file():
        return "UNKNOWN"
    text = path.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            if body.startswith(f"{key}:"):
                return f"commented_out (would be: {body.split(':', 1)[1].strip()})"
        elif stripped.startswith(f"{key}:"):
            return stripped.split(":", 1)[1].strip()
    return "UNKNOWN (not found in strategy_permission_gate block)"


def _lane_paper_forward_evidence(log_dir: str, strategy_key: str) -> dict[str, Any]:
    try:
        from ops.evidence_lane_health import build_snapshot
        snapshot = build_snapshot(log_dir=log_dir)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"UNKNOWN ({exc})"}
    matches = [lane for lane in snapshot.get("lanes", []) if strategy_key in (lane.get("lane") or "")]
    return {"lanes": matches} if matches else {"lanes": [], "note": "no active paper-forward lane found for this strategy key"}


def _strategy_intent_paper_evidence(log_dir: str, strategy_key: str) -> dict[str, Any]:
    try:
        from ops.strategy_intent_audit import build_audit
        audit = build_audit(journal_dir=Path(log_dir))
    except Exception as exc:  # noqa: BLE001
        return {"error": f"UNKNOWN ({exc})"}
    summary = audit.get("summary", {})
    return {
        "selected_strategy_counts_all_journal": summary.get("selected_strategy_counts", {}),
        "this_strategy_selected_count": summary.get("selected_strategy_counts", {}).get(strategy_key, 0),
        "note": "counts are from live/paper journal decisions, not the backtest — this is PAPER FORWARD "
                "EVIDENCE, kept separate from the RESEARCH RESULT (canonical evidence json)",
    }


def build_promotion_report(strategy: str, *, repo_root: Path, log_dir: str = "logs") -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    entry = CANONICAL_EVIDENCE_REGISTRY.get(strategy)

    if entry is None:
        return {
            "read_only": True,
            "generated_at": now,
            "strategy": strategy,
            "classification": "BLOCKED",
            "why": (
                f"No canonical-evidence artifact is registered for {strategy!r} in "
                "ops/promotion_gate.CANONICAL_EVIDENCE_REGISTRY. This repo's standard "
                "(see the Miyagi and 60M 3-2-2 precedent) is that a strategy is not "
                "promotable on standalone/detector-level results alone — it must be run "
                "through the real executable path (ReplayEngine -> DecisionEngine -> "
                "RiskEngine -> PaperBroker), the way scripts/orb_breakout_canonical_evidence.py "
                "does for orb_breakout. Build an equivalent canonical-evidence script for "
                "this strategy first, then register it here."
            ),
            "known_strategies": sorted(CANONICAL_EVIDENCE_REGISTRY),
        }

    report: dict[str, Any] = {
        "read_only": True,
        "generated_at": now,
        "strategy": strategy,
        "registry_entry": entry,
    }

    results_path = repo_root / entry["results_json"]
    if not results_path.is_file():
        report["classification"] = "BLOCKED"
        report["why"] = f"Registered results file {entry['results_json']} does not exist."
        return report

    results = _read_json(results_path)
    mtime = datetime.fromtimestamp(results_path.stat().st_mtime, tz=timezone.utc).isoformat()

    report["research_result"] = {
        "source_file": entry["results_json"],
        "source_file_last_modified": mtime,
        "freshness_note": (
            "This is whatever was last written by "
            f"{entry['script']} — a CACHED result, not re-run by this gate. "
            "Per this repo's own proof-baseline convention (no cached/remembered number "
            f"treated as current), re-run `python3 {entry['script']}` against current "
            "journals/corpus before treating this as fresh proof for a promotion decision."
        ),
        "meta": results.get("meta"),
        "classification_from_canonical_script": results.get("classification") or results.get("verdict"),
    }

    report["runtime_parity"] = {
        "strategy_permission_gate_status": _permission_gate_status(repo_root, entry["permission_gate_key"]),
        "inventory_row": _inventory_row(repo_root, entry["inventory_row"]),
    }

    report["paper_forward_evidence"] = {
        "lane_snapshot": _lane_paper_forward_evidence(log_dir, strategy),
        "journal_intent_audit": _strategy_intent_paper_evidence(log_dir, strategy),
    }

    try:
        from ops.live_box_guard import live_box_drift_report
        drift = live_box_drift_report(repo_root=repo_root, log_dir=log_dir)
        overrides = {c["name"]: c for c in drift.get("comparisons", [])}
        report["execution_context"] = {
            "entry_fill_model": overrides.get("entry_fill_model", {}),
            "entry_tolerance_ticks_mnq": overrides.get("entry_tolerance_ticks_mnq", {}),
            "entry_tolerance_ticks_mes": overrides.get("entry_tolerance_ticks_mes", {}),
            "runner_mode": overrides.get("runner_mode", {}),
            "exit_mode": overrides.get("exit_mode", {}),
        }
    except Exception as exc:  # noqa: BLE001
        report["execution_context"] = {"error": f"UNKNOWN ({exc})"}

    blockers: list[str] = []
    permission = report["runtime_parity"]["strategy_permission_gate_status"]
    if permission.startswith("commented_out") or permission.startswith("UNKNOWN"):
        blockers.append(
            f"strategy is NOT currently PAPER_ELIGIBLE per risk_rules.yaml strategy_permission_gate "
            f"(status: {permission!r}) — any paper-forward evidence predates or postdates this "
            "eligibility window and must not be presented as current runtime parity without saying so"
        )
    lane_note = report["paper_forward_evidence"]["lane_snapshot"]
    if not lane_note.get("lanes") and "error" not in lane_note:
        blockers.append(
            "zero active paper-forward lanes found for this strategy — "
            "PAPER FORWARD EVIDENCE is empty, state this explicitly rather than omitting it"
        )

    report["gate_status"] = "BLOCKER_FOUND" if blockers else "NO_MECHANICAL_BLOCKER"
    report["blockers"] = blockers
    report["classification"] = (
        "REQUIRES HUMAN/OPERATOR JUDGMENT — this tool assembles evidence and enforces mechanical "
        "rules (permission-gate status, lane presence) but does not itself declare "
        "VALIDATED / PROMISING BUT UNPROVEN / BROKEN / OVERFIT / UNSAFE / WAIT. "
        "Use research_result + runtime_parity + paper_forward_evidence above to decide, "
        "and apply the promotion rules in the task spec (no rescue/tuning variant in the "
        "same pass, no silent risk-control exemption, zero-fill must be stated plainly)."
    )
    return report
