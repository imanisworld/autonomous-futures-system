from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from config.settings import ConfigError, _validate_config
from context.five_min_feed import arm_fifteen_min_setup
from context.mnq_orb_breakout_inverse_paper import (
    MARKETABLE_TICKS,
    VALID_MODES,
    evaluate,
    is_candidate,
    mirror_order,
    mode,
)
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from risk.risk_engine import RiskEngine
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


def _config(tmp_path, **overrides):
    base = _base_config(tmp_path)
    return replace(
        base,
        enabled_concepts=list(base.enabled_concepts) + ["orb_breakout"],
        orb_stop_ticks={"MNQ": 48},
        mnq_orb_breakout_inverse_epoch_start="2026-05-23T14:00:00+00:00",
        **overrides,
    )


def _payload(**overrides):
    values = {
        "orb_status": "above",
        "volume": 5000,
        "avg_volume": 3800,
    }
    values.update(overrides)
    return _base_payload(**values)


def _journal_rows(log_dir) -> list[dict]:
    path = next(Path(log_dir).glob("journal_*.jsonl"))
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("selected", VALID_MODES)
def test_valid_modes(selected, tmp_path):
    cfg = replace(
        _base_config(tmp_path),
        mnq_orb_breakout_inverse_mode=selected,
    )
    assert mode(cfg) == selected


def test_invalid_mode_fails_closed_and_config_rejects(tmp_path):
    cfg = replace(
        _base_config(tmp_path),
        mnq_orb_breakout_inverse_mode="live",
        max_staleness_seconds=60,
    )
    assert mode(cfg) == "observe_only"
    with pytest.raises(ConfigError, match="MNQ_ORB_BREAKOUT_INVERSE_MODE"):
        _validate_config(cfg)


def test_inverse_and_legacy_breakout_proof_modes_are_mutually_exclusive(tmp_path):
    cfg = replace(
        _base_config(tmp_path),
        mnq_orb_breakout_inverse_mode="paper_sim",
        mnq_orb_breakout_inverse_epoch_start="2026-05-23T14:00:00+00:00",
        mnq_orb_breakout_proof_mode="paper_sim",
        max_staleness_seconds=60,
    )
    with pytest.raises(ConfigError, match="cannot both be active"):
        _validate_config(cfg)


def test_active_inverse_requires_offset_aware_epoch_start(tmp_path):
    missing = replace(
        _base_config(tmp_path),
        mnq_orb_breakout_inverse_mode="paper_sim",
        max_staleness_seconds=60,
    )
    with pytest.raises(ConfigError, match="EPOCH_START is required"):
        _validate_config(missing)

    naive = replace(
        missing,
        mnq_orb_breakout_inverse_epoch_start="2026-09-02T00:00:00",
    )
    with pytest.raises(ConfigError, match="UTC offset"):
        _validate_config(naive)


@pytest.mark.parametrize(
    "instrument,strategy,expected",
    [
        ("MNQ", "orb_breakout", True),
        ("MNQ1!", "orb_breakout", True),
        ("MES", "orb_breakout", False),
        ("MNQ", "orb_reclaim", False),
    ],
)
def test_candidate_scope(instrument, strategy, expected):
    assert is_candidate(instrument, strategy) is expected


def test_frozen_mirror_matches_research_geometry():
    source = BracketOrder(
        instrument="MNQ",
        direction="LONG",
        entry=24924.0,
        stop=24911.5,
        target=24951.5,
        rr_ratio=2.2,
        strategy="orb_breakout",
        contracts=2,
        force_market_entry=True,
        force_runner_exit=True,
    )
    inverse = mirror_order(source)
    assert inverse.direction == "SHORT"
    assert inverse.entry == 24924.0
    assert inverse.stop == 24936.5
    assert inverse.target == 24896.5
    assert inverse.contracts == 1
    assert inverse.force_market_entry is False
    assert inverse.force_runner_exit is False
    assert inverse.execution_model == "ioc_limit_static"
    assert inverse.max_slippage_ticks == MARKETABLE_TICKS
    assert inverse.max_dollar_risk == 29.0


