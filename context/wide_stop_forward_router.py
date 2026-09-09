"""Paper-only router for the three approved MNQ forward evidence lanes.

No external broker is imported or reachable here. The canonical 4HR and 60M
3-2-2 day collectors run first; the isolated Daily 2-2 swing collector then
receives the same completed 5-minute bar and its recent history.

Each day-strategy config copy is conservatively limited to one fill/day and the
Daily lane can produce at most one first-boundary entry/day. Therefore the
three-lane campaign cannot exceed the project-wide three-filled-trades/day cap.
This is intentionally stricter than the underlying day collectors' historical
per-ledger counter and does not modify the real config object.
"""
from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any, Optional

from context.daily_22_state_integrity import assert_state_integrity
from context.daily_22_swing_collector import process_five_min_bar as process_daily_22
from context.wide_stop_forward_collector import process_five_min_bar as process_wide_stop


def process_paper_five_min_bar(
    *,
    payload,
    cfg,
    bars_5m: list[dict],
    log_dir: str | Path,
    for_date: Optional[date] = None,
) -> list[dict[str, Any]]:
    """Feed one completed MNQ 5m bar to paper-only evidence collectors.

    Daily multi-day state is proof-critical. A malformed, unreadable,
    wrong-epoch, or unexpectedly missing persisted state raises before the Daily
    collector can reset itself or admit a second swing. The outer 5-minute feed
    catches the error and leaves market-data ingestion alive while the Daily lane
    fails closed.
    """
    events: list[dict[str, Any]] = []

    # Safety overlay only on the isolated copy. With one possible Daily first
    # break, 4HR<=1 + 3-2-2<=1 + Daily<=1 guarantees <=3 campaign fills/day.
    day_cfg = copy.copy(cfg)
    day_cfg.max_trades_per_day = 1
    day_cfg.bonus_trades_after_max = 0
    events.extend(
        process_wide_stop(
            payload=payload,
            cfg=day_cfg,
            bars_5m=bars_5m,
            log_dir=log_dir,
            for_date=for_date,
        )
    )

    assert_state_integrity(log_dir, cfg)
    events.extend(
        process_daily_22(
            payload=payload,
            cfg=cfg,
            bars_5m=bars_5m,
            log_dir=log_dir,
            for_date=for_date,
        )
    )
    return events
