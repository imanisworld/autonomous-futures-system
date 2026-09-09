"""Regression proofs for the wide-stop hypothetical-ledger lane.

Covers the tests spec §8 names by hand:
  - mode parsing and fail-closed config validation;
  - a 4HR candidate at the median 254-tick stop with R:R between 1.0 and 2.0
    is admitted by `wide_stop_4k` and rejected by the real book;
  - a 3-2-2 candidate at 700 ticks is rejected by both;
  - ledger isolation — the real book's balance is unchanged after a lane fill;
  - journal labeling;
  - the look-ahead check on the fill reference.

Everything here is synthetic. No corpus, no replay, no broker.
"""
from __future__ import annotations

import pytest

from config.settings import ConfigError, SystemConfig, _validate_wide_stop_ledger
from context import wide_stop_ledger_paper as lane
from execution.broker_interface import BracketOrder
from execution.paper_broker import PaperBroker
from risk.risk_engine import DailyState, RiskEngine, TradeSetup


TICK = 0.25
FOUR_HR = "strat_4hr_retrigger"
THREE_TWO_TWO = "strat_322_first_live"
MIYAGI = "strat_12hr_miyagi"


# ────────────────────────────── mode parsing ────────────────────────────────


@pytest.mark.parametrize("value,expected", [
    ("observe_only", "observe_only"),
    ("paper_sim", "paper_sim"),
    ("PAPER_SIM", "paper_sim"),
    ("  paper_sim  ", "paper_sim"),
    ("tradovate_demo", "observe_only"),   # no demo mode exists for this lane
    ("live", "observe_only"),
    ("", "observe_only"),
    (None, "observe_only"),
])
def test_mode_falls_back_to_observe_only(value, expected, monkeypatch):
    monkeypatch.delenv("WIDE_STOP_LEDGER_MODE", raising=False)
    cfg = type("C", (), {"wide_stop_ledger_mode": value})()
    assert lane.mode(cfg) == expected


def test_evaluate_is_inactive_unless_paper_sim():
    inactive = lane.evaluate(type("C", (), {"wide_stop_ledger_mode": "observe_only"})())
    assert not inactive.active
    active = lane.evaluate(type("C", (), {
        "wide_stop_ledger_mode": "paper_sim",
        "wide_stop_ledger_epoch_start": "2026-09-08T00:00:00+00:00",
    })())
    assert active.active
    assert active.contracts == 1
    assert active.marketable_ticks == 8.0


# ─────────────────────────── fail-closed validation ─────────────────────────


def _cfg(**overrides) -> SystemConfig:
    """A minimal SystemConfig carrying only what these tests read."""
    base = SystemConfig.__new__(SystemConfig)
    object.__setattr__(base, "wide_stop_ledger_mode", "observe_only")
    object.__setattr__(base, "wide_stop_ledger_epoch_start", None)
    for key, value in overrides.items():
        object.__setattr__(base, key, value)
    return base


def test_observe_only_needs_no_epoch():
    _validate_wide_stop_ledger(_cfg())


@pytest.mark.parametrize("bad", ["live", "tradovate_demo", "demo", "paper", "PAPER_SIM "])
def test_invalid_mode_fails_closed(bad):
    with pytest.raises(ConfigError, match="WIDE_STOP_LEDGER_MODE"):
        _validate_wide_stop_ledger(_cfg(wide_stop_ledger_mode=bad))


def test_paper_sim_requires_an_epoch():
    with pytest.raises(ConfigError, match="EPOCH_START is required"):
        _validate_wide_stop_ledger(_cfg(wide_stop_ledger_mode="paper_sim"))


def test_epoch_must_be_iso8601():
    with pytest.raises(ConfigError, match="ISO-8601"):
        _validate_wide_stop_ledger(
            _cfg(wide_stop_ledger_mode="paper_sim", wide_stop_ledger_epoch_start="last tuesday")
        )


def test_epoch_must_carry_a_utc_offset():
    with pytest.raises(ConfigError, match="UTC offset"):
        _validate_wide_stop_ledger(
            _cfg(wide_stop_ledger_mode="paper_sim",
                 wide_stop_ledger_epoch_start="2026-09-08T00:00:00")
        )


