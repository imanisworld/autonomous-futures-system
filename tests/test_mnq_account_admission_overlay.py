from __future__ import annotations

from pathlib import Path

import pytest

from research.mnq_account_admission_overlay import (
    DRAWDOWN_GATE_NOT_EVALUATED,
    DRAWDOWN_NOT_APPLIED,
    DRAWDOWN_WITHIN_FLOOR,
    AccountSeed,
    apply_account_admission,
)
from research.mnq_combined_portfolio_audit import PortfolioEvent, replay_portfolio


def event(
    source_id: str,
    family: str,
    fill: str,
    exit_: str | None,
    *,
    day: str,
    pnl: float,
    result: str | None = None,
) -> PortfolioEvent:
    return PortfolioEvent(
        family=family,
        source_id=source_id,
        signal_ts=fill,
        eligible_fill_ts=fill,
        exit_ts=exit_,
        observation_day=day,
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        result=result or ("WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"),
        net_pnl=pnl,
        session="new_york",
        source="unit",
    )


def test_capacity_matches_frozen_replay_when_daily_loss_does_not_bind():
    rows = [
        event("st", "SUSTAINED_TREND_V1", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=10),
        event("daily", "DAILY_22_COMPLETED_CLOSE", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=10),
        event("4hr", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=10),
        event("later", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=5),
    ]
    frozen = replay_portfolio(rows)
    overlay = apply_account_admission(rows)
    assert [item.source_id for item in overlay.fills] == [item.source_id for item in frozen.fills]
    assert [item.disposition for item in overlay.decisions] == [
        item.disposition for item in frozen.decisions
    ]
    assert overlay.exact_timestamp_collisions == frozen.exact_timestamp_collisions
    assert overlay.drawdown_gate == DRAWDOWN_GATE_NOT_EVALUATED
    assert all(item.drawdown_gate == DRAWDOWN_GATE_NOT_EVALUATED for item in overlay.decisions)


def test_daily_loss_skips_without_consuming_capacity_and_frees_the_next_journal_day():
    rows = [
        event(
            "loss",
            "4HR_RETRIGGER",
            "2026-01-05T15:00:00+00:00",
            "2026-01-05T16:00:00+00:00",
            day="2026-01-05",
            pnl=-200,
        ),
        event(
            "same-day",
            "60M_322_FIRST_LIVE",
            "2026-01-05T17:00:00+00:00",
            "2026-01-06T18:00:00+00:00",
            day="2026-01-05",
            pnl=10,
        ),
        event(
            "next-day",
            "DAILY_22_COMPLETED_CLOSE",
            "2026-01-06T15:30:00+00:00",
            "2026-01-06T16:00:00+00:00",
            day="2026-01-06",
            pnl=10,
        ),
    ]
    overlay = apply_account_admission(rows)
    dispositions = {item.source_id: item.disposition for item in overlay.decisions}
    assert dispositions["loss"] == "FILLED"
    assert dispositions["same-day"] == "SKIPPED_DAILY_LOSS"
    assert dispositions["next-day"] == "FILLED"
    assert [item.source_id for item in overlay.fills] == ["loss", "next-day"]

    frozen = replay_portfolio(rows)
    assert [item.source_id for item in frozen.fills] == ["loss", "same-day"]
    frozen_dispositions = {item.source_id: item.disposition for item in frozen.decisions}
    assert frozen_dispositions["next-day"] == "SKIPPED_BUSY_PORTFOLIO"


def test_overnight_loss_stays_on_the_entry_journal_day():
    """A close after UTC midnight is logged to the open's journal day.

    Live ``log_outcome`` uses ``for_date=open_position_date``. The next
    calendar day's ``get_daily_state(today)`` does not include that row.
    The exit here is also after 18:00 ET, so a CME observation-day key would
    lock the next event. The journal-day key must not.
    """

    rows = [
        event(
            "loss",
            "4HR_RETRIGGER",
            "2026-01-05T22:00:00+00:00",
            "2026-01-06T02:00:00+00:00",
            day="2026-01-06",
            pnl=-200,
        ),
        event(
            "next",
            "60M_322_FIRST_LIVE",
            "2026-01-06T03:00:00+00:00",
            "2026-01-06T04:00:00+00:00",
            day="2026-01-06",
            pnl=10,
        ),
    ]
    dispositions = {
        item.source_id: item.disposition for item in apply_account_admission(rows).decisions
    }
    assert dispositions["loss"] == "FILLED"
    assert dispositions["next"] == "FILLED"


