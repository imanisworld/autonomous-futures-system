"""Read-only effectiveness report for namespaced Signa v2 options telemetry.

This report reuses the existing options scanner SQLite evidence and the existing
readiness metric policy. It never opens the database writable, never changes a
trade decision, and never promotes Signa. Its purpose is narrower: once v2
telemetry has actually been collected, show whether specific Signa dimensions
are associated with different realized paper outcomes.

No runtime caller imports this script.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from ops.evidence_readiness import CONTEXT_MIN_EXAMPLES, STRATEGY_MIN_DAYS, _performance_metrics


TERMINAL_STATUSES = {"WIN", "LOSS", "BREAKEVEN", "CLOSED", "EXPIRED", "CANCELLED", "REJECTED"}


def load_resolved_v2_rows(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Read resolved shadow rows joined to the exact scan raw telemetry.

    SQLite is opened with ``mode=ro`` so this report cannot initialize tables,
    migrate schema, or mutate campaign evidence.
    """
    db_path = Path(path).expanduser().resolve()
    uri = f"file:{db_path}?mode=ro"
    rows: list[dict[str, Any]] = []
    counts = {"joined": 0, "malformed_json": 0, "without_v2": 0, "missing_pnl": 0}
    with sqlite3.connect(uri, uri=True) as conn:
        conn.row_factory = sqlite3.Row
        query = """
            SELECT
                s.id AS scan_id,
                s.timestamp AS scan_timestamp,
                s.ticker,
                s.direction,
                s.pattern,
                s.raw_json,
                j.id AS shadow_id,
                j.status,
                j.outcome_json
            FROM scans AS s
            JOIN options_shadow_journal AS j ON j.scan_id = s.id
            WHERE j.status <> 'OPEN'
            ORDER BY j.id ASC
        """
        for row in conn.execute(query):
            counts["joined"] += 1
            try:
                raw = json.loads(row["raw_json"] or "{}")
                outcome = json.loads(row["outcome_json"] or "{}")
            except (TypeError, ValueError):
                counts["malformed_json"] += 1
                continue
            if raw.get("signa_v2_ok") is not True:
                counts["without_v2"] += 1
                continue
            pnl = _number(outcome.get("pnl_dollars"))
            if pnl is None:
                counts["missing_pnl"] += 1
                continue
            rows.append(
                {
                    "scan_id": int(row["scan_id"]),
                    "shadow_id": int(row["shadow_id"]),
                    "timestamp": str(row["scan_timestamp"]),
                    "ticker": str(row["ticker"]),
                    "direction": str(row["direction"]).upper(),
                    "pattern": str(row["pattern"]),
                    "status": str(row["status"]).upper(),
                    "raw": raw,
                    "outcome": {**outcome, "pnl_dollars": pnl},
                }
            )
    return rows, counts


def build_effectiveness_report(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(rows)
    report: dict[str, Any] = {
        "policy": {
            "minimum_examples": CONTEXT_MIN_EXAMPLES,
            "minimum_distinct_days": STRATEGY_MIN_DAYS,
            "authority": "observational_only",
            "promotion": "not_permitted_by_this_report",
        },
        "overall": _metrics(rows),
        "by_setup": _group_metrics(rows, lambda row: row.get("pattern") or "UNKNOWN"),
        "by_trade_direction": _group_metrics(rows, lambda row: row.get("direction") or "UNKNOWN"),
        "by_signa_alignment": _group_metrics(rows, _alignment),
        "by_signa_grade": _group_metrics(rows, _grade),
        "by_confidence_band": _group_metrics(rows, _confidence_band),
        "by_signa_timeframe": _group_metrics(
            rows, lambda row: str((row.get("raw") or {}).get("signa_v2_timeframe") or "MISSING")
        ),
        "components": {},
    }

    component_names = sorted(
        {
            str(name)
            for row in rows
            for name in _dict((row.get("raw") or {}).get("signa_v2_component_scores"))
        }
    )
    for component in component_names:
        report["components"][component] = _group_metrics(
            rows,
            lambda row, component=component: _score_band(
                _number(
                    _dict((row.get("raw") or {}).get("signa_v2_component_scores")).get(component)
                )
            ),
        )
    return report


def _group_metrics(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row))].append(row)
    return {key: _metrics(grouped[key]) for key in sorted(grouped)}


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pairs = [(row, row.get("outcome") or {}) for row in rows]
    base = _performance_metrics(pairs)
    pnls = [float((row.get("outcome") or {}).get("pnl_dollars") or 0.0) for row in rows]
    wins = sum(pnl > 0 for pnl in pnls)
    losses = sum(pnl < 0 for pnl in pnls)
    breakeven = sum(pnl == 0 for pnl in pnls)
    base.update(
        {
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate_percent": round((wins / len(pnls)) * 100.0, 2) if pnls else None,
            "ready_for_review": (
                base["sample_size"] >= CONTEXT_MIN_EXAMPLES
                and base["distinct_days"] >= STRATEGY_MIN_DAYS
            ),
        }
    )
    return base


def _alignment(row: dict[str, Any]) -> str:
    trade = str(row.get("direction") or "").upper()
    signa = str((row.get("raw") or {}).get("signa_v2_direction") or "").upper()
    if not signa:
        return "MISSING"
    if signa in {"WAIT", "NEUTRAL", "FLAT", "SIDEWAYS"}:
        return "NEUTRAL"
    if trade == "LONG":
        return "ALIGNED" if signa in {"LONG", "BUY", "UP", "BULL", "BULLISH"} else "OPPOSED"
    if trade == "SHORT":
        return "ALIGNED" if signa in {"SHORT", "SELL", "DOWN", "BEAR", "BEARISH"} else "OPPOSED"
    return "UNKNOWN_TRADE_DIRECTION"


def _grade(row: dict[str, Any]) -> str:
    grade = str((row.get("raw") or {}).get("signa_v2_grade") or "").strip().upper()
    return grade or "MISSING"


def _confidence_band(row: dict[str, Any]) -> str:
    return _score_band(_number((row.get("raw") or {}).get("signa_v2_confidence")))


def _score_band(value: float | None) -> str:
    if value is None:
        return "MISSING"
    if value >= 80:
        return "80_PLUS"
    if value >= 70:
        return "70_79"
    if value >= 50:
        return "50_69"
    return "BELOW_50"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only options Signa v2 effectiveness report")
    parser.add_argument("sqlite_path")
    args = parser.parse_args()

    rows, data_quality = load_resolved_v2_rows(args.sqlite_path)
    report = build_effectiveness_report(rows)
    report["data_quality"] = data_quality
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
