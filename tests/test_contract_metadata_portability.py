"""Phase-2 portability layer: every runtime/replay/observation consumer resolves
tick size, tick value and point value through ``config/futures_contracts.py``.

Unknown roots never inherit 0.25 / $1 / $1.25 / MNQ / MES economics:
executable and economic paths fail closed, observation paths return an
explicit "no metadata" result. No instrument, population, route or policy is
activated by this layer.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from config.futures_contracts import (
    POINT_VALUE,
    SUPPORTED_ROOTS,
    TICK_SIZE,
    TICK_VALUE,
    UnsupportedContractError,
    contract_economics,
    contract_root,
    optional_tick_size,
    point_value,
    round_to_tick,
    symbol_economics,
    tick_size,
    tick_value,
)
from risk.risk_engine import DailyState, RiskEngine, TradeSetup
from strategy import stop_sizing
from strategy.signal_engine import DecisionEngine
from strategy.strat_212_122 import advance_strat_212_122
from webhook import runner

MICROS = {
    #  root   tick   tick$   $/point
    "MNQ": (0.25, 0.50, 2.0),
    "MES": (0.25, 1.25, 5.0),
    "M2K": (0.10, 0.50, 5.0),
    "MGC": (0.10, 1.00, 10.0),
    "MCL": (0.01, 1.00, 100.0),
    "MBT": (5.00, 0.50, 0.10),
}
UNKNOWN = ["", "XYZ", "MK2", "ESTC", "QQQ", "M2KXX", "MBT2026", "MNQFOO", None]


# ── canonical metadata ────────────────────────────────────────────────────────

@pytest.mark.parametrize("root,tick,value,pv", [(k, *v) for k, v in MICROS.items()])
def test_canonical_micro_economics(root, tick, value, pv):
    assert contract_economics(root) == (tick, value)
    assert tick_size(root) == tick
    assert tick_value(root) == value
    assert point_value(root) == pytest.approx(pv)
    assert POINT_VALUE[root] == pytest.approx(pv)


def test_point_value_is_derived_not_a_second_table():
    for root in SUPPORTED_ROOTS:
        assert POINT_VALUE[root] == pytest.approx(TICK_VALUE[root] / TICK_SIZE[root])
    assert set(POINT_VALUE) == set(TICK_SIZE) == set(TICK_VALUE)


def test_es_nq_retained():
    assert contract_economics("ES") == (0.25, 12.5)
    assert contract_economics("NQ") == (0.25, 5.0)


@pytest.mark.parametrize("symbol", UNKNOWN)
def test_unknown_roots_fail_closed_everywhere(symbol):
    with pytest.raises(UnsupportedContractError):
        contract_economics(symbol)
    with pytest.raises(UnsupportedContractError):
        symbol_economics(symbol)
    with pytest.raises(UnsupportedContractError):
        round_to_tick(100.0, symbol)
    assert contract_root(symbol) is None
    assert optional_tick_size(symbol) is None


@pytest.mark.parametrize("root", list(MICROS))
@pytest.mark.parametrize("wrap", ["{r}", "{r}1!", "CME_MINI:{r}1!", "{r}Z6", "CME:{r}H2027", "{r}!"])
def test_contract_root_keeps_m2k_digit_and_strips_suffixes(root, wrap):
    symbol = wrap.format(r=root)
    assert contract_root(symbol) == root
    assert symbol_economics(symbol) == contract_economics(root)
    assert optional_tick_size(symbol) == TICK_SIZE[root]


def test_symbol_recognition_grants_no_eligibility():
    """Naming a contract is not ingestion, strategy or broker permission."""
    from config.settings import load_config
    from webhook.app import _INGEST_FUTURES_ROOTS

    config = load_config("risk_rules.yaml")
    assert contract_root("M2K1!") == "M2K" and contract_root("MBT1!") == "MBT"
    assert "M2K" not in _INGEST_FUTURES_ROOTS and "MBT" not in _INGEST_FUTURES_ROOTS
    assert _INGEST_FUTURES_ROOTS == ("MNQ", "MES", "ES", "NQ", "MGC", "MCL")
    assert config.allowed_instruments == ["MNQ"]
    assert not config.live_trading_enabled


# ── DecisionEngine ────────────────────────────────────────────────────────────

def test_decision_engine_tick_table_is_the_central_one():
    assert DecisionEngine.TICK_SIZE is TICK_SIZE
    assert DecisionEngine._tick_size("M2K") == 0.10
    assert DecisionEngine._tick_size("MBT") == 5.0
    assert DecisionEngine._tick_size("MNQ") == 0.25
    with pytest.raises(ValueError):
        DecisionEngine._tick_size("XYZ")


def test_decision_engine_refuses_instrument_without_metadata(config, fresh_market_state, clean_daily_state):
    engine = DecisionEngine(config=replace(config, allowed_instruments=["MNQ", "XYZ"]))
    state = replace(fresh_market_state, instrument="XYZ")
    out = engine.evaluate(state, clean_daily_state)
    assert out.decision == "NO_TRADE"
    assert "no proven contract metadata" in out.reason


def test_decision_engine_orb_has_no_policy_for_unlisted_instruments():
    """No MAX_ORB_STOP_TICKS entry → no ORB setup, never another instrument's cap."""
    for root in ("M2K", "MBT", "ES", "NQ"):
        assert root not in DecisionEngine.MAX_ORB_STOP_TICKS
        assert DecisionEngine._max_orb_stop_ticks(root) is None
    assert DecisionEngine._max_orb_stop_ticks("MNQ") == 80
    assert DecisionEngine._max_orb_stop_ticks("MES") == 40
    # Phase 2 adds NO strategy policy for the new instruments.
    for table in (DecisionEngine.MIN_STOP_TICKS, DecisionEngine.MAX_ORB_STOP_TICKS):
        assert set(table) == {"MNQ", "MES", "MGC", "MCL"}


