"""Focused Pine-parity tests for the shared replay ORB status helper."""

from __future__ import annotations

import pytest

from scripts import csv_to_replay, polygon_to_replay


@pytest.mark.parametrize(
    ("previous_close", "close", "expected"),
    [
        (95.0, 101.0, "reclaimed_high"),
        (95.0, 89.0, "reclaimed_low"),
        (101.0, 100.0, "rejected_high"),
        (89.0, 90.0, "rejected_low"),
        (101.0, 102.0, "above"),
        (89.0, 88.0, "below"),
        (95.0, 95.0, "inside"),
    ],
)
def test_shared_orb_status_vocabulary(previous_close, close, expected):
    assert (
        csv_to_replay.derive_orb_status(
            close=close,
            orb_high=100.0,
            orb_low=90.0,
            previous_close=previous_close,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("previous_close", "close", "expected"),
    [
        # Reclaim previous-side predicates include equality; current close is strict.
        (100.0, 101.0, "reclaimed_high"),
        (90.0, 89.0, "reclaimed_low"),
        (100.0, 100.0, "inside"),
        (90.0, 90.0, "inside"),
        # Rejection previous-side predicates are strict; current close includes equality.
        (101.0, 100.0, "rejected_high"),
        (89.0, 90.0, "rejected_low"),
        (100.0, 99.0, "inside"),
        (90.0, 91.0, "inside"),
    ],
)
def test_exact_boundary_behavior_matches_pine(previous_close, close, expected):
    assert (
        csv_to_replay.derive_orb_status(
            close=close,
            orb_high=100.0,
            orb_low=90.0,
            previous_close=previous_close,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("close", "expected"),
    [
        (101.0, "above"),
        (89.0, "below"),
        (100.0, "inside"),
        (90.0, "inside"),
    ],
)
def test_missing_previous_close_falls_back_to_location(close, expected):
    assert (
        csv_to_replay.derive_orb_status(
            close=close,
            orb_high=100.0,
            orb_low=90.0,
            previous_close=None,
        )
        == expected
    )


def test_polygon_generator_inherits_shared_helper_without_duplication():
    assert polygon_to_replay.derive_orb_status is csv_to_replay.derive_orb_status
