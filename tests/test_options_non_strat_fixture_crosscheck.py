from __future__ import annotations

import json
import sqlite3

from options_manager.validation.fixture_status import (
    FixtureCandidate,
    FixtureStatus,
    build_fixture_candidate_inventory,
)
from scripts.options_non_strat_fixture_crosscheck import (
    EXPECTATIONS,
    _parse_window,
    crosscheck,
    evaluate_fixture,
    summary,
)


def _candidate(
    ticker: str,
    *,
    window: str = "2026-04-30/2026-05-01",
    status: FixtureStatus = FixtureStatus.SPECIAL_CASE_FIXTURE,
) -> FixtureCandidate:
    return FixtureCandidate(
        ticker=ticker,
        window=window,
        status=status,
        best_future_use="test",
        proof_confirmed=("test",),
        reason_not_first_proof="test",
    )


def test_parse_window_accepts_single_and_range_but_not_unknown():
    assert _parse_window("2026-04-30") == ("2026-04-30", "2026-04-30")
    assert _parse_window("2026-04-30/2026-05-01") == (
        "2026-04-30",
        "2026-05-01",
    )
    assert _parse_window("unknown") == (None, None)
    assert _parse_window("2026-05-02/2026-05-01") == (None, None)


def test_ebay_mapping_is_explicit_and_coverage_only():
    assert EXPECTATIONS["EBAY"]["semantic_setup"] == "PDL_RECLAIM_LONG"
    assert EXPECTATIONS["EBAY"]["expected_families"] == ("PDL_REJECTION_LONG",)

    row = evaluate_fixture(
        _candidate("EBAY"),
        [
            {
                "family": "PDL_REJECTION_LONG",
                "symbol": "EBAY",
                "session_date": "2026-04-30",
            }
        ],
    )
    assert row.result == "COVERAGE_MATCH"
    assert row.matched_events == 1
    assert row.edge_claim_allowed is False


def test_hood_stays_out_of_scope_until_planned_level_source_exists():
    candidate = _candidate(
        "HOOD",
        window="2026-06-12/2026-06-15",
        status=FixtureStatus.PENDING_PROOF_FIXTURE,
    )
    row = evaluate_fixture(candidate, [{"family": "VWAP_TEST_HOLD_LONG"}])
    assert row.result == "OUT_OF_SCOPE"
    assert row.reason == "generic_planned_level_source_unproven"
    assert row.matched_events == 0


def test_amd_premarket_fixture_is_not_backfilled_from_rth_events():
    candidate = _candidate(
        "AMD",
        window="2026-02-05/2026-02-06",
        status=FixtureStatus.SPECIAL_CASE_FIXTURE,
    )
    row = evaluate_fixture(candidate, [{"family": "PDH_RECLAIM_LONG"}])
    assert row.result == "OUT_OF_SCOPE"
    assert row.reason == "ns-v0.1_regular_session_only"


def test_unmapped_fixture_is_never_reverse_engineered_from_observed_events():
    row = evaluate_fixture(
        _candidate("ORCL", window="2026-05-04/2026-05-07"),
        [
            {"family": "VWAP_RECLAIM_LONG"},
            {"family": "PDH_RECLAIM_LONG"},
        ],
    )
    assert row.result == "UNTESTABLE"
    assert row.reason == "fixture_has_no_proven_setup_family_for_ns-v0.1"
    assert row.observed_families == ("PDH_RECLAIM_LONG", "VWAP_RECLAIM_LONG")
    assert row.edge_claim_allowed is False


def test_crosscheck_marks_testable_fixture_data_missing_instead_of_false_negative():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE non_strat_events (
               observer_version TEXT,
               symbol TEXT,
               session_date TEXT,
               bar_start TEXT,
               family TEXT,
               row_json TEXT
           )"""
    )
    inventory = {"EBAY": _candidate("EBAY")}
    rows = crosscheck(conn, inventory)
    assert len(rows) == 1
    assert rows[0].result == "DATA_MISSING"
    assert rows[0].reason == "no_ns-v0.1_events_loaded_for_fixture_window"


def test_crosscheck_reads_matching_event_without_mutating_fixture_truth():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE non_strat_events (
               observer_version TEXT,
               symbol TEXT,
               session_date TEXT,
               bar_start TEXT,
               family TEXT,
               row_json TEXT
           )"""
    )
    payload = {
        "family": "PDL_REJECTION_LONG",
        "symbol": "EBAY",
        "session_date": "2026-04-30",
    }
    conn.execute(
        """INSERT INTO non_strat_events
           (observer_version, symbol, session_date, bar_start, family, row_json)
           VALUES (?,?,?,?,?,?)""",
        (
            "ns-v0.1",
            "EBAY",
            "2026-04-30",
            "2026-04-30T15:00:00+00:00",
            "PDL_REJECTION_LONG",
            json.dumps(payload),
        ),
    )
    rows = crosscheck(conn, {"EBAY": _candidate("EBAY")})
    assert rows[0].result == "COVERAGE_MATCH"
    assert rows[0].matched_events == 1
    assert rows[0].fixture_status == "special_case_fixture"
    report = summary(rows)
    assert report["edge_claim_allowed"] is False


def test_real_inventory_is_complete_and_only_explicit_expectations_are_matchable():
    inventory = build_fixture_candidate_inventory()
    assert len(inventory) == 12
    assert set(EXPECTATIONS) == {"EBAY", "HOOD", "AMD"}
    for ticker in inventory:
        if ticker not in EXPECTATIONS:
            assert "setup" not in EXPECTATIONS.get(ticker, {})