#!/usr/bin/env python3
"""Offline MES Asian D+EMA baseline producer for the precursor audit.

This is a narrow portability adapter around the proven #593 MNQ missed-opportunity
producer. It does not change the MNQ producer and does not create a new strategy.
It reuses the same journal/shadow-candidate population, bar-cohort definition,
EMA-direction alignment, candidate dedupe identity, observation-day semantics,
and pessimistic resolver, but pins the contract to MES economics.

Canonical population emitted here:
- instrument: MES
- timeframe: 15m decision rows
- session: asian only
- bar cohort: D = Pine market_condition is not TRENDING AND repo structural
  condition is not STRUCTURAL_TREND_UP/DOWN
- candidate direction must match the existing trend/EMA direction
- candidate geometry is copied unchanged from the source shadow candidate

Execution simulation is research-only and mirrors the preserved producer:
- IOC tolerance: 16 MES ticks = 4.0 points
- one tick adverse entry slippage
- one tick adverse stop slippage
- clean target
- pessimistic stop on the fill bar
- filled-but-unresolved positions are EXPIRED and excluded from terminal P&L
- no commission is invented

Outputs:
- --out: every selected D+EMA candidate, including NO_FILL / EXPIRED
- --precursor-out: terminal WIN/LOSS rows normalized for PR #596
- --manifest-out: hashes, assumptions, counts, and input provenance

No broker, webhook runner, service, environment, deployment, or VPS path is used.
"""
from __future__ import annotations

import argparse
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

from config.futures_contracts import contract_economics, point_value  # noqa: E402
from scripts import mnq_missed_opportunity_producer as core  # noqa: E402

INSTRUMENT = "MES"
SESSION = "asian"
TIMEFRAME_MINUTES = core.TIMEFRAME_MINUTES
IOC_TOLERANCE_TICKS = 16.0
IOC_TOLERANCE_POINTS = 4.0
SLIPPAGE_TICKS = 1.0
PRODUCER_VERSION = "1.0.0"
TERMINAL_RESULTS = frozenset(core.TERMINAL_RESULTS)
NO_FILL = "NO_FILL"
EXPIRED = "EXPIRED"
SOURCE_VARIANT = "D0_D_EMA"

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


def discover_inputs(
    data_dir: Path,
    *,
    start_date: date,
    end_date: date,
    allow_journal_parse_skips: bool = False,
) -> StudyInputs:
    """MES-generalized copy of #593 input discovery; never substitutes MNQ files."""
    if end_date < start_date:
        raise core.StudyError("end date precedes start date")

    bar_files = tuple(sorted(data_dir.glob("bars_MES_*.jsonl")))
    all_journals = tuple(sorted(data_dir.glob("journal_*.jsonl")))
    dated_journals: list[Path] = []
    for path in all_journals:
        try:
            file_date = core._journal_file_date(path)
        except core.StudyError:
            continue
        if start_date <= file_date <= end_date:
            dated_journals.append(path)
    journal_files = tuple(dated_journals)

    if not bar_files:
        raise core.StudyError(f"no bars_MES_*.jsonl files found in {data_dir}")
    if not journal_files:
        raise core.StudyError(
            f"no journal files found in {data_dir} for "
            f"{start_date.isoformat()}..{end_date.isoformat()}"
        )

    bars: dict[str, dict[str, Any]] = {}
    for path in bar_files:
        file_rows, _ = core._json_lines(path, skip_invalid=False)
        for row in file_rows:
            if not core._timeframe_is_15(row.get("timeframe")):
                continue
            row_instrument = row.get("instrument")
            if row_instrument not in (None, "", INSTRUMENT):
                raise core.StudyError(
                    f"{path}: MES bar archive contains instrument {row_instrument!r}"
                )
            ts = str(row.get("ts") or "").strip()
            if not ts:
                raise core.StudyError(f"{path}: 15m bar missing ts")
            existing = bars.get(ts)
            if existing is not None and existing != row:
                raise core.StudyError(f"conflicting duplicate 15m bar timestamp {ts}")
            bars[ts] = row
    if not bars:
        raise core.StudyError("no MES 15m bars found")

    journals: list[dict[str, Any]] = []
    skipped = 0
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
            journals.append(row)
    if not journals:
        raise core.StudyError("no qualifying MES 15m journal decision rows found")
    if skipped and not allow_journal_parse_skips:
        raise core.StudyError("journal parse skips present without explicit permission")

    return StudyInputs(
        bars=bars,
        bar_timestamps=tuple(sorted(bars)),
        journal_rows=tuple(journals),
        bar_files=bar_files,
        journal_files=journal_files,
        journal_parse_skips=skipped,
    )


