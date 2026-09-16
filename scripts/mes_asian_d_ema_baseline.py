#!/usr/bin/env python3
"""Offline MES D+EMA canonical-IOC baseline producer.

Research-only portability adapter for the proven MNQ v3/v4 D+EMA population.
It reuses #593's preserved cohort construction / dedupe helpers, but terminal
fills are resolved through the real repo PaperBroker exactly as required by the
v3/v4 source artifacts:

- cohort D = neither Pine TRENDING nor structural-trend classification
- candidate direction aligned with existing EMA/trend direction
- original shadow-candidate geometry unchanged
- PaperBroker(entry_fill_model='ioc_limit')
- decision-bar CLOSE is the IOC arrival market price
- MES tolerance = 16 ticks = 4.0 points
- one adverse tick entry/stop slippage
- static exit, no breakeven, no runner
- entry/decision bar is never reused for bracket resolution
- pessimistic stop-before-target on ambiguous later bars

The full output contains every MES D+EMA candidate across Asian/London/New York.
A separate terminal precursor output is filtered to --precursor-session (Asian
by default) for PR #596.

No external broker, network client, webhook runner, service, env mutation,
deployment, or VPS path is used.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from config.futures_contracts import (  # noqa: E402
    contract_economics,
    contract_root,
    point_value,
)
from execution.broker_interface import BracketOrder  # noqa: E402
from execution.paper_broker import NextBarOHLC, PaperBroker  # noqa: E402
from scripts import mnq_missed_opportunity_producer as core  # noqa: E402

INSTRUMENT = "MES"
TIMEFRAME_MINUTES = core.TIMEFRAME_MINUTES
IOC_TOLERANCE_TICKS = 16.0
IOC_TOLERANCE_POINTS = 4.0
SLIPPAGE_TICKS = 1.0
PRODUCER_VERSION = "2.0.0"
TERMINAL_RESULTS = frozenset({"WIN", "LOSS"})
NO_FILL = "NO_FILL"
EXPIRED = "EXPIRED"
SOURCE_VARIANT = "D0_D_EMA_CANONICAL_IOC"
PARENT_HEAD = "22a96b34fdfd4470014d708ba84d4588f9c74cdf"
ALLOWED_SESSIONS = frozenset({"asian", "london", "new_york"})

TICK, TICK_VALUE = contract_economics(INSTRUMENT)
POINT_VALUE = point_value(INSTRUMENT)
if (TICK, TICK_VALUE, POINT_VALUE) != (0.25, 1.25, 5.0):
    raise RuntimeError(
        "MES contract economics drifted from the proven portability contract: "
        f"tick={TICK} tick_value={TICK_VALUE} point_value={POINT_VALUE}"
    )
if not math.isclose(IOC_TOLERANCE_TICKS * TICK, IOC_TOLERANCE_POINTS):
    raise RuntimeError("MES IOC tolerance ticks/points mismatch")


@dataclass(frozen=True)
class StudyInputs:
    bars: dict[str, dict[str, Any]]
    bar_timestamps: tuple[str, ...]
    journal_rows: tuple[dict[str, Any], ...]
    bar_files: tuple[Path, ...]
    journal_files: tuple[Path, ...]
    journal_parse_skips: int


def _bar_files(data_dir: Path) -> tuple[Path, ...]:
    """Accept archived-study and canonical polygon_to_replay MES file shapes."""
    paths: set[Path] = set(data_dir.glob("bars_MES_*.jsonl"))
    paths.update(data_dir.glob("MES_*.jsonl"))
    return tuple(sorted(paths))


def _normalized_bar(path: Path, row: dict[str, Any]) -> dict[str, Any] | None:
    if not core._timeframe_is_15(row.get("timeframe")):
        return None
    row_instrument = row.get("instrument")
    if row_instrument not in (None, ""):
        root = contract_root(row_instrument)
        if root != INSTRUMENT:
            raise core.StudyError(
                f"{path}: MES bar archive contains instrument {row_instrument!r}"
            )
    ts = str(row.get("ts") or row.get("timestamp") or "").strip()
    if not ts:
        raise core.StudyError(f"{path}: 15m bar missing ts/timestamp")
    normalized = dict(row)
    normalized["ts"] = ts
    return normalized


def discover_inputs(
    data_dir: Path,
    *,
    start_date: date,
    end_date: date,
    allow_journal_parse_skips: bool = False,
) -> StudyInputs:
    if end_date < start_date:
        raise core.StudyError("end date precedes start date")

    bar_files = _bar_files(data_dir)
    all_journals = tuple(sorted(data_dir.glob("journal_*.jsonl")))
    journal_files: list[Path] = []
    for path in all_journals:
        try:
            file_date = core._journal_file_date(path)
        except core.StudyError:
            continue
        if start_date <= file_date <= end_date:
            journal_files.append(path)

    if not bar_files:
        raise core.StudyError(
            f"no MES 15m replay files found in {data_dir}; expected "
            "bars_MES_*.jsonl or MES_*.jsonl"
        )
    if not journal_files:
        raise core.StudyError(
            f"no journal files found in {data_dir} for "
            f"{start_date.isoformat()}..{end_date.isoformat()}"
        )

    bars: dict[str, dict[str, Any]] = {}
    for path in bar_files:
        file_rows, _ = core._json_lines(path, skip_invalid=False)
        for row in file_rows:
            normalized = _normalized_bar(path, row)
            if normalized is None:
                continue
            ts = normalized["ts"]
            existing = bars.get(ts)
            if existing is not None and existing != normalized:
                raise core.StudyError(f"conflicting duplicate 15m bar timestamp {ts}")
            bars[ts] = normalized
    if not bars:
        raise core.StudyError("no MES 15m bars found")

    journals: list[dict[str, Any]] = []
    skipped = 0
    seen_journal_identity: dict[tuple[Any, ...], dict[str, Any]] = {}
    for path in journal_files:
        file_rows, file_skips = core._json_lines(
            path, skip_invalid=allow_journal_parse_skips
        )
        skipped += file_skips
        for row in file_rows:
            if row.get("instrument") != INSTRUMENT:
                continue
            if row.get("decision") not in {"NO_TRADE", "TRADE", "RISK_REJECTED"}:
                continue
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            if not core._timeframe_is_15(context.get("timeframe")):
                continue
            identity = (
                context.get("timestamp"),
                row.get("decision"),
                row.get("instrument"),
            )
            existing = seen_journal_identity.get(identity)
            if existing is not None and existing != row:
                raise core.StudyError(f"conflicting duplicate MES journal row {identity}")
            if existing is None:
                seen_journal_identity[identity] = row
                journals.append(row)
    if not journals:
        raise core.StudyError("no qualifying MES 15m journal decision rows found")

    return StudyInputs(
        bars=bars,
        bar_timestamps=tuple(sorted(bars)),
        journal_rows=tuple(journals),
        bar_files=bar_files,
        journal_files=tuple(journal_files),
        journal_parse_skips=skipped,
    )


def build_records(inputs: StudyInputs) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Reuse #593 representation/cohort semantics with the MES root only."""
    records: list[dict[str, Any]] = []
    seen_candidates: set[tuple[Any, ...]] = set()
    skipped_missing_bar = 0
    regime_errors = 0

    for decision in inputs.journal_rows:
        context = decision.get("context") if isinstance(decision.get("context"), dict) else {}
        ts = str(context.get("timestamp") or "").strip()
        if ts not in inputs.bars:
            skipped_missing_bar += 1
            continue

        pine = context.get("market_condition")
        structural = str(context.get("structural_market_condition") or "")
        structural_direction = context.get("structural_direction")
        structural_trend = structural in {
            "STRUCTURAL_TREND_UP",
            "STRUCTURAL_TREND_DOWN",
        }
        bar_cohort = (
            "C"
            if pine == "TRENDING" and structural_trend
            else "A"
            if structural_trend
            else "B"
            if pine == "TRENDING"
            else "D"
        )
        trend = context.get("trend") if isinstance(context.get("trend"), dict) else {}
        regime_pine = (
            decision.get("regime") or core.regime_for(context, pine)
            if pine == "TRENDING"
            else "NOT_EVALUATED(label)"
        )
        regime_struct = core.regime_for(context, "TRENDING") if structural_trend else "n/a"
        regime_nolabel = core.regime_for(context, "TRENDING")
        for value in (regime_pine, regime_struct, regime_nolabel):
            if str(value).startswith("ERR:"):
                regime_errors += 1

        candidates: list[dict[str, Any]] = []
        shadow = decision.get("shadow_candidates")
        if isinstance(shadow, list):
            for candidate in shadow:
                if not isinstance(candidate, dict):
                    continue
                identity = (
                    core.observation_day(INSTRUMENT, ts).isoformat(),
                    candidate.get("strategy"),
                    candidate.get("direction"),
                    candidate.get("entry"),
                    candidate.get("stop"),
                    candidate.get("target"),
                )
                if identity in seen_candidates:
                    continue
                seen_candidates.add(identity)
                candidates.append(candidate)

        records.append(
            {
                "ts": ts,
                "session": str(context.get("session") or "").lower(),
                "pine": pine,
                "struct": structural,
                "sdir": structural_direction,
                "bar_cohort": bar_cohort,
                "ema_dir": trend.get("direction"),
                "ema_str": trend.get("strength"),
                "regime_pine": regime_pine,
                "regime_struct": regime_struct,
                "regime_nolabel": regime_nolabel,
                "reason": str(decision.get("reason") or "")[:80],
                "candidates": candidates,
            }
        )

    if regime_errors:
        raise core.StudyError(f"regime classifier produced {regime_errors} error result(s)")
    return records, {
        "skipped_decisions_missing_matching_15m_bar": skipped_missing_bar,
        "distinct_shadow_candidates": sum(len(record["candidates"]) for record in records),
    }


