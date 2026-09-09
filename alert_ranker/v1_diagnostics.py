"""Read-only diagnostics capture and reporting for Options Paper V1.

This module deliberately does not change setup admission, contract selection,
stops, targets, sizing, or broker behavior.  It adds evidence needed to explain
*why* a strategy works or fails after the forward sample exists:

* option and underlying MAE/MFE;
* directional entry extension beyond the mechanical Strat trigger;
* configurable friction-stress overlays on recorded ask-entry/bid-exit fills;
* deterministic evidence-quality flags for stale/missing/gapped telemetry;
* sample-size and uncertainty reporting by setup/timeframe/evidence lane.

The diagnostics table is append-only and separate from the canonical shadow
journal/contract-mark tables so telemetry changes cannot rewrite V1 outcomes.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .contract_marks import contract_marks
from .paper_v1 import POLICY_ID
from .storage import ScanStorage

COUNTERFACTUAL_LANE = "COUNTERFACTUAL"
ACTIVE_LANE = "ACTIVE"
EXPECTED_MARK_INTERVAL_MINUTES = 5.0
MAX_ACCEPTABLE_MARK_GAP_MINUTES = 12.0
MAX_ENTRY_QUOTE_AGE_SECONDS = 120.0
MIN_RESOLVED_MARKS = 2
MIN_EARLY_SAMPLE = 20
MIN_REVIEWABLE_SAMPLE = 50
CONTRACT_MULTIPLIER = 100.0

# These are stress assumptions, not a claim about the user's broker fee schedule.
# The canonical V1 result remains the recorded ask-entry/bid-exit path.
FRICTION_SCENARIOS = (
    {
        "name": "RECORDED_EXECUTABLE",
        "fee_per_contract_per_leg": 0.0,
        "extra_slippage_per_share_per_leg": 0.0,
    },
    {
        "name": "FEE_STRESS_065",
        "fee_per_contract_per_leg": 0.65,
        "extra_slippage_per_share_per_leg": 0.0,
    },
    {
        "name": "FEE_065_PLUS_1C_SLIPPAGE",
        "fee_per_contract_per_leg": 0.65,
        "extra_slippage_per_share_per_leg": 0.01,
    },
)


@dataclass(frozen=True)
class DiagnosticSnapshot:
    shadow_id: int
    timestamp: datetime
    event: str
    underlying_price: float | None
    setup_entry_trigger: float | None
    option_bid: float | None
    option_ask: float | None
    option_mid: float | None
    quote_timestamp: datetime | None
    delta: float | None
    gamma: float | None
    theta: float | None
    implied_volatility: float | None
    error: str
    setup_type: str | None
    setup_timeframe: str | None
    paper_evidence_lane: str
    raw: dict[str, Any]


def _num(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _normalize_direction(value: Any) -> str:
    text = str(value or "").upper()
    if text in {"LONG", "CALL", "BULL", "BULLISH"}:
        return "LONG"
    if text in {"SHORT", "PUT", "BEAR", "BEARISH"}:
        return "SHORT"
    return text


def _lane(selected: dict[str, Any], setup_inputs: dict[str, Any] | None = None) -> str:
    setup_inputs = setup_inputs or {}
    lane = selected.get("paper_evidence_lane") or setup_inputs.get("paper_evidence_lane")
    if lane == COUNTERFACTUAL_LANE or selected.get("risk_budget_consumed") is False:
        return COUNTERFACTUAL_LANE
    return ACTIVE_LANE


def ensure_diagnostics_schema(storage: ScanStorage) -> None:
    with storage._connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS options_v1_diagnostic_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shadow_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                event TEXT NOT NULL,
                underlying_price REAL,
                setup_entry_trigger REAL,
                option_bid REAL,
                option_ask REAL,
                option_mid REAL,
                quote_timestamp TEXT,
                delta REAL,
                gamma REAL,
                theta REAL,
                implied_volatility REAL,
                error TEXT NOT NULL DEFAULT '',
                setup_type TEXT,
                setup_timeframe TEXT,
                paper_evidence_lane TEXT NOT NULL DEFAULT 'ACTIVE',
                raw_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(shadow_id, timestamp, event)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_options_v1_diag_shadow_time "
            "ON options_v1_diagnostic_snapshots (shadow_id, timestamp)"
        )


def record_diagnostic_snapshot(
    storage: ScanStorage,
    *,
    shadow_id: int,
    timestamp: datetime,
    event: str,
    underlying_price: Any = None,
    setup_entry_trigger: Any = None,
    option_bid: Any = None,
    option_ask: Any = None,
    option_mid: Any = None,
    quote_timestamp: Any = None,
    delta: Any = None,
    gamma: Any = None,
    theta: Any = None,
    implied_volatility: Any = None,
    error: str = "",
    setup_type: Any = None,
    setup_timeframe: Any = None,
    paper_evidence_lane: str = ACTIVE_LANE,
    raw: dict[str, Any] | None = None,
) -> int:
    ensure_diagnostics_schema(storage)
    quote_dt = _parse_dt(quote_timestamp)
    with storage._connect() as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO options_v1_diagnostic_snapshots (
                shadow_id, timestamp, event, underlying_price, setup_entry_trigger,
                option_bid, option_ask, option_mid, quote_timestamp, delta, gamma,
                theta, implied_volatility, error, setup_type, setup_timeframe,
                paper_evidence_lane, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(shadow_id),
                _utc_iso(timestamp),
                str(event).upper(),
                _num(underlying_price),
                _num(setup_entry_trigger),
                _num(option_bid),
                _num(option_ask),
                _num(option_mid),
                quote_dt.isoformat() if quote_dt else None,
                _num(delta),
                _num(gamma),
                _num(theta),
                _num(implied_volatility),
                str(error or ""),
                str(setup_type) if setup_type not in (None, "") else None,
                str(setup_timeframe) if setup_timeframe not in (None, "") else None,
                COUNTERFACTUAL_LANE if paper_evidence_lane == COUNTERFACTUAL_LANE else ACTIVE_LANE,
                json.dumps(raw or {}, sort_keys=True, default=str),
            ),
        )
        return int(cursor.lastrowid or 0)


def diagnostic_snapshots(storage: ScanStorage, shadow_id: int) -> list[DiagnosticSnapshot]:
    ensure_diagnostics_schema(storage)
    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT shadow_id, timestamp, event, underlying_price,
                   setup_entry_trigger, option_bid, option_ask, option_mid,
                   quote_timestamp, delta, gamma, theta, implied_volatility,
                   error, setup_type, setup_timeframe, paper_evidence_lane,
                   raw_json
            FROM options_v1_diagnostic_snapshots
            WHERE shadow_id = ? ORDER BY timestamp ASC, id ASC
            """,
            (int(shadow_id),),
        ).fetchall()
    result: list[DiagnosticSnapshot] = []
    for row in rows:
        stamp = _parse_dt(row["timestamp"])
        if stamp is None:
            continue
        result.append(
            DiagnosticSnapshot(
                shadow_id=int(row["shadow_id"]),
                timestamp=stamp,
                event=str(row["event"]),
                underlying_price=_num(row["underlying_price"]),
                setup_entry_trigger=_num(row["setup_entry_trigger"]),
                option_bid=_num(row["option_bid"]),
                option_ask=_num(row["option_ask"]),
                option_mid=_num(row["option_mid"]),
                quote_timestamp=_parse_dt(row["quote_timestamp"]),
                delta=_num(row["delta"]),
                gamma=_num(row["gamma"]),
                theta=_num(row["theta"]),
                implied_volatility=_num(row["implied_volatility"]),
                error=str(row["error"] or ""),
                setup_type=row["setup_type"],
                setup_timeframe=row["setup_timeframe"],
                paper_evidence_lane=str(row["paper_evidence_lane"] or ACTIVE_LANE),
                raw=json.loads(row["raw_json"] or "{}"),
            )
        )
    return result