def test_observe_only_is_a_noop(tmp_path):
    today = date(2026, 5, 23)
    cfg = _config(tmp_path)
    result = process_alert(
        _payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "TRADE"
    assert result["fill"]["direction"] == "LONG"
    intent = next(
        row for row in _journal_rows(cfg.log_dir)
        if row.get("decision") == "TRADE_INTENT"
    )
    assert intent["mnq_orb_breakout_inverse_audit"]["paper_mode"] == "observe_only"
    assert intent["mnq_orb_breakout_inverse_audit"]["apply_override"] is False


def test_runtime_signal_to_ioc_order_parity(tmp_path, monkeypatch):
    broker_init = {}
    submitted = {}
    real_init = PaperBroker.__init__
    real_execute = PaperBroker.execute_bracket

    def spy_init(self, *args, **kwargs):
        broker_init.update(kwargs)
        return real_init(self, *args, **kwargs)

    def spy_execute(self, order, market_price=None, **kwargs):
        submitted["order"] = order
        submitted["market_price"] = market_price
        return real_execute(self, order, market_price=market_price, **kwargs)

    monkeypatch.setattr(PaperBroker, "__init__", spy_init)
    monkeypatch.setattr(PaperBroker, "execute_bracket", spy_execute)

    today = date(2026, 5, 23)
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
        stop_multiplier_per_instrument={"MNQ": 2.0},
    )
    payload = _payload(timestamp="2026-05-23T15:00:00+00:00")
    result = process_alert(
        payload,
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )

    assert result["decision"] == "TRADE"
    assert broker_init["entry_fill_model"] == "ioc_limit"
    assert broker_init["entry_tolerance_ticks_by_root"] == {"MNQ": 8.0}
    assert broker_init["runner_mode"] is False
    assert broker_init["breakeven_at_1r"] is False
    assert broker_init["pessimistic_both_hit"] is True
    assert broker_init["slippage_ticks"] == 1.0

    order = submitted["order"]
    assert submitted["market_price"] == payload.close
    assert order.direction == "SHORT"
    assert order.contracts == 1
    assert order.entry == 19498.5
    assert order.stop == 19511.0
    assert order.target == 19471.0
    assert result["fill"]["direction"] == "SHORT"
    assert result["fill"]["stop"] == 19511.0
    assert result["fill"]["target"] == 19471.0

    rows = _journal_rows(cfg.log_dir)
    intent = next(row for row in rows if row.get("decision") == "TRADE_INTENT")
    confirmed = next(row for row in rows if row.get("decision") == "TRADE")
    audit = intent["mnq_orb_breakout_inverse_audit"]
    assert audit["accounting_epoch_start"] == "2026-05-23T14:00:00+00:00"
    assert audit["source_setup"] == {
        "direction": "LONG",
        "entry": 19498.5,
        "stop": 19486.0,
        "target": 19526.0,
    }
    assert audit["submitted_setup"] == {
        "direction": "SHORT",
        "entry": 19498.5,
        "stop": 19511.0,
        "target": 19471.0,
        "contracts": 1,
    }
    assert confirmed["setup"]["direction"] == "SHORT"
    assert confirmed["setup"]["stop"] == 19511.0
    assert confirmed["setup"]["target"] == 19471.0
    assert confirmed["setup"]["contracts"] == 1


def _write_outcomes(log_dir, rows):
    path = Path(log_dir) / "journal_2026-05-23.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def _outcome(ts, result, pnl):
    return {
        "ts": ts,
        "type": "OUTCOME",
        "instrument": "MNQ",
        "session": "new_york",
        "outcome": {"result": result, "pnl_dollars": pnl},
    }


