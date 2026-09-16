from __future__ import annotations

import pytest

from scripts import mes_asian_d_ema_baseline as mes
from scripts import mnq_missed_opportunity_producer as representation_parent


def _record(cohort: str, ema: str):
    return {
        "bar_cohort": cohort,
        "ema_dir": ema,
    }


def _candidate(direction: str):
    return {"direction": direction}


def test_parent_d0_population_semantics_match_exhaustive_matrix():
    parent_d0 = [predicate for name, predicate in representation_parent._variants() if name == "D0"]
    assert len(parent_d0) == 1
    mes_d0 = mes._d0_predicate()
    for cohort in ("A", "B", "C", "D"):
        for ema in ("UP", "DOWN", "NEUTRAL", None):
            for direction in ("LONG", "SHORT"):
                record = _record(cohort, ema)
                candidate = _candidate(direction)
                assert mes_d0(record, candidate) == parent_d0[0](record, candidate)


def test_population_port_keeps_timeframe_dedupe_helpers_and_one_tick_slippage():
    assert representation_parent.INSTRUMENT == "MNQ"
    assert mes.INSTRUMENT == "MES"
    assert representation_parent.TIMEFRAME_MINUTES == mes.TIMEFRAME_MINUTES == 15
    assert representation_parent.SLIPPAGE_TICKS == mes.SLIPPAGE_TICKS == 1.0
    assert representation_parent.TICK == mes.TICK == 0.25
    assert representation_parent.TICK_VALUE == 0.50
    assert mes.TICK_VALUE == 1.25


def test_mes_canonical_ioc_is_real_paperbroker_not_parent_touch_approximation():
    broker = mes._new_broker()
    assert broker.get_broker_name() == "PaperBroker"
    assert broker.is_live is False
    assert broker._entry_fill_model == "ioc_limit"
    assert broker._entry_tolerance_ticks("MES") == 16.0
    assert broker._slippage_ticks == 1.0
    assert broker._pessimistic_both_hit is True
    assert broker._breakeven_at_1r is False
    assert broker._runner_mode is False
    assert mes.IOC_TOLERANCE_POINTS == 4.0


def test_mes_dollar_conversion_is_five_dollars_per_point():
    assert mes.POINT_VALUE == 5.0
    assert (4.0 / mes.TICK) * mes.TICK_VALUE == pytest.approx(20.0)
