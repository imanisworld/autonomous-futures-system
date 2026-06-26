"""Read-only summary of the companion paper ledger for /status surfaces.

No live data fetch — pure ledger aggregation, mirroring
``alert_ranker.storage.ScanStorage.shadow_summary``.
"""

from __future__ import annotations

from typing import Any

from .store import OptionsCompanionStore


def companion_summary(store: OptionsCompanionStore) -> dict[str, Any]:
    rows = store.all_rows()
    counts = {s: 0 for s in ("OPEN", "WIN", "LOSS", "EXPIRED", "REJECTED", "WATCHLIST")}
    total_pnl = 0.0
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        if row.status in {"WIN", "LOSS", "EXPIRED"}:
            total_pnl += row.paper_pnl_dollars or 0.0

    wins = counts.get("WIN", 0)
    losses = counts.get("LOSS", 0)
    decided = wins + losses
    win_rate = round((wins / decided) * 100.0, 2) if decided else None
    formed = wins + losses + counts.get("EXPIRED", 0) + counts.get("OPEN", 0)

    return {
        "total": len(rows),
        "formed": formed,
        "open": counts.get("OPEN", 0),
        "wins": wins,
        "losses": losses,
        "expired": counts.get("EXPIRED", 0),
        "rejected": counts.get("REJECTED", 0),
        "watchlist": counts.get("WATCHLIST", 0),
        "win_rate_percent": win_rate,
        "total_paper_pnl_dollars": round(total_pnl, 2),
    }
