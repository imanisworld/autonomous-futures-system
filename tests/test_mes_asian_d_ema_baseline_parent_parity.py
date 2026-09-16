from __future__ import annotations

import pytest

from scripts import mes_asian_d_ema_baseline as mes
from scripts import mnq_missed_opportunity_producer as parent


def test_parent_d0_semantics_are_not_reimplemented():
    d0 = [predicate for name, predicate in parent._variants() if name == "D0"]
    assert len(d0) == 1
    assert mes._d0_predicate() is d0[0]


def test_portability_changes_only_root_economics_and_ioc_cap_at_constant_level():
    assert parent.INSTRUMENT == "MNQ"
    assert mes.INSTRUMENT == "MES"
    assert parent.TIMEFRAME_MINUTES == mes.TIMEFRAME_MINUTES == 15
    assert parent.SLIPPAGE_TICKS == mes.SLIPPAGE_TICKS == 1.0
    assert parent.TICK == mes.TICK == 0.25
    assert parent.TICK_VALUE == 0.50
    assert mes.TICK_VALUE == 1.25
    assert parent.IOC_TOLERANCE_POINTS == 8.0
    assert mes.IOC_TOLERANCE_POINTS == 4.0


def test_mes_dollar_conversion_is_five_dollars_per_point():
    assert mes.POINT_VALUE == 5.0
    assert (4.0 / mes.TICK) * mes.TICK_VALUE == pytest.approx(20.0)
