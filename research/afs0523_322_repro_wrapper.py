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

``RUN_AUTHORIZED`` is true. The superseded reproduction trial has no run
path. ``run_reproduction_gate`` belongs to the structural trial and calls
``compare_replay_structural``. It is the only path that scores the
reproduction window, and only on the committed manifest at
``DATA_MANIFEST_PATH``.
``DATA_MANIFEST_SHA256`` is the SHA-256 of that committed file. The gate
refuses a placeholder pin and any other byte mismatch. ``run_window`` scores nothing.
``run_declared_slices()`` scores one admitted slice only when
``SLICE_SCORING_AUTHORIZED`` is true. This commit sets that constant
true and stamps the slice-success evaluator on each scored slice. Raw slice
results plus the registered decisive cell (honest IOC, 3 ticks). The gate
verdict is the COMBINE artifact. A ``claude_review``
string is self-attested; pull-request review is the real gate.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from config.futures_contracts import contract_economics
from config.settings import per_instrument_contract_ceiling
from research.afs0523_322_slice_evaluator import (
    combine_cells,
    evaluate_cell,
    trade_records_from_ledger,
)
from research.databento_322_bar_adapter import data_gap_dates_from_trees
from research.run_322_expanded_evidence import detect_candidates
from scripts.edge_decomposition_audit import (
    COMMISSION_ROUND_TRIP,
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
SUPERSEDED_TRIAL_ID = "T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01"
TRIAL_ID = "T-2026-11-10-prereg-322-afs0523-oos-structural-2026-11-10-01"
RUN_AUTHORIZED = True
# The registered slice-success trial authorizes scoring. The evaluator does not promote.
SLICE_SCORING_AUTHORIZED = True
EVALUATOR_REL = "research/afs0523_322_slice_evaluator.py"
EXPECTED_EVALUATOR_SHA256 = "68a5f01326135fbd7bf0420d50337726580f074b9de11d26f058838affb0e07e"
TRIAL_LEDGER = REPO / "docs/research-trial-ledger.jsonl"
GATE_ARTIFACT = (
    REPO
    / "docs/research-evidence"
    / TRIAL_ID
    / "reproduction-gate.json"
)
SLICE_ARTIFACT_DIR = REPO / "docs/research-evidence" / TRIAL_ID / "slices"
DATA_MANIFEST_REL = (
    "docs/research-evidence/"
    f"{SUPERSEDED_TRIAL_ID}/databento-download-manifest.json"
)
DATA_MANIFEST_PATH = REPO / DATA_MANIFEST_REL
# SHA-256 of the committed download manifest. The gate refuses on any other bytes.
DATA_MANIFEST_SHA256 = "6c9d5ab3c29a7aeade90e95c84e0128795afc1bdd19aa165e18d364760b7f43d"
# Oct 4 corrected rerun profile. Sources: docs/322-corrected-eod-rerun-erratum-2026-10-04.md
# (MAX_CONTRACTS_HARD_CAP=1) and resolve_bracket in the frozen edge-decomposition
# harness. The ceiling is the max of risk_rules.yaml position_rules.max_contracts_per_instrument,
# which parse_max_contracts_hard_cap reads before it accepts the cap.
OCT4_SCORING_PROFILE = {
    "MAX_CONTRACTS_HARD_CAP": "1",
    "risk_rules_contract_ceiling": 6,
    "contracts": 1,
    "starting_balance": 100000.0,
    "pessimistic_both_hit": True,
    "entry_tolerance_ticks": 32.0,
    "slippage_ticks": [1.0, 2.0, 3.0],
    "entry_fill_models": ["market", "ioc_limit", "stop_market"],
    "commission_round_trip": 1.48,
    "tick_size": 0.25,
    "tick_value": 0.50,
}
_FROZEN_LEDGER_FIELDS = (
    "prereg_path",
    "prereg_commit",
    "family_id",
    "family_label",
    "population",
    "variant_set",
    "prior_exposed",
)
REPRO_START = date(2024, 7, 2)
REPRO_END = date(2026, 6, 26)
REPRO_EXPECTED_COUNT = 34
OUTCOME_RECORD_COUNT = 34 * 3 * 3
NQ_RESEARCH_LABEL = "MNQ-equivalent research, not executable historical MNQ"
GAP_OPEN_POLICY = "score_at_10am_open"
SLIPPAGE_SENSITIVITY_NOTE = (
    "1-3 tick slippage sensitivity tests fill-price sensitivity only, "
    "not whether fills would have happened; spread, depth and fill probability "
    "remain separate unquantified uncertainties."
)

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
WINDOW_SLIPPAGE_TICKS = (1.0, 2.0, 3.0)
WINDOW_ENTRY_FILL_MODELS = ("market", "ioc_limit", "stop_market")
OLDER_SLICE_IOC_NOTE = (
    "The reproduction gate is the only check of Databento bar quality. "
    "Older-slice results depend on Databento's bars and closing prints."
)
BAR_EVIDENCE_REL = (
    "docs/research-evidence/"
    "T-2026-11-09-prereg-322-afs0523-oos-reproduction-2026-11-09-01/BAR_EVIDENCE.json"
)
BAR_EVIDENCE_PATH = REPO / BAR_EVIDENCE_REL
BAR_EVIDENCE_SHA256 = "9bbd516d8c7a43b8c42d829723bc6f800b1dabe3f372ed8d20295f839017931d"
MAR06_EVIDENCE_DIR = BAR_EVIDENCE_PATH.parent
MAR06_BAR_EVIDENCE_PATH = MAR06_EVIDENCE_DIR / "MAR06_BAR_EVIDENCE.json"
MAR06_BAR_EVIDENCE_MD_PATH = MAR06_EVIDENCE_DIR / "MAR06_BAR_EVIDENCE.md"
MAR06_BAR_EVIDENCE_SHA256 = "408cebf65e72a4bf43685ad80b406b6bc6b18081c2c07658154c84e0a1ef00a7"
MAR06_BAR_EVIDENCE_MD_SHA256 = "6f970effb72bb573fe5bd5a38d4d45e14ae7e483a64bac204ef0605c7d080c15"
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
        date(2010, 6, 6),
        date(2019, 4, 30),
        "NQ",
        "best available evidence, not fully clean",
    ),
    SliceSpec(
        "combined",
        date(2010, 6, 6),
        date(2024, 6, 30),
        "COMBINED",
        "reported separately; no retuning",
    ),
)
DOWNLOAD_WINDOWS = (("reproduction", REPRO_START, REPRO_END),) + tuple(
    (item.id, item.start, item.end) for item in SLICES if item.id != "combined"
)

