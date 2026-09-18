"""Read-only options Backtest -> DEMO qualification gate."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from pathlib import Path
from typing import Any

MIN_CELL_FILLS = 30
MAX_TRADE_RISK = 300.0
PASS_CLASSES = {"PROMISING BUT UNPROVEN", "VALIDATED"}
NON_STRATEGY_PREFIXES = ("tests/", "docs/")


def _bool(sec: dict[str, Any], key: str, blockers: list[str], prefix: str, value: bool = True) -> None:
    if sec.get(key) is not value:
        blockers.append("{}.{} must be explicitly {}".format(prefix, key, str(value).lower()))


def _text(sec: dict[str, Any], key: str, blockers: list[str], prefix: str) -> str:
    value = str(sec.get(key) or "").strip()
    if not value:
        blockers.append("{}.{} is required".format(prefix, key))
    return value


def _number(value: Any) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _sha(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _resolve(root: Path, value: Any) -> Path | None:
    if value is None or not str(value).strip():
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else root / path


def _hash_check(root: Path, sec: dict[str, Any], path_key: str, hash_key: str,
                blockers: list[str], prefix: str) -> dict[str, Any]:
    path = _resolve(root, sec.get(path_key))
    claimed = str(sec.get(hash_key) or "").strip().lower()
    if path is None:
        blockers.append("{}.{} is required".format(prefix, path_key))
    if not claimed:
        blockers.append("{}.{} is required".format(prefix, hash_key))
    actual = _sha(path) if path else None
    if path and actual is None:
        blockers.append("{}.{} could not be hashed".format(prefix, path_key))
    elif claimed and actual and claimed != actual:
        blockers.append("{}.{} does not match current bytes".format(prefix, hash_key))
    return {"path": str(path) if path else None, "claimed_sha256": claimed or None, "actual_sha256": actual}


def _git(root: Path, args: list[str]) -> tuple[str | None, str | None]:
    try:
        p = subprocess.run(["git", *args], cwd=root, text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    except OSError as exc:
        return None, str(exc)
    if p.returncode:
        return None, p.stderr.strip() or "git failed"
    return p.stdout.strip(), None


def _head(root: Path) -> str | None:
    out, err = _git(root, ["rev-parse", "HEAD"])
    return None if err else out


def _scope(root: Path, ev: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    sec = ev.get("change_scope") or {}
    _bool(sec, "strategy_or_parameter_only", blockers, "change_scope")
    for key in ("risk_policy_unchanged", "contract_selection_semantics_unchanged",
                "fill_model_semantics_unchanged", "broker_routing_unchanged",
                "runtime_activation_unchanged"):
        _bool(sec, key, blockers, "change_scope")
    base = _text(sec, "base_sha", blockers, "change_scope")
    allowed = sec.get("allowed_strategy_paths")
    if not isinstance(allowed, list) or not allowed:
        blockers.append("change_scope.allowed_strategy_paths must be a non-empty pre-registered list")
        allowed = []
    allowed = [str(x).strip() for x in allowed if str(x).strip()]
    head = _head(root)
    changed: list[str] = []
    strategy_changed: list[str] = []
    disallowed: list[str] = []
    if not head:
        blockers.append("change_scope current HEAD could not be resolved")
    elif base:
        if base == head:
            blockers.append("change_scope.base_sha must be the pre-change commit")
        out, err = _git(root, ["diff", "--no-ext-diff", "--no-textconv", "--name-only",
                               "{}...{}".format(base, head)])
        if err:
            blockers.append("change_scope diff failed: {}".format(err))
        else:
            changed = [x.strip() for x in (out or "").splitlines() if x.strip()]
            if not changed:
                blockers.append("change_scope base_sha...HEAD diff is empty")
            for path in changed:
                if path in allowed:
                    strategy_changed.append(path)
                elif not path.startswith(NON_STRATEGY_PREFIXES):
                    disallowed.append(path)
            if not strategy_changed:
                blockers.append("change_scope has no changed pre-registered strategy path")
            if disallowed:
                blockers.append("change_scope has disallowed paths: {}".format(", ".join(disallowed)))
    return {**sec, "current_head": head, "changed_files": changed,
            "strategy_files_changed": strategy_changed, "disallowed_changed_files": disallowed}


def _flags(ev: dict[str, Any], blockers: list[str], section: str, keys: tuple[str, ...]) -> dict[str, Any]:
    sec = ev.get(section) or {}
    for key in keys:
        _bool(sec, key, blockers, section)
    return sec


def _data(root: Path, ev: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    sec = _flags(ev, blockers, "data_integrity", (
        "underlying_dataset_frozen", "option_quotes_dataset_frozen",
        "quote_source_identified", "bid_ask_available_at_decision",
        "stale_quotes_fail_closed", "missing_contract_rows_fail_closed"))
    u = _hash_check(root, sec, "underlying_manifest_path", "underlying_manifest_sha256",
                    blockers, "data_integrity")
    q = _hash_check(root, sec, "option_quotes_manifest_path", "option_quotes_manifest_sha256",
                    blockers, "data_integrity")
    return {**sec, "underlying_manifest_check": u, "option_quotes_manifest_check": q}


def _fills(ev: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    sec = _flags(ev, blockers, "fill_realism", (
        "entry_uses_executable_quote", "exit_uses_executable_quote", "spread_included",
        "fees_included", "slippage_included", "no_fill_modeled", "gap_handling_modeled",
        "same_bar_ambiguity_pessimistic", "slippage_stress_pre_registered",
        "slippage_stress_pass"))
    if str(sec.get("entry_fill_basis") or "").upper() not in {"ASK", "ASK_PLUS_SLIPPAGE"}:
        blockers.append("fill_realism.entry_fill_basis must use ASK")
    if str(sec.get("exit_fill_basis") or "").upper() not in {"BID", "BID_MINUS_SLIPPAGE"}:
        blockers.append("fill_realism.exit_fill_basis must use BID")
    age = _number(sec.get("max_quote_age_seconds"))
    if age is None or age <= 0:
        blockers.append("fill_realism.max_quote_age_seconds must be finite and > 0")
    return sec


def _risk(ev: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    sec = _flags(ev, blockers, "risk_policy", (
        "underlying_invalidation_required", "numeric_premium_stop_required",
        "planned_risk_uses_premium_stop", "aggregate_open_risk_enforced",
        "no_averaging_down"))
    trade = _number(sec.get("max_trade_risk_dollars"))
    if trade is None or trade <= 0:
        blockers.append("risk_policy.max_trade_risk_dollars must be finite and > 0")
    elif trade > MAX_TRADE_RISK:
        blockers.append("risk_policy.max_trade_risk_dollars exceeds $300")
    aggregate = _number(sec.get("max_aggregate_open_risk_dollars"))
    if aggregate is None or aggregate <= 0:
        blockers.append("risk_policy.max_aggregate_open_risk_dollars must be explicitly configured")
    return sec


def _validation(ev: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    sec = _flags(ev, blockers, "validation", (
        "untouched_validation_window", "multiple_months_covered",
        "chronological_walk_forward_pass", "sample_requirement_pre_registered",
        "drawdown_limit_pre_registered", "concentration_limit_pre_registered",
        "aggregate_expectancy_after_costs_positive", "aggregate_net_pnl_after_costs_positive"))
    floor = _integer(sec.get("required_resolved_fills_per_cell"))
    if floor is None or floor < MIN_CELL_FILLS:
        blockers.append("validation.required_resolved_fills_per_cell must be >= 30")
    dims = sec.get("required_cell_dimensions")
    if not isinstance(dims, list) or not dims:
        blockers.append("validation.required_cell_dimensions must be non-empty")
    cells = sec.get("cells")
    if not isinstance(cells, list) or not cells:
        blockers.append("validation.cells must contain per-cell evidence")
        cells = []
    required = 0
    for i, cell in enumerate(cells):
        if not isinstance(cell, dict) or cell.get("required") is not True:
            continue
        required += 1
        label = str(cell.get("cell_id") or "index_{}".format(i))
        fills = _integer(cell.get("resolved_fills"))
        need = floor if floor is not None and floor >= MIN_CELL_FILLS else MIN_CELL_FILLS
        if fills is None or fills < need:
            blockers.append("validation cell {} has insufficient resolved fills".format(label))
        exp = _number(cell.get("expectancy_r_after_costs"))
        if exp is None:
            blockers.append("validation cell {} missing finite expectancy".format(label))
        elif exp < 0:
            blockers.append("validation cell {} has negative expectancy after costs".format(label))
        if cell.get("drawdown_within_limit") is not True:
            blockers.append("validation cell {} fails drawdown limit".format(label))
        if cell.get("concentration_pass") is not True:
            blockers.append("validation cell {} fails concentration check".format(label))
        spread = _number(cell.get("average_spread_percent"))
        if spread is None or spread < 0:
            blockers.append("validation cell {} missing valid average spread".format(label))
    if required == 0:
        blockers.append("validation contains no required cells")
    return {**sec, "required_cell_count": required, "minimum_cell_floor": MIN_CELL_FILLS}


def _provenance(root: Path, ev: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    sec = ev.get("provenance") or {}
    claimed = _text(sec, "code_sha", blockers, "provenance")
    _text(sec, "options_policy_path", blockers, "provenance")
    _text(sec, "options_policy_sha256", blockers, "provenance")
    head = _head(root)
    if not head:
        blockers.append("provenance current HEAD could not be resolved")
    elif claimed and claimed != head:
        blockers.append("provenance.code_sha does not match current HEAD")
    policy = _hash_check(root, sec, "options_policy_path", "options_policy_sha256",
                         blockers, "provenance")
    return {**sec, "current_head": head, "policy_check": policy}


def build_options_demo_qualification_report(*, strategy: str, repo_root: str | Path,
                                            evidence_path: str | Path) -> dict[str, Any]:
    root, path = Path(repo_root), Path(evidence_path)
    try:
        ev = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        ev = None
        error = "evidence JSON unavailable: {}".format(exc)
    else:
        error = None
    if not isinstance(ev, dict):
        error = error or "evidence JSON root must be an object"
        return {"ok": False, "gate_pass": False, "demo_evidence_eligible": False,
                "strategy": strategy, "blockers": [error], "verdict": "BLOCKED",
                "paper_demo_activation_authorized": False, "live_trading_authorized": False}

    blockers: list[str] = []
    if str(ev.get("strategy") or "").strip() != strategy:
        blockers.append("evidence.strategy does not match requested strategy")
    classification = str(ev.get("classification") or "").strip().upper()
    if classification not in PASS_CLASSES:
        blockers.append("classification must be PROMISING BUT UNPROVEN or VALIDATED")

    scope = _scope(root, ev, blockers)
    identity = _flags(ev, blockers, "strategy_identity", (
        "underlying_entry_formula_frozen", "underlying_invalidation_formula_frozen",
        "target_formula_frozen", "timeframe_formula_frozen",
        "replay_forward_formula_parity", "causal_data_only"))
    _bool(identity, "lookahead_or_future_leak", blockers, "strategy_identity", False)
    selection = _flags(ev, blockers, "contract_selection", (
        "mechanical_selection", "expiration_rule_frozen", "strike_rule_frozen",
        "dte_rule_frozen", "moneyness_or_delta_rule_frozen", "liquidity_rule_frozen",
        "quote_timestamp_aligned_to_decision", "no_hindsight_contract_choice",
        "same_selector_replay_and_forward"))
    _text(selection, "selection_rule_id", blockers, "contract_selection")
    _text(selection, "selection_rule_sha256", blockers, "contract_selection")
    data = _data(root, ev, blockers)
    fills = _fills(ev, blockers)
    risk = _risk(ev, blockers)
    validation = _validation(ev, blockers)
    parity = _flags(ev, blockers, "golden_parity", (
        "fixture_set_frozen", "underlying_candidate_parity", "contract_selection_parity",
        "risk_decision_parity", "entry_fill_formula_parity", "exit_fill_formula_parity",
        "no_fill_case_covered", "stale_quote_case_covered", "missing_quote_case_covered",
        "wide_spread_case_covered", "premium_stop_case_covered",
        "underlying_invalidation_case_covered", "event_risk_case_covered"))
    provenance = _provenance(root, ev, blockers)

    blockers = list(dict.fromkeys(blockers))
    passed = not blockers
    return {
        "ok": passed,
        "gate_pass": passed,
        "demo_evidence_eligible": passed,
        "strategy": strategy,
        "classification": classification or None,
        "blockers": blockers,
        "change_scope": scope,
        "strategy_identity": identity,
        "contract_selection": selection,
        "data_integrity": data,
        "fill_realism": fills,
        "risk_policy": risk,
        "validation": validation,
        "golden_parity": parity,
        "provenance": provenance,
        "independent_review_required": True,
        "runtime_release_reconciliation_required": True,
        "paper_demo_activation_authorized": False,
        "live_trading_authorized": False,
        "verdict": "DEMO_EVIDENCE_ELIGIBLE" if passed else "BLOCKED",
    }
