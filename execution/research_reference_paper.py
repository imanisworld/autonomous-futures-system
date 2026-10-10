"""Opt-in, offline-only market-reference fills without mutating audited PaperBroker.

The canonical PaperBroker implementation is hash-pinned by an independent
execution audit. Reuse its existing force_market_entry pathway instead of
changing its live/paper behavior or the historical baseline implementation.
"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite

from execution.broker_interface import BracketOrder, Fill
from execution.paper_broker import PaperBroker


class ResearchReferencePaperBroker(PaperBroker):
    """Research-only real-reference market fill; no broker connections.

    Callers must provide a causal market_price; historical replay must pass
    the NEXT bar's open. No planned-entry fallback is permitted.
    """

    def __init__(self, *, entry_fill_model: str = "market_at_reference", **kwargs):
        if entry_fill_model != "market_at_reference":
            raise ValueError("ResearchReferencePaperBroker requires market_at_reference")
        # The audited broker's force_market_entry path already fills at
        # market_price plus adverse slippage and validates bracket geometry.
        super().__init__(entry_fill_model="market", **kwargs)

    def execute_bracket(
        self,
        order: BracketOrder,
        market_price: float | None = None,
        *,
        paper_order_id: str | None = None,
    ) -> Fill:
        try:
            reference = float(market_price)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("market_at_reference requires a valid market_price") from exc
        if not isfinite(reference) or reference <= 0:
            raise ValueError("market_at_reference requires a positive finite market_price")
        if order.direction not in ("LONG", "SHORT"):
            raise ValueError("market_at_reference requires LONG or SHORT direction")
        # Never weaken the caller's own post-fill validation preference.
        # Strict research fills always validate ACTUAL risk/R:R after slippage.
        strict_order = replace(
            order,
            force_market_entry=True,
            post_fill_validation_required=True,
        )
        return super().execute_bracket(
            strict_order, market_price=reference, paper_order_id=paper_order_id
        )
