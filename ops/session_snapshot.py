"""Read-only runtime/evidence-lane/deployed-state snapshot.

Composes existing read-only tooling -- it does not reimplement any of it:
  - ``config.settings.load_config`` for risk-config posture (paper/live mode,
    contract caps, daily loss limit)
  - ``ops.release_integrity.verify_release`` for the deployed-release check
  - ``ops.evidence_lane_health.build_snapshot`` for per-lane candidate/fill
    activity and lane status
  - ``execution.mnq_strat_evidence`` / ``execution.mes_trend_consolidation_break_evidence``
    for per-lane execution mode
  - ``execution.tradovate_broker._entry_slippage_tolerance_ticks`` for the
    effective per-instrument entry-slippage cap

No network access, no broker calls, no file writes. Values this process's
environment cannot answer are reported as ``"UNKNOWN"`` rather than guessed
-- in particular, the *deployed box's* environment is never the same thing
as this checkout's local process environment, and the two are labeled
separately so they are never mistaken for each other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ops import repo_state
from ops.evidence_lane_health import build_snapshot as build_lane_snapshot
from ops.release_integrity import verify_release


def _safe_call(fn, *args, **kwargs) -> tuple[Any, str | None]:
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:  # defensive: a read-only snapshot must never crash
        return None, f"{type(exc).__name__}: {exc}"


def risk_config_posture(risk_rules_path: str = "risk_rules.yaml") -> dict[str, Any]:
    from config.settings import ConfigError, LiveTradingBlockedError, load_config

    try:
        cfg = load_config(risk_rules_path)
    except LiveTradingBlockedError as exc:
        return {"ok": False, "error": f"LIVE TRADING ENABLED — {exc}", "live_trading_enabled": True}
    except (ConfigError, OSError) as exc:
        return {"ok": False, "error": str(exc), "live_trading_enabled": "UNKNOWN"}
    return {
        "ok": True,
        "live_trading_enabled": cfg.live_trading_enabled,
        "paper_mode": cfg.paper_mode,
        "allowed_instruments": list(cfg.allowed_instruments),
        "max_contracts_per_instrument": dict(cfg.max_contracts_per_instrument),
        "max_contracts_hard_cap": cfg.max_contracts_hard_cap,
        "max_daily_loss": cfg.max_daily_loss,
        "schedule_mode": getattr(cfg, "schedule_mode", "UNKNOWN"),
    }


def entry_tolerance_by_instrument(instruments: tuple[str, ...] = ("MES", "MNQ")) -> dict[str, float]:
    from execution.tradovate_broker import _entry_slippage_tolerance_ticks

    return {inst: _entry_slippage_tolerance_ticks(inst) for inst in instruments}


def entry_execution_mode() -> str:
    from execution.tradovate_broker import _entry_execution_mode

    return _entry_execution_mode()


def deployed_release_state(repo_root: str | Path | None = None) -> dict[str, Any]:
    report = verify_release(repo_root=repo_root)
    if not report["manifest_present"]:
        return {
            "status": "UNKNOWN",
            "reason": "no release_manifest.json in this checkout — deployed-box release "
            "identity cannot be confirmed from a repo checkout alone",
            "local_head_sha": repo_state.head_sha(cwd=repo_root),
        }
    return {
        "status": "OK" if report["ok"] else "MISMATCH",
        "release_commit": report["release_commit"],
        "problems": report["problems"],
        "local_head_sha": repo_state.head_sha(cwd=repo_root),
    }


def active_mnq_lanes() -> dict[str, str]:
    from execution.mnq_strat_evidence import LANES, lane_mode

    return {lane: lane_mode(lane) for lane in LANES}


def active_mes_lane_mode() -> str:
    from execution.mes_trend_consolidation_break_evidence import lane_mode

    return lane_mode()


def build_runtime_snapshot(
    log_dir: str | Path = "logs",
    *,
    repo_root: str | Path | None = None,
    risk_rules_path: str = "risk_rules.yaml",
) -> dict[str, Any]:
    """One read-only runtime/evidence/deployed-state snapshot.

    Every top-level section that could not be safely determined carries its
    own ``"UNKNOWN"``/error marker rather than being silently omitted.
    """
    lane_health, lane_health_error = _safe_call(build_lane_snapshot, log_dir)
    risk_posture, risk_error = _safe_call(risk_config_posture, risk_rules_path)
    tolerance, tolerance_error = _safe_call(entry_tolerance_by_instrument)
    entry_mode, entry_mode_error = _safe_call(entry_execution_mode)
    mnq_lanes, mnq_lanes_error = _safe_call(active_mnq_lanes)
    mes_lane_mode, mes_lane_mode_error = _safe_call(active_mes_lane_mode)
    release, release_error = _safe_call(deployed_release_state, repo_root)

    return {
        "read_only": True,
        "deployed_release": release if release is not None else {"status": "UNKNOWN", "error": release_error},
        "risk_config_posture": risk_posture if risk_posture is not None else {"ok": False, "error": risk_error},
        "evidence_lane_health": lane_health if lane_health is not None else {"error": lane_health_error},
        "active_lane_modes": {
            "mnq": mnq_lanes if mnq_lanes is not None else {"error": mnq_lanes_error},
            "mes_trend_consolidation_break": mes_lane_mode if mes_lane_mode is not None else f"UNKNOWN ({mes_lane_mode_error})",
        },
        "entry_execution_mode": entry_mode if entry_mode is not None else f"UNKNOWN ({entry_mode_error})",
        "entry_tolerance_ticks_by_instrument": tolerance if tolerance is not None else {"error": tolerance_error},
        "caveat": (
            "entry_execution_mode and entry_tolerance_ticks_by_instrument reflect THIS "
            "PROCESS's local environment/code defaults, not a confirmed read of the "
            "deployed box's environment -- do not treat them as the live runtime's "
            "actual posture without separately confirming against the box."
        ),
    }