def test_decision_engine_orb_reclaim_returns_none_without_policy(config, fresh_market_state, clean_daily_state):
    engine = DecisionEngine(config=replace(config, allowed_instruments=["M2K"], enabled_concepts=["orb_reclaim"]))
    state = replace(fresh_market_state, instrument="M2K")
    # Metadata exists for M2K, but no ORB stop-cap policy does: no setup, no borrowed cap.
    assert engine._try_orb_reclaim(state) is None
    assert engine._try_orb_breakout(state) is None
    out = engine.evaluate(state, clean_daily_state)
    assert out.decision == "NO_TRADE"


def test_mnq_orb_reclaim_unchanged(config, fresh_market_state, clean_daily_state):
    """Existing MNQ behaviour: the fixture's ORB reclaim still produces a TRADE."""
    engine = DecisionEngine(config=replace(config, enabled_concepts=["orb_reclaim"]))
    out = engine.evaluate(fresh_market_state, clean_daily_state)
    assert out.decision == "TRADE"
    assert out.setup.entry == 19498.0 + 0.25 * 2
    assert out.setup.stop == max(19462.0 - 0.25 * 4, out.setup.entry - 0.25 * 80)


# ── 2-1-2 / 1-2-2 canonical detector receives the instrument tick ─────────────

@pytest.mark.parametrize("root", ["M2K", "MBT", "MNQ", "MES", "MGC", "MCL"])
def test_strat_212_122_receives_instrument_tick(root):
    """The canonical detector is instrument-generic: entry = boundary ± exactly one instrument tick."""
    tick = DecisionEngine._tick_size(root)
    state, candidate = advance_strat_212_122(
        current_bar_type="1", previous_bar_type="2U",
        current_open=100.0, current_high=110.0, current_low=90.0,
        tick_size=tick, trading_date="2026-09-15", persisted_state={},
    )
    assert candidate is None and state["status"] == "ARMED" and state["direction"] == "LONG"
    assert state["entry_price"] == pytest.approx(110.0 + tick)
    assert state["entry_price"] - state["boundary_high"] == pytest.approx(tick)
    state, _ = advance_strat_212_122(
        current_bar_type="2D", previous_bar_type="1",
        current_open=100.0, current_high=110.0, current_low=90.0,
        tick_size=tick, trading_date="2026-09-16", persisted_state={},
    )
    assert state["status"] == "ARMED" and state["direction"] == "LONG"
    assert state["entry_price"] == pytest.approx(110.0 + tick)


