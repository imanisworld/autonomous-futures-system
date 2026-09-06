"""
tests/test_execution_safety.py

Execution-safety regression tests for schedule modes. These lock the invariants
that keep the always-on / shadow work from ever placing an order it shouldn't:

  * always_on_shadow NEVER places an order (any session, paper or live).
  * always_on_paper places PAPER orders only, and only for paper_eligible_sessions.
  * live execution may run ONLY the "current" schedule.
  * the read-only shadow generator touches no broker and only ever produces a
    candidate whose mode is non-executable.
"""
from __future__ import annotations

import dataclasses

import pytest

from config.settings import SystemConfig, _validate_config, ConfigError
from adaptive.execution_gate import order_placement_allowed
from adaptive.shadow_runner import evaluate_with_shadow
from risk.risk_engine import DailyState

PAPER = ["asian", "london", "new_york"]


# ── order_placement_allowed: the chokepoint ──────────────────────────────────

def test_current_mode_allows_orders():
    ok, _ = order_placement_allowed(
        schedule_mode="current", session="new_york",
        live_trading_enabled=False, paper_eligible_sessions=PAPER)
    assert ok is True


@pytest.mark.parametrize("session", ["new_york", "asian", "london", "session_gap", "off_hours"])
def test_always_on_shadow_never_places_orders(session):
    ok, reason = order_placement_allowed(
        schedule_mode="always_on_shadow", session=session,
        live_trading_enabled=False, paper_eligible_sessions=PAPER)
    assert ok is False
    assert "read-only" in reason


@pytest.mark.parametrize("session", PAPER)
def test_always_on_paper_allows_eligible_sessions(session):
    ok, _ = order_placement_allowed(
        schedule_mode="always_on_paper", session=session,
        live_trading_enabled=False, paper_eligible_sessions=PAPER)
    assert ok is True


@pytest.mark.parametrize("session", ["session_gap", "off_hours"])
def test_always_on_paper_blocks_shadow_only_sessions(session):
    ok, reason = order_placement_allowed(
        schedule_mode="always_on_paper", session=session,
        live_trading_enabled=False, paper_eligible_sessions=PAPER)
    assert ok is False
    assert "shadow-only" in reason


@pytest.mark.parametrize("mode", ["always_on_shadow", "always_on_paper"])
def test_live_execution_forbids_always_on(mode):
    ok, reason = order_placement_allowed(
        schedule_mode=mode, session="new_york",
        live_trading_enabled=True, paper_eligible_sessions=PAPER)
    assert ok is False
    assert "live" in reason.lower()


def test_live_execution_allows_only_current():
    ok, _ = order_placement_allowed(
        schedule_mode="current", session="new_york",
        live_trading_enabled=True, paper_eligible_sessions=PAPER)
    assert ok is True


def test_unknown_mode_is_denied():
    ok, _ = order_placement_allowed(
        schedule_mode="turbo", session="new_york",
        live_trading_enabled=False, paper_eligible_sessions=PAPER)
    assert ok is False


# ── demo_execution_hold_sessions: operator morning-window hold (2026-07-16) ──

HOLD = ["asian", "london"]


@pytest.mark.parametrize("session", ["asian", "london"])
def test_demo_hold_blocks_external_broker_in_held_session(session):
    ok, reason = order_placement_allowed(
        schedule_mode="current", session=session,
        live_trading_enabled=False, paper_eligible_sessions=PAPER,
        demo_execution_hold_sessions=HOLD, broker_is_paper=False)
    assert ok is False
    assert "demo_execution_hold" in reason


@pytest.mark.parametrize("session", ["asian", "london"])
def test_demo_hold_exempts_paper_broker_routes(session):
    # Proof/paper lanes must keep collecting evidence during a hold.
    ok, _ = order_placement_allowed(
        schedule_mode="current", session=session,
        live_trading_enabled=False, paper_eligible_sessions=PAPER,
        demo_execution_hold_sessions=HOLD, broker_is_paper=True)
    assert ok is True


def test_demo_hold_leaves_other_sessions_open():
    ok, _ = order_placement_allowed(
        schedule_mode="current", session="new_york",
        live_trading_enabled=False, paper_eligible_sessions=PAPER,
        demo_execution_hold_sessions=HOLD, broker_is_paper=False)
    assert ok is True


