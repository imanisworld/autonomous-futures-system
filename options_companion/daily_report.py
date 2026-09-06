"""End-of-day companion paper summary -> DISCORD_OPTIONS_DAILY_REPORT channel.

Run from cron on the box after the options close, e.g.:
    cd /root/autonomous-futures-system && PYTHONPATH=. .venv/bin/python -m options_companion.daily_report

``build_report`` is pure (no I/O) so it's unit-testable; ``main`` wires the store +
env + Discord post. Fail-soft: a reporting error never affects anything else.
"""

from __future__ import annotations

from datetime import datetime, timezone

from config.settings import options_companion_sqlite_path

from .notify import notify_companion_daily_report
from .status import companion_summary
from .store import CompanionRow, OptionsCompanionStore


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _pct(value) -> str:
    return f"{value:.1f}%" if isinstance(value, (int, float)) else "n/a"


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
        f"📊 **Options companion — daily paper report ({day_iso})**",
        (
            f"Today: **{len(opened_today)}** opened · "
            f"**{wins}W / {losses}L / {expired}exp** · "
            f"paper P&L **{_fmt_money(today_pnl)}** · "
            f"{len(watchlist_today)} watchlist · {len(skipped_today)} skipped"
        ),
        (
            f"All-time: {summary.get('formed', 0)} formed · {summary.get('open', 0)} open · "
            f"win rate {_pct(summary.get('win_rate_percent'))} · "
            f"total {_fmt_money(summary.get('total_paper_pnl_dollars', 0.0))}"
        ),
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
