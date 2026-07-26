"""
tests/test_replay_engine.py

Offline replay coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from replay import ReplayCandleLoader, ReplayEngine
from replay.__main__ import main as replay_cli_main
from replay.manifest import ReplayManifest


def test_candle_loader_reads_sample_day():
    candles = ReplayCandleLoader().load_jsonl("data/replay/sample_day_mnq.jsonl")

    assert len(candles) == 3
    assert candles[0].instrument == "MNQ"
    assert candles[0].session == "new_york"
    assert candles[0].price_vs_vwap == "above"


def test_replay_engine_runs_sample_day(config, tmp_path):
    config.log_dir = str(tmp_path)
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        "data/replay/sample_day_mnq.jsonl",
        review_date="2026-05-23",
    )

    assert report.candles_processed == 3
    assert report.approved_trades == 1
    assert report.wins == 1
    assert report.losses == 0
    assert report.open_trades == 0
    assert report.realized_pnl_dollars > 0
    assert Path(report.journal_path).exists()
    assert Path(report.review_path).exists()
    assert (tmp_path / "replay_report_2026-05-23.md").exists()


def test_replay_stops_after_max_trades(config, tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    candles = []
    for i in range(6):
        c = dict(base)
        c["timestamp"] = (base_dt + timedelta(minutes=i)).isoformat()
        # High well above any target → guarantees WIN on every resolution bar.
        c["high"] = 99999.0
        candles.append(json.dumps(c))

    replay_path = tmp_path / "many_trades.jsonl"
    replay_path.write_text("\n".join(candles) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 3
    assert report.stopped_reason == "max_trades_per_day"


def test_replay_generates_no_trade_for_choppy_day(config, tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle["market_condition"] = "CHOPPY"
    replay_path = tmp_path / "choppy.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 0
    assert report.no_trades == 1
    assert report.wins == 0


def test_replay_populates_window_direction_for_effective_condition_parity(
    config, tmp_path, monkeypatch
):
    """Effective-parity regression, not raw-parity: live (webhook/runner.py)
    sets state.window_direction from the last 6 recorded bars right after
    ingesting each bar, and DecisionEngine._has_directional_structure uses it
    to veto a CHOPPY label into RANGE_BOUND when no other signal (a 3-bar
    Pine strat run) independently confirms the direction. Before this fix,
    replay never populated window_direction — a live-only field — so this
    veto path could never fire in replay even when replay correctly
    reconstructed Pine's raw CHOPPY label. This proves DecisionEngine
    actually receives the same effective state in both lanes, not just that
    the raw market_condition string round-trips."""
    from dataclasses import replace as dc_replace
    from datetime import datetime, timedelta

    from strategy.signal_engine import DecisionEngine

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])
    # Six bars with closes decisively higher each step (5/5 up, positive net)
    # — exactly the BarHistory.window_direction "UP" case. No current_bar_type
    # / previous_bar_high fields are set, so _strat_context_from_candle
    # returns None: the strat-run confirmation path cannot fire here, so
    # window_direction is the ONLY thing that can trigger the veto.
    closes = [19480.0, 19485.0, 19490.0, 19495.0, 19500.0, 19505.0]
    candles = []
    for i, close in enumerate(closes):
        c = dict(base)
        for key in (
            "current_bar_type", "previous_bar_type", "two_bars_back_type",
            "strat_sequence", "strat_trigger", "strat_direction",
            "previous_bar_high", "previous_bar_low",
        ):
            c.pop(key, None)
        c["timestamp"] = (base_dt + timedelta(minutes=15 * i)).isoformat()
        c["open"] = close - 2.0
        c["high"] = close + 1.0
        c["low"] = close - 3.0
        c["close"] = close
        c["market_condition"] = "CHOPPY"
        c["trend_direction"] = "UP"
        c["trend_strength"] = "WEAK"
        c["orb_status"] = "inside"
        c["timeframe"] = "15"  # the primary decision timeframe (config.expected_timeframe_minutes)
        candles.append(json.dumps(c))

    replay_path = tmp_path / "window_direction.jsonl"
    replay_path.write_text("\n".join(candles) + "\n")

    seen_states = []
    original_evaluate = DecisionEngine.evaluate

    def _spy(self, state, daily_state):
        seen_states.append(state)
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _spy)

    ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path, review_date="2026-05-23",
    )

    assert len(seen_states) == 6
    last_state = seen_states[-1]
    assert last_state.strat is None  # confirms only window_direction could veto
    assert last_state.window_direction == "UP"

    engine = DecisionEngine(config=config)
    assert engine._has_directional_structure(last_state) is True
    assert engine._score_market_condition(last_state) == "RANGE_BOUND"

    # Causal proof: strip window_direction back to the pre-fix value (always
    # None) and confirm the veto no longer fires — isolates this fix's effect
    # from any other confluence in the fixture.
    pre_fix_state = dc_replace(last_state, window_direction=None)
    assert engine._has_directional_structure(pre_fix_state) is False
    assert engine._score_market_condition(pre_fix_state) == "CHOPPY"


def test_replay_window_direction_spans_run_many_day_boundary(config, tmp_path, monkeypatch):
    """Cross-day regression: live's window_direction can span up to 3 days
    (BarHistory.recent(..., lookback_days=3)), so the FIRST bar of a new
    trading day can still complete its 6-bar window from the prior day's
    tail. run_many()/run_manifest() call run() once per file/day, and run()
    intentionally clears _research_bars every call (shadow-setup history
    must never leak across days) — window_direction therefore needs its own
    history that survives across run() calls, separate from _research_bars.
    Day 1 supplies 5 bars of a decisive uptrend; day 2's only (first) bar
    completes the 6th step. If window_direction reset per file/day like
    _research_bars does, day 2's first bar would see only 1 close (itself)
    and window_direction would be None instead of "UP"."""
    from datetime import datetime, timedelta, timezone

    from strategy.signal_engine import DecisionEngine

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])

    def _candle(close: float, ts: datetime) -> dict:
        c = dict(base)
        for key in (
            "current_bar_type", "previous_bar_type", "two_bars_back_type",
            "strat_sequence", "strat_trigger", "strat_direction",
            "previous_bar_high", "previous_bar_low",
        ):
            c.pop(key, None)
        c["timestamp"] = ts.isoformat()
        c["open"] = close - 2.0
        c["high"] = close + 1.0
        c["low"] = close - 3.0
        c["close"] = close
        c["market_condition"] = "CHOPPY"
        c["trend_direction"] = "UP"
        c["trend_strength"] = "WEAK"
        c["orb_status"] = "inside"
        c["timeframe"] = "15"  # the primary decision timeframe (config.expected_timeframe_minutes)
        return c

    day1_dt = datetime(2026, 5, 22, 14, 30, tzinfo=timezone.utc)
    day1_closes = [19480.0, 19485.0, 19490.0, 19495.0, 19500.0]
    day1_candles = [
        _candle(close, day1_dt + timedelta(minutes=15 * i))
        for i, close in enumerate(day1_closes)
    ]
    day1_path = tmp_path / "day1.jsonl"
    day1_path.write_text("\n".join(json.dumps(c) for c in day1_candles) + "\n")

    day2_dt = datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc)
    day2_path = tmp_path / "day2.jsonl"
    day2_path.write_text(json.dumps(_candle(19505.0, day2_dt)) + "\n")

    seen_states = []
    original_evaluate = DecisionEngine.evaluate

    def _spy(self, state, daily_state):
        seen_states.append(state)
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _spy)

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    engine.run_many([day1_path, day2_path])

    # 5 states from day1 + 1 (day2's only bar) = 6.
    assert len(seen_states) == 6
    day2_state = seen_states[-1]
    assert day2_state.strat is None
    assert day2_state.window_direction == "UP"

    checker = DecisionEngine(config=config)
    assert checker._score_market_condition(day2_state) == "RANGE_BOUND"


def test_replay_window_direction_ignores_bars_older_than_the_live_lookback(
    config, tmp_path, monkeypatch
):
    """The cross-day fix above must not become an unbounded-history bug of
    its own. BarHistory.recent(..., lookback_days=3) walks back at most 3
    calendar-day FILES from the current bar's own date — live could not use
    a bar 10 days old to seed window_direction no matter how much history
    exists. Day 1 supplies the same 5-bar uptrend as the cross-day test, but
    day 2 is scheduled 10 days later — far outside the 3-day lookback. Only
    day 2's own single bar should be eligible, which is too few (<4 closes)
    for BarHistory.window_direction to resolve — it must be None, not "UP"."""
    from datetime import datetime, timedelta, timezone

    from strategy.signal_engine import DecisionEngine

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])

    def _candle(close: float, ts: datetime) -> dict:
        c = dict(base)
        for key in (
            "current_bar_type", "previous_bar_type", "two_bars_back_type",
            "strat_sequence", "strat_trigger", "strat_direction",
            "previous_bar_high", "previous_bar_low",
        ):
            c.pop(key, None)
        c["timestamp"] = ts.isoformat()
        c["open"] = close - 2.0
        c["high"] = close + 1.0
        c["low"] = close - 3.0
        c["close"] = close
        c["market_condition"] = "CHOPPY"
        c["trend_direction"] = "UP"
        c["trend_strength"] = "WEAK"
        c["orb_status"] = "inside"
        c["timeframe"] = "15"
        return c

    day1_dt = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)
    day1_closes = [19480.0, 19485.0, 19490.0, 19495.0, 19500.0]
    day1_candles = [
        _candle(close, day1_dt + timedelta(minutes=15 * i))
        for i, close in enumerate(day1_closes)
    ]
    day1_path = tmp_path / "day1.jsonl"
    day1_path.write_text("\n".join(json.dumps(c) for c in day1_candles) + "\n")

    day2_dt = datetime(2026, 5, 11, 14, 30, tzinfo=timezone.utc)  # 10 days later
    day2_path = tmp_path / "day2.jsonl"
    day2_path.write_text(json.dumps(_candle(19505.0, day2_dt)) + "\n")

    seen_states = []
    original_evaluate = DecisionEngine.evaluate

    def _spy(self, state, daily_state):
        seen_states.append(state)
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _spy)

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    engine.run_many([day1_path, day2_path])

    assert len(seen_states) == 6
    day2_state = seen_states[-1]
    assert day2_state.window_direction is None


def test_replay_window_direction_ignores_future_bars_run_out_of_order(
    config, tmp_path, monkeypatch
):
    """A negative day-delta must not pass the age filter. BarHistory.recent
    only ever walks BACKWARD from the current bar's own date — it can never
    read a file for a date after the one it's asked about. If a ReplayEngine
    instance processes a LATER day first (e.g. run_many given files out of
    order) and an EARLIER day second, the later day's bars sit in
    _window_direction_bars with a date after the earlier day's current date:
    (earlier - later).days is negative, and an unbounded `< 3` check treats
    every negative number as "recent enough" — exactly backwards. The
    earlier day's only bar must resolve window_direction to None (too few
    real bars), not "UP" leaked in from the future day run before it."""
    from datetime import datetime, timedelta, timezone

    from strategy.signal_engine import DecisionEngine

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])

    def _candle(close: float, ts: datetime) -> dict:
        c = dict(base)
        for key in (
            "current_bar_type", "previous_bar_type", "two_bars_back_type",
            "strat_sequence", "strat_trigger", "strat_direction",
            "previous_bar_high", "previous_bar_low",
        ):
            c.pop(key, None)
        c["timestamp"] = ts.isoformat()
        c["open"] = close - 2.0
        c["high"] = close + 1.0
        c["low"] = close - 3.0
        c["close"] = close
        c["market_condition"] = "CHOPPY"
        c["trend_direction"] = "UP"
        c["trend_strength"] = "WEAK"
        c["orb_status"] = "inside"
        c["timeframe"] = "15"
        return c

    later_dt = datetime(2026, 7, 2, 14, 30, tzinfo=timezone.utc)
    later_closes = [19480.0, 19485.0, 19490.0, 19495.0, 19500.0]
    later_candles = [
        _candle(close, later_dt + timedelta(minutes=15 * i))
        for i, close in enumerate(later_closes)
    ]
    later_path = tmp_path / "later.jsonl"
    later_path.write_text("\n".join(json.dumps(c) for c in later_candles) + "\n")

    earlier_dt = datetime(2026, 7, 1, 14, 30, tzinfo=timezone.utc)
    earlier_path = tmp_path / "earlier.jsonl"
    earlier_path.write_text(json.dumps(_candle(19505.0, earlier_dt)) + "\n")

    seen_states = []
    original_evaluate = DecisionEngine.evaluate

    def _spy(self, state, daily_state):
        seen_states.append(state)
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _spy)

    # Run the LATER day first, then the EARLIER day — out of chronological
    # order, same ReplayEngine instance (as run_many/run_manifest would do
    # if given misordered paths).
    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    engine.run_many([later_path, earlier_path])

    assert len(seen_states) == 6
    earlier_state = seen_states[-1]
    assert earlier_state.window_direction is None


def test_replay_window_direction_ignores_interleaved_5m_candles(
    config, tmp_path, monkeypatch
):
    """webhook/runner.py only records into BarHistory (and hence
    window_direction) on the authoritative decision timeframe — a
    5-minute-trigger or canonical-4HR 5-minute-native bar never reaches that
    block (`if not five_min_trigger and not four_hr_five_min`). A replay
    stream with 15m and 5m candles interleaved for the same instrument must
    reproduce that exclusion: the 5m bars carry wildly different closes here
    (opposite direction, if they leaked in they would break the 15m uptrend
    read), so a passing window_direction == "UP" on the final 15m bar proves
    they were never admitted."""
    from datetime import datetime, timedelta, timezone

    from strategy.signal_engine import DecisionEngine

    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])

    def _candle(close: float, ts: datetime, timeframe: str) -> dict:
        c = dict(base)
        for key in (
            "current_bar_type", "previous_bar_type", "two_bars_back_type",
            "strat_sequence", "strat_trigger", "strat_direction",
            "previous_bar_high", "previous_bar_low",
        ):
            c.pop(key, None)
        c["timestamp"] = ts.isoformat()
        c["open"] = close - 2.0
        c["high"] = close + 1.0
        c["low"] = close - 3.0
        c["close"] = close
        c["market_condition"] = "CHOPPY"
        c["trend_direction"] = "UP"
        c["trend_strength"] = "WEAK"
        c["orb_status"] = "inside"
        c["timeframe"] = timeframe
        return c

    base_dt = datetime(2026, 5, 23, 14, 30, tzinfo=timezone.utc)
    fifteen_min_closes = [19480.0, 19485.0, 19490.0, 19495.0, 19500.0, 19505.0]
    candles = []
    for i, close in enumerate(fifteen_min_closes):
        ts = base_dt + timedelta(minutes=15 * i)
        candles.append(_candle(close, ts, "15"))
        if i < len(fifteen_min_closes) - 1:
            # Noise: a 5m context bar between each pair of 15m bars, closing
            # at a wildly different, opposite-direction price. If it leaked
            # into window_direction's input, the "UP" read below would break.
            candles.append(_candle(10000.0, ts + timedelta(minutes=10), "5"))

    replay_path = tmp_path / "interleaved.jsonl"
    replay_path.write_text("\n".join(json.dumps(c) for c in candles) + "\n")

    seen_states = []
    original_evaluate = DecisionEngine.evaluate

    def _spy(self, state, daily_state):
        seen_states.append(state)
        return original_evaluate(self, state, daily_state)

    monkeypatch.setattr(DecisionEngine, "evaluate", _spy)

    ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path, review_date="2026-05-23",
    )

    # 6 fifteen-min bars + 5 interleaved five-min bars = 11 states.
    assert len(seen_states) == 11
    last_state = seen_states[-1]
    assert last_state.ohlc.timeframe == "15"
    assert last_state.window_direction == "UP"


def test_one_week_replay_survival_harness(config, tmp_path):
    paths = sorted(Path("data/replay/week").glob("*.jsonl"))

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_many(paths)

    assert report.days == 5
    assert report.survival_passed is True
    assert report.open_trades == 0
    assert report.approved_trades <= 15
    assert report.candles_processed > 0
    assert (tmp_path / "logs" / "multi_day_replay_report.md").exists()


# ---------------------------------------------------------------------------
# Phase 2B hardening: candle loader edge cases
# ---------------------------------------------------------------------------

def test_candle_loader_rejects_duplicate_timestamp(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    dup = tmp_path / "dup.jsonl"
    dup.write_text(json.dumps(base) + "\n" + json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="duplicate timestamp"):
        ReplayCandleLoader().load_jsonl(dup)


def test_candle_loader_rejects_high_below_low(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base["high"] = base["low"] - 1.0  # high < low → invalid
    bad = tmp_path / "bad_ohlc.jsonl"
    bad.write_text(json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="high < low"):
        ReplayCandleLoader().load_jsonl(bad)


def test_candle_loader_rejects_close_outside_range(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base["close"] = base["high"] + 10.0  # close > high → invalid
    bad = tmp_path / "bad_close.jsonl"
    bad.write_text(json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="close outside high/low"):
        ReplayCandleLoader().load_jsonl(bad)


def test_candle_loader_rejects_nonchronological(tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    c1 = dict(base)
    c2 = dict(base)
    c1["timestamp"] = (base_dt + timedelta(minutes=5)).isoformat()
    c2["timestamp"] = base_dt.isoformat()  # earlier than c1 → out of order

    bad = tmp_path / "unordered.jsonl"
    bad.write_text(json.dumps(c1) + "\n" + json.dumps(c2) + "\n")

    with pytest.raises(ValueError, match="not sorted by timestamp"):
        ReplayCandleLoader().load_jsonl(bad)


def test_candle_loader_rejects_mixed_instruments(tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    c1 = dict(base)
    c2 = dict(base)
    c2["timestamp"] = (base_dt + timedelta(minutes=5)).isoformat()
    c2["instrument"] = "MES"  # different instrument

    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(json.dumps(c1) + "\n" + json.dumps(c2) + "\n")

    with pytest.raises(ValueError, match="mixed instruments"):
        ReplayCandleLoader().load_jsonl(mixed)


def test_candle_loader_allows_mixed_instruments_when_flag_set(tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    c1 = dict(base)
    c2 = dict(base)
    c2["timestamp"] = (base_dt + timedelta(minutes=5)).isoformat()
    c2["instrument"] = "MES"

    mixed = tmp_path / "mixed_ok.jsonl"
    mixed.write_text(json.dumps(c1) + "\n" + json.dumps(c2) + "\n")

    candles = ReplayCandleLoader().load_jsonl(mixed, allow_mixed_instruments=True)
    assert len(candles) == 2
    assert {c.instrument for c in candles} == {"MNQ", "MES"}


def test_candle_loader_rejects_missing_required_field(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    del base["vwap"]
    bad = tmp_path / "missing_field.jsonl"
    bad.write_text(json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="missing fields"):
        ReplayCandleLoader().load_jsonl(bad)


# ---------------------------------------------------------------------------
# Phase 2B hardening: session blocking
# ---------------------------------------------------------------------------

def test_asian_session_produces_no_trade(config, tmp_path):
    """day_4 contains only an asian-session candle; risk engine must block it."""
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        "data/replay/week/day_4_asian_disabled.jsonl",
        review_date="2026-05-21",
    )

    assert report.approved_trades == 0
    assert report.wins == 0
    assert report.losses == 0


def test_session_cutoff_blocks_trade_at_1145_et(config, tmp_path):
    """A candle at 11:45 AM ET must be rejected by the session cutoff (11:30 ET)."""
    import dataclasses

    cfg = dataclasses.replace(config, session_cutoffs={"new_york": "11:30"})

    # 11:45 ET = 15:45 UTC (May = EDT = UTC-4)
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle["timestamp"] = "2026-05-23T15:45:00+00:00"
    candle["trend_strength"] = "STRONG"
    candle["volume"] = 5000  # relative = 5000/3800 ≈ 1.31 — above 0.8
    candle["price_vs_vwap"] = "above"

    replay_path = tmp_path / "cutoff_1145.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=cfg, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 0, (
        "Trade at 11:45 ET should be blocked by 11:30 session cutoff"
    )
    rows = [
        json.loads(line)
        for line in Path(report.journal_path).read_text().splitlines()
        if line.strip()
    ]
    decision_rows = [row for row in rows if row.get("decision")]
    outcome_rows = [row for row in rows if row.get("type") == "OUTCOME"]
    assert len(decision_rows) == 1
    entry = decision_rows[0]
    assert entry["decision"] == "RISK_REJECTED"
    assert entry["risk_check"]["result"] == "REJECTED"
    assert entry["risk_check"]["failed_rule"] == "session_cutoff"
    assert entry["reason"] == entry["risk_check"]["reason"]
    assert "session_cutoff" in entry["failed_gates"]
    assert outcome_rows == []


# ---------------------------------------------------------------------------
# Phase 2B hardening: manifest
# ---------------------------------------------------------------------------

def test_manifest_runs_week(config, tmp_path):
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_manifest(
        "data/replay/week/manifest.json"
    )

    assert report.days == 5
    assert report.survival_passed is True
    assert report.open_trades == 0
    assert report.candles_processed > 0
    assert (tmp_path / "logs" / "multi_day_replay_report.md").exists()


def test_manifest_replay_is_idempotent(config, tmp_path):
    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))

    first = engine.run_manifest("data/replay/week/manifest.json")
    second = engine.run_manifest("data/replay/week/manifest.json")

    assert second.approved_trades == first.approved_trades == 3
    assert second.no_trades == first.no_trades == 3
    assert second.wins == first.wins == 2
    assert second.losses == first.losses == 1
    assert second.realized_pnl_dollars == first.realized_pnl_dollars == 245.0


def test_manifest_rejects_empty_days(tmp_path):
    manifest = tmp_path / "empty_manifest.json"
    manifest.write_text(json.dumps({"days": []}))

    with pytest.raises(ValueError, match="non-empty 'days' list"):
        ReplayManifest.load(manifest)


def test_manifest_rejects_missing_replay_file(tmp_path):
    manifest = tmp_path / "missing_file_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "days": [
                    {
                        "path": "missing.jsonl",
                        "instrument": "MNQ",
                        "session": "new_york",
                        "expected_behavior": "no_trade",
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="missing replay file"):
        ReplayManifest.load(manifest)


def test_replay_cli_accepts_manifest_without_candles(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "replay",
            "--manifest",
            "data/replay/week/manifest.json",
            "--log-dir",
            str(tmp_path / "logs"),
        ],
    )

    assert replay_cli_main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["days"] == 5
    assert output["survival_passed"] is True


def test_replay_cli_rejects_ambiguous_manifest_and_candles(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "replay",
            "--manifest",
            "data/replay/week/manifest.json",
            "--candles",
            "data/replay/sample_day_mnq.jsonl",
        ],
    )

    with pytest.raises(SystemExit):
        replay_cli_main()


# ---------------------------------------------------------------------------
# Phase 2B hardening: aggregate stats
# ---------------------------------------------------------------------------

def test_aggregate_max_drawdown_is_cumulative(config, tmp_path):
    """Multi-day drawdown must reflect the cumulative equity curve, not just
    the worst single-day figure."""
    paths = sorted(Path("data/replay/week").glob("*.jsonl"))
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_many(paths)

    # max_drawdown must be >= 0 and representable as a float
    assert isinstance(report.max_drawdown, float)
    assert report.max_drawdown >= 0.0


# ---------------------------------------------------------------------------
# Full-day strat + ORB replay: mnq_full_day_2026_05_22
# Sequence with strat_212 disabled:
#   Trade 1 — orb_reclaim LONG  → WIN  (bar 4 fires, bar 5 resolves high≥target)
#   Trade 2 — vwap_hold   SHORT → OPEN (bar 12 fires, no future bar to resolve)
# strat_212 removed from enabled_concepts; bar 9 no longer fires.
# ---------------------------------------------------------------------------

FULL_DAY_PATH = Path("data/replay/mnq_full_day_2026_05_22.jsonl")


@pytest.mark.skipif(
    not FULL_DAY_PATH.exists(),
    reason="full-day candle file not present",
)
class TestFullDayReplay2026_05_22:
    def _run(self, tmp_path):
        from dataclasses import replace
        from config.settings import load_config, PositionSizingConfig

        cfg = replace(load_config(), position_sizing=PositionSizingConfig(enabled=False))
        return ReplayEngine(config=cfg, log_dir=str(tmp_path / "logs")).run(FULL_DAY_PATH)

    def test_all_13_candles_processed(self, tmp_path):
        report = self._run(tmp_path)
        assert report.candles_processed == 13

    def test_mnq_approves_one_trade_after_vwap_hold_demotion(self, tmp_path):
        """This day's candles produce 2 candidate trades (orb_reclaim, vwap_hold);
        risk_rules.yaml's strategy_permission_gate demotes vwap_hold to
        SHADOW_ONLY (2026-07-09), so only orb_reclaim reaches TRADE now."""
        report = self._run(tmp_path)
        assert report.approved_trades == 1

    def test_not_stopped_at_daily_limit(self, tmp_path):
        report = self._run(tmp_path)
        assert report.stopped_reason is None

    def test_at_least_one_win(self, tmp_path):
        report = self._run(tmp_path)
        assert report.wins >= 1

    def test_positive_realized_pnl(self, tmp_path):
        report = self._run(tmp_path)
        assert report.realized_pnl_dollars > 0

    def test_strategy_mix_in_journal(self, tmp_path):
        """Journal must contain exactly one entry for each expected strategy.

        vwap_hold is demoted to SHADOW_ONLY by risk_rules.yaml's
        strategy_permission_gate (2026-07-09) — it still generates a
        candidate here, but the gate now blocks it from reaching TRADE, same
        as strat_212 being disabled below.
        """
        report = self._run(tmp_path)
        journal_path = Path(report.journal_path)
        strategies_traded = []
        for line in journal_path.read_text().splitlines():
            entry = json.loads(line)
            if entry.get("decision") == "TRADE":
                strategies_traded.append(entry.get("setup", {}).get("strategy"))
        assert "orb_reclaim" in strategies_traded
        assert "vwap_hold" not in strategies_traded  # demoted: strategy_permission_gate
        assert "strat_212" not in strategies_traded  # disabled

    def test_strat_fields_loaded_from_candle(self, tmp_path):
        """ReplayCandle must carry the Phase-2 strat fields from the JSONL."""
        from replay import ReplayCandleLoader
        candles = ReplayCandleLoader().load_jsonl(FULL_DAY_PATH)
        bar9 = candles[8]  # 0-indexed
        assert bar9.strat_sequence == "strat_212"
        assert bar9.strat_direction == "LONG"
        assert bar9.current_bar_type == "two_up"
        assert bar9.previous_bar_type == "inside_bar"


def test_aggregate_average_win_is_weighted(config, tmp_path):
    """Weighted average win must equal gross_win / total_wins, not avg of avgs."""
    paths = sorted(Path("data/replay/week").glob("*.jsonl"))
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_many(paths)

    if report.wins > 0:
        # Recompute expected weighted average from per-day reports
        day_reports = [
            ReplayEngine(config=config, log_dir=str(tmp_path / f"logs_{i}")).run(
                path, review_date=None
            )
            for i, path in enumerate(paths)
        ]
        total_pnl = sum(r.average_win * r.wins for r in day_reports)
        total_wins = sum(r.wins for r in day_reports)
        expected = round(total_pnl / total_wins, 2) if total_wins else 0.0
        assert report.average_win == expected


def test_enriched_replay_fields_can_trigger_gex_gate(config, tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle.update({
        "current_bar_type": "two_up",
        "icc_indication_type": "demand",
        "gex_flip": candle["close"] - 1,
        "call_wall": candle["close"] - 0.25,
        "put_wall": candle["close"] - 80,
        "signa_grade": "A",
        "signa_weekly_direction": "UP",
        "demand_top": candle["close"] + 2,
        "demand_bottom": candle["close"] - 5,
    })
    replay_path = tmp_path / "enriched_gex_block.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    journal_path = Path(report.journal_path)
    entry = json.loads(journal_path.read_text().splitlines()[0])
    assert report.approved_trades == 0
    assert entry["failed_gates"] == ["GEX_UNDER_CALL_WALL"]
    assert entry["gex_status"] == "RED_LIGHT"


def test_candle_loader_preserves_enriched_fields(tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle.update({
        "gex_flip": 19500,
        "call_wall": 19550,
        "signa_grade": "B",
        "signa_weekly_direction": "UP",
        "icc_indication_type": "demand",
        "demand_top": 19510,
        "demand_bottom": 19490,
    })
    replay_path = tmp_path / "enriched.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    loaded = ReplayCandleLoader().load_jsonl(replay_path)[0]

    assert loaded.gex_flip == 19500
    assert loaded.call_wall == 19550
    assert loaded.signa_grade == "B"
    assert loaded.signa_weekly_direction == "UP"
    assert loaded.icc_indication_type == "demand"
    assert loaded.demand_top == 19510
    assert loaded.demand_bottom == 19490


# ---------------------------------------------------------------------------
# Cross-day position carry-forward (day-boundary orphans).
#
# Root cause (PR #333 / commit f9eb7a2's writeup, scripts/corpus_v1_orphan_
# resolution.py): ReplayEngine.run() builds a brand-new PaperBroker on every
# call, with no memory of the previous call's still-open position. A
# strategy with no DAY_ONLY_STRATEGIES rule (e.g. orb_reclaim) can still be
# legitimately open when a day's candle file runs out; the local `broker`
# then goes out of scope and the position — and its eventual WIN/LOSS — is
# silently lost. No OUTCOME row is ever written. PR #333 proved the fix
# mechanism (restore_position + resolve_position against real subsequent-day
# candles) via a one-off analysis script; this suite covers the engine fix
# itself, which must apply to ANY caller of run() (run_many/run_manifest, or
# an evidence script's own manual per-day loop).
# ---------------------------------------------------------------------------

def _orb_reclaim_trigger_candle(timestamp: str) -> dict:
    """The exact base candle from data/replay/sample_day_mnq.jsonl's bar 0 —
    already proven by test_replay_engine_runs_sample_day to fire an approved
    orb_reclaim LONG: orb_high=19498.0, orb_low=19462.0, tick=0.25,
    MAX_ORB_STOP_TICKS[MNQ]=80 -> entry=19498.5, stop=19478.5,
    target=19548.5 (2.5R), per strategy/signal_engine.py::_try_orb_reclaim."""
    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    base["timestamp"] = timestamp
    return base


def _flat_follow_up_candle(
    timestamp: str, *, high: float, low: float, close: float | None = None
) -> dict:
    """A quiet follow-up candle carrying the same base MNQ/new_york fields as
    the trigger (so it still parses as valid data) but with overwritten OHLC
    and a market_condition/orb_status that cannot fire a brand-new decision
    on its own — isolates assertions to the carried-forward resolution."""
    base = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    base["timestamp"] = timestamp
    base["open"] = high
    base["high"] = high
    base["low"] = low
    base["close"] = close if close is not None else high
    base["market_condition"] = "CHOPPY"
    base["orb_status"] = "inside"
    return base


ORB_RECLAIM_ENTRY = 19498.5
ORB_RECLAIM_STOP = 19478.5
ORB_RECLAIM_TARGET = 19548.5


def _read_journal_rows(path: Path) -> list[dict]:
    """A day whose only candle(s) are entirely consumed by cross-day
    carry-forward resolution (see run()'s pre-scan) never gets an
    independent decision journaled for it — the resolving bar's OUTCOME is
    written onto the ORIGINAL day's file, not this one — so JournalLogger
    never lazily creates this day's file at all (journal/journal_logger.py
    only opens it on first append). That's pre-existing behavior (a
    same-day resolving bar during the normal mid-day loop is treated
    identically), not something this fix changes, so read helpers here
    treat a missing file the same as an empty one."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_position_open_at_day_boundary_carries_forward_and_resolves(config, tmp_path):
    """THE FIX: day 1 is a single candle — the orb_reclaim trigger bar — with
    no subsequent bar in the same file to resolve it
    (`for future_idx in range(idx + 1, len(candles))` is empty when
    len(candles) == 1), so the position is still open when day 1's file runs
    out. Day 2 (three calendar days later — 2026-05-25 — proving no
    day-adjacency assumption is baked in) supplies a bar whose high (19600)
    clears the target (19548.5). The engine must restore the exact position
    into day 2's broker and resolve it causally against day 2's own candles,
    writing a real OUTCOME row into DAY 1's journal file (not day 2's) with
    the SAME paper_order_id already on the TRADE row, so JournalReader's
    exact-identity join (#327/#332) still pairs them across the file
    boundary. Before the fix, this scenario loses the position entirely (see
    the old defect-reproduction assertions in git history of this file /
    PR #333's writeup) — no OUTCOME row, no PnL, and a false-flagged
    open_trades_after_replay even though day 2 supplied data that resolves
    it."""
    day1 = tmp_path / "day1.jsonl"
    day1.write_text(json.dumps(_orb_reclaim_trigger_candle("2026-05-22T14:30:00+00:00")) + "\n")

    day2 = tmp_path / "day2.jsonl"
    day2.write_text(
        json.dumps(
            _flat_follow_up_candle("2026-05-25T14:30:00+00:00", high=19600.0, low=19490.0)
        )
        + "\n"
    )

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    report = engine.run_many([day1, day2])

    day1_journal = tmp_path / "logs" / "journal_2026-05-22.jsonl"
    rows = [json.loads(line) for line in day1_journal.read_text().splitlines() if line.strip()]
    trade_rows = [r for r in rows if r.get("decision") == "TRADE"]
    outcome_rows = [r for r in rows if r.get("type") == "OUTCOME"]
    assert len(trade_rows) == 1
    assert len(outcome_rows) == 1

    trade_order_id = trade_rows[0].get("paper_order_id")
    outcome = outcome_rows[0]["outcome"]
    assert trade_order_id  # real identity minted on the TRADE row
    assert outcome["paper_order_id"] == trade_order_id
    assert outcome["result"] == "WIN"
    assert outcome["entry_price"] == ORB_RECLAIM_ENTRY
    assert outcome["exit_price"] == ORB_RECLAIM_TARGET
    assert outcome["pnl_dollars"] > 0

    # day 2's own journal must NOT gain a duplicate/foreign trade or outcome
    # row for this position — it belongs to day 1's file. (day 2's file may
    # not even exist: its only candle was entirely consumed resolving the
    # carried position, so nothing was ever independently journaled for it —
    # see _read_journal_rows.)
    day2_rows = _read_journal_rows(tmp_path / "logs" / "journal_2026-05-25.jsonl")
    assert not any(r.get("type") == "OUTCOME" for r in day2_rows)
    assert not any(r.get("decision") == "TRADE" for r in day2_rows)

    # No more false positives: the position resolved, so the aggregate must
    # not flag it as still open.
    assert report.open_trades == 0
    assert report.survival_passed is True
    assert "open_trades_after_replay" not in report.failure_reasons


