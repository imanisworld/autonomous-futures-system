"""Read-only runtime/deployed-state snapshot, reused by session-start and daily.

Reuses ops.live_box_guard.live_box_drift_report for identity/drift (branch,
commit, risk_rules sha256, proof-critical env overrides) instead of
re-deriving it, and adds risk_rules.yaml parsing for the things
live_box_guard does not report: which strategy lanes are actually active per
instrument right now.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from ops.live_box_guard import live_box_drift_report

UNKNOWN = "UNKNOWN"


def _env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return None
    return value.strip()


def _load_risk_rules(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.exists():
        return None, f"{path} not found"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "risk_rules.yaml did not parse to a mapping"
    return data, None


def active_lanes(rules: dict[str, Any] | None) -> dict[str, Any]:
    """Which strategy concepts can actually fire, per allowed instrument, right now.

    A concept is active for an instrument only if it is ALL of:
    - listed in strategy.enabled_concepts
    - not listed in strategy.disabled_concepts_per_instrument[instrument]
    - strategy_permission_gate.strategy_status[concept] == PAPER_ELIGIBLE
      (falling back to strategy_permission_gate.default_status when the
      concept has no explicit entry)
    for an instrument that is itself in instruments.allowed.
    """
    if rules is None:
        return {"available": False, "reason": "risk_rules.yaml not loaded", "lanes": {}}

    instruments = ((rules.get("instruments") or {}).get("allowed")) or []
    strategy_cfg = rules.get("strategy") or {}
    enabled_concepts = set(strategy_cfg.get("enabled_concepts") or [])
    disabled_per_instrument = strategy_cfg.get("disabled_concepts_per_instrument") or {}
    gate = rules.get("strategy_permission_gate") or {}
    gate_enabled = bool(gate.get("enabled"))
    default_status = gate.get("default_status") or UNKNOWN
    strategy_status = gate.get("strategy_status") or {}

    lanes: dict[str, list[dict[str, Any]]] = {}
    for instrument in instruments:
        disabled_here = set(disabled_per_instrument.get(instrument) or [])
        candidates = enabled_concepts - disabled_here
        rows = []
        for concept in sorted(candidates):
            status = strategy_status.get(concept, default_status)
            paper_eligible = (status == "PAPER_ELIGIBLE") if gate_enabled else True
            rows.append(
                {
                    "strategy": concept,
                    "permission_status": status,
                    "paper_eligible": paper_eligible,
                }
            )
        lanes[instrument] = rows

    return {
        "available": True,
        "reason": None,
        "permission_gate_enabled": gate_enabled,
        "lanes": lanes,
        "active_lane_summary": {
            instrument: [r["strategy"] for r in rows if r["paper_eligible"]]
            for instrument, rows in lanes.items()
        },
    }


# config/settings.py's two fill-model code paths use DIFFERENT fallback
# defaults when ENTRY_SLIPPAGE_TOLERANCE_TICKS_<ROOT> is unset: the
# replay/paper ioc_limit path (_entry_tolerance_map) falls back to these
# per-root values, while the live Tradovate broker path
# (_entry_slippage_tolerance_ticks) falls back to 0 (Market entry). They only
# diverge when env is genuinely unset -- report both so that divergence is
# visible instead of assumed equal (this is the specific execution-context
# gap the promotion gate and trade-chain routines exist to keep catching).
_REPLAY_PAPER_TOLERANCE_FALLBACK = {"MES": 16.0, "MNQ": 32.0}
_LIVE_BROKER_TOLERANCE_FALLBACK = 0.0


def _entry_tolerance_ticks() -> dict[str, Any]:
    global_env = _env("ENTRY_SLIPPAGE_TOLERANCE_TICKS")
    result: dict[str, Any] = {"global_env": global_env or UNKNOWN}
    for root, replay_default in _REPLAY_PAPER_TOLERANCE_FALLBACK.items():
        root_env = _env(f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{root}")
        raw = root_env if root_env is not None else global_env
        if raw is not None:
            try:
                value = float(raw)
            except ValueError:
                value = None
            result[root] = {
                "env_value": raw,
                "effective_replay_paper": value,
                "effective_live_broker": value,
                "diverges": False,
            }
        else:
            result[root] = {
                "env_value": None,
                "effective_replay_paper": replay_default,
                "effective_live_broker": _LIVE_BROKER_TOLERANCE_FALLBACK,
                "diverges": True,
                "note": (
                    "env unset: replay/paper path falls back to this root's default "
                    f"({replay_default}), live Tradovate broker path falls back to "
                    f"{_LIVE_BROKER_TOLERANCE_FALLBACK} (Market) -- these differ"
                ),
            }
    return result


def _quantity_caps(rules: dict[str, Any] | None) -> dict[str, Any]:
    hard_cap = _env("MAX_CONTRACTS_HARD_CAP") or UNKNOWN
    per_instrument = UNKNOWN
    if rules is not None:
        position_rules = rules.get("position_rules") or {}
        per_instrument = position_rules.get("max_contracts_per_instrument") or UNKNOWN
    return {"hard_cap_env": hard_cap, "max_contracts_per_instrument_config": per_instrument}


def runtime_snapshot(
    *,
    repo_root: str | Path,
    risk_rules_path: str | Path = "risk_rules.yaml",
    log_dir: str | Path = "logs",
) -> dict[str, Any]:
    root = Path(repo_root)
    drift = live_box_drift_report(repo_root=root, risk_rules_path=risk_rules_path, log_dir=log_dir)

    risk_path = Path(risk_rules_path)
    if not risk_path.is_absolute():
        risk_path = root / risk_path
    rules, rules_error = _load_risk_rules(risk_path)
    lanes = active_lanes(rules)

    # Mirrors config/settings.py's load_config(): env, then risk_rules.yaml
    # fill_model.entry_fill_model, then the code default "market" -- reporting
    # the code default (rather than UNKNOWN) here is not a guess, it is what
    # load_config() actually computes when both are absent.
    entry_fill_model_source = "env"
    entry_fill_model = _env("ENTRY_FILL_MODEL")
    if entry_fill_model is None:
        entry_fill_model_source = "risk_rules.yaml"
        entry_fill_model = (rules or {}).get("fill_model", {}).get("entry_fill_model")
    if entry_fill_model is None:
        entry_fill_model_source = "code_default"
        entry_fill_model = "market"
    execution_mode = _env("TRADOVATE_ENTRY_EXECUTION_MODE") or UNKNOWN

    return {
        "deployed_release_sha": drift.get("commit") or UNKNOWN,
        "deployed_branch": drift.get("branch") or UNKNOWN,
        "risk_rules_sha256": drift.get("risk_rules_sha256") or UNKNOWN,
        "risk_rules_version": (rules or {}).get("version", UNKNOWN),
        "git_dirty": drift.get("git_dirty"),
        "evidence_epoch": None,
        "evidence_epoch_note": (
            "no 'evidence epoch' field exists anywhere in this repo's convention; "
            "risk_rules_sha256 + deployed_release_sha above are the closest identity "
            "anchors and are reported instead of inventing one"
        ),
        "active_instruments": ((rules or {}).get("instruments") or {}).get("allowed", UNKNOWN),
        "active_lanes": lanes,
        "execution_mode": execution_mode,
        "entry_fill_model": entry_fill_model,
        "entry_fill_model_source": entry_fill_model_source,
        "entry_tolerance_ticks": _entry_tolerance_ticks(),
        "quantity_caps": _quantity_caps(rules),
        "runtime_overrides_active": drift.get("active_runtime_overrides") or [],
        "runtime_overrides_unpinned": drift.get("unpinned_runtime_overrides") or [],
        "live_trading_enabled": (rules or {}).get("trading_mode", {}).get("live_trading_enabled", UNKNOWN),
        "paper_mode": (rules or {}).get("trading_mode", {}).get("paper_mode", UNKNOWN),
        "risk_rules_load_error": rules_error,
        "live_box_drift": drift,
    }