def test_a_valid_epoch_passes():
    _validate_wide_stop_ledger(
        _cfg(wide_stop_ledger_mode="paper_sim",
             wide_stop_ledger_epoch_start="2026-09-08T00:00:00+00:00")
    )


# ────────────────────────────── membership ──────────────────────────────────


def test_membership_routes_each_strategy_to_its_ledger():
    assert lane.ledger_for("MNQ", FOUR_HR).name == "wide_stop_4k"
    assert lane.ledger_for("MNQ", THREE_TWO_TWO).name == "wide_stop_6k"
    assert lane.ledger_for("MNQ", MIYAGI).name == "wide_stop_6k"


def test_miyagi_is_shadow_only_and_cannot_fill():
    """D5: it is a research detector, never wired into signal_engine."""
    assert lane.role("MNQ", MIYAGI) == "shadow_only"
    assert not lane.is_fill_eligible("MNQ", MIYAGI)
    assert lane.is_fill_eligible("MNQ", FOUR_HR)
    assert lane.is_fill_eligible("MNQ", THREE_TWO_TWO)


@pytest.mark.parametrize("instrument,strategy", [
    ("MES", FOUR_HR),          # wrong instrument
    ("MNQ", "orb_breakout"),   # not a family member
    ("MNQ", "vwap_hold"),
    (None, FOUR_HR),
])
def test_non_members_get_no_ledger(instrument, strategy):
    assert lane.ledger_for(instrument, strategy) is None
    assert lane.role(instrument, strategy) is None


def test_mnq1_contract_suffix_is_still_mnq():
    assert lane.ledger_for("MNQ1!", FOUR_HR).name == "wide_stop_4k"


# ───────────────────── the §8 admit/reject regression proofs ────────────────


def _setup(strategy: str, stop_ticks: float, rr: float) -> TradeSetup:
    entry = 20_000.0
    stop = entry - stop_ticks * TICK
    return TradeSetup(
        instrument="MNQ", direction="LONG", entry=entry, stop=stop,
        target=entry + stop_ticks * TICK * rr, rr_ratio=rr, strategy=strategy,
        session="new_york",
    )


def _engine_rejection(config, setup: TradeSetup) -> str | None:
    """Just the stop-cap / R:R verdict, isolated from session and state gates."""
    engine = RiskEngine(config=config)
    for check in (engine._check_max_stop_distance, engine._check_rr_ratio):
        result = check(setup, DailyState())
        if result is not None:
            return result.failed_rule
    return None


@pytest.fixture
def real_book():
    """The real $1,500 book's binding constraints: 120 ticks, R:R 2.0."""
    cfg = SystemConfig.__new__(SystemConfig)
    object.__setattr__(cfg, "max_stop_ticks", {"MNQ": 120.0})
    object.__setattr__(cfg, "min_rr_ratio", 2.0)
    object.__setattr__(cfg, "max_daily_loss", 150.0)
    object.__setattr__(cfg, "max_drawdown_percent", 0.20)
    object.__setattr__(cfg, "runner_mode", False)
    object.__setattr__(cfg, "schedule_mode", "always")
    return cfg


def test_median_4hr_candidate_is_admitted_by_the_lane_and_rejected_by_the_book(real_book):
    """Spec §8: 254-tick stop, R:R between 1.0 and 2.0 — the whole point."""
    setup = _setup(FOUR_HR, stop_ticks=254.0, rr=1.4)
    assert _engine_rejection(real_book, setup) == "stop_too_wide"

    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"])
    assert _engine_rejection(lane_cfg, setup) is None


def test_a_sub_one_rr_4hr_candidate_is_still_rejected_by_the_lane(real_book):
    """D3 kept an R:R >= 1.0 floor on wide_stop_4k — it is not a free pass."""
    setup = _setup(FOUR_HR, stop_ticks=254.0, rr=0.9)
    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"])
    assert _engine_rejection(lane_cfg, setup) == "rr_below_minimum"


def test_a_low_rr_322_candidate_is_admitted_because_its_floor_is_disabled(real_book):
    """3-2-2's median R:R is 0.24; the floor cannot be kept in any form."""
    setup = _setup(THREE_TWO_TWO, stop_ticks=472.0, rr=0.24)
    assert _engine_rejection(real_book, setup) == "stop_too_wide"
    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_6k"])
    assert _engine_rejection(lane_cfg, setup) is None