def test_position_open_at_day_boundary_resolves_after_multiple_days(config, tmp_path):
    """The carry-forward must not assume same-file-format/adjacency and must
    keep waiting across MULTIPLE subsequent run() calls, not just one. Day 2
    and day 3 both supply bars that stay inside [stop, target] — the position
    remains open after each — and only day 4 finally clears the target."""
    day1 = tmp_path / "day1.jsonl"
    day1.write_text(json.dumps(_orb_reclaim_trigger_candle("2026-05-22T14:30:00+00:00")) + "\n")

    day2 = tmp_path / "day2.jsonl"
    day2.write_text(
        json.dumps(
            _flat_follow_up_candle("2026-05-23T14:30:00+00:00", high=19510.0, low=19490.0)
        )
        + "\n"
    )
    day3 = tmp_path / "day3.jsonl"
    day3.write_text(
        json.dumps(
            _flat_follow_up_candle("2026-05-24T14:30:00+00:00", high=19515.0, low=19485.0)
        )
        + "\n"
    )
    day4 = tmp_path / "day4.jsonl"
    day4.write_text(
        json.dumps(
            _flat_follow_up_candle("2026-05-25T14:30:00+00:00", high=19600.0, low=19490.0)
        )
        + "\n"
    )

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    report = engine.run_many([day1, day2, day3, day4])

    day1_journal = tmp_path / "logs" / "journal_2026-05-22.jsonl"
    rows = [json.loads(line) for line in day1_journal.read_text().splitlines() if line.strip()]
    outcome_rows = [r for r in rows if r.get("type") == "OUTCOME"]
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["outcome"]["result"] == "WIN"
    assert outcome_rows[0]["outcome"]["exit_price"] == ORB_RECLAIM_TARGET

    # Days 2 and 3 must show no outcome at all (still waiting) and no new
    # decision of their own (their own files may not even exist — see
    # _read_journal_rows).
    for day in ("2026-05-23", "2026-05-24"):
        day_rows = _read_journal_rows(tmp_path / "logs" / f"journal_{day}.jsonl")
        assert not any(r.get("type") == "OUTCOME" for r in day_rows)
        assert not any(r.get("decision") == "TRADE" for r in day_rows)

    assert report.open_trades == 0
    assert report.survival_passed is True


