"""Converter-level integration tests for engine-facing Pine regime parity.

The canonical reconstruction must populate ``market_condition``.  The former
heuristic is provenance-only under ``legacy_market_condition``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.csv_to_replay import LOOKBACK, convert
from scripts.pine_market_condition import (
    RECONSTRUCTED,
    RECONSTRUCTED_UNVALIDATED_INIT,
    UNAVAILABLE_SYNTHETIC_VOLUME,
)
from scripts.polygon_to_replay import derive_candles
from replay.candle_loader import ReplayCandleLoader
from replay.replay_engine import ReplayEngine
from config.settings import load_config
from strategy.signal_engine import DecisionEngine

_ET = ZoneInfo("America/New_York")

RECON_FIELDS = (
    "reconstructed_trend_direction",
    "reconstructed_trend_status",
    "reconstructed_market_condition",
    "reconstructed_market_condition_status",
    "reconstructed_atr14",
    "reconstructed_rel_vol",
)


def _mk_csv(n: int, *, include_volume: bool = True, start_et: datetime | None = None) -> str:
    """n consecutive 15m NY-session TradingView-export rows, monotonically
    rising close, full bull EMA stack, constant far-below ORB (never
    ambiguous), volume constant at 500 unless include_volume=False."""
    start_et = start_et or datetime(2026, 6, 9, 9, 30, tzinfo=_ET)
    header = (
        "time,open,high,low,close,"
        + ("Volume," if include_volume else "")
        + "EMA 9,EMA 21,EMA 55,NY ORB High,NY ORB Low,"
        + "Bar Type 1 Label,Bar Type 2 Label,Bar Type 3 Label\n"
    )
    lines = [header]
    price = 19000.0
    for i in range(n):
        ts = (start_et + timedelta(minutes=15 * i)).isoformat()
        close = price + 1.0
        row = [
            ts,
            f"{price:.2f}", f"{price + 2:.2f}", f"{price - 2:.2f}", f"{close:.2f}",
        ]
        if include_volume:
            row.append("500")
        row += [
            f"{close - 3:.2f}", f"{close - 6:.2f}", f"{close - 9:.2f}",  # ema9/21/55: full bull stack
            "18000.00", "17900.00",  # ORB far below every close — always "above"
            "2U", "2U", "2U",
        ]
        lines.append(",".join(row) + "\n")
        price += 1.0
    return "".join(lines)


class TestCsvToReplayReconstructionWiring:
    def test_additive_fields_present_after_warmup(self, tmp_path):
        csv_path = tmp_path / "MNQ_15.csv"
        csv_path.write_text(_mk_csv(25), encoding="utf-8")
        jsonl_path = convert(csv_path, tmp_path / "out")
        import json
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        assert len(candles) == 25

        last = candles[-1]
        for field in RECON_FIELDS:
            assert field in last
        # CSV path uses Pine's own exported EMA columns -- trend is fully
        # Pine-exact (RECONSTRUCTED). market_condition depends on ATR, which
        # is always self-computed -- never plain RECONSTRUCTED.
        assert last["reconstructed_trend_status"] == RECONSTRUCTED
        assert last["reconstructed_trend_direction"] == "UP"
        assert last["reconstructed_market_condition_status"] == RECONSTRUCTED_UNVALIDATED_INIT
        assert last["reconstructed_market_condition"] in {"DEAD", "CHOPPY", "TRENDING", "RANGE_BOUND"}
        assert last["reconstructed_atr14"] is not None
        assert last["reconstructed_rel_vol"] is not None

    def test_engine_facing_condition_matches_canonical_reconstruction(self, tmp_path):
        csv_path = tmp_path / "MNQ_15.csv"
        csv_path.write_text(_mk_csv(25), encoding="utf-8")
        jsonl_path = convert(csv_path, tmp_path / "out")
        import json
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]

        # avg_volume remains a separate 21-bar legacy context value. It must
        # not affect the exact SMA20 used by canonical market_condition.
        volumes = [500] * 25
        for i, c in enumerate(candles):
            window = volumes[max(0, i - LOOKBACK):i + 1]
            expected_avg_vol = max(1, int(sum(window) / len(window)))
            assert c["avg_volume"] == expected_avg_vol
            assert c["market_condition"] == c["reconstructed_market_condition"]
            assert c["market_condition_status"] == c["reconstructed_market_condition_status"]
            assert c["legacy_market_condition"] in {
                "TRENDING", "CHOPPY", "CONSOLIDATING", "UNKNOWN"
            }
            assert c["trend_direction"] == "UP"

        # ATR14 is available at index 13, but exact SMA20 volume is not
        # available until index 19. Unavailable bars must not execute under
        # their still-present legacy heuristic.
        assert all(c["market_condition"] is None for c in candles[:19])
        assert all(c["legacy_market_condition"] is not None for c in candles[:19])
        assert candles[19]["market_condition"] is not None

    def test_missing_volume_column_marks_condition_synthetic_unavailable_not_trend(self, tmp_path):
        csv_path = tmp_path / "MNQ_15.csv"
        csv_path.write_text(_mk_csv(25, include_volume=False), encoding="utf-8")
        jsonl_path = convert(csv_path, tmp_path / "out")
        import json
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        assert len(candles) == 25

        for c in candles:
            assert c["reconstructed_market_condition_status"] == UNAVAILABLE_SYNTHETIC_VOLUME
            assert c["reconstructed_market_condition"] is None
            # Trend depends only on close/EMA, not volume -- synthetic
            # volume must not suppress it.
            assert c["reconstructed_trend_status"] == RECONSTRUCTED
            assert c["reconstructed_trend_direction"] == "UP"
            # Missing real volume fails closed for engine-facing regime;
            # the legacy diagnostic remains available but cannot execute.
            assert c["market_condition"] is None
            assert c["legacy_market_condition"] is not None
            assert c["trend_direction"] == "UP"
            assert c["avg_volume"] is not None

    def test_one_synthetic_volume_bar_contaminates_its_own_20_bar_window(self, tmp_path):
        # Build 25 real-volume rows, then splice one row's Volume value away
        # (simulate a single gap in an otherwise-good export) partway through.
        text = _mk_csv(25)
        lines = text.splitlines(keepends=True)
        header = lines[0].rstrip("\n").split(",")
        vol_idx = header.index("Volume")
        # Blank out bar index 5's volume field (0-indexed among data rows).
        target_line_no = 1 + 5
        fields = lines[target_line_no].rstrip("\n").split(",")
        fields[vol_idx] = ""
        lines[target_line_no] = ",".join(fields) + "\n"
        csv_path = tmp_path / "MNQ_15.csv"
        csv_path.write_text("".join(lines), encoding="utf-8")

        jsonl_path = convert(csv_path, tmp_path / "out")
        import json
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]

        # Bar 5 itself, and every bar whose own 20-bar trailing window still
        # includes bar 5 (indices 5..24 here, window width 20), must have
        # market_condition UNAVAILABLE_SYNTHETIC_VOLUME. Trend is unaffected
        # (volume-independent) throughout.
        for i, c in enumerate(candles):
            if 5 <= i <= 24:
                assert c["reconstructed_market_condition_status"] == UNAVAILABLE_SYNTHETIC_VOLUME, i
            assert c["reconstructed_trend_status"] == RECONSTRUCTED, i


class TestPolygonToReplayReconstructionWiring:
    def _mk_bars(self, n: int, start_et: datetime, *, base: float = 100.0, step: float = 0.5):
        bars = []
        price = base
        for i in range(n):
            ts = int((start_et + timedelta(minutes=15 * i)).timestamp())
            bars.append({"ts": ts, "open": price, "high": price + 1.0,
                         "low": price - 1.0, "close": price + step, "volume": 100})
            price += step
        return bars

    def _candles(self, n=260):
        start = datetime(2026, 6, 9, 2, 0, tzinfo=_ET)
        bars = self._mk_bars(n, start)
        return bars, derive_candles(bars, "MES", 15)

    def test_additive_fields_present_and_unvalidated_init_after_warmup(self):
        _, candles = self._candles()
        assert candles
        last = candles[-1]
        for field in RECON_FIELDS:
            assert field in last
        # Polygon EMA is always self-computed -- trend can never claim the
        # plain Pine-exact RECONSTRUCTED tier here.
        assert last["reconstructed_trend_status"] == RECONSTRUCTED_UNVALIDATED_INIT
        assert last["reconstructed_trend_direction"] == "UP"  # monotonic rise
        assert last["reconstructed_market_condition_status"] == RECONSTRUCTED_UNVALIDATED_INIT
        assert last["reconstructed_market_condition"] == "TRENDING"

    def test_never_claims_plain_reconstructed_pine_native_tier(self):
        _, candles = self._candles()
        # Neither output may claim the plain RECONSTRUCTED status for a
        # Polygon-derived bar -- EMA and ATR are both self-computed there.
        for c in candles:
            assert c["reconstructed_trend_status"] != RECONSTRUCTED
            assert c["reconstructed_market_condition_status"] != RECONSTRUCTED

    def test_engine_facing_condition_uses_canonical_reconstruction(self):
        _, candles = self._candles()
        for c in candles:
            assert c["trend_direction"] == "UP"
            assert c["market_condition"] == c["reconstructed_market_condition"]
            assert c["market_condition_status"] == c["reconstructed_market_condition_status"]
            assert c["legacy_market_condition"] == "TRENDING"
            assert c["avg_volume"] == 100  # constant volume, old 21-bar window == 100 either way

    @pytest.mark.parametrize(
        ("last_volume", "canonical"),
        [(1, "DEAD"), (50, "CHOPPY"), (70, "RANGE_BOUND")],
    )
    def test_legacy_trending_cannot_leak_into_engine_condition(
        self, last_volume, canonical
    ):
        start = datetime(2026, 6, 9, 2, 0, tzinfo=_ET)
        bars = self._mk_bars(260, start)
        bars[-1]["volume"] = last_volume

        last = derive_candles(bars, "MES", 15)[-1]

        assert last["legacy_market_condition"] == "TRENDING"
        assert last["reconstructed_market_condition"] == canonical
        assert last["market_condition"] == canonical

    def test_replay_engine_consumes_canonical_not_legacy_condition(self, tmp_path):
        start = datetime(2026, 6, 9, 2, 0, tzinfo=_ET)
        bars = self._mk_bars(260, start)
        bars[-1]["volume"] = 1
        raw = derive_candles(bars, "MES", 15)[-1]
        assert raw["legacy_market_condition"] == "TRENDING"
        assert raw["market_condition"] == "DEAD"

        candle = ReplayCandleLoader._parse(raw)
        engine = ReplayEngine(config=load_config(), log_dir=str(tmp_path))
        state = engine._market_state_from_candle(candle)

        assert state.market_condition == "DEAD"
        assert DecisionEngine(config=engine.config)._score_market_condition(state) == "DEAD"

    def test_never_marked_synthetic_volume_real_polygon_feed(self):
        _, candles = self._candles()
        # Polygon volume is a direct feed passthrough — reconstruction must
        # never exclude a Polygon-derived bar as synthetic-volume.
        assert all(
            c["reconstructed_market_condition_status"] != UNAVAILABLE_SYNTHETIC_VOLUME
            for c in candles
        )

    def test_wilder_atr_end_to_end_matches_independent_calculation(self):
        bars, candles = self._candles()
        # Constant high-low=2.0 range, no gaps (close[i] == open[i+1] exactly
        # by construction) → true range is a constant 2.0 for every bar,
        # including bar 0 (high-low, no prior close needed), so Wilder
        # ATR14 converges to exactly 2.0 regardless of the seed-window
        # boundary.
        expected_atr = 2.0
        for c in candles[-10:]:
            assert c["reconstructed_atr14"] == pytest.approx(expected_atr)

    def test_relative_volume_matches_constant_volume_series(self):
        _, candles = self._candles()
        # volume is constant at 100 for every bar → sma20(volume) == 100 →
        # rel_vol == 1.0 once warm.
        for c in candles[-10:]:
            assert c["reconstructed_rel_vol"] == pytest.approx(1.0)
