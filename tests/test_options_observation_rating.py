"""Observation-rating telemetry tests for the Phase-1 options plan lane."""

from __future__ import annotations

import pytest

from options_manager.plans import (
    ObservationRatingSnapshot,
    PlanObservation,
    StructuralLevel,
    render_plan_update,
    update_trade_thesis,
)
from options_manager.storage import _snapshot_from_payload, _snapshot_to_payload


def _observation(*, observed_at: str, rating: ObservationRatingSnapshot) -> PlanObservation:
    return PlanObservation(
        ticker="AAPL",
        direction="CALL",
        setup_type="2-1-2 continuation",
        timeframe="30m",
        observed_at=observed_at,
        mechanical_triggered=True,
        entry_trigger=100.0,
        underlying_invalidation=98.0,
        levels=(
            StructuralLevel(price=101.0, side="RESISTANCE", source="PDH"),
            StructuralLevel(price=103.0, side="RESISTANCE", source="PWH"),
        ),
        contract_valid=True,
        portfolio_risk_valid=True,
        spy_qqq_aligned=True,
        htf_aligned=True,
        event_risk_clear=True,
        observation_rating=rating,
    )


def test_observation_rating_is_telemetry_not_trade_authority():
    first_rating = ObservationRatingSnapshot(
        rating=82.0,
        components=(
            ("strat_htf", 90.0),
            ("market_alignment", 85.0),
            ("signa", 70.0),
            ("gex", 75.0),
            ("level_quality", 88.0),
        ),
    )
    first = update_trade_thesis(
        None,
        _observation(
            observed_at="2026-10-01T10:00:00-04:00",
            rating=first_rating,
        ),
    )
    assert first.snapshot.actionable is True
    assert first.snapshot.observation_rating == first_rating
    assert first_rating.observation_only is True
    assert first_rating.trade_authority is False

    second_rating = ObservationRatingSnapshot(
        rating=41.0,
        components=(("strat_htf", 45.0), ("market_alignment", 40.0)),
    )
    second = update_trade_thesis(
        first.snapshot,
        _observation(
            observed_at="2026-10-01T10:05:00-04:00",
            rating=second_rating,
        ),
    )

    assert second.snapshot.actionable is True
    assert second.snapshot.observation_rating == second_rating
    assert second.material_reasons == ()
    assert second.should_emit_update is False
    assert second.telemetry_only is True


def test_observation_rating_validates_bounds_and_duplicate_components():
    with pytest.raises(ValueError, match="between 0 and 100"):
        update_trade_thesis(
            None,
            _observation(
                observed_at="2026-10-01T10:00:00-04:00",
                rating=ObservationRatingSnapshot(rating=101.0),
            ),
        )

    with pytest.raises(ValueError, match="duplicate observation component"):
        update_trade_thesis(
            None,
            _observation(
                observed_at="2026-10-01T10:00:00-04:00",
                rating=ObservationRatingSnapshot(
                    rating=80.0,
                    components=(("signa", 70.0), ("signa", 71.0)),
                ),
            ),
        )


def test_observation_rating_storage_round_trip_pins_authority_flags():
    update = update_trade_thesis(
        None,
        _observation(
            observed_at="2026-10-01T10:00:00-04:00",
            rating=ObservationRatingSnapshot(
                rating=77.5,
                components=(
                    ("strat_htf", 80.0),
                    ("level_quality", 75.0),
                    ("other_context", 60.0),
                ),
            ),
        ),
    )
    payload = _snapshot_to_payload(update.snapshot)
    stored = payload["observation_rating"]

    assert stored["rating"] == 77.5
    assert stored["observation_only"] is True
    assert stored["trade_authority"] is False
    assert _snapshot_from_payload(payload).observation_rating == update.snapshot.observation_rating

    tampered = dict(payload)
    tampered_rating = dict(stored)
    tampered_rating["trade_authority"] = True
    tampered["observation_rating"] = tampered_rating
    with pytest.raises(ValueError, match="must not have trade authority"):
        _snapshot_from_payload(tampered)


def test_renderer_labels_rating_observational_only():
    update = update_trade_thesis(
        None,
        _observation(
            observed_at="2026-10-01T10:00:00-04:00",
            rating=ObservationRatingSnapshot(
                rating=88.0,
                components=(("strat_htf", 90.0), ("level_quality", 86.0)),
            ),
        ),
    )
    rendered = render_plan_update(update)
    assert "Observation rating: 88.0/100" in rendered.body
    assert "OBSERVATIONAL ONLY; trade authority=NO" in rendered.body
    assert "strat_htf=90.0" in rendered.body