def test_position_still_open_at_true_corpus_tail_is_still_flagged(config, tmp_path):
    """The carry-forward mechanism must not paper over a position that is
    GENUINELY still open when the caller has no more data to supply — this
    is the same case Corpus v1 itself still has a few of after PR #333's
    manual fix ("open_with_identity" trades near the tail of a corpus's date
    range). Day 2's only bar stays inside [stop, target] (never resolves),
    and there is no day 3 — the aggregate must still flag
    open_trades_after_replay, and no OUTCOME row must ever be fabricated."""
    day1 = tmp_path / "day1.jsonl"
    day1.write_text(json.dumps(_orb_reclaim_trigger_candle("2026-05-22T14:30:00+00:00")) + "\n")

    day2 = tmp_path / "day2.jsonl"
    day2.write_text(
        json.dumps(
            _flat_follow_up_candle("2026-05-23T14:30:00+00:00", high=19510.0, low=19490.0)
        )
        + "\n"
    )

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    report = engine.run_many([day1, day2])

    day1_journal = tmp_path / "logs" / "journal_2026-05-22.jsonl"
    rows = [json.loads(line) for line in day1_journal.read_text().splitlines() if line.strip()]
    assert not any(r.get("type") == "OUTCOME" for r in rows)

    assert report.open_trades == 1
    assert report.survival_passed is False
    assert "open_trades_after_replay" in report.failure_reasons