def test_a_301_tick_4hr_candidate_is_rejected_by_the_lane(real_book):
    """Operator amendment 2026-09-08: wide_stop_4k is capped at 300 ticks."""
    setup = _setup(FOUR_HR, stop_ticks=301.0, rr=1.4)
    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"])
    assert _engine_rejection(lane_cfg, setup) == "stop_too_wide"


def test_a_700_tick_322_candidate_is_rejected_by_both(real_book):
    """Spec §8: the family cap is a cap, not a removal."""
    setup = _setup(THREE_TWO_TWO, stop_ticks=700.0, rr=1.0)
    assert _engine_rejection(real_book, setup) == "stop_too_wide"
    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_6k"])
    assert _engine_rejection(lane_cfg, setup) == "stop_too_wide"


def test_lane_config_leaves_the_global_config_untouched(real_book):
    """The global RiskEngine and its rules must never be mutated."""
    before_caps = dict(real_book.max_stop_ticks)
    before_rr = real_book.min_rr_ratio
    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"])
    assert real_book.max_stop_ticks == before_caps
    assert real_book.min_rr_ratio == before_rr
    assert lane_cfg.max_stop_ticks is not real_book.max_stop_ticks
    assert lane_cfg.max_stop_ticks["MNQ"] == 300.0
    assert lane_cfg.min_rr_ratio == 1.0


def test_lane_config_carries_the_lane_scoped_floors(real_book):
    four_k = lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"])
    six_k = lane.lane_config(real_book, lane.LEDGERS["wide_stop_6k"])
    assert (four_k.max_daily_loss, four_k.max_drawdown_percent) == (300.0, 0.20)
    assert (six_k.max_daily_loss, six_k.max_drawdown_percent) == (600.0, 0.20)
    assert real_book.max_daily_loss == 150.0


def test_lane_never_enables_runner_mode(real_book):
    """runner_mode would skip the R:R gate entirely — not an authorized override."""
    object.__setattr__(real_book, "runner_mode", True)
    assert lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"]).runner_mode is False


def test_other_instruments_keep_the_real_cap(real_book):
    object.__setattr__(real_book, "max_stop_ticks", {"MNQ": 120.0, "MES": 100.0})
    lane_cfg = lane.lane_config(real_book, lane.LEDGERS["wide_stop_4k"])
    assert lane_cfg.max_stop_ticks["MES"] == 100.0


# ─────────────── only the two gates may be overridden (spec §3) ─────────────


@pytest.mark.parametrize("rules,expected", [
    (["stop_too_wide"], True),
    (["rr_below_minimum"], True),
    (["stop_too_wide", "rr_below_minimum"], True),
    (["stop_too_wide", "trend_strength_below_required"], False),
    (["entry_detached_from_price"], False),
    ("stop_too_wide", True),
    ("consecutive_losses", False),
    ([], False),
    (None, False),
])
def test_only_the_two_family_gates_may_be_overturned(rules, expected):
    assert lane.global_rejection_is_overridable(rules) is expected


# ───────────────────────────── ledger isolation ─────────────────────────────


def test_a_lane_fill_does_not_touch_the_real_books_balance():
    """Spec §8: real-book balance unchanged after a lane fill."""
    real = PaperBroker(starting_balance=1_500.0)
    ledger = lane.LEDGERS["wide_stop_4k"]
    lane_broker = PaperBroker(
        starting_balance=ledger.starting_balance,
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": lane.MARKETABLE_TICKS},
        slippage_ticks=1.0,
    )
    entry = 20_000.0
    order = BracketOrder(
        instrument="MNQ", direction="LONG", entry=entry,
        stop=entry - 254 * TICK, target=entry + 356 * TICK,
        rr_ratio=1.4, strategy=FOUR_HR, contracts=lane.CONTRACTS,
    )
    fill = lane_broker.execute_bracket(order, market_price=entry)
    assert fill.result == "OPEN"
    assert real.get_account_balance() == 1_500.0
    assert lane_broker.get_account_balance() == ledger.starting_balance
    assert real.get_position() is None


