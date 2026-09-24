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
        for ts, status, contract, outcome in conn.execute(
            "SELECT timestamp, status, selected_contract_json, outcome_json "
            "FROM options_shadow_journal WHERE status != 'REJECTED'"
        ):
            try:
                outcome_d = json.loads(outcome or "{}")
            except json.JSONDecodeError:
                outcome_d = {}
            out["journal"].append({
                "timestamp": ts, "status": status,
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
    lines.append("[paper only · 1 contract · entry at ask, exit at bid, no commission · no rule change]")
    return "\n".join(lines)


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