def test_position_open_at_day_boundary_carries_forward_across_bare_run_calls(config, tmp_path):
    """Several evidence scripts (scripts/run_replay_batch.py,
    scripts/strat_212_122_canonical_evidence_run.py) construct ONE
    ReplayEngine and call .run(file, review_date=...) directly in their own
    per-day loop — never touching run_many/run_manifest. The carry-forward
    state must live at a level shared by every caller (inside run() itself),
    not bolted onto run_many/run_manifest only."""
    day1 = tmp_path / "day1.jsonl"
    day1.write_text(json.dumps(_orb_reclaim_trigger_candle("2026-05-22T14:30:00+00:00")) + "\n")

    day2 = tmp_path / "day2.jsonl"
    day2.write_text(
        json.dumps(
            _flat_follow_up_candle("2026-05-25T14:30:00+00:00", high=19600.0, low=19490.0)
        )
        + "\n"
    )

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    engine.run(day1, review_date="2026-05-22")
    engine.run(day2, review_date="2026-05-25")

    day1_journal = tmp_path / "logs" / "journal_2026-05-22.jsonl"
    rows = [json.loads(line) for line in day1_journal.read_text().splitlines() if line.strip()]
    outcome_rows = [r for r in rows if r.get("type") == "OUTCOME"]
    assert len(outcome_rows) == 1
    assert outcome_rows[0]["outcome"]["result"] == "WIN"