# ── stop rounding: live and replay share one grid ─────────────────────────────

@pytest.mark.parametrize("root,price,expected", [
    ("M2K", 2312.07, 2312.1), ("M2K1!", 2312.03, 2312.0), ("M2KZ6", 2312.06, 2312.1),
    ("MBT", 65003.0, 65005.0), ("MBT1!", 65002.4, 65000.0), ("CME:MBTH27", 65007.5, 65010.0),
    ("MNQ", 19975.13, 19975.25), ("MNQ1!", 19975.13, 19975.25),
    ("MGC", 1850.07, 1850.1), ("MCL", 75.013, 75.01),
])
def test_stop_rounding_same_grid_live_and_replay(root, price, expected):
    assert stop_sizing.round_to_tick(price, root) == expected           # replay + live (shared)
    assert runner._round_to_tick(price, root) == expected               # live runner wrapper
    assert round_to_tick(price, root) == expected                       # canonical


@pytest.mark.parametrize("symbol", ["XYZ", "MK2", "ESTC", ""])
def test_stop_rounding_unknown_root_raises_in_both_paths(symbol):
    with pytest.raises(UnsupportedContractError):
        stop_sizing.round_to_tick(100.0, symbol)
    with pytest.raises(UnsupportedContractError):
        runner._round_to_tick(100.0, symbol)


class _Setup:
    def __init__(self, direction, entry, stop, target, strategy="orb_breakout"):
        self.direction, self.entry, self.stop, self.target, self.strategy = direction, entry, stop, target, strategy
        self.rr_ratio = 0.0


def test_stop_multiplier_rounds_m2k_and_mbt_on_their_own_grid():
    s = _Setup("LONG", entry=2300.0, stop=2299.0, target=2302.2)  # risk 1.0
    assert stop_sizing.apply_stop_multiplier(s, "M2K", {"M2K": 1.55}) == 1.55
    assert s.stop == 2298.4 and (s.stop * 10) == pytest.approx(round(s.stop * 10))
    s = _Setup("SHORT", entry=65000.0, stop=65010.0, target=64970.0)  # risk 10
    assert stop_sizing.apply_stop_multiplier(s, "MBT", {"MBT": 1.3}) == 1.3
    assert s.stop == 65015.0 and s.stop % 5 == 0


def test_runner_tick_helpers_fail_closed():
    assert runner._tick_value_for("M2K1!") == 0.5 and runner._tick_size_for("M2KZ6") == 0.1
    assert runner._tick_value_for("MBT") == 0.5 and runner._tick_size_for("MBT1!") == 5.0
    assert runner._tick_value_for("MNQ1!") == 0.5 and runner._tick_value_for("MES") == 1.25
    for bad in ("XYZ", "MK2", ""):
        with pytest.raises(UnsupportedContractError):
            runner._tick_value_for(bad)
        with pytest.raises(UnsupportedContractError):
            runner._tick_size_for(bad)
    assert not hasattr(runner, "_TICK_VALUES") and not hasattr(runner, "_TICK_SIZE_BY_ROOT")


# ── RiskEngine ────────────────────────────────────────────────────────────────

def _setup(root, entry, stop, target, contracts=1):
    return TradeSetup(direction="LONG", entry=entry, stop=stop, target=target, rr_ratio=2.0,
                      strategy="orb_reclaim", instrument=root, session="new_york", contracts=contracts)


def test_risk_engine_has_no_local_point_value_table():
    assert not hasattr(RiskEngine, "_POINT_VALUES")