def test_same_utc_day_still_locks_across_the_cme_18et_boundary():
    rows = [
        event(
            "loss",
            "4HR_RETRIGGER",
            "2026-01-05T21:00:00+00:00",
            "2026-01-05T23:30:00+00:00",
            day="2026-01-05",
            pnl=-200,
        ),
        event(
            "later",
            "60M_322_FIRST_LIVE",
            "2026-01-05T23:45:00+00:00",
            "2026-01-06T00:30:00+00:00",
            day="2026-01-06",
            pnl=10,
        ),
    ]
    dispositions = {
        item.source_id: item.disposition for item in apply_account_admission(rows).decisions
    }
    assert dispositions["loss"] == "FILLED"
    assert dispositions["later"] == "SKIPPED_DAILY_LOSS"
    assert apply_account_admission(rows).decisions[1].journal_day == "2026-01-05"


def test_exact_loss_boundary_and_unresolved_fill_do_not_lock_early():
    at_limit = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=-150),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=10),
    ]
    blocked = apply_account_admission(at_limit)
    assert [item.disposition for item in blocked.decisions] == ["FILLED", "SKIPPED_DAILY_LOSS"]

    just_inside = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=-149.99),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=10),
    ]
    admitted = apply_account_admission(just_inside)
    assert [item.disposition for item in admitted.decisions] == ["FILLED", "FILLED"]

    still_open = [
        event(
            "open",
            "4HR_RETRIGGER",
            "2026-01-05T15:00:00+00:00",
            None,
            day="2026-01-05",
            pnl=-500,
            result="OPEN",
        ),
        event("later", "60M_322_FIRST_LIVE", "2026-01-05T18:00:00+00:00", "2026-01-05T18:10:00+00:00", day="2026-01-05", pnl=10),
    ]
    overlay = apply_account_admission(still_open)
    assert [item.disposition for item in overlay.decisions] == [
        "FILLED",
        "SKIPPED_BUSY_PORTFOLIO",
    ]


def test_loss_is_booked_only_after_the_exit_timestamp():
    rows = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T17:00:00+00:00", day="2026-01-05", pnl=-200),
        event("during", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=10),
        event("at-exit", "DAILY_22_COMPLETED_CLOSE", "2026-01-05T17:00:00+00:00", "2026-01-05T17:10:00+00:00", day="2026-01-05", pnl=10),
        event("after", "12HR_MIYAGI", "2026-01-05T17:01:00+00:00", "2026-01-05T17:20:00+00:00", day="2026-01-05", pnl=10),
    ]
    dispositions = {
        item.source_id: item.disposition for item in apply_account_admission(rows).decisions
    }
    assert dispositions["during"] == "SKIPPED_BUSY_PORTFOLIO"
    assert dispositions["at-exit"] == "SKIPPED_BUSY_PORTFOLIO"
    assert dispositions["after"] == "SKIPPED_DAILY_LOSS"


def test_three_fill_cap_still_wins_when_loss_has_not_reached_the_floor():
    rows = [
        event("a", "4HR_RETRIGGER", "2026-01-05T14:00:00+00:00", "2026-01-05T14:10:00+00:00", day="2026-01-05", pnl=-40),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T15:00:00+00:00", "2026-01-05T15:10:00+00:00", day="2026-01-05", pnl=-40),
        event("c", "12HR_MIYAGI", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=-40),
        event("d", "SUSTAINED_TREND_V1", "2026-01-05T17:00:00+00:00", "2026-01-05T17:10:00+00:00", day="2026-01-05", pnl=-40),
    ]
    overlay = apply_account_admission(rows)
    assert [item.disposition for item in overlay.decisions] == [
        "FILLED",
        "FILLED",
        "FILLED",
        "SKIPPED_MAX_TRADES_PORTFOLIO",
    ]