def _forward_bars(
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
    signal_ts: str,
) -> list[dict[str, Any]]:
    """Strictly after decision bar; stop at MES observation-day rollover."""
    signal_day = core.observation_day(INSTRUMENT, signal_ts).isoformat()
    out: list[dict[str, Any]] = []
    for ts in bar_timestamps:
        if ts <= signal_ts:
            continue
        if core.observation_day(INSTRUMENT, ts).isoformat() != signal_day:
            break
        out.append(bars[ts])
    return out


def _order(candidate: dict[str, Any]) -> BracketOrder:
    try:
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
        direction = str(candidate["direction"])
    except (KeyError, TypeError, ValueError) as exc:
        raise core.StudyError(f"invalid candidate geometry: {candidate}") from exc
    if direction not in {"LONG", "SHORT"}:
        raise core.StudyError(f"invalid candidate direction {direction!r}")
    planned_risk = abs(entry - stop)
    if planned_risk <= 0:
        raise core.StudyError("candidate has non-positive planned risk")
    rr = abs(target - entry) / planned_risk
    return BracketOrder(
        instrument=INSTRUMENT,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=rr,
        strategy=str(candidate.get("strategy") or "unknown_shadow"),
        contracts=1,
        min_rr_ratio=0.0,
        post_fill_validation_required=False,
    )