def build_records(inputs: StudyInputs) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Port #593 record construction by changing only the instrument root."""
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
        structural_trend = structural in {"STRUCTURAL_TREND_UP", "STRUCTURAL_TREND_DOWN"}
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
                "session": context.get("session"),
                "pine": pine,
                "struct": structural,
                "sdir": structural_direction,
                "bar_cohort": bar_cohort,
                "ema_dir": trend.get("direction"),
                "ema_str": trend.get("strength"),
                "regime_pine": regime_pine,
                "regime_struct": regime_struct,
                "regime_nolabel": regime_nolabel,
                "reason": str(decision.get("reason") or "")[:40],
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
    signal_day = core.observation_day(INSTRUMENT, signal_ts).isoformat()
    out: list[dict[str, Any]] = []
    for ts in bar_timestamps:
        if ts <= signal_ts:
            continue
        if core.observation_day(INSTRUMENT, ts).isoformat() != signal_day:
            break
        out.append(bars[ts])
    return out


def resolve_ioc(
    candidate: dict[str, Any],
    signal_ts: str,
    *,
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
) -> dict[str, Any]:
    """Exact #593 IOC geometry with MES units/tolerance substituted."""
    forward = _forward_bars(bars, bar_timestamps, signal_ts)
    try:
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
        direction = str(candidate["direction"])
    except (KeyError, TypeError, ValueError) as exc:
        raise core.StudyError(f"invalid candidate geometry at {signal_ts}: {candidate}") from exc
    if direction not in {"LONG", "SHORT"}:
        raise core.StudyError(f"invalid candidate direction {direction!r}")
    risk = abs(entry - stop)
    if risk <= 0:
        raise core.StudyError(f"non-positive candidate risk at {signal_ts}")

    slippage = SLIPPAGE_TICKS * TICK
    fill_price: float | None = None
    fill_index: int | None = None
    for index, bar in enumerate(forward):
        try:
            open_px = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, TypeError, ValueError) as exc:
            raise core.StudyError(f"invalid forward bar for {signal_ts}: {bar}") from exc
        if not (low <= entry <= high):
            continue
        if direction == "LONG":
            if open_px > entry + IOC_TOLERANCE_POINTS:
                return {
                    "result": NO_FILL,
                    "exit_reason": "IOC_GAP_BEYOND_TOLERANCE",
                    "bars_seen": index + 1,
                    "pnl_r": None,
                    "pnl_dollars": None,
                    "mae_r": None,
                    "mfe_r": None,
                }
            fill_price = max(entry, open_px) + slippage
        else:
            if open_px < entry - IOC_TOLERANCE_POINTS:
                return {
                    "result": NO_FILL,
                    "exit_reason": "IOC_GAP_BEYOND_TOLERANCE",
                    "bars_seen": index + 1,
                    "pnl_r": None,
                    "pnl_dollars": None,
                    "mae_r": None,
                    "mfe_r": None,
                }
            fill_price = min(entry, open_px) - slippage
        fill_index = index
        break

    if fill_price is None or fill_index is None:
        return {
            "result": NO_FILL,
            "exit_reason": "NEVER_TOUCHED",
            "bars_seen": len(forward),
            "pnl_r": None,
            "pnl_dollars": None,
            "mae_r": None,
            "mfe_r": None,
        }

    pending = {
        "record": {
            "direction": direction,
            "entry": fill_price,
            "stop": stop,
            "target": target,
            "instrument": INSTRUMENT,
        },
        "filled": True,
        "fill_ts": forward[fill_index]["ts"],
        "mae_points": 0.0,
        "mfe_points": 0.0,
        "bars_seen": 0,
    }

    fill_bar = forward[fill_index]
    fill_bar_stop = (
        float(fill_bar["low"]) <= stop
        if direction == "LONG"
        else float(fill_bar["high"]) >= stop
    )
    if fill_bar_stop:
        result = "LOSS"
        exit_reason = "STOP_HIT_ON_FILL_BAR"
        bars_seen = 1
        mae_r = None
        mfe_r = None
        exit_price = stop - slippage if direction == "LONG" else stop + slippage
    else:
        outcome = core._resolve_one(pending, forward[fill_index + 1 :])
        if outcome is None:
            return {
                "result": EXPIRED,
                "exit_reason": "OBSERVATION_DATE_ROLLED",
                "bars_seen": len(forward) - fill_index,
                "pnl_r": None,
                "pnl_dollars": None,
                "mae_r": pending["mae_points"] / risk,
                "mfe_r": pending["mfe_points"] / risk,
            }
        result = str(outcome["result"])
        exit_reason = str(outcome["exit_reason"])
        bars_seen = int(outcome["bars_seen"]) + 1
        mae_r = outcome.get("mae_r")
        mfe_r = outcome.get("mfe_r")
        exit_price = (
            stop - slippage
            if result == "LOSS" and direction == "LONG"
            else stop + slippage
            if result == "LOSS"
            else target
        )

    points = exit_price - fill_price if direction == "LONG" else fill_price - exit_price
    return {
        "result": result,
        "exit_reason": exit_reason,
        "bars_seen": bars_seen,
        "pnl_r": points / risk,
        "pnl_dollars": points / TICK * TICK_VALUE,
        "mae_r": mae_r,
        "mfe_r": mfe_r,
        "fill_price": fill_price,
        "exit_price": exit_price,
    }


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


