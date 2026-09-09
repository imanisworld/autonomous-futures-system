"""Top-level invariants for the current three-lane MNQ paper campaign."""
from context import daily_22_swing_collector as daily
from context import wide_stop_execution as execution
from context import wide_stop_ledger_paper as wide


def test_campaign_has_no_route_other_than_internal_paper_sim():
    assert execution.VALID_ROUTES == ("paper_sim",)


def test_4hr_and_322_capital_contracts_are_pinned():
    four = wide.LEDGERS["wide_stop_4k"]
    three_two_two = wide.LEDGERS["wide_stop_6k"]
    assert four.starting_balance == 4_000.0
    assert four.max_stop_ticks == 300.0
    # Historical journal identifier only; actual forward capital is capped at $5k.
    assert three_two_two.starting_balance == 5_000.0
    assert three_two_two.max_stop_ticks == 600.0
    assert wide.role("MNQ", "strat_322_first_live") == "fill_eligible"


def test_miyagi_remains_shadow_only():
    assert wide.role("MNQ", "strat_12hr_miyagi") == "shadow_only"
    assert not wide.is_fill_eligible("MNQ", "strat_12hr_miyagi")


def test_daily_lane_is_one_contract_and_5k_max_capital_assumption():
    assert daily.STARTING_BALANCE == 5_000.0
    assert daily.CONTRACTS == 1
    assert daily.MAX_PLANNED_RISK_DOLLARS == 1_750.0
    assert daily.MAX_DRAWDOWN == 0.30
