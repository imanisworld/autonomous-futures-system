from datetime import datetime, timezone
import hashlib
import sqlite3
import hashlib
import sqlite3

import pytest

from alert_ranker.causal_bars import Bar
from alert_ranker.trigger_geometry import geometry_for_trigger
from alert_ranker.trigger_time import ArmedStratTrigger, TriggerResolution
from scripts.options_trigger_geometry_audit import (
    annotate_frozen_212_membership,
    frozen_212_reversal_keys,
    summarize_frozen_212,
)
from scripts.options_trigger_geometry_audit import (
    _walk_target_stop,
    annotate_frozen_212_membership,
    frozen_212_reversal_keys,
)

UTC = timezone.utc


def _bar(high, low):
    return Bar(
        start=datetime(2026, 9, 18, 13, 30, tzinfo=UTC),
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
        volume=100,
        vwap=(high + low) / 2,
    )


def _armed(pattern="212", high=10, low=5):
    return ArmedStratTrigger(
        pattern=pattern,
        armed_at=datetime(2026, 9, 18, 14, 0, tzinfo=UTC),
        watch_until=datetime(2026, 9, 18, 14, 30, tzinfo=UTC),
        boundary_high=high,
        boundary_low=low,
        reference_direction="two_down",
        source_timeframe="30Min",
    )


def _result(family, direction="LONG", subtype="REVERSAL", trigger=10, stop=5):
    return TriggerResolution(
        status="TRIGGERED",
        pattern="212",
        family=family,
        subtype=subtype,
        direction=direction,
        break_side="HIGH" if direction == "LONG" else "LOW",
        trigger_level=trigger,
        invalidation_level=stop,
        trigger_bar_start=datetime(2026, 9, 18, 14, 0, tzinfo=UTC),
        trigger_bar_timeframe="5Min",
        final_scenario="two_up",
        opposite_side_broken_later=False,
        reason_code="first_boundary_break",
    )


def test_212_reversal_geometry_is_fully_defined():
    g = geometry_for_trigger(
        armed=_armed(),
        result=_result("STRAT_212_REVERSAL"),
        parent_bar=_bar(12, 2),
    )
    assert g.status == "DEFINED"
    assert g.target == 12
    assert g.stop == 5


def test_212_continuation_does_not_invent_target():
    g = geometry_for_trigger(
        armed=_armed(),
        result=_result("STRAT_212_CONTINUATION", subtype="CONTINUATION"),
        parent_bar=_bar(12, 2),
    )
    assert g.status == "STOP_ONLY"
    assert g.target is None
    assert g.stop == 5


def test_222_reversal_defines_magnitude_but_not_stop():
    g = geometry_for_trigger(
        armed=_armed(pattern="222"),
        result=_result("STRAT_222_REVERSAL"),
        parent_bar=_bar(15, 3),
    )
    assert g.status == "TARGET_ONLY"
    assert g.target == 15
    assert g.stop is None


def test_32_refuses_far_side_stop_and_own_target():
    g = geometry_for_trigger(
        armed=_armed(pattern="32"),
        result=_result("STRAT_32_REVERSAL", direction="SHORT", trigger=5, stop=10),
        parent_bar=_bar(12, 2),
    )
    assert g.status == "NO_OWN_MAGNITUDE"
    assert g.target is None
    assert g.stop is None
    assert "not_far_side_of_3" in g.stop_source



def test_equal_parent_extreme_is_defined_but_magnitude_is_consumed_at_trigger():
    g = geometry_for_trigger(
        armed=_armed(high=10, low=5),
        result=_result("STRAT_212_REVERSAL", direction="LONG", trigger=10, stop=5),
        parent_bar=_bar(10, 2),
    )
    assert g.status == "DEFINED"
    assert g.target == 10
    assert g.stop == 5


def test_zero_remaining_magnitude_is_labeled_consumed_not_malformed():
    outcome = _walk_target_stop(
        direction="LONG",
        entry=163.75,
        target=163.75,
        stop=163.32,
        trigger_bar_start=datetime(2026, 9, 18, 14, 0, tzinfo=UTC),
        session_close=datetime(2026, 9, 18, 20, 0, tzinfo=UTC),
        bars=(),
    )
    assert outcome == "TARGET_CONSUMED_AT_ENTRY"


