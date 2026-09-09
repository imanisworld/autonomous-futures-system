"""Contract and isolation invariants for the MES 15m 1-2-2 forward-paper lane."""
import dataclasses
import json
from datetime import date, timedelta

import pytest

from context import mes_122_paper_lane as lane


# ─────────────────────────────── pinned contract ─────────────────────────────


def test_campaign_parameters_are_pinned():
    assert lane.INSTRUMENT == "MES"
    assert lane.STRATEGY == "strat_122"
    assert lane.STARTING_BALANCE == 1_500.0
    assert lane.CONTRACTS == 1
    assert lane.COMMISSION_ROUND_TRIP == 1.48
    assert lane.MAX_DRAWDOWN == 0.30
    assert lane.WARN_DRAWDOWNS == (0.20, 0.25)


def test_lane_is_paper_only_and_inactive_by_default():
    assert lane.VALID_MODES == ("observe_only", "paper_sim")
    assert lane.DEFAULT_MODE == "observe_only"
    assert "tradovate" not in " ".join(lane.VALID_MODES)


def test_lane_needs_both_paper_sim_and_an_offset_aware_epoch(monkeypatch):
    monkeypatch.delenv(lane.MODE_ENV, raising=False)
    monkeypatch.delenv(lane.EPOCH_ENV, raising=False)
    assert lane.is_active(None) is False

    monkeypatch.setenv(lane.MODE_ENV, "paper_sim")
    assert lane.is_active(None) is False  # no epoch

    monkeypatch.setenv(lane.EPOCH_ENV, "2026-09-09T12:00:00")  # naive
    assert lane.is_active(None) is False

    monkeypatch.setenv(lane.EPOCH_ENV, "2026-09-09T12:00:00+00:00")
    assert lane.is_active(None) is True


def test_only_mes_strat_122_is_a_lane_candidate():
    assert lane.is_candidate("MES", "strat_122") is True
    assert lane.is_candidate("MES1!", "strat_122") is True
    assert lane.is_candidate("MNQ", "strat_122") is False
    assert lane.is_candidate("MES", "orb_breakout") is False


# ─────────────────────────── isolation / no-Tradovate ────────────────────────


def test_lane_config_forces_paper_and_cannot_reach_tradovate(config):
    lane_cfg = lane.lane_config(config)
    # webhook/runner.py derives `simulate` from paper_mode; simulate=True selects
    # _paper_broker() for execution and makes _using_tradovate_position False.
    assert lane_cfg.paper_mode is True


def test_lane_config_isolates_the_strategy_so_ranking_cannot_preempt_it(config):
    lane_cfg = lane.lane_config(config)
    # risk_rules.yaml:395-403 — the permission gate alone is not isolation;
    # strat_122 must be the ONLY enabled concept.
    assert lane_cfg.enabled_concepts == ["strat_122"]
    assert lane_cfg.allowed_instruments == ["MES"]
    assert lane_cfg.disabled_concepts_per_instrument == {}
    assert lane_cfg.strategy_status["strat_122"] == "PAPER_ELIGIBLE"


def test_lane_config_pins_one_contract_and_no_scaling(config):
    lane_cfg = lane.lane_config(config)
    assert lane_cfg.max_contracts_per_instrument == {"MES": 1}
    assert lane_cfg.max_contracts_hard_cap == 1
    assert lane_cfg.win_streak_bonus_after == 0
    assert lane_cfg.win_streak_bonus_contracts == 0
    assert lane_cfg.position_sizing.enabled is False
    assert lane_cfg.position_sizing.starting_balance == 1_500.0


def test_lane_config_does_not_mutate_the_shipped_config(config):
    before = dataclasses.asdict(config) if dataclasses.is_dataclass(config) else None
    allowed_before = list(config.allowed_instruments)
    concepts_before = list(config.enabled_concepts)
    lane.lane_config(config)
    assert list(config.allowed_instruments) == allowed_before
    assert list(config.enabled_concepts) == concepts_before
    if before is not None:
        assert dataclasses.asdict(config) == before


