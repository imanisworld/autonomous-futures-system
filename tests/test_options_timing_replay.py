from __future__ import annotations

import asyncio
import importlib.util
from datetime import date, datetime, timedelta, timezone
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


# ------------------------------------------------------------ scoring stage

from alert_ranker.session_calendar import nyse_session_for  # noqa: E402


def _flat_minutes(days, price=100.0):
    """1m bars for every regular-session minute of the given days (plus 09:29)."""
    bars = []
    for d in days:
        s = nyse_session_for(d)
        t = s.open - timedelta(minutes=1)
        while t < s.close:
            bars.append(Bar(start=t, open=price, high=price + 0.05, low=price - 0.05,
                            close=price, volume=100.0, vwap=price))
            t += timedelta(minutes=1)
    return bars


def _sessions_before(last, n):
    out, d = [], last
    while len(out) < n:
        if nyse_session_for(d) is not None:
            out.insert(0, d)
        d -= timedelta(days=1)
    return out


def test_minute_series_matches_list_aggregation_and_quote_proxy():
    t0 = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    bars = [_minute(t0 + timedelta(minutes=i), 100 + i, volume=10 + i) for i in range(45)]
    s = otr.MinuteSeries.from_bars(bars)
    got, want = s.to_30m_bars(), otr.aggregate_30m(bars)
    assert [(b.start_utc, b.open, b.high, b.low, b.close, b.volume) for b in got] == \
           [(b.start_utc, b.open, b.high, b.low, b.close, b.volume) for b in want]
    assert got[0].vwap == pytest.approx(want[0].vwap)
    assert s.price_at(t0 + timedelta(minutes=2, seconds=30)) == pytest.approx(bars[1].close)


def test_indexed_provider_is_causal_and_guarded():
    t0 = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    bars = [Bar(start=t0 + timedelta(minutes=30 * i), open=1, high=2, low=0.5, close=1.5, volume=1)
            for i in range(4)]
    provider = otr.IndexedBarProvider({"SPY": bars}, guard_end=otr.SCORE_PULL_END)
    got = asyncio.run(provider.fetch_bars(["SPY"], MINUTE_30, t0, t0 + timedelta(minutes=75)))
    assert [b.start_utc for b in got["SPY"]] == [t0, t0 + timedelta(minutes=30)]
    with pytest.raises(ValueError):
        asyncio.run(provider.fetch_bars(["SPY"], MINUTE_30, t0, otr.SCORE_PULL_END + timedelta(minutes=1)))


def _paths_with(minutes):
    paths = otr.RthPaths({"AAPL": otr.MinuteSeries.from_bars(minutes)})
    paths.data_end = datetime(2026, 9, 1, tzinfo=UTC)
    return paths


def test_outcome_target_stop_same_minute_and_horizon():
    day = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    mk = lambda i, h, l, c: Bar(start=day + timedelta(minutes=i), open=c, high=h, low=l,  # noqa: E731
                                close=c, volume=1, vwap=c)
    paths = _paths_with([mk(0, 100.2, 99.9, 100), mk(1, 102.1, 100, 102), mk(2, 103, 98, 100)])
    r, how = paths.outcome_r("AAPL", "LONG", 100.0, 99.0, 102.0, day)
    assert (r, how) == (2.0, "target")
    paths = _paths_with([mk(0, 100.2, 99.9, 100), mk(1, 103, 98, 100)])
    assert paths.outcome_r("AAPL", "LONG", 100.0, 99.0, 102.0, day) == (-1.0, "stop")  # shared minute
    paths = _paths_with([mk(0, 100.2, 99.9, 100), mk(1, 100.6, 100, 100.5)])
    r, how = paths.outcome_r("AAPL", "SHORT", 100.0, 101.0, 98.0, day)
    assert r == pytest.approx(-0.5) and how in ("horizon", "data_end")


def test_first_cross_and_target_touch_use_completed_minutes():
    day = datetime(2026, 8, 3, 13, 30, tzinfo=UTC)
    mins = [Bar(start=day + timedelta(minutes=i), open=100, high=100 + i * 0.5, low=99.5, close=100,
                volume=1, vwap=100) for i in range(6)]
    paths = _paths_with(mins)
    assert paths.first_cross("AAPL", "LONG", 101.0, day, day + timedelta(minutes=10)) == day + timedelta(minutes=2)
    assert paths.first_cross("AAPL", "LONG", 101.0, day, day + timedelta(minutes=2, seconds=30)) is None
    assert paths.touched_between("AAPL", "LONG", 102.0, day, day + timedelta(minutes=5))
    assert not paths.touched_between("AAPL", "LONG", 102.0, day, day + timedelta(minutes=4))


