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


def test_fixture_audit_history_is_regular_session_only():
    from datetime import date, timedelta, timezone

    from alert_ranker.causal_bars import Bar
    from alert_ranker.session_calendar import nyse_session_for
    from scripts.options_non_strat_fixture_audit import _history_before

    prior = nyse_session_for(date(2026, 9, 17))
    assert prior is not None
    open_utc = prior.open.astimezone(timezone.utc)

    def bar(minutes):
        return Bar(start=open_utc + timedelta(minutes=minutes), open=1, high=1, low=1, close=1, volume=1, vwap=1)

    bars = [bar(-30), bar(0), bar(385), bar(390)]  # pre-market, RTH, last RTH, post-close
    assert [b.start_utc for b in _history_before(bars, [prior])] == [open_utc, open_utc + timedelta(minutes=385)]