def test_demo_hold_empty_default_changes_nothing():
    ok, _ = order_placement_allowed(
        schedule_mode="current", session="asian",
        live_trading_enabled=False, paper_eligible_sessions=PAPER,
        demo_execution_hold_sessions=[], broker_is_paper=False)
    assert ok is True


def test_demo_hold_blocks_even_live_execution():
    # A hold is a safety pause: it must not be bypassable by ANY mode, live
    # included (live is otherwise allowed under "current").
    ok, reason = order_placement_allowed(
        schedule_mode="current", session="asian",
        live_trading_enabled=True, paper_eligible_sessions=PAPER,
        demo_execution_hold_sessions=HOLD, broker_is_paper=False)
    assert ok is False
    assert "demo_execution_hold" in reason


def test_demo_hold_normalizes_case_and_whitespace():
    ok, _ = order_placement_allowed(
        schedule_mode="current", session="asian",
        live_trading_enabled=False, paper_eligible_sessions=PAPER,
        demo_execution_hold_sessions=[" Asian "], broker_is_paper=False)
    assert ok is False


def test_config_rejects_unknown_demo_hold_session(config):
    # A typo'd hold session would silently fail OPEN — must be a hard error.
    bad = dataclasses.replace(
        config, demo_execution_hold_sessions=["asain"],
        max_staleness_seconds=300)
    with pytest.raises(ConfigError):
        _validate_config(bad)


def test_config_accepts_valid_demo_hold_sessions(config):
    # fixture allows ["london", "new_york"] — hold must be a subset of allowed
    good = dataclasses.replace(
        config, demo_execution_hold_sessions=["london"],
        max_staleness_seconds=300)
    _validate_config(good)


def test_demo_hold_env_parsing(monkeypatch):
    from config.settings import load_config
    monkeypatch.setenv("DEMO_EXECUTION_HOLD_SESSIONS", " Asian , london ,")
    cfg = load_config()
    assert cfg.demo_execution_hold_sessions == ["asian", "london"]


def test_demo_hold_env_absent_means_no_hold(monkeypatch):
    from config.settings import load_config
    monkeypatch.delenv("DEMO_EXECUTION_HOLD_SESSIONS", raising=False)
    cfg = load_config()
    assert cfg.demo_execution_hold_sessions == []


# ── Config-level safety (belt-and-suspenders) ────────────────────────────────

@pytest.mark.parametrize("mode", ["always_on_shadow", "always_on_paper"])
def test_config_rejects_any_always_on_when_live(config, mode):
    # BOTH always-on modes must be rejected when live trading is enabled.
    bad = dataclasses.replace(
        config, schedule_mode=mode, live_trading_enabled=True,
        max_staleness_seconds=300)
    with pytest.raises(ConfigError):
        _validate_config(bad)


# ── Shadow generator is read-only ────────────────────────────────────────────

def test_shadow_generator_emits_non_executable_candidate(config, fresh_market_state, monkeypatch):
    """The generator must never place an order, and any candidate it emits is
    produced under a shadow mode that order_placement_allowed refuses."""
    import execution.paper_broker as pb

    def _boom(*a, **k):
        raise AssertionError("shadow generation must NOT place an order")

    # Trip-wire: if the shadow path ever instantiates/sends a broker order, fail.
    if hasattr(pb.PaperBroker, "execute_bracket"):
        monkeypatch.setattr(pb.PaperBroker, "execute_bracket", _boom, raising=False)

    cfg = dataclasses.replace(config, allowed_sessions=["london"])  # new_york disallowed
    cand = evaluate_with_shadow(fresh_market_state, DailyState(), cfg)
    assert cand is not None  # new_york setup was schedule-blocked in current
    # The candidate would only ever run under a shadow mode → never executes.
    ok, _ = order_placement_allowed(
        schedule_mode="always_on_shadow", session=cand.session,
        live_trading_enabled=False, paper_eligible_sessions=PAPER)
    assert ok is False


# ── #1: the gate is actually WIRED into the runner ───────────────────────────

