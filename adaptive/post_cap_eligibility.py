"""Observation-only post-cap eligibility evaluator.

This module answers one narrow question for a setup seen after the executable
max-trades/day gate has already fired:

    Would this setup pass the normal local strategy/risk pipeline if the
    daily-trade-count gate alone were removed?

It NEVER submits an order, NEVER instantiates a broker, NEVER mutates config,
and NEVER mutates the caller's setup or DailyState. Account state comes from
the system journal ledger and is labeled as such; broker-balance parity is not
claimed.

The evaluator intentionally mirrors webhook.runner's post-decision ordering:
stop multiplier -> confluence -> journal-ledger sizing -> tick rounding ->
RiskEngine -> countertrend risk cap.
"""
from __future__ import annotations

import copy
from typing import Any

from config.futures_contracts import round_to_tick, symbol_economics
from risk.risk_engine import DailyState, RiskEngine, RiskResult, TradeSetup
from strategy.confluence_scorer import score_setup
from strategy.stop_sizing import apply_stop_multiplier


def evaluate_post_cap_eligibility(
    *,
    state: Any,
    setup: Any,
    cfg: Any,
    daily_state: DailyState,
    account_balance: float,
    account_peak_balance: float,
    skip_stop_multiplier: bool = False,
    force_one_contract: bool = False,
) -> dict:
    """Return an inert cap-only eligibility record.

    daily_state.trade_count must already be at or above the configured total
    daily capacity. The copy passed to RiskEngine has only trade_count reset
    to zero; every other reconstructed risk field is preserved.

    account_balance and account_peak_balance are explicitly the journal
    ledger values supplied by the caller. This keeps the observer broker-free.
    """
    max_trades = int(getattr(cfg, "max_trades_per_day", 0) or 0)
    bonus = int(getattr(cfg, "bonus_trades_after_max", 0) or 0)
    total_cap = max_trades + bonus
    if total_cap <= 0:
        raise ValueError("post-cap observer requires a positive daily capacity")
    if int(daily_state.trade_count) < total_cap:
        raise ValueError(
            f"post-cap observer called before capacity: "
            f"{daily_state.trade_count} < {total_cap}"
        )
    if setup is None:
        raise ValueError("post-cap observer requires a selected setup")

    observed_setup = copy.deepcopy(setup)
    multiplier = 1.0
    if not skip_stop_multiplier:
        multiplier = apply_stop_multiplier(
            observed_setup,
            state.instrument,
            getattr(cfg, "stop_multiplier_per_instrument", {}) or {},
        )

    confluence = score_setup(state, observed_setup)
    risk_engine = RiskEngine(config=cfg)
    recommended_contracts = risk_engine.recommended_contracts(
        state.instrument, account_balance
    )
    contracts = 1 if force_one_contract else recommended_contracts
    if getattr(observed_setup, "direction_role", None) == "COUNTERTREND_SCALP":
        contracts = 1

    entry_px = round_to_tick(observed_setup.entry, state.instrument)
    stop_px = round_to_tick(observed_setup.stop, state.instrument)
    target_px = round_to_tick(observed_setup.target, state.instrument)

    shadow_state = copy.deepcopy(daily_state)
    original_trade_count = int(shadow_state.trade_count)
    # Remove exactly the daily-count capacity gate. Search proves RiskEngine
    # reads DailyState.trade_count only in _check_daily_trade_limit.
    shadow_state.trade_count = 0
    shadow_state.account_balance = float(account_balance)
    shadow_state.account_peak_balance = float(account_peak_balance)

    trade_setup = TradeSetup(
        direction=observed_setup.direction,
        entry=entry_px,
        stop=stop_px,
        target=target_px,
        rr_ratio=observed_setup.rr_ratio,
        strategy=observed_setup.strategy,
        instrument=state.instrument,
        session=state.session,
        notes=observed_setup.notes,
        entry_time=observed_setup.entry_time or state.timestamp,
        contracts=contracts,
        confluence_grade=confluence.grade,
    )
    risk_result = risk_engine.validate(trade_setup, shadow_state)

    # Mirror webhook.runner's post-RiskEngine countertrend budget check.
    if risk_result.approved and getattr(observed_setup, "direction_role", None) == "COUNTERTREND_SCALP":
        tick_size, tick_value = symbol_economics(state.instrument)
        stop_ticks = abs(float(entry_px) - float(stop_px)) / tick_size
        planned_risk = stop_ticks * tick_value
        normal_budget = (
            float(account_balance or 0)
            * float(getattr(cfg, "max_account_risk_per_trade_percent", 1.0) or 1.0)
            / 100.0
        )
        countertrend_budget = normal_budget * 0.5
        if planned_risk > countertrend_budget:
            risk_result = RiskResult(
                result="REJECTED",
                failed_rule="countertrend_risk_cap",
                reason=(
                    f"Countertrend scalp risk USD {planned_risk:.2f} exceeds "
                    f"50% risk budget USD {countertrend_budget:.2f}; structural stop "
                    "is not tightened or widened."
                ),
            )

    return {
        "schema_version": 1,
        "observation_only": True,
        "execution_reachable": False,
        "daily_trade_limit_bypassed": True,
        "original_trade_count": original_trade_count,
        "configured_daily_capacity": total_cap,
        "account_balance_source": "journal_ledger",
        "broker_balance_parity_claimed": False,
        "account_balance": round(float(account_balance), 2),
        "account_peak_balance": round(float(account_peak_balance), 2),
        "stop_multiplier_applied": float(multiplier),
        "recommended_contracts": int(recommended_contracts),
        "shadow_contracts": int(contracts),
        "transformed_setup": {
            "direction": observed_setup.direction,
            "strategy": observed_setup.strategy,
            "entry": entry_px,
            "stop": stop_px,
            "target": target_px,
            "rr_ratio": observed_setup.rr_ratio,
            "direction_role": getattr(observed_setup, "direction_role", None),
        },
        "confluence": {
            "score": confluence.score,
            "grade": confluence.grade,
            "factors": list(confluence.factors),
            "penalties": list(confluence.penalties),
        },
        "risk_without_daily_cap": {
            "result": risk_result.result,
            "failed_rule": risk_result.failed_rule,
            "reason": risk_result.reason,
        },
        "eligible_except_daily_cap": bool(risk_result.approved),
    }
