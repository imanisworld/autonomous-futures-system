"""Read-only EOD/EOW evidence rollups for futures and options.

This module does not collect market data, mutate strategy state, place orders,
or promote strategies. It summarizes evidence that existing collectors have
already written and posts the summaries directly to the dedicated optional
Discord webhook environment variables:

- ``DISCORD_ROUTE_PAPER_COLLECTION_FUTURES``
- ``DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS``

Usage::

    python -m scripts.paper_collection_report --period eod
    python -m scripts.paper_collection_report --period eow

The report is intentionally conservative: missing or unreadable inputs are
shown explicitly instead of inferred as zero activity. EOW additionally
includes a read-only master evidence registry so active populations and gaps do
not disappear across separate collectors.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from notifications import plain_english as pe
from ops.evidence_registry import REGISTRY_HEADER, build_registry, format_registry_lines


NY_TZ = ZoneInfo("America/New_York")
RTH_CLOSE = time(16, 0)
# Collectors whose silence is by design; never raised as attention.
EXPECTED_QUIET = {
    "options companion": "turned off on purpose",
}
# Collectors that only advance during regular trading hours; judged against the
# session close of the report window, not against the wall clock at 17:10 ET.
SESSION_BOUND = {"options scans"}

FUTURES_ENV = "DISCORD_ROUTE_PAPER_COLLECTION_FUTURES"
OPTIONS_ENV = "DISCORD_ROUTE_PAPER_COLLECTION_OPTIONS"


def period_bounds(ref: date, period: str) -> tuple[date, date]:
    if period == "eod":
        return ref, ref
    if period == "eow":
        monday = ref - timedelta(days=ref.weekday())
        return monday, ref
    raise ValueError(f"unsupported period: {period}")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows


def _futures_rows(log_dir: Path, start: date, end: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    day = start
    while day <= end:
        rows.extend(_read_jsonl(log_dir / f"journal_{day.isoformat()}.jsonl"))
        day += timedelta(days=1)
    return rows


def summarize_futures(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Count what the futures journal actually writes.

    Three row shapes share the file: decision rows (``decision`` set, no
    ``type``), ``type=BAR_CLAIM`` bar claims, and ``type=SHADOW_OUTCOME`` rows
    that carry ``strategy``/``lane`` and a nested ``shadow_outcome.result``.
    Shadow outcomes are paper resolutions of observed setups — not fills.
    """
    row_types: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    shadow_results: Counter[str] = Counter()
    shadow_strategies: Counter[str] = Counter()
    shadow_lanes: Counter[str] = Counter()
    instruments: Counter[str] = Counter()
    for row in rows:
        row_type = row.get("type") or ("DECISION" if row.get("decision") else "OTHER")
        row_types[str(row_type)] += 1
        decision = row.get("decision")
        if decision:
            decisions[str(decision)] += 1
        if row_type == "SHADOW_OUTCOME":
            shadow = row.get("shadow_outcome")
            result = shadow.get("result") if isinstance(shadow, dict) else None
            shadow_results[str(result or "UNKNOWN")] += 1
            strategy = row.get("strategy")
            if strategy:
                shadow_strategies[str(strategy)] += 1
            lane = row.get("lane")
            if lane:
                shadow_lanes[str(lane)] += 1
        instrument = row.get("instrument") or row.get("ticker")
        if instrument:
            instruments[str(instrument)] += 1
    return {
        "rows": len(rows),
        "row_types": dict(row_types),
        "decisions": dict(decisions),
        "shadow_outcomes": dict(shadow_results),
        "shadow_strategies": dict(shadow_strategies),
        "shadow_lanes": dict(shadow_lanes),
        "instruments": dict(instruments),
    }


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row[1]) for row in conn.execute(f"PRAGMA table_info([{table}])")}
    except sqlite3.Error:
        return set()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None
    except sqlite3.Error:
        return False


