"""Session prints for the six-micro paper journal. No network."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from research.six_micro_daily_tsmom_collect import (
    UNSCHEDULED_ROOTS,
    completed_through,
    listed_front,
    prints_for_soonest,
    session_prints,
)
from sources.polygon_client import PolygonBar, PolygonError, front_contract


def _bar(local: str, *, price: float = 100.0, ticker: str = "MNQU6") -> PolygonBar:
    ts = datetime.fromisoformat(local).replace(tzinfo=timezone.utc)
    # Tests pass UTC stamps that are already the New York wall clock shifted.
    # 13:30 UTC is 09:30 ET in October (EDT, UTC-4).
    return PolygonBar(ts=ts, open=price, high=price, low=price, close=price + 1, volume=1, ticker=ticker)


def test_completed_through_saturday_is_friday():
    now = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)
    assert completed_through(now) == date(2026, 10, 9)


def test_session_print_uses_the_open_and_the_1545_close():
    bars = [
        _bar("2026-10-09T13:30:00"),
        _bar("2026-10-09T19:45:00", price=10),
    ]
    prints = session_prints("MNQ", bars, start=date(2026, 6, 29), end=date(2026, 10, 9))
    assert len(prints) == 1
    assert prints[0].rth_open == bars[0].open
    assert prints[0].session_close == bars[1].close
    assert prints[0].contract == "MNQU6"


def test_missing_open_is_not_stored():
    bars = [_bar("2026-10-09T19:45:00")]
    assert session_prints("MNQ", bars, start=date(2026, 6, 29), end=date(2026, 10, 9)) == []


def test_missing_close_is_not_stored():
    bars = [_bar("2026-10-09T13:30:00")]
    assert session_prints("MNQ", bars, start=date(2026, 6, 29), end=date(2026, 10, 9)) == []


def test_gold_crude_and_bitcoin_are_not_in_the_scheduled_fetch():
    assert UNSCHEDULED_ROOTS == ("MGC", "MCL", "MBT")


def test_listed_front_is_the_soonest_expiry_still_open():
    rows = [
        {"ticker": "MGCZ6", "product_code": "MGC", "first_trade_date": "2025-12-01", "last_trade_date": "2026-12-28"},
        {"ticker": "MGCV6", "product_code": "MGC", "first_trade_date": "2025-10-01", "last_trade_date": "2026-10-28"},
    ]
    assert listed_front("MGC", rows, date(2026, 10, 9)) == "MGCV6"


def test_two_contracts_keep_the_sooner_expiry():
    bars = [
        _bar("2026-10-09T13:30:00", ticker="MGCZ6"),
        _bar("2026-10-09T19:45:00", ticker="MGCZ6", price=10),
        _bar("2026-10-09T13:30:00", ticker="MGCV6", price=20),
        _bar("2026-10-09T19:45:00", ticker="MGCV6", price=30),
    ]
    prints = prints_for_soonest("MGC", bars, start=date(2026, 6, 29), end=date(2026, 10, 9), listings=[
        {"ticker": "MGCZ6", "product_code": "MGC", "first_trade_date": "2026-01-01", "last_trade_date": "2026-12-28"},
        {"ticker": "MGCV6", "product_code": "MGC", "first_trade_date": "2026-01-01", "last_trade_date": "2026-10-28"},
    ])
    assert len(prints) == 1
    assert prints[0].contract == "MGCV6"


def test_expired_listing_is_not_used():
    rows = [
        {"ticker": "MGCV6", "first_trade_date": "2025-10-01", "last_trade_date": "2026-09-28"},
        {"ticker": "MGCZ6", "first_trade_date": "2025-12-01", "last_trade_date": "2026-12-28"},
    ]
    assert listed_front("MGC", rows, date(2026, 10, 9)) == "MGCZ6"


def test_journal_under_research_evidence_is_refused(tmp_path):
    from research.six_micro_daily_tsmom_paper import PaperLedger

    with pytest.raises(ValueError):
        PaperLedger(tmp_path / "research-evidence" / "journal")

def test_listed_front_uses_true_last_trade_and_first_trade_date():
    rows = [
        {"ticker": "MGCZ6", "product_code": "MGC", "first_trade_date": "2026-01-01", "last_trade_date": "2026-11-01"},
        {"ticker": "MGCV6", "product_code": "MGC", "first_trade_date": "2026-10-10", "last_trade_date": "2026-10-28"},
    ]
    # Although V is an earlier month code, it was not listed on October 9.
    assert listed_front("MGC", rows, date(2026, 10, 9)) == "MGCZ6"
    assert listed_front("MCL", rows, date(2026, 10, 9)) is None


def test_listed_front_missing_bar_does_not_substitute_far_contract():
    rows = [
        {"ticker": "MGCV6", "product_code": "MGC", "first_trade_date": "2026-01-01", "last_trade_date": "2026-10-28"},
        {"ticker": "MGCZ6", "product_code": "MGC", "first_trade_date": "2026-01-01", "last_trade_date": "2026-12-28"},
    ]
    only_far = [_bar("2026-10-09T13:30:00", ticker="MGCZ6"),
                _bar("2026-10-09T19:45:00", ticker="MGCZ6")]
    with pytest.raises(PolygonError, match="no complete"):
        prints_for_soonest(
            "MGC", only_far, start=date(2026, 10, 9),
            end=date(2026, 10, 9), listings=rows,
        )


def test_six_micro_incremental_restarts(monkeypatch, tmp_path):
    import research.six_micro_daily_tsmom_collect as module
    from research.six_micro_daily_tsmom_paper import PaperLedger

    monkeypatch.setattr(module, "completed_through", lambda _now: date(2026, 10, 15))
    dates = [date(2026, 10, 8), date(2026, 10, 9),
             date(2026, 10, 12), date(2026, 10, 13)]

    class Fake:
        def fetch_contracts(self, root):
            return [{"ticker": f"{root}Z6", "product_code": root,
                     "first_trade_date": "2026-01-01", "last_trade_date": "2026-12-18"}]

        def fetch_bars(self, ticker, start, end, timeframe):
            assert timeframe == 15
            result = []
            for day in dates:
                if start <= day <= end:
                    for hhmm, p in (("13:30", 100.0), ("19:45", 101.0)):
                        result.append(_bar(f"{day.isoformat()}T{hhmm}:00",
                                           price=p, ticker=ticker))
            return result

    fake = Fake()
    for end, expected in ((date(2026, 10, 9), 2),
                          (date(2026, 10, 12), 3),
                          (date(2026, 10, 12), 3),
                          (date(2026, 10, 13), 4)):
        module.collect_scheduled(tmp_path, fake, start=dates[0], end=end)
        module.collect_listed(tmp_path, fake, start=dates[0], end=end)
        ledger = PaperLedger(tmp_path)  # simulates process restart
        for root in ("MNQ", "MES", "M2K", "MGC", "MCL", "MBT"):
            entries = ledger.state["closes"][root]
            assert len(entries) == expected
            assert len({item["session"] for item in entries}) == expected
        assert ledger.round_turns == 0


def test_scheduled_roll_fetches_old_contract_open(monkeypatch, tmp_path):
    import research.six_micro_daily_tsmom_collect as module

    monkeypatch.setattr(module, "completed_through", lambda _now: date(2026, 6, 15))
    prior, switch, after = date(2026, 6, 10), date(2026, 6, 11), date(2026, 6, 12)
    old = front_contract("MNQ", prior)
    new = front_contract("MNQ", switch)
    assert old != new

    class Fake:
        def __init__(self):
            self.calls = []

        def fetch_bars(self, ticker, start, end, timeframe):
            self.calls.append((ticker, start, end, timeframe))
            bars = []
            for day in (prior, switch, after):
                if start <= day <= end:
                    for hhmm in ("13:30", "19:45"):
                        bars.append(_bar(f"{day.isoformat()}T{hhmm}:00", ticker=ticker))
            return bars

    fake = Fake()
    module.collect_scheduled(tmp_path, fake, start=prior, end=after)
    assert (old, switch, switch, 15) in fake.calls

def test_reference_contract_pagination_is_read_only_and_complete():
    from sources.polygon_client import PolygonFuturesClient

    class Stub(PolygonFuturesClient):
        def __init__(self):
            super().__init__(api_key="test-only")
            self.calls = []

        def _get(self, _client, url, params=None):
            self.calls.append((url, params))
            if len(self.calls) == 1:
                return {
                    "status": "OK",
                    "results": [{"product_code": "MGC", "ticker": "MGCV6",
                                 "first_trade_date": "2026-01-01",
                                 "last_trade_date": "2026-10-28"}],
                    "next_url": "https://example.invalid/page2",
                }
            return {
                "status": "OK",
                "results": [{"product_code": "MGC", "ticker": "MGCZ6",
                             "first_trade_date": "2026-01-01",
                             "last_trade_date": "2026-12-28"}],
            }

    stub = Stub()
    rows = stub.fetch_contracts("MGC")
    assert {row["ticker"] for row in rows} == {"MGCV6", "MGCZ6"}
    assert stub.calls[0][1]["product_code"] == "MGC"
    assert stub.calls[1][1] is None


def test_empty_reference_response_fails_closed():
    from sources.polygon_client import PolygonFuturesClient

    class Stub(PolygonFuturesClient):
        def __init__(self):
            super().__init__(api_key="test-only")

        def _get(self, _client, url, params=None):
            return {"status": "OK", "results": []}

    with pytest.raises(PolygonError, match="no dated futures listings"):
        Stub().fetch_contracts("MGC")
