"""Read-only fill-realism status derived exclusively from journal outcomes."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

LIMIT_SETUPS = {
    "vwap_reclaim",
    "vwap_hold",
    "vwap_rejection",
    "pdh_reclaim",
    "pdl_reclaim",
    "continuation_pullback",
}
_STATUS_CACHE: dict[tuple, dict] = {}


def is_entry_nofill(result: str | None, reason: str | None) -> bool:
    reason = reason or ""
    return result == "CANCELLED" and (
        "ENTRY_NOT_FILLED" in reason.upper()
        or "execution_failed:CANCELLED" in reason
    )


def pair_resolved_attempts(paths: Iterable[Path | str]) -> tuple[list[dict], int]:
    """Pair TRADE decisions to outcomes, preserving pending trades across files."""
    pending: dict[str, dict] = {}
    rows: list[dict] = []
    for raw_path in sorted(Path(path) for path in paths):
        try:
            lines = raw_path.read_text().splitlines()
        except OSError:
            continue
        journal_date = raw_path.stem.removeprefix("journal_")
        for line in lines:
            try:
                record = json.loads(line)
            except (TypeError, ValueError):
                continue
            instrument = record.get("instrument")
            if not instrument:
                continue
            if record.get("decision") == "TRADE" and isinstance(record.get("setup"), dict):
                setup = record["setup"]
                pending[instrument] = {
                    "strategy": setup.get("strategy") or "unknown",
                    "instrument": instrument,
                    "direction": setup.get("direction"),
                    "decision_timestamp": (
                        record.get("ts")
                        or record.get("timestamp")
                        or record.get("bar_ts")
                    ),
                    "decision_date": journal_date,
                }
            elif record.get("type") == "OUTCOME" and isinstance(record.get("outcome"), dict):
                decision = pending.pop(instrument, None)
                if decision is None:
                    continue
                outcome = record["outcome"]
                decision.update(
                    {
                        "result": outcome.get("result"),
                        "exit_reason": outcome.get("exit_reason"),
                        "outcome_timestamp": record.get("ts") or record.get("timestamp"),
                        "outcome_date": journal_date,
                        "no_fill": is_entry_nofill(
                            outcome.get("result"), outcome.get("exit_reason")
                        ),
                    }
                )
                rows.append(decision)
    return rows, len(pending)


def _rate(no_fills: int, resolved: int) -> float | None:
    return round(100.0 * no_fills / resolved, 1) if resolved else None


def build_fill_realism_status(
    log_dir: str | Path,
    *,
    days: int = 7,
    through_date: date | None = None,
    recent_limit: int = 20,
) -> dict:
    """Build API-ready fill status without mutating or modeling journal evidence."""
    end = through_date or date.today()
    start = end - timedelta(days=days - 1)
    directory = Path(log_dir)
    paths = [
        directory / f"journal_{start + timedelta(days=offset)}.jsonl"
        for offset in range(days)
    ]
    existing = [path for path in paths if path.exists()]
    signatures = tuple(
        (str(path), path.stat().st_mtime_ns, path.stat().st_size) for path in existing
    )
    cache_key = (
        str(directory.resolve()),
        start.isoformat(),
        end.isoformat(),
        recent_limit,
        signatures,
    )
    cached = _STATUS_CACHE.get(cache_key)
    if cached is not None:
        return cached
    rows, unresolved = pair_resolved_attempts(existing)

    buckets: dict[str, dict] = defaultdict(lambda: {"resolved": 0, "no_fills": 0})
    for row in rows:
        bucket = buckets[row["strategy"]]
        bucket["resolved"] += 1
        bucket["no_fills"] += int(row["no_fill"])

    by_setup = []
    for strategy, counts in buckets.items():
        resolved = counts["resolved"]
        no_fills = counts["no_fills"]
        by_setup.append(
            {
                "setup": strategy,
                "entry_type": "limit" if strategy in LIMIT_SETUPS else "stop_or_other",
                "resolved_attempts": resolved,
                "no_fills": no_fills,
                "no_fill_rate_pct": _rate(no_fills, resolved),
            }
        )
    by_setup.sort(key=lambda item: (-item["resolved_attempts"], item["setup"]))

    resolved = len(rows)
    no_fills = sum(int(row["no_fill"]) for row in rows)
    misses = [row for row in rows if row["no_fill"]]
    misses.reverse()
    payload = {
        "source": "journal_only",
        "window": {
            "days_requested": days,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "journal_files_found": len(existing),
            "resolved_attempts": resolved,
            "unresolved_attempts": unresolved,
        },
        "overall": {
            "resolved_attempts": resolved,
            "no_fills": no_fills,
            "no_fill_rate_pct": _rate(no_fills, resolved),
        },
        "by_setup": by_setup,
        "recent_no_fills": misses[:recent_limit],
        "limitations": [
            "Counts only TRADE decisions paired with a later journaled OUTCOME.",
            "Classifies a no-fill only from a CANCELLED outcome whose reason records ENTRY_NOT_FILLED or execution_failed:CANCELLED.",
            "Does not infer fills from signal close, later bars, intrabar prices, replay behavior, order-book data, or broker state outside the journal.",
            "Does not estimate whether a different entry price, momentum re-anchor, or execution policy would have filled.",
        ],
    }
    # Keep only the latest signature-keyed result. Any journal append changes its
    # mtime/size and naturally invalidates this read-only cache.
    _STATUS_CACHE.clear()
    _STATUS_CACHE[cache_key] = payload
    return payload