def _count_table_range(
    conn: sqlite3.Connection, table: str, start: date, end: date
) -> dict[str, Any]:
    if not _table_exists(conn, table):
        return {"status": "MISSING_TABLE", "rows": None}
    columns = _table_columns(conn, table)
    timestamp_column = next(
        (c for c in ("timestamp", "created_at", "observed_at", "ts") if c in columns),
        None,
    )
    if timestamp_column is None:
        try:
            total = int(conn.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0])
        except sqlite3.Error:
            return {"status": "QUERY_ERROR", "rows": None}
        return {"status": "NO_TIMESTAMP_COLUMN", "rows": total}
    try:
        cur = conn.execute(
            f"SELECT * FROM [{table}] WHERE substr([{timestamp_column}], 1, 10) BETWEEN ? AND ?",
            (start.isoformat(), end.isoformat()),
        )
        names = [d[0] for d in (cur.description or [])]
        rows = cur.fetchall()
    except sqlite3.Error:
        return {"status": "QUERY_ERROR", "rows": None}

    status_counts: Counter[str] = Counter()
    if "status" in names:
        idx = names.index("status")
        for row in rows:
            status_counts[str(row[idx] or "UNKNOWN")] += 1
    return {
        "status": "OK",
        "rows": len(rows),
        "status_counts": dict(status_counts),
        "timestamp_column": timestamp_column,
    }


def summarize_options(sqlite_path: Path, start: date, end: date) -> dict[str, Any]:
    if not sqlite_path.exists():
        return {"status": "MISSING_DB", "path": str(sqlite_path), "tables": {}}
    try:
        conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {"status": "OPEN_ERROR", "path": str(sqlite_path), "tables": {}}
    try:
        tables = {
            name: _count_table_range(conn, name, start, end)
            for name in ("scans", "options_shadow_journal")
        }
    finally:
        conn.close()
    return {"status": "OK", "path": str(sqlite_path), "tables": tables}


