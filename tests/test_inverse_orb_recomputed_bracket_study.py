"""Pins the recomputed-bracket study's logic and its committed artifact.

The study answers whether translating the bracket onto the actual fill rescues
the inverse ORB lane. It does not: -$249.86 across 57 arms, negative in both
halves, versus -$61.10 across the 20 arms the shipped PaperBroker guard keeps.

These tests do NOT need the gitignored bar corpus: the pure geometry functions
are exercised directly, and the aggregate claims are checked against the
committed JSON artifact.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import inverse_orb_recomputed_bracket_study as study

ARTIFACT = Path("scripts/inverse_orb_recomputed_bracket_study_2026-09-08.json")


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(ARTIFACT.read_text())


def _row(direction: str, *, source_entry: float, stop: float, target: float, fill: float) -> dict:
    return {
        "inverse_direction": direction, "source_entry": source_entry,
        "inverse_stop": stop, "inverse_target": target, "fill_market": fill,
    }


def test_recompute_preserves_both_bracket_distances() -> None:
    """R:R and stop width must be untouched -- only the anchor moves."""
    row = _row("SHORT", source_entry=19498.5, stop=19511.0, target=19473.5, fill=19460.0)
    stop, target = study.recomputed_bracket(row)
    fill = study.entry_price(row)
    assert stop - fill == pytest.approx(19511.0 - 19498.5)
    assert target - fill == pytest.approx(19473.5 - 19498.5)
    # and the recomputed bracket actually surrounds the fill, which is the point
    assert target < fill < stop


def test_recomputed_bracket_is_always_valid_at_the_fill() -> None:
    """By construction: every arm becomes admissible, including detached ones."""
    for direction, entry, stop, target, fill in [
        ("LONG", 19498.5, 19486.0, 19523.5, 19540.0),   # filled above its own target
        ("SHORT", 19498.5, 19511.0, 19473.5, 19460.0),  # filled below its own target
    ]:
        row = _row(direction, source_entry=entry, stop=stop, target=target, fill=fill)
        assert study.bracket_valid_at_fill(row) is False  # original bracket: invalid
        new_stop, new_target = study.recomputed_bracket(row)
        moved = dict(row, inverse_stop=new_stop, inverse_target=new_target)
        assert study.bracket_valid_at_fill(moved) is True


def test_bracket_validity_matches_the_shipped_guard_rule() -> None:
    valid = _row("LONG", source_entry=19498.5, stop=19486.0, target=19523.5, fill=19500.0)
    assert study.bracket_valid_at_fill(valid) is True
    beyond_target = _row("LONG", source_entry=19498.5, stop=19486.0, target=19523.5, fill=19540.0)
    assert study.bracket_valid_at_fill(beyond_target) is False


def test_control_reproduced_the_frozen_baseline_exactly(report: dict) -> None:
    """If the control ever stops holding, the treatment numbers are meaningless."""
    assert "0 disagreements" in report["manifest"]["control"]
    baseline = report["baseline_as_recorded"]
    assert baseline["n"] == 57
    assert baseline["net"] == pytest.approx(1026.64)
    assert baseline["profit_factor"] == pytest.approx(5.28)


def test_recomputing_does_not_rescue_the_lane(report: dict) -> None:
    recomputed = report["recomputed_bracket"]
    assert recomputed["n"] == 57
    assert recomputed["net"] < 0
    assert recomputed["halves"]["h1"] < 0 and recomputed["halves"]["h2"] < 0
    assert recomputed["unresolved"] == 0
    # worse in absolute terms than simply refusing the invalid fills
    assert recomputed["net"] < report["refuse_admissible_only"]["net"]


def test_the_artifact_profit_set_is_negative_once_the_bracket_is_honest(report: dict) -> None:
    """The 37 arms carrying +$1,138.26 as 'wins' lose money when measured properly."""
    split = report["recomputed_split_by_original_admissibility"]["originally_rejected"]
    assert split["n"] == 37
    assert split["net"] < 0
    assert split["losses"] > split["wins"]
