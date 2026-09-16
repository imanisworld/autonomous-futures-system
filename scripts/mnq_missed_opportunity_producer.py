#!/usr/bin/env python3
"""Offline MNQ missed-opportunity counterfactual producer.

This preserves the 2026-09-16 A/B/C/D layer-isolation study as repo-side,
read-only research code. It consumes archived MNQ 15m bar JSONL and journal
JSONL, reuses the repository's real regime classifier and observation resolver,
and emits deterministic candidate rows for ``counterfactual_stats_report.py``.

It does NOT connect to a broker, submit orders, call the webhook runner, mutate
campaign state, read runtime environment configuration, or touch the VPS.

Preserved study assumptions:
- instrument: MNQ only
- decision timeframe: 15m only
- IOC tolerance: 8.0 MNQ points
- one tick adverse entry slippage
- one tick adverse stop-exit slippage
- clean target fills
- same-bar fill+stop is a pessimistic stop loss
- commission is not configured; no commission is deducted here
- shadow-candidate dedupe identity:
  observation day × strategy × direction × entry × stop × target

Output contract, one row per selected candidate/outcome:
- cohort: A, B, C, D1, D2, or D0
- sample_half: H1/H2 using the archived study's per-cohort terminal-day midpoint
- sequence: stable encounter order within cohort
- ts: candidate bar timestamp
- filled: true only for terminal WIN/LOSS rows
- pnl_dollars: gross one-contract P&L for filled terminal rows, else null
- optional MAE/MFE in R

The producer fails closed on ambiguous inputs that could silently change the
recorded study: conflicting duplicate bars, regime-classifier errors, filled
but unresolved EXPIRED candidates, or malformed journal JSON unless the operator
explicitly opts into the archived script's skip-invalid-journal behavior.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any, Callable, Iterable

from execution.cross_instrument_observation import _resolve_one, observation_day
from strategy.regime_classifier import classify_regime

INSTRUMENT = "MNQ"
TIMEFRAME_MINUTES = 15
TICK = 0.25
TICK_VALUE = 0.50
SLIPPAGE_TICKS = 1.0
IOC_TOLERANCE_POINTS = 8.0
PRODUCER_VERSION = "1.0.0"
TERMINAL_RESULTS = {"WIN", "LOSS"}

VARIANT_DESCRIPTIONS = {
    "A": "market-condition ONLY: Pine label -> repo structural classifier; HTF regime intact",
    "B": "regime ONLY: Pine label intact; HTF regime removed; direction = EMA",
    "C": "detectors ONLY: both gates intact; broader shadow detector set allowed",
    "D1": "diagnostic: A+B; structural label, no regime; direction = structure",
    "D2": "diagnostic: all candidates; no gates",
    "D0": "control: neither Pine TRENDING nor structural trend; EMA-aligned",
}

_JOURNAL_DATE_RE = re.compile(r"journal_(\d{4}-\d{2}-\d{2})")


class StudyError(ValueError):
    """Input/provenance ambiguity that blocks a reproducible study run."""


@dataclass(frozen=True)
class StudyInputs:
    bars: dict[str, dict[str, Any]]
    bar_timestamps: tuple[str, ...]
    journal_rows: tuple[dict[str, Any], ...]
    bar_files: tuple[Path, ...]
    journal_files: tuple[Path, ...]
    journal_parse_skips: int


def _timeframe_is_15(value: object) -> bool:
    return str(value or "").strip().lower() in {"15", "15m"}


def _json_lines(path: Path, *, skip_invalid: bool) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    with path.open(encoding="utf-8") as handle:
        for lineno, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                if skip_invalid:
                    skipped += 1
                    continue
                raise StudyError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            if not isinstance(value, dict):
                if skip_invalid:
                    skipped += 1
                    continue
                raise StudyError(f"{path}:{lineno}: row must be a JSON object")
            rows.append(value)
    return rows, skipped


def _journal_file_date(path: Path) -> date:
    match = _JOURNAL_DATE_RE.search(path.name)
    if not match:
        raise StudyError(f"journal filename lacks YYYY-MM-DD date: {path}")
    return date.fromisoformat(match.group(1))


def discover_inputs(
    data_dir: Path,
    *,
    start_date: date,
    end_date: date,
    allow_journal_parse_skips: bool = False,
) -> StudyInputs:
    if end_date < start_date:
        raise StudyError("end date precedes start date")
    bar_files = tuple(sorted(data_dir.glob("bars_MNQ_*.jsonl")))
    all_journals = tuple(sorted(data_dir.glob("journal_2026-09-*.jsonl")))
    journal_files = tuple(
        path for path in all_journals if start_date <= _journal_file_date(path) <= end_date
    )
    if not bar_files:
        raise StudyError(f"no bars_MNQ_*.jsonl files found in {data_dir}")
    if not journal_files:
        raise StudyError(
            f"no journal files found in {data_dir} for {start_date.isoformat()}..{end_date.isoformat()}"
        )

    bars: dict[str, dict[str, Any]] = {}
    for path in bar_files:
        file_rows, _ = _json_lines(path, skip_invalid=False)
        for row in file_rows:
            if not _timeframe_is_15(row.get("timeframe")):
                continue
            ts = str(row.get("ts") or "").strip()
            if not ts:
                raise StudyError(f"{path}: 15m bar missing ts")
            existing = bars.get(ts)
            if existing is not None and existing != row:
                raise StudyError(f"conflicting duplicate 15m bar timestamp {ts}")
            bars[ts] = row
    if not bars:
        raise StudyError("no MNQ 15m bars found")

    journals: list[dict[str, Any]] = []
    skipped = 0
    for path in journal_files:
        file_rows, file_skips = _json_lines(path, skip_invalid=allow_journal_parse_skips)
        skipped += file_skips
        for row in file_rows:
            if row.get("instrument") != INSTRUMENT:
                continue
            if row.get("decision") not in {"NO_TRADE", "TRADE", "RISK_REJECTED"}:
                continue
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            if not _timeframe_is_15(context.get("timeframe")):
                continue
            journals.append(row)
    if not journals:
        raise StudyError("no qualifying MNQ 15m journal decision rows found")
    if skipped and not allow_journal_parse_skips:
        raise StudyError("journal parse skips present without explicit permission")

    return StudyInputs(
        bars=bars,
        bar_timestamps=tuple(sorted(bars)),
        journal_rows=tuple(journals),
        bar_files=bar_files,
        journal_files=journal_files,
        journal_parse_skips=skipped,
    )


def _ns(value: Any) -> Any:
    if isinstance(value, dict):
        return NS(**{key: _ns(item) for key, item in value.items()})
    return value


def regime_for(context: dict[str, Any], market_condition: object) -> str:
    state = _ns(
        {
            "strat": context.get("strat"),
            "icc": context.get("icc"),
            "signa": context.get("signa"),
            "trend": context.get("trend"),
            "vwap": context.get("vwap") or {},
            "orb": context.get("orb") or {},
            "market_condition": market_condition,
        }
    )
    if not hasattr(state.vwap, "price_vs_vwap"):
        state.vwap.price_vs_vwap = None
    if not hasattr(state.orb, "status"):
        state.orb.status = None
    try:
        return str(classify_regime(state, None).regime)
    except Exception as exc:  # exact error type is recorded, then caller fails closed
        return f"ERR:{type(exc).__name__}"


def _forward_bars(
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
    signal_ts: str,
) -> list[dict[str, Any]]:
    signal_day = observation_day(INSTRUMENT, signal_ts).isoformat()
    out: list[dict[str, Any]] = []
    for ts in bar_timestamps:
        if ts <= signal_ts:
            continue
        if observation_day(INSTRUMENT, ts).isoformat() != signal_day:
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
    """Preserved paper-broker-like IOC geometry from the archived study."""
    forward = _forward_bars(bars, bar_timestamps, signal_ts)
    try:
        entry = float(candidate["entry"])
        stop = float(candidate["stop"])
        target = float(candidate["target"])
        direction = str(candidate["direction"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StudyError(f"invalid candidate geometry at {signal_ts}: {candidate}") from exc
    if direction not in {"LONG", "SHORT"}:
        raise StudyError(f"invalid candidate direction {direction!r}")
    risk = abs(entry - stop)
    if risk <= 0:
        raise StudyError(f"non-positive candidate risk at {signal_ts}")

    slippage = SLIPPAGE_TICKS * TICK
    fill_price: float | None = None
    fill_index: int | None = None
    for index, bar in enumerate(forward):
        try:
            open_px = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StudyError(f"invalid forward bar for {signal_ts}: {bar}") from exc
        if not (low <= entry <= high):
            continue
        if direction == "LONG":
            if open_px > entry + IOC_TOLERANCE_POINTS:
                return {
                    "result": "NO_FILL",
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
                    "result": "NO_FILL",
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
            "result": "NO_FILL",
            "exit_reason": "NEVER_TOUCHED",
            "bars_seen": len(forward),
            "pnl_r": None,
            "pnl_dollars": None,
            "mae_r": None,
            "mfe_r": None,
        }

    record = {
        "direction": direction,
        "entry": fill_price,
        "stop": stop,
        "target": target,
        "instrument": INSTRUMENT,
    }
    pending = {
        "record": record,
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
        outcome = _resolve_one(pending, forward[fill_index + 1 :])
        if outcome is None:
            return {
                "result": "EXPIRED",
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

    points = (
        exit_price - fill_price if direction == "LONG" else fill_price - exit_price
    )
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


def _struct_direction(record: dict[str, Any]) -> str | None:
    return "LONG" if record.get("sdir") == "UP" else "SHORT" if record.get("sdir") == "DOWN" else None


def _ema_direction(record: dict[str, Any]) -> str | None:
    return "LONG" if record.get("ema_dir") == "UP" else "SHORT" if record.get("ema_dir") == "DOWN" else None


def _full_direction(regime: object) -> str | None:
    return "LONG" if regime == "FULL_LONG" else "SHORT" if regime == "FULL_SHORT" else None


def build_records(inputs: StudyInputs) -> tuple[list[dict[str, Any]], dict[str, int]]:
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
            decision.get("regime")
            or regime_for(context, pine)
            if pine == "TRENDING"
            else "NOT_EVALUATED(label)"
        )
        regime_struct = regime_for(context, "TRENDING") if structural_trend else "n/a"
        regime_nolabel = regime_for(context, "TRENDING")
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
                    observation_day(INSTRUMENT, ts).isoformat(),
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
        raise StudyError(f"regime classifier produced {regime_errors} error result(s)")
    return records, {
        "skipped_decisions_missing_matching_15m_bar": skipped_missing_bar,
        "distinct_shadow_candidates": sum(len(record["candidates"]) for record in records),
    }


def _variants() -> tuple[tuple[str, Callable[[dict[str, Any], dict[str, Any]], bool]], ...]:
    return (
        (
            "A",
            lambda record, candidate: record["struct"].startswith("STRUCTURAL_TREND")
            and _full_direction(record["regime_struct"]) == candidate.get("direction"),
        ),
        (
            "B",
            lambda record, candidate: record["pine"] == "TRENDING"
            and _ema_direction(record) == candidate.get("direction"),
        ),
        (
            "C",
            lambda record, candidate: record["pine"] == "TRENDING"
            and _full_direction(record["regime_pine"]) == candidate.get("direction"),
        ),
        (
            "D1",
            lambda record, candidate: _struct_direction(record) == candidate.get("direction"),
        ),
        ("D2", lambda _record, _candidate: True),
        (
            "D0",
            lambda record, candidate: record["bar_cohort"] == "D"
            and _ema_direction(record) == candidate.get("direction"),
        ),
    )


def produce_rows(
    records: Iterable[dict[str, Any]],
    *,
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    record_list = list(records)
    output: list[dict[str, Any]] = []
    variant_summary: dict[str, Any] = {}

    for cohort, predicate in _variants():
        picks = [
            (record, candidate)
            for record in record_list
            for candidate in record["candidates"]
            if predicate(record, candidate)
        ]
        resolved: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for record, candidate in picks:
            outcome = resolve_ioc(
                candidate,
                record["ts"],
                bars=bars,
                bar_timestamps=bar_timestamps,
            )
            if outcome["result"] == "EXPIRED":
                raise StudyError(
                    f"{cohort}: filled-but-unresolved EXPIRED candidate cannot be represented "
                    "by the strict stats contract"
                )
            resolved.append((record, candidate, outcome))

        terminal_days = sorted(
            {
                observation_day(INSTRUMENT, record["ts"]).isoformat()
                for record, _candidate, outcome in resolved
                if outcome["result"] in TERMINAL_RESULTS
            }
        )
        if resolved and not terminal_days:
            raise StudyError(f"{cohort}: candidates exist but no terminal rows define H1/H2 split")
        midpoint = terminal_days[len(terminal_days) // 2] if terminal_days else None

        terminal_count = 0
        no_fill_count = 0
        for sequence, (record, candidate, outcome) in enumerate(resolved):
            result = str(outcome["result"])
            filled = result in TERMINAL_RESULTS
            if filled:
                terminal_count += 1
            elif result == "NO_FILL":
                no_fill_count += 1
            else:
                raise StudyError(f"{cohort}: unsupported outcome result {result!r}")
            day = observation_day(INSTRUMENT, record["ts"]).isoformat()
            if midpoint is None:
                raise StudyError(f"{cohort}: cannot assign sample half without midpoint")
            sample_half = "H1" if day < midpoint else "H2"
            row = {
                "cohort": cohort,
                "cohort_description": VARIANT_DESCRIPTIONS[cohort],
                "sample_half": sample_half,
                "sample_split_mid_day": midpoint,
                "sequence": sequence,
                "ts": record["ts"],
                "observation_day": day,
                "instrument": INSTRUMENT,
                "timeframe_minutes": TIMEFRAME_MINUTES,
                "strategy": candidate.get("strategy"),
                "direction": candidate.get("direction"),
                "entry": candidate.get("entry"),
                "stop": candidate.get("stop"),
                "target": candidate.get("target"),
                "result": result,
                "exit_reason": outcome.get("exit_reason"),
                "filled": filled,
                "pnl_dollars": float(outcome["pnl_dollars"]) if filled else None,
                "pnl_r": float(outcome["pnl_r"]) if filled and outcome.get("pnl_r") is not None else None,
                "mae_r": float(outcome["mae_r"]) if filled and outcome.get("mae_r") is not None else None,
                "mfe_r": float(outcome["mfe_r"]) if filled and outcome.get("mfe_r") is not None else None,
                "bars_seen": outcome.get("bars_seen"),
                "pine_market_condition": record.get("pine"),
                "structural_market_condition": record.get("struct"),
                "structural_direction": record.get("sdir"),
                "ema_direction": record.get("ema_dir"),
                "ema_strength": record.get("ema_str"),
                "regime_pine": record.get("regime_pine"),
                "regime_struct": record.get("regime_struct"),
                "ioc_tolerance_points": IOC_TOLERANCE_POINTS,
                "slippage_assumption_ticks": SLIPPAGE_TICKS,
                "commission_assumption_dollars": None,
                "cost_note": "gross before commission; no configured commission value used",
                "producer": "scripts/mnq_missed_opportunity_producer.py",
                "producer_version": PRODUCER_VERSION,
            }
            for key in ("pnl_dollars", "pnl_r", "mae_r", "mfe_r"):
                value = row.get(key)
                if value is not None and not math.isfinite(float(value)):
                    raise StudyError(f"{cohort}: non-finite {key}")
            output.append(row)

        variant_summary[cohort] = {
            "description": VARIANT_DESCRIPTIONS[cohort],
            "candidates": len(resolved),
            "terminal": terminal_count,
            "no_fill": no_fill_count,
            "terminal_days": terminal_days,
            "sample_split_mid_day": midpoint,
        }

    if not output:
        raise StudyError("counterfactual producer emitted no rows")
    return output, variant_summary


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n")
    return _sha256(path)


def build_manifest(
    inputs: StudyInputs,
    *,
    start_date: date,
    end_date: date,
    record_summary: dict[str, int],
    variant_summary: dict[str, Any],
    output_path: Path,
    output_sha256: str,
) -> dict[str, Any]:
    return {
        "study": "MNQ missed-opportunity layer-isolated counterfactual",
        "producer_version": PRODUCER_VERSION,
        "instrument": INSTRUMENT,
        "timeframe_minutes": TIMEFRAME_MINUTES,
        "journal_date_range": [start_date.isoformat(), end_date.isoformat()],
        "assumptions": {
            "ioc_tolerance_points": IOC_TOLERANCE_POINTS,
            "slippage_ticks_entry": SLIPPAGE_TICKS,
            "slippage_ticks_stop": SLIPPAGE_TICKS,
            "target_fill": "clean",
            "same_bar_fill_stop": "pessimistic_stop_first",
            "commission_assumption_dollars": None,
        },
        "journal_parse_skips": inputs.journal_parse_skips,
        "record_summary": record_summary,
        "variant_summary": variant_summary,
        "inputs": {
            "bars": [{"path": str(path), "sha256": _sha256(path)} for path in inputs.bar_files],
            "journals": [
                {"path": str(path), "sha256": _sha256(path)} for path in inputs.journal_files
            ],
        },
        "output": {"path": str(output_path), "sha256": output_sha256},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="Archive directory containing bars_MNQ_*.jsonl and journal_*.jsonl")
    parser.add_argument("--start-date", default="2026-09-01", help="Inclusive journal file date, YYYY-MM-DD")
    parser.add_argument("--end-date", default="2026-09-16", help="Inclusive journal file date, YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output candidate JSONL path")
    parser.add_argument("--manifest-out", required=True, help="Output provenance manifest JSON path")
    parser.add_argument(
        "--allow-journal-parse-skips",
        action="store_true",
        help="Explicit compatibility mode matching the archived script's invalid-journal-row skip behavior",
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
        rows, variant_summary = produce_rows(
            records,
            bars=inputs.bars,
            bar_timestamps=inputs.bar_timestamps,
        )
        output_path = Path(args.out)
        output_sha = write_jsonl(output_path, rows)
        manifest = build_manifest(
            inputs,
            start_date=start_date,
            end_date=end_date,
            record_summary=record_summary,
            variant_summary=variant_summary,
            output_path=output_path,
            output_sha256=output_sha,
        )
        manifest_path = Path(args.manifest_out)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, StudyError, ValueError) as exc:
        parser.error(str(exc))

    print(
        f"wrote {len(rows)} rows to {output_path} "
        f"sha256={output_sha} manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