def directional_entry_extension(
    direction: Any,
    entry_underlying: Any,
    trigger: Any,
) -> tuple[float | None, float | None]:
    price = _num(entry_underlying)
    level = _num(trigger)
    side = _normalize_direction(direction)
    if price is None or level is None or level == 0 or side not in {"LONG", "SHORT"}:
        return None, None
    signed = price - level if side == "LONG" else level - price
    return round(signed, 6), round((signed / abs(level)) * 100.0, 6)


def friction_pnl_dollars(
    *,
    entry_ask: float,
    exit_bid: float,
    contracts: int,
    fee_per_contract_per_leg: float = 0.0,
    extra_slippage_per_share_per_leg: float = 0.0,
) -> float:
    quantity = max(0, int(contracts))
    gross = (float(exit_bid) - float(entry_ask)) * CONTRACT_MULTIPLIER * quantity
    fees = 2.0 * float(fee_per_contract_per_leg) * quantity
    extra_slippage = (
        2.0
        * float(extra_slippage_per_share_per_leg)
        * CONTRACT_MULTIPLIER
        * quantity
    )
    return round(gross - fees - extra_slippage, 2)


def _friction_scenarios(
    *, entry_ask: float | None, exit_bid: float | None, contracts: int
) -> dict[str, float | None]:
    if entry_ask is None or exit_bid is None or contracts <= 0:
        return {str(item["name"]): None for item in FRICTION_SCENARIOS}
    return {
        str(item["name"]): friction_pnl_dollars(
            entry_ask=entry_ask,
            exit_bid=exit_bid,
            contracts=contracts,
            fee_per_contract_per_leg=float(item["fee_per_contract_per_leg"]),
            extra_slippage_per_share_per_leg=float(
                item["extra_slippage_per_share_per_leg"]
            ),
        )
        for item in FRICTION_SCENARIOS
    }


