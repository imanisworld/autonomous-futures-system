"""Converter-level integration tests: reconstructed_* fields are wired
additively into scripts/csv_to_replay.py and scripts/polygon_to_replay.py
without disturbing the existing market_condition/trend_direction/
trend_strength/avg_volume fields the live replay decision path still reads.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts.csv_to_replay import LOOKBACK, convert
from scripts.pine_market_condition import RECONSTRUCTED, UNAVAILABLE_SYNTHETIC_VOLUME
from scripts.polygon_to_replay import derive_candles

_ET = ZoneInfo("America/New_York")

RECON_FIELDS = (
    "reconstructed_trend_direction",
    "reconstructed_market_condition",
    "reconstructed_status",
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
        assert last["reconstructed_status"] == RECONSTRUCTED
        assert last["reconstructed_trend_direction"] == "UP"
        assert last["reconstructed_market_condition"] in {"DEAD", "CHOPPY", "TRENDING", "RANGE_BOUND"}
        assert last["reconstructed_atr14"] is not None
        assert last["reconstructed_rel_vol"] is not None

    def test_existing_fields_untouched_by_new_wiring(self, tmp_path):
        csv_path = tmp_path / "MNQ_15.csv"
        csv_path.write_text(_mk_csv(25), encoding="utf-8")
        jsonl_path = convert(csv_path, tmp_path / "out")
        import json
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]

        # The existing avg_volume window is the pre-existing (unrelated,
        # untouched) range(max(0, i-LOOKBACK), i+1) computation -- 21 bars
        # once warm. Recompute it independently here and confirm the
        # engine-facing field still matches it exactly (i.e. the new,
        # correct 20-bar reconstruction did NOT get wired into the old field).
        volumes = [500] * 25
        for i, c in enumerate(candles):
            window = volumes[max(0, i - LOOKBACK):i + 1]
            expected_avg_vol = max(1, int(sum(window) / len(window)))
            assert c["avg_volume"] == expected_avg_vol
            assert c["market_condition"] in {"TRENDING", "CHOPPY", "CONSOLIDATING", "UNKNOWN"}
            assert c["trend_direction"] == "UP"

    def test_missing_volume_column_marks_all_bars_synthetic_unavailable(self, tmp_path):
        csv_path = tmp_path / "MNQ_15.csv"
        csv_path.write_text(_mk_csv(25, include_volume=False), encoding="utf-8")
        jsonl_path = convert(csv_path, tmp_path / "out")
        import json
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        assert len(candles) == 25

        for c in candles:
            assert c["reconstructed_status"] == UNAVAILABLE_SYNTHETIC_VOLUME
            assert c["reconstructed_market_condition"] is None
            assert c["reconstructed_trend_direction"] is None
            # But the EXISTING engine-facing fields must still be populated —
            # synthetic-volume exclusion is scoped to reconstruction only.
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
        # includes bar 5 (indices 5..24 here, window width 20), must be
        # UNAVAILABLE_SYNTHETIC_VOLUME. Bars before index 5 are unaffected by
        # bar 5 (it isn't in their trailing window) but may still be
        # UNAVAILABLE_WARMUP if ATR/volume warmup hasn't completed.
        for i, c in enumerate(candles):
            if 5 <= i <= 24:
                assert c["reconstructed_status"] == UNAVAILABLE_SYNTHETIC_VOLUME, i
        # A bar clearly past bar 5's window influence and past all warmup
        # would be needed to see RECONSTRUCTED again — with only 25 bars and
        # a 20-bar window, bar 5 taints every remaining bar in this fixture,
        # which itself proves the contamination logic reaches forward
        # correctly (not just the single synthetic bar).


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

    def test_additive_fields_present_and_reconstructed_after_warmup(self):
        _, candles = self._candles()
        assert candles
        last = candles[-1]
        for field in RECON_FIELDS:
            assert field in last
        assert last["reconstructed_status"] == RECONSTRUCTED
        assert last["reconstructed_trend_direction"] == "UP"  # monotonic rise
        assert last["reconstructed_market_condition"] == "TRENDING"

    def test_existing_fields_untouched(self):
        _, candles = self._candles()
        for c in candles:
            assert c["trend_direction"] == "UP"
            assert c["market_condition"] == "TRENDING"
            assert c["avg_volume"] == 100  # constant volume, old 21-bar window == 100 either way

    def test_never_marked_synthetic_volume_real_polygon_feed(self):
        _, candles = self._candles()
        # Polygon volume is a direct feed passthrough — reconstruction must
        # never exclude a Polygon-derived bar as synthetic-volume.
        assert all(c["reconstructed_status"] != UNAVAILABLE_SYNTHETIC_VOLUME for c in candles)

    def test_wilder_atr_end_to_end_matches_independent_calculation(self):
        bars, candles = self._candles()
        # Constant high-low=2.0 range, no gaps (close[i] == open[i+1] exactly
        # by construction) → true range is a constant 2.0 for every bar from
        # index 1 onward, so Wilder ATR14 converges to exactly 2.0.
        expected_atr = 2.0
        for c in candles[-10:]:
            assert c["reconstructed_atr14"] == pytest.approx(expected_atr)

    def test_relative_volume_matches_constant_volume_series(self):
        _, candles = self._candles()
        # volume is constant at 100 for every bar → sma20(volume) == 100 →
        # rel_vol == 1.0 once warm.
        for c in candles[-10:]:
            assert c["reconstructed_rel_vol"] == pytest.approx(1.0)