def test_lane_journal_root_is_never_the_real_book(tmp_path):
    root = lane.journal_dir(tmp_path)
    assert root == tmp_path / "hypothetical_ledger" / "mes_122_1500"
    assert root != tmp_path


def test_lane_refuses_to_recurse_into_itself(config, tmp_path, monkeypatch):
    monkeypatch.setenv(lane.MODE_ENV, "paper_sim")
    monkeypatch.setenv(lane.EPOCH_ENV, "2026-09-09T12:00:00+00:00")
    lane_cfg = lane.lane_config(config)
    assert lane.is_lane_config(lane_cfg) is True
    # An alert re-entering process_alert on the lane's own config must not
    # spawn another lane evaluation.
    assert lane.observe_alert(object(), cfg=lane_cfg, log_dir=str(tmp_path)) is None


def test_lane_ignores_non_mes_alerts(config, tmp_path, monkeypatch):
    monkeypatch.setenv(lane.MODE_ENV, "paper_sim")
    monkeypatch.setenv(lane.EPOCH_ENV, "2026-09-09T12:00:00+00:00")

    class _P:
        ticker = "MNQ1!"

    assert lane.observe_alert(_P(), cfg=config, log_dir=str(tmp_path)) is None


# ───────────────────────────── realistic ledger ──────────────────────────────


def _outcome(pnl, *, result="LOSS", same_bar=False):
    row = {"result": result, "pnl_dollars": pnl, "strategy": "strat_122"}
    if same_bar:
        row["execution_audit"] = {"source": lane.SAME_BAR_SOURCE}
    return row


def test_ordinary_fill_costs_one_entry_tick_plus_commission():
    # PR #553: restore_position() never slips the entry, so the realistic ledger
    # charges exactly one adverse tick there; ordinary market exits already carry
    # the broker's own slippage and are not charged twice.
    assert lane.realistic_cost_dollars(same_bar_resolved=False) == 1.25
    assert lane.realistic_pnl(_outcome(0.0)) == round(-1.25 - 1.48, 2)


def test_same_bar_fill_costs_two_ticks_because_force_resolve_slips_neither_leg():
    assert lane.realistic_cost_dollars(same_bar_resolved=True) == 2.50
    assert lane.realistic_pnl(_outcome(0.0, same_bar=True)) == round(-2.50 - 1.48, 2)


def test_ledger_uses_realistic_pnl_not_raw_paper_pnl():
    rows = [_outcome(10.0, result="WIN"), _outcome(-5.0)]
    state = lane.ledger_state(rows)
    assert state["basis"] == "realistic_1_tick_per_leg"
    assert state["resolved_trades"] == 2
    # raw: 1500 + 10 - 5 = 1505; realistic: each leg costs 1.25 + 1.48 commission
    assert state["raw_paper_balance_diagnostic_only"] == 1_505.0
    assert state["realistic_balance"] == round(1_500.0 + (10.0 - 2.73) + (-5.0 - 2.73), 2)
    assert state["realistic_balance"] < state["raw_paper_balance_diagnostic_only"]


def test_unresolved_rows_are_not_counted():
    assert lane.ledger_state([{"result": "CANCELLED", "pnl_dollars": 0.0}])["resolved_trades"] == 0


def test_halt_is_driven_by_the_realistic_ledger():
    # A drawdown that only crosses 30% once realistic per-leg cost is applied
    # must still halt the lane.
    losses = [_outcome(-45.0) for _ in range(10)]
    state = lane.ledger_state(losses)
    assert state["realistic_closed_trade_drawdown_percent"] > lane.MAX_DRAWDOWN
    assert state["halted"] is True


def test_warning_thresholds_fire_before_the_halt():
    losses = [_outcome(-30.0) for _ in range(11)]  # ~22% realistic
    state = lane.ledger_state(losses)
    assert state["halted"] is False
    assert state["warning_drawdown"] == 0.20


