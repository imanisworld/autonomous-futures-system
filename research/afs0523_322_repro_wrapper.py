"""Preregistered wrapper around the frozen 60M 3-2-2 harness.

The frozen module ``scripts/322_trigger_timing_ab_2026_09_18.py`` and its
``check_repro()`` pins stay untouched. Those pins still expect the pre-#1057
1-tick totals (plan net 2532.66, IOC net 1859.40). The corrected Oct 4 ledger
replaced those totals with plan net 2293.64 and IOC net 1622.38, and moved
2025-01-20 to UNRESOLVED / EOD_BAR_MISSING. This wrapper does not call
``check_repro()`` and does not replace it. The reproduction gate of record for
AFS-0523 is a row-by-row comparison against the already committed ledger:

``docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json``

SHA-256 ``0d0d8d72c48b3c9dd1e7065b23a5422176a765ac7b1eb594e2833b72f5d5a9b1``
(commit ``cf2ac0724a266198629a363fde23364ef00ce721``). No second copy of the
34 rows is written.

Detection and accounting are the frozen functions. The only parameters are the
window, the expected-count check, and the data paths. NQ prices, when a later
reviewed run supplies them on the MNQ path, are accounted with the unchanged
MNQ lane ($2/point). This module does not assign a point value.

``RUN_AUTHORIZED`` is false. ``run_declared_slices()`` refuses every call until
Claude reviews the preregistration. A mechanical ``run_window()`` call is not
a study result and must not be committed as evidence.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from research.run_322_expanded_evidence import detect_candidates
from scripts.edge_decomposition_audit import (
    LANES,
    extract_state_machine,
    load_bars,
    run_bracket_stage,
)

REPO = Path(__file__).resolve().parents[1]
HARNESS_PATH = REPO / "scripts" / "322_trigger_timing_ab_2026_09_18.py"
OCT4_LEDGER_REL = (
    "docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json"
)
OCT4_LEDGER_SHA256 = "0d0d8d72c48b3c9dd1e7065b23a5422176a765ac7b1eb594e2833b72f5d5a9b1"
OCT4_LEDGER_COMMIT = "cf2ac0724a266198629a363fde23364ef00ce721"
EOD_DATE = "2025-01-20"
RUN_AUTHORIZED = False
REPRO_START = date(2024, 7, 2)
REPRO_END = date(2026, 6, 26)
REPRO_EXPECTED_COUNT = 34

FROZEN_MODULE_SHA256 = {
    "scripts/322_trigger_timing_ab_2026_09_18.py":
        "0490e8948548eff1f39902e64402e8d00368facda9d61ab132bbbd2e16522320",
    "scripts/edge_decomposition_audit.py":
        "8d2c08ead799fc8700e5706c3fc83e5ff439bd4b0519c1ad00c865b8b745811c",
    "research/run_322_expanded_evidence.py":
        "b0e6307afee6ab12934feec76d543838382fe0f89c444a0b455ffd65a8a4bed0",
    "research/detector_322_first_live.py":
        "7792301db6703a86eefd3e1b221e3784a1802d91522f6b654c44fab47d03f0ca",
    "research/bars_322_polygon_loader.py":
        "0a4db0be7e57525dad1dfa3131eb0ade52dbf9cb8c1913bd2eb3fee632629a10",
    "execution/paper_broker.py":
        "ac32824f951dc4b6db80a11a0223383cc678a0ee9a20de2518bab25b2a3f35ed",
    "execution/day_only_exit.py":
        "f071251f4a2cb5cfd9818d25f743b2b7eb98e84c99dcda50e4274b46eacf8c3e",
}

MODEL_LABELS = (
    ("plan", "original"),
    ("ioc_close", "honest_ioc"),
    ("prearmed_touch", "prearmed"),
)
SLIPPAGES = ("1tick", "2tick", "3tick")
COMPARE_FIELDS = (
    "date",
    "direction",
    "trigger",
    "stop",
    "target",
    "status",
    "exit_reason",
    "fill_price",
    "exit_price",
    "net",
    "result",
)


@dataclass(frozen=True)
class SliceSpec:
    id: str
    start: date
    end: date
    price_source: str
    label: str


SLICES = (
    SliceSpec(
        "mnq-2019-05-to-2024-06",
        date(2019, 5, 1),
        date(2024, 6, 30),
        "MNQ",
        "MNQ",
    ),
    SliceSpec(
        "nq-2010-06-to-2019-05",
        date(2010, 6, 1),
        date(2019, 4, 30),
        "NQ",
        "best available evidence, not fully clean",
    ),
    SliceSpec(
        "combined",
        date(2010, 6, 1),
        date(2024, 6, 30),
        "COMBINED",
        "reported separately; no retuning",
    ),
)

_SPEC = importlib.util.spec_from_file_location("frozen_322_trigger_timing_ab", HARNESS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load frozen harness {HARNESS_PATH}")
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)


def _load_verified_ledger(path: Path | None = None) -> dict:
    ledger_path = path or (REPO / OCT4_LEDGER_REL)
    raw = ledger_path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != OCT4_LEDGER_SHA256:
        raise RuntimeError(
            f"Oct 4 ledger hash {digest} != {OCT4_LEDGER_SHA256}; refusing to compare"
        )
    payload = json.loads(raw)
    if payload.get("trial_id") != "T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01":
        raise RuntimeError("Oct 4 ledger trial_id does not match the reference of record")
    return payload


def _project(row: dict, model_key: str) -> dict:
    model = row["models"][model_key]
    status = model.get("status")
    exit_reason = model.get("exit_reason") if status == "RESOLVED" else model.get("reason")
    return {
        "date": row.get("date"),
        "direction": row.get("direction"),
        "trigger": row.get("trigger"),
        "stop": row.get("stop"),
        "target": row.get("target"),
        "status": status,
        "exit_reason": exit_reason,
        "fill_price": model.get("fill_entry"),
        "exit_price": model.get("exit_price"),
        "net": model.get("net"),
        "result": model.get("result"),
    }


def _equal(expected: object, actual: object) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, str) or isinstance(actual, str):
        return expected == actual
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    return float(expected) == float(actual)


def _index_rows(rows: list[dict], slippage: str) -> dict[str, dict]:
    indexed: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("date"))
        if key in indexed:
            raise RuntimeError(f"duplicate ledger date {key} at {slippage}")
        indexed[key] = row
    return indexed


def compare_replay_to_oct4(replay: dict, ledger_path: Path | None = None) -> dict:
    """Compare one replay ledger to the Oct 4 reference, field by field.

    34 candidates x 3 models (original / honest IOC / pre-armed) x 1/2/3 ticks.
    2025-01-20 must stay UNRESOLVED / EOD_BAR_MISSING, with no exit price, no
    net, and no WIN/LOSS result. Any difference fails the gate.
    """
    reference = _load_verified_ledger(ledger_path)
    actual_ledger = replay.get("ledger") or {}
    mismatches: list[dict] = []
    comparisons = 0
    eod_cells: dict[str, dict[str, dict]] = {}
    eod_scored = False
    reference_dates: set[str] = set()

    for slippage in SLIPPAGES:
        reference_rows = list(reference["ledger"][slippage])
        actual_rows = _index_rows(list(actual_ledger.get(slippage) or []), slippage)
        reference_dates.update(str(row["date"]) for row in reference_rows)
        actual_dates = set(actual_rows)
        for reference_row in reference_rows:
            day = str(reference_row["date"])
            actual_row = actual_rows.get(day)
            for model_key, label in MODEL_LABELS:
                comparisons += 1
                if actual_row is None:
                    mismatches.append(
                        {
                            "date": day,
                            "slippage": slippage,
                            "model": label,
                            "field": "row",
                            "expected": day,
                            "actual": None,
                        }
                    )
                    if day == EOD_DATE:
                        eod_scored = True
                        eod_cells.setdefault(slippage, {})[label] = {
                            "status": None,
                            "exit_reason": None,
                            "exit_price": None,
                            "net": None,
                            "result": None,
                        }
                    continue
                if model_key not in actual_row.get("models", {}):
                    mismatches.append(
                        {
                            "date": day,
                            "slippage": slippage,
                            "model": label,
                            "field": "model",
                            "expected": model_key,
                            "actual": None,
                        }
                    )
                    if day == EOD_DATE:
                        eod_scored = True
                    continue
                expected = _project(reference_row, model_key)
                actual = _project(actual_row, model_key)
                for field in COMPARE_FIELDS:
                    if not _equal(expected[field], actual[field]):
                        mismatches.append(
                            {
                                "date": day,
                                "slippage": slippage,
                                "model": label,
                                "field": field,
                                "expected": expected[field],
                                "actual": actual[field],
                            }
                        )
                if day == EOD_DATE:
                    eod_cells.setdefault(slippage, {})[label] = {
                        "status": actual["status"],
                        "exit_reason": actual["exit_reason"],
                        "exit_price": actual["exit_price"],
                        "net": actual["net"],
                        "result": actual["result"],
                    }
                    unresolved = (
                        actual["status"] == "UNRESOLVED"
                        and actual["exit_reason"] == "EOD_BAR_MISSING"
                        and actual["exit_price"] is None
                        and actual["net"] is None
                        and actual["result"] is None
                    )
                    if not unresolved:
                        eod_scored = True
        for day in sorted(actual_dates - {str(row["date"]) for row in reference_rows}):
            mismatches.append(
                {
                    "date": day,
                    "slippage": slippage,
                    "model": "original",
                    "field": "row",
                    "expected": None,
                    "actual": day,
                }
            )

    mismatches.sort(key=lambda item: (item["date"] or "", item["slippage"], item["model"], item["field"]))
    eod_ok = (not eod_scored) and all(
        cell["status"] == "UNRESOLVED" and cell["exit_reason"] == "EOD_BAR_MISSING"
        for slip_cells in eod_cells.values()
        for cell in slip_cells.values()
    )
    return {
        "ok": not mismatches and eod_ok and len(reference_dates) == REPRO_EXPECTED_COUNT,
        "ledger_sha256": OCT4_LEDGER_SHA256,
        "rows": len(reference_dates),
        "comparisons": comparisons,
        "mismatches": mismatches,
        "eod_bar_missing": {
            "ok": eod_ok,
            "date": EOD_DATE,
            "scored": eod_scored,
            "cells": eod_cells,
        },
    }


def _parity(research_15m: Path, audit_5m: Path, start: date, end: date, expected_count: int | None):
    """Same research-vs-state-machine check as the frozen harness.

    The window, the expected-count check, and the two data paths are the only
    differences. ``check_repro()`` is not involved.
    """
    research = detect_candidates(research_15m, "MNQ", start, end)
    bars = load_bars(audit_5m, "MNQ")
    state = extract_state_machine(LANES["322_mnq"], bars)
    state.sort(key=lambda candidate: candidate.bar_idx)
    if expected_count is not None and (
        len(research) != expected_count or len(state) != expected_count
    ):
        raise RuntimeError(
            "expected-count check failed "
            f"research={len(research)} state={len(state)} expected={expected_count}"
        )
    if len(research) != len(state):
        raise RuntimeError(f"candidate count mismatch research={len(research)} state={len(state)}")
    mismatches = []
    triggers = {}
    for research_row, state_row in zip(research, state):
        research_key = (
            research_row["date"].isoformat(),
            research_row["direction"],
            round(float(research_row["entry_trigger"]), 8),
            round(float(research_row["stop"]), 8),
            round(float(research_row["target"]), 8),
        )
        state_key = (
            state_row.date,
            state_row.direction,
            round(float(state_row.entry), 8),
            round(float(state_row.stop), 8),
            round(float(state_row.target), 8),
        )
        if research_key != state_key:
            mismatches.append({"research": research_key, "state": state_key})
        triggers[state_row.date] = {
            "trigger": float(research_row["entry_trigger"]),
            "gap_open": bool(research_row["gap_open"]),
        }
    if mismatches:
        raise RuntimeError(f"candidate parity failed: {mismatches[:5]}")
    if any(item["gap_open"] for item in triggers.values()):
        raise RuntimeError("accepted baseline should contain zero gap-open candidates")
    return state, triggers, bars


def run_window(
    research_15m: Path,
    audit_5m: Path,
    *,
    start: date,
    end: date,
    expected_count: int | None,
) -> dict:
    """Run the frozen detection and bracket accounting on caller-supplied trees.

    This is mechanical. It is not authorization to score a declared slice.
    """
    candidates, triggers, bars = _parity(research_15m, audit_5m, start, end, expected_count)
    originals = {candidate.date: candidate for candidate in candidates}
    prearmed = harness.make_prearmed(candidates, triggers, bars) if candidates else []
    ledgers: dict[str, list] = {}
    for slip in (1.0, 2.0, 3.0):
        key = f"{int(slip)}tick"
        if not candidates:
            ledgers[key] = []
            continue
        plan_rows = run_bracket_stage(
            LANES["322_mnq"], bars, candidates,
            fill_model="market", slippage_ticks=slip, tolerance_ticks=harness.IOC_TOLERANCE,
        )
        ioc_rows = run_bracket_stage(
            LANES["322_mnq"], bars, candidates,
            fill_model="ioc_limit", slippage_ticks=slip, tolerance_ticks=harness.IOC_TOLERANCE,
        )
        pre_rows = run_bracket_stage(
            LANES["322_mnq"], bars, prearmed,
            fill_model="stop_market", slippage_ticks=slip, tolerance_ticks=harness.IOC_TOLERANCE,
        )
        ledgers[key] = harness.ledger(
            candidates,
            {"plan": plan_rows, "ioc_close": ioc_rows, "prearmed_touch": pre_rows},
            triggers,
            bars,
        )
    return {
        "status": "MECHANICAL_ONLY",
        "candidate_count": len(candidates),
        "accounting_instrument": LANES["322_mnq"].instrument,
        "dollar_basis": "MNQ-equivalent ($2/point)",
        "ledger": ledgers,
    }


def run_declared_slices(*_args: object, **_kwargs: object) -> None:
    """Refuse the declared MNQ / NQ / combined slices.

    Claude's review of the preregistration is required before any new data
    runs through this wrapper. Flipping ``RUN_AUTHORIZED`` in a later commit
    is part of that review; this function still refuses in this registration.
    """
    raise RuntimeError(
        "AFS-0523 preregistration is not authorized to score new data. "
        "Claude's review is required before any new-vendor run."
    )


def assemble_slice_report(mnq_summary: dict, nq_summary: dict) -> dict:
    """Place the two slice summaries next to a combined count. No retune."""
    nq = next(item for item in SLICES if item.price_source == "NQ")
    return {
        "mnq-2019-05-to-2024-06": {
            **mnq_summary,
            "price_source": "MNQ",
            "dollar_basis": "MNQ-equivalent ($2/point)",
        },
        "nq-2010-06-to-2019-05": {
            **nq_summary,
            "price_source": "NQ",
            "label": nq.label,
            "dollar_basis": "MNQ-equivalent ($2/point)",
        },
        "combined": {
            "label": "reported separately; no retuning",
            "retuned": False,
            "parts": ["mnq-2019-05-to-2024-06", "nq-2010-06-to-2019-05"],
            "candidate_count": int(mnq_summary.get("candidate_count") or 0)
            + int(nq_summary.get("candidate_count") or 0),
        },
    }
