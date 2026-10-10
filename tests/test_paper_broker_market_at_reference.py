"""Strict market-reference fills must never silently use planned entries.

No broker connection. These tests protect the opt-in research entry model;
the legacy market model remains available for reproducing frozen histories.
"""
from __future__ import annotations

import pytest

from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from execution.research_reference_paper import ResearchReferencePaperBroker


def _order(direction: str = "LONG", *, target: float | None = None) -> BracketOrder:
    is_long = direction == "LONG"
    return BracketOrder(
        instrument="MNQ", direction=direction, entry=100.0,
        stop=95.0 if is_long else 105.0,
        target=target if target is not None else (130.0 if is_long else 70.0),
        rr_ratio=2.0, strategy="research_reference_price",
        contracts=1,
    )


@pytest.mark.parametrize(
    ("direction", "reference", "expected_fill"),
    [("LONG", 104.0, 104.25), ("SHORT", 98.0, 97.75)],
)
def test_strict_market_uses_supplied_price_not_planned_entry(
    direction: str, reference: float, expected_fill: float
) -> None:
    broker = ResearchReferencePaperBroker(
        entry_fill_model="market_at_reference",
        slippage_ticks=1.0,
    )
    fill = broker.execute_bracket(_order(direction), market_price=reference)
    assert fill.result != "CANCELLED"
    assert fill.entry_price == pytest.approx(expected_fill)
    assert fill.entry_price != pytest.approx(100.0)
    assert broker.get_position() is not None


@pytest.mark.parametrize("missing", [None, 0.0, -2.0, float("nan"), float("inf")])
def test_strict_market_refuses_missing_or_invalid_reference(missing: float | None) -> None:
    broker = ResearchReferencePaperBroker(entry_fill_model="market_at_reference")
    with pytest.raises(ValueError, match="market_at_reference requires"):
        broker.execute_bracket(_order(), market_price=missing)
    assert broker.get_position() is None


def test_strict_market_refuses_reference_past_target() -> None:
    broker = ResearchReferencePaperBroker(entry_fill_model="market_at_reference")
    fill = broker.execute_bracket(_order(), market_price=131.0)
    assert fill.result == "CANCELLED"
    assert fill.exit_reason == "ENTRY_BRACKET_INVALID_AT_FILL"
    assert broker.get_position() is None


def test_legacy_model_still_reproduces_planned_fill() -> None:
    broker = PaperBroker(entry_fill_model="market", slippage_ticks=1.0)
    fill = broker.execute_bracket(_order(), market_price=104.0)
    assert fill.entry_price == pytest.approx(100.25)


def test_unknown_model_still_rejected() -> None:
    with pytest.raises(ValueError, match="unknown entry_fill_model"):
        PaperBroker(entry_fill_model="unproved_guess")


def test_research_resolve_position_never_calls_after_exit(monkeypatch) -> None:
    from execution import paper_mirror_hook

    exit_calls: list[object] = []
    monkeypatch.setattr(
        paper_mirror_hook,
        "after_exit",
        lambda fill, **kwargs: exit_calls.append(fill),
    )
    broker = ResearchReferencePaperBroker(entry_fill_model="market_at_reference")
    fill = broker.execute_bracket(_order(), market_price=100.0)
    assert fill.result != "CANCELLED"
    exit_calls.clear()
    outcome = broker.resolve_position(NextBarOHLC(open=100.0, high=135.0, low=90.0))
    assert outcome is not None
    assert exit_calls == []


def test_research_force_resolve_never_calls_after_exit(monkeypatch) -> None:
    from execution import paper_mirror_hook

    exit_calls: list[object] = []
    monkeypatch.setattr(
        paper_mirror_hook,
        "after_exit",
        lambda fill, **kwargs: exit_calls.append(fill),
    )
    broker = ResearchReferencePaperBroker(entry_fill_model="market_at_reference")
    fill = broker.execute_bracket(_order(), market_price=100.0)
    assert fill.result != "CANCELLED"
    exit_calls.clear()
    outcome = broker.force_resolve("WIN", 130.0)
    assert outcome is not None
    assert exit_calls == []


def test_paper_broker_resolve_still_invokes_after_exit(monkeypatch) -> None:
    from execution import paper_mirror_hook

    exit_calls: list[object] = []
    monkeypatch.setattr(
        paper_mirror_hook,
        "after_exit",
        lambda fill, **kwargs: exit_calls.append(fill),
    )
    broker = PaperBroker(entry_fill_model="market")
    fill = broker.execute_bracket(_order(), market_price=100.0)
    assert fill.result != "CANCELLED"
    exit_calls.clear()
    outcome = broker.resolve_position(NextBarOHLC(open=100.0, high=135.0, low=90.0))
    assert outcome is not None
    assert len(exit_calls) == 1


@pytest.mark.parametrize("requested_optimism", [None, False, True])
def test_strict_intrabar_ambiguity_always_books_stop_first(requested_optimism):
    options = {} if requested_optimism is None else {
        "pessimistic_both_hit": requested_optimism
    }
    broker = ResearchReferencePaperBroker(
        entry_fill_model="market_at_reference", **options
    )
    initial = broker.execute_bracket(_order(), market_price=100.0)
    assert initial.result == "OPEN"
    # Both levels traded during one five-minute candle: its OHLC alone
    # cannot establish intrabar order. Stop-first prevents lookahead wins.
    outcome = broker.resolve_position(NextBarOHLC(open=100.0, high=135.0, low=90.0))
    assert outcome is not None and outcome.result == "LOSS"
    assert outcome.exit_reason == "STOP_HIT"
