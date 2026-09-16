#!/usr/bin/env python3
"""Reproduction driver for the preserved MNQ missed-opportunity study.

This driver intentionally reuses the archived-study implementation in
``mnq_missed_opportunity_producer.py`` for input discovery, regime evaluation,
shadow-candidate dedupe, IOC fill geometry, pessimistic resolution, manifest
hashing, and cohort predicates.

It exists for two proof-only integration fixes discovered on the real preserved
2026-09-01..16 snapshot:

1. The archived study reports filled-but-unresolved candidates as ``EXPIRED``
   and excludes them from terminal P&L.  The first strict producer aborted on
   that state.  This driver emits EXPIRED explicitly so the stats reporter can
   count it separately without changing P&L.
2. Direct invocation from outside the repo now bootstraps the repository root
   onto ``sys.path`` before importing repo modules; no PYTHONPATH workaround is
   required.

No trading/runtime path is called. Inputs and outputs are local files only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Any, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts import mnq_missed_opportunity_producer as core  # noqa: E402

REPRO_VERSION = "1.1.0"
_EXPIRED = "EXPIRED"
_NO_FILL = "NO_FILL"
_TERMINAL = frozenset(core.TERMINAL_RESULTS)


def produce_rows(
    records: Iterable[dict[str, Any]],
    *,
    bars: dict[str, dict[str, Any]],
    bar_timestamps: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Emit strict rows while preserving archived EXPIRED semantics.

    ``fills`` in the downstream reporter remains terminal WIN/LOSS count for
    comparability with the recorded extended-statistics table.  EXPIRED rows
    carry ``filled=true`` because the entry was filled, but P&L remains null and
    they are counted separately as ``expired_open``.
    """
    record_list = list(records)
    output: list[dict[str, Any]] = []
    variant_summary: dict[str, Any] = {}

    for cohort, predicate in core._variants():
        picks = [
            (record, candidate)
            for record in record_list
            for candidate in record["candidates"]
            if predicate(record, candidate)
        ]
        resolved: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for record, candidate in picks:
            outcome = core.resolve_ioc(
                candidate,
                record["ts"],
                bars=bars,
                bar_timestamps=bar_timestamps,
            )
            result = str(outcome.get("result") or "")
            if result not in _TERMINAL | {_NO_FILL, _EXPIRED}:
                raise core.StudyError(f"{cohort}: unsupported outcome result {result!r}")
            resolved.append((record, candidate, outcome))

        terminal_days = sorted(
            {
                core.observation_day(core.INSTRUMENT, record["ts"]).isoformat()
                for record, _candidate, outcome in resolved
                if outcome["result"] in _TERMINAL
            }
        )
        if resolved and not terminal_days:
            raise core.StudyError(f"{cohort}: candidates exist but no terminal rows define H1/H2 split")
        midpoint = terminal_days[len(terminal_days) // 2] if terminal_days else None

        terminal_count = 0
        no_fill_count = 0
        expired_count = 0
        for sequence, (record, candidate, outcome) in enumerate(resolved):
            result = str(outcome["result"])
            if result in _TERMINAL:
                terminal_count += 1
                entry_filled = True
            elif result == _NO_FILL:
                no_fill_count += 1
                entry_filled = False
            elif result == _EXPIRED:
                expired_count += 1
                entry_filled = True
            else:  # guarded above; retained as fail-closed defense
                raise core.StudyError(f"{cohort}: unsupported outcome result {result!r}")

            if midpoint is None:
                raise core.StudyError(f"{cohort}: cannot assign sample half without midpoint")
            day = core.observation_day(core.INSTRUMENT, record["ts"]).isoformat()
            sample_half = "H1" if day < midpoint else "H2"
            terminal = result in _TERMINAL
            row = {
                "cohort": cohort,
                "cohort_description": core.VARIANT_DESCRIPTIONS[cohort],
                "sample_half": sample_half,
                "sample_split_mid_day": midpoint,
                "sequence": sequence,
                "ts": record["ts"],
                "observation_day": day,
                "instrument": core.INSTRUMENT,
                "timeframe_minutes": core.TIMEFRAME_MINUTES,
                "strategy": candidate.get("strategy"),
                "direction": candidate.get("direction"),
                "entry": candidate.get("entry"),
                "stop": candidate.get("stop"),
                "target": candidate.get("target"),
                "result": result,
                "exit_reason": outcome.get("exit_reason"),
                "filled": entry_filled,
                "terminal": terminal,
                "pnl_dollars": float(outcome["pnl_dollars"]) if terminal else None,
                "pnl_r": (
                    float(outcome["pnl_r"])
                    if terminal and outcome.get("pnl_r") is not None
                    else None
                ),
                # Preserve excursion evidence for EXPIRED rows as provenance,
                # but the reporter excludes them from terminal MAE/MFE means.
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
                "bars_seen": outcome.get("bars_seen"),
                "pine_market_condition": record.get("pine"),
                "structural_market_condition": record.get("struct"),
                "structural_direction": record.get("sdir"),
                "ema_direction": record.get("ema_dir"),
                "ema_strength": record.get("ema_str"),
                "regime_pine": record.get("regime_pine"),
                "regime_struct": record.get("regime_struct"),
                "ioc_tolerance_points": core.IOC_TOLERANCE_POINTS,
                "slippage_assumption_ticks": core.SLIPPAGE_TICKS,
                "commission_assumption_dollars": None,
                "cost_note": "gross before commission; no configured commission value used",
                "producer": "scripts/mnq_missed_opportunity_repro.py",
                "producer_version": REPRO_VERSION,
            }
            for key in ("pnl_dollars", "pnl_r", "mae_r", "mfe_r"):
                value = row.get(key)
                if value is not None and not math.isfinite(float(value)):
                    raise core.StudyError(f"{cohort}: non-finite {key}")
            output.append(row)

        variant_summary[cohort] = {
            "description": core.VARIANT_DESCRIPTIONS[cohort],
            "candidates": len(resolved),
            "terminal": terminal_count,
            "no_fill": no_fill_count,
            "expired_open": expired_count,
            "entry_filled_total": terminal_count + expired_count,
            "terminal_days": terminal_days,
            "sample_split_mid_day": midpoint,
        }

    if not output:
        raise core.StudyError("counterfactual reproduction driver emitted no rows")
    return output, variant_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Archive directory containing bars_MNQ_*.jsonl and journal_*.jsonl",
    )
    parser.add_argument("--start-date", default="2026-09-01", help="Inclusive journal date, YYYY-MM-DD")
    parser.add_argument("--end-date", default="2026-09-16", help="Inclusive journal date, YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output candidate JSONL path")
    parser.add_argument("--manifest-out", required=True, help="Output provenance manifest JSON path")
    parser.add_argument(
        "--allow-journal-parse-skips",
        action="store_true",
        help="Compatibility mode matching the archived script's invalid-journal-row skip behavior",
    )
    args = parser.parse_args(argv)

    try:
        start_date = date.fromisoformat(args.start_date)
        end_date = date.fromisoformat(args.end_date)
        inputs = core.discover_inputs(
            Path(args.data_dir),
            start_date=start_date,
            end_date=end_date,
            allow_journal_parse_skips=args.allow_journal_parse_skips,
        )
        records, record_summary = core.build_records(inputs)
        rows, variant_summary = produce_rows(
            records,
            bars=inputs.bars,
            bar_timestamps=inputs.bar_timestamps,
        )
        output_path = Path(args.out)
        output_sha = core.write_jsonl(output_path, rows)
        manifest = core.build_manifest(
            inputs,
            start_date=start_date,
            end_date=end_date,
            record_summary=record_summary,
            variant_summary=variant_summary,
            output_path=output_path,
            output_sha256=output_sha,
        )
        manifest["producer"] = "scripts/mnq_missed_opportunity_repro.py"
        manifest["producer_version"] = REPRO_VERSION
        manifest["expired_semantics"] = (
            "filled-but-unresolved candidates are emitted as EXPIRED with null terminal P&L"
        )
        manifest_path = Path(args.manifest_out)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (OSError, core.StudyError, ValueError) as exc:
        parser.error(str(exc))

    print(
        f"wrote {len(rows)} rows to {output_path} "
        f"sha256={output_sha} manifest={manifest_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