def _d0_predicate() -> Any:
    matches = [predicate for name, predicate in core._variants() if name == "D0"]
    if len(matches) != 1:
        raise core.StudyError("#593 D0 predicate missing or ambiguous")
    return matches[0]


def produce_baseline(
    records: Iterable[dict[str, Any]],
    *,
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Select only Asian D+EMA and emit full + terminal precursor rows."""
    predicate = _d0_predicate()
    picks = [
        (record, candidate)
        for record in records
        if str(record.get("session") or "").lower() == SESSION
        for candidate in record["candidates"]
        if predicate(record, candidate)
    ]
    if not picks:
        raise core.StudyError("no MES Asian D+EMA candidates selected")

    full_rows: list[dict[str, Any]] = []
    precursor_rows: list[dict[str, Any]] = []
    counts = {"WIN": 0, "LOSS": 0, NO_FILL: 0, EXPIRED: 0}
    seen_ids: set[str] = set()

    for sequence, (record, candidate) in enumerate(picks):
        outcome = resolve_ioc(
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
            entry = float(candidate["entry"])
            stop = float(candidate["stop"])
            target = float(candidate["target"])
        except (KeyError, TypeError, ValueError) as exc:
            raise core.StudyError(f"invalid selected candidate geometry: {candidate}") from exc
        planned_risk = abs(entry - stop)
        if planned_risk <= 0:
            raise core.StudyError(f"non-positive planned risk for {cid}")

        terminal = result in TERMINAL_RESULTS
        entry_filled = terminal or result == EXPIRED
        obs_day = core.observation_day(INSTRUMENT, record["ts"]).isoformat()
        full = {
            "schema": "mes_asian_d_ema_baseline_v1",
            "candidate_id": cid,
            "sequence": sequence,
            "instrument": INSTRUMENT,
            "timeframe_minutes": TIMEFRAME_MINUTES,
            "session": SESSION,
            "signal_ts": record["ts"],
            "observation_day": obs_day,
            "strategy": candidate.get("strategy"),
            "direction": candidate.get("direction"),
            "entry": entry,
            "stop": stop,
            "target": target,
            "baseline_stop_ticks": planned_risk / TICK,
            "target_r": abs(target - entry) / planned_risk,
            "result": result,
            "outcome_label": result if terminal else None,
            "entry_filled": entry_filled,
            "filled": terminal,
            "baseline_pnl_dollars": float(outcome["pnl_dollars"]) if terminal else None,
            "pnl_r": float(outcome["pnl_r"]) if terminal and outcome.get("pnl_r") is not None else None,
            "mae_r": float(outcome["mae_r"]) if entry_filled and outcome.get("mae_r") is not None else None,
            "mfe_r": float(outcome["mfe_r"]) if entry_filled and outcome.get("mfe_r") is not None else None,
            "exit_reason": outcome.get("exit_reason"),
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
            "source_logic": "#593 D0 predicate + MES economics",
        }
        for key in ("baseline_pnl_dollars", "pnl_r", "mae_r", "mfe_r"):
            value = full.get(key)
            if value is not None and not math.isfinite(float(value)):
                raise core.StudyError(f"non-finite {key} for {cid}")
        full_rows.append(full)

        if terminal:
            precursor_rows.append(
                {
                    "candidate_id": cid,
                    "instrument": INSTRUMENT,
                    "strategy": candidate.get("strategy"),
                    "session": SESSION,
                    "direction": candidate.get("direction"),
                    "signal_ts": record["ts"],
                    "outcome_label": result,
                    "baseline_pnl_dollars": float(outcome["pnl_dollars"]),
                    "baseline_stop_ticks": planned_risk / TICK,
                    "target_r": abs(target - entry) / planned_risk,
                    "source_variant": SOURCE_VARIANT,
                    "source_file": "mes_asian_d_ema_baseline.jsonl",
                }
            )

    if not precursor_rows:
        raise core.StudyError("MES Asian D+EMA selected candidates produced no terminal WIN/LOSS rows")

    summary = {
        "selected_candidates": len(full_rows),
        "terminal": counts["WIN"] + counts["LOSS"],
        "wins": counts["WIN"],
        "losses": counts["LOSS"],
        "no_fill": counts[NO_FILL],
        "expired_open": counts[EXPIRED],
        "entry_filled_total": counts["WIN"] + counts["LOSS"] + counts[EXPIRED],
        "precursor_rows": len(precursor_rows),
    }
    if summary["selected_candidates"] != (
        summary["terminal"] + summary["no_fill"] + summary["expired_open"]
    ):
        raise core.StudyError("baseline population does not reconcile")
    return full_rows, precursor_rows, summary


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> str:
    return core.write_jsonl(path, rows)


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
        "study": "MES Asian cohort D + EMA-aligned canonical baseline",
        "producer": "scripts/mes_asian_d_ema_baseline.py",
        "producer_version": PRODUCER_VERSION,
        "source_logic": {
            "parent_pr": 593,
            "parent_head": "22a96b34fdfd4470014d708ba84d4588f9c74cdf",
            "predicate": "D0: bar_cohort D and candidate direction == EMA direction",
            "session": SESSION,
            "candidate_geometry": "unchanged source shadow candidate",
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
            "model": "preserved_#593_ioc_geometry",
            "ioc_tolerance_ticks": IOC_TOLERANCE_TICKS,
            "ioc_tolerance_points": IOC_TOLERANCE_POINTS,
            "slippage_ticks_entry": SLIPPAGE_TICKS,
            "slippage_ticks_stop": SLIPPAGE_TICKS,
            "target_fill": "clean",
            "same_bar_fill_stop": "pessimistic_stop_first",
            "expired_policy": "count_separately_exclude_from_terminal_performance",
            "commission_assumption_dollars": None,
        },
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
            },
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Proven snapshot containing bars_MES_*.jsonl and journal_*.jsonl",
    )
    parser.add_argument("--start-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="Inclusive YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="All selected D+EMA candidate rows")
    parser.add_argument(
        "--precursor-out",
        required=True,
        help="Terminal WIN/LOSS cohort normalized for PR #596",
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
        )
        full_out = Path(args.out)
        precursor_out = Path(args.precursor_out)
        full_sha = _write_jsonl(full_out, full_rows)
        precursor_sha = _write_jsonl(precursor_out, precursor_rows)
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
