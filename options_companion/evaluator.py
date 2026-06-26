"""Orchestration: futures fill -> Signa gate -> map -> select -> risk -> ledger row.

Pure, fail-soft, and isolated. ``evaluate_companion`` never mutates futures state,
the futures journal, or daily counts — it only writes to the companion ledger and
returns an audit summary. The runner hook wraps the call in try/except so a companion
error can never affect the futures result.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from context.market_context import MarketState
from risk.options_risk_engine import (
    OptionsRiskConfig,
    OptionsRiskEngine,
    OptionTradePlan,
)

from .chain_provider import ChainProvider
from .mapping import map_companion_candidates
from .selection import CompanionSelection, SelectionRejected, select_contract
from .signa_gate import evaluate_companion_signa
from .store import OptionsCompanionStore


@dataclass(frozen=True)
class CompanionConfig:
    """Companion-lane knobs, separate from the advisory scanner's OptionsRiskConfig."""

    allowed_underlyings: list[str] = field(default_factory=lambda: ["SPY", "QQQ"])
    max_daily_trades: int = 3
    max_open_positions_per_underlying: int = 1
    max_premium_per_contract: float = 400.0
    max_total_premium: float = 400.0
    premium_caps_by_underlying: dict[str, float] = field(default_factory=dict)
    min_rr_ratio: float = 2.0
    require_confluence_grade: str = "B"
    max_dte: int = 2
    max_spread_ratio: float = 0.25

    def risk_config(self, session: str) -> OptionsRiskConfig:
        return OptionsRiskConfig(
            enabled=True,
            paper_only=True,
            allowed_underlyings=[u.upper() for u in self.allowed_underlyings],
            allowed_contract_types=["CALL", "PUT"],
            allowed_sessions=[session],  # companion follows the futures session
            session_windows={},
            max_contracts=1,
            max_premium_per_contract=self.max_premium_per_contract,
            max_total_premium=self.max_total_premium,
            premium_caps_by_underlying=dict(self.premium_caps_by_underlying),
            max_daily_trades=self.max_daily_trades,
            max_daily_loss=0.0,  # paper lane: don't halt on paper drawdown
            max_consecutive_losses=0,
            max_open_positions=self.max_open_positions_per_underlying,
            require_entry=True,
            require_stop=True,
            require_target=True,
            min_rr_ratio=self.min_rr_ratio,
            allow_market_orders=False,
            require_confluence_grade=self.require_confluence_grade,
        )


