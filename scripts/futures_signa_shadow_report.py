#!/usr/bin/env python3
"""Read-only effectiveness/coverage report for futures Signa shadow context.

Joins only on exact durable identity already present in the futures journal:
(instrument, signal_timestamp, strategy). It never falls back to FIFO, nearest
 timestamp, or same-session guesses. Missing/ambiguous identity stays unjoined.

This report does not promote Signa or change any runtime behavior.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from context.futures_signa_shadow import direction_relation


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows = []
    with Path(path).open() as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def load_outcomes(log_dir: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(Path(log_dir).glob("journal_*.jsonl")):
        for row in load_jsonl(path):
            if row.get("type") != "OUTCOME":
                continue
            outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
            rows.append({**row, "outcome": outcome})
    return rows


def exact_join(
    shadow_rows: Iterable[dict[str, Any]],
    outcomes: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Join without any inferred identity.

    An outcome participates only when instrument, signal_timestamp, and strategy
    are all present. If more than one outcome has the same identity, that key is
    ambiguous and no shadow row is joined to it.
    """
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    quality = {
        "shadow_rows": 0,
        "outcomes": 0,
        "outcomes_missing_identity": 0,
        "ambiguous_outcome_keys": 0,
        "joined": 0,
        "shadow_missing_identity": 0,
        "shadow_unmatched": 0,
    }
    for outcome_row in outcomes:
        quality["outcomes"] += 1
        outcome = outcome_row.get("outcome") or {}
        key = _key(
            outcome_row.get("instrument"),
            outcome.get("signal_timestamp"),
            outcome.get("strategy"),
        )
        if key is None:
            quality["outcomes_missing_identity"] += 1
            continue
        index[key].append(outcome_row)
    ambiguous = {key for key, rows in index.items() if len(rows) != 1}
    quality["ambiguous_outcome_keys"] = len(ambiguous)

    joined = []
    for shadow in shadow_rows:
        quality["shadow_rows"] += 1
        key = _key(shadow.get("instrument"), shadow.get("timestamp"), shadow.get("strategy"))
        if key is None:
            quality["shadow_missing_identity"] += 1
            continue
        matches = index.get(key, [])
        if key in ambiguous or len(matches) != 1:
            quality["shadow_unmatched"] += 1
            continue
        outcome_row = matches[0]
        outcome = outcome_row.get("outcome") or {}
        pnl = _float(outcome.get("pnl_dollars"))
        joined.append(
            {
                "instrument": key[0],
                "timestamp": key[1],
                "strategy": key[2],
                "trade_direction": shadow.get("trade_direction"),
                "signa_direction": shadow.get("signa_v2_direction"),
                "relation": direction_relation(
                    shadow.get("trade_direction"), shadow.get("signa_v2_direction")
                ),
                "grade": shadow.get("signa_v2_grade"),
                "confidence": _float(shadow.get("signa_v2_confidence")),
                "signa_timeframe": shadow.get("signa_v2_timeframe"),
                "cached": shadow.get("signa_v2_cached"),
                "data_as_of": shadow.get("signa_v2_data_as_of"),
                "retrieved_at": shadow.get("signa_v2_retrieved_at"),
                "component_scores": shadow.get("signa_v2_component_scores") or {},
                "outcome_result": outcome.get("result"),
                "pnl_dollars": pnl,
            }
        )
        quality["joined"] += 1
    return joined, quality


def build_report(joined: Iterable[dict[str, Any]], quality: dict[str, int]) -> dict[str, Any]:
    rows = list(joined)
    return {
        "authority": "observational_only",
        "promotion_permitted": False,
        "join_contract": "exact instrument + signal_timestamp + strategy only",
        "data_quality": quality,
        "overall": _metrics(rows),
        "by_strategy": _group(rows, lambda row: row.get("strategy") or "MISSING"),
        "by_instrument": _group(rows, lambda row: row.get("instrument") or "MISSING"),
        "by_relation": _group(rows, lambda row: row.get("relation") or "MISSING"),
        "by_grade": _group(rows, lambda row: row.get("grade") or "MISSING"),
        "by_confidence_band": _group(rows, _confidence_band),
        "by_signa_timeframe": _group(rows, lambda row: row.get("signa_timeframe") or "MISSING"),
    }


def _group(rows, key_fn) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row))].append(row)
    return {key: _metrics(grouped[key]) for key in sorted(grouped)}


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [value for row in rows if (value := _float(row.get("pnl_dollars"))) is not None]
    wins = sum(value > 0 for value in pnls)
    losses = sum(value < 0 for value in pnls)
    gross_win = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    days = {str(row.get("timestamp") or "")[:10] for row in rows if row.get("timestamp")}
    return {
        "n_joined": len(rows),
        "n_priced": len(pnls),
        "distinct_days": len(days),
        "wins": wins,
        "losses": losses,
        "net_pnl_dollars": round(sum(pnls), 2) if pnls else None,
        "win_rate_percent": round((wins / len(pnls)) * 100.0, 2) if pnls else None,
        "profit_factor": (
            round(gross_win / gross_loss, 4)
            if gross_loss > 0
            else None
        ),
    }


def _confidence_band(row: dict[str, Any]) -> str:
    value = _float(row.get("confidence"))
    if value is None:
        return "MISSING"
    if value >= 80:
        return "80_PLUS"
    if value >= 70:
        return "70_79"
    if value >= 50:
        return "50_69"
    return "BELOW_50"


def _key(instrument: Any, timestamp: Any, strategy: Any) -> tuple[str, str, str] | None:
    instrument = str(instrument or "").strip().upper()
    timestamp = str(timestamp or "").strip()
    strategy = str(strategy or "").strip()
    if not instrument or not timestamp or not strategy:
        return None
    return instrument, timestamp, strategy


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only futures Signa shadow report")
    parser.add_argument("--shadow-log", required=True)
    parser.add_argument("--futures-log-dir", required=True)
    args = parser.parse_args()

    shadow = load_jsonl(args.shadow_log)
    outcomes = load_outcomes(args.futures_log_dir)
    joined, quality = exact_join(shadow, outcomes)
    print(json.dumps(build_report(joined, quality), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