def _quote_age_seconds(snapshot: DiagnosticSnapshot) -> float | None:
    if snapshot.quote_timestamp is None:
        return None
    return max(0.0, (snapshot.timestamp - snapshot.quote_timestamp).total_seconds())


def _max_gap_minutes(snapshots: Iterable[DiagnosticSnapshot]) -> float | None:
    ordered = sorted((item.timestamp for item in snapshots))
    if len(ordered) < 2:
        return None
    return round(
        max((later - earlier).total_seconds() / 60.0 for earlier, later in zip(ordered, ordered[1:])),
        4,
    )


def _mae_mfe_option(entry_ask: float | None, marks: list[dict[str, Any]]) -> dict[str, float | None]:
    if entry_ask is None or entry_ask <= 0:
        return {
            "option_mae_dollars_per_share": None,
            "option_mae_percent": None,
            "option_mfe_dollars_per_share": None,
            "option_mfe_percent": None,
        }
    bids = [_num(item.get("bid")) for item in marks if not item.get("error")]
    bids = [value for value in bids if value is not None and value > 0]
    if not bids:
        return {
            "option_mae_dollars_per_share": None,
            "option_mae_percent": None,
            "option_mfe_dollars_per_share": None,
            "option_mfe_percent": None,
        }
    low = min(bids)
    high = max(bids)
    mae = max(0.0, entry_ask - low)
    mfe = max(0.0, high - entry_ask)
    return {
        "option_mae_dollars_per_share": round(mae, 4),
        "option_mae_percent": round((mae / entry_ask) * 100.0, 4),
        "option_mfe_dollars_per_share": round(mfe, 4),
        "option_mfe_percent": round((mfe / entry_ask) * 100.0, 4),
    }


def _mae_mfe_underlying(
    direction: str,
    entry_price: float | None,
    snapshots: list[DiagnosticSnapshot],
) -> dict[str, float | None]:
    if entry_price is None or entry_price <= 0:
        return {
            "underlying_mae": None,
            "underlying_mae_percent": None,
            "underlying_mfe": None,
            "underlying_mfe_percent": None,
        }
    values = [item.underlying_price for item in snapshots if item.underlying_price is not None]
    if not values:
        return {
            "underlying_mae": None,
            "underlying_mae_percent": None,
            "underlying_mfe": None,
            "underlying_mfe_percent": None,
        }
    side = _normalize_direction(direction)
    if side == "LONG":
        mae = max(0.0, entry_price - min(values))
        mfe = max(0.0, max(values) - entry_price)
    elif side == "SHORT":
        mae = max(0.0, max(values) - entry_price)
        mfe = max(0.0, entry_price - min(values))
    else:
        mae = mfe = 0.0
    return {
        "underlying_mae": round(mae, 6),
        "underlying_mae_percent": round((mae / entry_price) * 100.0, 4),
        "underlying_mfe": round(mfe, 6),
        "underlying_mfe_percent": round((mfe / entry_price) * 100.0, 4),
    }