def test_runner_gate_suppresses_orders_in_shadow_mode(tmp_path):
    """End-to-end: the same tradeable bar places an order in 'current' but is
    suppressed (no fill) in always_on_shadow — proving the gate is a real
    chokepoint in process_alert, not just a standalone function."""
    from datetime import date
    from tests.conftest import load_permissive_config
    from webhook.runner import process_alert
    import sys
    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    # Explicit permissive universe: these are general execution-safety
    # proofs, not assertions about the shipped isolated-lane config.
    cfg = load_permissive_config(max_staleness_seconds=10 ** 9)
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)

    base = process_alert(payload, config=cfg, log_dir=str(tmp_path / "cur"), for_date=fd)
    assert base["decision"] == "TRADE" and base.get("fill"), base  # sanity baseline

    scfg = dataclasses.replace(cfg, schedule_mode="always_on_shadow")
    shadow = process_alert(payload, config=scfg, log_dir=str(tmp_path / "shad"), for_date=fd)
    assert shadow["decision"] == "SHADOW_NO_ORDER", shadow
    assert not shadow.get("fill")


def test_runner_demo_hold_blocks_external_but_not_paper(tmp_path, monkeypatch):
    """End-to-end wiring proof for the operator session hold: the same
    tradeable bar in a held session (a) still fills on the PaperBroker route
    (proof/paper lanes keep collecting evidence) but (b) is suppressed before
    placement on an external-broker route — the broker's execute_bracket is a
    trip-wire that must never fire."""
    from datetime import date
    from tests.conftest import load_permissive_config
    from webhook.runner import process_alert
    import webhook.runner as runner_mod
    import sys
    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")  # new_york
    fd = date(2026, 5, 23)
    held = load_permissive_config(
        max_staleness_seconds=10 ** 9,
        demo_execution_hold_sessions=["new_york"],
    )

    # (a) PaperBroker route (paper_mode=True): hold must NOT apply.
    paper = process_alert(
        payload, config=held, log_dir=str(tmp_path / "paper"), for_date=fd)
    assert paper["decision"] == "TRADE" and paper.get("fill"), paper

    # (b) External-broker route: same bar, hold must suppress pre-placement.
    class _ExternalBrokerTripwire:
        is_live = False

        # Benign pre-gate reads are fine; only placement must never happen.
        def get_account_balance(self, *a, **k):
            return 1500.0

        def __getattr__(self, name):  # any order/fill call = wiring failure
            raise AssertionError(
                f"external broker must not be touched under hold (got .{name})")

    ext_cfg = dataclasses.replace(held, paper_mode=False)
    monkeypatch.setattr(
        runner_mod, "_make_broker",
        lambda *a, **k: _ExternalBrokerTripwire())
    ext = process_alert(
        payload, config=ext_cfg, log_dir=str(tmp_path / "ext"), for_date=fd)
    assert ext["decision"] == "SHADOW_NO_ORDER", ext
    assert "demo_execution_hold" in (ext.get("gate_reason") or "")
    assert not ext.get("fill")


# ── #2: a risk-blocked shadow candidate is RISK_REJECTED, not SETUP_BLOCKED ───

def test_risk_blocked_candidate_is_risk_rejected(config, fresh_market_state):
    from adaptive.shadow_runner import evaluate_with_shadow
    from adaptive.opportunity_tracker import RISK_REJECTED
    cfg = dataclasses.replace(config, allowed_sessions=["london"], max_daily_loss=150.0)
    ds = DailyState(realized_pnl_dollars=-500.0)  # over the daily loss cap
    cand = evaluate_with_shadow(fresh_market_state, ds, cfg)
    assert cand is not None
    assert cand.block_type == RISK_REJECTED       # not a clean schedule miss
    assert cand.risk_failed_rule == "max_daily_loss"


# ── #3: shadow risk validation includes confluence ───────────────────────────

def test_shadow_risk_includes_confluence(config, fresh_market_state):
    from adaptive.shadow_runner import evaluate_with_shadow
    from adaptive.opportunity_tracker import SETUP_BLOCKED
    # Require grade B. If confluence weren't scored into the risk setup, the
    # RiskEngine would reject on min_confluence_grade → RISK_REJECTED. A clean
    # SETUP_BLOCKED proves the grade was passed through.
    cfg = dataclasses.replace(config, allowed_sessions=["london"], min_confluence_grade="B")
    cand = evaluate_with_shadow(fresh_market_state, DailyState(), cfg)
    assert cand is not None
    assert cand.risk_failed_rule != "min_confluence_grade"
    assert cand.block_type == SETUP_BLOCKED


# ── Phantom-open prevention: execution failure must clear the journal open ────

