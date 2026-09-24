from __future__ import annotations

import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from alert_ranker.causal_bars import MINUTE_1, MINUTE_30, Bar

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "options_timing_replay", ROOT / "scripts" / "research" / "options_timing_replay.py"
)
otr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(otr)

UTC = timezone.utc


def _minute(start: datetime, price: float, volume: float = 100.0) -> Bar:
    return Bar(start=start, open=price, high=price + 0.5, low=price - 0.5, close=price + 0.1,
               volume=volume, vwap=price)


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("session_not_started", "SESSION_NOT_STARTED"),
        ("no_session_bars", "NO_SESSION_BARS"),
        ("no_setup:sequence_not_212", "NO_SETUP"),
        ("counterfactual_observer_only", "OBSERVER"),
        ("ENTRY_LATE:episode_blocked_after_entry_late", "ENTRY_LATE_BLOCKED"),
        ("ENTRY_LATE:price_past_target", "ENTRY_LATE_FIRST"),
        ("entry_late_counterfactual_only:remaining_rr_0.45_below_1.00", "ENTRY_LATE_FIRST"),
        ("setup_forming:daily_32", "SETUP_FORMING"),
        ("setup_proof_incomplete:market_not_aligned", "SETUP_PROOF_INCOMPLETE"),
        ("DATA_INVALID:direction_unknown", "LEVELS_INVALID"),
        ("DATA_INVALID:planned_risk_outside_v1_cap:428.00", "PAST_LATE_GATE"),
        ("DATA_INVALID:expiration_fetch_error:ReplayChainUnavailable", "PAST_LATE_GATE"),
        ("trade_proof_incomplete:event_risk_unavailable", "PAST_LATE_GATE"),
        ("missing_context:spy", "BAR_CONTEXT_OTHER"),
        ("stale_market_data", "BAR_CONTEXT_OTHER"),
    ],
)
def test_reason_classes(reason, expected):
    assert otr.reason_class(reason) == expected


def test_alert_sent_is_past_late_gate():
    assert otr.reason_class("", alert_sent=True) == "PAST_LATE_GATE"


def test_aggregate_30m_is_clock_aligned_ohlcv():
    t0 = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    bars = [_minute(t0 + timedelta(minutes=i), 100 + i, volume=10 + i) for i in range(31)]
    agg = otr.aggregate_30m(bars)
    assert [b.start_utc for b in agg] == [t0, t0 + timedelta(minutes=30)]
    first = agg[0]
    assert first.open == 100 and first.close == pytest.approx(129.1)
    assert first.high == pytest.approx(129.5) and first.low == pytest.approx(99.5)
    assert first.volume == sum(10 + i for i in range(30))
    expected_vwap = sum((100 + i) * (10 + i) for i in range(30)) / sum(10 + i for i in range(30))
    assert first.vwap == pytest.approx(expected_vwap)


def test_provider_serves_only_bars_closed_by_end():
    t0 = datetime(2026, 9, 15, 13, 30, tzinfo=UTC)
    bars = [Bar(start=t0 + timedelta(minutes=30 * i), open=1, high=2, low=0.5, close=1.5, volume=1)
            for i in range(4)]
    provider = otr.HistoricalBarProvider({"SPY": bars})
    got = asyncio.run(provider.fetch_bars(["SPY"], MINUTE_30, t0, t0 + timedelta(minutes=75)))
    # 13:30 and 14:00 closed by 14:45; 14:30 still forming -> withheld.
    assert [b.start_utc for b in got["SPY"]] == [t0, t0 + timedelta(minutes=30)]


def test_provider_refuses_excluded_window_and_other_timeframes():
    provider = otr.HistoricalBarProvider({"SPY": []})
    with pytest.raises(ValueError):
        asyncio.run(provider.fetch_bars(["SPY"], MINUTE_30, otr.STEP0_START, otr.EXCLUDED_FROM))
    from alert_ranker.bar_provider import BarProviderError

    with pytest.raises(BarProviderError):
        asyncio.run(provider.fetch_bars(["SPY"], MINUTE_1, otr.STEP0_START,
                                        otr.STEP0_START + timedelta(hours=1)))


def test_quote_proxy_uses_last_completed_minute():
    t0 = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    market = otr.ReplayMarketData({"AAPL": [_minute(t0, 10.0), _minute(t0 + timedelta(minutes=1), 11.0)]})
    market.now = t0 + timedelta(minutes=1, seconds=30)  # second minute still forming
    snap = asyncio.run(market.fetch_market_snapshot("AAPL"))
    assert snap.price == pytest.approx(10.1)
    with pytest.raises(otr.ReplayChainUnavailable):
        asyncio.run(market.fetch_option_expirations("AAPL"))


def test_pull_start_is_first_bucket_inside_lookback():
    first = datetime(2026, 9, 15, 16, 52, 57, tzinfo=UTC)
    start = otr.pull_start(first)
    assert start == datetime(2026, 9, 5, 17, 0, tzinfo=UTC)


def test_only_v0_variant_is_runnable(tmp_path):
    with pytest.raises(SystemExit):
        otr.main(["step0", "--bars", str(tmp_path / "b"), "--prod", str(tmp_path / "p"),
                  "--out", str(tmp_path / "o"), "--variant", "v1_iex_feed_60s_close_confirm"])


def test_compare_counts_missing_rows_as_disagreement():
    ts = "2026-09-22T14:20:44+00:00"
    prod = [
        {"timestamp": ts, "ticker": "SPY", "source": "scheduled", "pattern": "N/A", "direction": "",
         "reason": "no_setup:sequence_not_212"},
        {"timestamp": ts, "ticker": "QQQ", "source": "scheduled", "pattern": "N/A", "direction": "",
         "reason": "no_setup:sequence_not_212"},
    ]
    replay = [dict(prod[0])]
    out = otr.compare(prod, replay)
    assert out["rows_compared"] == 2 and out["rows_agree"] == 1
    assert out["disagreements_prod_to_replay"] == {"NO_SETUP -> <absent>": 1}


def test_real_scanner_replay_before_session_open_is_offline(tmp_path, monkeypatch):
    for key, value in otr.REPLAY_ENV.items():
        monkeypatch.setenv(key, value)
    day = datetime(2026, 9, 22, 13, 0, tzinfo=UTC)
    minute_bars = {s: [_minute(day - timedelta(days=1) + timedelta(minutes=i), 100.0) for i in range(60)]
                   for s in ("AAPL", "SPY", "QQQ")}
    now = datetime(2026, 9, 22, 13, 40, tzinfo=UTC)  # cutoff 13:24Z < 13:30Z open
    rows = asyncio.run(otr.replay_cycles([(now, ["AAPL"])], minute_bars, [], tmp_path))
    assert rows, "scanner produced no row"
    assert {otr.reason_class(r["reason"]) for r in rows} == {"SESSION_NOT_STARTED"}
    assert all(r["alert_sent"] == 0 for r in rows)


def test_direction_only_differences_agree_at_prereg_granularity():
    ts = "2026-09-22T14:20:44+00:00"
    prod = [{"timestamp": ts, "ticker": "SPY", "source": "scheduled", "pattern": "N/A",
             "direction": "UNKNOWN", "reason": "no_setup:sequence_not_212"}]
    replay = [dict(prod[0], direction="LONG")]
    assert otr.compare(prod, replay)["agreement"] == 0.0
    assert otr.compare(prod, replay, with_direction=False)["agreement"] == 1.0
