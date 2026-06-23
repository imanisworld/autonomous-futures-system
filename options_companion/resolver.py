"""Refresh open companion paper positions and mark WIN / LOSS / EXPIRED.

On each webhook the runner calls ``run_companion_resolve``. For every OPEN row we
fetch the current option mid and decide:
    mid >= target_mark -> WIN
    mid <= stop_mark   -> LOSS
    expiry already past -> EXPIRED (still open at/after expiry)
P&L mirrors the advisory store: ``(exit - entry) * 100 * qty`` (qty=1).
Never touches futures state.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from typing import Any, Optional

from .chain_provider import ChainProvider
from .store import OptionsCompanionStore


def _pnl(entry: float, exit_mark: float) -> tuple[float, float]:
    dollars = round((exit_mark - entry) * 100.0, 2)
    percent = round(((exit_mark - entry) / entry) * 100.0, 2) if entry else 0.0
    return dollars, percent


async def resolve_open_companions(
    provider: ChainProvider,
    store: OptionsCompanionStore,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    today: date = now.astimezone(timezone.utc).date()
    resolved: list[dict[str, Any]] = []

    for row in store.open_positions():
        if row.entry_mark is None or row.stop_mark is None or row.target_mark is None:
            continue
        quote = await provider.fetch_quote(
            row.option_symbol or "", underlying=row.underlying, expiry=row.expiry
        )
        mid = quote.mid
        if mid is None:
            # No mark this tick. Only force EXPIRED once the contract is past expiry.
            if _expired(row.expiry, today):
                store.resolve(row.id, status="EXPIRED", paper_pnl_dollars=0.0, paper_pnl_percent=0.0, resolved_at=now)
                resolved.append({"option_symbol": row.option_symbol, "underlying": row.underlying,
                                 "id": row.id, "status": "EXPIRED", "reason": "no_mark_past_expiry"})
            continue

        ctx = {"option_symbol": row.option_symbol, "underlying": row.underlying}
        if mid >= row.target_mark:
            dollars, percent = _pnl(row.entry_mark, row.target_mark)
            store.resolve(row.id, status="WIN", paper_pnl_dollars=dollars, paper_pnl_percent=percent, resolved_at=now)
            resolved.append({**ctx, "id": row.id, "status": "WIN", "pnl_dollars": dollars})
        elif mid <= row.stop_mark:
            dollars, percent = _pnl(row.entry_mark, row.stop_mark)
            store.resolve(row.id, status="LOSS", paper_pnl_dollars=dollars, paper_pnl_percent=percent, resolved_at=now)
            resolved.append({**ctx, "id": row.id, "status": "LOSS", "pnl_dollars": dollars})
        elif _expired(row.expiry, today):
            dollars, percent = _pnl(row.entry_mark, mid)
            store.resolve(row.id, status="EXPIRED", paper_pnl_dollars=dollars, paper_pnl_percent=percent, resolved_at=now)
            resolved.append({**ctx, "id": row.id, "status": "EXPIRED", "pnl_dollars": dollars})

    return {"resolved": resolved}


def _expired(expiry: str | None, today: date) -> bool:
    if not expiry:
        return False
    try:
        return date.fromisoformat(expiry[:10]) < today
    except ValueError:
        return False


def run_companion_resolve(
    provider: ChainProvider,
    store: OptionsCompanionStore,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Sync bridge for the runner hook (which executes off the event loop)."""

    async def _run() -> dict[str, Any]:
        async with provider:  # type: ignore[union-attr]
            return await resolve_open_companions(provider, store, now=now)

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — surface to Discord, then re-raise for the runner
        from .notify import notify_companion_error

        notify_companion_error(f"resolve: {exc}")
        raise
    from .notify import notify_companion_resolved

    notify_companion_resolved(result)
    return result
