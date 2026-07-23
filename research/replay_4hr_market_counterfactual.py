"""Research-only market-entry counterfactual for rejected 4HR IOC crossings.

Only dates classified ``IOC_CANCELLED`` by the audited honest-fill replay are
replayed as uncapped market entries. Existing IOC fills remain unchanged.
Invalid post-fill brackets fail closed and never enter P&L.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from research.reconcile_4hr_retrigger import load_bars_jsonl
from research.replay_4hr_retrigger_honest import replay_one, summarize


def _load_regime_rows(root: Path, dates: set[date]) -> dict[datetime, dict[str, Any]]:
    """Load cached Polygon replay labels for the requested ET trade dates."""
    rows: dict[datetime, dict[str, Any]] = {}
    candidate_dates = {
        day + timedelta(days=offset)
        for day in dates
        for offset in (-1, 0, 1)
    }
    for day in sorted(candidate_dates):
        path = root / f"MNQ_{day.isoformat()}.jsonl"
        if not path.exists():
            continue
        with path.open() as handle:
            for line in handle:
                raw = json.loads(line)
                ts = datetime.fromisoformat(raw["timestamp"]).astimezone(timezone.utc)
                rows[ts] = raw
    return rows


def _attach_regime(
    row: dict[str, Any], regime_rows: dict[datetime, dict[str, Any]]
) -> dict[str, Any]:
    enriched = dict(row)
    entry_ts = row.get("entry_bar_ts")
    if not entry_ts:
        enriched["market_condition"] = None
        enriched["trend_direction"] = None
        enriched["trend_strength"] = None
        return enriched
    key = datetime.fromisoformat(entry_ts).astimezone(timezone.utc)
    source = regime_rows.get(key)
    if source is None:
        raise ValueError(f"missing historical regime row for {entry_ts}")
    enriched["market_condition"] = source.get("market_condition")
    enriched["trend_direction"] = source.get("trend_direction")
    enriched["trend_strength"] = source.get("trend_strength")
    return enriched


def _split_summary(rows: list[dict[str, Any]], midpoint: date) -> dict[str, Any]:
    h1 = [row for row in rows if date.fromisoformat(row["date"]) < midpoint]
    h2 = [row for row in rows if date.fromisoformat(row["date"]) >= midpoint]
    return {
        "overall": summarize(rows),
        "halves": {"H1": summarize(h1), "H2": summarize(h2)},
        "direction": {
            side: summarize([row for row in rows if row["direction"] == side])
            for side in ("LONG", "SHORT")
        },
    }


def _quarterly(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        day = date.fromisoformat(row["date"])
        key = f"{day.year}-Q{((day.month - 1) // 3) + 1}"
        buckets.setdefault(key, []).append(row)
    result = {key: summarize(bucket) for key, bucket in sorted(buckets.items())}
    for summary in result.values():
        profit_factor = summary.get("profit_factor")
        if isinstance(profit_factor, float) and not math.isfinite(profit_factor):
            summary["profit_factor"] = None
    return result


def _with_original_signal_expectancy(
    summary: dict[str, Any], *, original_signal_count: int
) -> dict[str, Any]:
    result = dict(summary)
    result["expectancy_per_original_signal"] = round(
        result["net_pnl"] / original_signal_count, 4
    )
    return result


def build_report(
    *,
    reconciliation: dict[str, Any],
    honest_report: dict[str, Any],
    bars_5m: list[dict[str, Any]],
    bars_1h: list[dict[str, Any]],
    regime_rows: dict[datetime, dict[str, Any]],
    slippage_ticks: float = 2.0,
) -> dict[str, Any]:
    baseline_rows = honest_report["baseline"]["trades"]
    rejected_dates = {
        row["date"] for row in baseline_rows if row["status"] == "IOC_CANCELLED"
    }
    if len(rejected_dates) != 32:
        raise ValueError(f"expected 32 IOC rejections, found {len(rejected_dates)}")

    signal_map = reconciliation["detector_signals"]
    market_attempts = [
        replay_one(
            eval_date=date.fromisoformat(day),
            signal=signal_map[day],
            bars_5m=bars_5m,
            bars_1h=bars_1h,
            slippage_ticks=slippage_ticks,
            entry_model="market",
        )
        for day in sorted(rejected_dates)
    ]
    if any(row["status"] in {"IOC_CANCELLED", "NO_TRIGGER"} for row in market_attempts):
        raise ValueError("market counterfactual must recover every rejected crossing")

    original_ioc_fills = [
        dict(row) for row in baseline_rows if row["status"] == "FILLED"
    ]
    valid_market_fills = [row for row in market_attempts if row["filled"]]
    combined = original_ioc_fills + valid_market_fills
    combined = sorted(
        (_attach_regime(row, regime_rows) for row in combined),
        key=lambda row: row["date"],
    )
    market_attempts = [
        _attach_regime(row, regime_rows) for row in market_attempts
    ]

    midpoint = date.fromisoformat(honest_report["chronological_midpoint"][:10])
    trending = [row for row in combined if row["market_condition"] == "TRENDING"]
    non_trending = [
        row for row in combined if row["market_condition"] != "TRENDING"
    ]
    original_signal_count = honest_report["baseline"]["overall"]["n"]
    combined_summary = _split_summary(combined, midpoint)
    trending_summary = _split_summary(trending, midpoint)
    non_trending_summary = _split_summary(non_trending, midpoint)
    combined_summary["overall"] = _with_original_signal_expectancy(
        combined_summary["overall"], original_signal_count=original_signal_count
    )
    trending_summary["overall"] = _with_original_signal_expectancy(
        trending_summary["overall"], original_signal_count=original_signal_count
    )
    non_trending_summary["overall"] = _with_original_signal_expectancy(
        non_trending_summary["overall"], original_signal_count=original_signal_count
    )
    return {
        "schema_version": 1,
        "research_only": True,
        "strategy": "4HR Re-Trigger",
        "instrument": "MNQ",
        "counterfactual": "uncapped market entry on the 32 audited IOC rejections",
        "chronological_midpoint": midpoint.isoformat(),
        "assumptions": {
            "market_proxy": "completed trigger-crossing 5m bar close",
            "entry_fill": "market proxy +/- 2 ticks adverse slippage; no IOC cap",
            "stop": "last 1H bar completed before crossing-bar open; fixed forever",
            "target": "resolved prior 4PM reference boundary; unchanged",
            "bracket_validity": "LONG stop < fill < target; SHORT target < fill < stop",
            "invalid_bracket": "fail closed and exclude from P&L",
            "post_fill_causality": "bracket evaluation begins on next 5m bar",
            "same_bar_ambiguity": "stop first",
            "unresolved_exit": "15:55 ET bar close",
            "commission_round_trip": 1.24,
            "regime_gate": "entry decision row market_condition == TRENDING",
            "regime_label_provenance": (
                "cached Polygon replay labels derived by scripts/polygon_to_replay.py; "
                "historical replay proxy, not exact Pine-label parity"
            ),
        },
        "original_ioc": honest_report["baseline"]["overall"],
        "market_attempts": {
            "attempted": len(market_attempts),
            "valid_fills": len(valid_market_fills),
            "excluded": len(market_attempts) - len(valid_market_fills),
            "excluded_reasons": dict(
                sorted(
                    Counter(
                        row["status"] for row in market_attempts if not row["filled"]
                    ).items()
                )
            ),
            "invalid_bracket_reasons": dict(
                sorted(
                    Counter(
                        row.get("invalid_bracket_reason", "UNKNOWN")
                        for row in market_attempts
                        if not row["filled"]
                    ).items()
                )
            ),
            "regime_counts": dict(
                sorted(
                    Counter(
                        row["market_condition"]
                        for row in market_attempts
                        if row["filled"]
                    ).items()
                )
            ),
            **_split_summary(market_attempts, midpoint),
            "quarterly_valid_fills": _quarterly(valid_market_fills),
            "trades": market_attempts,
        },
        "combined": {
            **combined_summary,
            "regime_counts": dict(
                sorted(Counter(row["market_condition"] for row in combined).items())
            ),
            "quarterly": _quarterly(combined),
            "trades": combined,
        },
        "trending_only": {
            **trending_summary,
            "excluded_non_trending": len(non_trending),
            "quarterly": _quarterly(trending),
            "trades": trending,
        },
        "non_trending": {
            **non_trending_summary,
            "quarterly": _quarterly(non_trending),
            "trades": non_trending,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reconciliation", required=True)
    parser.add_argument("--honest-report", required=True)
    parser.add_argument("--bars-5m", required=True)
    parser.add_argument("--bars-1h", required=True)
    parser.add_argument("--regime-bars-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    reconciliation = json.loads(Path(args.reconciliation).read_text())
    honest_report = json.loads(Path(args.honest_report).read_text())
    dates = {
        date.fromisoformat(row["date"])
        for row in honest_report["baseline"]["trades"]
        if row.get("entry_bar_ts")
    }
    report = build_report(
        reconciliation=reconciliation,
        honest_report=honest_report,
        bars_5m=load_bars_jsonl(args.bars_5m),
        bars_1h=load_bars_jsonl(args.bars_1h),
        regime_rows=_load_regime_rows(Path(args.regime_bars_dir), dates),
    )
    Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