def test_pre_epoch_shared_drawdown_and_loss_streak_do_not_block(tmp_path):
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        max_drawdown_percent=0.20,
    )
    _write_outcomes(
        cfg.log_dir,
        [
            _outcome("2026-05-23T12:00:00+00:00", "WIN", 410.75),
            _outcome("2026-05-23T12:15:00+00:00", "LOSS", -205.00),
            _outcome("2026-05-23T12:30:00+00:00", "LOSS", -205.00),
        ],
    )

    result = process_alert(
        _payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "TRADE"
    assert result["risk"]["result"] == "APPROVED"


def test_loss_inside_epoch_still_trips_unchanged_max_drawdown(tmp_path):
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        max_drawdown_percent=0.20,
        max_daily_loss=0.0,
        max_consecutive_losses=99,
    )
    _write_outcomes(
        cfg.log_dir,
        [_outcome("2026-05-23T14:30:00+00:00", "LOSS", -2_500.00)],
    )

    result = process_alert(
        _payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "RISK_REJECTED"
    assert result["risk"]["failed_rule"] == "max_drawdown"


def test_dynamic_sizing_is_diagnostic_only(tmp_path, monkeypatch):
    monkeypatch.setattr(
        RiskEngine,
        "recommended_contracts",
        lambda self, instrument, balance: 2,
    )
    today = date(2026, 5, 23)
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
    )
    result = process_alert(
        _payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "TRADE"
    assert result["fill"]["contracts"] == 1
    intent = next(
        row for row in _journal_rows(cfg.log_dir)
        if row.get("decision") == "TRADE_INTENT"
    )
    audit = intent["mnq_orb_breakout_inverse_audit"]
    assert audit["recommended_contracts"] == 2
    assert audit["submitted_contracts"] == 1


def test_original_short_signal_becomes_inverse_long(tmp_path):
    today = date(2026, 5, 23)
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
    )
    result = process_alert(
        _payload(
            timestamp="2026-05-23T15:00:00+00:00",
            open=19470.0,
            high=19472.0,
            low=19450.0,
            close=19455.0,
            vwap=19460.0,
            orb_status="below",
            trend_direction="DOWN",
            current_bar_type="two_down",
            previous_bar_type="two_down",
            two_bars_back_type="two_down",
        ),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "TRADE"
    assert result["fill"]["direction"] == "LONG"
    assert result["fill"]["contracts"] == 1
    assert result["fill"]["entry"] == 19455.25
    assert result["fill"]["stop"] == 19449.0
    assert result["fill"]["target"] == 19489.0


def test_paper_mode_cannot_construct_external_broker(tmp_path, monkeypatch):
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")

    class TradovateMustNotExist:
        def __init__(self, *args, **kwargs):
            raise AssertionError("paper-only inverse reached external broker")

    monkeypatch.setattr(
        tradovate_module,
        "TradovateBroker",
        TradovateMustNotExist,
    )
    today = date(2026, 5, 23)
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
    )
    result = process_alert(
        _payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert result["decision"] == "TRADE"
    assert result["fill"]["paper_order_id"].startswith("PAPER-")


def test_paper_position_resolves_with_inverse_static_bracket(
    tmp_path,
    monkeypatch,
):
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")

    class TradovateMustNotExist:
        def __init__(self, *args, **kwargs):
            raise AssertionError("inverse paper position reached external resolver")

    monkeypatch.setattr(
        tradovate_module,
        "TradovateBroker",
        TradovateMustNotExist,
    )
    today = date(2026, 5, 23)
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
    )
    opened = process_alert(
        _payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert opened["decision"] == "TRADE"
    assert opened["fill"]["direction"] == "SHORT"

    resolved = process_alert(
        _payload(
            timestamp="2026-05-23T15:15:00+00:00",
            high=19520.0,
            low=19460.0,
            close=19490.0,
        ),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )
    assert resolved["resolution"] == "LOSS"
    outcome = next(
        row["outcome"]
        for row in _journal_rows(cfg.log_dir)
        if row.get("type") == "OUTCOME"
    )
    assert outcome["contracts"] == 1
    assert outcome["exit_reason"] == "STOP_HIT"
    confirmed = next(
        row for row in _journal_rows(cfg.log_dir)
        if row.get("decision") == "TRADE"
    )
    assert confirmed["setup"]["direction"] == "SHORT"


def test_ioc_cap_fails_closed():
    source = BracketOrder(
        instrument="MNQ",
        direction="SHORT",
        entry=100.0,
        stop=103.0,
        target=93.4,
        rr_ratio=2.2,
        strategy="orb_breakout",
        contracts=1,
    )
    inverse = mirror_order(source)
    broker = PaperBroker(
        slippage_ticks=1,
        pessimistic_both_hit=True,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": MARKETABLE_TICKS},
    )
    cancelled = broker.execute_bracket(inverse, market_price=102.25)
    assert cancelled.result == "CANCELLED"
    assert cancelled.exit_reason == "ENTRY_NOT_FILLED"


# ── 5-minute re-trigger isolation ─────────────────────────────────────────────
# The armed-setup retest re-runs an ORIGINAL 15m orb_breakout decision on a 5m
# bar. bar_timeframe_minutes is deliberately 5 there (the claim is tagged to the
# bar the alert arrived on), which used to make the inverse lane's guard skip the
# candidate entirely: it fell through to the non-inverse branch and was priced
# against all-history journal accounting and the configured external broker.
# ENTRY_DETACHED_FROM_PRICE is both the arming gate and this lane's most common
# miss, so the retest is its main second-chance route.

_ARMED_15M_TS = "2026-05-23T15:00:00+00:00"
_RETEST_5M_TS = "2026-05-23T15:20:00+00:00"
# The 15m engine's own source setup for `_payload(...)`; the runner arms the
# ORIGINAL (uninverted) setup, so the retest matches a LONG entry.
_SOURCE_SETUP = {
    "direction": "LONG",
    "entry": 19498.5,
    "stop": 19486.0,
    "target": 19526.0,
    "rr_ratio": 2.2,
    "strategy": "orb_breakout",
    "contracts": 1,
}


def _arm_then_retest(cfg, monkeypatch):
    """Arm the 15m setup, then return the 5m bar that retests its entry."""
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    arm_fifteen_min_setup(
        "MNQ1!",
        cfg.log_dir,
        setup=dict(_SOURCE_SETUP),
        payload=_payload(timestamp=_ARMED_15M_TS).model_dump(),
        for_date=date(2026, 5, 23),
    )
    # LONG retest: trades at/below entry and closes back within one tick of it.
    return _payload(
        timestamp=_RETEST_5M_TS,
        timeframe="5",
        open=19500.0,
        high=19499.0,
        low=19497.0,
        close=19498.5,
    )


def _inverse_audits(log_dir) -> list[dict]:
    return [
        row["mnq_orb_breakout_inverse_audit"]
        for row in _journal_rows(log_dir)
        if row.get("mnq_orb_breakout_inverse_audit")
    ]


def test_five_min_retrigger_is_owned_by_the_active_inverse_lane(
    tmp_path, monkeypatch
):
    """REGRESSION: the 5m retest used to escape the lane entirely.

    Before the fix this candidate carried no inverse audit at all — proof it
    took the non-inverse branch.
    """
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")

    class TradovateMustNotExist:
        def __init__(self, *args, **kwargs):
            raise AssertionError("5m re-triggered inverse candidate reached Tradovate")

    monkeypatch.setattr(tradovate_module, "TradovateBroker", TradovateMustNotExist)

    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
    )
    retest = _arm_then_retest(cfg, monkeypatch)

    result = process_alert(
        retest,
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "TRADE"
    audits = _inverse_audits(cfg.log_dir)
    assert audits, "5m re-triggered candidate escaped the inverse lane"
    assert audits[-1]["apply_override"] is True
    assert audits[-1]["force_paper_broker"] is True
    # The claim stays tagged to the 5m bar it arrived on — the fix must not
    # relabel the bar to buy itself the old `== 15` guard.
    assert [
        row["timeframe_minutes"]
        for row in _journal_rows(cfg.log_dir)
        if row.get("mnq_orb_breakout_inverse_audit")
    ][-1] == 5


def test_five_min_retrigger_submits_the_isolated_inverse_bracket(
    tmp_path, monkeypatch
):
    import execution.tradovate_broker as tradovate_module

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setattr(
        tradovate_module,
        "TradovateBroker",
        lambda *a, **k: pytest.fail("external broker constructed"),
    )
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
    )
    retest = _arm_then_retest(cfg, monkeypatch)

    result = process_alert(
        retest,
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    fill = result["fill"]
    assert fill["direction"] == "SHORT"          # mirrored from the LONG source
    assert fill["contracts"] == 1                # fixed one MNQ contract
    assert fill["paper_order_id"].startswith("PAPER-")
    audit = _inverse_audits(cfg.log_dir)[-1]
    assert audit["source_setup"]["direction"] == "LONG"
    assert audit["submitted_setup"]["direction"] == "SHORT"
    assert audit["submitted_setup"]["contracts"] == 1
    assert audit["marketable_ticks"] == MARKETABLE_TICKS


def test_five_min_retrigger_uses_epoch_scoped_balance_and_peak(
    tmp_path, monkeypatch
):
    """A pre-epoch drawdown must not gate the 5m path either.

    This is the exact shape of the deployed defect: peak $1,910.75 frozen by a
    pre-epoch loss run, compared against a candidate the epoch-scoped lane owns.
    """
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=False,
        max_drawdown_percent=0.20,
    )
    _write_outcomes(
        cfg.log_dir,
        [
            _outcome("2026-05-23T12:00:00+00:00", "WIN", 410.75),
            _outcome("2026-05-23T12:15:00+00:00", "LOSS", -205.00),
            _outcome("2026-05-23T12:30:00+00:00", "LOSS", -205.00),
        ],
    )
    retest = _arm_then_retest(cfg, monkeypatch)

    result = process_alert(
        retest,
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "TRADE"
    assert result["risk"]["result"] == "APPROVED"


def test_five_min_bar_without_an_armed_setup_stays_out_of_the_lane(
    tmp_path, monkeypatch
):
    """The fix widens the guard by exactly one case: a fired retest.

    A bare 5m bar (nothing armed) must still be ignored by the lane.
    """
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=True,
    )

    process_alert(
        _payload(timestamp=_RETEST_5M_TS, timeframe="5"),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    assert _inverse_audits(cfg.log_dir) == []


def test_fifteen_min_path_unchanged_with_the_five_min_feed_enabled(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    cfg = _config(
        tmp_path,
        mnq_orb_breakout_inverse_mode="paper_sim",
        paper_mode=True,
    )

    result = process_alert(
        _payload(timestamp=_ARMED_15M_TS),
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=date(2026, 5, 23),
    )

    assert result["decision"] == "TRADE"
    audit = _inverse_audits(cfg.log_dir)[-1]
    assert audit["apply_override"] is True
    assert audit["submitted_setup"]["direction"] == "SHORT"
    assert audit["submitted_setup"]["contracts"] == 1