def _observer_db(tmp_path):
    path = tmp_path / "observer.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE coverage_events (
            observer_version TEXT,
            family TEXT,
            symbol TEXT,
            session_date TEXT,
            bar_start TEXT,
            direction TEXT,
            entry_trigger REAL,
            invalidation REAL
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO coverage_events (
            observer_version, family, symbol, session_date, bar_start,
            direction, entry_trigger, invalidation
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "cov-v0.1",
                "STRAT_212_REVERSAL",
                "SPY",
                "2026-09-09",
                "2026-09-09T14:00:00+00:00",
                "LONG",
                500.0,
                495.0,
            ),
            (
                "cov-v0.1",
                "STRAT_212_REVERSAL",
                "AEP",
                "2026-09-09",
                "2026-09-09T14:30:00+00:00",
                "SHORT",
                100.0,
                101.0,
            ),
            (
                "other-version",
                "STRAT_212_REVERSAL",
                "SPY",
                "2026-09-09",
                "2026-09-09T15:00:00+00:00",
                "LONG",
                510.0,
                505.0,
            ),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_frozen_observer_membership_is_sha_gated_and_universe_filtered(tmp_path):
    path = _observer_db(tmp_path)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    keys = frozen_212_reversal_keys(
        path,
        expected_sha256=sha,
        symbols=["SPY"],
        from_date="2026-09-09",
        to_date="2026-09-15",
    )
    assert len(keys) == 1

    rows = [
        {
            "family": "STRAT_212_REVERSAL",
            "symbol": "SPY",
            "session_date": "2026-09-09",
            "watch_bar_start": "2026-09-09T14:00:00+00:00",
            "direction": "LONG",
            "entry": 500.0,
            "generic_stop": 495.0,
            "canonical_target_consumed_at_entry": False,
            "canonical_target_valid": True,
            "canonical_stop_matches_generic": True,
            "canonical_r": 0.5,
            "nearest_relation": "MATCH",
            "floor_relation": "BEYOND_CANONICAL",
            "canonical_outcome": "TARGET_FIRST",
            "nearest_outcome": "TARGET_FIRST",
            "floor_outcome": "UNRESOLVED_AT_CLOSE",
        }
    ]
    annotate_frozen_212_membership(rows, keys)
    assert rows[0]["frozen_212_reversal_match"] is True

    summary = summarize_frozen_212(rows, expected_count=1)
    assert summary["all_expected_rows_matched"] is True
    assert summary["matched_rows"] == 1
    assert summary["canonical_stop_matches_generic_n"] == 1
    assert summary["canonical_r_below_1_n"] == 1


def test_frozen_observer_membership_rejects_hash_drift(tmp_path):
    path = _observer_db(tmp_path)
    with pytest.raises(ValueError, match="observer db sha256 mismatch"):
        frozen_212_reversal_keys(
            path,
            expected_sha256="0" * 64,
            symbols=["SPY"],
            from_date="2026-09-09",
            to_date="2026-09-15",
        )


def test_frozen_212_matcher_is_hash_gated_and_marks_exact_event(tmp_path):
    db = tmp_path / "observer.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE coverage_events (
            observer_version TEXT,
            family TEXT,
            symbol TEXT,
            session_date TEXT,
            bar_start TEXT,
            direction TEXT,
            entry_trigger REAL,
            invalidation REAL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO coverage_events
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "cov-v0.1",
            "STRAT_212_REVERSAL",
            "AMZN",
            "2026-09-09",
            "2026-09-09T15:00:00+00:00",
            "LONG",
            250.0,
            248.0,
        ),
    )
    conn.commit()
    conn.close()

    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    keys = frozen_212_reversal_keys(
        db,
        expected_sha256=digest,
        symbols=("AMZN",),
        from_date="2026-09-09",
        to_date="2026-09-15",
    )
    assert len(keys) == 1

    rows = [
        {
            "family": "STRAT_212_REVERSAL",
            "symbol": "AMZN",
            "session_date": "2026-09-09",
            "watch_bar_start": "2026-09-09T15:00:00+00:00",
            "direction": "LONG",
            "entry": 250.0,
            "generic_stop": 248.0,
        }
    ]
    annotate_frozen_212_membership(rows, keys)
    assert rows[0]["frozen_212_reversal_match"] is True


def test_frozen_212_matcher_rejects_wrong_db_hash(tmp_path):
    db = tmp_path / "observer.sqlite"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE coverage_events (
            observer_version TEXT,
            family TEXT,
            symbol TEXT,
            session_date TEXT,
            bar_start TEXT,
            direction TEXT,
            entry_trigger REAL,
            invalidation REAL
        )
        """
    )
    conn.commit()
    conn.close()

    import pytest

    with pytest.raises(ValueError, match="observer db sha256 mismatch"):
        frozen_212_reversal_keys(
            db,
            expected_sha256="0" * 64,
            symbols=("AMZN",),
            from_date="2026-09-09",
            to_date="2026-09-15",
        )