def test_flat_ledger_is_not_halted():
    state = lane.ledger_state([])
    assert state["realistic_balance"] == 1_500.0
    assert state["halted"] is False
    assert state["warning_drawdown"] is None


# ───────────────────────────── config validation ─────────────────────────────


def test_paper_sim_requires_an_offset_aware_epoch_in_config(monkeypatch):
    from config.settings import ConfigError, load_config

    monkeypatch.setenv("MES_122_PAPER_MODE", "paper_sim")
    monkeypatch.delenv("MES_122_PAPER_EPOCH_START", raising=False)
    with pytest.raises(ConfigError, match="MES_122_PAPER_EPOCH_START is required"):
        load_config()

    monkeypatch.setenv("MES_122_PAPER_EPOCH_START", "2026-09-09T12:00:00")
    with pytest.raises(ConfigError, match="must include a UTC offset"):
        load_config()


def test_invalid_mode_is_rejected(monkeypatch):
    from config.settings import ConfigError, load_config

    monkeypatch.setenv("MES_122_PAPER_MODE", "tradovate_demo")
    with pytest.raises(ConfigError, match="MES_122_PAPER_MODE must be one of"):
        load_config()


def test_default_config_leaves_the_lane_off(monkeypatch):
    from config.settings import load_config

    monkeypatch.delenv("MES_122_PAPER_MODE", raising=False)
    monkeypatch.delenv("MES_122_PAPER_EPOCH_START", raising=False)
    cfg = load_config()
    assert cfg.mes_122_paper_mode == "observe_only"
    assert lane.is_active(cfg) is False


# ───────────────────────────── end-to-end isolation ──────────────────────────


def _active(config):
    """The lane reads its own config fields first (env is only the fallback that
    load_config() populates), so activate it the way production does."""
    return dataclasses.replace(
        config,
        mes_122_paper_mode="paper_sim",
        mes_122_paper_epoch_start="2026-01-01T00:00:00+00:00",
    )


def _mes_payload(**overrides):
    from tests.test_webhook import _base_payload

    data = {
        "ticker": "MES1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "open": 6800.0,
        "high": 6810.0,
        "low": 6795.0,
        "close": 6803.0,
    }
    data.update(overrides)
    return _base_payload(**data)


def test_real_book_decision_is_unchanged_while_the_lane_runs(config, tmp_path):
    """The lane is an observer: the real book's own decision must be identical
    with the lane on and off, whatever that decision happens to be."""
    from webhook.runner import process_alert

    payload = _mes_payload()
    off = process_alert(payload, config=config, log_dir=str(tmp_path / "off"),
                        for_date=date(2026, 5, 23))
    on = process_alert(payload, config=_active(config), log_dir=str(tmp_path / "on"),
                       for_date=date(2026, 5, 23))

    for key in ("decision", "resolution", "risk", "fill"):
        assert off.get(key) == on.get(key), f"lane perturbed the real book's {key}"


def test_lane_writes_only_to_its_own_journal_root(config, tmp_path):
    from webhook.runner import process_alert

    log_dir = tmp_path / "logs"
    process_alert(_mes_payload(), config=_active(config), log_dir=str(log_dir),
                  for_date=date(2026, 5, 23))

    assert lane.journal_dir(log_dir).exists(), "lane must journal into its own root"