def test_episodes_first_detection_and_first_eligible():
    base = {"ticker": "AAPL", "source": "scheduled", "pattern": "strat_212", "direction": "LONG",
            "alert_sent": 0, "setup_timeframe": "30m", "setup_type": "STRAT_212_CONTINUATION",
            "setup_direction": "CALL", "setup_entry_trigger": 101.0, "underlying_invalidation": 99.0,
            "target_1": 104.0, "setup_status": "TRIGGERED", "counterfactual_observer": False}
    recs = [
        {**base, "timestamp": "2026-08-03T14:46:00+00:00", "price": 103.5,
         "reason": "entry_late_counterfactual_only:remaining_rr_0.11_below_1.00", "counterfactual_observer": True},
        {**base, "timestamp": "2026-08-03T15:01:00+00:00", "price": 101.5,
         "reason": "DATA_INVALID:expiration_fetch_error:ReplayChainUnavailable"},
        {**base, "timestamp": "2026-08-03T15:02:00+00:00", "price": 101.5, "setup_timeframe": "1H",
         "reason": "DATA_INVALID:expiration_fetch_error:ReplayChainUnavailable"},
        {**base, "timestamp": "2026-08-03T15:03:00+00:00", "price": 101.5,
         "reason": "counterfactual_observer_only", "counterfactual_observer": True},
    ]
    eps = otr.episodes_from_records(recs)
    assert len(eps) == 1
    ep = eps[0]
    assert ep["first"]["cls"] == "ENTRY_LATE_FIRST"
    assert ep["first_eligible"]["time"] == datetime(2026, 8, 3, 15, 1, tzinfo=UTC)


def _metrics(eligible, rr=1.5, tbd=0.0, fp=0.0, exp=0.2, null=0.1):
    return {"eligible_episodes": eligible, "r_remaining_at_first_detection": {"median": rr},
            "target_before_detection_rate": tbd, "false_positive_rate": fp,
            "underlying_structural_expectancy_R": exp, "null_p95_expectancy_R": null}


def test_classification_thresholds():
    v0 = {"H1": _metrics(10, exp=0.1), "H2": _metrics(10, exp=0.1)}
    good = {"H1": _metrics(40), "H2": _metrics(40)}
    assert otr.classify_variant(otr.VARIANTS[3], good, v0)["verdict"] == "SUPPORTS"
    for bad in (
        {"H1": _metrics(19), "H2": _metrics(41)},            # < 2x v0 in H1
        {"H1": _metrics(25), "H2": _metrics(25, fp=0.2)},    # FP > 15 %
        {"H1": _metrics(40, rr=0.9), "H2": _metrics(40)},    # median RR < 1
        {"H1": _metrics(40, tbd=0.11), "H2": _metrics(40)},  # target before detection > 10 %
        {"H1": _metrics(40, exp=0.04), "H2": _metrics(40)},  # below v0 - 0.05
        {"H1": _metrics(40, exp=0.2, null=0.3), "H2": _metrics(40)},  # not above null p95
        {"H1": _metrics(29), "H2": _metrics(30)},            # total < 60
    ):
        assert otr.classify_variant(otr.VARIANTS[3], bad, v0)["verdict"] == "NO IMPROVEMENT"
    # v2 is not held to the false-positive check
    fp_heavy = {"H1": _metrics(40, fp=0.9), "H2": _metrics(40, fp=0.9)}
    assert otr.classify_variant(otr.VARIANTS[2], fp_heavy, v0)["verdict"] == "SUPPORTS"


def _thirty(day_list, price=100.0):
    bars = []
    for d in day_list:
        s = nyse_session_for(d)
        t = s.open
        while t < s.close:
            bars.append(Bar(start=t, open=price, high=price + 0.5, low=price - 0.5, close=price, volume=100.0,
                            vwap=price))
            t += timedelta(minutes=30)
    return bars