def run_collector_census(log_dir: Path) -> dict[str, Any]:
    """Run the existing read-only census and return its JSON payload."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ops.collector_census", "--log-dir", str(log_dir), "--json"],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "ERROR", "error": type(exc).__name__}
    try:
        payload = json.loads(proc.stdout)
    except ValueError:
        return {"status": "ERROR", "exit_code": proc.returncode, "error": "invalid_json"}
    if isinstance(payload, dict):
        payload.setdefault("exit_code", proc.returncode)
        payload.setdefault("status", "OK" if proc.returncode == 0 else "ATTENTION")
        return payload
    return {"status": "ERROR", "error": "unexpected_payload", "exit_code": proc.returncode}


def _session_close_utc(session: date) -> datetime:
    return datetime.combine(session, RTH_CLOSE, tzinfo=NY_TZ).astimezone(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _effective_status(item: dict[str, Any], *, session_end: date) -> tuple[str, str]:
    """Return (status, note) after applying reporting-time context.

    The census is a wall-clock freshness test. At 17:10 ET the market has been
    closed for over an hour, so a session-bound collector is judged against the
    session close instead; a collector that is quiet by design is reported as
    such rather than raised as attention. Notes are plain-English display text.
    """
    name = str(item.get("name") or "")
    status = str(item.get("status") or "UNKNOWN")
    if name in EXPECTED_QUIET:
        return "QUIET_BY_DESIGN", EXPECTED_QUIET[name]
    if name in SESSION_BOUND and status in {"STALE", "DEAD"}:
        last = _parse_ts(item.get("last"))
        limit = item.get("limit_minutes")
        if last is not None and isinstance(limit, (int, float)):
            close = _session_close_utc(session_end)
            if close - timedelta(minutes=float(limit)) <= last <= close + timedelta(minutes=float(limit)):
                return "FRESH_AT_CLOSE", f"last update {pe.et_time(last, with_day=False)}, at market close"
            return status, f"last update {pe.et_time(last)} — more than {int(limit)} min from market close"
    return status, ""


def _census_lines(census: dict[str, Any], *, options: bool, session_end: date) -> list[str]:
    collectors = census.get("collectors")
    if not isinstance(collectors, list):
        return [f"collector census: {census.get('status', 'UNKNOWN')}"]
    chosen: list[tuple[str, str, str]] = []
    for item in collectors:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        is_options = name.startswith("options ")
        if is_options != options:
            continue
        status, note = _effective_status(item, session_end=session_end)
        chosen.append((name, status, note))
    if not chosen:
        return ["collector census: no matching collectors"]
    counts = Counter(status for _, status, _ in chosen)
    bad = [f"{name} ({note})" if note else name for name, status, note in chosen if status in {"STALE", "DEAD", "ABSENT"}]
    line = "collector health: " + " · ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    if bad:
        line += " · attention: " + ", ".join(bad[:8])
        if len(bad) > 8:
            line += f" (+{len(bad) - 8} more)"
    quiet = [f"{name}: {note}" for name, status, note in chosen if status in {"QUIET_BY_DESIGN", "FRESH_AT_CLOSE"} and note]
    return [line] + [f"census context: {q}" for q in quiet]


def _top(counter: dict[str, int], limit: int = 5) -> str:
    if not counter:
        return "none"
    return ", ".join(
        f"{k}={v}" for k, v in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    )


# Presentation aliases only: journal identifiers and counters remain unchanged.
# Plain English for a phone reader (docs/discord-operator-message-style.md).
_DISPLAY_NAMES = {
    "ema_pullback_trend": "Pullback in a trend",
    "SHADOW_OUTCOME": "practice results",
    "BAR_CLAIM": "price bars received",
    "DECISION": "bot decisions",
    "shadow_setups": "Practice setups",
    "range_signal": "Sideways-market signals",
    # decisions
    "NO_TRADE": "No setup",
    "TRADE": "Practice trade taken",
    "SHADOW_NO_ORDER": "Setup seen, no order (practice only)",
    "RISK_REJECTED": "Skipped by risk limits",
    "BLOCKED_MAX_TRADES": "Skipped — today's trade limit",
    "BLOCKED_LOSS_LOCKOUT": "Skipped — paused after losses",
    "ORDER_SUPPRESSED": "Order held back",
    # results / row statuses
    "WIN": "won",
    "LOSS": "lost",
    "NO_FILL": "never filled",
    "OPEN": "still open",
    "WATCH": "watching",
    "ACTIVE": "active",
}

# Collector health words (census status codes → short words).
_HEALTH_WORDS = {
    "FRESH": "up to date",
    "FRESH_AT_CLOSE": "up to date at market close",
    "QUIET_BY_DESIGN": "off on purpose",
    "OFF_SESSION": "market closed",
    "STALE": "running late",
    "DEAD": "stopped",
    "ABSENT": "missing",
    "UNKNOWN": "unknown",
}

_FOOTER = "READ ONLY · practice tracking only · nothing was traded or switched on"


def _display_name(value: str) -> str:
    # Bound and neutralize data-derived labels in Discord markdown.
    label = _DISPLAY_NAMES.get(value)
    if label is None:
        key = str(value)
        for suffix in ("_observed", "_observer"):
            if key.endswith(suffix):
                key = key[: -len(suffix)]
        known = pe.SETUPS.get(key)
        label = (known[0].upper() + known[1:]) if known else str(value).replace("_", " ").capitalize()
    return label.translate(str.maketrans("", "", "*`~|<>\\"))[:80]


def _health_words(status: str) -> str:
    return _HEALTH_WORDS.get(status) or _display_name(status).lower()


def _window_words(start: date, end: date) -> str:
    """``Wed Sep 16`` or ``Mon Sep 14 – Fri Sep 18``."""
    if start == end:
        return pe.et_date(start)
    return f"{pe.et_date(start)} – {pe.et_date(end)}"


def _window_cutoff(end: date) -> str:
    """Journals are UTC-dated: say in ET when the report day actually ends."""
    midnight_utc = datetime.combine(end + timedelta(days=1), time(0), tzinfo=timezone.utc)
    return pe.et_time(midnight_utc, with_day=False)


def _count_lines(counter: dict[str, int], *, limit: int = 5) -> str:
    if not counter:
        return "None recorded"
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    lines = [f"**{count:,}** · {_display_name(name)}" for name, count in ordered[:limit]]
    if len(ordered) > limit:
        rest = len(ordered) - limit
        lines.append(f"+ {sum(n for _, n in ordered[limit:]):,} more across {rest} other type{'' if rest == 1 else 's'}")
    return "\n".join(lines)


def _registry_field(registry: dict[str, Any] | None, *, system: str) -> dict[str, Any] | None:
    if not registry:
        return None
    lines = format_registry_lines(registry, system=system, max_entries=8)
    if not lines:
        return None
    value = "\n".join(lines[1:] if lines[0] == REGISTRY_HEADER else lines)
    if len(value) > 900:
        value = value[:850] + "\n… Full list in the saved report file."
    return {"name": "What we're tracking", "value": value or "Nothing being tracked"}


def _collector_health(census: dict[str, Any], *, options: bool, end: date) -> tuple[dict[str, Any], bool]:
    """Lead with failures and retain the timestamps needed to investigate them."""
    collectors = census.get("collectors")
    chosen = [item for item in collectors if isinstance(item, dict)
              and str(item.get("name") or "").startswith("options ") == options] if isinstance(collectors, list) else []
    healthy = {"FRESH", "FRESH_AT_CLOSE", "QUIET_BY_DESIGN", "OFF_SESSION"}
    statuses = [(item, *_effective_status(item, session_end=end)) for item in chosen]
    attention = [(item, status, note) for item, status, note in statuses if status not in healthy]
    counts = Counter(status for _, status, _ in statuses)
    lines = []
    for item, status, note in attention[:5]:
        lines.append(f"**{_display_name(str(item.get('name') or 'Unnamed collector'))}** — {_health_words(status)}")
        last = _parse_ts(item.get("last"))
        lines.append(note or (f"Last update {pe.et_time(last)}" if last else "Last update unknown"))
    if len(attention) > 5:
        lines.append(f"+ {len(attention) - 5} more to look at")
    if statuses:
        lines.append(" · ".join(f"{n} {_health_words(k)}" for k, n in sorted(counts.items())))
    else:
        lines.append("Can't tell if the data collectors are working — the health check didn't answer.")
    for item, status, note in statuses:
        if note and status in {"FRESH_AT_CLOSE", "QUIET_BY_DESIGN"}:
            lines.append(f"{_display_name(str(item.get('name') or 'Collector'))}: {note}")
    if attention or not statuses:
        lines.append("Next step: check that data collector's logs on the server.")
    value = "\n".join(lines)
    if len(value) > 900:
        value = value[:800] + "\n… Full details in the saved report file."
    name = "⚠ Data collectors need a look" if attention or not statuses else "✓ Data collectors OK"
    return {"name": name, "value": value}, bool(attention) or not statuses


def _table_rows(table: dict[str, Any]) -> int | None:
    rows = table.get("rows")
    return rows if isinstance(rows, int) else None


def ftfc_tracker_field(split: dict[str, Any] | None) -> dict[str, Any] | None:
    """Weekly line for the MNQ 2-2 reversal 'big picture lined up' tracker."""
    if not isinstance(split, dict):
        return None
    buckets = split.get("by_alignment") or {}

    def part(name: str) -> str:
        b = buckets.get(name) or {}
        trades = int(b.get("trades") or 0)
        dollars = float(b.get("gross_dollars_1_contract") or 0.0)
        sign = "+" if dollars > 0 else "−" if dollars < 0 else ""
        return f"**{trades:,}** trades · **{int(b.get('wins') or 0):,}** won · **{sign}${abs(dollars):,.0f}**"

    since = str(split.get("first_labeled_signal") or "")[:10]
    lines = [
        f"Lined up only: {part('aligned')}",
        f"Mixed: {part('conflict')}",
        f"Against: {part('against')}",
        "Since " + (since or "labels start") + " · all trades so far, 1 contract, before fees",
    ]
    return {"name": "MNQ 2-2 reversal: big-picture check", "value": "\n".join(lines)}


def futures_discord_payload(
    summary: dict[str, Any],
    census: dict[str, Any],
    *,
    period: str,
    start: date,
    end: date,
    registry: dict[str, Any] | None = None,
    ftfc_split: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A mobile-readable card; counts are observations, never inferred fills/P&L."""
    health_field, health_warning = _collector_health(census, options=False, end=end)

    warning = health_warning or summary["rows"] == 0
    outcomes = summary.get("shadow_outcomes") or {}
    outcome_lines = []
    for keys in (("WIN", "LOSS"), ("NO_FILL", "OPEN")):
        values = [f"**{outcomes[key]:,}** {_display_name(key).lower()}" for key in keys if key in outcomes]
        if values:
            outcome_lines.append(" · ".join(values))
    other_outcomes = {k: v for k, v in outcomes.items() if k not in {"WIN", "LOSS", "NO_FILL", "OPEN"}}
    if other_outcomes:
        outcome_lines.append(_count_lines(other_outcomes))
    outcome_text = "\n".join(outcome_lines) or "None recorded"

    collection = f"**{summary['rows']:,}** records saved\n" + _count_lines(summary.get("row_types") or {}, limit=4)
    if summary.get("instruments"):
        collection += "\nBy market: " + " · ".join(
            f"{_display_name(k).upper()} **{v:,}**" for k, v in sorted(summary["instruments"].items())[:6]
        )
    if summary["rows"] == 0:
        collection += "\n⚠ nothing was recorded for futures in this window"

    fields = [
        health_field,
        {"name": "Bot decisions", "value": _count_lines(summary.get("decisions") or {}), "inline": True},
        {"name": "Practice results", "value": outcome_text + "\nPractice tracking · not real trades", "inline": True},
        {"name": "Most active setups", "value": _count_lines(summary.get("shadow_strategies") or {})},
        {"name": "Activity recorded", "value": collection},
        {"name": "Where setups came from", "value": _count_lines(summary.get("shadow_lanes") or {})},
    ]
    if period == "eow":
        field = _registry_field(registry, system="futures")
        if field:
            fields.append(field)
        field = ftfc_tracker_field(ftfc_split)
        if field:
            fields.append(field)
    # Bounded fields keep the card inside Discord's per-field and total limits.
    for field in fields:
        if len(field["value"]) > 900:
            field["value"] = field["value"][:850] + "\n… Full counts in the saved report file."
    return {
        "allowed_mentions": {"parse": []},
        "embeds": [{
            "title": "📋 Futures practice report · " + ("daily" if period == "eod" else "weekly"),
            "description": f"{_window_words(start, end)} · counts up to {_window_cutoff(end)}",
            "color": 0xF0B232 if warning else 0x5865F2,
            "fields": fields,
            "footer": {"text": _FOOTER},
        }],
    }


