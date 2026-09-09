"""Runtime isolation for the wide-stop hypothetical-ledger lane (part 2).

The load-bearing claim is that this lane cannot affect the real book. These
tests assert that directly: the real journal root stays empty, the real
balance is untouched, and the lane's own verdict never feeds back into the
caller's risk result.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone

import pytest

from config.settings import load_config

from context import wide_stop_ledger_paper as contract
from context.wide_stop_ledger_runtime import observe_candidate
from journal.journal_logger import JournalLogger
from risk.risk_engine import RiskResult, TradeSetup

TICK = 0.25
FOUR_HR = "strat_4hr_retrigger"
THREE_TWO_TWO = "strat_322_first_live"
MIYAGI = "strat_12hr_miyagi"
EPOCH = "2026-09-08T00:00:00+00:00"


def _Cfg(mode="paper_sim", epoch=EPOCH):
    """A real SystemConfig with the real book's binding constraints.

    A hand-rolled stub is not usable here: RiskEngine reads far more of the
    config than the lane does, and stubbing only the lane's fields would make
    these tests pass through the lane's own error handler rather than through
    the risk gates they are meant to exercise.
    """
    cfg = copy.copy(load_config())
    cfg.wide_stop_ledger_mode = mode
    cfg.wide_stop_ledger_epoch_start = epoch
    cfg.max_stop_ticks = {"MNQ": 120.0}
    cfg.min_rr_ratio = 2.0
    cfg.max_daily_loss = 150.0
    cfg.max_drawdown_percent = 0.20
    cfg.runner_mode = False
    cfg.schedule_mode = "always"
    return cfg


def _setup(strategy=FOUR_HR, stop_ticks=254.0, rr=1.4, entry=20_000.0) -> TradeSetup:
    return TradeSetup(
        direction="LONG", entry=entry, stop=entry - stop_ticks * TICK,
        target=entry + stop_ticks * TICK * rr, rr_ratio=rr, strategy=strategy,
        instrument="MNQ", session="new_york", contracts=1,
        entry_time=datetime.now(timezone.utc), confluence_grade="B",
    )


REJECTED_ON_CAP = RiskResult(result="REJECTED", failed_rule="stop_too_wide", reason="wide")
REJECTED_ON_TREND = RiskResult(
    result="REJECTED", failed_rule="trend_strength_below_required", reason="chop"
)
APPROVED = RiskResult(result="APPROVED")


def _observe(tmp_path, cfg=None, setup=None, result=REJECTED_ON_CAP, market_price=20_000.0):
    return observe_candidate(
        cfg=cfg or _Cfg(),
        setup=setup or _setup(),
        global_risk_result=result,
        log_dir=str(tmp_path),
        market_price=market_price,
    )


# ─────────────────────────── the common path is None ────────────────────────


def test_returns_none_when_the_lane_is_off(tmp_path):
    assert _observe(tmp_path, cfg=_Cfg(mode="observe_only")) is None


def test_returns_none_for_a_non_member(tmp_path):
    assert _observe(tmp_path, setup=_setup(strategy="orb_breakout")) is None


def test_returns_none_for_the_wrong_instrument(tmp_path):
    setup = _setup()
    object.__setattr__(setup, "instrument", "MES")
    assert _observe(tmp_path, setup=setup) is None


def test_nothing_is_written_when_the_lane_is_off(tmp_path):
    _observe(tmp_path, cfg=_Cfg(mode="observe_only"))
    assert not (tmp_path / contract.JOURNAL_ROOT).exists()


# ──────────────────────── isolation from the real book ──────────────────────


def test_the_real_journal_root_is_never_written(tmp_path):
    _observe(tmp_path)
    real = JournalLogger(log_dir=str(tmp_path))
    assert real.read_day() == []
    # Only the lane's own subtree exists.
    assert (tmp_path / contract.JOURNAL_ROOT / "wide_stop_4k").exists()


def test_each_ledger_writes_only_to_its_own_root(tmp_path):
    _observe(tmp_path, setup=_setup(FOUR_HR))
    _observe(tmp_path, setup=_setup(THREE_TWO_TWO, stop_ticks=472.0, rr=0.24))
    root = tmp_path / contract.JOURNAL_ROOT
    assert (root / "wide_stop_4k").exists() and (root / "wide_stop_6k").exists()
    four = JournalLogger(log_dir=str(root / "wide_stop_4k")).read_day()
    assert all(row["ledger"] == "wide_stop_4k" for row in four)


def test_every_journal_row_carries_the_hypothetical_label(tmp_path):
    _observe(tmp_path)
    rows = JournalLogger(
        log_dir=str(tmp_path / contract.JOURNAL_ROOT / "wide_stop_4k")
    ).read_day()
    assert rows and all(row["label"] == contract.LABEL for row in rows)
    assert all(row["decision"] == "HYPOTHETICAL_LEDGER" for row in rows)
    assert all(row["wide_stop_ledger"]["promotion_path"] is False for row in rows)


def test_the_caller_risk_result_is_not_mutated(tmp_path):
    result = RiskResult(result="REJECTED", failed_rule="stop_too_wide", reason="wide")
    _observe(tmp_path, result=result)
    assert result.result == "REJECTED"
    assert result.failed_rule == "stop_too_wide"
    assert result.approved is False


# ───────────────────── only the two family gates are overturned ─────────────


def test_a_cap_rejection_is_reconsidered_on_the_lane(tmp_path):
    audit = _observe(tmp_path, result=REJECTED_ON_CAP)
    assert audit["lane_may_consider"] is True
    assert audit["lane_result"] == "APPROVED"
    assert audit["ledger"] == "wide_stop_4k"


def test_a_trend_rejection_is_not_reconsidered(tmp_path):
    """Spec §3: anything other than the two family gates stays rejected."""
    audit = _observe(tmp_path, result=REJECTED_ON_TREND)
    assert audit["lane_may_consider"] is False
    assert audit["lane_result"] == "REJECTED_UPSTREAM"
    assert audit["lane_failed_rule"] == "trend_strength_below_required"
    assert "fill_status" not in audit


def test_an_approved_candidate_is_also_evaluated(tmp_path):
    audit = _observe(tmp_path, result=APPROVED)
    assert audit["global_result"] == "APPROVED"
    assert audit["lane_may_consider"] is True


# ──────────────────────────── fills and shadow role ─────────────────────────


def test_a_fill_eligible_member_fills_on_its_ledger(tmp_path):
    audit = _observe(tmp_path)
    assert audit["fill_status"] == "OPEN"
    assert audit["fill_price"] is not None
    assert audit["invalid_at_fill"] is False
    assert audit["ledger_balance"] == 4_000.0


def test_the_shadow_member_is_journaled_but_never_filled(tmp_path):
    """D5: Miyagi is a research detector; the lane cannot fill it."""
    audit = _observe(tmp_path, setup=_setup(MIYAGI, stop_ticks=522.0, rr=1.0))
    assert audit["role"] == "shadow_only"
    assert audit["lane_result"] == "SHADOW_ONLY_NOT_FILLED"
    assert "fill_status" not in audit


def test_an_invalid_at_fill_outcome_is_counted_not_blended(tmp_path):
    """§6: a fill landing outside its own bracket is its own line.

    #508 makes PaperBroker refuse it; the lane reports rather than hides it.
    """
    setup = _setup(stop_ticks=100.0, rr=1.4)      # stop 25 points below entry
    audit = _observe(tmp_path, setup=setup, market_price=19_950.0)  # 50 below
    assert audit["fill_status"] == "CANCELLED"
    assert audit["invalid_at_fill"] is True
    assert audit["fill_price"] is None


def test_no_market_price_is_recorded_rather_than_guessed(tmp_path):
    audit = _observe(tmp_path, market_price=None)
    assert audit["lane_result"] == "NO_MARKET_PRICE"


# ──────────────────────────────── failure modes ─────────────────────────────


def test_a_missing_epoch_is_reported_not_raised(tmp_path):
    """An active lane without a valid epoch must not break the real decision."""
    audit = _observe(tmp_path, cfg=_Cfg(epoch=None))
    assert audit["lane_result"] == "LANE_ERROR"
    assert "epoch" in audit["lane_error"]


def test_a_naive_epoch_is_refused(tmp_path):
    audit = _observe(tmp_path, cfg=_Cfg(epoch="2026-09-08T00:00:00"))
    assert audit["lane_result"] == "LANE_ERROR"


def test_the_lane_never_raises_into_the_caller(tmp_path):
    """Every path returns a dict or None — never an exception."""
    for cfg in (_Cfg(), _Cfg(epoch=None), _Cfg(epoch="nonsense")):
        for result in (APPROVED, REJECTED_ON_CAP, REJECTED_ON_TREND):
            out = observe_candidate(
                cfg=cfg, setup=_setup(), global_risk_result=result,
                log_dir=str(tmp_path), market_price=20_000.0,
            )
            assert out is None or isinstance(out, dict)


# ─────────────────────────── proof-critical pinning ─────────────────────────


def test_both_lane_variables_are_proof_critical():
    """§5: a release must pin them; an unpinned active override is a finding."""
    from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES

    assert "WIDE_STOP_LEDGER_MODE" in PROOF_CRITICAL_RUNTIME_OVERRIDES
    assert "WIDE_STOP_LEDGER_EPOCH_START" in PROOF_CRITICAL_RUNTIME_OVERRIDES


# ─────────────────── §6 build-step-1 offline expectation ────────────────────


@pytest.fixture(scope="module")
def expectation() -> dict:
    from scripts.wide_stop_ledger_offline_expectation import build_report

    return build_report()


def test_the_cells_reproduce_the_amended_caps_exactly(expectation):
    """32 admitted for 4HR at 300 ticks; 24 for 3-2-2 at 600 ticks."""
    four = expectation["ledgers"]["wide_stop_4k"]
    six = expectation["ledgers"]["wide_stop_6k"]
    assert four["cell_size"] == four["expected_cell_size"] == 32
    assert six["cell_size"] == six["expected_cell_size"] == 24
    assert four["cell_matches_memo"] and six["cell_matches_memo"]


def test_4hr_300_tick_ioc_expectation_is_pinned(expectation):
    four = expectation["ledgers"]["wide_stop_4k"]
    assert four["ioc_filled"] == 11
    assert four["ioc_unmarketable"] == 19
    assert four["invalid_at_fill"] == 2
    assert four["fill_rate"] == 0.344


def test_invalid_at_fill_is_reported_separately(expectation):
    """The §6 precondition: counted on its own line, never blended."""
    four = expectation["ledgers"]["wide_stop_4k"]
    assert four["invalid_at_fill"] == 2
    assert four["invalid_at_fill_dates"] == ["2026-01-08", "2026-04-06"]
    assert expectation["ledgers"]["wide_stop_6k"]["invalid_at_fill"] == 0


def test_the_expectation_states_what_it_does_not_establish(expectation):
    assert expectation["establishes"].startswith("entry-side")
    assert any("forward P&L" in item for item in expectation["does_not_establish"])
