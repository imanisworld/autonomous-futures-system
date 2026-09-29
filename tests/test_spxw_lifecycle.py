"""SPXW paper lifecycle: episode dedupe + OPEN resolution (V1 semantics)."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from alert_ranker import paper_spxw_v1 as spxw
from alert_ranker.spxw_lane import SpxwPaperLane
from alert_ranker.spxw_storage import SpxwStorage

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 29, 10, 30, tzinfo=NY)
LATER = datetime(2026, 9, 29, 10, 35, tzinfo=NY)
NEXT_BAR = datetime(2026, 9, 29, 11, 5, tzinfo=NY)


class _Chain:
    def __init__(self, expiration: str, calls, puts=()):
        self.expiration = expiration
        self.calls = calls
        self.puts = puts
        self.error = None


def _quote(*, bid=1.10, ask=1.50, symbol="SPXW260929C05800000", stale=False):
    return SimpleNamespace(
        symbol=symbol,
        option_type="CALL",
        strike=5800.0,
        bid=bid,
        ask=ask,
        mid=round((bid + ask) / 2.0, 4),
        volume=2000,
        open_interest=8000,
        delta=0.40,
        gamma=0.01,
        theta=-0.05,
        implied_volatility=0.18,
        quote_timestamp="2026-09-29T14:29:30+00:00",
        source="test",
        stale=stale,
    )


class _Market:
    def __init__(self, *, bid=1.40, ask=1.50, underlying=5800.0, quote_ts="2026-09-29T14:29:30+00:00"):
        self.bid = bid
        self.ask = ask
        self.underlying = underlying
        self.quote_ts = quote_ts
        self.chain_calls = 0

    async def fetch_market_snapshot(self, ticker: str):
        return SimpleNamespace(ticker=ticker, price=self.underlying, error=None)

    async def fetch_option_expirations(self, ticker: str):
        assert ticker == "SPXW"
        return ["2026-09-29", "2026-10-03"]

    async def fetch_option_chain(self, ticker: str, expiration: str | None = None):
        assert ticker == "SPXW"
        self.chain_calls += 1
        q = _quote(bid=self.bid, ask=self.ask)
        q.quote_timestamp = self.quote_ts
        return _Chain(expiration, (q,))


def _setup(**overrides):
    data = {
        "ticker": "SPX",
        "signal_underlying": "SPX",
        "setup_status": "TRIGGERED",
        "direction": "LONG",
        "price": 5800.0,
        "pattern": "2-1-2",
        "setup_type": "2-1-2",
        "setup_timeframe": "30M",
        "setup_entry_trigger": 5795.0,
        "underlying_invalidation": 5780.0,
        "target_1": 5850.0,
    }
    data.update(overrides)
    return data


def _enabled_cfg(tmp_path):
    return SimpleNamespace(
        spxw_paper_lane_enabled=True,
        timezone="America/New_York",
        public_stale_quote_seconds=900.0,
        paper_v1_min_remaining_rr=1.0,
        spxw_interval_minutes=5,
        spxw_sqlite_path=tmp_path / "spxw.sqlite",
    )


def test_repeated_scan_same_episode_creates_only_one_open(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=_Market(),
        storage=storage,
        equity_watchlist=["SPY"],
    )
    first = asyncio.run(lane.scan_spx_setup(_setup(), now=NOW))
    second = asyncio.run(lane.scan_spx_setup(_setup(), now=LATER))
    third = asyncio.run(lane.scan_spx_setup(_setup(), now=LATER))

    assert first.status == "OPEN"
    assert first.journal_id is not None
    assert second.status == "SKIPPED"
    assert second.reason.startswith("duplicate_episode:")
    assert third.status == "SKIPPED"
    opens = [r for r in storage.all_rows() if r.status == "OPEN"]
    assert len(opens) == 1
    assert opens[0].selected_contract["episode_key"]
    assert opens[0].dte_cohort == spxw.COHORT_0DTE


def test_new_trigger_creates_new_episode(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=_Market(),
        storage=storage,
        equity_watchlist=["SPY"],
    )
    first = asyncio.run(lane.scan_spx_setup(_setup(setup_entry_trigger=5795.0), now=NOW))
    # Different mechanical trigger = new episode identity.
    second = asyncio.run(
        lane.scan_spx_setup(_setup(setup_entry_trigger=5810.0), now=NOW)
    )
    assert first.status == "OPEN"
    assert second.status == "OPEN"
    assert first.journal_id != second.journal_id
    assert len([r for r in storage.all_rows() if r.status == "OPEN"]) == 2


def test_resolve_premium_stop_records_pnl_and_frees_aggregate_risk(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    market = _Market(bid=1.40, ask=1.50, underlying=5800.0)
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=market,
        storage=storage,
        equity_watchlist=["SPY"],
    )
    opened = asyncio.run(lane.scan_spx_setup(_setup(), now=NOW))
    assert opened.status == "OPEN"
    assert storage.open_planned_risk() == 37.5

    # Exit bid at/below premium stop 1.125 → LOSS.
    market.bid = 1.10
    market.ask = 1.20
    market.quote_ts = "2026-09-29T14:34:00+00:00"
    counts = asyncio.run(lane.resolve_open_positions(now=LATER, scheduled=False))
    assert counts["checked"] == 1
    assert counts["resolved"] == 1
    row = storage.get(opened.journal_id)
    assert row.status == "LOSS"
    assert row.dte_cohort == spxw.COHORT_0DTE
    assert row.outcome["closed_reason"] == "premium_stop_hit"
    assert row.outcome["exit_mark"] == 1.10
    assert row.outcome["entry_mark"] == 1.50
    # (1.10 - 1.50) * 100 = -40
    assert row.outcome["pnl_dollars"] == -40.0
    assert row.outcome["averaging_down"] is False
    assert storage.open_planned_risk() == 0.0


def test_resolve_target_hit_uses_bid_exit(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    market = _Market(bid=1.40, ask=1.50, underlying=5800.0)
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=market,
        storage=storage,
        equity_watchlist=["SPY"],
    )
    opened = asyncio.run(lane.scan_spx_setup(_setup(), now=NOW))
    assert opened.status == "OPEN"

    lane.market_data = _Market(
        bid=2.00,
        ask=2.10,
        underlying=5855.0,
        quote_ts="2026-09-29T14:34:00+00:00",
    )
    counts = asyncio.run(lane.resolve_open_positions(now=LATER, scheduled=False))
    assert counts["resolved"] == 1
    row = storage.get(opened.journal_id)
    assert row.status == "WIN"
    assert row.outcome["closed_reason"] == "target_hit"
    assert row.outcome["exit_mark"] == 2.00
    assert row.outcome["pnl_dollars"] == 50.0  # (2.00-1.50)*100
    assert row.dte_cohort == "0DTE"


def test_resolve_fails_closed_on_stale_quote(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=_Market(),
        storage=storage,
        equity_watchlist=["SPY"],
    )
    opened = asyncio.run(lane.scan_spx_setup(_setup(), now=NOW))

    class _Stale(_Market):
        async def fetch_option_chain(self, ticker, expiration=None):
            q = _quote(bid=0.50, ask=0.60, stale=True)
            return _Chain(expiration, (q,))

    lane.market_data = _Stale(underlying=5700.0)
    counts = asyncio.run(lane.resolve_open_positions(now=LATER, scheduled=False))
    assert counts["resolved"] == 0
    assert storage.get(opened.journal_id).status == "OPEN"
    assert storage.open_planned_risk() == 37.5


def test_resolve_fails_closed_on_contract_mismatch(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=_Market(),
        storage=storage,
        equity_watchlist=["SPY"],
    )
    opened = asyncio.run(lane.scan_spx_setup(_setup(), now=NOW))

    class _WrongSymbol(_Market):
        async def fetch_option_chain(self, ticker, expiration=None):
            q = _quote(bid=0.50, ask=0.60, symbol="SPXW260929C05900000")
            q.quote_timestamp = "2026-09-29T14:34:00+00:00"
            return _Chain(expiration, (q,))

    lane.market_data = _WrongSymbol(underlying=5700.0)
    # Without exact symbol match, resolver falls back to expiry-only path with
    # underlying_price=None → may EXPIRY only if past expiry; otherwise OPEN.
    counts = asyncio.run(lane.resolve_open_positions(now=LATER, scheduled=False))
    assert counts["resolved"] == 0
    assert storage.get(opened.journal_id).status == "OPEN"


def test_aggregate_risk_after_dedupe_and_resolve(tmp_path):
    storage = SpxwStorage(tmp_path / "spxw.sqlite")
    market = _Market()
    lane = SpxwPaperLane(
        config=_enabled_cfg(tmp_path),
        market_data=market,
        storage=storage,
        equity_watchlist=["SPY"],
    )
    a = asyncio.run(lane.scan_spx_setup(_setup(setup_entry_trigger=5795.0), now=NOW))
    asyncio.run(lane.scan_spx_setup(_setup(setup_entry_trigger=5795.0), now=LATER))
    b = asyncio.run(lane.scan_spx_setup(_setup(setup_entry_trigger=5810.0), now=NOW))
    assert a.status == "OPEN" and b.status == "OPEN"
    assert storage.open_planned_risk() == 75.0  # 37.5 * 2

    lane.market_data = _Market(
        bid=1.10,
        ask=1.20,
        underlying=5800.0,
        quote_ts="2026-09-29T14:34:00+00:00",
    )
    asyncio.run(lane.resolve_open_positions(now=LATER, scheduled=False))
    assert storage.open_planned_risk() == 0.0
    # Same bar bucket remains suppressed after resolve (episode identity).
    same_bucket = asyncio.run(
        lane.scan_spx_setup(_setup(setup_entry_trigger=5795.0), now=LATER)
    )
    assert same_bucket.status == "SKIPPED"
    assert same_bucket.reason.startswith("duplicate_episode:")
    # A later bar bucket is a new episode and may open (fresh liquid quotes).
    lane.market_data = _Market(
        bid=1.40,
        ask=1.50,
        underlying=5800.0,
        quote_ts="2026-09-29T15:04:30+00:00",
    )
    later_bar = asyncio.run(
        lane.scan_spx_setup(_setup(setup_entry_trigger=5795.0), now=NEXT_BAR)
    )
    assert later_bar.status == "OPEN"