def _quality_flags(
    *,
    status: str,
    snapshots: list[DiagnosticSnapshot],
    marks: list[dict[str, Any]],
    outcome: dict[str, Any],
    entry_underlying: float | None,
    entry_trigger: float | None,
    exit_bid: float | None,
) -> list[str]:
    flags: list[str] = []
    entry = next((item for item in snapshots if item.event == "ENTRY"), None)
    if entry is None:
        flags.append("DIAGNOSTIC_ENTRY_MISSING")
    if entry_underlying is None:
        flags.append("ENTRY_UNDERLYING_MISSING")
    if entry_trigger is None:
        flags.append("ENTRY_TRIGGER_MISSING")
    if entry is not None:
        age = _quote_age_seconds(entry)
        if age is None:
            flags.append("ENTRY_QUOTE_TIMESTAMP_MISSING")
        elif age > MAX_ENTRY_QUOTE_AGE_SECONDS:
            flags.append("ENTRY_QUOTE_STALE_GT_120S")
        if entry.delta is None:
            flags.append("DELTA_MISSING")
        if entry.gamma is None:
            flags.append("GAMMA_MISSING")
        if entry.theta is None:
            flags.append("THETA_MISSING")
        if entry.implied_volatility is None:
            flags.append("IV_MISSING")
    gap = _max_gap_minutes(snapshots)
    if gap is not None and gap > MAX_ACCEPTABLE_MARK_GAP_MINUTES:
        flags.append("OBSERVATION_GAP_GT_12M")
    valid_marks = [item for item in marks if not item.get("error") and _num(item.get("bid"))]
    if status != "OPEN" and len(valid_marks) < MIN_RESOLVED_MARKS:
        flags.append("RESOLVED_MARK_PATH_TOO_SPARSE")
    if status != "OPEN" and exit_bid is None:
        flags.append("RESOLVED_EXIT_BID_MISSING")
    if outcome.get("resolution_ambiguity") == "AMBIGUOUS":
        flags.append("AMBIGUOUS_RESOLUTION_PATH")
    if outcome.get("intra_interval_path_known") is False:
        flags.append("INTRA_INTERVAL_PATH_UNOBSERVED")
    if any(item.error for item in snapshots):
        flags.append("DIAGNOSTIC_PROVIDER_ERROR_PRESENT")
    return sorted(set(flags))


def evidence_quality_grade(flags: Iterable[str]) -> str:
    flag_set = set(flags)
    critical = {
        "DIAGNOSTIC_ENTRY_MISSING",
        "ENTRY_UNDERLYING_MISSING",
        "ENTRY_TRIGGER_MISSING",
        "ENTRY_QUOTE_STALE_GT_120S",
        "OBSERVATION_GAP_GT_12M",
        "RESOLVED_MARK_PATH_TOO_SPARSE",
        "RESOLVED_EXIT_BID_MISSING",
        "AMBIGUOUS_RESOLUTION_PATH",
        "DIAGNOSTIC_PROVIDER_ERROR_PRESENT",
    }
    if flag_set & critical:
        return "LOW"
    if flag_set:
        return "MEDIUM"
    return "HIGH"