async def evaluate_companion(
    *,
    state: MarketState,
    futures_instrument: str,
    futures_direction: str,
    provider: ChainProvider,
    store: OptionsCompanionStore,
    config: Optional[CompanionConfig] = None,
    now: Optional[datetime] = None,
    futures_timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Form, risk-check, and ledger a companion paper options candidate.

    Returns an audit dict (``{"candidates": [...]}``). Records:
    - WATCHLIST rows for grade A/B Signa candidates whose daily direction is WAIT/neutral,
    - REJECTED rows for FORMED candidates that fail Signa / selection / risk,
    - exactly one OPEN row for an approved candidate.
    Produces NO row when mapping yields nothing.
    """
    cfg = config or CompanionConfig()
    now = now or datetime.now(timezone.utc)
    ts = futures_timestamp or (state.timestamp.isoformat() if state.timestamp else None)
    signa = state.signa

    audit: list[dict[str, Any]] = []

    candidates = map_companion_candidates(futures_instrument, futures_direction)
    if not candidates:
        return {"candidates": audit}

    # Signa gate is direction-level: one verdict applies to all candidates.
    signa_result = evaluate_companion_signa(state, futures_direction)

    engine = OptionsRiskEngine(cfg.risk_config(state.session))

    for underlying, contract_type in candidates:
        base = dict(
            futures_instrument=futures_instrument,
            futures_direction=futures_direction,
            futures_timestamp=ts,
            underlying=underlying,
            contract_type=contract_type,
            signa_grade=(signa.grade if signa else None),
            signa_score=(signa.score if signa else None),
            signa_daily_direction=(signa.daily_direction if signa else None),
            created_at=now,
        )

        audit_ctx = {
            "underlying": underlying,
            "contract_type": contract_type,
            "futures_instrument": futures_instrument,
            "futures_direction": futures_direction,
        }

        if not signa_result.passed:
            if signa_result.watchlist:
                row_id = store.record(
                    status="WATCHLIST",
                    risk_result="WATCHLIST",
                    risk_failed_rule=signa_result.failed_rule,
                    **base,
                )
                audit.append({**audit_ctx, "status": "WATCHLIST", "rule": signa_result.failed_rule, "id": row_id})
                continue
            row_id = store.record(
                status="REJECTED",
                risk_result="REJECTED",
                risk_failed_rule=signa_result.failed_rule,
                **base,
            )
            audit.append({**audit_ctx, "status": "REJECTED", "rule": signa_result.failed_rule, "id": row_id})
            continue

        snapshot = await provider.fetch_chain(underlying, max_dte=cfg.max_dte)
        selection = select_contract(
            snapshot,
            contract_type,
            now=now,
            max_dte=cfg.max_dte,
            max_spread_ratio=cfg.max_spread_ratio,
        )
        if isinstance(selection, SelectionRejected):
            row_id = store.record(
                status="REJECTED",
                risk_result="REJECTED",
                risk_failed_rule=selection.failed_rule,
                **base,
            )
            audit.append({**audit_ctx, "status": "REJECTED", "rule": selection.failed_rule, "id": row_id})
            continue

        assert isinstance(selection, CompanionSelection)
        plan = OptionTradePlan(
            underlying=underlying,
            symbol=selection.option_symbol,
            contract_type=contract_type,
            side="BUY",
            quantity=1,
            entry_premium=selection.entry_mark,
            stop_premium=selection.stop_mark,
            target_premium=selection.target_mark,
            strategy="companion",
            session=state.session,
            timestamp=now,
            order_type="limit",
            confluence_grade=(signa.grade if signa else None),
        )
        daily_state = store.daily_state(underlying, now.astimezone(timezone.utc).date())
        risk = engine.validate(plan, daily_state, broker_is_live=False)

        sel_fields = dict(
            option_symbol=selection.option_symbol,
            expiry=selection.expiry,
            strike=selection.strike,
            dte=selection.dte,
            entry_mark=selection.entry_mark,
            stop_mark=selection.stop_mark,
            target_mark=selection.target_mark,
        )
        if not risk.approved:
            row_id = store.record(
                status="REJECTED",
                risk_result="REJECTED",
                risk_failed_rule=risk.failed_rule,
                **base,
                **sel_fields,
            )
            audit.append({**audit_ctx, "status": "REJECTED", "rule": risk.failed_rule, "id": row_id})
            continue

        row_id = store.record(
            status="OPEN",
            risk_result="APPROVED",
            risk_failed_rule=None,
            **base,
            **sel_fields,
        )
        audit.append(
            {
                **audit_ctx,
                "status": "OPEN",
                "option_symbol": selection.option_symbol,
                "strike": selection.strike,
                "expiry": selection.expiry,
                "dte": selection.dte,
                "entry_mark": selection.entry_mark,
                "stop_mark": selection.stop_mark,
                "target_mark": selection.target_mark,
                "id": row_id,
            }
        )

    return {"candidates": audit}


def run_companion_create(
    *,
    state: MarketState,
    futures_instrument: str,
    futures_direction: str,
    provider: ChainProvider,
    store: OptionsCompanionStore,
    config: Optional[CompanionConfig] = None,
    now: Optional[datetime] = None,
    futures_timestamp: Optional[str] = None,
) -> dict[str, Any]:
    """Sync bridge for the runner hook (which executes off the event loop)."""

    async def _run() -> dict[str, Any]:
        async with provider:  # type: ignore[union-attr]
            return await evaluate_companion(
                state=state,
                futures_instrument=futures_instrument,
                futures_direction=futures_direction,
                provider=provider,
                store=store,
                config=config,
                now=now,
                futures_timestamp=futures_timestamp,
            )

    try:
        result = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001 — surface to Discord, then re-raise for the runner
        from .notify import notify_companion_error

        notify_companion_error(f"create: {exc}")
        raise
    # Fail-soft Discord notifications (opens / opted-in rejections).
    from .notify import notify_companion_create as _notify

    _notify(result)
    return result