def _new_broker() -> PaperBroker:
    return PaperBroker(
        starting_balance=100_000.0,
        slippage_ticks=SLIPPAGE_TICKS,
        pessimistic_both_hit=True,
        breakeven_at_1r=False,
        runner_mode=False,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={INSTRUMENT: IOC_TOLERANCE_TICKS},
        entry_tolerance_ticks_default=0.0,
    )


def resolve_canonical_ioc(
    candidate: dict[str, Any],
    signal_ts: str,
    *,
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
) -> dict[str, Any]:
    """Resolve one candidate with the canonical v3/v4 real PaperBroker IOC."""
    decision_bar = bars.get(signal_ts)
    if decision_bar is None:
        raise core.StudyError(f"missing decision bar for {signal_ts}")
    try:
        market_price = float(decision_bar["close"])
    except (KeyError, TypeError, ValueError) as exc:
        raise core.StudyError(f"decision bar missing numeric close at {signal_ts}") from exc

    order = _order(candidate)
    broker = _new_broker()
    entry_fill = broker.execute_bracket(order, market_price=market_price)
    if entry_fill.result == "CANCELLED":
        return {
            "result": NO_FILL,
            "exit_reason": entry_fill.exit_reason or "ENTRY_NOT_FILLED",
            "bars_seen": 0,
            "entry_price": entry_fill.entry_price,
            "exit_price": None,
            "exit_ts": None,
            "pnl_r": None,
            "pnl_dollars": None,
            "mae_r": None,
            "mfe_r": None,
            "decision_close": market_price,
        }
    if entry_fill.result != "OPEN":
        raise core.StudyError(
            f"canonical PaperBroker returned unexpected entry result {entry_fill.result!r}"
        )

    actual_entry = float(entry_fill.entry_price)
    if order.direction == "LONG":
        baseline_risk = actual_entry - order.stop
    else:
        baseline_risk = order.stop - actual_entry
    if baseline_risk <= 0:
        raise core.StudyError(
            f"PaperBroker opened bracket with non-positive post-fill risk at {signal_ts}"
        )

    max_adverse = 0.0
    max_favorable = 0.0
    forward = _forward_bars(bars, bar_timestamps, signal_ts)
    for bars_seen, bar in enumerate(forward, start=1):
        try:
            open_px = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, TypeError, ValueError) as exc:
            raise core.StudyError(f"invalid forward bar after {signal_ts}: {bar}") from exc
        if order.direction == "LONG":
            max_adverse = max(max_adverse, actual_entry - low)
            max_favorable = max(max_favorable, high - actual_entry)
        else:
            max_adverse = max(max_adverse, high - actual_entry)
            max_favorable = max(max_favorable, actual_entry - low)

        resolved = broker.resolve_position(NextBarOHLC(open=open_px, high=high, low=low))
        if resolved is None:
            continue
        if resolved.result not in TERMINAL_RESULTS:
            raise core.StudyError(
                f"canonical PaperBroker returned unexpected terminal result {resolved.result!r}"
            )
        exit_price = float(resolved.exit_price)
        points = (
            exit_price - actual_entry
            if order.direction == "LONG"
            else actual_entry - exit_price
        )
        return {
            "result": resolved.result,
            "exit_reason": resolved.exit_reason,
            "bars_seen": bars_seen,
            "entry_price": actual_entry,
            "exit_price": exit_price,
            "exit_ts": str(bar["ts"]),
            "pnl_r": points / baseline_risk,
            "pnl_dollars": float(resolved.pnl_dollars),
            "mae_r": max_adverse / baseline_risk,
            "mfe_r": max_favorable / baseline_risk,
            "decision_close": market_price,
        }

    return {
        "result": EXPIRED,
        "exit_reason": "OBSERVATION_DATE_ROLLED",
        "bars_seen": len(forward),
        "entry_price": actual_entry,
        "exit_price": None,
        "exit_ts": str(forward[-1]["ts"]) if forward else signal_ts,
        "pnl_r": None,
        "pnl_dollars": None,
        "mae_r": max_adverse / baseline_risk,
        "mfe_r": max_favorable / baseline_risk,
        "decision_close": market_price,
    }