def _v3_fixture(live_price):
    from alert_ranker.bar_context import BarContextBuilder  # noqa: F401

    days = _sessions_before(date(2026, 8, 28), 12)
    today = nyse_session_for(days[-1])
    prior = _thirty(days[:-1])
    two_back = Bar(start=today.open, open=100, high=101.5, low=99.8, close=101.2, volume=100.0, vwap=100.6)
    inside = Bar(start=today.open + timedelta(minutes=30), open=101, high=101.0, low=100.2, close=100.8,
                 volume=100.0, vwap=100.6)
    bars = {s: prior + [two_back, inside] for s in ("AAPL", "SPY", "QQQ")}
    slot = inside.start_utc + timedelta(minutes=30)
    now = slot + timedelta(minutes=1, seconds=45)
    sip = otr.MinuteSeries.from_bars([Bar(start=slot, open=live_price, high=live_price, low=live_price,
                                          close=live_price, volume=10, vwap=live_price)])
    cls = otr._intrabar_builder_cls()
    builder = cls(provider=otr.IndexedBarProvider(bars, guard_end=otr.SCORE_PULL_END),
                  calendar=otr.LocalCalendar(), timeframe=MINUTE_30, delay_buffer=otr.IEX_BUFFER,
                  lookback_days=10)
    builder._v3 = {"symbol": "AAPL", "now": now, "sip": sip, "iex": None}
    return builder, now, slot, inside


def test_v3_intrabar_bar_only_after_live_cross_of_completed_inside_bar():
    builder, now, slot, inside = _v3_fixture(live_price=101.4)
    ctx = asyncio.run(builder.build("AAPL", now)).ticker
    assert ctx.last_bar_start == slot.isoformat()          # synthetic breakout bar appended
    assert ctx.setup_entry_trigger == pytest.approx(inside.high)
    builder, now, slot, inside = _v3_fixture(live_price=100.6)  # still inside the inside bar
    ctx = asyncio.run(builder.build("AAPL", now)).ticker
    assert ctx.last_bar_start == inside.start_utc.isoformat()


def test_v2_daily_candle_gets_live_prices_and_prior_session_context(tmp_path, monkeypatch):
    for key, value in otr.REPLAY_ENV.items():
        monkeypatch.setenv(key, value)
    from alert_ranker.bar_context import BarContextBuilder
    from alert_ranker.config import load_config

    days = _sessions_before(date(2026, 8, 28), 12)
    today = nyse_session_for(days[-1])
    bars = {s: _thirty(days[:-1]) for s in ("AAPL", "SPY", "QQQ")}
    sip = {"AAPL": otr.MinuteSeries.from_bars(_flat_minutes([days[-1]], price=103.0))}
    cls = otr.build_scanner_class(otr.VARIANTS[2], sip=sip)
    captured = {}

    def spy_candidate(self, builder, bars_arg, session, required, cutoff, ticker_ctx, spy, qqq):
        captured.update(bars=bars_arg, session=session, cutoff=cutoff, spy=spy)
        return None

    monkeypatch.setattr(cls, "_daily_candidate_from_raw", spy_candidate)
    builder = BarContextBuilder(provider=otr.IndexedBarProvider(bars, guard_end=otr.SCORE_PULL_END),
                                calendar=otr.LocalCalendar(), timeframe=MINUTE_30,
                                delay_buffer=otr.DELAY_BUFFER, lookback_days=10)
    from alert_ranker.storage import ScanStorage

    cfg = load_config()
    scanner = cls(config=cfg, market_data=otr.SeriesMarketData(sip), storage=ScanStorage(tmp_path / "s.sqlite"),
                  discord=None, signa_client=None, bar_context=builder)
    now = today.open + timedelta(minutes=5, seconds=45)   # 09:35:45 ET: v0 sees no session bar
    asyncio.run(scanner._v2_daily("AAPL", now))
    synth = captured["bars"][-1]
    assert synth.start_utc == today.open and synth.high == pytest.approx(103.0)
    assert captured["cutoff"] == today.open + timedelta(minutes=30)
    assert captured["spy"].available and captured["spy"].session_date == days[-2].isoformat()


def test_scoring_replay_smoke_all_variants(tmp_path, monkeypatch):
    for key, value in otr.REPLAY_ENV.items():
        monkeypatch.setenv(key, value)
    days = _sessions_before(date(2026, 8, 28), 12)
    minutes = _flat_minutes(days)
    series = otr.MinuteSeries.from_bars(minutes)
    for feed in ("sip", "iex"):
        for sym in otr.SYMBOLS:
            otr.save_series(tmp_path / "data" / feed / f"{sym}.npz", series)
    session = nyse_session_for(days[-1])
    for variant in otr.VARIANTS:
        out = tmp_path / f"{variant}.jsonl"
        summary = asyncio.run(otr.replay_scoring_variant(
            variant, tmp_path / "data", out, tmp_path / variant, sessions=[session]))
        assert summary["rows"] > 0
        classes = {otr.reason_class(__import__("json").loads(line)["reason"]) for line in out.read_text().splitlines()}
        assert "BAR_CONTEXT_OTHER" not in classes, (variant, classes)