def test_the_two_ledgers_do_not_share_a_journal_root(tmp_path):
    four = lane.journal_dir(tmp_path, lane.LEDGERS["wide_stop_4k"])
    six = lane.journal_dir(tmp_path, lane.LEDGERS["wide_stop_6k"])
    assert four != six
    assert four.parent == six.parent == tmp_path / lane.JOURNAL_ROOT
    # Never the real book's journal root.
    assert four.parent != tmp_path


def test_ledger_worst_case_matches_the_amended_cap():
    assert lane.LEDGERS["wide_stop_4k"].worst_case_stop_dollars == 150.0
    assert lane.LEDGERS["wide_stop_6k"].worst_case_stop_dollars == 300.0


def test_daily_floor_is_two_times_worst_case():
    """D2, the approved variant: one max-loss must not halt the day."""
    for ledger in lane.LEDGERS.values():
        assert ledger.daily_loss_limit == 2 * ledger.worst_case_stop_dollars


# ───────────────────────────── journal labeling ─────────────────────────────


def test_every_audit_row_is_labeled_hypothetical():
    decision = lane.evaluate(type("C", (), {
        "wide_stop_ledger_mode": "paper_sim",
        "wide_stop_ledger_epoch_start": "2026-09-08T00:00:00+00:00",
    })())
    audit = decision.audit(instrument="MNQ", strategy=FOUR_HR, stop_ticks=254.0, rr_ratio=1.4)
    assert audit["label"] == "hypothetical_ledger"
    assert audit["hypothetical"] is True
    assert audit["promotion_path"] is False
    assert audit["ledger"] == "wide_stop_4k"
    assert audit["ledger_starting_balance"] == 4_000.0
    assert audit["role"] == "fill_eligible"
    assert audit["contracts"] == 1
    assert audit["dynamic_sizing_diagnostic_only"] is True
    assert audit["admissible_under_family_caps"] is True


def test_audit_marks_an_over_cap_candidate_inadmissible():
    decision = lane.evaluate(type("C", (), {"wide_stop_ledger_mode": "paper_sim"})())
    audit = decision.audit(instrument="MNQ", strategy=THREE_TWO_TWO, stop_ticks=700.0, rr_ratio=1.0)
    assert audit["admissible_under_family_caps"] is False


def test_audit_of_a_non_member_carries_no_ledger():
    decision = lane.evaluate(type("C", (), {"wide_stop_ledger_mode": "paper_sim"})())
    audit = decision.audit(instrument="MNQ", strategy="orb_breakout", stop_ticks=40.0, rr_ratio=2.0)
    assert audit["ledger"] is None and audit["role"] is None
    assert audit["label"] == "hypothetical_ledger"


def test_observe_only_audit_says_the_real_book_is_unchanged():
    decision = lane.evaluate(type("C", (), {"wide_stop_ledger_mode": "observe_only"})())
    audit = decision.audit(instrument="MNQ", strategy=FOUR_HR)
    assert audit["active"] is False
    assert "real book unchanged" in audit["reason"]


# ─────────────── look-ahead check on the fill reference (spec §8) ───────────


def test_the_lane_entry_reference_must_be_decision_time():
    """Spec §3 names `assert_decision_time_reference` as the lane's offline
    check, and §8 requires it as a regression proof. The lane does not
    reimplement it — this pins that the named check exists and still refuses
    the arrival-close reference the 2026-09-07 reconciliation caught."""
    from datetime import datetime, timedelta

    from scripts.vwap_hold_evidence_package import (
        LookaheadError,
        assert_decision_time_reference,
        ioc_fill,
    )

    decision = datetime(2026, 9, 8, 14, 30)
    bars = [
        {"ts": decision + timedelta(minutes=5 * i),
         "open": 20_000.0, "high": 20_002.0, "low": 19_998.0, "close": 20_001.0}
        for i in range(4)
    ]
    arm = {"armed_at": decision, "direction": "LONG", "entry": 20_000.0}

    # The arrival bar's OPEN is knowable at the decision; its CLOSE is not.
    assert_decision_time_reference(ioc_fill(arm, bars, "open"))
    with pytest.raises(LookaheadError, match="after the decision"):
        assert_decision_time_reference(ioc_fill(arm, bars, "close"))
