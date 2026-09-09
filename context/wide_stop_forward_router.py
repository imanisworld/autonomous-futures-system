"""Router for the three approved MNQ forward evidence lanes.

4HR and 60M 3-2-2 use the shared wide-stop day collector. Daily 2-2 remains a
separate PaperBroker-only swing collector. The shared three-filled-trades/day
campaign limit is enforced at actual admission points by ``wide_stop_portfolio``;
this router does not distort any strategy by imposing an artificial one-fill/day
cap on its copied config.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

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
    """Feed one completed MNQ 5m bar to the isolated evidence collectors."""
    events: list[dict[str, Any]] = []
    events.extend(
        process_wide_stop(
            payload=payload,
            cfg=cfg,
            bars_5m=bars_5m,
            log_dir=log_dir,
            for_date=for_date,
        )
    )
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
