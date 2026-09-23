"""End-of-week trading review -> DISCORD_ROUTE_DAILY_REPORT channel.

Run from cron on the box on Friday after the futures close, e.g.:
    cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m scripts.weekly_review

Read-only + fail-soft: a reporting error never touches trading state. The
``summarize_week`` / ``format_report`` functions are pure (no I/O) so they are
unit-testable; ``main`` wires the journal files + options ledger + journald +
env + the Discord post, and writes a ``logs/weekly_review_YYYY-Www.json`` artifact
so week-over-week trends (fill rate, win rate) can be compared.

Posts via a plain webhook URL resolved from the environment
(``DISCORD_ROUTE_DAILY_REPORT`` preferred, ``DISCORD_WEBHOOK_URL`` fallback) so it
does not depend on the box-only notification router. If no webhook is configured
(e.g. CI), it just writes the artifact and prints the report.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:  # card layout when run from a release; plain text from a standalone copy
    from notifications.discord_card import post_card_or_text as _post_card_or_text
except ImportError:  # pragma: no cover - standalone copy without the package
    _post_card_or_text = None

from config.settings import options_companion_sqlite_path

_FILLED = {"WIN", "LOSS", "BREAKEVEN", "BE"}


# ── pure helpers ───────────────────────────────────────────────────────────
def week_bounds(ref: date) -> tuple[date, date]:
    """Monday..Sunday of the ISO week containing ``ref``."""
    monday = ref - timedelta(days=ref.weekday())
    return monday, monday + timedelta(days=6)


def iso_week_label(ref: date) -> str:
    y, w, _ = ref.isocalendar()
    return f"{y}-W{w:02d}"


def _pct(num: int, den: int) -> str:
    return f"{(100.0 * num / den):.0f}%" if den else "n/a"


def summarize_week(
    journal: list[dict],
    option_rows: list[dict],
    *,
    health: Optional[dict] = None,
) -> dict:
    """Pure aggregation of one week of journal records + option ledger rows."""
    decisions: Counter = Counter()
    results: Counter = Counter()
    pnl_by_inst: Counter = Counter()
    outcomes_by_inst: Counter = Counter()
    total_pnl = 0.0

    for rec in journal:
        if rec.get("type") == "OUTCOME":
            outcome = rec.get("outcome") or {}
            res = (outcome.get("result") if isinstance(outcome, dict) else None) or "?"
            results[res] += 1
            inst = rec.get("instrument") or "?"
            outcomes_by_inst[inst] += 1
            pnl = outcome.get("pnl_dollars") if isinstance(outcome, dict) else None
            if isinstance(pnl, (int, float)):
                total_pnl += pnl
                pnl_by_inst[inst] += pnl
        elif rec.get("decision"):
            decisions[rec["decision"]] += 1

    cancelled = results.get("CANCELLED", 0)
    filled = sum(v for k, v in results.items() if k in _FILLED)
    wins = results.get("WIN", 0)
    losses = results.get("LOSS", 0)
    attempted = filled + cancelled

    # options ledger
    opt_status: Counter = Counter()
    opt_rejects: Counter = Counter()
    opt_pnl = 0.0
    for row in option_rows:
        status = row.get("status") or "?"
        opt_status[status] += 1
        if status == "REJECTED":
            opt_rejects[row.get("risk_failed_rule") or "?"] += 1
        p = row.get("paper_pnl_dollars")
        if isinstance(p, (int, float)):
            opt_pnl += p

    return {
        "decisions": dict(decisions),
        "approved_trades": decisions.get("TRADE", 0),
        "no_trade": decisions.get("NO_TRADE", 0),
        "risk_rejected": decisions.get("RISK_REJECTED", 0),
        "outcomes": dict(results),
        "filled": filled,
        "cancelled": cancelled,
        "attempted": attempted,
        "fill_rate_pct": round(100.0 * filled / attempted, 1) if attempted else None,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(100.0 * wins / filled, 1) if filled else None,
        "pnl_total": round(total_pnl, 2),
        "pnl_by_instrument": {k: round(v, 2) for k, v in pnl_by_inst.items()},
        "outcomes_by_instrument": dict(outcomes_by_inst),
        "options": {
            "candidates": sum(opt_status.values()),
            "opened": sum(v for k, v in opt_status.items() if k not in {"REJECTED", "WATCHLIST"}),
            "status": dict(opt_status),
            "rejects_by_reason": dict(opt_rejects),
            "paper_pnl": round(opt_pnl, 2),
        },
        "health": health or {},
    }


def week_words(monday: date, sunday: date) -> str:
    """``Week of Jun 22–28`` (``Week of Jun 29–Jul 5`` across months)."""
    start = f"{monday.strftime('%b')} {monday.day}"
    end = f"{sunday.day}" if sunday.month == monday.month else f"{sunday.strftime('%b')} {sunday.day}"
    return f"Week of {start}–{end}"


def _signed(v) -> str:
    if not isinstance(v, (int, float)):
        return "n/a"
    return f"{'-' if v < 0 else '+'}${abs(v):,.2f}"


def _plural(n, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def format_report(data: dict, *, week: str, monday: date, sunday: date) -> str:
    """Render the scorecard as a plain-English Discord card.

    ``week`` (ISO label) stays the artifact key; the message says "Week of …"
    (docs/discord-operator-message-style.md).
    """
    opt = data["options"]
    h = data.get("health") or {}
    lines = [
        f"\U0001F4C5 **Weekly review · {week_words(monday, sunday)}**",
        f"Practice trades taken: **{data['approved_trades']}**",
        f"No setup: {data['no_trade']}",
        f"Skipped by risk limits: {data['risk_rejected']}",
        (
            f"Orders filled: **{data['filled']} of {data['attempted']}** "
            f"({_pct(data['filled'], data['attempted'])}) · {data['cancelled']} never filled"
        ),
        (
            f"Won / lost: **{data['wins']} won, {data['losses']} lost** "
            f"({_pct(data['wins'], data['filled'])} won)"
        ),
        f"Profit: **{_signed(data['pnl_total'])}**",
    ]
    if data["pnl_by_instrument"]:
        by = " · ".join(f"{k} {_signed(v)}" for k, v in sorted(data["pnl_by_instrument"].items()))
        lines.append(f"By market: {by}")
    options = (
        f"Options: {_plural(opt['candidates'], 'idea')} · **{opt['opened']}** opened · "
        f"practice profit {_signed(opt['paper_pnl'])}"
    )
    if opt["rejects_by_reason"]:
        top = max(opt["rejects_by_reason"], key=opt["rejects_by_reason"].get)
        options += f" · most common skip: {str(top).replace('_', ' ').lower()}"
    lines.append(options)
    if h:
        lines.append(
            f"Server: {_plural(h.get('errors', '?'), 'error')} · "
            f"{_plural(h.get('breaker_events', '?'), 'safety stop')} · "
            f"{_plural(h.get('restarts', '?'), 'restart')}"
        )
    lines.append("[practice account · read only]")
    return "\n".join(lines)


# ── I/O (impure, fail-soft) ────────────────────────────────────────────────
def load_journal(log_dir: Path, monday: date, sunday: date) -> list[dict]:
    recs: list[dict] = []
    for f in sorted(log_dir.glob("journal_*.jsonl")):
        try:
            d = date.fromisoformat(f.stem[len("journal_"):])
        except ValueError:
            continue
        if not (monday <= d <= sunday):
            continue
        try:
            for ln in f.read_text().splitlines():
                ln = ln.strip()
                if ln:
                    try:
                        recs.append(json.loads(ln))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue
    return recs


def load_option_rows(sqlite_path: Path, monday: date, sunday: date) -> list[dict]:
    if not sqlite_path.exists():
        return []
    try:
        conn = sqlite3.connect(str(sqlite_path))
        conn.row_factory = sqlite3.Row
        rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM options_companion")
        ]
        conn.close()
    except sqlite3.Error:
        return []
    lo, hi = monday.isoformat(), sunday.isoformat()
    return [r for r in rows if lo <= (r.get("created_at") or "")[:10] <= hi]


def collect_health(monday: date) -> dict:
    """Best-effort journald scrape. Returns {} if journalctl is unavailable."""
    try:
        out = subprocess.run(
            ["journalctl", "-u", "futures-bot", "--since", monday.isoformat(), "--no-pager"],
            capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    low = out.lower()
    # Expected limit-misses now log at WARNING (ENTRY_NOT_FILLED), so error-level
    # lines are genuine failures — count them directly, no string-exclusion needed.
    real_errors = sum(
        1 for ln in out.splitlines()
        if any(t in ln.lower() for t in ("error", "traceback", "exception", "critical"))
    )
    return {
        "errors": real_errors,
        "breaker_events": sum(low.count(t) for t in ("breaker", "outage")),
        "restarts": out.count("Started futures-bot"),
    }


def _post_discord(webhook_url: str, content: str) -> bool:
    def _post(payload: dict) -> int:
        req = urllib.request.Request(
            webhook_url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status

    try:
        if _post_card_or_text is not None:
            status = _post_card_or_text(_post, content, source="weekly review")
        else:
            status = _post({"content": content})
        return 200 <= status < 300
    except Exception:
        return False


def main(argv: Optional[list[str]] = None) -> int:
    ref_env = os.getenv("WEEKLY_REVIEW_DATE", "").strip()
    ref = date.fromisoformat(ref_env) if ref_env else datetime.now(timezone.utc).date()
    monday, sunday = week_bounds(ref)
    week = iso_week_label(ref)

    log_dir = Path(os.getenv("LOG_DIR", "logs"))
    journal = load_journal(log_dir, monday, sunday)
    options = load_option_rows(options_companion_sqlite_path(), monday, sunday)
    health = collect_health(monday)

    data = summarize_week(journal, options, health=health)
    report = format_report(data, week=week, monday=monday, sunday=sunday)

    # Trend artifact (best-effort).
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"weekly_review_{week}.json").write_text(
            json.dumps({"week": week, "generated_at": datetime.now(timezone.utc).isoformat(), **data}, indent=2)
        )
    except OSError:
        pass

    webhook = (os.getenv("DISCORD_ROUTE_DAILY_REPORT") or os.getenv("DISCORD_WEBHOOK_URL") or "").strip()
    if webhook:
        ok = _post_discord(webhook, report)
        print(f"[weekly_review] posted to Discord: {ok}")
    else:
        print("[weekly_review] no webhook configured; artifact written only")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
