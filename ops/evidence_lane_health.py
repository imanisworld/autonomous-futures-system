"""Read-only daily health snapshot for the MES and MNQ evidence lanes.

This module deliberately composes the existing lane monitors.  It does not
advance collectors, repair state, contact a broker, or write any files.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from execution.mes_trend_consolidation_break_evidence import (
    lane_mode as mes_lane_mode,
    state_path as mes_state_path,
)
from execution.mnq_strat_evidence import (
    LANES,
    lane_mode as mnq_lane_mode,
    state_path as mnq_state_path,
)
from ops.feed_gap_alarm import (
    THRESHOLD_MIN,
    last_reopen,
    market_open,
    newest_15m_bar,
)
from ops.mes_trend_consolidation_break_monitor import (
    read_events as mes_events,
    summarize_lane as summarize_mes,
)
from ops.mnq_strat_evidence_monitor import (
    read_events as mnq_events,
    summarize_lane as summarize_mnq,
)


def _parse_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _event_timestamp(row: dict[str, Any]) -> datetime | None:
    event = row.get("event")
    fields = {
        "CANDIDATE": ("timestamp", "signal_ts"),
        "FILL": ("entry_ts", "timestamp"),
        "OUTCOME": ("exit_ts", "timestamp"),
        "NO_FILL": ("resolved_at", "timestamp"),
        "RUNNER_MOVE": ("ts", "timestamp"),
    }.get(event, ("timestamp",))
    for field in (*fields, "observed_at"):
        parsed = _parse_timestamp(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _on_date(rows: Iterable[dict[str, Any]], day: date) -> list[dict[str, Any]]:
    return [row for row in rows if (ts := _event_timestamp(row)) and ts.date() == day]


def _read_state(path: Path) -> dict[str, Any]:
    import json

    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _feed_health(log_dir: Path, instrument: str, now: datetime, day: date) -> dict[str, Any]:
    current_day = now.date()
    if day != current_day:
        return {
            "status": "NOT_EVALUATED",
            "last_bar": None,
            "age_minutes": None,
            "reason": "feed freshness is only meaningful for the current date",
        }
    if not market_open(now):
        return {
            "status": "MARKET_CLOSED",
            "last_bar": None,
            "age_minutes": None,
            "reason": "CME Globex is closed; silence is expected",
        }
    newest = newest_15m_bar(log_dir, instrument)
    # Match the feed-gap monitor's Sunday/daily-reopen grace.  An old Friday
    # bar must not make a healthy Sunday reopen look blocked immediately.
    baseline = max(newest, last_reopen(now)) if newest else last_reopen(now)
    age = (now - baseline).total_seconds() / 60.0
    stale = age > THRESHOLD_MIN
    return {
        "status": "STALE" if stale else "FRESH",
        "last_bar": newest.isoformat() if newest else None,
        "age_minutes": round(age, 1) if age is not None else None,
        "reason": (
            ("no 15-minute bar history found" if stale else "within market-reopen grace")
            if newest is None else f"freshness baseline is {age:.1f} minutes old"
        ),
    }


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    candidates = [row for row in rows if row.get("event") == "CANDIDATE"]
    outcomes = [row for row in rows if row.get("event") == "OUTCOME"]
    terminal_no_fills = sum(row.get("event") == "NO_FILL" for row in rows)
    candidate_no_fills = sum(row.get("fill_status") == "NO_FILL" for row in candidates)
    observe_only_no_orders = sum(
        row.get("accepted") is True
        and row.get("mode") == "observe_only"
        and row.get("fill_status") == "NO_FILL"
        for row in candidates
    )
    return {
        "candidates": len(candidates),
        "accepted": sum(row.get("accepted") is True for row in candidates),
        "rejected": sum(row.get("accepted") is False for row in candidates),
        "fills": sum(row.get("event") == "FILL" for row in rows)
        + sum(row.get("fill_status") == "FILLED" for row in candidates),
        # Compatibility total. The components below distinguish an intentional
        # observe-only no-order from a terminal paper no-fill.
        "no_fills": terminal_no_fills + candidate_no_fills,
        "terminal_no_fills": terminal_no_fills,
        "observe_only_no_orders": observe_only_no_orders,
        "outcomes": len(outcomes),
        "wins": sum(row.get("result") == "WIN" for row in outcomes),
        "losses": sum(row.get("result") == "LOSS" for row in outcomes),
        "breakevens": sum(row.get("result") == "BREAKEVEN" for row in outcomes),
    }


def _lane_status(
    *,
    counts: dict[str, int],
    mode: str,
    feed: dict[str, Any],
    state: dict[str, Any],
    rejections: Counter[str],
) -> tuple[str, list[str]]:
    signals: list[str] = []
    pending = isinstance(state.get("pending_order"), dict)
    open_position = isinstance(state.get("position"), dict)

    if feed["status"] == "STALE":
        signals.append("FEED_STALE")
    if counts["rejected"]:
        signals.append(f"CANDIDATE_REJECTIONS:{counts['rejected']}")
    if counts["candidates"] and counts["accepted"] == 0:
        signals.append("ALL_CANDIDATES_REJECTED")
    if rejections:
        signals.append(f"TOP_REJECTION:{rejections.most_common(1)[0][0]}")
    if pending:
        signals.append("PENDING_NEXT_BAR_FILL_CHECK")
    if open_position:
        signals.append("OPEN_PAPER_POSITION")

    if mode == "paper_sim" and counts["accepted"] > counts["fills"]:
        if not pending:
            signals.append("FILL_STARVATION")
    elif mode == "observe_only" and counts["accepted"]:
        signals.append("OBSERVE_ONLY_NO_FILLS_EXPECTED")

    unresolved = counts["fills"] - counts["outcomes"]
    if unresolved > 0 and not open_position:
        signals.append(f"UNRESOLVED_FILLS_WITHOUT_OPEN_STATE:{unresolved}")

    if feed["status"] == "STALE" or (
        counts["candidates"] and counts["accepted"] == 0
    ):
        return "BLOCKED", signals
    if "FILL_STARVATION" in signals or any(
        signal.startswith("UNRESOLVED_FILLS_WITHOUT_OPEN_STATE") for signal in signals
    ):
        return "STARVED", signals
    if pending:
        return "PENDING", signals
    if open_position:
        return "OPEN", signals
    if counts["candidates"]:
        return "ACTIVE", signals
    if feed["status"] == "MARKET_CLOSED":
        signals.append("NO_ACTIVITY_EXPECTED_MARKET_CLOSED")
        return "CLOSED", signals
    signals.append("NO_PATTERN_MATCHES")
    return "QUIET", signals


def _lane_snapshot(
    *,
    instrument: str,
    lane: str,
    mode: str,
    rows: list[dict[str, Any]],
    lifetime: dict[str, Any],
    state: dict[str, Any],
    feed: dict[str, Any],
    day: date,
) -> dict[str, Any]:
    daily_rows = _on_date(rows, day)
    counts = _counts(daily_rows)
    rejections: Counter[str] = Counter()
    for row in daily_rows:
        if row.get("event") != "CANDIDATE":
            continue
        for reason in str(row.get("rejection_reason") or "").split(";"):
            if reason.strip():
                rejections[reason.strip()] += 1
    status, signals = _lane_status(
        counts=counts,
        mode=mode,
        feed=feed,
        state=state,
        rejections=rejections,
    )
    return {
        "instrument": instrument,
        "lane": lane,
        "mode": mode,
        "status": status,
        "counts": counts,
        "open_state": {
            "pending_order": isinstance(state.get("pending_order"), dict),
            "position": isinstance(state.get("position"), dict),
        },
        "signals": signals,
        "rejection_reasons": dict(rejections.most_common()),
        "lifetime": {
            key: lifetime.get(key)
            for key in (
                "candidate_count", "accepted_count", "rejected_count",
                "fill_count", "no_fill_count", "wins", "losses", "breakevens",
            )
        },
    }


def build_snapshot(
    log_dir: str | Path = "logs",
    *,
    day: date | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one deterministic, read-only operational snapshot."""
    root = Path(log_dir)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    day = day or now.date()
    feeds = {
        instrument: _feed_health(root, instrument, now, day)
        for instrument in ("MES", "MNQ")
    }
    lanes = [
        _lane_snapshot(
            instrument="MES",
            lane="trend_consolidation_break",
            mode=mes_lane_mode(),
            rows=mes_events(root),
            lifetime=summarize_mes(root),
            state=(
                _read_state(mes_state_path(root)) if day == now.date() else {}
            ),
            feed=feeds["MES"],
            day=day,
        )
    ]
    lanes.extend(
        _lane_snapshot(
            instrument="MNQ",
            lane=lane,
            mode=mnq_lane_mode(lane),
            rows=mnq_events(root, lane),
            lifetime=summarize_mnq(root, lane),
            state=(
                _read_state(mnq_state_path(root, lane))
                if day == now.date() else {}
            ),
            feed=feeds["MNQ"],
            day=day,
        )
        for lane in LANES
    )
    total_keys = tuple(_counts([]))
    totals = {
        key: sum(int(item["counts"][key]) for item in lanes) for key in total_keys
    }
    status_counts = Counter(item["status"] for item in lanes)
    if status_counts["BLOCKED"]:
        overall = "BLOCKED"
    elif status_counts["STARVED"]:
        overall = "STARVED"
    elif status_counts["PENDING"] or status_counts["OPEN"]:
        overall = "IN_FLIGHT"
    elif status_counts["ACTIVE"]:
        overall = "ACTIVE"
    elif status_counts["CLOSED"] == len(lanes):
        overall = "MARKET_CLOSED"
    else:
        overall = "QUIET"
    return {
        "generated_at": now.isoformat(),
        "date": day.isoformat(),
        "read_only": True,
        "overall_status": overall,
        "market_open": market_open(now) if day == now.date() else None,
        "feeds": feeds,
        "totals": totals,
        "status_counts": dict(sorted(status_counts.items())),
        "lanes": lanes,
    }


