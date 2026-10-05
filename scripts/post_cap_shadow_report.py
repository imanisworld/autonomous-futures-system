#!/usr/bin/env python3
"""Read-only report for setups observed after the daily execution cap is exhausted.

The live runner already preserves a BLOCKED_MAX_TRADES journal row for every
post-cap bar and records the selected setup when one exists. This tool does not
change that runtime path. It reads those rows after the fact, assigns ordinal
labels (#4, #5, #6, ... when the configured execution cap is 3), and resolves
each setup against persisted causal BarHistory using the repository's existing
OpportunityTracker outcome model.

Important boundaries:
- no broker imports or broker calls;
- no config/env mutation;
- no journal/bar-history mutation;
- no automatic cap change or strategy promotion;
- outcome math is the existing OpportunityTracker counterfactual model
  (1 adverse tick entry/stop slippage, $5 round-trip commission, pessimistic
  same-bar stop+target), not a claim of exact broker-fill parity.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from adaptive.opportunity_tracker import (
    DEFAULT_COMMISSION_RT,
    DEFAULT_SLIPPAGE_TICKS,
    OpportunityCandidate,
    resolve_outcome,
)

BLOCK_CODE = "BLOCKED_MAX_TRADES"
OBSERVED_TRADE = "TRADE"
EVIDENCE_MODEL = "opportunity_tracker_v1"
HORIZON_HOURS = 8


def _parse_ts(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_ts(row: dict[str, Any]) -> datetime:
    for key in ("timestamp", "bar_ts", "ts"):
        if row.get(key):
            return _parse_ts(row[key])
    raise ValueError("blocked row has no timestamp/bar_ts/ts")


def _timeframe_minutes(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
        return number if number > 0 else None
    text = str(value).strip().lower()
    aliases = {
        "1": 1,
        "1m": 1,
        "1min": 1,
        "5": 5,
        "5m": 5,
        "5min": 5,
        "15": 15,
        "15m": 15,
        "15min": 15,
        "30": 30,
        "30m": 30,
        "30min": 30,
        "60": 60,
        "60m": 60,
        "1h": 60,
        "240": 240,
        "240m": 240,
        "4h": 240,
    }
    if text in aliases:
        return aliases[text]
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        number = int(digits)
        return number if number > 0 else None
    return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{lineno}: invalid JSON: {exc}") from exc
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _journal_paths(log_dir: Path, requested_date: date | None) -> list[Path]:
    if requested_date is not None:
        return [log_dir / f"journal_{requested_date.isoformat()}.jsonl"]
    return sorted(log_dir.glob("journal_*.jsonl"))


def collect_post_cap_rows(
    log_dir: str | Path,
    *,
    requested_date: date | None = None,
    expected_cap: int = 3,
) -> list[dict[str, Any]]:
    """Return selected setups observed after the execution cap.

    The runner-level BLOCKED_MAX_TRADES gate occurs before RiskEngine/broker
    construction. A valid post-cap setup is therefore represented by:
      decision == BLOCKED_MAX_TRADES
      observed_decision == TRADE
      setup is a dict
      execution_block.limit == expected_cap

    Rows are numbered per journal day in causal timestamp order. With cap=3,
    the first observed setup is opportunity #4, then #5, #6, ...
    """
    root = Path(log_dir)
    out: list[dict[str, Any]] = []
    if expected_cap <= 0:
        raise ValueError("expected_cap must be positive")

    for path in _journal_paths(root, requested_date):
        day_text = path.stem.removeprefix("journal_")
        try:
            journal_day = date.fromisoformat(day_text)
        except ValueError:
            continue

        eligible: list[tuple[datetime, dict[str, Any]]] = []
        for row in _read_jsonl(path):
            if row.get("decision") != BLOCK_CODE:
                continue
            if row.get("observed_decision") != OBSERVED_TRADE:
                continue
            setup = row.get("setup")
            if not isinstance(setup, dict):
                continue
            block = row.get("execution_block")
            if not isinstance(block, dict):
                raise ValueError(f"{path}: BLOCKED_MAX_TRADES row missing execution_block")
            limit = block.get("limit")
            if isinstance(limit, bool):
                raise ValueError(f"{path}: invalid execution_block.limit={limit!r}")
            try:
                limit_int = int(limit)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: invalid execution_block.limit={limit!r}") from exc
            if limit_int != expected_cap:
                raise ValueError(
                    f"{path}: cap mismatch: row limit={limit_int}, expected={expected_cap}"
                )
            eligible.append((_row_ts(row), row))

        eligible.sort(key=lambda item: item[0])
        for index, (detected_at, row) in enumerate(eligible, start=1):
            setup = dict(row["setup"])
            timeframe_minutes = _timeframe_minutes(
                row.get("timeframe_minutes")
                or row.get("timeframe")
                or setup.get("timeframe")
            )
            out.append(
                {
                    "journal_day": journal_day.isoformat(),
                    "detected_at": detected_at.isoformat(),
                    "execution_cap": expected_cap,
                    "post_cap_index": index,
                    "opportunity_number": expected_cap + index,
                    "instrument": str(row.get("instrument") or ""),
                    "session": row.get("session"),
                    "timeframe_minutes": timeframe_minutes,
                    "strategy": str(setup.get("strategy") or ""),
                    "direction": str(setup.get("direction") or ""),
                    "entry": setup.get("entry"),
                    "stop": setup.get("stop"),
                    "target": setup.get("target"),
                    "rr_ratio": setup.get("rr_ratio"),
                    "source_decision": BLOCK_CODE,
                    "observed_decision": OBSERVED_TRADE,
                    "event_id": row.get("event_id"),
                }
            )
    return out


def _load_future_bars(
    log_dir: Path,
    *,
    instrument: str,
    detected_at: datetime,
    expires_at: datetime,
    timeframe_minutes: int | None,
) -> list[dict[str, Any]]:
    bars: list[tuple[datetime, dict[str, Any]]] = []
    for path in sorted(log_dir.glob(f"bars_{instrument}_*.jsonl")):
        for bar in _read_jsonl(path):
            if not bar.get("ts"):
                continue
            try:
                ts = _parse_ts(bar["ts"])
            except ValueError:
                continue
            if not (detected_at < ts <= expires_at):
                continue
            if timeframe_minutes is not None:
                bar_tf = _timeframe_minutes(bar.get("timeframe"))
                if bar_tf != timeframe_minutes:
                    continue
            if not all(key in bar for key in ("high", "low")):
                continue
            copied = dict(bar)
            copied["ts"] = ts.isoformat()
            bars.append((ts, copied))
    bars.sort(key=lambda item: item[0])
    return [bar for _, bar in bars]


def _latest_matching_bar_ts(
    log_dir: Path,
    *,
    instrument: str,
    timeframe_minutes: int | None,
) -> datetime | None:
    latest: datetime | None = None
    for path in sorted(log_dir.glob(f"bars_{instrument}_*.jsonl")):
        for bar in _read_jsonl(path):
            if not bar.get("ts"):
                continue
            if timeframe_minutes is not None:
                if _timeframe_minutes(bar.get("timeframe")) != timeframe_minutes:
                    continue
            try:
                ts = _parse_ts(bar["ts"])
            except ValueError:
                continue
            if latest is None or ts > latest:
                latest = ts
    return latest


def resolve_post_cap_rows(
    rows: Iterable[dict[str, Any]],
    log_dir: str | Path,
) -> list[dict[str, Any]]:
    root = Path(log_dir)
    resolved: list[dict[str, Any]] = []

    for source in rows:
        row = dict(source)
        detected_at = _parse_ts(row["detected_at"])
        expires_at = detected_at + timedelta(hours=HORIZON_HOURS)
        instrument = str(row.get("instrument") or "")
        direction = str(row.get("direction") or "")
        strategy = str(row.get("strategy") or "")
        timeframe_minutes = row.get("timeframe_minutes")

        required = {
            "instrument": instrument,
            "direction": direction,
            "strategy": strategy,
            "entry": row.get("entry"),
            "stop": row.get("stop"),
            "target": row.get("target"),
        }
        missing = [key for key, value in required.items() if value in (None, "")]
        if missing:
            row.update(
                {
                    "evidence_status": "INVALID_SETUP",
                    "missing_fields": missing,
                    "outcome": None,
                }
            )
            resolved.append(row)
            continue

        candidate = OpportunityCandidate(
            candidate_id=OpportunityCandidate.make_id(
                instrument,
                row["detected_at"],
                strategy,
                direction,
            ),
            source_bar_id=row["detected_at"],
            detected_at=row["detected_at"],
            instrument=instrument,
            session=str(row.get("session") or ""),
            timeframe=str(timeframe_minutes or ""),
            strategy=strategy,
            direction=direction,
            entry=float(row["entry"]),
            stop=float(row["stop"]),
            target=float(row["target"]),
            failed_gates=[BLOCK_CODE],
            risk_failed_rule=None,
            block_type="RISK_REJECTED",
            status="PENDING",
            expires_at=expires_at.isoformat(),
            selected=True,
            attempted=False,
            reject_code=BLOCK_CODE,
            reject_reason="Daily execution cap reached; observation only.",
        )
        if not candidate.has_valid_bracket():
            row.update(
                {
                    "evidence_status": "INVALID_BRACKET",
                    "missing_fields": [],
                    "outcome": None,
                }
            )
            resolved.append(row)
            continue

        future_bars = _load_future_bars(
            root,
            instrument=instrument,
            detected_at=detected_at,
            expires_at=expires_at,
            timeframe_minutes=timeframe_minutes,
        )
        latest_bar = _latest_matching_bar_ts(
            root,
            instrument=instrument,
            timeframe_minutes=timeframe_minutes,
        )
        if not future_bars:
            row.update(
                {
                    "evidence_status": "PENDING_NO_MATCHING_BARS",
                    "horizon_expires_at": expires_at.isoformat(),
                    "outcome": None,
                }
            )
            resolved.append(row)
            continue

        outcome = resolve_outcome(candidate, future_bars)
        terminal = outcome.result in {"TARGET_HIT", "STOP_HIT"}
        horizon_complete = latest_bar is not None and latest_bar >= expires_at
        if not terminal and not horizon_complete:
            row.update(
                {
                    "evidence_status": "PENDING",
                    "horizon_expires_at": expires_at.isoformat(),
                    "outcome": outcome.to_dict(),
                }
            )
            resolved.append(row)
            continue

        row.update(
            {
                "evidence_status": "FINAL",
                "horizon_expires_at": expires_at.isoformat(),
                "outcome": outcome.to_dict(),
            }
        )
        resolved.append(row)

    return resolved


def _bucket(number: int) -> str:
    if number == 4:
        return "4"
    if number == 5:
        return "5"
    return "6_plus"


def _summarize_group(rows: list[dict[str, Any]]) -> dict[str, Any]:
    final = [row for row in rows if row.get("evidence_status") == "FINAL"]
    outcomes = [row.get("outcome") or {} for row in final]
    filled_terminal = [
        outcome for outcome in outcomes if outcome.get("result") in {"TARGET_HIT", "STOP_HIT"}
    ]
    wins = sum(outcome.get("result") == "TARGET_HIT" for outcome in filled_terminal)
    losses = sum(outcome.get("result") == "STOP_HIT" for outcome in filled_terminal)
    pnl = round(sum(float(outcome.get("pnl_dollars") or 0.0) for outcome in filled_terminal), 2)
    gross_profit = sum(
        float(outcome.get("pnl_dollars") or 0.0)
        for outcome in filled_terminal
        if float(outcome.get("pnl_dollars") or 0.0) > 0
    )
    gross_loss = abs(
        sum(
            float(outcome.get("pnl_dollars") or 0.0)
            for outcome in filled_terminal
            if float(outcome.get("pnl_dollars") or 0.0) < 0
        )
    )
    return {
        "observed_setups": len(rows),
        "final": len(final),
        "pending": sum(str(row.get("evidence_status", "")).startswith("PENDING") for row in rows),
        "invalid": sum(str(row.get("evidence_status", "")).startswith("INVALID") for row in rows),
        "filled_terminal": len(filled_terminal),
        "wins": wins,
        "losses": losses,
        "entry_not_touched": sum(
            outcome.get("result") == "ENTRY_NOT_TOUCHED" for outcome in outcomes
        ),
        "expired_open": sum(outcome.get("result") == "EXPIRED_OPEN" for outcome in outcomes),
        "win_rate_percent": round(wins / len(filled_terminal) * 100.0, 2)
        if filled_terminal
        else None,
        "terminal_pnl_dollars": pnl,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
    }


def build_report(
    log_dir: str | Path,
    *,
    requested_date: date | None = None,
    expected_cap: int = 3,
) -> dict[str, Any]:
    source_rows = collect_post_cap_rows(
        log_dir,
        requested_date=requested_date,
        expected_cap=expected_cap,
    )
    rows = resolve_post_cap_rows(source_rows, log_dir)
    by_bucket: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_bucket[_bucket(int(row["opportunity_number"]))].append(row)
    return {
        "schema_version": 1,
        "read_only": True,
        "execution_authority": False,
        "actual_execution_cap": expected_cap,
        "collection_policy": "ALL_POST_CAP_SELECTED_SETUPS",
        "report_buckets": ["4", "5", "6_plus", "all_post_cap"],
        "evidence_model": {
            "name": EVIDENCE_MODEL,
            "horizon_hours": HORIZON_HOURS,
            "slippage_ticks": DEFAULT_SLIPPAGE_TICKS,
            "commission_rt_dollars": DEFAULT_COMMISSION_RT,
            "pessimistic_same_bar": True,
            "broker_fill_parity_claimed": False,
        },
        "summary": {
            "4": _summarize_group(by_bucket.get("4", [])),
            "5": _summarize_group(by_bucket.get("5", [])),
            "6_plus": _summarize_group(by_bucket.get("6_plus", [])),
            "all_post_cap": _summarize_group(rows),
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Report all selected setups observed after the daily execution cap."
    )
    parser.add_argument("--log-dir", default="logs")
    parser.add_argument("--date", help="Journal date YYYY-MM-DD; default = all available days")
    parser.add_argument("--expected-cap", type=int, default=3)
    parser.add_argument("--output", help="Optional JSON output path; default = stdout")
    args = parser.parse_args()

    requested_date = date.fromisoformat(args.date) if args.date else None
    report = build_report(
        args.log_dir,
        requested_date=requested_date,
        expected_cap=args.expected_cap,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