def test_day_only_strategy_open_at_file_end_is_never_carried_forward(monkeypatch, config, tmp_path):
    """DAY_ONLY_STRATEGIES members (currently only strat_4hr_retrigger) are
    designed to always flatten before their file ends
    (execution/day_only_exit.py). If one is still open at file-end anyway —
    the EOD_BAR_MISSING case already covered by
    test_run_many_missing_eod_stays_explicit_and_open in
    tests/test_day_only_exit.py — that indicates the day-only-flatten logic
    itself failed, a DIFFERENT bug from the general cross-day carry-forward
    this suite covers. It must never be silently carried forward as if it
    were a legitimate open position: no OUTCOME row must ever appear for it,
    and the engine's internal carry-forward state must stay empty."""
    from tests.test_day_only_exit import _install_replay_fakes, _replay_row

    _install_replay_fakes(monkeypatch)
    day1 = tmp_path / "day1.jsonl"
    day1.write_text(
        json.dumps(_replay_row("2026-07-13T19:50:00Z"))
        + "\n"
        + json.dumps(_replay_row("2026-07-13T20:00:00Z", close=111.0))
        + "\n"
    )
    day2 = tmp_path / "day2.jsonl"
    day2.write_text(json.dumps(_replay_row("2026-07-14T14:30:00Z")) + "\n")

    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))
    report = engine.run_many([day1, day2])

    assert report.open_trades == 1
    assert report.survival_passed is False
    assert "open_trades_after_replay" in report.failure_reasons
    # Never silently carried forward: no OUTCOME row anywhere, and the
    # engine's own carry-forward bookkeeping must never have accepted it.
    assert engine._carried_positions == {}
    day1_rows = [
        json.loads(line)
        for line in (tmp_path / "logs" / "journal_2026-07-13.jsonl").read_text().splitlines()
    ]
    assert not any(row.get("type") == "OUTCOME" for row in day1_rows)