def test_scoring_refuses_unknown_variant(tmp_path):
    with pytest.raises(SystemExit):
        asyncio.run(otr.replay_scoring_variant("v9", tmp_path, tmp_path / "o.jsonl", tmp_path))


def _day_bars(d, highs_lows_closes):
    s = nyse_session_for(d)
    out, t = [], s.open
    for h, l, c in highs_lows_closes:
        out.append(Bar(start=t, open=c, high=h, low=l, close=c, volume=100.0, vwap=c))
        t += timedelta(minutes=30)
    return out


def _const_day(d, h, l, c):
    return _day_bars(d, [(h, l, c)] * 13)


def test_v2_detects_open_gap_daily_322_that_v0_cannot_see(tmp_path, monkeypatch):
    for key, value in otr.REPLAY_ENV.items():
        monkeypatch.setenv(key, value)
    import httpx

    from alert_ranker.bar_context import BarContextBuilder
    from alert_ranker.config import load_config
    from alert_ranker.discord import DiscordAlerter
    from alert_ranker.storage import ScanStorage

    days = _sessions_before(date(2026, 8, 28), 12)
    *flat, d_a, d_b, d1, d2, d3, today = days
    aapl = [b for d in flat for b in _const_day(d, 100.5, 99.5, 100)]
    aapl += _const_day(d_a, 115, 105, 110) + _const_day(d_b, 110, 100, 105)
    aapl += _const_day(d1, 101, 99, 100) + _const_day(d2, 102, 98, 100) + _const_day(d3, 103, 99, 102)
    rising = [(100 + i * 0.5 + 0.2, 100 + i * 0.5 - 0.2, 100 + i * 0.5) for i in range(13)]
    idx = [b for d in days[:-2] for b in _const_day(d, 100.5, 99.5, 100)] + _day_bars(d3, rising)
    bars = {"AAPL": aapl, "SPY": idx, "QQQ": idx}
    sip = {"AAPL": otr.MinuteSeries.from_bars(_flat_minutes([today], price=104.0))}
    session = nyse_session_for(today)
    now = session.open + timedelta(seconds=45)

    async def run(variant):
        cfg = load_config()
        from dataclasses import replace as dc_replace

        cfg = dc_replace(cfg, watchlist=["AAPL"])
        storage = ScanStorage(tmp_path / f"{variant}.sqlite")
        builder = BarContextBuilder(provider=otr.IndexedBarProvider(bars, guard_end=otr.SCORE_PULL_END),
                                    calendar=otr.LocalCalendar(), timeframe=MINUTE_30,
                                    delay_buffer=otr.DELAY_BUFFER, lookback_days=10,
                                    exchange_timezone=cfg.timezone)
        market = otr.SeriesMarketData(sip)
        market.now = now
        async with httpx.AsyncClient(transport=otr._refusing_transport()) as client:
            cls = otr.build_scanner_class(variant, sip=sip)
            scanner = cls(config=cfg, market_data=market, storage=storage,
                          discord=DiscordAlerter(cfg, storage, client=client),
                          signa_client=None, bar_context=builder)
            await scanner.scan_watchlist(source="scheduled", now=now)
        return otr._extract_and_prune(storage)

    v0 = asyncio.run(run(otr.VARIANTS[0]))
    assert {otr.reason_class(r["reason"]) for r in v0} == {"SESSION_NOT_STARTED"}
    v2 = asyncio.run(run(otr.VARIANTS[2]))
    daily = [r for r in v2 if str(r.get("setup_timeframe")).upper() == "1D"]
    assert daily, v2
    assert otr.reason_class(daily[0]["reason"]) == "PAST_LATE_GATE", daily[0]
    assert daily[0]["setup_type"] == "DAILY_322_CONTINUATION"
    eps = otr.episodes_from_records(v2)
    assert len(eps) == 1 and eps[0]["first_eligible"] is not None


