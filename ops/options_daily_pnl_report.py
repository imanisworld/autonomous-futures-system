#!/usr/bin/env python3
"""Daily options paper P&L report (read-only, evidence only).

Options counterpart of ops/shadow_daily_pnl_report.py. Reads the options
scanner's SQLite (`options_shadow_journal`, `options_episode_blocks`, `scans`)
in read-only mode and adds up one New York trading day:

* Paper trades (ACTIVE lane — the same rows /shadow-journal/summary counts):
  opened today, closed today (by `outcome.resolved_at`), dollars today, all-time.
* Blocked today: first-look ACTIVE episodes refused (ENTRY_LATE reasons).
* What-if lane (paper_evidence_lane = COUNTERFACTUAL): rows the filters turned
  away, priced anyway. Reported separately and labelled — never mixed into the
  paper-trade dollars.

Dollars are the scanner's own `pnl_dollars` (entry at ask, exit at bid, no
commission). Never grants eligibility. Never changes a rule.

Your limits (operator rule, 2026-09-29, options counterpart): the same paper
trades replayed with at most 3 new trades per New York day — once for the whole
account and once per ticker — in entry order (ties by journal id). Options trade
in the day session only. The scanner's own position and risk rules are untouched:
this only leaves out a day's 4th and later entries. Shown for today and all time,
next to the actual (no limit) result.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notifications import plain_english as pe  # noqa: E402
from ops.gate_condition_report import _post_discord  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_DB = "logs/options_scanner.sqlite"
COUNTERFACTUAL_MARK = '"paper_evidence_lane": "COUNTERFACTUAL"'
CLOSED = ("WIN", "LOSS", "BREAKEVEN", "EXPIRED")
CONSUMED = ("TARGET_CONSUMED_AT_ENTRY", "STOP_CONSUMED_AT_ENTRY")


def _et_day(value: object) -> date | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if ts.tzinfo is None:  # scanner writes aware UTC; treat naive as UTC
        ts = ts.replace(tzinfo=ZoneInfo("UTC"))
    return ts.astimezone(ET).date()


def _empty() -> dict:
    return {"opened": 0, "closed": 0, "wins": 0, "losses": 0, "other_closed": 0, "open": 0,
            "consumed_at_entry": 0, "pnl_usd": 0.0}


def load_rows(db_path: str | Path, day: date) -> dict:
    """Read the three tables read-only. Missing DB/tables -> empty lists.
    Scans are only read from the day before `day` onward (the table is large)."""
    out: dict = {"journal": [], "blocks": [], "scans": []}
    path = Path(db_path)
    if not path.exists():
        return out
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        # REJECTED rows are ~all of the table and carry no dollars; skip them.
        for row_id, ts, ticker, status, contract, outcome in conn.execute(
            "SELECT id, timestamp, ticker, status, selected_contract_json, outcome_json "
            "FROM options_shadow_journal WHERE status != 'REJECTED'"
        ):
            try:
                outcome_d = json.loads(outcome or "{}")
            except json.JSONDecodeError:
                outcome_d = {}
            out["journal"].append({
                "id": row_id, "timestamp": ts, "ticker": ticker, "status": status,
                "counterfactual": COUNTERFACTUAL_MARK in (contract or ""),
                "resolved_at": outcome_d.get("resolved_at"),
                "pnl_dollars": outcome_d.get("pnl_dollars"),
            })
        try:
            out["blocks"] = [
                {"blocked_at": b, "reason": r}
                for b, r in conn.execute("SELECT blocked_at, reason FROM options_episode_blocks")
            ]
        except sqlite3.OperationalError:
            pass
        try:
            out["scans"] = [
                {"timestamp": t, "alert_sent": bool(a)}
                for t, a in conn.execute(
                    "SELECT timestamp, alert_sent FROM scans WHERE timestamp >= ?",
                    ((day - timedelta(days=1)).isoformat(),),
                )
            ]
        except sqlite3.OperationalError:
            pass
    finally:
        conn.close()
    return out


def _tally(rows: list[dict], day: date) -> dict:
    b = _empty()
    for r in rows:
        status = r["status"]
        if _et_day(r["timestamp"]) == day:
            if status in CONSUMED:
                b["consumed_at_entry"] += 1
            elif status != "CANCELLED":
                b["opened"] += 1
        if status == "OPEN":
            b["open"] += 1
        if status in CLOSED and _et_day(r.get("resolved_at") or r["timestamp"]) == day:
            b["closed"] += 1
            if status == "WIN":
                b["wins"] += 1
            elif status == "LOSS":
                b["losses"] += 1
            else:
                b["other_closed"] += 1
            try:
                b["pnl_usd"] = round(b["pnl_usd"] + float(r.get("pnl_dollars") or 0.0), 2)
            except (TypeError, ValueError):
                pass
    return b


def _all_time(rows: list[dict]) -> dict:
    closed = [r for r in rows if r["status"] in CLOSED]
    pnl = 0.0
    for r in closed:
        try:
            pnl += float(r.get("pnl_dollars") or 0.0)
        except (TypeError, ValueError):
            pass
    return {
        "closed": len(closed),
        "wins": sum(r["status"] == "WIN" for r in closed),
        "losses": sum(r["status"] == "LOSS" for r in closed),
        "open": sum(r["status"] == "OPEN" for r in rows),
        "pnl_usd": round(pnl, 2),
    }


def _block_family(reason: str) -> str:
    """'ENTRY_LATE:remaining_rr_0.45_below_1.00' -> 'ENTRY_LATE:remaining_rr_below_min'."""
    head, _, tail = str(reason).partition(":")
    if tail.startswith("remaining_rr_"):
        return f"{head}:remaining_rr_below_min"
    return str(reason)


DAILY_CAP = 3


def apply_daily_cap(rows: list[dict], scope: str, cap: int = DAILY_CAP) -> list[dict]:
    """Paper trades kept when at most `cap` open per New York day ('account' or 'per_ticker')."""
    opened = [r for r in rows if r["status"] not in CONSUMED and r["status"] != "CANCELLED"]
    opened.sort(key=lambda r: (str(r["timestamp"]), r.get("id") or 0))
    taken: Counter = Counter()
    kept = []
    for r in opened:
        slot = (_et_day(r["timestamp"]), "account" if scope == "account" else r.get("ticker"))
        if taken[slot] < cap:
            taken[slot] += 1
            kept.append(r)
    return kept


def _cap_summary(rows: list[dict], first: date | None = None, last: date | None = None) -> dict:
    """Closed trades (closed first..last, or all time) with dollars and the deepest drop in closing order."""
    def _in(r: dict) -> bool:
        d = _et_day(r.get("resolved_at") or r["timestamp"])
        return (first is None or (d is not None and d >= first)) and (last is None or (d is not None and d <= last))
    closed = [r for r in rows if r["status"] in CLOSED and _in(r)]
    closed.sort(key=lambda r: (str(r.get("resolved_at") or r["timestamp"]), r.get("id") or 0))
    pnl = peak = drop = 0.0
    for r in closed:
        try:
            pnl += float(r.get("pnl_dollars") or 0.0)
        except (TypeError, ValueError):
            pass
        peak, drop = max(peak, pnl), max(drop, max(peak, pnl) - pnl)
    return {"closed": len(closed), "wins": sum(r["status"] == "WIN" for r in closed),
            "losses": sum(r["status"] == "LOSS" for r in closed), "pnl_usd": round(pnl, 2),
            "max_drawdown_usd": round(drop, 2)}


def capped_views(active: list[dict], cap: int = DAILY_CAP) -> dict:
    return {"account": apply_daily_cap(active, "account", cap),
            "per_ticker": apply_daily_cap(active, "per_ticker", cap),
            "no_limit": apply_daily_cap(active, "account", cap=10 ** 9)}


def capped_period(active: list[dict], first: date, last: date, cap: int = DAILY_CAP) -> dict:
    """Your-limits summary of trades closed first..last (e.g. a week), per view."""
    return {k: _cap_summary(v, first, last) for k, v in capped_views(active, cap).items()}


def capped_view(active: list[dict], day: date, cap: int = DAILY_CAP) -> dict:
    views = capped_views(active, cap)
    opened_today = sum(_et_day(r["timestamp"]) == day for r in views["no_limit"])
    return {
        "rule": {"per_day": cap, "scopes": ["account", "per_ticker"], "positions": "scanner rules unchanged"},
        "opened_today": opened_today,
        "left_out_today": {k: opened_today - sum(_et_day(r["timestamp"]) == day for r in v)
                           for k, v in views.items() if k != "no_limit"},
        "today": {k: _cap_summary(v, day, day) for k, v in views.items()},
        "all_time": {k: _cap_summary(v) for k, v in views.items()},
    }


def build_report(data: dict, day: date) -> dict:
    active = [r for r in data["journal"] if not r["counterfactual"]]
    whatif = [r for r in data["journal"] if r["counterfactual"]]
    blocks = Counter(_block_family(b["reason"]) for b in data["blocks"] if _et_day(b["blocked_at"]) == day)
    scans_today = [s for s in data["scans"] if _et_day(s["timestamp"]) == day]
    return {
        "generated_at": datetime.now(ET).isoformat(),
        "day": day.isoformat(),
        "paper": _tally(active, day),
        "paper_all_time": _all_time(active),
        "blocked": dict(blocks.most_common()),
        "what_if": _tally(whatif, day),
        "scans": {"count": len(scans_today), "alerts_sent": sum(s["alert_sent"] for s in scans_today)},
        "capped": capped_view(active, day),
        "cost_model": "scanner pnl_dollars: entry at ask, exit at bid, no commission",
        "authority": "evidence_only",
    }


_BLOCK_WORDS = {
    "ENTRY_LATE:price_past_target": "price already past target",
    "ENTRY_LATE:remaining_rr_below_min": "too little reward left",
}


def _money(v: float) -> str:
    return pe.money(round(v))[:-3]


def format_digest(rep: dict) -> str:
    p, a, w = rep["paper"], rep["paper_all_time"], rep["what_if"]
    lines = [f"🧮 **Options paper P&L · {pe.et_date(rep['day'])}**"]
    if p["opened"] or p["closed"]:
        lines.append(
            f"Paper trades: {p['opened']} opened · {p['closed']} closed "
            f"({p['wins']} won, {p['losses']} lost) · {_money(p['pnl_usd'])} today"
        )
    else:
        lines.append("Paper trades: none opened or closed today")
    lines.append(f"Still open: {a['open']}")
    if rep["blocked"]:
        parts = [f"{n} {_BLOCK_WORDS.get(k, k)}" for k, n in rep["blocked"].items()]
        lines.append(f"Setups refused (too late): {sum(rep['blocked'].values())} — {' · '.join(parts)}")
    lines.append(
        f"All time: {a['closed']} closed, {a['wins']} won, {a['losses']} lost, {_money(a['pnl_usd'])}"
    )
    if w["opened"] or w["closed"]:
        lines.append(
            f"What-if (filtered out, not trades): {w['closed']} closed "
            f"({w['wins']} won, {w['losses']} lost) · {_money(w['pnl_usd'])} · "
            f"{w['consumed_at_entry']} already done at entry"
        )
    lines.append(f"Scans: {rep['scans']['count']} · alerts sent: {rep['scans']['alerts_sent']}")
    lines.extend(_capped_lines(rep.get("capped")))
    lines.append("[paper only · 1 contract · entry at ask, exit at bid, no commission · no rule change]")
    return "\n".join(lines)


def _cap_line(label: str, s: dict) -> str:
    if not s["closed"]:
        return f"{label}: no closed trades"
    return (f"{label}: {s['closed']} closed, {s['wins']} won, {s['losses']} lost, {_money(s['pnl_usd'])},"
            f" deepest drop {pe.money(round(s['max_drawdown_usd']), signed=False)[:-3]}")


def _capped_lines(c: dict | None) -> list[str]:
    if not c:
        return []
    left = c["left_out_today"]
    lines = [f"**Your limits** — at most {c['rule']['per_day']} new paper trades a day",
             f"Today: {c['opened_today']} opened · left out by the limit: {left['account']} (whole account),"
             f" {left['per_ticker']} (per ticker)"]
    for name, label in (("account", "whole account"), ("per_ticker", "each ticker separately"),
                        ("no_limit", "no limit (actual)")):
        lines.append(_cap_line(f"All time, {label}", c["all_time"][name]))
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("OPTIONS_SCANNER_SQLITE_PATH", DEFAULT_DB))
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument("--day", default=None, help="trading day YYYY-MM-DD (default: today in New York)")
    parser.add_argument("--json", action="store_true", help="print JSON instead of the digest")
    parser.add_argument("--discord", action="store_true", help="post the digest to DISCORD_OPTIONS_DAILY_REPORT")
    args = parser.parse_args(argv)

    day = date.fromisoformat(args.day) if args.day else datetime.now(ET).date()
    report = build_report(load_rows(args.db, day), day)
    try:
        text = json.dumps(report, indent=2)
        (Path(args.log_dir) / f"options_daily_pnl_{day.isoformat()}.json").write_text(text)
        (Path(args.log_dir) / "options_daily_pnl_latest.json").write_text(text)
    except OSError:
        pass
    digest = format_digest(report)
    print(json.dumps(report, indent=2) if args.json else digest)
    if args.discord:
        url = os.getenv("DISCORD_OPTIONS_DAILY_REPORT") or os.getenv("DISCORD_ROUTE_DAILY_REPORT")
        if url:
            print("discord:", "ok" if _post_discord(url, digest) else "FAILED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
