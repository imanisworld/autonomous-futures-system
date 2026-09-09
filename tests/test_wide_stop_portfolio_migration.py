"""Migration safety for the shared futures campaign daily cap."""
from __future__ import annotations

import json
from datetime import date

from context import wide_stop_ledger_paper as contract
from context import wide_stop_portfolio as portfolio

DAY = date(2026, 9, 8)


def _legacy_count(tmp_path, ledger_name: str, count: int) -> None:
    ledger = contract.LEDGERS[ledger_name]
    path = contract.journal_dir(tmp_path, ledger) / "forward_collector_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "filled_date": DAY.isoformat(),
        "filled_count": count,
        "position": None,
        "seen": [],
    }))


def test_midday_activation_inherits_legacy_fill_counts(tmp_path):
    _legacy_count(tmp_path, "wide_stop_4k", 2)
    _legacy_count(tmp_path, "wide_stop_6k", 1)

    assert portfolio.legacy_filled_floor(tmp_path, DAY) == 3
    assert portfolio.daily_slots_used(tmp_path, DAY) == 3
    ok, reason = portfolio.reserve_daily_slot(
        tmp_path, DAY, "would-be-fourth", route="paper_sim"
    )
    assert not ok
    assert reason == "portfolio_max_trades_per_day"


def test_old_date_counts_do_not_poison_new_day(tmp_path):
    ledger = contract.LEDGERS["wide_stop_4k"]
    path = contract.journal_dir(tmp_path, ledger) / "forward_collector_state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "filled_date": "2026-09-07",
        "filled_count": 3,
        "position": None,
        "seen": [],
    }))

    assert portfolio.legacy_filled_floor(tmp_path, DAY) == 0
    ok, reason = portfolio.reserve_daily_slot(tmp_path, DAY, "first", route="paper_sim")
    assert ok
    assert reason == "reserved"
