"""Read-only daily / weekly PAPER COLLECTION digest — futures and options.

One place that answers "what evidence are we actually collecting?" (daily,
operational sanity) and "is that evidence going anywhere?" (weekly, progress).
It is NOT a collector and duplicates no evidence: every number is either read
from an existing authoritative report or is a plain count of rows that already
exist inside a date window.

Reused as-is (no report is rebuilt here):
  * ops.evidence_lane_health.build_snapshot        MNQ Strat + MES tcb lanes
  * ops.collector_census.build_census              freshness, hypothetical-lane
                                                   positions, forward A/B arms
  * execution.cross_instrument_evidence_quality    cross-instrument populations
    + ops.cross_instrument_feed_health              + campaign feed proof
  * scripts.weekly_review.load_journal/summarize   real-book decision journal
  * context.mes_122_paper_lane.ledger_status       MES 1-2-2 realistic ledger
  * alert_ranker.v1_diagnostics (_lane, _financial_outcome, sample_status)
  * alert_ranker.coverage_collector.read_ledger

Window rule (no checkpoint file): a daily digest covers exactly one UTC
calendar date (the #595 cohort uses its own CME observation ``day``); a weekly
digest covers the ISO week Monday..Sunday containing the reference date.
Because the window is derived strictly from row timestamps, the first run can
never claim historical rows were "collected today".

Never writes: no campaign evidence, state or checkpoint file is created or
modified.  Never prints a webhook URL.  Draws no performance conclusion and
never proposes promotion; the only "review gate" fields are thresholds that
already exist elsewhere (the #595 30-resolved/10-day gate, V1's
INSUFFICIENT/EARLY/REVIEWABLE sample labels).
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TS_FIELDS = ("observed_at", "ts", "timestamp", "recorded_at", "signal_timestamp")
_TERMINAL = ("WIN", "LOSS", "BREAKEVEN", "EXPIRED")

# #595 review gate, as written in docs/futures-current-state-handoff.md (lane 5).
ASIA_REVIEW_GATE = {"resolved_trades": 30, "observation_days": 10}

FUTURES_ROUTE = "paper_collection_futures"
OPTIONS_ROUTE = "paper_collection_options"
DISCORD_CHUNK = 1900  # Discord hard limit is 2000 characters per message
_ATTENTION_HEALTH = ("FAILED", "REPORT_ERROR", "STATE_MISSING", "PENDING", "DB_MISSING", "LANE_DIR")


# ── window / parsing helpers (generic; the only "new" parsing in this file) ──
def window_bounds(period: str, ref: date) -> tuple[date, date]:
    if period == "daily":
        return ref, ref
    if period == "weekly":
        from scripts.weekly_review import week_bounds

        return week_bounds(ref)
    raise ValueError(f"period must be 'daily' or 'weekly', got {period!r}")


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _row_ts(row: dict, fields: Iterable[str] = _TS_FIELDS) -> Optional[datetime]:
    for field in fields:
        parsed = _parse_ts(row.get(field))
        if parsed is not None:
            return parsed
    return None


def _row_date(row: dict, fields: Iterable[str] = _TS_FIELDS) -> Optional[date]:
    ts = _row_ts(row, fields)
    return ts.astimezone(timezone.utc).date() if ts else None


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _in_window(rows: Iterable[dict], start: date, end: date, *, day_of=None) -> list[dict]:
    day_of = day_of or _row_date
    return [row for row in rows if (d := day_of(row)) is not None and start <= d <= end]


def _last_ts(rows: Iterable[dict], fields: Iterable[str] = _TS_FIELDS) -> Optional[str]:
    stamps = [ts for row in rows if (ts := _row_ts(row, fields)) is not None]
    return max(stamps).isoformat() if stamps else None


def _days(rows: Iterable[dict], day_of=None) -> int:
    day_of = day_of or _row_date
    return len({d for row in rows if (d := day_of(row)) is not None})


def _dated_journal_rows(directory: Path, start: date, end: date, pattern: str = "journal_*.jsonl") -> list[dict]:
    """Rows of per-day journal files whose filename date falls in the window."""
    rows: list[dict] = []
    for path in sorted(directory.glob(pattern)):
        try:
            day = date.fromisoformat(path.stem.split("_", 1)[1])
        except (IndexError, ValueError):
            continue
        if start <= day <= end:
            rows.extend(_read_jsonl(path))
    return rows


def _lane(
    name: str,
    *,
    instrument: str,
    mode: str,
    epoch: Optional[str],
    window: dict[str, Any],
    cumulative: dict[str, Any],
    last_evidence: Optional[str],
    health: str,
    stalled: Optional[bool],
    note: str = "",
    campaign_id: Optional[str] = None,
    open_position: Any = None,
    review_gate: Optional[dict[str, Any]] = None,
    status: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "lane": name,
        "campaign_id": campaign_id,
        "instrument": instrument,
        "mode": mode,
        "epoch": epoch,
        "window": window,
        "zero_activity": not any(v for v in window.values() if isinstance(v, int)),
        "cumulative": cumulative,
        "last_evidence": last_evidence,
        "open_position": open_position,
        "health": health,
        "stalled": stalled,
        "status": status,
        "review_gate": review_gate,
        "note": note,
    }


# ── futures lanes ───────────────────────────────────────────────────────────
def _census_status(census: dict, name: str) -> Optional[str]:
    for row in census.get("collectors", []):
        if row.get("name") == name:
            return row.get("status")
    return None


def _asia_lane(root: Path, start: date, end: date) -> dict[str, Any]:
    from context.asia_d_ema_paper_cohort import (
        CAMPAIGN_ID, EPOCH_ENV, MODE_ENV, MODE_OFF, evidence_path, state_path,
    )

    rows = _read_jsonl(evidence_path(root))
    state = _read_json(state_path(root))
    mode = (os.getenv(MODE_ENV) or MODE_OFF).strip() or MODE_OFF
    day_of = lambda r: _parse_date(r.get("day"))  # noqa: E731 — CME observation day
    win = _in_window(rows, start, end, day_of=day_of)
    events = Counter(str(r.get("event")) for r in win)
    outcomes = [r for r in win if r.get("event") == "OUTCOME"]
    all_outcomes = [r for r in rows if r.get("event") == "OUTCOME"]
    results = Counter(str(r.get("result")) for r in outcomes)
    all_results = Counter(str(r.get("result")) for r in all_outcomes)
    resolved = sum(all_results[k] for k in ("WIN", "LOSS"))
    obs_days = _days(all_outcomes, day_of)
    return _lane(
        "MNQ Asia D+EMA cohort (#595)",
        campaign_id=CAMPAIGN_ID,
        instrument="MNQ 15m / asian",
        mode=mode if mode == "paper_sim" else f"{mode} (inactive)",
        epoch=os.getenv(EPOCH_ENV) or None,
        window={
            "candidates": events["CANDIDATE_FILLED"] + events["NO_FILL"] + events["CANDIDATE_SKIPPED_BUSY"],
            "fills": events["CANDIDATE_FILLED"],
            "no_fills": events["NO_FILL"],
            "skipped_busy": events["CANDIDATE_SKIPPED_BUSY"],
            "pre_epoch": events["CANDIDATE_PRE_EPOCH"],
            "wins": results["WIN"],
            "losses": results["LOSS"],
            "expired": results["EXPIRED"],
            "observation_days": _days(win, day_of),
        },
        cumulative={
            "events": len(rows),
            "fills": sum(1 for r in rows if r.get("event") == "CANDIDATE_FILLED"),
            "wins": all_results["WIN"],
            "losses": all_results["LOSS"],
            "expired": all_results["EXPIRED"],
            "resolved_trades": resolved,
            "observation_days_with_outcomes": obs_days,
        },
        last_evidence=_last_ts(rows),
        open_position=state.get("position"),
        health=(
            "STATE_MISSING" if not state else
            "PENDING_EVENTS" if state.get("pending_events") else "OK"
        ),
        stalled=None,
        review_gate={
            **ASIA_REVIEW_GATE,
            "resolved_trades_so_far": resolved,
            "observation_days_so_far": obs_days,
            "reached": resolved >= ASIA_REVIEW_GATE["resolved_trades"]
            and obs_days >= ASIA_REVIEW_GATE["observation_days"],
        },
        note="one position; same-observation-day resolution; PaperBroker only; status not evidence",
    )


def _parse_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def _wide_stop_lanes(root: Path, start: date, end: date, census: dict) -> list[dict[str, Any]]:
    from ops.collector_census import LEDGER_ROOT

    positions = (census.get("hypothetical_lanes") or {}).get("lanes") or {}
    epoch = os.getenv("WIDE_STOP_LEDGER_EPOCH_START") or None
    mode = (os.getenv("WIDE_STOP_LEDGER_MODE") or "off").strip()
    lanes: list[dict[str, Any]] = []
    specs = (
        ("MNQ Daily 2-2 (daily_22_5k)", "daily_22_5k", "swing_audit.jsonl", "daily_22 swing state"),
        ("MNQ 4HR Re-Trigger (wide_stop_4k)", "wide_stop_4k", None, None),
        ("MNQ 60M 3-2-2 (wide_stop_6k, $5k ledger)", "wide_stop_6k", None, None),
    )
    for label, lane, audit_file, census_name in specs:
        lane_dir = root / LEDGER_ROOT / lane
        if audit_file:
            all_rows = _read_jsonl(lane_dir / audit_file)
            win = _in_window(all_rows, start, end)
            evt = lambda r: r  # noqa: E731
        else:
            all_rows = [
                r.get("wide_stop_ledger") or {}
                for r in _dated_journal_rows(lane_dir, date(2000, 1, 1), date(2999, 12, 31))
                if r.get("decision") == "HYPOTHETICAL_LEDGER"
            ]
            win = [
                r.get("wide_stop_ledger") or {}
                for r in _dated_journal_rows(lane_dir, start, end)
                if r.get("decision") == "HYPOTHETICAL_LEDGER"
            ]
            evt = lambda r: r  # noqa: E731
        c = Counter(str(evt(r).get("collector_event")) for r in win)
        fills = sum(str(evt(r).get("fill_status") or "").upper() == "FILLED" for r in win)
        outcomes = Counter(
            str(evt(r).get("lane_result") or evt(r).get("result") or "")
            for r in win if evt(r).get("collector_event") == "OUTCOME"
        )
        pos = positions.get(lane) or {}
        lanes.append(_lane(
            label,
            instrument="MNQ",
            mode=mode,
            epoch=epoch,
            window={
                "candidates": c["CANDIDATE"],
                "fills": fills,
                "outcomes": c["OUTCOME"],
                "wins": outcomes["WIN"], "losses": outcomes["LOSS"], "breakevens": outcomes["BREAKEVEN"],
            },
            cumulative={
                "candidates": sum(1 for r in all_rows if evt(r).get("collector_event") == "CANDIDATE"),
                "outcomes": sum(1 for r in all_rows if evt(r).get("collector_event") == "OUTCOME"),
                "halted": pos.get("halted"),
            },
            last_evidence=_last_ts(all_rows) or pos.get("state_last"),
            open_position=pos.get("open_position"),
            health=_census_status(census, census_name) or ("LANE_DIR_MISSING" if not pos.get("exists") else "n/a"),
            stalled=(_census_status(census, census_name) in ("DEAD", "ABSENT")) if census_name else None,
            note="heartbeat = swing state on every MNQ 5m bar" if audit_file else "state written only on candidates (no heartbeat)",
        ))
    return lanes


def _mes_122_lane(root: Path, start: date, end: date, census: dict) -> dict[str, Any]:
    from context.mes_122_paper_lane import STRATEGY, epoch_start, journal_dir, ledger_status

    lane_dir = journal_dir(root)
    win = _dated_journal_rows(lane_dir, start, end)
    decisions = Counter(str(r.get("decision")) for r in win if r.get("decision"))
    outcomes = [
        r.get("outcome") or {} for r in win
        if r.get("type") == "OUTCOME"
        and str((r.get("outcome") or {}).get("strategy") or r.get("strategy") or "") == STRATEGY
    ]
    results = Counter(str(o.get("result")) for o in outcomes)
    try:
        ledger = ledger_status(None, root)
    except Exception as exc:  # noqa: BLE001 — a reporting failure must stay soft
        ledger = {"error": type(exc).__name__}
    positions = (census.get("hypothetical_lanes") or {}).get("lanes") or {}
    pos = positions.get("mes_122_1500") or {}
    health = _census_status(census, "mes_122 lane journal")
    return _lane(
        "MES 15m 1-2-2 (mes_122_1500)",
        instrument="MES 15m",
        mode=(os.getenv("MES_122_PAPER_MODE") or "off").strip(),
        epoch=epoch_start(None),
        window={
            "bars": sum(1 for r in win if r.get("type") == "BAR_CLAIM"),
            "trade_decisions": decisions["TRADE"],
            "no_trade": decisions["NO_TRADE"],
            "risk_rejected": decisions["RISK_REJECTED"],
            "outcomes": len(outcomes),
            "wins": results["WIN"], "losses": results["LOSS"], "breakevens": results["BREAKEVEN"],
        },
        cumulative={k: ledger.get(k) for k in ("resolved_trades", "realistic_balance", "max_drawdown_pct", "halted", "error") if k in ledger},
        last_evidence=pos.get("state_last"),
        open_position=pos.get("open_position"),
        health=health or "n/a",
        stalled=health in ("DEAD", "ABSENT"),
        note="judge on the realistic 1-tick-per-leg ledger, never raw P&L",
    )


def _strat_lanes(root: Path, start: date, end: date, now: datetime) -> list[dict[str, Any]]:
    """MNQ Strat + MES trend-consolidation-break lanes via evidence_lane_health, summed per day."""
    from execution.mes_trend_consolidation_break_evidence import evidence_path as mes_evidence_path
    from execution.mnq_strat_evidence import evidence_path as mnq_evidence_path
    from ops.evidence_lane_health import build_snapshot

    def _last(instrument: str, lane: str) -> Optional[str]:
        path = mes_evidence_path(root) if instrument == "MES" else mnq_evidence_path(root, lane)
        return _last_ts(_read_jsonl(path))

    # Counts are summed per day; status / signals / open state / feed come from
    # the LAST evaluated day only (the snapshot reads live state for today alone,
    # so summing signals across days would invent stale-state findings).
    per_lane: dict[str, dict[str, Any]] = {}
    day = start
    last_day = min(end, now.date())
    while day <= last_day:
        snap = build_snapshot(root, day=day, now=now)
        for lane in snap["lanes"]:
            key = f"{lane['instrument']} {lane['lane']}"
            entry = per_lane.setdefault(key, {
                "instrument": lane["instrument"], "lane_key": lane["lane"], "mode": lane["mode"],
                "counts": Counter(), "days_active": 0,
            })
            entry["counts"].update({k: int(v) for k, v in lane["counts"].items()})
            entry.update({
                "lifetime": lane["lifetime"], "status": lane["status"], "signals": set(lane["signals"]),
                "open_state": lane["open_state"], "feed": snap["feeds"].get(lane["instrument"], {}),
            })
            if lane["counts"]["candidates"]:
                entry["days_active"] += 1
        day += timedelta(days=1)
    lanes = []
    for key, e in per_lane.items():
        c = e["counts"]
        lanes.append(_lane(
            f"{key} (Strat evidence lane)",
            instrument=e["instrument"],
            mode=e["mode"],
            epoch=None,
            window={
                "candidates": c["candidates"], "accepted": c["accepted"], "rejected": c["rejected"],
                "fills": c["fills"], "no_fills": c["no_fills"], "outcomes": c["outcomes"],
                "wins": c["wins"], "losses": c["losses"], "breakevens": c["breakevens"],
                "days_with_candidates": e["days_active"],
            },
            cumulative=e["lifetime"],
            last_evidence=_last(e["instrument"], e["lane_key"]),
            open_position=(
                " + ".join(k.replace("_", " ") for k, v in e["open_state"].items() if v) or None
            ),
            health=f"feed {e['feed'].get('status', '?')}",
            stalled=e["feed"].get("status") == "STALE",
            status=e["status"],
            note=", ".join(sorted(e["signals"])) if e["signals"] else "",
        ))
    return lanes


def _forward_ab_lane(root: Path, start: date, end: date, census: dict) -> dict[str, Any]:
    from execution.forward_evidence_campaign import CAMPAIGN_ID, EVIDENCE_FILENAME

    rows = _read_jsonl(root / EVIDENCE_FILENAME)
    win = _in_window(rows, start, end)
    types = Counter(str(r.get("record_type")) for r in win)
    terminal = Counter(str(r.get("terminal_state")) for r in win if r.get("record_type") == "OUTCOME")
    arms = (census.get("campaign_arms") or {}).get("configured") or {}
    health = _census_status(census, "forward A/B campaign")
    return _lane(
        "MNQ forward A/B campaign",
        campaign_id=CAMPAIGN_ID,
        instrument="MNQ",
        mode="paper_sim (matched-pair evidence)",
        epoch=None,
        window={
            "candidates": types["CANDIDATE"], "outcomes": types["OUTCOME"],
            "wins": terminal["WIN"], "losses": terminal["LOSS"],
            "no_fills": terminal["NO_FILL"] + terminal["CANCELLED"],
        },
        cumulative={
            "rows": len(rows),
            "candidates_by_arm": {k: v.get("count") for k, v in sorted(arms.items())},
        },
        last_evidence=_last_ts(rows),
        health=health or "n/a",
        stalled=health in ("DEAD", "ABSENT"),
        note="per-arm detail: ops/forward_campaign_report.py",
    )


def _cross_instrument_lane(root: Path, start: date, end: date) -> dict[str, Any]:
    from execution.cross_instrument_evidence_quality import build_quality_report
    from execution.cross_instrument_observation import EVIDENCE_FILENAME
    from ops.cross_instrument_feed_health import build_feed_health

    epoch = os.getenv("CROSS_INSTRUMENT_OBSERVATION_EPOCH") or None
    rows = _read_jsonl(root / EVIDENCE_FILENAME)
    win = _in_window(rows, start, end)
    types = Counter(str(r.get("record_type")) for r in win)
    by_inst = Counter(str(r.get("instrument")) for r in win)
    try:
        report = build_quality_report(root, epoch=epoch)
        feed = build_feed_health(root, epoch=epoch)
        populations = [
            {
                "population": f"{p['instrument']} {p['strategy']}",
                "mode": p.get("collection_mode"),
                "terminal": p.get("terminal_outcomes"),
                "clean": p.get("quality_eligible_terminal_outcomes"),
                "days": p.get("quality_distinct_terminal_days"),
                "status": p.get("status"),
            }
            for p in report.get("populations", [])
        ]
        health = (
            "FEED_TRUSTED" if feed.get("ready_to_trust_collection_feed") else
            "FEED_NOT_TRUSTED"
        )
        campaign_id = report.get("campaign_id")
        enabled = report.get("enabled")
    except Exception as exc:  # noqa: BLE001
        populations, health, campaign_id, enabled = [], f"REPORT_ERROR:{type(exc).__name__}", None, None
    return _lane(
        "Cross-instrument observation campaign",
        campaign_id=campaign_id,
        instrument=", ".join(sorted(by_inst)) or "MNQ/MES/M2K/MGC/MCL/MBT",
        mode="observation-only (collection roots non-executable)" if enabled else "observation-only (not armed)",
        epoch=epoch,
        window={
            "candidates": types["CANDIDATE"], "signals": types["SIGNAL"], "outcomes": types["OUTCOME"],
            "days": _days(win),
        },
        cumulative={"rows": len(rows), "populations": populations},
        last_evidence=_last_ts(rows),
        health=health,
        stalled=None,
        note="status per population is the authoritative quality report's; nothing here is executable",
    )


def _real_book_lane(root: Path, start: date, end: date, census: dict) -> dict[str, Any]:
    from scripts.weekly_review import load_journal, summarize_week

    journal = load_journal(root, start, end)
    data = summarize_week(journal, [])
    health = _census_status(census, "futures journal")
    return _lane(
        "Real-book decision journal (paper/shadow)",
        instrument="MNQ (allowed universe)",
        mode=f"{os.getenv('SCHEDULE_MODE') or '?'} / paper_mode={os.getenv('PAPER_MODE') or '?'} / live=false",
        epoch=None,
        window={
            "trade_decisions": data["approved_trades"], "no_trade": data["no_trade"],
            "risk_rejected": data["risk_rejected"], "fills": data["filled"], "cancelled": data["cancelled"],
            "wins": data["wins"], "losses": data["losses"],
        },
        cumulative={},
        last_evidence=_last_ts(journal),
        health=health or "n/a",
        stalled=health in ("DEAD", "ABSENT"),
        note="executable MNQ 15m set is empty by design (#376 -> #517); decisions here are expected NO_TRADE",
    )


def _observation_files(root: Path, start: date, end: date, census: dict) -> list[dict[str, Any]]:
    specs = (
        ("Runner shadow evidence (observation-only)", "runner_shadow_evidence.jsonl", None),
        ("VWAP-hold early shadow (observation-only)", "vwap_hold_early_shadow_evidence.jsonl", None),
        ("Strategy context observations (observation-only)", "strategy_context_observations.jsonl", "strategy context"),
    )
    lanes = []
    for label, filename, census_name in specs:
        rows = _read_jsonl(root / filename)
        win = _in_window(rows, start, end)
        health = _census_status(census, census_name) if census_name else None
        lanes.append(_lane(
            label, instrument="MNQ/MES", mode="observation-only", epoch=None,
            window={"rows": len(win), "days": _days(win)},
            cumulative={"rows": len(rows)},
            last_evidence=_last_ts(rows),
            health=health or "n/a",
            stalled=(health in ("DEAD", "ABSENT")) if census_name else None,
        ))
    return lanes


def build_futures_digest(
    log_dir: str | Path = "logs",
    *,
    period: str = "daily",
    ref_date: Optional[date] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    from ops.collector_census import build_census

    root = Path(log_dir)
    now = now or datetime.now(timezone.utc)
    ref = ref_date or now.date()
    start, end = window_bounds(period, ref)
    census = build_census(root, now=now)
    lanes: list[dict[str, Any]] = [_asia_lane(root, start, end)]
    lanes += _wide_stop_lanes(root, start, end, census)
    lanes.append(_mes_122_lane(root, start, end, census))
    lanes.append(_cross_instrument_lane(root, start, end))
    lanes += _strat_lanes(root, start, end, now)
    lanes.append(_forward_ab_lane(root, start, end, census))
    lanes.append(_real_book_lane(root, start, end, census))
    lanes += _observation_files(root, start, end, census)
    data_health = {
        "collectors": {r["name"]: r["status"] for r in census.get("collectors", [])},
        "dead_or_absent": census.get("dead", []),
        "stale": [r["name"] for r in census.get("collectors", []) if r.get("status") == "STALE"],
        "mnq_position_exposed_without_fresh_5m_bars": (census.get("hypothetical_lanes") or {}).get(
            "mnq_position_exposed_without_fresh_5m_bars"
        ),
    }
    return {
        "domain": "futures",
        "period": period,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "basis": "UTC calendar date; #595 uses CME observation day"},
        "generated_at": now.isoformat(),
        "read_only": True,
        "lanes": lanes,
        "zero_activity_lanes": [l["lane"] for l in lanes if l["zero_activity"]],
        "stalled_lanes": [l["lane"] for l in lanes if l["stalled"]],
        "data_health": data_health,
    }


# ── options ─────────────────────────────────────────────────────────────────
def _ro_connect(path: Path) -> Optional[sqlite3.Connection]:
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error:
        return None


def _options_epoch() -> dict[str, Any]:
    return _read_json(_REPO_ROOT / "docs" / "options_v1_evidence_epoch.json")


def _v1_lane(scanner_db: Path, start: date, end: date, epoch: dict[str, Any]) -> dict[str, Any]:
    from alert_ranker.paper_v1 import POLICY_ID
    from alert_ranker.v1_diagnostics import _financial_outcome, _lane as v1_lane, sample_status

    conn = _ro_connect(scanner_db)
    epoch_start = _parse_ts(epoch.get("epoch_start"))
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    scans_win = scans_tickers = alerts = 0
    journal_all: list[dict[str, Any]] = []
    last_scan = last_journal = None
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n, COUNT(DISTINCT ticker) AS t, COALESCE(SUM(alert_sent), 0) AS a "
                "FROM scans WHERE timestamp >= ? AND timestamp < ?", (lo, hi),
            ).fetchone()
            scans_win, scans_tickers, alerts = int(row["n"]), int(row["t"]), int(row["a"])
            last_scan = conn.execute("SELECT MAX(timestamp) FROM scans").fetchone()[0]
            for r in conn.execute(
                "SELECT id, timestamp, status, setup_inputs_json, selected_contract_json, outcome_json "
                "FROM options_shadow_journal WHERE selected_contract_json LIKE ? ORDER BY id",
                (f"%{POLICY_ID}%",),
            ):
                try:
                    selected = json.loads(r["selected_contract_json"] or "{}")
                    setup_inputs = json.loads(r["setup_inputs_json"] or "{}")
                    outcome = json.loads(r["outcome_json"] or "{}")
                except ValueError:
                    continue
                if selected.get("paper_policy_id") != POLICY_ID:
                    continue
                pnl = outcome.get("pnl_dollars")
                journal_all.append({
                    "timestamp": r["timestamp"],
                    "lane": v1_lane(selected, setup_inputs),
                    "financial": _financial_outcome(float(pnl) if isinstance(pnl, (int, float)) else None, r["status"]),
                })
            last_journal = conn.execute("SELECT MAX(timestamp) FROM options_shadow_journal").fetchone()[0]
        except sqlite3.Error as exc:
            journal_all = []
            last_scan = last_scan or f"sqlite error: {type(exc).__name__}"
        finally:
            conn.close()
    in_epoch = [
        j for j in journal_all
        if epoch_start is None or ((ts := _parse_ts(j["timestamp"])) is not None and ts >= epoch_start)
    ]
    win = _in_window(journal_all, start, end, day_of=lambda j: _row_date(j, ("timestamp",)))

    def _bucket(rows: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for lane in ("ACTIVE", "COUNTERFACTUAL"):
            sub = [j for j in rows if j["lane"] == lane]
            fin = Counter(j["financial"] for j in sub)
            out[lane.lower()] = {
                "rows": len(sub), "open": fin["OPEN"], "profit": fin["PROFIT"], "loss": fin["LOSS"],
                "breakeven": fin["BREAKEVEN"], "unpriced": fin["UNPRICED"],
            }
        return out

    active_resolved = sum(
        1 for j in in_epoch if j["lane"] == "ACTIVE" and j["financial"] in ("PROFIT", "LOSS", "BREAKEVEN")
    )
    return _lane(
        f"Options Paper V1 ({epoch.get('cohort') or 'epoch unknown'})",
        campaign_id=epoch.get("policy_id"),
        instrument=f"{len((epoch.get('universe') or {}).get('current') or [])} symbols" if epoch else "?",
        mode="paper-sim (advisory only; provider read-only)",
        epoch=epoch.get("epoch_start"),
        window={
            "scans": scans_win, "symbols_scanned": scans_tickers, "alerts_sent": alerts,
            "journal_rows": len(win), **{f"{k}_rows": v["rows"] for k, v in _bucket(win).items()},
            "active_profit": _bucket(win)["active"]["profit"], "active_loss": _bucket(win)["active"]["loss"],
        },
        cumulative={"since_epoch": _bucket(in_epoch), "all_time_journal_rows": len(journal_all)},
        last_evidence=last_journal or last_scan,
        health="DB_MISSING" if conn is None and not scanner_db.exists() else "OK",
        stalled=None,
        review_gate={"sample_status": sample_status(active_resolved), "active_resolved_since_epoch": active_resolved,
                     "labels": "INSUFFICIENT<20, EARLY<50, REVIEWABLE (v1_diagnostics; reporting labels, not promotion rules)"},
        note="financial outcome = sign of recorded P&L, never the journal status label; last scan "
             f"{last_scan}",
    )


def _coverage_lane(data_dir: Path, start: date, end: date) -> dict[str, Any]:
    from alert_ranker.coverage_collector import (
        STATUS_ALREADY_COLLECTED, STATUS_DONE, STATUS_FAILED, read_ledger,
    )

    ledger = [r for r in read_ledger(data_dir / "ledger.jsonl") if "malformed" not in r]
    win = _in_window(ledger, start, end, day_of=lambda r: _parse_date(r.get("session_date")))
    # A session's collection status is its last DONE/ALREADY_COLLECTED/FAILED record;
    # STARTED/AGGREGATED rows are progress markers, not results.
    terminal = {STATUS_DONE, STATUS_ALREADY_COLLECTED, STATUS_FAILED}
    status_by_session: dict[str, str] = {}
    for r in sorted(ledger, key=lambda r: str(r.get("recorded_at") or "")):
        if r.get("session_date") and str(r.get("status")) in terminal:
            status_by_session[str(r["session_date"])] = str(r.get("status"))
    win_sessions = {str(r["session_date"]) for r in win if r.get("session_date")}
    final = Counter(status_by_session.get(s, "IN_PROGRESS") for s in win_sessions)
    failures = [
        {"session": str(r.get("session_date")), "reason": r.get("reason"), "detail": str(r.get("detail") or "")[:160]}
        for r in win if r.get("status") == STATUS_FAILED
    ]
    newest = max(win_sessions, default="")
    # Health follows the newest session's FINAL terminal status: a FAILED run that
    # was later retried to DONE is healthy; only a still-failed newest session is not.
    newest_final = status_by_session.get(newest) if newest else None
    if not newest:
        health = "NO_SESSIONS_IN_WINDOW"
    elif newest_final == STATUS_FAILED:
        health = "FAILED_LAST_SESSION"
    elif newest_final is None:
        health = "IN_PROGRESS"
    else:
        health = "OK"
    still_failed = [f for f in failures if status_by_session.get(f["session"]) == STATUS_FAILED]
    return _lane(
        "Options coverage collector (col-v0.1)",
        instrument="RTH equities universe",
        mode="observation-only (after-close oneshot)",
        epoch=None,
        window={
            "sessions_attempted": len(win_sessions),
            "sessions_done": final[STATUS_DONE] + final[STATUS_ALREADY_COLLECTED],
            "sessions_failed": final[STATUS_FAILED],
            "failed_runs_later_recovered": len(failures) - len(still_failed),
            "ledger_records": len(win),
        },
        cumulative={
            "sessions_seen": len(status_by_session),
            "final_status_counts": dict(Counter(status_by_session.values())),
        },
        last_evidence=_last_ts(ledger, ("recorded_at",)),
        health=health,
        stalled=None,
        note="; ".join(f"{f['session']} {f['reason']}: {f['detail']}" for f in still_failed[-2:]),
    )


def _companion_lane(sqlite_path: Path, start: date, end: date) -> dict[str, Any]:
    from scripts.weekly_review import load_option_rows

    rows = load_option_rows(sqlite_path, start, end)
    statuses = Counter(str(r.get("status")) for r in rows)
    return _lane(
        "Options companion (paper ledger)",
        instrument="equity options",
        mode="companion OFF per V1 epoch record; ledger read for visibility only",
        epoch=None,
        window={"rows": len(rows), **{k.lower(): v for k, v in statuses.items()}},
        cumulative={},
        last_evidence=_last_ts(rows, ("created_at",)),
        health="n/a",
        stalled=None,
    )


def build_options_digest(
    log_dir: str | Path = "logs",
    *,
    period: str = "daily",
    ref_date: Optional[date] = None,
    now: Optional[datetime] = None,
    scanner_db: Optional[Path] = None,
    coverage_dir: Optional[Path] = None,
    companion_db: Optional[Path] = None,
) -> dict[str, Any]:
    from config.settings import options_companion_sqlite_path

    root = Path(log_dir)
    now = now or datetime.now(timezone.utc)
    ref = ref_date or now.date()
    start, end = window_bounds(period, ref)
    scanner_db = scanner_db or Path(os.getenv("OPTIONS_SCANNER_SQLITE_PATH") or root / "options_scanner.sqlite")
    coverage_dir = coverage_dir or Path(os.getenv("OPTIONS_COVERAGE_DATA_DIR") or root / "coverage_collector")
    companion_db = companion_db or options_companion_sqlite_path()
    epoch = _options_epoch()
    lanes = [
        _v1_lane(scanner_db, start, end, epoch),
        _coverage_lane(coverage_dir, start, end),
        _companion_lane(companion_db, start, end),
    ]
    return {
        "domain": "options",
        "period": period,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "basis": "UTC calendar date (coverage: NYSE session date)"},
        "generated_at": now.isoformat(),
        "read_only": True,
        "lanes": lanes,
        "zero_activity_lanes": [l["lane"] for l in lanes if l["zero_activity"]],
        "stalled_lanes": [l["lane"] for l in lanes if l["stalled"]],
        "data_health": {"v1_epoch_cohort": epoch.get("cohort"), "v1_epoch_start": epoch.get("epoch_start")},
    }


# ── formatting ──────────────────────────────────────────────────────────────
def _kv(d: dict[str, Any], keys: Iterable[str] | None = None) -> str:
    items = [(k, v) for k, v in d.items() if (keys is None or k in keys) and not isinstance(v, (dict, list))]
    return " · ".join(f"{k} {v}" for k, v in items)


def _short_ts(value: Optional[str]) -> str:
    return (value or "never")[:16].replace("T", " ")


def format_digest(digest: dict[str, Any]) -> str:
    """Render one Discord-ready message. Daily = concise sanity; weekly = progress."""
    period, dom = digest["period"], digest["domain"]
    w = digest["window"]
    head = "📋" if period == "daily" else "📈"
    title = f"{head} **{dom.upper()} paper collection — {period} {w['start']}"
    title += f" → {w['end']}**" if w["start"] != w["end"] else "**"
    lines = [title + " (read-only status, not evidence)"]
    quiet: list[str] = []
    for lane in digest["lanes"]:
        marker = "⚪" if lane["zero_activity"] else "🟢"
        attention = lane["stalled"] or str(lane["health"]).startswith(_ATTENTION_HEALTH)
        if attention:
            marker = "🔴"
        # Daily = operational sanity: a quiet, healthy lane with nothing open is
        # one name in the zero-activity line, not a block. Weekly shows every lane.
        if period == "daily" and lane["zero_activity"] and not attention and not lane.get("open_position"):
            quiet.append(f"{lane['lane']} (last {_short_ts(lane['last_evidence'])})")
            continue
        head_bits = [lane["lane"]]
        if lane.get("campaign_id"):
            head_bits.append(f"`{lane['campaign_id']}`")
        lines.append(f"{marker} **{' '.join(head_bits)}** — {lane['mode']}")
        lines.append(f"   window: {_kv(lane['window']) or 'no activity'}")
        if period == "weekly" or lane.get("review_gate"):
            cum = _kv(lane["cumulative"])
            if cum:
                lines.append(f"   cumulative: {cum}")
        if lane.get("open_position"):
            pos = lane["open_position"]
            desc = (
                f"{pos.get('direction')} @ {pos.get('entry')} since {_short_ts(str(pos.get('entry_time') or pos.get('entry_ts') or ''))}"
                if isinstance(pos, dict) else str(pos)
            )
            lines.append(f"   open position: {desc}")
        gate = lane.get("review_gate")
        if gate and period == "weekly":
            lines.append(f"   review gate: {_kv(gate)}")
        tail = f"   last evidence {_short_ts(lane['last_evidence'])} · health {lane['health']}"
        if lane.get("status"):
            tail += f" · status {lane['status']}"
        if lane.get("epoch") and period == "weekly":
            tail += f" · epoch {str(lane['epoch'])[:19]}"
        lines.append(tail)
        if lane.get("note") and (period == "weekly" or attention or lane.get("status")):
            lines.append(f"   ↳ {lane['note']}")
        if period == "weekly" and isinstance(lane["cumulative"].get("populations"), list):
            pops = lane["cumulative"]["populations"]
            by_status = Counter(str(p["status"]) for p in pops)
            lines.append(
                f"      populations {len(pops)}: " + " · ".join(f"{k} {v}" for k, v in sorted(by_status.items()))
                + f" · terminal {sum(int(p['terminal'] or 0) for p in pops)}"
                + f" · quality-clean {sum(int(p['clean'] or 0) for p in pops)}"
            )
            top = sorted((p for p in pops if p["terminal"]), key=lambda p: -int(p["terminal"]))[:5]
            if top:
                lines.append("      most terminal: " + "; ".join(
                    f"{p['population']} {p['terminal']}/{p['clean']} clean ({p['status']})" for p in top
                ))
    dh = digest["data_health"]
    if dom == "futures":
        bad = list(dh.get("dead_or_absent") or []) + [f"{n} (stale)" for n in dh.get("stale") or []]
        lines.append("**Data health:** " + (", ".join(bad) if bad else "all collectors fresh"))
        if dh.get("mnq_position_exposed_without_fresh_5m_bars"):
            lines.append("⚠️ MNQ position exposed without fresh 5m bars")
    else:
        lines.append(f"**V1 epoch:** {dh.get('v1_epoch_cohort')} from {_short_ts(dh.get('v1_epoch_start'))}")
    if period == "daily" and quiet:
        lines.append("Zero activity (healthy): " + "; ".join(quiet))
    elif digest["zero_activity_lanes"]:
        lines.append("Zero activity: " + ", ".join(digest["zero_activity_lanes"]))
    if digest["stalled_lanes"]:
        lines.append("⚠️ Stalled (collector DEAD/ABSENT or feed stale): " + ", ".join(digest["stalled_lanes"]))
    lines.append("_No strategy judgement; thresholds shown are pre-existing gates only._")
    return "\n".join(lines)


def chunk_message(text: str, limit: int = DISCORD_CHUNK) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [text]