def format_futures_report(
    summary: dict[str, Any],
    census: dict[str, Any],
    *,
    period: str,
    start: date,
    end: date,
    registry: dict[str, Any] | None = None,
    ftfc_split: dict[str, Any] | None = None,
) -> str:
    """Keep CLI/artifact-only runs readable using the same card content."""
    embed = futures_discord_payload(
        summary, census, period=period, start=start, end=end, registry=registry,
        ftfc_split=ftfc_split,
    )["embeds"][0]
    sections = [f"**{embed['title']}**\n{embed['description']}"]
    sections.extend(f"**{field['name']}**\n{field['value']}" for field in embed["fields"])
    sections.append(embed["footer"]["text"])
    return "\n\n".join(sections)


_DB_WORDS = {"OK": "readable", "MISSING_DB": "database file missing"}


def options_discord_payload(
    summary: dict[str, Any], census: dict[str, Any], *, period: str,
    start: date, end: date, registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Match the futures card without interpreting journal statuses as option P&L."""
    tables = summary.get("tables") or {}
    scans = tables.get("scans") or {}
    journal = tables.get("options_shadow_journal") or {}
    health, health_warning = _collector_health(census, options=True, end=end)

    db_status = str(summary.get("status", "UNKNOWN"))
    scans_status = str(scans.get("status", "UNKNOWN"))
    journal_status = str(journal.get("status", "UNKNOWN"))
    scan_rows = _table_rows(scans)
    journal_rows = _table_rows(journal)

    blockers: list[str] = []
    warnings: list[str] = []
    if db_status != "OK":
        blockers.append(f"Can't read the scanner database ({_DB_WORDS.get(db_status, _display_name(db_status).lower())}). Check the file is there and readable.")
    if db_status == "OK" and scans_status != "OK":
        blockers.append(f"Can't count this window's scans ({_display_name(scans_status)}). The scans table layout may have changed.")
    if db_status == "OK" and journal_status != "OK":
        blockers.append(f"Can't count this window's practice setups ({_display_name(journal_status)}). The table layout may have changed.")
    if scans_status == "OK" and scan_rows == 0:
        warnings.append("No option scans in this window. Check the market calendar and the scanner logs.")
    if journal_status == "OK" and journal_rows == 0:
        warnings.append("No practice setups logged in this window. Fine only if no setups showed up.")

    no_data = (
        db_status == "OK"
        and scans_status == "OK"
        and journal_status == "OK"
        and (scan_rows or 0) == 0
        and (journal_rows or 0) == 0
    )
    if blockers:
        badge, icon, color = "Can't read the data", "🔴", 0xED4245
    elif no_data:
        badge, icon, color = "No data", "⚠️", 0xF0B232
    elif health_warning or warnings:
        badge, icon, color = "Needs a look", "⚠️", 0xF0B232
    else:
        badge, icon, color = "All good", "✅", 0x57F287

    collection_lines = [f"Scanner database: **{_DB_WORDS.get(db_status, _display_name(db_status).lower())}**"]
    if scans_status == "OK" and scan_rows is not None:
        collection_lines.append(f"Scans run: **{scan_rows:,}**")
    else:
        collection_lines.append(f"Scans run: can't count ({_display_name(scans_status).lower()})")
    if journal_status == "OK" and journal_rows is not None:
        collection_lines.append(f"Practice setups logged: **{journal_rows:,}**")
    else:
        collection_lines.append(f"Practice setups logged: can't count ({_display_name(journal_status).lower()})")
    collection_lines.append("Counts only · not trade recommendations")

    problem_lines = blockers + warnings
    if not problem_lines and not health_warning:
        problem_lines.append("No problems found.")
    if not problem_lines:
        problem_lines.append("None found")

    if blockers:
        next_action = "Fix the database read problem first, then re-run the report without posting to check it."
    elif health_warning:
        next_action = "Check the data collectors above before trusting this report."
    elif no_data:
        next_action = "Check whether options should have been scanned today; if yes, look at the scanner logs and market calendar."
    elif warnings:
        next_action = "Look at the problem above; if it's expected, nothing needs doing."
    else:
        next_action = "Nothing to do. Keep collecting."

    journal_statuses = (_count_lines(journal.get("status_counts") or {})
                        if journal_status == "OK" else "Can't count — see Problems")

    fields = [
        {"name": "Status", "value": f"**{badge}** · options practice tracking"},
        {"name": "What was collected", "value": "\n".join(collection_lines)},
        {"name": "Problems", "value": "\n".join(problem_lines[:8])},
        {"name": "Next step", "value": next_action},
        health,
        {"name": "Practice setups by status", "value": journal_statuses + "\nSetup status only · not option profit or loss"},
    ]
    if period == "eow":
        field = _registry_field(registry, system="options")
        if field:
            fields.append(field)
    for field in fields:
        if len(field["value"]) > 900:
            field["value"] = field["value"][:850] + "\n… Full details in the saved report file."
    title = f"{icon} Options practice report · " + ("daily" if period == "eod" else "weekly")
    return {"allowed_mentions": {"parse": []}, "embeds": [{
        "title": title,
        "description": f"{_window_words(start, end)} · **{badge}**",
        "color": color,
        "fields": fields,
        "footer": {"text": f"{_FOOTER} · details: paper_collection_{period}_{end.isoformat()}.json"},
    }]}


def format_options_report(
    summary: dict[str, Any], census: dict[str, Any], *, period: str,
    start: date, end: date, registry: dict[str, Any] | None = None,
) -> str:
    embed = options_discord_payload(summary, census, period=period, start=start, end=end, registry=registry)["embeds"][0]
    sections = [f"**{embed['title']}**\n{embed['description']}"]
    sections.extend(f"**{field['name']}**\n{field['value']}" for field in embed["fields"])
    sections.append(embed["footer"]["text"])
    return "\n\n".join(sections)


def _post_discord(webhook_url: str, content: str | dict[str, Any]) -> bool:
    try:
        payload = content if isinstance(content, dict) else {"content": content, "allowed_mentions": {"parse": []}}
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "afs-paper-collection/1"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _write_artifact(log_dir: Path, payload: dict[str, Any], *, ref: date, period: str) -> Path | None:
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"paper_collection_{period}_{ref.isoformat()}.json"
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        return path
    except OSError:
        return None


def _default_coverage_dir() -> str:
    configured = (os.getenv("OPTIONS_COVERAGE_DATA_DIR") or "").strip()
    if configured:
        return configured
    shared = Path("/root/afs-shared/coverage")
    try:
        if shared.exists():
            return str(shared)
    except OSError:
        # Non-root/CI environments may be unable even to stat /root. Treat an
        # inaccessible shared path exactly like an absent one; the report stays
        # read-only and falls back to the repository-local collector directory.
        pass
    return str(Path("logs/coverage_collector"))


def _resolve_family_summary(coverage_dir: Path, configured: str | None) -> Path | None:
    if configured:
        return Path(configured)
    for name in ("prospective_family_summary.json", "prospective_family_summary.csv"):
        candidate = coverage_dir / name
        if candidate.exists():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--period", choices=("eod", "eow"), required=True)
    parser.add_argument("--date", help="UTC reference date YYYY-MM-DD; default today")
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument(
        "--options-db",
        default=os.getenv("OPTIONS_SCANNER_SQLITE_PATH", "logs/options_scanner.sqlite"),
    )
    parser.add_argument(
        "--coverage-data-dir",
        default=_default_coverage_dir(),
        help="options coverage collector data dir (read-only for the report)",
    )
    parser.add_argument(
        "--prospective-family-summary",
        default=os.getenv("OPTIONS_PROSPECTIVE_FAMILY_SUMMARY", ""),
        help="optional JSON/CSV prospective-family summary artifact",
    )
    parser.add_argument("--no-discord", action="store_true", help="build artifacts only")
    args = parser.parse_args(argv)

    ref = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    start, end = period_bounds(ref, args.period)
    log_dir = Path(args.log_dir)
    options_db = Path(args.options_db)
    coverage_dir = Path(args.coverage_data_dir)

    futures = summarize_futures(_futures_rows(log_dir, start, end))
    options = summarize_options(options_db, start, end)
    census = run_collector_census(log_dir)

    registry = None
    if args.period == "eow":
        registry = build_registry(
            log_dir=log_dir,
            coverage_dir=coverage_dir,
            census=census,
            start=start,
            end=end,
            family_summary_path=_resolve_family_summary(
                coverage_dir, args.prospective_family_summary or None
            ),
        )

    ftfc_split = None
    if args.period == "eow":
        try:
            from execution.cross_instrument_observation import strat_ftfc_split

            ftfc_split = strat_ftfc_split(log_dir)
        except Exception as exc:  # noqa: BLE001 — the report must still go out
            print(f"[paper_collection_report] ftfc split unavailable: {exc}")

    futures_report = format_futures_report(
        futures, census, period=args.period, start=start, end=end, registry=registry,
        ftfc_split=ftfc_split,
    )
    options_report = format_options_report(
        options, census, period=args.period, start=start, end=end, registry=registry
    )

    payload = {
        "schema": "paper_collection_rollup_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period": args.period,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "futures": futures,
        "options": options,
        "collector_census": census,
    }
    if registry is not None:
        payload["evidence_registry"] = registry
    if ftfc_split is not None:
        payload["strat_ftfc_split"] = ftfc_split
    artifact = _write_artifact(log_dir, payload, ref=ref, period=args.period)

    send_failures = 0
    if not args.no_discord:
        futures_card = futures_discord_payload(
            futures, census, period=args.period, start=start, end=end, registry=registry,
            ftfc_split=ftfc_split,
        )
        options_card = options_discord_payload(
            options, census, period=args.period, start=start, end=end, registry=registry
        )
        for env_name, report in ((FUTURES_ENV, futures_card), (OPTIONS_ENV, options_card)):
            webhook = (os.getenv(env_name) or "").strip()
            if not webhook:
                print(f"[paper_collection_report] {env_name} unset; artifact only")
                continue
            ok = _post_discord(webhook, report)
            print(f"[paper_collection_report] {env_name} posted={ok}")
            if not ok:
                send_failures += 1

    print(futures_report)
    print()
    print(options_report)
    if artifact:
        print(f"[paper_collection_report] artifact={artifact}")
    return 1 if send_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())