def test_risk_engine_profit_protect_uses_derived_point_value(config):
    cfg = replace(config, daily_profit_protect_threshold=100.0, allowed_instruments=["MNQ", "M2K", "MBT"],
                  max_contracts_per_instrument={"MNQ": 2, "M2K": 1, "MBT": 1})
    engine = RiskEngine(config=cfg)
    day = DailyState(realized_pnl_dollars=120.0)
    # M2K: 10 points × $5 = $50 planned risk ≤ $120 → passes the gate.
    assert engine._check_profit_protect_gate(_setup("M2K", 2300.0, 2290.0, 2320.0), day) is None
    # M2K: 30 points × $5 = $150 > $120 → rejected by the gate with the M2K point value.
    r = engine._check_profit_protect_gate(_setup("M2K", 2300.0, 2270.0, 2360.0), day)
    assert r is not None and r.failed_rule == "profit_protect_gate" and "$150.00" in r.reason
    # MBT: 2000 points × $0.10 = $200 > $120 → rejected; with a 0.25/$1 fallback it would be wrongly approved.
    r = engine._check_profit_protect_gate(_setup("MBT", 65000.0, 63000.0, 69000.0), day)
    assert r is not None and "$200.00" in r.reason
    # MNQ unchanged: 20 points × $2 = $40.
    assert engine._check_profit_protect_gate(_setup("MNQ", 19500.0, 19480.0, 19540.0), day) is None


def test_risk_engine_rejects_generic_economics_for_unknown_root(config):
    cfg = replace(config, daily_profit_protect_threshold=100.0, allowed_instruments=["XYZ"],
                  max_stop_ticks={"XYZ": 10})
    engine = RiskEngine(config=cfg)
    day = DailyState(realized_pnl_dollars=120.0)
    r = engine._check_profit_protect_gate(_setup("XYZ", 100.0, 99.0, 102.0), day)
    assert r is not None and r.failed_rule == "contract_metadata_missing"
    r = engine._check_max_stop_distance(_setup("XYZ", 100.0, 99.0, 102.0), DailyState())
    assert r is not None and r.failed_rule == "contract_metadata_missing"


def test_risk_engine_max_stop_distance_uses_instrument_tick(config):
    cfg = replace(config, allowed_instruments=["MNQ", "M2K", "MBT"], max_stop_ticks={"MNQ": 80, "M2K": 80, "MBT": 80})
    engine = RiskEngine(config=cfg)
    # M2K: 10 points = 100 ticks at 0.10 → too wide (a 0.25 fallback would call it 40 ticks and approve).
    r = engine._check_max_stop_distance(_setup("M2K", 2300.0, 2290.0, 2320.0), DailyState())
    assert r is not None and r.failed_rule == "stop_too_wide" and "100 ticks" in r.reason
    # MBT: 300 points = 60 ticks at 5.0 → fine (a 0.25 fallback would call it 1200 ticks and reject).
    assert engine._check_max_stop_distance(_setup("MBT", 65000.0, 64700.0, 65600.0), DailyState()) is None
    # MNQ unchanged: 20 points = 80 ticks → at the cap, allowed.
    assert engine._check_max_stop_distance(_setup("MNQ", 19500.0, 19480.0, 19540.0), DailyState()) is None
    assert engine._check_max_stop_distance(_setup("MNQ", 19500.0, 19479.75, 19540.0), DailyState()).failed_rule == "stop_too_wide"


def test_risk_thresholds_untouched():
    """Phase 2 adds no per-instrument policy for the new universe."""
    from config.settings import load_config
    cfg = load_config("risk_rules.yaml")
    assert cfg.max_stop_ticks["MNQ"] == 120 and cfg.max_stop_ticks["MES"] == 60
    assert cfg.orb_stop_ticks == {"MES": 16, "MNQ": 48}
    for root in ("M2K", "MBT"):
        assert root not in cfg.max_stop_ticks
        assert root not in cfg.max_contracts_per_instrument
    for root in ("MGC", "MCL"):
        assert cfg.max_stop_ticks[root] == 0 and cfg.max_contracts_per_instrument[root] == 0


