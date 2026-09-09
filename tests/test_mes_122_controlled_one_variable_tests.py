import pytest

from scripts.mes_122_controlled_one_variable_tests import (
    _fixed_one_contract_config,
    _metric_block,
    _variant_advance,
)


def _arm(fn):
    state, candidate = fn(
        current_bar_type="2D",
        previous_bar_type="1",
        current_open=95.0,
        current_high=100.0,
        current_low=90.0,
        tick_size=0.25,
        trading_date="2026-01-01",
        persisted_state=None,
    )
    assert candidate is None
    assert state["status"] == "ARMED"
    assert state["pattern"] == "strat_122"
    return state


def test_fixed_config_removes_dynamic_contract_feedback():
    cfg = _fixed_one_contract_config(isolated=True)
    assert cfg.position_sizing.enabled is False
    assert cfg.position_sizing.sizing_rules == []
    assert cfg.max_contracts_per_instrument == {"MES": 1, "MNQ": 1}
    assert cfg.max_contracts_hard_cap == 1
    assert cfg.win_streak_bonus_after == 0
    assert cfg.enabled_concepts == ["strat_212", "strat_122"]
    assert cfg.fill_slippage_ticks == 1.0
    assert cfg.fill_pessimistic_both_hit is True
    assert cfg.runner_mode is False


def test_each_mechanical_variant_changes_only_one_armed_level():
    baseline = _arm(_variant_advance("baseline"))

    entry = _arm(_variant_advance("entry_confirm_2t"))
    assert entry["entry_price"] == pytest.approx(baseline["entry_price"] + 0.25)
    assert entry["stop_price"] == baseline["stop_price"]
    assert entry["target_price"] == baseline["target_price"]

    stop = _arm(_variant_advance("stop_wider_4t"))
    assert stop["entry_price"] == baseline["entry_price"]
    assert stop["stop_price"] == pytest.approx(baseline["stop_price"] - 1.0)
    assert stop["target_price"] == baseline["target_price"]

    target = _arm(_variant_advance("target_1_5r"))
    assert target["entry_price"] == baseline["entry_price"]
    assert target["stop_price"] == baseline["stop_price"]
    risk = abs(baseline["entry_price"] - baseline["stop_price"])
    assert target["target_price"] == pytest.approx(baseline["entry_price"] + 1.5 * risk)


def test_metrics_include_round_trip_commission_and_period_split():
    rows = [
        {"date": "2026-01-02", "pnl_dollars": 100.0},
        {"date": "2026-01-03", "pnl_dollars": -50.0},
        {"date": "2026-02-02", "pnl_dollars": 25.0},
        {"date": "2026-02-03", "pnl_dollars": -10.0},
    ]
    out = _metric_block(rows)
    assert out["trades"] == 4
    assert out["raw_net"] == 65.0
    assert out["commission_adjusted_net"] == pytest.approx(65.0 - 4 * 1.48)
    assert out["h1_commission_adjusted"] == pytest.approx(50.0 - 2 * 1.48)
    assert out["h2_commission_adjusted"] == pytest.approx(15.0 - 2 * 1.48)
    assert set(out["months"]) == {"2026-01", "2026-02"}
