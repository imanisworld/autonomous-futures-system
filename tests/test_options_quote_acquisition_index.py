from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.options_quote_acquisition_index import acquisition_index_bytes, build_acquisition_rows


def _row(**overrides):
    row = {
        "symbol": "SPY", "session_date": "2026-09-10", "bar_time": "2026-09-10T15:00:00+00:00",
        "direction": "LONG", "family": "STRAT_212_REVERSAL", "episode_id": "ep-1",
        "trigger_level": "650.5", "invalidation_level": "648.0", "trigger_time": "2026-09-10T14:45:00+00:00",
        "first_seen_time": "2026-09-10T15:17:57+00:00", "in_20_universe": "True",
        "quality_flags": "", "source_file": "observer.jsonl",
        # Outcome fields deliberately present; builder must ignore them.
        "first_sight_outcome": "WIN", "MFE_R": "2.0", "MAE_R": "-0.2", "target_before_stop": "True",
    }
    row.update(overrides)
    return row


def test_structural_population_is_canonical_and_sorted():
    a = _row(symbol="QQQ", episode_id="ep-2", first_seen_time="2026-09-10T16:17:57+00:00")
    b = _row(symbol="SPY", episode_id="ep-1", first_seen_time="2026-09-10T15:17:57+00:00")
    out = build_acquisition_rows([a, b], family="STRAT_212_REVERSAL")
    assert [row["episode_id"] for row in out] == ["ep-1", "ep-2"]
    assert out[0]["decision_ts"] == "2026-09-10T15:17:57+00:00"
    assert out[0]["decision_basis"] == "first_sight"


def test_outcome_mutation_cannot_change_membership_or_bytes():
    base = _row()
    changed = deepcopy(base)
    changed.update(first_sight_outcome="LOSS", MFE_R="0.0", MAE_R="-5.0", target_before_stop="False")
    first = build_acquisition_rows([base], family="STRAT_212_REVERSAL")
    second = build_acquisition_rows([changed], family="STRAT_212_REVERSAL")
    assert first == second
    assert acquisition_index_bytes(first) == acquisition_index_bytes(second)


def test_other_family_and_outside_primary20_are_excluded():
    rows = [_row(), _row(episode_id="ep-2", family="STRAT_212_CONTINUATION"), _row(episode_id="ep-3", in_20_universe="False")]
    out = build_acquisition_rows(rows, family="STRAT_212_REVERSAL")
    assert [row["episode_id"] for row in out] == ["ep-1"]


def test_duplicate_decision_identity_fails_closed():
    with pytest.raises(ValueError, match="duplicate quote acquisition decision_id"):
        build_acquisition_rows([_row(), _row()], family="STRAT_212_REVERSAL")


def test_invalid_structural_fields_fail_closed():
    with pytest.raises(ValueError, match="invalid boolean"):
        build_acquisition_rows([_row(in_20_universe="maybe")], family="STRAT_212_REVERSAL")
    with pytest.raises(ValueError, match="trigger_level must be numeric"):
        build_acquisition_rows([_row(trigger_level="x")], family="STRAT_212_REVERSAL")
