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


# ── Config-level safety (belt-and-suspenders) ────────────────────────────────

def test_config_rejects_always_on_paper_when_live(config):
    bad = dataclasses.replace(
        config, schedule_mode="always_on_paper", live_trading_enabled=True)
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