# ── observation paths: explicit unsupported, never fabricated ─────────────────

def test_shadow_setups_skip_instrument_without_metadata(fresh_market_state):
    from strategy import shadow_setups
    assert shadow_setups.TICK_SIZE is TICK_SIZE
    assert shadow_setups.evaluate_shadow_setups(replace(fresh_market_state, instrument="XYZ")) == []
    assert shadow_setups._tick(replace(fresh_market_state, instrument="M2K")) == 0.10
    with pytest.raises(UnsupportedContractError):
        shadow_setups._tick(replace(fresh_market_state, instrument="XYZ"))
    assert "M2K" not in shadow_setups._FOURHR_MAX_STOP_TICKS and "MBT" not in shadow_setups.STRAT_122_MAX_STOP_TICKS


def test_structural_regime_reports_missing_metadata():
    from context.structural_regime import INSUFFICIENT, classify_structural_regime
    bars = [{"open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "ts": f"2026-09-15T{9 + i // 12:02d}:{(i % 12) * 5:02d}:00+00:00"} for i in range(40)]
    out = classify_structural_regime(bars, instrument="XYZ")
    assert out.condition == INSUFFICIENT and "no contract metadata" in out.reason
    assert classify_structural_regime(bars, instrument="M2K").condition != "" \
        and classify_structural_regime(bars, instrument="M2K").reason != "no contract metadata for instrument"


def test_day_only_fill_uses_canonical_economics():
    from execution.day_only_exit import build_day_only_fill, instrument_root
    assert instrument_root("M2KZ6") == "M2K" and instrument_root("MNQ1!") == "MNQ" and instrument_root("MBT") == "MBT"
    fill = build_day_only_fill({"instrument": "M2K1!", "direction": "LONG", "entry": 2300.0, "contracts": 1}, 2301.0)
    assert fill.pnl_ticks == pytest.approx(10.0) and fill.pnl_dollars == pytest.approx(5.0)
    fill = build_day_only_fill({"instrument": "MBT", "direction": "SHORT", "entry": 65000.0, "contracts": 1}, 64900.0)
    assert fill.pnl_ticks == pytest.approx(20.0) and fill.pnl_dollars == pytest.approx(10.0)
    with pytest.raises(UnsupportedContractError):
        build_day_only_fill({"instrument": "XYZ", "direction": "LONG", "entry": 1.0, "contracts": 1}, 2.0)


def test_no_duplicate_economic_tables_remain_in_active_paths():
    """Guard: active runtime/replay/observation modules define no private tick/point tables."""
    import inspect
    import context.five_min_feed, context.structural_regime, execution.day_only_exit, main
    import replay.replay_engine, risk.risk_engine, strategy.shadow_setups, strategy.signal_engine, strategy.stop_sizing
    for mod in (context.five_min_feed, context.structural_regime, execution.day_only_exit, main,
                replay.replay_engine, risk.risk_engine, strategy.shadow_setups, strategy.signal_engine,
                strategy.stop_sizing, runner):
        src = inspect.getsource(mod)
        assert ".get(state.instrument, 0.25)" not in src, mod.__name__
        assert ".get(root, 0.25)" not in src, mod.__name__
        assert "_TICK_SIZE = {" not in src and "_TICK_VALUES" not in src and "_POINT_VALUES" not in src, mod.__name__
        assert "EXEC_TICK_SIZE" not in src and "EXEC_TICK_VALUE" not in src, mod.__name__


# ── nothing activated ─────────────────────────────────────────────────────────

def test_no_population_instrument_or_route_activated():
    from execution.evidence_identity import configured_populations
    from context.wide_stop_ledger_paper import ledger_for
    pops = configured_populations()
    assert len(pops) == 5 and {p[1] for p in pops} == {"MNQ"}
    for root in ("M2K", "MGC", "MCL", "MBT"):
        assert ledger_for(root, "strat_4hr_retrigger") is None
        assert ledger_for(root, "strat_322_first_live") is None
