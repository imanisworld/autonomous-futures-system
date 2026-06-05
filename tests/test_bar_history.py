"""Tests for the per-instrument rolling bar history (context.bar_history)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from context.bar_history import BarHistory


def _ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _rec(bh, instrument, dt, close, **kw):
    return bh.record(
        instrument,
        ts=_ts(dt),
        open=kw.get("open", close),
        high=kw.get("high", close + 1),
        low=kw.get("low", close - 1),
        close=close,
        volume=kw.get("volume", 1000),
        timeframe="15",
    )


class TestRecordAndRead:
    def test_record_then_recent_roundtrips(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        base = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        for i in range(5):
            _rec(bh, "MES", base + timedelta(minutes=15 * i), 7500 + i)
        recent = bh.recent("MES", 3, for_date=base.date())
        assert [b["close"] for b in recent] == [7502, 7503, 7504]

    def test_resend_same_ts_not_duplicated(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        dt = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        _rec(bh, "MES", dt, 7500)
        _rec(bh, "MES", dt, 7500)  # resend
        assert len(bh.recent("MES", 10, for_date=dt.date())) == 1

    def test_instruments_are_isolated(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        dt = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        _rec(bh, "MES", dt, 7500)
        _rec(bh, "MNQ", dt, 20000)
        assert bh.last_bar("MES", for_date=dt.date())["close"] == 7500
        assert bh.last_bar("MNQ", for_date=dt.date())["close"] == 20000

    def test_last_bar_none_when_empty(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        assert bh.last_bar("MES") is None


class TestGapDetection:
    def test_no_gap_for_consecutive_bars(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        d = date(2026, 6, 5)
        t0 = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        _rec(bh, "MES", t0, 7500)
        gap = bh.detect_gap("MES", _ts(t0 + timedelta(minutes=15)), 15, for_date=d)
        assert gap["gapped"] is False
        assert gap["missing_bars"] == 0

    def test_gap_detected_when_bars_missing(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        d = date(2026, 6, 5)
        t0 = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        _rec(bh, "MES", t0, 7500)
        # Next bar arrives 60 min later → 4 intervals → 3 missing bars (:15/:30/:45).
        gap = bh.detect_gap("MES", _ts(t0 + timedelta(minutes=60)), 15, for_date=d)
        assert gap["gapped"] is True
        assert gap["missing_bars"] == 3

    def test_first_bar_never_gapped(self, tmp_path):
        bh = BarHistory(log_dir=str(tmp_path))
        t0 = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
        gap = bh.detect_gap("MES", _ts(t0), 15, for_date=t0.date())
        assert gap["gapped"] is False
        assert gap["last_ts"] is None


class TestWindowDirection:
    def test_steady_downtrend_reads_down(self):
        bars = [{"close": c} for c in (7600, 7590, 7580, 7570, 7557)]
        assert BarHistory.window_direction(bars) == "DOWN"

    def test_steady_uptrend_reads_up(self):
        bars = [{"close": c} for c in (7500, 7510, 7520, 7535)]
        assert BarHistory.window_direction(bars) == "UP"

    def test_chop_reads_none(self):
        bars = [{"close": c} for c in (7500, 7510, 7498, 7512, 7501)]
        assert BarHistory.window_direction(bars) is None

    def test_too_few_bars_reads_none(self):
        assert BarHistory.window_direction([{"close": 1}, {"close": 2}]) is None

    def test_one_pullback_still_trends(self):
        # 4 of 5 steps up, net up → UP despite one down step.
        bars = [{"close": c} for c in (7500, 7510, 7520, 7515, 7530, 7545)]
        assert BarHistory.window_direction(bars) == "UP"