def _active_or_counterfactual_rows(storage: ScanStorage) -> list[dict[str, Any]]:
    with storage._connect() as conn:
        rows = conn.execute(
            """
            SELECT id, timestamp, ticker, direction, status, pattern,
                   setup_inputs_json, selected_contract_json, outcome_json
            FROM options_shadow_journal
            WHERE selected_contract_json LIKE ?
            ORDER BY timestamp ASC, id ASC
            """,
            (f"%{POLICY_ID}%",),
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        selected = json.loads(row["selected_contract_json"] or "{}")
        if selected.get("paper_policy_id") != POLICY_ID:
            continue
        setup_inputs = json.loads(row["setup_inputs_json"] or "{}")
        result.append(
            {
                "id": int(row["id"]),
                "timestamp": row["timestamp"],
                "ticker": row["ticker"],
                "direction": row["direction"],
                "status": str(row["status"] or "OPEN").upper(),
                "pattern": row["pattern"],
                "setup_inputs": setup_inputs,
                "selected": selected,
                "outcome": json.loads(row["outcome_json"] or "{}"),
                "lane": _lane(selected, setup_inputs),
            }
        )
    return result


def trade_diagnostic(storage: ScanStorage, row: dict[str, Any]) -> dict[str, Any]:
    selected = row["selected"]
    setup_inputs = row["setup_inputs"]
    outcome = row["outcome"]
    snapshots = diagnostic_snapshots(storage, row["id"])
    marks = contract_marks(storage, row["id"], limit=5000)
    entry_snapshot = next((item for item in snapshots if item.event == "ENTRY"), None)

    entry_ask = _num(
        selected.get("option_mark")
        or selected.get("entry_quote")
        or selected.get("option_ask")
    )
    contracts = int(_num(selected.get("contracts") or selected.get("quantity") or 1) or 1)
    exit_bid = _num(
        outcome.get("exit_mark")
        or outcome.get("option_bid_at_resolution")
        or outcome.get("exit_premium")
    )
    entry_underlying = (
        entry_snapshot.underlying_price if entry_snapshot else None
    )
    if entry_underlying is None:
        entry_underlying = _num(
            setup_inputs.get("price")
            or setup_inputs.get("underlying_price")
            or setup_inputs.get("spot")
        )
    entry_trigger = (
        entry_snapshot.setup_entry_trigger if entry_snapshot else None
    )
    if entry_trigger is None:
        entry_trigger = _num(
            setup_inputs.get("setup_entry_trigger")
            or setup_inputs.get("entry_trigger")
        )
    extension, extension_percent = directional_entry_extension(
        row["direction"], entry_underlying, entry_trigger
    )
    option_excursion = _mae_mfe_option(entry_ask, marks)
    underlying_excursion = _mae_mfe_underlying(
        row["direction"], entry_underlying, snapshots
    )
    quote_ages = [
        age for age in (_quote_age_seconds(item) for item in snapshots) if age is not None
    ]
    flags = _quality_flags(
        status=row["status"],
        snapshots=snapshots,
        marks=marks,
        outcome=outcome,
        entry_underlying=entry_underlying,
        entry_trigger=entry_trigger,
        exit_bid=exit_bid,
    )
    entry_time = _parse_dt(row["timestamp"])
    exit_time = _parse_dt(outcome.get("resolved_at"))
    hold_minutes = None
    if entry_time is not None and exit_time is not None:
        hold_minutes = round(max(0.0, (exit_time - entry_time).total_seconds() / 60.0), 2)

    setup_type = (
        selected.get("setup_type")
        or setup_inputs.get("setup_type")
        or row.get("pattern")
    )
    timeframe = (
        selected.get("setup_timeframe")
        or setup_inputs.get("setup_timeframe")
        or setup_inputs.get("timeframe")
        or "UNKNOWN"
    )
    event_risk = (
        setup_inputs.get("event_risk")
        or setup_inputs.get("iv_event_risk")
        or setup_inputs.get("earnings_risk")
        or "EVENT_RISK_UNKNOWN"
    )

    friction = _friction_scenarios(
        entry_ask=entry_ask,
        exit_bid=exit_bid,
        contracts=contracts,
    )
    result = {
        "shadow_id": row["id"],
        "ticker": row["ticker"],
        "direction": _normalize_direction(row["direction"]),
        # `status` in options_shadow_journal records WHICH UNDERLYING EVENT closed
        # the episode (target touched / stop touched).  It is not a financial
        # result: a `WIN` row can and does carry negative option P&L.  Both are
        # published under unambiguous names so no consumer can read one as the
        # other, and `financial_outcome` is derived from recorded P&L only.
        "status": row["status"],
        "underlying_target_event": row["status"],
        "financial_outcome": _financial_outcome(
            friction.get("RECORDED_EXECUTABLE"), row["status"]
        ),
        "setup_type": setup_type,
        "timeframe": timeframe,
        "paper_evidence_lane": row["lane"],
        "entry_ask": entry_ask,
        "exit_bid": exit_bid,
        "contracts": contracts,
        "entry_underlying": entry_underlying,
        "setup_entry_trigger": entry_trigger,
        "directional_entry_extension": extension,
        "directional_entry_extension_percent": extension_percent,
        "option_mark_count": len([item for item in marks if not item.get("error")]),
        "diagnostic_snapshot_count": len(snapshots),
        "max_observation_gap_minutes": _max_gap_minutes(snapshots),
        "entry_quote_age_seconds": _quote_age_seconds(entry_snapshot) if entry_snapshot else None,
        "max_quote_age_seconds": round(max(quote_ages), 2) if quote_ages else None,
        "evidence_quality": evidence_quality_grade(flags),
        "evidence_flags": flags,
        "hold_minutes": hold_minutes,
        "event_risk_state": event_risk,
        "resolution_ambiguity": outcome.get("resolution_ambiguity"),
        "friction_pnl_dollars": friction,
    }
    result.update(option_excursion)
    result.update(underlying_excursion)
    return result


def _wilson_interval(wins: int, total: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = wins / total
    denominator = 1.0 + (z * z / total)
    center = (p + (z * z / (2.0 * total))) / denominator
    margin = (
        z
        * math.sqrt((p * (1.0 - p) / total) + (z * z / (4.0 * total * total)))
        / denominator
    )
    return round(max(0.0, center - margin) * 100.0, 2), round(
        min(1.0, center + margin) * 100.0, 2
    )


def _mean_ci(values: list[float], z: float = 1.96) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    mean = statistics.mean(values)
    if len(values) == 1:
        return round(mean, 2), round(mean, 2)
    se = statistics.stdev(values) / math.sqrt(len(values))
    return round(mean - z * se, 2), round(mean + z * se, 2)


def _max_trade_sequence_drawdown(values: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return round(max_dd, 2)


def _financial_outcome(recorded_pnl: float | None, status: str | None) -> str:
    """Financial result derived from recorded P&L, never from the journal label."""
    if str(status or "OPEN").upper() == "OPEN":
        return "OPEN"
    if recorded_pnl is None:
        return "UNPRICED"
    if recorded_pnl > 0:
        return "PROFIT"
    if recorded_pnl < 0:
        return "LOSS"
    return "BREAKEVEN"


def sample_status(n: int) -> str:
    # Reporting label only.  It is not a strategy-promotion rule.
    if n < MIN_EARLY_SAMPLE:
        return "INSUFFICIENT"
    if n < MIN_REVIEWABLE_SAMPLE:
        return "EARLY"
    return "REVIEWABLE"


def strategy_summary(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for trade in trades:
        key = (
            str(trade.get("paper_evidence_lane") or ACTIVE_LANE),
            str(trade.get("setup_type") or "UNKNOWN"),
            str(trade.get("timeframe") or "UNKNOWN"),
        )
        groups.setdefault(key, []).append(trade)

    summaries: list[dict[str, Any]] = []
    for (lane, setup_type, timeframe), rows in sorted(groups.items()):
        closed = [
            row
            for row in rows
            if row.get("status") in {"WIN", "LOSS", "BREAKEVEN", "EXPIRED"}
            and row.get("friction_pnl_dollars", {}).get("RECORDED_EXECUTABLE") is not None
        ]
        recorded = [
            float(row["friction_pnl_dollars"]["RECORDED_EXECUTABLE"])
            for row in closed
        ]
        wins = sum(1 for value in recorded if value > 0)
        losses = sum(1 for value in recorded if value < 0)
        win_low, win_high = _wilson_interval(wins, len(recorded))
        mean_low, mean_high = _mean_ci(recorded)
        gross_profit = sum(value for value in recorded if value > 0)
        gross_loss = abs(sum(value for value in recorded if value < 0))
        profit_factor = (
            round(gross_profit / gross_loss, 4) if gross_loss > 0 else None
        )
        friction_totals = {
            scenario["name"]: round(
                sum(
                    float(row["friction_pnl_dollars"][scenario["name"]])
                    for row in closed
                    if row["friction_pnl_dollars"].get(scenario["name"]) is not None
                ),
                2,
            )
            for scenario in FRICTION_SCENARIOS
        }
        quality_counts = {
            grade: sum(1 for row in rows if row.get("evidence_quality") == grade)
            for grade in ("HIGH", "MEDIUM", "LOW")
        }
        # Counterfactual/observation rows are NOT a trade population.  They never
        # reserved risk and were never eligible to be taken, so scoring them as
        # trades manufactures an edge out of observations.  Their observational
        # value (MAE/MFE, entry extension, evidence quality) is kept intact; every
        # trade-outcome metric is suppressed rather than computed.
        is_trade_population = lane != COUNTERFACTUAL_LANE
        summaries.append(
            {
                "paper_evidence_lane": lane,
                "setup_type": setup_type,
                "timeframe": timeframe,
                "is_trade_population": is_trade_population,
                "trade_metrics_suppressed_reason": (
                    None if is_trade_population else "COUNTERFACTUAL_NOT_A_TRADE_POPULATION"
                ),
                "n_observations": len(rows),
                "n_total": len(rows) if is_trade_population else None,
                "n_closed_priced": len(recorded) if is_trade_population else None,
                "sample_status": (
                    sample_status(len(recorded)) if is_trade_population else "NOT_A_TRADE_POPULATION"
                ),
                "wins": wins if is_trade_population else None,
                "losses": losses if is_trade_population else None,
                "win_rate_percent": (
                    round((wins / len(recorded)) * 100.0, 2)
                    if is_trade_population and recorded
                    else None
                ),
                "win_rate_95ci_percent": [win_low, win_high] if is_trade_population else None,
                "expectancy_dollars_per_trade": (
                    round(statistics.mean(recorded), 2)
                    if is_trade_population and recorded
                    else None
                ),
                "expectancy_95ci_dollars": [mean_low, mean_high] if is_trade_population else None,
                "profit_factor": profit_factor if is_trade_population else None,
                "trade_sequence_max_drawdown_dollars": (
                    _max_trade_sequence_drawdown(recorded) if is_trade_population else None
                ),
                "friction_total_pnl_dollars": friction_totals if is_trade_population else None,
                "quality_counts": quality_counts,
                "median_option_mae_percent": _median_present(rows, "option_mae_percent"),
                "median_option_mfe_percent": _median_present(rows, "option_mfe_percent"),
                "median_underlying_mae_percent": _median_present(rows, "underlying_mae_percent"),
                "median_underlying_mfe_percent": _median_present(rows, "underlying_mfe_percent"),
                "median_entry_extension_percent": _median_present(
                    rows, "directional_entry_extension_percent"
                ),
            }
        )
    return summaries


def _median_present(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_num(row.get(key)) for row in rows]
    present = [value for value in values if value is not None]
    return round(statistics.median(present), 4) if present else None


def build_diagnostics_report(storage: ScanStorage) -> dict[str, Any]:
    rows = _active_or_counterfactual_rows(storage)
    trades = [trade_diagnostic(storage, row) for row in rows]
    return {
        "policy_id": POLICY_ID,
        "read_only_analysis": True,
        "friction_scenarios": list(FRICTION_SCENARIOS),
        "sample_labels_are_promotion_rules": False,
        "quality_thresholds": {
            "expected_mark_interval_minutes": EXPECTED_MARK_INTERVAL_MINUTES,
            "max_acceptable_mark_gap_minutes": MAX_ACCEPTABLE_MARK_GAP_MINUTES,
            "max_entry_quote_age_seconds": MAX_ENTRY_QUOTE_AGE_SECONDS,
            "min_resolved_marks": MIN_RESOLVED_MARKS,
        },
        "trades": trades,
        "strategy_summary": strategy_summary(trades),
    }


def _entry_capture_fields(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "underlying_price": raw.get("price") or raw.get("underlying_price") or raw.get("spot"),
        "setup_entry_trigger": raw.get("setup_entry_trigger") or raw.get("entry_trigger"),
        "option_bid": raw.get("option_bid"),
        "option_ask": raw.get("option_ask"),
        "option_mid": raw.get("option_mid") or raw.get("mid"),
        "quote_timestamp": raw.get("option_quote_timestamp"),
        "delta": raw.get("delta"),
        "gamma": raw.get("gamma"),
        "theta": raw.get("theta"),
        "implied_volatility": raw.get("implied_volatility"),
        "setup_type": raw.get("setup_type"),
        "setup_timeframe": raw.get("setup_timeframe") or raw.get("timeframe"),
        "paper_evidence_lane": raw.get("paper_evidence_lane") or ACTIVE_LANE,
    }


def build_v1_diagnostics_capture(base_cls):
    """Wrap an OptionsScanner class with append-only diagnostic capture."""

    class V1DiagnosticsCaptureScanner(base_cls):
        async def _process_normalized_candidate(self, ticker, normalized, *, source, now):
            outcome = await super()._process_normalized_candidate(
                ticker, normalized, source=source, now=now
            )
            if not outcome.shadow_id:
                return outcome
            raw = outcome.result.raw or {}
            if raw.get("paper_policy_id") != POLICY_ID:
                return outcome
            fields = _entry_capture_fields(raw)
            record_diagnostic_snapshot(
                self.storage,
                shadow_id=outcome.shadow_id,
                timestamp=now,
                event="ENTRY",
                raw={
                    "source": source,
                    "ticker": ticker,
                    "paper_policy_id": POLICY_ID,
                    "entry_mark_basis": raw.get("entry_mark_basis") or "ASK",
                },
                **fields,
            )
            return outcome

        async def _resolve_v1_candidate(self, setup, underlying_price, now, chain_cache):
            resolution = await super()._resolve_v1_candidate(
                setup, underlying_price, now, chain_cache
            )
            marks = contract_marks(self.storage, setup.id, limit=5000)
            latest = marks[-1] if marks else {}
            selected = setup.selected_contract or {}
            setup_inputs = setup.setup_inputs or {}
            lane = _lane(selected, setup_inputs)
            record_diagnostic_snapshot(
                self.storage,
                shadow_id=setup.id,
                timestamp=now,
                event="RESOLUTION" if resolution is not None else "MARK",
                underlying_price=underlying_price,
                setup_entry_trigger=(
                    setup_inputs.get("setup_entry_trigger")
                    or setup_inputs.get("entry_trigger")
                ),
                option_bid=latest.get("bid"),
                option_ask=latest.get("ask"),
                option_mid=latest.get("mid"),
                quote_timestamp=latest.get("quote_timestamp"),
                delta=latest.get("delta"),
                gamma=latest.get("gamma"),
                theta=latest.get("theta"),
                implied_volatility=latest.get("implied_volatility"),
                error=str(latest.get("error") or ""),
                setup_type=selected.get("setup_type") or setup_inputs.get("setup_type"),
                setup_timeframe=(
                    selected.get("setup_timeframe")
                    or setup_inputs.get("setup_timeframe")
                    or setup_inputs.get("timeframe")
                ),
                paper_evidence_lane=lane,
                raw={
                    "paper_policy_id": POLICY_ID,
                    "contract_mark_id": latest.get("id"),
                    "resolution_returned": resolution is not None,
                    "resolution_status": resolution[0] if resolution is not None else None,
                },
            )
            return resolution

    V1DiagnosticsCaptureScanner.__name__ = base_cls.__name__
    V1DiagnosticsCaptureScanner.__qualname__ = base_cls.__qualname__
    return V1DiagnosticsCaptureScanner


def main() -> int:
    parser = argparse.ArgumentParser(description="Options Paper V1 diagnostics report")
    parser.add_argument("sqlite_path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_diagnostics_report(ScanStorage(args.sqlite_path))
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    raise SystemExit(main())