_SPEC = importlib.util.spec_from_file_location("frozen_322_trigger_timing_ab", HARNESS_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError(f"cannot load frozen harness {HARNESS_PATH}")
harness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(harness)


def verify_frozen_modules(expected: dict[str, str] | None = None) -> None:
    """Refuse to run when an imported frozen file no longer matches its pin."""
    pins = FROZEN_MODULE_SHA256 if expected is None else expected
    for rel, pinned in pins.items():
        digest = hashlib.sha256((REPO / rel).read_bytes()).hexdigest()
        if digest != pinned:
            raise RuntimeError(f"frozen module hash mismatch {rel}: {digest} != {pinned}")


def assert_5m_window(audit_5m: Path, start: date, end: date) -> None:
    """The 5-minute tree's day files must be exactly the declared window.

    A wider tree is a window error. It is not an expected-count failure and
    it is not ignored.
    """
    folder = audit_5m / "MNQ"
    if not folder.is_dir():
        return
    outside: list[str] = []
    for path in sorted(folder.glob("MNQ_*.jsonl")):
        day = date.fromisoformat(path.name.removeprefix("MNQ_").removesuffix(".jsonl"))
        if day < start or day > end:
            outside.append(day.isoformat())
    if outside:
        raise RuntimeError(
            "5m tree does not match the declared window "
            f"{start.isoformat()}..{end.isoformat()}: {outside[:8]}"
        )


def entry_inside_bracket(direction: str, entry_price: float, stop: float, target: float) -> bool:
    """Strict bracket from ``strategy/strat_322_first_live.py`` lines 193-198.

    SHORT requires ``target < entry_price < stop``. LONG requires
    ``stop < entry_price < target``. Equality at either boundary is outside.
    Line 200 labels a failed check ``INVALID_ENTRY_BRACKET``. Line 191 only
    assigns ``stop``.
    """
    if direction == "SHORT":
        bracket_valid = target < entry_price < stop
    elif direction == "LONG":
        bracket_valid = stop < entry_price < target
    else:
        raise RuntimeError(f"unsupported direction {direction!r}")
    return bracket_valid


def partition_invalid_gap_opens(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Drop every research row outside the strict bracket at lines 193-198.

    SHORT requires ``target < entry_price < stop``. LONG requires
    ``stop < entry_price < target``. Gap-open and non-gap-open rows use that
    same check. The detector keeps the rows this removes, and the state
    machine labels them ``INVALID_ENTRY_BRACKET`` at line 200.
    """
    kept: list[dict] = []
    invalid: list[dict] = []
    for row in rows:
        direction = str(row["direction"])
        entry_price = float(row["entry_price"])
        stop = float(row["stop"])
        target = float(row["target"])
        if entry_inside_bracket(direction, entry_price, stop, target):
            kept.append(row)
            continue
        day = row["date"]
        invalid.append(
            {
                "date": day.isoformat() if hasattr(day, "isoformat") else str(day),
                "direction": direction,
                "entry_price": entry_price,
                "stop": stop,
                "target": target,
                "gap_open": bool(row.get("gap_open")),
                "invalidation": "INVALID_ENTRY_BRACKET",
            }
        )
    return kept, invalid


def invalid_brackets_outside_data_gaps(invalid: list[dict], gap_dates: set[str]) -> list[dict]:
    """DATA_GAP wins when a day would otherwise be listed in both reports."""
    return [row for row in invalid if row["date"] not in gap_dates]


def comparison_entry(research_row: dict) -> float:
    """Entry the parity check uses. Gap-open days use the 10AM open."""
    if research_row["gap_open"]:
        return float(research_row["entry_price"])
    return float(research_row["entry_trigger"])


def summarize_gap_opens(rows: list[dict]) -> dict:
    """Every gap-open candidate is scored. The count is reported beside it."""
    count = sum(1 for row in rows if row.get("gap_open"))
    return {
        "policy": GAP_OPEN_POLICY,
        "gap_open_count": count,
        "scored_count": len(rows),
    }


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


def _load_bar_evidence() -> dict:
    """Read the committed bar evidence and refuse any other bytes."""
    raw = BAR_EVIDENCE_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != BAR_EVIDENCE_SHA256:
        raise RuntimeError(
            "BAR_EVIDENCE.json hash mismatch: "
            f"{digest} != {BAR_EVIDENCE_SHA256}"
        )
    return json.loads(raw)


def _ohl_triple(bar: object) -> tuple[Decimal, Decimal, Decimal] | None:
    if not isinstance(bar, dict):
        return None
    try:
        return (
            Decimal(str(bar["open"])),
            Decimal(str(bar["high"])),
            Decimal(str(bar["low"])),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _close_delta(day: dict) -> Decimal | None:
    oct4 = day.get("oct4_trigger_bar")
    databento = day.get("databento_5m")
    if not isinstance(oct4, dict) or not isinstance(databento, dict):
        return None
    if "close" not in oct4 or "close" not in databento:
        return None
    return Decimal(str(databento["close"])) - Decimal(str(oct4["close"]))


def _decision_bar_records(payload: dict) -> list[dict]:
    records: list[dict] = []
    for key in ("mismatch_days", "other_decision_bar_close_disagreements"):
        rows = payload.get(key) or []
        if isinstance(rows, list):
            records.extend(row for row in rows if isinstance(row, dict))
    return records


def _ohl_disagreement_days() -> set[str]:
    """Decision days whose pinned bars do not share open, high, and low.

    The ``ohl_agree`` flag is not read. A day without both numeric triples
    cannot be shown to agree.
    """
    found: set[str] = set()
    for day in _decision_bar_records(_load_bar_evidence()):
        left = _ohl_triple(day.get("oct4_trigger_bar"))
        right = _ohl_triple(day.get("databento_5m"))
        if left is None or right is None or left != right:
            found.add(str(day["date"]))
    return found


def _read_pinned_bytes(path: Path, expected: str, label: str) -> bytes:
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected:
        raise RuntimeError(f"{label} hash mismatch: {digest} != {expected}")
    return raw


def _load_mar06_evidence() -> dict:
    """Read the Mar 6 evidence and refuse any other bytes."""
    _read_pinned_bytes(
        MAR06_BAR_EVIDENCE_MD_PATH,
        MAR06_BAR_EVIDENCE_MD_SHA256,
        "MAR06_BAR_EVIDENCE.md",
    )
    raw = _read_pinned_bytes(
        MAR06_BAR_EVIDENCE_PATH,
        MAR06_BAR_EVIDENCE_SHA256,
        "MAR06_BAR_EVIDENCE.json",
    )
    return json.loads(raw)


def _decimal_field(bar: dict, field: str) -> Decimal | None:
    if field not in bar:
        return None
    try:
        return Decimal(str(bar[field]))
    except (TypeError, ValueError):
        return None


def _ohlcv(bar: object) -> dict[str, Decimal | int] | None:
    if not isinstance(bar, dict):
        return None
    values: dict[str, Decimal | int] = {}
    for field in ("open", "high", "low", "close"):
        number = _decimal_field(bar, field)
        if number is None:
            return None
        values[field] = number
    if "volume" not in bar:
        return None
    try:
        values["volume"] = int(bar["volume"])
    except (TypeError, ValueError):
        return None
    return values


def _aggregate_minutes(rows: list[dict]) -> dict[str, Decimal | int] | None:
    if len(rows) != 5:
        return None
    ordered = sorted(rows, key=lambda row: str(row.get("ts_utc")))
    parts = [_ohlcv(row) for row in ordered]
    if any(part is None for part in parts):
        return None
    return {
        "open": parts[0]["open"],
        "high": max(part["high"] for part in parts),
        "low": min(part["low"] for part in parts),
        "close": parts[-1]["close"],
        "volume": sum(int(part["volume"]) for part in parts),
    }


def _expected_minute_stamps(start: str) -> list[str] | None:
    try:
        stamp = datetime.fromisoformat(start)
    except ValueError:
        return None
    return [(stamp + timedelta(minutes=offset)).isoformat() for offset in range(5)]


def _classify_mar06(payload: dict) -> str:
    """Recompute the Mar 6 label from the pinned 1-minute rows.

    ``classification`` and the match flags are not read. Five selected-contract
    minutes must rebuild the Databento 5-minute bar exactly. The reference bar
    is a defect only when that rebuild is exact and its open, high, or low
    still differs.
    """
    decision = payload.get("decision_bar")
    window = payload.get("mnqh6_1m_15_00_to_15_04")
    adapter = payload.get("adapter")
    if not isinstance(decision, dict) or not isinstance(window, dict) or not isinstance(adapter, dict):
        return "UNEXPLAINED"
    rows = window.get("rows")
    databento = _ohlcv(decision.get("databento_5m"))
    reference = _ohl_triple(decision.get("oct4"))
    selected = adapter.get("selected_instrument_id")
    stamps = _expected_minute_stamps(str(decision.get("timestamp_utc")))
    if not isinstance(rows, list) or databento is None or reference is None or stamps is None:
        return "UNEXPLAINED"
    if not isinstance(selected, int):
        return "UNEXPLAINED"
    ordered = sorted(
        (row for row in rows if isinstance(row, dict)),
        key=lambda row: str(row.get("ts_utc")),
    )
    if [row.get("ts_utc") for row in ordered] != stamps:
        return "DATABENTO_SIDE"
    if any(row.get("instrument_id") != selected for row in ordered):
        return "DATABENTO_SIDE"
    aggregate = _aggregate_minutes(ordered)
    if aggregate is None or aggregate != databento:
        return "DATABENTO_SIDE"
    rebuilt = (aggregate["open"], aggregate["high"], aggregate["low"])
    if rebuilt == reference:
        return "UNEXPLAINED"
    return "REFERENCE_DEFECT"


def _ohl_resolution() -> dict[str, list[dict[str, str]]]:
    """Classify every open/high/low disagreement. There is no caller override."""
    excluded: list[dict[str, str]] = []
    failed: list[dict[str, str]] = []
    mar06: dict | None = None
    for day in sorted(_ohl_disagreement_days()):
        if day == "2026-03-06":
            if mar06 is None:
                mar06 = _load_mar06_evidence()
            label = _classify_mar06(mar06)
        else:
            label = "UNEXPLAINED"
        row = {"date": day, "classification": label}
        if label == "REFERENCE_DEFECT":
            excluded.append(row)
        else:
            failed.append(row)
    return {"exclude": excluded, "fail": failed}


def _exclusion_report(resolution: dict[str, list[dict[str, str]]]) -> dict:
    dates = [row["date"] for row in resolution["exclude"]]
    return {"count": len(dates), "dates": dates}


def _replay_rows_by_date(actual_ledger: dict) -> dict[str, list[dict]]:
    found: dict[str, list[dict]] = {}
    for slippage in SLIPPAGES:
        for row in actual_ledger.get(slippage) or []:
            if isinstance(row, dict):
                found.setdefault(str(row.get("date")), []).append(row)
    return found


def _as_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


def _scored_bar_mismatch(day: str, actual: str, expected: str) -> dict:
    return {
        "date": day,
        "slippage": "decision_bar",
        "model": "decision_bar",
        "field": "scored_bar",
        "expected": expected,
        "actual": actual,
    }


def _pinned_databento_closes() -> dict[str, Decimal]:
    """Databento closes for allowlisted days, read from the bar objects."""
    payload = _load_bar_evidence()
    closes: dict[str, Decimal] = {}
    for day in payload["mismatch_days"]:
        bar = day.get("databento_5m")
        close = _as_decimal(bar.get("close") if isinstance(bar, dict) else None)
        if close is None:
            continue
        closes[str(day["date"])] = close
    return closes


def _scored_bar_mismatches(
    actual_ledger: dict,
    resolution: dict[str, list[dict[str, str]]],
    allowlist: dict[str, Decimal],
) -> tuple[list[dict], set[str]]:
    """Bind pinned evidence to the decision bar the replay actually scored.

    An exclusion is valid only when that scored bar is the pinned Databento
    bar. A close-delta day is valid only when the scored close is the pinned
    Databento close. Anything else fails.
    """
    rows = _replay_rows_by_date(actual_ledger)
    mismatches: list[dict] = []
    revoked: set[str] = set()
    mar06 = _load_mar06_evidence()
    decision = mar06.get("decision_bar") if isinstance(mar06, dict) else None
    databento = decision.get("databento_5m") if isinstance(decision, dict) else None
    mar06_day = str(decision.get("date")) if isinstance(decision, dict) else ""
    expected_ohlc = {
        field: _as_decimal(databento.get(field))
        for field in ("open", "high", "low", "close")
    } if isinstance(databento, dict) else {}
    for item in resolution["exclude"]:
        day = item["date"]
        day_rows = rows.get(day) or []
        matches = (
            day == mar06_day
            and all(value is not None for value in expected_ohlc.values())
            and bool(day_rows)
            and all(
                _as_decimal(row.get(f"trigger_bar_{field}")) == expected_ohlc[field]
                for row in day_rows
                for field in ("open", "high", "low", "close")
            )
        )
        if matches:
            continue
        revoked.add(day)
        mismatches.append(
            _scored_bar_mismatch(
                day,
                "DATABENTO_SIDE",
                "scored trigger bar equals the pinned Databento bar",
            )
        )
    closes = _pinned_databento_closes()
    for day in sorted(allowlist):
        expected = closes.get(day)
        day_rows = rows.get(day) or []
        if expected is not None and day_rows and all(
            _as_decimal(row.get("trigger_bar_close")) == expected for row in day_rows
        ):
            continue
        mismatches.append(
            _scored_bar_mismatch(
                day,
                "close_mismatch",
                "missing" if expected is None else str(expected),
            )
        )
    return mismatches, revoked


def _ohl_mismatch(day: str, classification: str) -> dict:
    return {
        "date": day,
        "slippage": "decision_bar",
        "model": "decision_bar",
        "field": "open_high_low",
        "expected": "open, high, and low agree, or a pinned reference defect",
        "actual": classification,
    }


def _refuse_ohl_disagreement() -> None:
    failed = _ohl_resolution()["fail"]
    if failed:
        raise RuntimeError(
            "decision bar open, high, or low disagree: "
            + ", ".join(f"{row['date']} {row['classification']}" for row in failed)
        )


def _fail_closed_on_ohl_disagreement(comparison: dict) -> dict:
    """Apply the pinned-evidence rule even when the fill comparison matched."""
    resolution = _ohl_resolution()
    scored_failures = {
        item.get("date")
        for item in list(comparison.get("mismatches") or [])
        if isinstance(item, dict) and item.get("field") == "scored_bar"
    }
    excluded = {row["date"] for row in resolution["exclude"] if row["date"] not in scored_failures}
    mismatches = [
        item
        for item in list(comparison.get("mismatches") or [])
        if not (
            isinstance(item, dict)
            and item.get("field") == "open_high_low"
            and item.get("date") in excluded
        )
    ]
    present = {
        item.get("date")
        for item in mismatches
        if isinstance(item, dict) and item.get("field") == "open_high_low"
    }
    for row in resolution["fail"]:
        if row["date"] not in present:
            mismatches.append(_ohl_mismatch(row["date"], row["classification"]))
    if resolution["fail"] or scored_failures:
        comparison["ok"] = False
    comparison["mismatches"] = mismatches
    comparison["ohl_reference_exclusions"] = {
        "count": len(excluded),
        "dates": sorted(excluded),
    }
    return comparison


def _ioc_close_allowlist() -> dict[str, Decimal]:
    """Days whose honest-IOC fill may move by the recomputed close delta.

    Open, high, and low must agree as numbers. The delta is Databento close
    minus the Oct 4 close. ``databento_minus_oct4`` is not trusted.
    """
    payload = _load_bar_evidence()
    allowed: dict[str, Decimal] = {}
    for day in payload["mismatch_days"]:
        if day.get("has_ioc_fill") is not True:
            continue
        left = _ohl_triple(day.get("oct4_trigger_bar"))
        right = _ohl_triple(day.get("databento_5m"))
        if left is None or right is None or left != right:
            raise RuntimeError(f"bar evidence {day['date']} disagrees on open, high, or low")
        delta = _close_delta(day)
        if delta is None:
            raise RuntimeError(f"bar evidence {day['date']} is missing a close")
        allowed[str(day["date"])] = delta
    return allowed


def _ioc_close_delta_is_exact(
    day: str,
    label: str,
    field: str,
    expected: object,
    actual: object,
    allowlist: dict[str, Decimal],
) -> bool:
    if label != "honest_ioc" or field not in {"fill_price", "net"} or day not in allowlist:
        return False
    if expected is None or actual is None:
        return False
    delta = Decimal(str(actual)) - Decimal(str(expected))
    close_delta = allowlist[day]
    if field == "fill_price":
        return delta == close_delta
    tick, value = contract_economics("MNQ")
    point = Decimal(str(value)) / Decimal(str(tick))
    return delta == close_delta * point


def compare_replay_structural(replay: dict, ledger_path: Path | None = None) -> dict:
    """Post-hoc structural comparison. It does not score and it does not authorize a run."""
    return compare_replay_to_oct4(replay, ledger_path, structural=True)


def compare_replay_to_oct4(
    replay: dict,
    ledger_path: Path | None = None,
    *,
    structural: bool = False,
) -> dict:
    """Compare one replay ledger to the Oct 4 reference, field by field.

    Frozen-file hashes are checked before the comparison. Gap-open handling
    does not change this gate: the Oct 4 population has no gap-open rows.

    34 candidates x 3 models (original / honest IOC / pre-armed) x 1/2/3 ticks.
    2025-01-20 must stay UNRESOLVED / EOD_BAR_MISSING, with no exit price, no
    net, and no WIN/LOSS result. Any difference fails the gate.
    """
    verify_frozen_modules()
    allowlist = _ioc_close_allowlist() if structural else {}
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
                    if _equal(expected[field], actual[field]):
                        continue
                    if structural and _ioc_close_delta_is_exact(
                        day, label, field, expected[field], actual[field], allowlist
                    ):
                        continue
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

    exclusions = None
    if structural:
        resolution = _ohl_resolution()
        scored, revoked = _scored_bar_mismatches(actual_ledger, resolution, allowlist)
        mismatches.extend(scored)
        excluded = {row["date"] for row in resolution["exclude"] if row["date"] not in revoked}
        for row in resolution["fail"]:
            if row["date"] in reference_dates:
                mismatches.append(_ohl_mismatch(row["date"], row["classification"]))
        mismatches = [
            item
            for item in mismatches
            if not (
                item.get("field") == "open_high_low" and item.get("date") in excluded
            )
        ]
        exclusions = {"count": len(excluded), "dates": sorted(excluded)}

    mismatches.sort(key=lambda item: (item["date"] or "", item["slippage"], item["model"], item["field"]))
    eod_ok = (not eod_scored) and all(
        cell["status"] == "UNRESOLVED" and cell["exit_reason"] == "EOD_BAR_MISSING"
        for slip_cells in eod_cells.values()
        for cell in slip_cells.values()
    )
    report = {
        "ok": (
            not mismatches
            and eod_ok
            and len(reference_dates) == REPRO_EXPECTED_COUNT
            and comparisons == OUTCOME_RECORD_COUNT
        ),
        "outcome_records": OUTCOME_RECORD_COUNT,
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
    if exclusions is not None:
        report["ohl_reference_exclusions"] = exclusions
    return report


def _parity(
    research_15m: Path,
    audit_5m: Path,
    start: date,
    end: date,
    expected_count: int | None,
    gap_dates: set[str],
):
    """Same research-vs-state-machine check as the frozen harness.

    The window, the expected-count check, and the two data paths are the only
    differences. ``check_repro()`` is not involved. DATA_GAP days are removed
    before the count check and are not scored. A gap-open candidate inside
    the strict bracket is kept and compared at the 10AM open.
    """
    verify_frozen_modules()
    assert_5m_window(audit_5m, start, end)
    detected = detect_candidates(research_15m, "MNQ", start, end)
    detected, invalid_brackets = partition_invalid_gap_opens(detected)
    invalid_brackets = invalid_brackets_outside_data_gaps(invalid_brackets, gap_dates)
    research = [row for row in detected if row["date"].isoformat() not in gap_dates]
    bars = load_bars(audit_5m, "MNQ")
    state = extract_state_machine(LANES["322_mnq"], bars)
    state = [candidate for candidate in state if candidate.date not in gap_dates]
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
            round(comparison_entry(research_row), 8),
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
            "entry_price": float(research_row["entry_price"]),
            "gap_open": bool(research_row["gap_open"]),
        }
    if mismatches:
        raise RuntimeError(f"candidate parity failed: {mismatches[:5]}")
    return state, triggers, bars, invalid_brackets


def _run_window(
    research_15m: Path,
    audit_5m: Path,
    *,
    start: date,
    end: date,
    expected_count: int | None,
) -> dict:
    """Run the frozen detection and bracket accounting on caller-supplied trees.

    This is mechanical. It is not authorization to score a declared slice.
    The Oct 4 profile is required here so a slice cannot score around the gate.
    """
    _scoring_profile_or_refuse()
    gap_report = data_gap_dates_from_trees(research_15m, audit_5m, start, end)
    slice_window = (start, end) != (REPRO_START, REPRO_END)
    holiday_closed = _holiday_closed_dates(gap_report) if slice_window else []
    gap_dates = _unscored_dates(gap_report, include_holiday_closures=slice_window)
    candidates, triggers, bars, invalid_brackets = _parity(
        research_15m, audit_5m, start, end, expected_count, gap_dates
    )
    prearmed = harness.make_prearmed(candidates, triggers, bars) if candidates else []
    scored_prearmed = []
    for candidate in prearmed:
        info = triggers[candidate.date]
        if info["gap_open"]:
            scored_prearmed.append(dataclasses.replace(candidate, entry=float(info["entry_price"])))
        else:
            scored_prearmed.append(candidate)
    prearmed = scored_prearmed
    gap_summary = summarize_gap_opens(
        [{"gap_open": info["gap_open"]} for info in triggers.values()]
    )
    ledgers: dict[str, list] = {}
    for slip in WINDOW_SLIPPAGE_TICKS:
        key = f"{int(slip)}tick"
        if not candidates:
            ledgers[key] = []
            continue
        plan_rows = run_bracket_stage(
            LANES["322_mnq"], bars, candidates,
            fill_model=WINDOW_ENTRY_FILL_MODELS[0],
            slippage_ticks=slip,
            tolerance_ticks=harness.IOC_TOLERANCE,
        )
        ioc_rows = run_bracket_stage(
            LANES["322_mnq"], bars, candidates,
            fill_model=WINDOW_ENTRY_FILL_MODELS[1],
            slippage_ticks=slip,
            tolerance_ticks=harness.IOC_TOLERANCE,
        )
        pre_rows = run_bracket_stage(
            LANES["322_mnq"], bars, prearmed,
            fill_model=WINDOW_ENTRY_FILL_MODELS[2],
            slippage_ticks=slip,
            tolerance_ticks=harness.IOC_TOLERANCE,
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
        "gap_open_policy": GAP_OPEN_POLICY,
        "gap_open_count": gap_summary["gap_open_count"],
        "invalid_entry_bracket_count": len(invalid_brackets),
        "invalid_entry_bracket_gap_open_count": sum(
            1 for row in invalid_brackets if row.get("gap_open")
        ),
        "invalid_entry_bracket_non_gap_open_count": sum(
            1 for row in invalid_brackets if not row.get("gap_open")
        ),
        "invalid_entry_brackets": invalid_brackets,
        "data_gaps": gap_report["data_gaps"],
        "data_gap_count": gap_report["data_gap_count"],
        "early_close_sessions": gap_report.get("early_close_sessions", []),
        "holiday_closed_unscored": holiday_closed,
        "holiday_closed_unscored_count": len(holiday_closed),
    }


def _tree_sha256(root: Path) -> str:
    folder = root / "MNQ"
    digest = hashlib.sha256()
    if folder.is_dir():
        for path in sorted(folder.glob("MNQ_*.jsonl")):
            digest.update(path.name.encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _day_file_dates(root: Path) -> list[date]:
    folder = root / "MNQ"
    if not folder.is_dir():
        return []
    found = []
    for path in sorted(folder.glob("MNQ_*.jsonl")):
        found.append(date.fromisoformat(path.name.removeprefix("MNQ_").removesuffix(".jsonl")))
    return found


def _refuse_outside_reproduction_window(root: Path, label: str) -> None:
    outside = [
        day.isoformat()
        for day in _day_file_dates(root)
        if day < REPRO_START or day > REPRO_END
    ]
    if outside:
        raise RuntimeError(
            f"{label} day file outside the reproduction window "
            f"{REPRO_START.isoformat()}..{REPRO_END.isoformat()}: {outside[:8]}"
        )


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _load_pinned_manifest() -> tuple[dict, str]:
    """Read the download record from its fixed path and check the pinned hash.

    Callers cannot supply this record. While the pin is a placeholder, or the
    file bytes are not that pin, the gate refuses.
    """
    if DATA_MANIFEST_SHA256 == "PLACEHOLDER" or not _is_sha256(DATA_MANIFEST_SHA256):
        raise RuntimeError(
            "DATA_MANIFEST_SHA256 is a placeholder; the reproduction gate refuses "
            "until a reviewed commit pins the committed Databento manifest"
        )
    if not DATA_MANIFEST_PATH.is_file():
        raise RuntimeError(f"pinned Databento manifest is missing: {DATA_MANIFEST_REL}")
    raw = DATA_MANIFEST_PATH.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DATA_MANIFEST_SHA256:
        raise RuntimeError("pinned Databento manifest hash mismatch")
    manifest = json.loads(raw)
    if not isinstance(manifest, dict):
        raise RuntimeError("pinned Databento manifest is not an object")
    validate_download_manifest(manifest)
    return manifest, digest


def _is_sha40(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 40:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def validate_download_manifest(manifest: dict) -> None:
    """Require every pulled file and a tree hash for every declared window.

    The reproduction window and the two real slices share this one record.
    ``combined`` is a report of those two slices and is not a download window.
    Slice trees are not required to hash to the reproduction trees.
    """
    if not isinstance(manifest, dict):
        raise RuntimeError("download manifest schema: not an object")
    pulled = manifest.get("pulled_files")
    if not isinstance(pulled, list) or not pulled:
        raise RuntimeError("download manifest schema: pulled_files")
    for item in pulled:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not item["path"]:
            raise RuntimeError("download manifest schema: pulled file path")
        if not _is_sha256(item.get("sha256")):
            raise RuntimeError("download manifest schema: pulled file sha256")
    windows = manifest.get("windows")
    if not isinstance(windows, list):
        raise RuntimeError("download manifest schema: windows")
    found: dict[str, dict] = {}
    for item in windows:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or item["id"] in found:
            raise RuntimeError("download manifest schema: window id")
        found[item["id"]] = item
    if "combined" in found:
        raise RuntimeError("download manifest schema: combined is report-only and is not a download window")
    expected = {window_id for window_id, _start, _end in DOWNLOAD_WINDOWS}
    if set(found) != expected:
        raise RuntimeError("download manifest schema: windows must be the reproduction window and the two real slices")
    for window_id, start, end in DOWNLOAD_WINDOWS:
        item = found[window_id]
        if item.get("start") != start.isoformat() or item.get("end") != end.isoformat():
            raise RuntimeError(f"download manifest schema: {window_id} bounds")
        if not _is_sha256(item.get("research_15m_sha256")) or not _is_sha256(item.get("audit_5m_sha256")):
            raise RuntimeError(f"download manifest schema: {window_id} tree hash")


def _window_entry(manifest: dict, window_id: str) -> dict:
    for item in manifest["windows"]:
        if item.get("id") == window_id:
            return item
    raise RuntimeError(f"download manifest schema: missing {window_id}")


def _resolved_data_root(data_root: Path) -> Path:
    """Require a real directory. Callers cannot omit it or inherit the working directory."""
    if not isinstance(data_root, Path):
        data_root = Path(data_root)
    if not data_root.exists() or not data_root.is_dir():
        raise RuntimeError(f"data_root is missing: {data_root}")
    return data_root.resolve()


def _resolve_under_root(data_root: Path, raw: str | Path) -> Path:
    """Resolve one manifest or tree path under data_root and nowhere else."""
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"path escapes data_root: {raw}")
    candidate = data_root / path
    if not candidate.exists():
        raise RuntimeError(f"path is missing under data_root: {raw}")
    resolved = candidate.resolve()
    if not resolved.is_relative_to(data_root):
        raise RuntimeError(f"path escapes data_root: {raw}")
    return resolved


def _verify_download_record(
    data_root: Path, research_15m: Path, audit_5m: Path, manifest: dict
) -> tuple[Path, Path]:
    pulled = manifest.get("pulled_files")
    if not isinstance(pulled, list) or not pulled:
        raise RuntimeError("download record is missing pulled-file checksums")
    for item in pulled:
        path = _resolve_under_root(data_root, item["path"])
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError(f"checksum mismatch for {path}")
    research = _resolve_under_root(data_root, research_15m)
    audit = _resolve_under_root(data_root, audit_5m)
    _refuse_outside_reproduction_window(research, "15m")
    _refuse_outside_reproduction_window(audit, "5m")
    entry = _window_entry(manifest, "reproduction")
    research_hash = _tree_sha256(research)
    audit_hash = _tree_sha256(audit)
    if research_hash != entry.get("research_15m_sha256"):
        raise RuntimeError("research 15m tree hash mismatch")
    if audit_hash != entry.get("audit_5m_sha256"):
        raise RuntimeError("audit 5m tree hash mismatch")
    return research, audit


def _data_identity(
    manifest: dict,
    manifest_sha256: str,
    research_15m: Path,
    audit_5m: Path,
    data_root: Path,
    scoring_profile: dict,
) -> dict:
    return {
        "pulled_files": manifest.get("pulled_files"),
        "research_15m_sha256": _tree_sha256(research_15m),
        "audit_5m_sha256": _tree_sha256(audit_5m),
        "manifest_sha256": manifest_sha256,
        "data_root": str(data_root),
        "scoring_profile": scoring_profile,
    }


def _gate_payload(
    scored: dict | None,
    comparison: dict | None,
    manifest: dict,
    error: str | None,
    identity: dict,
    prior_attempts: list[dict],
) -> dict:
    passed = comparison is not None and comparison.get("ok") is True and error is None
    payload = {
        "pass": passed,
        "comparisons": None if comparison is None else comparison.get("comparisons"),
        "outcome_records": OUTCOME_RECORD_COUNT,
        "mismatches": [] if comparison is None else comparison.get("mismatches"),
        "data_gaps": [] if scored is None else scored.get("data_gaps"),
        "data_gap_count": 0 if scored is None else scored.get("data_gap_count"),
        "invalid_entry_brackets": [] if scored is None else scored.get("invalid_entry_brackets"),
        "invalid_entry_bracket_count": 0 if scored is None else scored.get("invalid_entry_bracket_count"),
        "gap_open_count": 0 if scored is None else scored.get("gap_open_count"),
        "adapter_report": manifest.get("adapter_report"),
        "error": error,
        "claude_review": None,
        "prior_attempts": prior_attempts,
        "ohl_reference_exclusions": (
            {"count": 0, "dates": []}
            if comparison is None
            else comparison.get("ohl_reference_exclusions") or {"count": 0, "dates": []}
        ),
    }
    payload.update(identity)
    return payload


def _attempt_artifact_path(attempt_number: int) -> Path:
    if attempt_number < 1:
        raise RuntimeError(f"gate attempt {attempt_number} is not a positive attempt number")
    if attempt_number == 1:
        return GATE_ARTIFACT
    return GATE_ARTIFACT.with_name(f"reproduction-gate.attempt-{attempt_number:02d}.json")


def _replay_ledger_path(gate_path: Path) -> Path:
    """Sibling of a gate artifact. The name does not match the gate-file glob."""
    name = gate_path.name
    if name == "reproduction-gate.json":
        return gate_path.with_name("reproduction-replay-ledger.json")
    prefix = "reproduction-gate."
    if name.startswith(prefix) and name.endswith(".json"):
        return gate_path.with_name("reproduction-replay-ledger." + name[len(prefix):])
    raise RuntimeError(f"no replay-ledger name for gate artifact {gate_path}")


def _repo_relative(path: Path) -> str:
    if path.is_relative_to(REPO):
        return str(path.relative_to(REPO))
    return str(path)


def _attach_replay_ledger(payload: dict, scored: dict | None) -> None:
    """Save the scored replay beside this attempt's gate file and pin its hash.

    An attempt that fails before a replay exists records a null path and hash.
    """
    ledger = scored.get("ledger") if isinstance(scored, dict) else None
    gate_path = _attempt_artifact_path(len(_gate_lines()) + 1)
    if not isinstance(ledger, dict):
        payload["replay_ledger"] = None
        payload["replay_ledger_sha256"] = None
        return
    path = _replay_ledger_path(gate_path)
    if path.exists():
        raise RuntimeError(f"refusing to overwrite replay ledger {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"ledger": ledger}, indent=2, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    payload["replay_ledger"] = _repo_relative(path)
    payload["replay_ledger_sha256"] = hashlib.sha256(text.encode()).hexdigest()


def _write_gate_artifact(payload: dict) -> tuple[str, str]:
    path = _attempt_artifact_path(len(_gate_lines()) + 1)
    if path.exists():
        raise RuntimeError(f"refusing to overwrite gate artifact {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, default=str) + "\n"
    path.write_text(text, encoding="utf-8")
    if path.is_relative_to(REPO):
        rel = str(path.relative_to(REPO))
    else:
        rel = str(path)
    return hashlib.sha256(text.encode()).hexdigest(), rel


def _append_gate_event(payload: dict, artifact_sha256: str, artifact_rel: str) -> None:
    lines = TRIAL_LEDGER.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    first = next(row for row in rows if row.get("trial_id") == TRIAL_ID)
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    for row in rows:
        if row.get("trial_id") != TRIAL_ID:
            continue
        previous = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
        if stamp <= previous:
            stamp = previous + timedelta(seconds=1)
    event = {key: first.get(key) for key in _FROZEN_LEDGER_FIELDS}
    event.update(
        {
            "trial_id": TRIAL_ID,
            "event": "GATE",
            "recorded_at": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recorded_by": "reproduction-gate",
            "gate_pass": payload["pass"],
            "gate_artifact": artifact_rel,
            "gate_artifact_sha256": artifact_sha256,
            "replay_ledger": payload.get("replay_ledger"),
            "replay_ledger_sha256": payload.get("replay_ledger_sha256"),
            "pulled_files": payload.get("pulled_files"),
            "research_15m_sha256": payload.get("research_15m_sha256"),
            "audit_5m_sha256": payload.get("audit_5m_sha256"),
            "manifest_sha256": payload.get("manifest_sha256"),
            "data_root": payload.get("data_root"),
            "scoring_profile": payload.get("scoring_profile"),
            "prior_attempts": payload.get("prior_attempts"),
            "notes": "Reproduction gate only. Older slices stay blocked until Claude reviews this result on the pull request.",
        }
    )
    with TRIAL_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def _trial_rows() -> list[dict]:
    if not TRIAL_LEDGER.is_file():
        return []
    found = []
    for line in TRIAL_LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("trial_id") == TRIAL_ID:
            found.append(row)
    return found


def _gate_lines() -> list[dict]:
    return [row for row in _trial_rows() if row.get("event") == "GATE"]


def _review_lines() -> list[dict]:
    return [row for row in _trial_rows() if row.get("event") == "GATE_REVIEW"]


def _prior_attempts() -> list[dict]:
    attempts = []
    for row in _gate_lines():
        attempts.append(
            {
                "recorded_at": row.get("recorded_at"),
                "gate_pass": row.get("gate_pass"),
                "pulled_files": row.get("pulled_files"),
                "research_15m_sha256": row.get("research_15m_sha256"),
                "audit_5m_sha256": row.get("audit_5m_sha256"),
                "manifest_sha256": row.get("manifest_sha256"),
                "gate_artifact_sha256": row.get("gate_artifact_sha256"),
                "replay_ledger": row.get("replay_ledger"),
                "replay_ledger_sha256": row.get("replay_ledger_sha256"),
            }
        )
    return attempts


def _record_scoring_failure(
    research_15m: Path,
    audit_5m: Path,
    manifest: dict,
    identity: dict,
    error: str,
    scored: dict | None = None,
) -> dict:
    gaps: list[dict] = []
    gap_count = 0
    try:
        gap_report = data_gap_dates_from_trees(research_15m, audit_5m, REPRO_START, REPRO_END)
        gaps = gap_report["data_gaps"]
        gap_count = gap_report["data_gap_count"]
    except Exception:
        gaps = []
        gap_count = 0
    payload = _gate_payload(None, None, manifest, error, identity, _prior_attempts())
    payload["data_gaps"] = gaps
    payload["data_gap_count"] = gap_count
    _attach_replay_ledger(payload, scored)
    digest, artifact_rel = _write_gate_artifact(payload)
    _append_gate_event(payload, digest, artifact_rel)
    return payload


def _frozen_bracket_literals() -> dict[str, object]:
    """Read the Oct 4 broker literals from frozen resolve_bracket. Do not edit that file."""
    text = (REPO / "scripts/edge_decomposition_audit.py").read_text(encoding="utf-8")
    start = text.index("def resolve_bracket")
    body = text[start:text.index("\ndef ", start + 1)]
    if (
        "starting_balance=100_000.0" not in body
        or "pessimistic_both_hit=True" not in body
        or "contracts=1" not in body
    ):
        raise RuntimeError(
            "bad config: frozen resolve_bracket no longer states the Oct 4 "
            "contracts, starting balance, and pessimistic same-bar handling"
        )
    return {
        "contracts": 1,
        "starting_balance": 100000.0,
        "pessimistic_both_hit": True,
    }


def _effective_scoring_profile() -> dict:
    """Read the settings the frozen bracket path consults. Do not assign them."""
    tick, tick_value = contract_economics("MNQ")
    profile = {
        "MAX_CONTRACTS_HARD_CAP": os.environ.get("MAX_CONTRACTS_HARD_CAP"),
        "risk_rules_contract_ceiling": per_instrument_contract_ceiling(),
        "entry_tolerance_ticks": float(harness.IOC_TOLERANCE),
        "slippage_ticks": [float(item) for item in WINDOW_SLIPPAGE_TICKS],
        "entry_fill_models": list(WINDOW_ENTRY_FILL_MODELS),
        "commission_round_trip": float(COMMISSION_ROUND_TRIP),
        "tick_size": tick,
        "tick_value": tick_value,
    }
    profile.update(_frozen_bracket_literals())
    return profile


def _scoring_profile_or_refuse() -> dict:
    """Refuse a profile that is not the Oct 4 run. This is not a scoring attempt."""
    profile = _effective_scoring_profile()
    for key, expected in OCT4_SCORING_PROFILE.items():
        if profile[key] != expected:
            raise RuntimeError(
                f"bad config: scoring profile {key} is {profile[key]!r}, "
                f"Oct 4 run used {expected!r}; refusing before scoring"
            )
    return profile


def run_reproduction_gate(data_root: Path, research_15m: Path, audit_5m: Path) -> dict:
    """Score only the reproduction window, and only on the pinned download.

    ``data_root`` is required. Pulled files and both trees are resolved under
    it. The working directory is not used. The Oct 4 scoring profile, including
    ``MAX_CONTRACTS_HARD_CAP`` exactly equal to ``1``, is required before any
    scoring and is not assigned by this function. The manifest path and its SHA-256
    are module constants. The window and the expected count are fixed. Tree
    hashes are checked against the reproduction window entry. A DATA_GAP that
    removes a candidate fails this gate through the 34-count check. Any
    scoring exception is stored as a FAIL artifact and a GATE line, then
    returned as that FAIL.
    """
    _refuse_if_superseded()
    if not RUN_AUTHORIZED:
        raise RuntimeError("RUN_AUTHORIZED is false; reproduction gate refused")
    scoring_profile = _scoring_profile_or_refuse()
    root = _resolved_data_root(data_root)
    verify_frozen_modules()
    manifest, manifest_sha256 = _load_pinned_manifest()
    research, audit = _verify_download_record(root, research_15m, audit_5m, manifest)
    identity = _data_identity(manifest, manifest_sha256, research, audit, root, scoring_profile)
    scored = None
    try:
        scored = _run_window(
            research,
            audit,
            start=REPRO_START,
            end=REPRO_END,
            expected_count=REPRO_EXPECTED_COUNT,
        )
        comparison = _fail_closed_on_ohl_disagreement(compare_replay_structural(scored))
    except Exception as exc:
        return _record_scoring_failure(research, audit, manifest, identity, str(exc), scored)
    payload = _gate_payload(scored, comparison, manifest, None, identity, _prior_attempts())
    _attach_replay_ledger(payload, scored)
    digest, artifact_rel = _write_gate_artifact(payload)
    _append_gate_event(payload, digest, artifact_rel)
    return payload


def run_window(
    research_15m: Path,
    audit_5m: Path,
    *,
    start: date,
    end: date,
    expected_count: int | None,
) -> dict:
    """Refuse every window. Scoring goes through ``run_reproduction_gate``.

    A window other than the reproduction window is refused here. A
    hash-verified PASS artifact does not open it: the older slices stay
    on ``run_declared_slices`` until Claude reviews that result.
    """
    if (start, end) != (REPRO_START, REPRO_END) or expected_count != REPRO_EXPECTED_COUNT:
        raise RuntimeError(
            "run_window refuses a window other than the reproduction window"
        )
    raise RuntimeError("score the reproduction window through run_reproduction_gate")


def _matching_attempt_file(digest: object) -> Path | None:
    if not isinstance(digest, str):
        return None
    candidates = [GATE_ARTIFACT]
    folder = GATE_ARTIFACT.parent
    if folder.is_dir():
        candidates.extend(sorted(folder.glob("reproduction-gate*.json")))
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        if hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
    return None


def _verified_gate_artifact() -> dict:
    lines = _gate_lines()
    if not lines:
        raise RuntimeError(
            "no committed reproduction-gate artifact; Claude's review of a PASS "
            "gate is required before the older slices run"
        )
    path = _matching_attempt_file(lines[-1].get("gate_artifact_sha256"))
    if path is None:
        raise RuntimeError("reproduction-gate artifact hash mismatch")
    return json.loads(path.read_text(encoding="utf-8"))


def _same_data(left: dict, right: dict) -> bool:
    return (
        left.get("manifest_sha256") == right.get("manifest_sha256")
        and left.get("research_15m_sha256") == right.get("research_15m_sha256")
        and left.get("audit_5m_sha256") == right.get("audit_5m_sha256")
    )


def _refuse_changed_gate_data(artifact: dict) -> None:
    """Surface every earlier GATE attempt and refuse a data change."""
    lines = _gate_lines()
    disclosed = artifact.get("prior_attempts")
    if not isinstance(disclosed, list) or len(disclosed) != len(lines) - 1:
        raise RuntimeError("PASS artifact does not report every prior GATE attempt")
    disclosed_keys = {
        (item.get("manifest_sha256"), item.get("research_15m_sha256"), item.get("audit_5m_sha256"))
        for item in disclosed
        if isinstance(item, dict)
    }
    for earlier in lines[:-1]:
        key = (
            earlier.get("manifest_sha256"),
            earlier.get("research_15m_sha256"),
            earlier.get("audit_5m_sha256"),
        )
        if key not in disclosed_keys:
            raise RuntimeError(
                "earlier GATE attempt is not disclosed in the PASS artifact: "
                f"manifest={key[0]} research_15m={key[1]} audit_5m={key[2]}"
            )
        if not _same_data(earlier, artifact):
            raise RuntimeError(
                "earlier GATE attempt used different data from the PASS: "
                f"manifest={key[0]} research_15m={key[1]} audit_5m={key[2]}"
            )
    if not _same_data(lines[-1], artifact):
        raise RuntimeError("latest GATE line data hashes do not match the PASS artifact")


def _refuse_if_superseded() -> None:
    if TRIAL_ID == SUPERSEDED_TRIAL_ID:
        raise RuntimeError("superseded trial has no run path")
    if any(row.get("event") == "SUPERSEDED" for row in _trial_rows()):
        raise RuntimeError("trial is SUPERSEDED; preregister a new trial id")


def _require_gate_review(artifact_sha256: str) -> None:
    reviews = _review_lines()
    if not reviews:
        raise RuntimeError(
            "Claude's review of the gate result is required before the older slices run"
        )
    latest = reviews[-1]
    if latest.get("gate_artifact_sha256") != artifact_sha256:
        raise RuntimeError("GATE_REVIEW points at a different artifact hash")
    if not _is_sha40(latest.get("pr_head_sha")):
        raise RuntimeError("GATE_REVIEW is missing the reviewed pull request head SHA")


def _refuse_slice_data_mismatch(
    artifact: dict,
    research_15m: Path,
    audit_5m: Path,
    slice_id: str,
) -> None:
    """Bind the slice to its own window in the same pinned download record."""
    manifest, manifest_sha256 = _load_pinned_manifest()
    if manifest_sha256 != artifact.get("manifest_sha256"):
        raise RuntimeError("slice manifest hash does not match the PASS gate")
    if slice_id == "combined":
        raise RuntimeError("combined is report-only and is not admitted")
    allowed = {window_id for window_id, _start, _end in DOWNLOAD_WINDOWS if window_id != "reproduction"}
    if slice_id not in allowed:
        raise RuntimeError(f"unknown slice {slice_id}")
    entry = _window_entry(manifest, slice_id)
    start = date.fromisoformat(entry["start"])
    end = date.fromisoformat(entry["end"])
    for label, root in (("15m", research_15m), ("5m", audit_5m)):
        outside = [
            day.isoformat()
            for day in _day_file_dates(root)
            if day < start or day > end
        ]
        if outside:
            raise RuntimeError(
                f"slice {label} day file outside {slice_id} "
                f"{start.isoformat()}..{end.isoformat()}: {outside[:8]}"
            )
    if _tree_sha256(research_15m) != entry.get("research_15m_sha256"):
        raise RuntimeError(
            f"slice research 15m tree hash does not match the PASS gate manifest for {slice_id}"
        )
    if _tree_sha256(audit_5m) != entry.get("audit_5m_sha256"):
        raise RuntimeError(
            f"slice audit 5m tree hash does not match the PASS gate manifest for {slice_id}"
        )


def _next_stamp(rows: list[dict]) -> datetime:
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    for row in rows:
        if row.get("trial_id") != TRIAL_ID:
            continue
        previous = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
        if stamp <= previous:
            stamp = previous + timedelta(seconds=1)
    return stamp


def record_gate_review(pr_head_sha: str) -> dict:
    """Append a GATE_REVIEW for the current PASS artifact. This does not score.

    The event references that artifact's SHA-256 and the reviewed pull-request
    head. It is not a GATE attempt, so the prior-attempt count ignores it.
    """
    _refuse_if_superseded()
    if not _is_sha40(pr_head_sha):
        raise RuntimeError("GATE_REVIEW requires the reviewed pull request head SHA")
    lines = _gate_lines()
    if not lines or lines[-1].get("gate_pass") is not True:
        raise RuntimeError("GATE_REVIEW requires a PASS gate line")
    path = _matching_attempt_file(lines[-1].get("gate_artifact_sha256"))
    if path is None:
        raise RuntimeError("GATE_REVIEW artifact hash mismatch")
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode()).hexdigest()
    raw_lines = TRIAL_LEDGER.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in raw_lines if line.strip()]
    first = next(row for row in rows if row.get("trial_id") == TRIAL_ID)
    event = {key: first.get(key) for key in _FROZEN_LEDGER_FIELDS}
    event.update(
        {
            "trial_id": TRIAL_ID,
            "event": "GATE_REVIEW",
            "recorded_at": _next_stamp(rows).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recorded_by": "gate-review",
            "gate_artifact_sha256": digest,
            "pr_head_sha": pr_head_sha,
            "notes": "Review of the PASS artifact. This event is not a GATE attempt and it does not score.",
        }
    )
    with TRIAL_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")
    return event


def _git_head_and_clean(repo: Path) -> tuple[str, bool]:
    """HEAD commit and whether ``git status`` is empty. Status text is not returned."""
    try:
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--untracked-files=all"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("refusing to score without a clean git checkout") from exc
    return head, status == ""


def _scoring_provenance() -> dict:
    """Refuse a dirty checkout before a slice is scored."""
    head, clean = _git_head_and_clean(REPO)
    if not _is_sha40(head):
        raise RuntimeError("refusing to score without a git HEAD commit")
    if not clean:
        raise RuntimeError("refusing to score a dirty checkout")
    return {
        "git_head": head,
        "clean_tree": True,
        "wrapper_sha256": hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest(),
    }


def _holiday_closed_dates(gap_report: dict) -> list[str]:
    """Full closures only. Early-close rows stay on the early-close disposition."""
    found: set[str] = set()
    for item in gap_report.get("expected_holiday_closures") or []:
        if isinstance(item, dict) and item.get("kind") == "closed" and item.get("date"):
            found.add(str(item["date"]))
    return sorted(found)


def _unscored_dates(gap_report: dict, *, include_holiday_closures: bool = False) -> set[str]:
    """DATA_GAP days, held-out early closes, and slice holiday closures.

    The reproduction window leaves holiday closures eligible. A full closure
    can still have a short noon session in the trees, and a slice must not
    turn that session into a candidate.
    """
    dates = {item["date"] for item in gap_report.get("data_gaps") or [] if item.get("date")}
    for item in gap_report.get("early_close_sessions") or []:
        if isinstance(item, dict) and item.get("scored") is False and item.get("date"):
            dates.add(str(item["date"]))
    if include_holiday_closures:
        dates.update(_holiday_closed_dates(gap_report))
    return dates


def _resolved_trade(cell: dict) -> tuple[str, float] | None:
    """A resolved fill. Unresolved fills stay in the counts and out of P&L."""
    if not isinstance(cell, dict) or cell.get("status") != "RESOLVED":
        return None
    result = cell.get("result")
    net = cell.get("net")
    if result not in {"WIN", "LOSS", "BREAKEVEN"}:
        return None
    if isinstance(net, bool) or not isinstance(net, (int, float)):
        return None
    return str(result), float(net)


def _cell_reason(cell: dict) -> str | None:
    reason = cell.get("reason")
    if reason is None:
        reason = cell.get("exit_reason")
    if reason is None:
        return None
    return str(reason)


def summarize_slice_ledger(ledger: dict) -> dict:
    """Raw per-model counts at each slippage. No pass/fail threshold.

    ``net_pnl_resolved_only``, ``win_rate_resolved_only``, and
    ``drawdown_resolved_only`` use resolved fills only. An unresolved fill is
    counted, with its date and reason, and is not in those metrics. Win rate
    is wins divided by resolved trades. A breakeven trade is resolved and is
    not a win. Drawdown is the largest peak-to-trough of resolved cumulative
    net, starting from zero.
    """
    by_slippage: dict[str, dict] = {}
    for slippage in SLIPPAGES:
        rows = list(ledger.get(slippage) or [])
        models: dict[str, dict] = {}
        for model_key, label in MODEL_LABELS:
            resolved_rows: list[dict] = []
            unresolved: list[dict] = []
            no_fills: dict[str, int] = {}
            fills = 0
            for row in rows:
                cell = (row.get("models") or {}).get(model_key) or {}
                status = cell.get("status")
                if status in {"RESOLVED", "UNRESOLVED", "OPEN"}:
                    fills += 1
                if status == "UNRESOLVED":
                    unresolved.append({"date": row.get("date"), "reason": _cell_reason(cell)})
                elif status == "NO_FILL":
                    reason = _cell_reason(cell) or "UNSPECIFIED"
                    no_fills[reason] = no_fills.get(reason, 0) + 1
                resolved = _resolved_trade(cell)
                if resolved is None:
                    continue
                result, net = resolved
                resolved_rows.append(
                    {"date": row.get("date"), "result": result, "net": round(net, 2)}
                )
            equity = 0.0
            peak = 0.0
            max_drawdown = 0.0
            streak = 0
            longest = 0
            wins = 0
            total = 0.0
            for trade in resolved_rows:
                total += float(trade["net"])
                equity += float(trade["net"])
                if equity > peak:
                    peak = equity
                drawdown = peak - equity
                if drawdown > max_drawdown:
                    max_drawdown = drawdown
                if trade["result"] == "WIN":
                    wins += 1
                    streak = 0
                elif trade["result"] == "LOSS":
                    streak += 1
                    if streak > longest:
                        longest = streak
                else:
                    streak = 0
            resolved_count = len(resolved_rows)
            models[label] = {
                "candidates": len(rows),
                "fills": fills,
                "resolved": resolved_count,
                "unresolved": unresolved,
                "unresolved_count": len(unresolved),
                "no_fills": no_fills,
                "no_fill_count": sum(no_fills.values()),
                "resolved_trade_list": resolved_rows,
                "net_pnl_resolved_only": round(total, 2),
                "win_rate_resolved_only": None if resolved_count == 0 else wins / resolved_count,
                "drawdown_resolved_only": round(max_drawdown, 2),
                "longest_losing_streak_resolved_only": longest,
            }
        by_slippage[slippage] = models
    return by_slippage


def _slice_event_lines(slice_id: str) -> list[dict]:
    return [
        row
        for row in _trial_rows()
        if row.get("event") == "SLICE" and row.get("slice_id") == slice_id
    ]


def _slice_attempt_paths(slice_id: str) -> tuple[int, Path, Path]:
    attempt = len(_slice_event_lines(slice_id)) + 1
    if attempt < 1:
        raise RuntimeError(f"slice attempt {attempt} is not a positive attempt number")
    artifact = SLICE_ARTIFACT_DIR / f"{slice_id}.attempt-{attempt:02d}.json"
    ledger = SLICE_ARTIFACT_DIR / f"{slice_id}.replay-ledger.attempt-{attempt:02d}.json"
    if artifact.exists() or ledger.exists():
        raise RuntimeError(f"refusing to overwrite slice attempt {attempt} for {slice_id}")
    return attempt, artifact, ledger


def _slice_data_identity(research_15m: Path, audit_5m: Path, data_root: Path) -> dict:
    _manifest, manifest_sha256 = _load_pinned_manifest()
    profile = _scoring_profile_or_refuse()
    return {
        "manifest_sha256": manifest_sha256,
        "research_15m_sha256": _tree_sha256(research_15m),
        "audit_5m_sha256": _tree_sha256(audit_5m),
        "data_root": str(data_root),
        "MAX_CONTRACTS_HARD_CAP": profile["MAX_CONTRACTS_HARD_CAP"],
    }


def _record_slice_scoring_failure(
    slice_id: str,
    provenance: dict,
    identity: dict,
    error: str,
) -> None:
    """A scoring exception still leaves a SLICE line. Missing products stay null."""
    payload = {
        "slice_id": slice_id,
        "git_head": provenance.get("git_head"),
        "clean_tree": provenance.get("clean_tree"),
        "wrapper_sha256": provenance.get("wrapper_sha256"),
        "replay_ledger": None,
        "replay_ledger_sha256": None,
        "error": error,
    }
    payload.update(identity)
    _append_slice_event(payload, None, None)


def _evaluator_file_sha256() -> str:
    path = REPO / EVALUATOR_REL
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _market_for_slice(spec: SliceSpec) -> str:
    if spec.price_source == "MNQ":
        return "MNQ"
    if spec.price_source == "NQ":
        return "NQ"
    raise RuntimeError(f"slice {spec.id} has no decisive market")


def _require_pinned_evaluator() -> str:
    digest = _evaluator_file_sha256()
    if digest != EXPECTED_EVALUATOR_SHA256:
        raise RuntimeError(
            "refusing to score: evaluator file hash does not match EXPECTED_EVALUATOR_SHA256"
        )
    return digest


def _decisive_cell(ledger: dict, slice_id: str, market: str) -> dict:
    """Score the 3-tick honest-IOC rows and stamp the evaluator file hash."""
    digest = _require_pinned_evaluator()
    records = trade_records_from_ledger(ledger, slice_id=slice_id, market=market)
    cell = evaluate_cell(records, cell_id=f"{slice_id}/honest_ioc/3tick", market=market)
    cell["evaluator_sha256"] = digest
    return cell


def _slice_event_for_artifact(slice_id: str, artifact_name: str) -> dict:
    matches = [
        row
        for row in _trial_rows()
        if row.get("event") == "SLICE"
        and row.get("slice_id") == slice_id
        and str(row.get("slice_artifact") or "").endswith(artifact_name)
    ]
    if len(matches) != 1:
        raise RuntimeError(f"{slice_id} attempt-01 has no single SLICE event")
    return matches[0]


def _verify_attempt01_artifact(path: Path) -> dict:
    raw = path.read_bytes()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path.name} is not a slice artifact")
    event = _slice_event_for_artifact(str(payload.get("slice_id")), path.name)
    digest = hashlib.sha256(raw).hexdigest()
    if event.get("slice_artifact_sha256") != digest:
        raise RuntimeError(f"{path.name} sha256 does not match its SLICE event")
    if event.get("evaluator_sha256") != payload.get("evaluator_sha256"):
        raise RuntimeError(f"{path.name} evaluator sha256 does not match its SLICE event")
    cell = payload.get("decisive_cell")
    if not isinstance(cell, dict) or cell.get("evaluator_sha256") != payload.get("evaluator_sha256"):
        raise RuntimeError(f"{path.name} cell hash does not match the artifact")
    return payload


def _write_combine_artifact_if_ready() -> None:
    """Write one COMBINE artifact once both attempt-01 slice artifacts exist."""
    mnq_path = SLICE_ARTIFACT_DIR / "mnq-2019-05-to-2024-06.attempt-01.json"
    nq_path = SLICE_ARTIFACT_DIR / "nq-2010-06-to-2019-05.attempt-01.json"
    out = SLICE_ARTIFACT_DIR / "combine.attempt-01.json"
    if not mnq_path.is_file() or not nq_path.is_file() or out.exists():
        return
    mnq_raw = mnq_path.read_bytes()
    nq_raw = nq_path.read_bytes()
    mnq = _verify_attempt01_artifact(mnq_path)
    nq = _verify_attempt01_artifact(nq_path)
    combined = combine_cells(nq["decisive_cell"], mnq["decisive_cell"])
    payload = {
        "overall": combined["overall"],
        "verdict": combined["verdict"],
        "cells": combined["cells"],
        "evaluator_sha256": combined["evaluator_sha256"],
        "unsafe_account": combined["unsafe_account"],
        "inputs": {
            "nq-2010-06-to-2019-05": {
                "slice_artifact": _repo_relative(nq_path),
                "slice_artifact_sha256": hashlib.sha256(nq_raw).hexdigest(),
                "evaluator_sha256": nq["evaluator_sha256"],
            },
            "mnq-2019-05-to-2024-06": {
                "slice_artifact": _repo_relative(mnq_path),
                "slice_artifact_sha256": hashlib.sha256(mnq_raw).hexdigest(),
                "evaluator_sha256": mnq["evaluator_sha256"],
            },
        },
    }
    out.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _score_admitted_slice(
    research_15m: Path,
    audit_5m: Path,
    slice_id: str,
    *,
    data_root: Path,
) -> dict:
    """Raw slice results plus the registered decisive cell (honest IOC, 3 ticks). The gate verdict is the COMBINE artifact."""
    _require_pinned_evaluator()
    provenance = _scoring_provenance()
    identity = _slice_data_identity(research_15m, audit_5m, data_root)
    spec = next((item for item in SLICES if item.id == slice_id), None)
    if spec is None or spec.id == "combined":
        raise RuntimeError(f"unknown slice {slice_id}")
    _attempt, artifact_path, ledger_path = _slice_attempt_paths(slice_id)
    try:
        scored = _run_window(
            research_15m,
            audit_5m,
            start=spec.start,
            end=spec.end,
            expected_count=None,
        )
    except Exception as exc:
        _record_slice_scoring_failure(slice_id, provenance, identity, str(exc))
        raise
    ledger = scored.get("ledger") if isinstance(scored, dict) else None
    if not isinstance(ledger, dict):
        _record_slice_scoring_failure(
            slice_id, provenance, identity, "slice scoring produced no replay ledger"
        )
        raise RuntimeError("slice scoring produced no replay ledger")
    try:
        cell = _decisive_cell(ledger, spec.id, _market_for_slice(spec))
    except Exception as exc:
        _record_slice_scoring_failure(slice_id, provenance, identity, str(exc))
        raise
    evaluator_sha256 = cell["evaluator_sha256"]
    payload = {
        "slice_id": spec.id,
        "start": spec.start.isoformat(),
        "end": spec.end.isoformat(),
        "price_source": spec.price_source,
        "label": spec.label,
        "dollar_basis": "MNQ-equivalent ($2/point)",
        "older_slice_ioc_note": OLDER_SLICE_IOC_NOTE,
        "git_head": provenance["git_head"],
        "clean_tree": provenance["clean_tree"],
        "wrapper_sha256": provenance["wrapper_sha256"],
        "by_slippage": summarize_slice_ledger(ledger),
        "data_gaps": scored.get("data_gaps") or [],
        "data_gap_count": scored.get("data_gap_count") or 0,
        "early_close_sessions": scored.get("early_close_sessions") or [],
        "holiday_closed_unscored": list(scored.get("holiday_closed_unscored") or []),
        "holiday_closed_unscored_count": int(scored.get("holiday_closed_unscored_count") or 0),
        "invalid_entry_bracket_count": int(scored.get("invalid_entry_bracket_count") or 0),
        "invalid_entry_bracket_gap_open_count": int(
            scored.get("invalid_entry_bracket_gap_open_count") or 0
        ),
        "invalid_entry_bracket_non_gap_open_count": int(
            scored.get("invalid_entry_bracket_non_gap_open_count") or 0
        ),
        "slippage_sensitivity": SLIPPAGE_SENSITIVITY_NOTE,
        "evaluator_path": EVALUATOR_REL,
        "evaluator_sha256": evaluator_sha256,
        "decisive_cell": cell,
    }
    payload.update(identity)
    if spec.price_source == "NQ":
        payload["result_label"] = NQ_RESEARCH_LABEL
    if ledger_path.exists() or artifact_path.exists():
        raise RuntimeError(f"refusing to overwrite slice attempt for {slice_id}")
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_text = json.dumps({"ledger": ledger}, indent=2, default=str) + "\n"
    ledger_path.write_text(ledger_text, encoding="utf-8")
    payload["replay_ledger"] = _repo_relative(ledger_path)
    payload["replay_ledger_sha256"] = hashlib.sha256(ledger_text.encode()).hexdigest()
    artifact_text = json.dumps(payload, indent=2, default=str) + "\n"
    artifact_path.write_text(artifact_text, encoding="utf-8")
    digest = hashlib.sha256(artifact_text.encode()).hexdigest()
    _append_slice_event(payload, digest, _repo_relative(artifact_path))
    _write_combine_artifact_if_ready()
    return payload


def _slice_stamp(rows: list[dict]) -> datetime:
    stamp = datetime.now(timezone.utc).replace(microsecond=0)
    for row in rows:
        if row.get("trial_id") != TRIAL_ID or not isinstance(row.get("recorded_at"), str):
            continue
        previous = datetime.fromisoformat(row["recorded_at"].replace("Z", "+00:00"))
        if stamp <= previous:
            stamp = previous + timedelta(seconds=1)
    return stamp


def _append_slice_event(
    payload: dict,
    artifact_sha256: str | None,
    artifact_rel: str | None,
) -> None:
    lines = TRIAL_LEDGER.read_text(encoding="utf-8").splitlines()
    rows = [json.loads(line) for line in lines if line.strip()]
    first = next(row for row in rows if row.get("trial_id") == TRIAL_ID)
    event = {key: first.get(key) for key in _FROZEN_LEDGER_FIELDS}
    event.update(
        {
            "trial_id": TRIAL_ID,
            "event": "SLICE",
            "slice_id": payload["slice_id"],
            "recorded_at": _slice_stamp(rows).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recorded_by": "slice-scoring",
            "slice_artifact": artifact_rel,
            "slice_artifact_sha256": artifact_sha256,
            "replay_ledger": payload.get("replay_ledger"),
            "replay_ledger_sha256": payload.get("replay_ledger_sha256"),
            "git_head": payload.get("git_head"),
            "clean_tree": payload.get("clean_tree"),
            "wrapper_sha256": payload.get("wrapper_sha256"),
            "manifest_sha256": payload.get("manifest_sha256"),
            "research_15m_sha256": payload.get("research_15m_sha256"),
            "audit_5m_sha256": payload.get("audit_5m_sha256"),
            "data_root": payload.get("data_root"),
            "MAX_CONTRACTS_HARD_CAP": payload.get("MAX_CONTRACTS_HARD_CAP"),
            "holiday_closed_unscored": payload.get("holiday_closed_unscored"),
            "holiday_closed_unscored_count": payload.get("holiday_closed_unscored_count"),
            "invalid_entry_bracket_gap_open_count": payload.get(
                "invalid_entry_bracket_gap_open_count"
            ),
            "invalid_entry_bracket_non_gap_open_count": payload.get(
                "invalid_entry_bracket_non_gap_open_count"
            ),
            "evaluator_path": payload.get("evaluator_path"),
            "evaluator_sha256": payload.get("evaluator_sha256"),
            "error": payload.get("error"),
            "notes": "Raw slice results plus the registered decisive cell (honest IOC, 3 ticks). The gate verdict is the COMBINE artifact.",
        }
    )
    with TRIAL_LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, separators=(",", ":")) + "\n")


def run_declared_slices(
    research_15m: Path | None = None,
    audit_5m: Path | None = None,
    *,
    data_root: Path | None = None,
    slice_id: str | None = None,
) -> dict | None:
    """Admit one slice, then score it only when ``SLICE_SCORING_AUTHORIZED`` is true.

    Every admission check still runs: the pinned manifest, the slice tree
    hashes, refusal of ``combined``, the SUPERSEDED check, and
    ``MAX_CONTRACTS_HARD_CAP`` exactly equal to ``1``. The authorization
    constant is true. An admitted slice is scored and the evaluator hash is
    stamped on the artifact. Setting the constant false returns None and
    writes nothing.

    When the constant is true, the checkout must be clean. The artifact
    records git HEAD, that clean-tree check, and this file's sha256. The
    attempt writes its own artifact and replay ledger. An existing attempt
    file is refused. The ledger event records both sha256 values. Results
    are raw trades, net P&L, win rate, drawdown, and the longest losing
    streak at each slippage. There is no pass/fail criterion.

    A ``claude_review`` string in the gate artifact is self-attested and is
    not authorization. The review record is a ``GATE_REVIEW`` ledger event.
    Pull-request review is the real gate. A download change does not unlock
    this trial. Mark it SUPERSEDED and preregister a new trial id.
    """
    if slice_id == "combined":
        raise RuntimeError("combined is report-only and is not admitted")
    _scoring_profile_or_refuse()
    artifact = _verified_gate_artifact()
    if artifact.get("pass") is not True:
        raise RuntimeError("reproduction gate did not PASS")
    review = artifact.get("claude_review")
    if review is not None and not isinstance(review, str):
        raise RuntimeError(
            "Claude's review of the gate result is required before the older slices run"
        )
    _refuse_changed_gate_data(artifact)
    _refuse_if_superseded()
    path = _matching_attempt_file(_gate_lines()[-1].get("gate_artifact_sha256"))
    if path is None:
        raise RuntimeError("reproduction-gate artifact hash mismatch")
    _require_gate_review(hashlib.sha256(path.read_bytes()).hexdigest())
    if data_root is None or research_15m is None or audit_5m is None or slice_id is None:
        raise RuntimeError("slice admission requires data_root, the slice id, and its trees")
    root = _resolved_data_root(data_root)
    research = _resolve_under_root(root, research_15m)
    audit = _resolve_under_root(root, audit_5m)
    _refuse_slice_data_mismatch(artifact, research, audit, slice_id)
    _refuse_ohl_disagreement()
    if not SLICE_SCORING_AUTHORIZED:
        return None
    return _score_admitted_slice(research, audit, slice_id, data_root=root)


def _target_and_stop_nets(
    direction: str,
    fill_price: float,
    stop: float,
    target: float,
    slippage_ticks: float,
) -> tuple[float, float]:
    """Net dollars if this fill hits its own target, and if it hits its own stop.

    Entry is the actual fill. Adverse slippage is applied to both exits, and
    the round-turn commission is charged on both. The dollar scale is the
    unchanged MNQ contract economics.
    """
    tick, tick_value = contract_economics("MNQ")
    slip = float(slippage_ticks) * tick
    dollars_per_point = tick_value / tick
    if direction == "LONG":
        win_points = (target - slip) - fill_price
        loss_points = (stop - slip) - fill_price
    elif direction == "SHORT":
        win_points = fill_price - (target + slip)
        loss_points = fill_price - (stop + slip)
    else:
        raise RuntimeError(f"unsupported direction {direction!r}")
    win_net = win_points * dollars_per_point - COMMISSION_ROUND_TRIP
    loss_net = loss_points * dollars_per_point - COMMISSION_ROUND_TRIP
    return win_net, loss_net


def aggregate_break_even(ledger: dict) -> dict:
    """Shared win rate at which aggregate net P&L is zero.

    One rate is solved from the sums of each trade's target net and stop net.
    It is not the average of the per-trade break-even percentages. Only
    resolved fills are trades. 2025-01-20 stays out of the rate while it is
    unresolved.
    """
    by_model: dict[str, dict] = {}
    for slippage in SLIPPAGES:
        ticks = float(slippage.removesuffix("tick"))
        rows = list(ledger[slippage])
        for model_key, label in MODEL_LABELS:
            reward = 0.0
            risk = 0.0
            trades = 0
            for row in rows:
                model = row["models"][model_key]
                if model.get("status") != "RESOLVED" or model.get("fill_entry") is None:
                    continue
                win_net, loss_net = _target_and_stop_nets(
                    str(row["direction"]),
                    float(model["fill_entry"]),
                    float(row["stop"]),
                    float(row["target"]),
                    ticks,
                )
                reward += win_net
                risk += loss_net
                trades += 1
            denominator = reward - risk
            if trades == 0 or denominator == 0:
                rate = None
                expectancy = None
            else:
                rate = -risk / denominator
                expectancy = rate * reward + (1.0 - rate) * risk
            by_model.setdefault(label, {})[slippage] = {
                "trades": trades,
                "reward_net_total": round(reward, 2),
                "risk_net_total": round(risk, 2),
                "break_even_win_rate": None if rate is None else rate,
                "aggregate_net_expectancy": None if expectancy is None else round(expectancy, 10),
                "averaged_per_trade_rates": False,
            }
    return {
        "method": "single_shared_win_rate",
        "slippage_sensitivity": SLIPPAGE_SENSITIVITY_NOTE,
        "by_model": by_model,
    }


def assemble_slice_report(mnq_summary: dict, nq_summary: dict) -> dict:
    """Place the two slice summaries next to a combined count. No retune."""
    nq = next(item for item in SLICES if item.price_source == "NQ")
    nq_block = {
        **nq_summary,
        "price_source": "NQ",
        "label": nq.label,
        "dollar_basis": "MNQ-equivalent ($2/point)",
        "result_label": NQ_RESEARCH_LABEL,
        "slippage_sensitivity": SLIPPAGE_SENSITIVITY_NOTE,
        "older_slice_ioc_note": OLDER_SLICE_IOC_NOTE,
    }
    return {
        "mnq-2019-05-to-2024-06": {
            **mnq_summary,
            "price_source": "MNQ",
            "dollar_basis": "MNQ-equivalent ($2/point)",
            "slippage_sensitivity": SLIPPAGE_SENSITIVITY_NOTE,
            "older_slice_ioc_note": OLDER_SLICE_IOC_NOTE,
        },
        "nq-2010-06-to-2019-05": nq_block,
        "combined": {
            "label": "reported separately; no retuning",
            "retuned": False,
            "parts": ["mnq-2019-05-to-2024-06", "nq-2010-06-to-2019-05"],
            "candidate_count": int(mnq_summary.get("candidate_count") or 0)
            + int(nq_summary.get("candidate_count") or 0),
            "result_label": NQ_RESEARCH_LABEL,
            "slippage_sensitivity": SLIPPAGE_SENSITIVITY_NOTE,
            "older_slice_ioc_note": OLDER_SLICE_IOC_NOTE,
        },
    }