def test_execution_failure_clears_phantom_open(tmp_path, monkeypatch):
    """A TRADE decision whose execution returns non-OPEN (reject / no-fill /
    naked-flatten) must NOT leave a phantom open in the journal. A CANCELLED
    outcome is booked immediately so the NEXT bar sees the instrument FLAT,
    instead of blocked for ~20 min until the reconciler sweeps it. Regression for
    the 2026-06-19 limit-entry no-fill phantom churn."""
    from datetime import date
    from tests.conftest import load_permissive_config
    from webhook.runner import process_alert
    from journal.journal_logger import JournalLogger
    from execution.broker_interface import Fill
    import execution.paper_broker as pb
    import sys
    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    def _cancelled(self, order):
        return Fill(instrument=order.instrument, direction=order.direction,
                    contracts=order.contracts, entry_price=order.entry,
                    exit_price=None, exit_reason="ENTRY_NOT_FILLED",
                    result="CANCELLED", pnl_ticks=None, pnl_dollars=None)
    monkeypatch.setattr(pb.PaperBroker, "execute_bracket", _cancelled, raising=False)

    # Explicit permissive universe: these are general execution-safety
    # proofs, not assertions about the shipped isolated-lane config.
    cfg = load_permissive_config(max_staleness_seconds=10 ** 9)
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)
    log_dir = str(tmp_path / "j")

    res = process_alert(payload, config=cfg, log_dir=log_dir, for_date=fd)
    assert res["decision"] == "BLOCKED_EXECUTION_FAILED", res

    # No phantom: the journal must read FLAT, and the failed attempt is un-counted.
    ds = JournalLogger(log_dir=log_dir).get_daily_state(fd)
    assert ds.has_open_position is False, "phantom open left in journal"
    assert ds.trade_count == 0, "failed attempt should not consume a trade slot"


def test_cancelled_outcome_carries_no_fill_taxonomy_fields(tmp_path, monkeypatch):
    """The no-fill cause taxonomy must reach the journal end-to-end: runner.py
    passes fill.no_fill_reason/order_type through, and the ORIGINAL exit_reason
    format ("execution_failed:CANCELLED") is UNCHANGED so ops/fill_realism.py
    and ops/proof_30_mnq.py's existing substring/equality matching still works
    (logging-only addition, no metric drift)."""
    import json
    from datetime import date
    from tests.conftest import load_permissive_config
    from webhook.runner import process_alert
    from execution.broker_interface import Fill
    import execution.paper_broker as pb
    import sys
    sys.path.insert(0, "tests")
    from test_e2e_scenarios import _base_payload

    def _cancelled(self, order):
        return Fill(instrument=order.instrument, direction=order.direction,
                    contracts=order.contracts, entry_price=order.entry,
                    exit_price=None, exit_reason="ENTRY_NOT_FILLED",
                    result="CANCELLED", pnl_ticks=None, pnl_dollars=None,
                    no_fill_reason="NO_FILL_LIMIT_TOO_PASSIVE", order_type="Limit")
    monkeypatch.setattr(pb.PaperBroker, "execute_bracket", _cancelled, raising=False)

    # Explicit permissive universe: these are general execution-safety
    # proofs, not assertions about the shipped isolated-lane config.
    cfg = load_permissive_config(max_staleness_seconds=10 ** 9)
    payload = _base_payload(timestamp="2026-05-23T14:30:00+00:00")
    fd = date(2026, 5, 23)
    log_dir = str(tmp_path / "j")

    res = process_alert(payload, config=cfg, log_dir=log_dir, for_date=fd)
    assert res["decision"] == "BLOCKED_EXECUTION_FAILED", res

    lines = (tmp_path / "j" / f"journal_{fd.isoformat()}.jsonl").read_text().splitlines()
    outcomes = [json.loads(l)["outcome"] for l in lines if json.loads(l).get("type") == "OUTCOME"]
    assert outcomes, "expected an OUTCOME entry"
    outcome = outcomes[-1]
    assert outcome["exit_reason"] == "execution_failed:CANCELLED"  # unchanged, existing consumers rely on this
    assert outcome["no_fill_reason"] == "NO_FILL_LIMIT_TOO_PASSIVE"
    assert outcome["order_type"] == "Limit"
    assert outcome["broker_status_raw"] == "ENTRY_NOT_FILLED"
    assert outcome["strategy"]
    assert outcome["signal_timestamp"] is not None
    assert outcome["submit_timestamp"] is not None
    assert outcome["cancel_timestamp"] is not None
    assert outcome["seconds_until_cancel"] is not None and outcome["seconds_until_cancel"] >= 0
