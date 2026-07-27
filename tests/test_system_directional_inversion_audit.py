import json
from pathlib import Path

from execution.broker_interface import BracketOrder
from research.system_directional_inversion_audit import _mirror


REPO = Path(__file__).resolve().parents[1]


def _order(direction: str, entry: float, stop: float, target: float) -> BracketOrder:
    return BracketOrder(
        instrument="MNQ",
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr_ratio=2.5,
        strategy="orb_reclaim",
    )


def test_mirror_preserves_distances_and_reverses_direction():
    long_inverse = _mirror(_order("LONG", 100.0, 96.0, 110.0))
    assert (long_inverse.direction, long_inverse.stop, long_inverse.target) == (
        "SHORT",
        104.0,
        90.0,
    )

    short_inverse = _mirror(_order("SHORT", 100.0, 104.0, 90.0))
    assert (short_inverse.direction, short_inverse.stop, short_inverse.target) == (
        "LONG",
        96.0,
        110.0,
    )


def test_frozen_results_reconcile_and_lane_b_gross_exactly_negates():
    results = json.loads(
        (REPO / "scripts/system_directional_inversion_results.json").read_text()
    )
    for fields in results["original_reconciliation"].values():
        assert all(actual == expected for actual, expected in fields.values())
    assert results["lane_b"]["gross_reconciliation_error"] == 0.0
    assert results["lane_b"]["original"]["overall"]["trades"] == 490
    assert results["lane_b"]["inverse"]["overall"]["trades"] == 490
