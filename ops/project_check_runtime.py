"""Read-only runtime/config snapshot helpers shared by ops.project_check.

Composes existing config surfaces — never re-derives trading behavior:
  - config.settings.load_config() / SystemConfig — the single config source
    of truth (risk_rules.yaml + env overrides).
  - docs/strategy-rules/Strategy_Inventory.md — the hand-maintained strategy
    verdict table.
  - EXPECTED_LIVE_BRANCH / EXPECTED_LIVE_COMMIT env pins, the same names
    ops.live_box_guard.live_box_drift_report already treats as the intended
    deployed identity.

Every field is UNKNOWN when the source data isn't available — this module
never infers or guesses a value.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

UNKNOWN = "UNKNOWN"

# (lane_key, config attribute holding its mode string, instrument hint or None)
# Instrument hints are read off the *name*, matching this repo's own naming
# convention (mnq_* / mes_* prefixes) — not a separate config field, because
# none exists. Kept explicit here (not derived from the string) so a rename
# doesn't silently change instrument attribution.
LANE_FIELDS: tuple[tuple[str, str, str | None], ...] = (
    ("mnq_orb_reclaim", "mnq_orb_reclaim_proof_mode", "MNQ"),
    ("mnq_orb_breakout", "mnq_orb_breakout_proof_mode", "MNQ"),
    ("mnq_orb_breakout_inverse", "mnq_orb_breakout_inverse_mode", "MNQ"),
    ("mnq_vwap_hold", "mnq_vwap_hold_proof_mode", "MNQ"),
    ("mnq_strat_22_reversal", "mnq_strat_22_reversal_mode", "MNQ"),
    ("mnq_strat_22_continuation", "mnq_strat_22_continuation_mode", "MNQ"),
    ("mnq_strat_32", "mnq_strat_32_mode", "MNQ"),
    ("mnq_strat_322", "mnq_strat_322_mode", "MNQ"),
    ("mes_trend_consolidation_break", "mes_trend_consolidation_break_mode", "MES"),
    ("entry_refresh", "entry_refresh_mode", None),  # instrument list is dynamic; see entry_refresh_instruments
    ("vwap_hold_early", "vwap_hold_early_mode", "MNQ"),
)

# Modes that mean "not reaching paper/live execution" — anything else counts
# as an active lane for reporting purposes.
INACTIVE_MODES = {"off", "observe_only"}


def _getattr_or_unknown(config: Any, name: str) -> Any:
    if config is None:
        return UNKNOWN
    return getattr(config, name, UNKNOWN)


def active_lanes(config: Any) -> list[dict[str, Any]]:
    """One row per lane whose configured mode is beyond pure observation."""
    if config is None:
        return []
    lanes: list[dict[str, Any]] = []
    for lane_key, attr, instrument_hint in LANE_FIELDS:
        mode = getattr(config, attr, None)
        if mode is None or mode in INACTIVE_MODES:
            continue
        instrument = instrument_hint
        if lane_key == "entry_refresh":
            instruments = tuple(getattr(config, "entry_refresh_instruments", ()) or ())
            instrument = ",".join(instruments) if instruments else UNKNOWN
        tolerance = UNKNOWN
        tolerance_map = getattr(config, "entry_tolerance_ticks_by_root", None) or {}
        if instrument and instrument != UNKNOWN and instrument in tolerance_map:
            tolerance = tolerance_map[instrument]
        contract_cap = UNKNOWN
        per_instrument_caps = getattr(config, "max_contracts_per_instrument", None) or {}
        if instrument and instrument != UNKNOWN and instrument in per_instrument_caps:
            contract_cap = per_instrument_caps[instrument]
        elif getattr(config, "max_contracts_hard_cap", None) is not None:
            contract_cap = getattr(config, "max_contracts_hard_cap")
        lanes.append(
            {
                "lane": lane_key,
                "config_field": attr,
                "execution_mode": mode,
                "instrument": instrument or UNKNOWN,
                "entry_fill_model": _getattr_or_unknown(config, "entry_fill_model"),
                "effective_entry_tolerance_ticks": tolerance,
                "contract_cap": contract_cap,
            }
        )
    return lanes


def strategy_permission_snapshot(config: Any) -> dict[str, Any]:
    if config is None:
        return {"enabled": UNKNOWN, "default_status": UNKNOWN, "strategy_status": {}}
    return {
        "enabled": _getattr_or_unknown(config, "strategy_permission_gate_enabled"),
        "default_status": _getattr_or_unknown(config, "strategy_permission_default_status"),
        "strategy_status": dict(getattr(config, "strategy_status", {}) or {}),
    }


def enabled_concepts_snapshot(config: Any) -> dict[str, Any]:
    if config is None:
        return {
            "enabled_concepts": [],
            "selection_mode": UNKNOWN,
            "disabled_concepts_per_instrument": {},
        }
    return {
        "enabled_concepts": list(getattr(config, "enabled_concepts", []) or []),
        "selection_mode": _getattr_or_unknown(config, "strategy_selection_mode"),
        "disabled_concepts_per_instrument": dict(
            getattr(config, "disabled_concepts_per_instrument", {}) or {}
        ),
    }


def intended_release_identity() -> dict[str, Any]:
    """EXPECTED_LIVE_* pins — the same env names live_box_guard compares against.

    This is what this repo currently uses to record "what SHOULD be deployed";
    there is no other source. UNKNOWN (not None/False) when unset, per the
    UNKNOWN-first rule — an unset pin is not evidence of anything.
    """
    branch = os.getenv("EXPECTED_LIVE_BRANCH", "").strip()
    commit = os.getenv("EXPECTED_LIVE_COMMIT", "").strip()
    return {
        "expected_branch": branch or UNKNOWN,
        "expected_commit": commit or UNKNOWN,
        "source": "EXPECTED_LIVE_BRANCH / EXPECTED_LIVE_COMMIT env pins",
    }


_TABLE_ROW_RE = re.compile(r"^\|(.+)\|\s*$")


def parse_strategy_inventory(path: Path) -> dict[str, Any]:
    """Parse the Master Table of docs/strategy-rules/Strategy_Inventory.md.

    Returns {"available": bool, "rows": [{"strategy": str, "verdict": str}]}.
    Verdict is pulled from the last table column, stripped of markdown bold
    markers. This is a best-effort markdown parse of a hand-maintained doc —
    it does not validate the table shape beyond "looks like a pipe table".
    """
    if not path.exists():
        return {"available": False, "detail": f"not found: {path}", "rows": []}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return {"available": False, "detail": str(exc), "rows": []}

    rows: list[dict[str, str]] = []
    in_master_table = False
    header_seen = False
    for line in text.splitlines():
        if line.strip().startswith("## Master Table"):
            in_master_table = True
            header_seen = False
            continue
        if in_master_table and line.strip().startswith("## "):
            break  # next section
        if not in_master_table:
            continue
        match = _TABLE_ROW_RE.match(line.strip())
        if not match:
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if not header_seen:
            header_seen = True
            continue  # header row
        if cells and set(cells[0]) <= {"-", ":"}:
            continue  # separator row
        if len(cells) < 2:
            continue
        strategy = cells[0]
        verdict_raw = cells[-1]
        verdict = verdict_raw.replace("**", "").strip()
        rows.append({"strategy": strategy, "verdict": verdict})
    return {"available": True, "rows": rows}