def test_lane_journal_is_separate_from_the_real_books(config, tmp_path):
    """Whatever the lane books, it must land under its own root, not the real book's."""
    from webhook.runner import process_alert

    log_dir = tmp_path / "logs"
    process_alert(_mes_payload(), config=_active(config), log_dir=str(log_dir),
                  for_date=date(2026, 5, 23))

    lane_rows = [
        json.loads(line)
        for path in lane.journal_dir(log_dir).glob("journal_*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert lane_rows, "lane produced no journal rows"
    # Nothing the lane wrote may appear in the real book's own journal files.
    real_root_files = list(log_dir.glob("journal_*.jsonl"))
    for path in real_root_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = json.loads(line)
            setup = entry.get("setup") or {}
            assert not (
                setup.get("strategy") == "strat_122" and entry.get("decision") == "TRADE"
            ), "a lane trade leaked into the real book's journal"


def test_lane_never_constructs_a_non_paper_broker(config, tmp_path, monkeypatch):
    """Even with BROKER=tradovate, the lane's paper_mode pin keeps it on PaperBroker."""
    import webhook.runner as runner

    monkeypatch.setenv("BROKER", "tradovate")

    def _fail(*_a, **_k):
        pytest.fail("lane must never construct a non-paper broker")

    monkeypatch.setattr(runner, "_make_broker", _fail)
    runner.process_alert(_mes_payload(), config=_active(config), log_dir=str(tmp_path),
                         for_date=date(2026, 5, 23))


# ──────────────── ledger counts EVERY outcome path, not just same-bar ────────
#
# Regression for the blocker found in review of #555: `_lane_outcomes()` filters
# on the OUTCOME row's strategy, but the normal `fill is not None` resolution and
# the stale/price-mismatch force-close both used to journal without one, so the
# realistic ledger silently ignored ordinary later-bar wins and losses while
# still counting same-bar outcomes. Balance, warnings and the 30% halt were
# therefore untrustworthy. `strategy=_open_pos_strategy` is now passed on both
# paths (metadata only; fill behavior unchanged).


def _seed_lane_open_trade(log_dir, for_date, *, entry=6800.0, stop=6790.0, target=6820.0,
                          direction="LONG"):
    """An OPEN MES strat_122 trade in the lane's own journal."""
    from journal.journal_logger import JournalLogger

    journal = JournalLogger(log_dir=str(lane.journal_dir(log_dir)))
    journal._append({
        "ts": f"{for_date.isoformat()}T14:00:00+00:00",
        "instrument": "MES",
        "session": "new_york",
        "decision": "TRADE",
        "reason": "lane ledger regression",
        "market_condition": "TRENDING",
        "setup": {
            "direction": direction, "entry": entry, "stop": stop, "target": target,
            "rr_ratio": 2.0, "strategy": "strat_122", "notes": None, "contracts": 1,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, for_date)
    return journal


def _lane_resolved(config, tmp_path, for_date):
    return lane.ledger_status(_active(config), tmp_path, for_date)


def test_normal_later_bar_resolution_is_counted_by_the_realistic_ledger(config, tmp_path):
    """The blocker: an ordinary stop/target resolution must reach the ledger."""
    from webhook.runner import process_alert

    day = date(2026, 5, 22)
    nextday = date(2026, 5, 23)
    _seed_lane_open_trade(tmp_path, day)
    assert _lane_resolved(config, tmp_path, nextday)["resolved_trades"] == 0

    # A later bar that trades through the stop resolves the position normally.
    process_alert(
        _mes_payload(timestamp="2026-05-23T14:30:00+00:00",
                     open=6795.0, high=6798.0, low=6780.0, close=6788.0),
        config=_active(config), log_dir=str(tmp_path), for_date=nextday,
    )

    status = _lane_resolved(config, tmp_path, nextday)
    assert status["resolved_trades"] == 1, "normal resolution was skipped by the ledger"


def test_realistic_balance_charges_one_entry_tick_and_commission_on_a_normal_close(
    config, tmp_path
):
    from journal.journal_logger import JournalLogger
    from webhook.runner import process_alert

    day = date(2026, 5, 22)
    nextday = date(2026, 5, 23)
    _seed_lane_open_trade(tmp_path, day)
    process_alert(
        _mes_payload(timestamp="2026-05-23T14:30:00+00:00",
                     open=6795.0, high=6798.0, low=6780.0, close=6788.0),
        config=_active(config), log_dir=str(tmp_path), for_date=nextday,
    )

    outcomes = [
        json.loads(line)["outcome"]
        for path in lane.journal_dir(tmp_path).glob("journal_*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("type") == "OUTCOME"
    ]
    assert len(outcomes) == 1
    raw = float(outcomes[0]["pnl_dollars"])
    assert lane.is_same_bar_resolved(outcomes[0]) is False

    status = _lane_resolved(config, tmp_path, nextday)
    # raw − 1 adverse entry tick ($1.25) − $1.48 commission
    assert status["realistic_balance"] == round(1_500.0 + raw - 1.25 - 1.48, 2)
    assert status["raw_paper_balance_diagnostic_only"] == round(1_500.0 + raw, 2)
    _ = JournalLogger


def test_price_mismatch_force_close_is_counted_by_the_realistic_ledger(config, tmp_path):
    """The other unstrategied path: a force-close must reach the ledger too."""
    from webhook.runner import process_alert

    day = date(2026, 5, 22)
    nextday = date(2026, 5, 23)
    # Deliberately wide bracket so stop/target cannot resolve it; only the
    # price-scale mismatch safety close can decide the outcome.
    _seed_lane_open_trade(tmp_path, day, entry=6800.0, stop=100.0, target=25000.0)

    process_alert(
        _mes_payload(timestamp="2026-05-23T14:30:00+00:00",
                     open=7250.0, high=7300.0, low=7200.0, close=7250.0),
        config=_active(config), log_dir=str(tmp_path), for_date=nextday,
    )

    # The force-close happens inside the LANE's own evaluation (its journal), not
    # the real book's — so assert on the lane's outcome row and its ledger.
    outcomes = [
        json.loads(line)["outcome"]
        for path in lane.journal_dir(tmp_path).glob("journal_*.jsonl")
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and json.loads(line).get("type") == "OUTCOME"
    ]
    assert len(outcomes) == 1
    assert outcomes[0]["exit_reason"] == "FORCE_CLOSE_PRICE_MISMATCH"
    assert outcomes[0]["strategy"] == "strat_122", "force-close outcome lost its strategy"

    status = _lane_resolved(config, tmp_path, nextday)
    assert status["resolved_trades"] == 1, "force-close was skipped by the ledger"


def test_same_bar_outcome_still_counts_and_costs_two_ticks():
    """Unchanged behavior: the same-bar path already carried its strategy."""
    same_bar = _outcome(0.0, same_bar=True)
    assert lane.is_same_bar_resolved(same_bar) is True
    state = lane.ledger_state([same_bar])
    assert state["resolved_trades"] == 1
    assert state["realistic_balance"] == round(1_500.0 - 2.50 - 1.48, 2)


# ──────────────────────── timeframe pin + swing visibility ───────────────────


def test_lane_config_pins_the_15m_decision_timeframe(config):
    lane_cfg = lane.lane_config(config)
    assert lane.TIMEFRAME_MINUTES == 15
    assert lane_cfg.expected_timeframe_minutes == 15


def test_closed_trade_drawdown_is_not_labelled_mark_to_market():
    state = lane.ledger_state([_outcome(-100.0)])
    assert state["drawdown_basis"] == "closed_trade_realistic"
    assert "realistic_closed_trade_drawdown_percent" in state
    assert "max_mtm_drawdown" not in state


def test_open_position_exposure_is_reported_and_observational(config, tmp_path):
    day = date(2026, 5, 22)
    _seed_lane_open_trade(tmp_path, day, entry=6800.0)

    # Marked 20 points against a LONG: -80 ticks x $1.25 = -$100.00
    exposure = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6780.0, for_date=day
    )
    assert exposure["open"] is True
    assert exposure["basis"] == "observational_only"
    assert exposure["unrealized_dollars_raw"] == -100.0
    # closed balance ($1,500, no closes yet) + unrealized - the entry tick already paid
    assert exposure["realistic_mtm_equity"] == round(1_500.0 - 100.0 - 1.25, 2)
    assert exposure["open_position_mtm_drawdown_percent"] > 0


def test_open_position_exposure_is_flat_when_nothing_is_open(config, tmp_path):
    exposure = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6800.0, for_date=date(2026, 5, 22)
    )
    assert exposure["open"] is False
    assert exposure["basis"] == "observational_only"


def test_open_position_exposure_never_halts_the_lane(config, tmp_path):
    """Swing exposure is visibility only: a deep unrealized excursion must not
    halt the lane, which halts on the CLOSED-trade realistic ledger alone."""
    day = date(2026, 5, 22)
    _seed_lane_open_trade(tmp_path, day, entry=6800.0)
    exposure = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6300.0, for_date=day
    )
    assert exposure["exceeds_halt_threshold_observational"] is True
    assert lane.ledger_status(_active(config), tmp_path, day)["halted"] is False


# ────────────────── swing observer must find CARRIED positions ───────────────
#
# Regression for the second review blocker: open_position_exposure() checked only
# `for_date`, but webhook/runner.py:876-885 walks back up to 7 calendar days so a
# Friday->Monday carry is still found. A genuinely open weekend swing therefore
# reported `open: False`, defeating the swing-risk visibility this lane exists to
# provide.

FRIDAY = date(2026, 5, 22)
MONDAY = date(2026, 5, 25)


def test_exposure_finds_a_friday_position_still_open_on_monday(config, tmp_path):
    _seed_lane_open_trade(tmp_path, FRIDAY, entry=6800.0)

    exposure = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6780.0, for_date=MONDAY
    )

    assert exposure["open"] is True, "weekend carry reported as flat"
    assert exposure["open_position_date"] == FRIDAY.isoformat()
    assert exposure["direction"] == "LONG"
    assert exposure["entry"] == 6800.0
    # 20 points against a LONG = 80 ticks x $1.25
    assert exposure["unrealized_dollars_raw"] == -100.0
    # closed balance ($1,500, nothing resolved yet) + unrealized − entry tick paid
    assert exposure["realistic_mtm_equity"] == round(1_500.0 - 100.0 - 1.25, 2)
    assert exposure["open_position_mtm_drawdown_percent"] > 0


def test_exposure_carry_lookback_matches_the_runners_seven_days(config, tmp_path):
    assert lane.CARRY_LOOKBACK_DAYS == 7
    # 7 days back is still found; 8 is outside the window, exactly like the runner.
    _seed_lane_open_trade(tmp_path, FRIDAY, entry=6800.0)
    inside = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6800.0,
        for_date=FRIDAY + timedelta(days=7),
    )
    outside = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6800.0,
        for_date=FRIDAY + timedelta(days=8),
    )
    assert inside["open"] is True
    assert outside["open"] is False