def _d0_predicate() -> Any:
    matches = [predicate for name, predicate in core._variants() if name == "D0"]
    if len(matches) != 1:
        raise core.StudyError("#593 D0 predicate missing or ambiguous")
    return matches[0]


def _candidate_id(record: dict[str, Any], candidate: dict[str, Any]) -> str:
    identity = [
        INSTRUMENT,
        core.observation_day(INSTRUMENT, record["ts"]).isoformat(),
        candidate.get("strategy"),
        candidate.get("direction"),
        candidate.get("entry"),
        candidate.get("stop"),
        candidate.get("target"),
    ]
    raw = json.dumps(identity, separators=(",", ":"), default=str)
    return f"MES-D-EMA-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def _session_summary(rows: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    buckets: dict[str, Counter] = defaultdict(Counter)
    for row in rows:
        session = str(row["session"])
        result = str(row["result"])
        buckets[session]["candidates"] += 1
        buckets[session][result] += 1
        if result in TERMINAL_RESULTS:
            buckets[session]["terminal"] += 1
    return {session: dict(counter) for session, counter in sorted(buckets.items())}


def produce_baseline(
    records: Iterable[dict[str, Any]],
    *,
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
    precursor_session: str = "asian",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Produce full MES D+EMA, plus terminal rows for one precursor session."""
    precursor_session = str(precursor_session).lower()
    if precursor_session not in ALLOWED_SESSIONS:
        raise core.StudyError(f"unsupported precursor session {precursor_session!r}")
    predicate = _d0_predicate()
    picks = [
        (record, candidate)
        for record in records
        if record.get("session") in ALLOWED_SESSIONS
        for candidate in record["candidates"]
        if predicate(record, candidate)
    ]
    if not picks:
        raise core.StudyError("no MES D+EMA candidates selected")

    full_rows: list[dict[str, Any]] = []
    precursor_rows: list[dict[str, Any]] = []
    counts = Counter()
    seen_ids: set[str] = set()

    for sequence, (record, candidate) in enumerate(picks):
        outcome = resolve_canonical_ioc(
            candidate,
            record["ts"],
            bars=bars,
            bar_timestamps=bar_timestamps,
        )
        result = str(outcome.get("result") or "")
        if result not in TERMINAL_RESULTS | {NO_FILL, EXPIRED}:
            raise core.StudyError(f"unsupported outcome result {result!r}")
        counts[result] += 1

        cid = _candidate_id(record, candidate)
        if cid in seen_ids:
            raise core.StudyError(f"duplicate canonical candidate id {cid}")
        seen_ids.add(cid)

        try:
            planned_entry = float(candidate["entry"])
            stop = float(candidate["stop"])
            target = float(candidate["target"])
        except (KeyError, TypeError, ValueError) as exc:
            raise core.StudyError(f"invalid selected candidate geometry: {candidate}") from exc
        planned_risk = abs(planned_entry - stop)
        if planned_risk <= 0:
            raise core.StudyError(f"non-positive planned risk for {cid}")

        terminal = result in TERMINAL_RESULTS
        entry_filled = terminal or result == EXPIRED
        actual_entry = float(outcome["entry_price"]) if entry_filled else None
        if entry_filled:
            post_fill_risk = (
                actual_entry - stop
                if candidate.get("direction") == "LONG"
                else stop - actual_entry
            )
            if post_fill_risk <= 0:
                raise core.StudyError(f"non-positive post-fill risk for {cid}")
            baseline_stop_ticks = post_fill_risk / TICK
            target_r = abs(target - actual_entry) / post_fill_risk
        else:
            baseline_stop_ticks = None
            target_r = None

        full = {
            "schema": "mes_d_ema_baseline_v2",
            "candidate_id": cid,
            "sequence": sequence,
            "instrument": INSTRUMENT,
            "timeframe_minutes": TIMEFRAME_MINUTES,
            "session": record["session"],
            "signal_ts": record["ts"],
            "observation_day": core.observation_day(INSTRUMENT, record["ts"]).isoformat(),
            "strategy": candidate.get("strategy"),
            "direction": candidate.get("direction"),
            "planned_entry": planned_entry,
            "stop": stop,
            "target": target,
            "decision_close": outcome.get("decision_close"),
            "entry_price": actual_entry if entry_filled else outcome.get("entry_price"),
            "baseline_stop_ticks": baseline_stop_ticks,
            "target_r": target_r,
            "result": result,
            "outcome_label": result if terminal else None,
            "entry_filled": entry_filled,
            "filled": terminal,
            "baseline_pnl_dollars": (
                float(outcome["pnl_dollars"]) if terminal else None
            ),
            "pnl_r": (
                float(outcome["pnl_r"])
                if terminal and outcome.get("pnl_r") is not None
                else None
            ),
            "mae_r": (
                float(outcome["mae_r"])
                if entry_filled and outcome.get("mae_r") is not None
                else None
            ),
            "mfe_r": (
                float(outcome["mfe_r"])
                if entry_filled and outcome.get("mfe_r") is not None
                else None
            ),
            "exit_reason": outcome.get("exit_reason"),
            "exit_price": outcome.get("exit_price"),
            "exit_ts": outcome.get("exit_ts"),
            "bars_seen": outcome.get("bars_seen"),
            "source_variant": SOURCE_VARIANT,
            "pine_market_condition": record.get("pine"),
            "structural_market_condition": record.get("struct"),
            "structural_direction": record.get("sdir"),
            "ema_direction": record.get("ema_dir"),
            "ema_strength": record.get("ema_str"),
            "regime_pine": record.get("regime_pine"),
            "regime_struct": record.get("regime_struct"),
            "tick_size": TICK,
            "tick_value": TICK_VALUE,
            "point_value": POINT_VALUE,
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "ioc_tolerance_points": IOC_TOLERANCE_POINTS,
            "slippage_assumption_ticks": SLIPPAGE_TICKS,
            "commission_assumption_dollars": None,
            "producer": "scripts/mes_asian_d_ema_baseline.py",
            "producer_version": PRODUCER_VERSION,
            "source_logic": "v3/v4 D+EMA population + real repo PaperBroker canonical IOC",
        }
        for key in (
            "baseline_pnl_dollars",
            "pnl_r",
            "mae_r",
            "mfe_r",
            "baseline_stop_ticks",
            "target_r",
        ):
            value = full.get(key)
            if value is not None and not math.isfinite(float(value)):
                raise core.StudyError(f"non-finite {key} for {cid}")
        full_rows.append(full)

        if terminal and record["session"] == precursor_session:
            precursor_rows.append(
                {
                    "candidate_id": cid,
                    "instrument": INSTRUMENT,
                    "strategy": candidate.get("strategy"),
                    "session": record["session"],
                    "direction": candidate.get("direction"),
                    "signal_ts": record["ts"],
                    "outcome_label": result,
                    "baseline_pnl_dollars": float(outcome["pnl_dollars"]),
                    "baseline_stop_ticks": baseline_stop_ticks,
                    "target_r": target_r,
                    "source_variant": SOURCE_VARIANT,
                }
            )

    summary = {
        "selected_candidates": len(full_rows),
        "terminal": counts["WIN"] + counts["LOSS"],
        "wins": counts["WIN"],
        "losses": counts["LOSS"],
        "no_fill": counts[NO_FILL],
        "expired_open": counts[EXPIRED],
        "entry_filled_total": counts["WIN"] + counts["LOSS"] + counts[EXPIRED],
        "precursor_session": precursor_session,
        "precursor_terminal_rows": len(precursor_rows),
        "by_session": _session_summary(full_rows),
    }
    if summary["selected_candidates"] != (
        summary["terminal"] + summary["no_fill"] + summary["expired_open"]
    ):
        raise core.StudyError("baseline population does not reconcile")
    return full_rows, precursor_rows, summary


def _build_manifest(
    inputs: StudyInputs,
    *,
    start_date: date,
    end_date: date,
    record_summary: dict[str, int],
    population_summary: dict[str, Any],
    full_out: Path,
    full_sha: str,
    precursor_out: Path,
    precursor_sha: str,
) -> dict[str, Any]:
    return {
        "study": "MES D+EMA canonical baseline; terminal session slice for precursor audit",
        "producer": "scripts/mes_asian_d_ema_baseline.py",
        "producer_version": PRODUCER_VERSION,
        "source_logic": {
            "population_parent": "MNQ v3/v4 D+EMA source artifacts",
            "representation_helper_parent_pr": 593,
            "representation_helper_parent_head": PARENT_HEAD,
            "predicate": "cohort D and candidate direction == EMA direction",
            "candidate_geometry": "unchanged source shadow candidate",
            "dedupe": "observation_day x strategy x direction x entry x stop x target",
        },
        "instrument": INSTRUMENT,
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "journal_date_range": [start_date.isoformat(), end_date.isoformat()],
        "economics": {
            "tick_size": TICK,
            "tick_value": TICK_VALUE,
            "point_value": POINT_VALUE,
        },
        "fill_assumptions": {
            "broker": "execution.paper_broker.PaperBroker",
            "entry_fill_model": "ioc_limit",
            "market_price": "decision_bar_close",
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "ioc_tolerance_points": IOC_TOLERANCE_POINTS,
            "slippage_ticks_entry": SLIPPAGE_TICKS,
            "slippage_ticks_stop": SLIPPAGE_TICKS,
            "target_fill": "clean",
            "entry_bar_reused_for_exit": False,
            "pessimistic_both_hit": True,
            "breakeven_at_1r": False,
            "runner_mode": False,
            "expired_policy": "count separately and exclude from terminal PnL",
            "commission_assumption_dollars": None,
        },
        "accepted_bar_input_shapes": [
            "bars_MES_*.jsonl with ts",
            "polygon_to_replay MES_*.jsonl with timestamp",
        ],
        "journal_parse_skips": inputs.journal_parse_skips,
        "record_summary": record_summary,
        "population_summary": population_summary,
        "inputs": {
            "bars": [
                {"path": str(path), "sha256": core._sha256(path)}
                for path in inputs.bar_files
            ],
            "journals": [
                {"path": str(path), "sha256": core._sha256(path)}
                for path in inputs.journal_files
            ],
        },
        "outputs": {
            "baseline": {"path": str(full_out), "sha256": full_sha},
            "precursor_terminal_cohort": {
                "path": str(precursor_out),
                "sha256": precursor_sha,
                "session": population_summary["precursor_session"],
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        required=True,
        help=(
            "Proven snapshot containing MES 15m replay candles "
            "(MES_*.jsonl or bars_MES_*.jsonl) and journal_*.jsonl"
        ),
    )
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Full MES D+EMA candidate rows")
    parser.add_argument(
        "--precursor-out",
        required=True,
        help="Terminal WIN/LOSS rows for one session, normalized for PR #596",
    )
    parser.add_argument(
        "--precursor-session",
        choices=sorted(ALLOWED_SESSIONS),
        default="asian",
    )
    parser.add_argument("--manifest-out", required=True)
    parser.add_argument(
        "--allow-journal-parse-skips",
        action="store_true",
        help="Explicit compatibility mode only; default fails closed",
    )
    args = parser.parse_args(argv)

    try:
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date)
        inputs = discover_inputs(
            Path(args.data_dir),
            start_date=start_date,
            end_date=end_date,
            allow_journal_parse_skips=args.allow_journal_parse_skips,
        )
        records, record_summary = build_records(inputs)
        full_rows, precursor_rows, population_summary = produce_baseline(
            records,
            bars=inputs.bars,
            bar_timestamps=inputs.bar_timestamps,
            precursor_session=args.precursor_session,
        )
        full_out = Path(args.out)
        precursor_out = Path(args.precursor_out)
        full_sha = core.write_jsonl(full_out, full_rows)
        precursor_sha = core.write_jsonl(precursor_out, precursor_rows)
        manifest = _build_manifest(
            inputs,
            start_date=start_date,
            end_date=end_date,
            record_summary=record_summary,
            population_summary=population_summary,
            full_out=full_out,
            full_sha=full_sha,
            precursor_out=precursor_out,
            precursor_sha=precursor_sha,
        )
        manifest_out = Path(args.manifest_out)
        manifest_out.parent.mkdir(parents=True, exist_ok=True)
        manifest_out.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, core.StudyError, ValueError) as exc:
        parser.error(str(exc))

    print(
        json.dumps(
            {
                "instrument": INSTRUMENT,
                "selected_candidates": len(full_rows),
                "precursor_session": args.precursor_session,
                "terminal_precursor_rows": len(precursor_rows),
                "baseline_sha256": full_sha,
                "precursor_sha256": precursor_sha,
                "manifest": str(manifest_out),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
