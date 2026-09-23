"""End-of-day companion paper summary -> DISCORD_OPTIONS_DAILY_REPORT channel.

Run from cron on the box after the options close, e.g.:
    cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m options_companion.daily_report

``build_report`` is pure (no I/O) so it's unit-testable; ``main`` wires the store +
env + Discord post. Fail-soft: a reporting error never affects anything else.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config.settings import options_companion_sqlite_path
from notifications import plain_english as pe

from .notify import notify_companion_daily_report
from .status import companion_summary
from .store import CompanionRow, OptionsCompanionStore


def _fmt_money(value: float) -> str:
    return pe.money(value)


def _win_rate(value) -> str:
    if not isinstance(value, (int, float)):
        return "no finished trades yet"
    return f"{value:.0f}% of finished trades won"


def build_report(rows: list[CompanionRow], summary: dict, *, day_iso: str) -> str:
    """Format a daily report from ledger rows + the all-time summary."""
    created_today = [r for r in rows if (r.created_at or "")[:10] == day_iso]
    opened_today = [r for r in created_today if r.status not in {"REJECTED", "WATCHLIST"}]
    skipped_today = [r for r in created_today if r.status == "REJECTED"]
    watchlist_today = [r for r in created_today if r.status == "WATCHLIST"]

    resolved_today = [
        r for r in rows
        if (r.resolved_at or "")[:10] == day_iso and r.status in {"WIN", "LOSS", "EXPIRED"}
    ]
    wins = sum(1 for r in resolved_today if r.status == "WIN")
    losses = sum(1 for r in resolved_today if r.status == "LOSS")
    expired = sum(1 for r in resolved_today if r.status == "EXPIRED")
    today_pnl = round(sum(r.paper_pnl_dollars or 0.0 for r in resolved_today), 2)

    return "\n".join([
        f"📊 Paper options daily report — {pe.et_date(day_iso)}",
        f"Opened today: **{len(opened_today)}**",
        f"Closed today: **{wins} won, {losses} lost, {expired} expired**",
        f"Result today: **{_fmt_money(today_pnl)}** (paper)",
        f"Not opened today: {len(watchlist_today)} on watch, {len(skipped_today)} skipped",
        (
            f"All time: {summary.get('formed', 0)} trades taken · {summary.get('open', 0)} still open · "
            f"{_win_rate(summary.get('win_rate_percent'))} · "
            f"total {_fmt_money(summary.get('total_paper_pnl_dollars', 0.0))}"
        ),
        "PAPER ONLY · practice tracking, no real money",
    ])


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(".env")
    except Exception:  # noqa: BLE001 — dotenv optional; cron may export env directly
        pass

    store = OptionsCompanionStore(options_companion_sqlite_path())
    day_iso = datetime.now(timezone.utc).date().isoformat()
    report = build_report(store.all_rows(), companion_summary(store), day_iso=day_iso)
    sent = notify_companion_daily_report(report)
    print(f"companion daily report {'sent' if sent else 'NOT sent (disabled / url missing)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