def test_weekend_swing_resolves_on_monday_and_the_ledger_counts_it(config, tmp_path):
    """End to end: open Friday, still visible Monday, resolve Monday, exposure
    goes flat and the realistic closed-trade ledger picks the outcome up."""
    from webhook.runner import process_alert

    _seed_lane_open_trade(tmp_path, FRIDAY, entry=6800.0, stop=6790.0, target=6820.0)

    before = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6795.0, for_date=MONDAY
    )
    assert before["open"] is True
    assert _lane_resolved(config, tmp_path, MONDAY)["resolved_trades"] == 0

    # Monday bar trades through the stop: the carried position resolves.
    process_alert(
        _mes_payload(timestamp="2026-05-25T14:30:00+00:00",
                     open=6795.0, high=6798.0, low=6780.0, close=6788.0),
        config=_active(config), log_dir=str(tmp_path), for_date=MONDAY,
    )

    after = lane.open_position_exposure(
        _active(config), tmp_path, mark_price=6788.0, for_date=MONDAY
    )
    assert after["open"] is False, "exposure still reports open after resolution"

    status = _lane_resolved(config, tmp_path, MONDAY)
    assert status["resolved_trades"] == 1, "weekend swing outcome missed by the ledger"
    assert status["realistic_balance"] < 1_500.0