def test_v3_detects_forming_30m_breakout_that_v0_and_v1_cannot(tmp_path, monkeypatch):
    for key, value in otr.REPLAY_ENV.items():
        monkeypatch.setenv(key, value)
    import httpx
    from dataclasses import replace as dc_replace

    from alert_ranker.bar_context import BarContextBuilder
    from alert_ranker.config import load_config
    from alert_ranker.discord import DiscordAlerter
    from alert_ranker.storage import ScanStorage

    days = _sessions_before(date(2026, 8, 28), 12)
    *flat, d_a, d_b, d2, d3, today = days
    session = nyse_session_for(today)
    aapl = [b for d in flat for b in _const_day(d, 100.5, 99.5, 100)]
    aapl += _const_day(d_a, 115, 105, 110) + _const_day(d_b, 110, 100, 105)
    aapl += _const_day(d2, 102, 98, 100) + _const_day(d3, 103, 99, 102)
    aapl += [Bar(start=session.open, open=102, high=104.0, low=101.0, close=103.5, volume=100.0, vwap=103.0),
             Bar(start=session.open + timedelta(minutes=30), open=103.5, high=103.8, low=101.5, close=103.6,
                 volume=100.0, vwap=103.0)]
    idx = [b for d in days[:-1] for b in _const_day(d, 100.5, 99.5, 100)]
    idx += [Bar(start=session.open, open=100, high=101.2, low=100.8, close=101, volume=100.0, vwap=101),
            Bar(start=session.open + timedelta(minutes=30), open=101, high=102.2, low=101.8, close=102,
                volume=100.0, vwap=102)]
    bars = {"AAPL": aapl, "SPY": idx, "QQQ": idx}
    slot = session.open + timedelta(minutes=60)
    now = slot + timedelta(minutes=1, seconds=45)
    live = [Bar(start=slot, open=104.5, high=104.5, low=104.5, close=104.5, volume=10, vwap=104.5)]
    sip = {"AAPL": otr.MinuteSeries.from_bars(live)}

    async def run(variant):
        cfg = dc_replace(load_config(), watchlist=["AAPL"])
        storage = ScanStorage(tmp_path / f"{variant}.sqlite")

        def mk(buffer, cls=BarContextBuilder):
            return cls(provider=otr.IndexedBarProvider(bars, guard_end=otr.SCORE_PULL_END),
                       calendar=otr.LocalCalendar(), timeframe=MINUTE_30, delay_buffer=buffer,
                       lookback_days=10, exchange_timezone=cfg.timezone)

        main = mk(otr.IEX_BUFFER) if variant == otr.VARIANTS[1] else mk(otr.DELAY_BUFFER)
        v3b = mk(otr.IEX_BUFFER, otr._intrabar_builder_cls())
        market = otr.SeriesMarketData(sip)
        market.now = now
        async with httpx.AsyncClient(transport=otr._refusing_transport()) as client:
            cls = otr.build_scanner_class(variant, sip=sip, iex={}, v3_builder=v3b)
            scanner = cls(config=cfg, market_data=market, storage=storage,
                          discord=DiscordAlerter(cfg, storage, client=client),
                          signa_client=None, bar_context=main)
            await scanner.scan_watchlist(source="scheduled", now=now)
        return otr._extract_and_prune(storage)

    def primary(rows):
        return next(r for r in rows if r["source"] == "scheduled")

    inside_start = (session.open + timedelta(minutes=30)).isoformat()
    for variant in (otr.VARIANTS[0], otr.VARIANTS[1]):
        row = primary(asyncio.run(run(variant)))
        assert row["latest_completed_bar_start"] != slot.isoformat(), variant
        assert otr.reason_class(row["reason"]) not in ("SETUP_PROOF_INCOMPLETE", "PAST_LATE_GATE"), (variant, row)
    v1_row = primary(asyncio.run(run(otr.VARIANTS[1])))
    assert v1_row["latest_completed_bar_start"] == inside_start
    v3_row = primary(asyncio.run(run(otr.VARIANTS[3])))
    # The forming breakout bar is now the latest bar and production confirmed the
    # 2-1-2 sequence (the fixture's market alignment then decides eligibility,
    # through the same production promotion as every other variant).
    assert v3_row["latest_completed_bar_start"] == slot.isoformat()
    assert otr.reason_class(v3_row["reason"]) in ("SETUP_PROOF_INCOMPLETE", "PAST_LATE_GATE", "ENTRY_LATE_FIRST")
    assert v3_row["setup_entry_trigger"] == pytest.approx(103.8)