def format_snapshot(snapshot: dict[str, Any]) -> str:
    """Render a compact operator view; JSON remains available to callers."""
    feeds = snapshot["feeds"]
    lines = [
        f"Evidence lane health | {snapshot['date']} | {snapshot['overall_status']}",
        "Feed: " + " | ".join(
            f"{instrument} {value['status']}"
            + (f" ({value['age_minutes']:.1f}m)" if value["age_minutes"] is not None else "")
            for instrument, value in feeds.items()
        ),
        "",
        "LANE                              MODE         HEALTH    CAND  A/R   FILL NF  OUT  W/L/BE",
    ]
    for lane in snapshot["lanes"]:
        counts = lane["counts"]
        name = f"{lane['instrument']} {lane['lane']}"
        lines.append(
            f"{name[:33]:33} {lane['mode'][:12]:12} {lane['status'][:9]:9} "
            f"{counts['candidates']:4}  {counts['accepted']}/{counts['rejected']:<3} "
            f"{counts['fills']:4} {counts['no_fills']:2}  {counts['outcomes']:3}  "
            f"{counts['wins']}/{counts['losses']}/{counts['breakevens']}"
        )
        if lane["signals"]:
            lines.append(f"  -> {', '.join(lane['signals'])}")
    totals = snapshot["totals"]
    lines.extend([
        "",
        "Total: "
        f"{totals['candidates']} candidates, {totals['accepted']} accepted, "
        f"{totals['rejected']} rejected, {totals['fills']} fills, "
        f"{totals['no_fills']} no-fills, {totals['outcomes']} outcomes "
        f"({totals['wins']}W/{totals['losses']}L/{totals['breakevens']}BE)",
    ])
    return "\n".join(lines)
