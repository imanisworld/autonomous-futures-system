"""Read-only freshness census over every evidence collector on the box.

`ops.evidence_lane_health` already reports the MES/MNQ strategy lanes in
depth.  This module answers a different, blunter question -- *is every
collector we believe is running actually still writing?* -- across the lanes
that no other monitor watches: the options scanner and its shadow journal, the
options companion, the forward A/B campaign arms, and the scheduled cron
reports.

The gap this closes is concrete.  On 2026-08-13 the options shadow journal
stopped writing entirely and nothing noticed for twelve days, because every
health check we had covered the futures lanes only.

Deliberately stdlib-only and path-driven so it can be run directly against a
box whose release predates this file::

    python3 ops/collector_census.py --log-dir /root/afs-shared/logs

It never writes, never advances a collector, and never contacts a broker.
Exit status is 1 when any collector is DEAD, else 0.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

_CAMPAIGN_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "forward_evidence_campaign.json"


def _configured_campaign_populations() -> tuple[tuple[str, str], ...]:
    try:
        config = json.loads(_CAMPAIGN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ()
    return tuple(
        (str(row.get("strategy")), str(row.get("variant")))
        for row in (config.get("populations") or [])
        if isinstance(row, dict)
    )


FRESH = "FRESH"
STALE = "STALE"
DEAD = "DEAD"
ABSENT = "ABSENT"

# A collector is STALE past its expected cadence and DEAD past the point where
# a market-hours gap or a weekend can still explain the silence.
DEAD_MULTIPLE = 4

# Isolated hypothetical-ledger lanes (context/wide_stop_ledger_paper.JOURNAL_ROOT).
LEDGER_ROOT = "hypothetical_ledger"
HYPOTHETICAL_LANES: tuple[tuple[str, str, str], ...] = (
    # (lane, state file, kind)  kind: "swing_state" | "forward_state" | "lane_journal"
    ("daily_22_5k", "swing_state.json", "swing_state"),
    ("wide_stop_4k", "forward_collector_state.json", "forward_state"),
    ("wide_stop_6k", "forward_collector_state.json", "forward_state"),
    ("mes_122_1500", "", "lane_journal"),
)
LANE_JOURNAL_LOOKBACK_FILES = 8  # >= the runner's 7-calendar-day carry lookup


@dataclass(frozen=True)
class Collector:
    """One evidence stream and the cadence we expect it to keep."""

    name: str
    kind: str  # "daily_jsonl" | "jsonl" | "file" | "sqlite_table"
    target: str
    max_age_minutes: int
    note: str = ""
    # sqlite_table only
    table: str = ""
    time_columns: tuple[str, ...] = ("timestamp", "ts", "created_at", "observed_at")


COLLECTORS: tuple[Collector, ...] = (
    # --- futures runtime -------------------------------------------------
    Collector("futures journal", "daily_jsonl", "journal_{date}.jsonl", 30),
    Collector("bars MNQ", "daily_jsonl", "bars_MNQ_{date}.jsonl", 30),
    Collector("bars MES", "daily_jsonl", "bars_MES_{date}.jsonl", 30),
    Collector("strategy context", "jsonl", "strategy_context_observations.jsonl", 30),
    Collector("feed gap alarm", "file", "feed_gap_alarm_state.json", 15),
    # --- 5-minute feed + hypothetical-ledger lane heartbeats -------------
    # Every MNQ paper lane (Daily 2-2, wide-stop 4HR, wide-stop 3-2-2) resolves
    # on the 5-MINUTE stream under logs/tf5m/, not the 15m bars above. These
    # three files are heartbeats, not candidate files: the 5m bar file grows on
    # every MNQ 5m alert, the Daily 2-2 collector rewrites swing_state.json on
    # every MNQ 5m bar it processes (position open or flat), and the MES 1-2-2
    # lane journals a BAR_CLAIM + decision on every MES 15m bar. The wide-stop
    # ledgers have NO heartbeat file (state is written only on candidates) and
    # are reported through hypothetical_lane_positions() instead.
    Collector("bars MNQ 5m", "daily_jsonl", "tf5m/bars_MNQ_{date}.jsonl", 30),
    Collector(
        "daily_22 swing state",
        "file",
        f"{LEDGER_ROOT}/daily_22_5k/swing_state.json",
        30,
        note="rewritten on every MNQ 5m bar while WIDE_STOP_LEDGER_MODE=paper_sim",
    ),
    Collector(
        "mes_122 lane journal",
        "daily_jsonl",
        f"{LEDGER_ROOT}/mes_122_1500/journal_{{date}}.jsonl",
        30,
        note="BAR_CLAIM + decision on every MES 15m bar while MES_122_PAPER_MODE=paper_sim",
    ),
    # --- event-driven futures strategy evidence -------------------------
    # Do not assign wall-clock DEAD thresholds to candidate-driven files.
    # MNQ Strat / MES lane health is owned by ops.evidence_lane_health, which
    # separates feed health from NO_PATTERN_MATCHES. VWAP early health is
    # checked via its 5m feed/runtime prerequisites and campaign/raw evidence;
    # candidate-file silence alone is not a valid death signal.
    # --- forward A/B campaign -------------------------------------------
    Collector(
        "forward A/B campaign",
        "jsonl",
        "forward_ab_2026_08_v1.jsonl",
        2880,
        note="per-arm accrual reported separately",
    ),
    # --- options lane ----------------------------------------------------
    Collector(
        "options scans",
        "sqlite_table",
        "options_scanner.sqlite",
        30,
        table="scans",
    ),
    Collector(
        "options shadow journal",
        "sqlite_table",
        "options_scanner.sqlite",
        1440,
        table="options_shadow_journal",
        note="OPEN-gated; silence here also means zero eligible candidates",
    ),
    Collector(
        "options companion",
        "sqlite_table",
        "options_companion.sqlite",
        10080,
        table="options_companion",
    ),
    # --- scheduled reports ----------------------------------------------
    Collector("health digest", "file", "health_digest_latest.json", 1560),
    Collector("proof backup", "file", "proof_backup.log", 1560),
    Collector("companion daily", "file", "companion_daily.log", 4320),
    Collector("overnight watch", "file", "overnight_watch_summary.log", 60),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_minutes(moment: datetime, now: datetime) -> float:
    return (now - moment).total_seconds() / 60.0


def _classify(age: float | None, limit: int) -> str:
    if age is None:
        return ABSENT
    if age <= limit:
        return FRESH
    return DEAD if age > limit * DEAD_MULTIPLE else STALE


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _last_jsonl_timestamp(path: Path) -> datetime | None:
    """Timestamp of the final parseable record, without reading the whole file."""
    if not path.exists():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            window = min(size, 65536)
            handle.seek(size - window)
            tail = handle.read().decode("utf-8", "ignore")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        for key in ("ts", "timestamp", "observed_at", "signal_timestamp", "checked_utc"):
            parsed = _parse_ts(row.get(key))
            if parsed is not None:
                return parsed
    return None


def _mtime(path: Path) -> datetime | None:
    if not path.exists():
        return None
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def _sqlite_last(
    path: Path, table: str, columns: tuple[str, ...]
) -> tuple[datetime | None, int, bool]:
    """Return (newest timestamp, row count, table_exists).

    Row count is reported even when no timestamp column matches, so that a
    populated-but-frozen table is never mistaken for an absent one.
    """
    if not path.exists():
        return None, 0, False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return None, 0, False
    try:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not exists:
            return None, 0, False
        count = int(conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0])
        present = {row[1] for row in conn.execute(f"PRAGMA table_info([{table}])")}
        for column in columns:
            if column not in present:
                continue
            last = conn.execute(f"SELECT MAX([{column}]) FROM [{table}]").fetchone()[0]
            parsed = _parse_ts(last)
            if parsed is not None:
                return parsed, count, True
        return None, count, True
    except sqlite3.Error:
        return None, 0, False
    finally:
        conn.close()


def _resolve(collector: Collector, log_dir: Path, now: datetime) -> Path:
    target = collector.target
    if collector.kind == "daily_jsonl":
        target = target.format(date=now.strftime("%Y-%m-%d"))
    return log_dir / target


def check(collector: Collector, log_dir: Path, now: datetime) -> dict[str, Any]:
    path = _resolve(collector, log_dir, now)
    rows: int | None = None

    if collector.kind == "sqlite_table":
        last, rows, _exists = _sqlite_last(path, collector.table, collector.time_columns)
    elif collector.kind in {"jsonl", "daily_jsonl"}:
        last = _last_jsonl_timestamp(path)
        if last is None:
            # A daily file that has not been created yet is absent, not stale.
            last = _mtime(path) if collector.kind == "jsonl" else None
    else:
        last = _mtime(path)

    age = _age_minutes(last, now) if last else None
    return {
        "name": collector.name,
        "status": _classify(age, collector.max_age_minutes),
        "age_minutes": None if age is None else round(age, 1),
        "limit_minutes": collector.max_age_minutes,
        "last": last.isoformat() if last else None,
        "rows": rows,
        "path": str(path),
        "note": collector.note,
    }


def campaign_arms(log_dir: Path, now: datetime) -> dict[str, Any]:
    """Per-population accrual, including configured populations with zero rows."""
    configured = {
        f"{strategy}/{variant}": {
            "strategy": strategy,
            "variant": variant,
            "count": 0,
            "last": None,
        }
        for strategy, variant in _configured_campaign_populations()
    }
    unexpected: dict[str, dict[str, Any]] = {}
    path = log_dir / "forward_ab_2026_08_v1.jsonl"
    if path.exists():
        try:
            with path.open() as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("record_type") != "CANDIDATE":
                        continue
                    strategy = str(row.get("strategy") or "unknown")
                    variant = str(row.get("variant") or "unknown")
                    key = f"{strategy}/{variant}"
                    target = configured if key in configured else unexpected
                    entry = target.setdefault(
                        key,
                        {"strategy": strategy, "variant": variant, "count": 0, "last": None},
                    )
                    stamp = _parse_ts(row.get("signal_timestamp"))
                    entry["count"] += 1
                    if stamp and (entry["last"] is None or stamp > entry["last"]):
                        entry["last"] = stamp
        except OSError:
            pass

    for population in (configured, unexpected):
        for entry in population.values():
            last = entry["last"]
            entry["idle_hours"] = None if last is None else round(_age_minutes(last, now) / 60, 1)
            entry["last"] = last.isoformat() if last else None
    return {"configured": configured, "unexpected": unexpected}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _position_summary(position: Any) -> dict[str, Any] | None:
    if not isinstance(position, dict):
        return None
    keys = ("strategy", "direction", "entry", "stop", "target", "entry_time", "paper_order_id", "candidate_key")
    return {key: position.get(key) for key in keys if position.get(key) is not None}


def _mes_lane_open_position(lane_dir: Path) -> tuple[dict[str, Any] | None, datetime | None]:
    """Open strat_122 position from the lane's own journal_*.jsonl, and the newest row ts.

    Mirrors the runner's identity: a TRADE row with an APPROVED risk check opens a
    paper_order_id; an OUTCOME row carrying the same paper_order_id closes it. Only
    the last few day files are read (the runner's carry lookup is 7 calendar days).
    """
    paths = sorted(lane_dir.glob("journal_*.jsonl"))[-LANE_JOURNAL_LOOKBACK_FILES:]
    open_trades: dict[str, dict[str, Any]] = {}
    newest: datetime | None = None
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict):
                continue
            stamp = _parse_ts(row.get("ts"))
            if stamp and (newest is None or stamp > newest):
                newest = stamp
            if row.get("decision") == "TRADE" and (row.get("risk_check") or {}).get("result") == "APPROVED":
                order_id = str(row.get("paper_order_id") or "")
                setup = row.get("setup") if isinstance(row.get("setup"), dict) else {}
                if order_id:
                    open_trades[order_id] = {**setup, "paper_order_id": order_id, "entry_time": row.get("ts")}
            elif row.get("type") == "OUTCOME":
                order_id = str((row.get("outcome") or {}).get("paper_order_id") or "")
                open_trades.pop(order_id, None)
    if not open_trades:
        return None, newest
    # One open position at a time by contract; report the most recent if not.
    last_id = list(open_trades)[-1]
    return _position_summary(open_trades[last_id]), newest


def hypothetical_lane_positions(log_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    """Read-only inventory of the isolated hypothetical-ledger lanes.

    Answers, per lane: does its directory exist yet (they are created lazily), is a
    paper position OPEN right now, when was the lane's state/journal last touched,
    and -- the safety question -- is a position exposed while the 5-minute MNQ bar
    stream (which every MNQ lane resolves on) has gone quiet. `bars_stale` is a
    plain age test with no market-hours logic; the caller decides whether the
    market is open. Never writes.
    """
    now = now or _now()
    root = log_dir / LEDGER_ROOT
    five_min_bar = _last_jsonl_timestamp(log_dir / "tf5m" / f"bars_MNQ_{now.strftime('%Y-%m-%d')}.jsonl")
    five_min_age = _age_minutes(five_min_bar, now) if five_min_bar else None
    lanes: dict[str, Any] = {}
    for lane, state_name, kind in HYPOTHETICAL_LANES:
        lane_dir = root / lane
        entry: dict[str, Any] = {
            "lane": lane,
            "exists": lane_dir.is_dir(),
            "kind": kind,
            "open_position": None,
            "state_path": str(lane_dir / state_name) if state_name else None,
            "state_last": None,
            "state_age_minutes": None,
            "heartbeat": {
                "swing_state": "state file rewritten on every MNQ 5m bar",
                "forward_state": "none: state written only on candidate events",
                "lane_journal": "lane journal row on every MES 15m bar",
            }[kind],
            "halted": None,
            "epoch": None,
        }
        if kind == "swing_state":
            data = _read_json(lane_dir / state_name) or {}
            entry["open_position"] = _position_summary(data.get("position"))
            entry["halted"] = data.get("halted")
            entry["epoch"] = data.get("epoch")
            last = _mtime(lane_dir / state_name)
        elif kind == "forward_state":
            data = _read_json(lane_dir / state_name) or {}
            entry["open_position"] = _position_summary(data.get("position"))
            entry["filled_count"] = data.get("filled_count")
            entry["filled_date"] = data.get("filled_date")
            last = _mtime(lane_dir / state_name)
        else:
            entry["open_position"], last = _mes_lane_open_position(lane_dir)
        entry["state_last"] = last.isoformat() if last else None
        entry["state_age_minutes"] = round(_age_minutes(last, now), 1) if last else None
        lanes[lane] = entry
    open_lanes = [name for name, row in lanes.items() if row["open_position"]]
    mnq_open = [name for name in open_lanes if name != "mes_122_1500"]
    return {
        "five_min_bar_last": five_min_bar.isoformat() if five_min_bar else None,
        "five_min_bar_age_minutes": None if five_min_age is None else round(five_min_age, 1),
        "lanes": lanes,
        "open_positions": open_lanes,
        "mnq_position_exposed_without_fresh_5m_bars": bool(mnq_open) and (
            five_min_age is None or five_min_age > 30
        ),
    }


def build_census(log_dir: Path, now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    results = [check(c, log_dir, now) for c in COLLECTORS]
    counts: dict[str, int] = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "generated_utc": now.isoformat(),
        "log_dir": str(log_dir),
        "collectors": results,
        "campaign_arms": campaign_arms(log_dir, now),
        "hypothetical_lanes": hypothetical_lane_positions(log_dir, now),
        "counts": counts,
        "dead": [r["name"] for r in results if r["status"] in {DEAD, ABSENT}],
    }


def format_census(census: dict[str, Any]) -> str:
    lines = [
        f"COLLECTOR CENSUS  {census['generated_utc']}",
        f"log-dir: {census['log_dir']}",
        "",
        f"{'status':<7} {'collector':<28} {'age':>10}  {'limit':>7}  rows",
        "-" * 72,
    ]
    order = {DEAD: 0, ABSENT: 1, STALE: 2, FRESH: 3}
    for row in sorted(census["collectors"], key=lambda r: (order[r["status"]], r["name"])):
        age = "never" if row["age_minutes"] is None else f"{row['age_minutes']:.0f}m"
        rows = "-" if row["rows"] is None else str(row["rows"])
        lines.append(
            f"{row['status']:<7} {row['name']:<28} {age:>10}  {row['limit_minutes']:>6}m  {rows}"
        )
        if row["note"] and row["status"] != FRESH:
            lines.append(f"{'':<7} {'':<28} -> {row['note']}")

    campaign = census["campaign_arms"]
    configured_arms = campaign.get("configured", {})
    unexpected_arms = campaign.get("unexpected", {})
    if configured_arms:
        lines += ["", "forward A/B configured populations:"]
        for arm, entry in sorted(configured_arms.items()):
            idle = "never" if entry["idle_hours"] is None else f"{entry['idle_hours']:.0f}h idle"
            lines.append(f"  {arm:<28} candidates={entry['count']:<4} {idle}")
    if unexpected_arms:
        lines += ["", "forward A/B UNEXPECTED populations:"]
        for arm, entry in sorted(unexpected_arms.items()):
            idle = "never" if entry["idle_hours"] is None else f"{entry['idle_hours']:.0f}h idle"
            lines.append(f"  {arm:<28} candidates={entry['count']:<4} {idle}")

    lanes = census.get("hypothetical_lanes") or {}
    if lanes:
        lines += ["", "hypothetical-ledger lanes:"]
        for name, row in sorted((lanes.get("lanes") or {}).items()):
            if not row.get("exists"):
                lines.append(f"  {name:<28} directory not created yet (lazy; no candidate so far)")
                continue
            pos = row.get("open_position") or {}
            state = "never" if row.get("state_age_minutes") is None else f"{row['state_age_minutes']:.0f}m"
            held = (
                f"OPEN {pos.get('direction')} @ {pos.get('entry')} since {pos.get('entry_time')}"
                if pos else "flat"
            )
            lines.append(f"  {name:<28} {held:<50} state {state}")
        age = lanes.get("five_min_bar_age_minutes")
        lines.append(
            f"  5m MNQ bar age: {'never' if age is None else f'{age:.0f}m'}"
            + ("  ** MNQ POSITION EXPOSED WITHOUT FRESH 5m BARS **" if lanes.get("mnq_position_exposed_without_fresh_5m_bars") else "")
        )

    summary = ", ".join(f"{k}={v}" for k, v in sorted(census["counts"].items()))
    lines += ["", f"summary: {summary}"]
    if census["dead"]:
        lines.append("DEAD/ABSENT: " + ", ".join(census["dead"]))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--log-dir", default="logs", type=Path)
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args(argv)

    census = build_census(args.log_dir)
    print(json.dumps(census, indent=2) if args.json else format_census(census))
    return 1 if census["dead"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