def test_drawdown_evolves_from_the_seed_and_ignored_trades_do_not_move_it():
    rows = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=-400),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:30:00+00:00", day="2026-01-05", pnl=500),
        event("c", "12HR_MIYAGI", "2026-01-05T17:00:00+00:00", "2026-01-05T17:10:00+00:00", day="2026-01-05", pnl=10),
    ]
    missing = apply_account_admission(rows)
    assert missing.drawdown_gate == DRAWDOWN_GATE_NOT_EVALUATED
    assert [item.disposition for item in missing.decisions] == [
        "FILLED",
        "SKIPPED_DAILY_LOSS",
        "SKIPPED_DAILY_LOSS",
    ]

    seed = AccountSeed(
        starting_balance=1000.0,
        starting_peak=1000.0,
        source="unit-fixture:not-live-equity",
    )
    breached = apply_account_admission(rows, account_seed=seed)
    assert breached.drawdown_gate == "EVALUATED"
    assert [item.disposition for item in breached.decisions] == [
        "FILLED",
        "SKIPPED_DAILY_LOSS",
        "SKIPPED_DAILY_LOSS",
    ]
    assert [item.drawdown_gate for item in breached.decisions[1:]] == [
        DRAWDOWN_NOT_APPLIED,
        DRAWDOWN_NOT_APPLIED,
    ]
    assert [item.source_id for item in breached.fills] == ["a"]

    under_floor = AccountSeed(400.0, 400.0, source="unit-fixture:not-live-equity")
    just_inside = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=-119),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=10),
    ]
    inside = apply_account_admission(just_inside, account_seed=under_floor)
    assert [item.disposition for item in inside.decisions] == ["FILLED", "FILLED"]
    assert inside.decisions[1].drawdown_gate == DRAWDOWN_WITHIN_FLOOR


def test_daily_loss_wins_when_drawdown_is_also_breached():
    """Both gates fail. Daily loss is the disposition, matching RiskEngine order.

    The skipped winner would repair the floor if it were booked. The next
    journal day is clear of the $150 rule and still fails the floor, which
    shows the skip did not move balance or peak and did not take a fill slot.
    """

    seed = AccountSeed(
        starting_balance=1000.0,
        starting_peak=1000.0,
        source="unit-fixture:not-live-equity",
    )
    rows = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=-400),
        event("repair", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:30:00+00:00", day="2026-01-05", pnl=500),
        event("next-day", "12HR_MIYAGI", "2026-01-06T15:00:00+00:00", "2026-01-06T15:10:00+00:00", day="2026-01-06", pnl=10),
    ]
    overlay = apply_account_admission(rows, account_seed=seed)
    assert [item.disposition for item in overlay.decisions] == [
        "FILLED",
        "SKIPPED_DAILY_LOSS",
        "SKIPPED_DRAWDOWN_FLOOR",
    ]
    assert overlay.decisions[1].drawdown_gate == DRAWDOWN_NOT_APPLIED
    assert [item.source_id for item in overlay.fills] == ["a"]


def test_exact_thirty_percent_floor_uses_only_accepted_resolutions():
    seed = AccountSeed(400.0, 400.0, source="unit-fixture:not-live-equity")
    rows = [
        event("a", "4HR_RETRIGGER", "2026-01-05T15:00:00+00:00", "2026-01-05T15:30:00+00:00", day="2026-01-05", pnl=-120),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00", day="2026-01-05", pnl=10),
    ]
    overlay = apply_account_admission(rows, account_seed=seed)
    assert [item.disposition for item in overlay.decisions] == [
        "FILLED",
        "SKIPPED_DRAWDOWN_FLOOR",
    ]


def test_account_seed_without_provenance_fails_closed():
    with pytest.raises(ValueError, match="provenance"):
        AccountSeed(starting_balance=1000.0, starting_peak=1000.0, source="  ")


def test_overlay_source_does_not_use_synthetic_balances_or_the_cme_day():
    source = Path("research/mnq_account_admission_overlay.py").read_text()
    assert "STARTING_BALANCE" not in source
    assert "5000" not in source
    assert "1500" not in source
    assert "EquityPoint" not in source
    assert "observation_day(" not in source
    assert "import risk_rules" not in source
    assert "load_config" not in source
    pinned = Path("research/mnq_combined_portfolio_audit.py").read_text()
    assert "SKIPPED_DAILY_LOSS" not in pinned
    assert "apply_account_admission" not in pinned
