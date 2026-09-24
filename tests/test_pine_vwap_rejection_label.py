"""Source-level regression lock for Pine's advisory `vwap_rejection` label.

TradingView Pine is not compiled in CI. The backend's _try_vwap_rejection was
fixed in PR #308 to gate on the one-bar-lookback failed reclaim, because a
same-bar "crossover above VWAP and close below VWAP" can never be true. Pine's
advisory label kept the impossible same-bar test, so it never fired. These
checks pin Pine to the backend's gate: failed_reclaim + trend DOWN.
"""
from pathlib import Path

PINE = Path("tradingview/risksentinel_context.pine")


def _source() -> str:
    return PINE.read_text()


def _failed_reclaim(prev_crossed_above: bool, close: float, vwap: float) -> bool:
    """Python model of Pine's `vwap_reclaimed[1] and close < vwap_val`."""
    return prev_crossed_above and close < vwap


def test_failed_reclaim_is_defined_as_one_bar_lookback():
    src = _source()
    assert "vwap_reclaimed = ta.crossover(close, vwap_val)" in src
    assert "vwap_failed_reclaim = vwap_reclaimed[1] and close < vwap_val" in src


def test_vwap_rejection_label_uses_failed_reclaim_and_down_trend():
    src = _source()
    gate = 'else if vwap_failed_reclaim and trend_dir == "DOWN"\n    signal_strategy := "vwap_rejection"'
    assert gate in src
    # The impossible same-bar predicate must not come back.
    assert 'vwap_reclaimed and close < vwap_val and trend_dir == "DOWN"' not in src
    # failed_reclaim is defined before the signal chain uses it.
    assert src.index("vwap_failed_reclaim = ") < src.index(gate)


def test_same_bar_predicate_was_unreachable_and_lookback_is_reachable():
    # A crossover bar closes ABOVE VWAP, so "crossed above this bar AND close
    # below VWAP this bar" is impossible; the next-bar failure is reachable.
    vwap = 100.0
    crossover_bar_close = 100.5          # crossover implies close > vwap
    assert not (crossover_bar_close > vwap and crossover_bar_close < vwap)
    assert _failed_reclaim(True, 99.75, vwap)
    assert not _failed_reclaim(True, 100.25, vwap)   # held the reclaim
    assert not _failed_reclaim(False, 99.75, vwap)   # no reclaim attempt


def test_vwap_rejection_bracket_matches_backend():
    # Advisory bracket must equal the backend's (entry VWAP-2t, stop VWAP+20t,
    # 3R) or signal_engine ignores it anyway; pin it so the two stay aligned.
    src = _source()
    i = src.index('signal_strategy := "vwap_rejection"')
    block = src[i:i + 300]
    assert "signal_entry := vwap_val - tick * 2" in block
    assert "signal_stop  := vwap_val + tick * 20" in block
    assert "* 3.0" in block
