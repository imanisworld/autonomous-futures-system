from __future__ import annotations

from options_manager.validation.fixture_status import build_fixture_candidate_inventory
from scripts.options_non_strat_fixture_audit import fixture_testability, parse_window


def test_parse_window_accepts_concrete_fixture_ranges_and_rejects_unknown():
    assert parse_window("2026-04-30/2026-05-01") is not None
    assert parse_window("unknown") is None
    assert parse_window("") is None
    assert parse_window("2026-05-02/2026-05-01") is None


def test_logged_fixture_inventory_is_not_silently_dropped():
    inventory = build_fixture_candidate_inventory()
    assert len(inventory) == 12
    testable = {}
    blocked = {}
    for ticker, candidate in inventory.items():
        ok, reason = fixture_testability(candidate)
        (testable if ok else blocked)[ticker] = reason

    # Concrete equity windows must be routed to the audit.
    for ticker in ("HOOD", "EBAY", "AMD", "ORCL", "FITB", "BAC"):
        assert ticker in testable

    # Unknown-window / structurally incompatible fixtures stay explicit.
    assert blocked
    for reason in blocked.values():
        assert reason


def test_spxw_is_explicitly_blocked_from_equity_bar_observer():
    candidate = build_fixture_candidate_inventory()["SPXW"]
    ok, reason = fixture_testability(candidate)
    assert ok is False
    assert reason in {
        "fixture_window_unknown",
        "index_option_fixture_not_equity_bar_observer",
    }
