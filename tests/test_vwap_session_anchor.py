"""VWAP session-anchor parity tests (scripts/csv_to_replay + scripts/polygon_to_replay).

Regression coverage for the confirmed defect: replay's VWAP accumulator was
resetting at every detect_session() transition (Asian/London/New York/off-hours
== 4x/day), while TradingView's real Pine ta.vwap(hlc3) — and RiskSentinel's own
Pine VWAP — resets once per CME trading day at 18:00 ET. Confirmed empirically
against real TradingView Pine VWAP output (see PR description): 0.00 divergence
within a day, sharp divergence introduced by each spurious sub-session reset.

The fix keys the VWAP accumulator reset off vwap_day_range() (the same
detect_day_boundaries()-derived day range HOD/LOD and PDH/PDL/PDC already use),
not off detect_session(). Both csv_to_replay.convert() and
polygon_to_replay.derive_candles() now share that single helper.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from scripts.csv_to_replay import compute_vwap, convert, detect_day_boundaries, vwap_day_range
from scripts.polygon_to_replay import derive_candles

_ET = ZoneInfo("America/New_York")


def _mk_bars(n: int, start_et: datetime, *, base: float = 100.0, step: float = 0.5):
    """n 5m bars climbing `step` per bar from an ET start time (EMA/ORB warmup)."""
    bars = []
    price = base
    for i in range(n):
        ts = int((start_et + timedelta(minutes=5 * i)).timestamp())
        bars.append({"ts": ts, "open": price, "high": price + 1.0,
                     "low": price - 1.0, "close": price + step, "volume": 100})
        price += step
    return bars


def _flat_block(n: int, start_et: datetime, price: float, *, volume: float = 100):
    """n 5m flat bars at a constant price/volume — makes VWAP arithmetic exact."""
    bars = []
    for i in range(n):
        ts = int((start_et + timedelta(minutes=5 * i)).timestamp())
        bars.append({"ts": ts, "open": price, "high": price, "low": price,
                      "close": price, "volume": volume})
    return bars


class TestVwapDayRangePure:
    """vwap_day_range() itself: the sole reset-boundary source of truth."""

    def _bars_and_ranges(self):
        # Explicit timestamps (not hour-of-day lookups, which collide across
        # a >24h window): one CME day starting 18:00 ET, sampled at Asian,
        # London, New York, and off-hours points, plus one bar the next day.
        day_start = datetime(2026, 6, 1, 18, 0, tzinfo=_ET)
        offsets_hours = {
            "asian": 2,        # 20:00 ET
            "london": 9,       # 03:00 ET next day
            "new_york": 15.5,  # 09:30 ET next day
            "off_hours": 23,   # 17:00 ET next day
        }
        idx_of = {}
        bars = []
        for label, off in offsets_hours.items():
            idx_of[label] = len(bars)
            bars.append({"ts": int((day_start + timedelta(hours=off)).timestamp())})
        idx_of["next_day"] = len(bars)
        bars.append({"ts": int((day_start + timedelta(hours=24)).timestamp())})

        boundaries = detect_day_boundaries(bars)
        day_ranges = [(boundaries[i], boundaries[i + 1] if i + 1 < len(boundaries) else len(bars))
                      for i in range(len(boundaries))]
        return day_ranges, idx_of

    def test_same_range_across_asian_london_ny_offhours(self):
        day_ranges, idx_of = self._bars_and_ranges()
        r_asian = vwap_day_range(day_ranges, idx_of["asian"])
        r_london = vwap_day_range(day_ranges, idx_of["london"])
        r_ny = vwap_day_range(day_ranges, idx_of["new_york"])
        r_offhours = vwap_day_range(day_ranges, idx_of["off_hours"])

        assert r_asian == r_london == r_ny == r_offhours
        assert r_asian is not None

    def test_different_range_across_1800_et_boundary(self):
        day_ranges, idx_of = self._bars_and_ranges()
        r_before = vwap_day_range(day_ranges, idx_of["off_hours"])  # 17:00 ET, still old day
        r_after = vwap_day_range(day_ranges, idx_of["next_day"])    # 18:00 ET, new day
        assert r_before != r_after


class TestPolygonDeriveCandlesVwapReset:
    """derive_candles() (Polygon path): integration-level reset behavior."""

    def _build(self):
        # Warmup: long climbing run so EMA200 and the first NY ORB are
        # established well before the day boundary under test.
        warmup_start = datetime(2026, 6, 8, 2, 0, tzinfo=_ET)
        warmup = _mk_bars(280, warmup_start)
        warmup_end = warmup_start + timedelta(minutes=5 * 280)

        # Roll forward to the next clean 18:00 ET boundary for the test day.
        day_start = warmup_end.replace(hour=18, minute=0, second=0, microsecond=0)
        if day_start <= warmup_end:
            day_start += timedelta(days=1)

        asian = _flat_block(36, day_start, 1000.0)                                    # 18:00-02:45
        london = _flat_block(26, day_start + timedelta(hours=9), 2000.0)              # 03:00-08:45
        new_york = _flat_block(30, day_start + timedelta(hours=15, minutes=30), 3000.0)  # 09:30-16:45
        off_hours = _flat_block(4, day_start + timedelta(hours=23), 4000.0)           # 17:00-17:45
        day2_start = day_start + timedelta(hours=24)
        day2 = _flat_block(5, day2_start, 5000.0)

        bars = warmup + asian + london + new_york + off_hours + day2
        candles = derive_candles(bars, "MES", 5)
        by_ts = {c["timestamp"]: c for c in candles}
        return bars, candles, by_ts, dict(asian=asian, london=london, new_york=new_york,
                                           off_hours=off_hours, day2=day2)

    def _ts(self, bar):
        return datetime.fromtimestamp(bar["ts"], tz=timezone_utc()).isoformat()

    def test_03_00_et_does_not_reset_vwap(self):
        bars, candles, by_ts, blocks = self._build()
        first_london_bar = blocks["london"][0]
        c = by_ts[self._ts(first_london_bar)]
        assert c["session"] == "london"
        # A reset would snap VWAP to ~2000 (this bar's own price). No reset
        # keeps it anchored near the accumulated Asian-block average (~1000).
        assert c["vwap"] < 1100, f"expected VWAP still anchored near 1000, got {c['vwap']}"

    def test_09_30_et_does_not_reset_vwap(self):
        bars, candles, by_ts, blocks = self._build()
        first_ny_bar = blocks["new_york"][0]
        c = by_ts[self._ts(first_ny_bar)]
        assert c["session"] == "new_york"
        # A reset would snap VWAP to ~3000. No reset keeps it near the
        # accumulated Asian+London average (~1000-2000 range, well under 3000).
        assert c["vwap"] < 1600, f"expected VWAP still anchored well under 3000, got {c['vwap']}"

    def test_1700_offhours_does_not_introduce_new_session(self):
        bars, candles, by_ts, blocks = self._build()
        first_offhours_bar = blocks["off_hours"][0]
        c = by_ts[self._ts(first_offhours_bar)]
        assert c["session"] == "off_hours"
        # A reset would snap VWAP to ~4000. No reset keeps it well under that,
        # reflecting the full Asian+London+NY accumulation.
        assert c["vwap"] < 2200, f"expected VWAP still anchored well under 4000, got {c['vwap']}"

    def test_1800_et_starts_new_vwap_session(self):
        bars, candles, by_ts, blocks = self._build()
        first_day2_bar = blocks["day2"][0]
        c = by_ts[self._ts(first_day2_bar)]
        assert c["session"] == "asian"
        # This IS a real reset: the accumulator must snap to this bar's own
        # price (~5000), not carry forward the prior day's ~1000-4000 range.
        assert c["vwap"] == pytest.approx(5000.0, abs=1.0)

    def test_reset_matches_exact_compute_vwap_expectation(self):
        """Cross-check against compute_vwap() directly, not just inequalities."""
        bars, candles, by_ts, blocks = self._build()
        # First NY bar's VWAP must equal compute_vwap(asian + london + [that bar]).
        expected = compute_vwap(blocks["asian"] + blocks["london"] + [blocks["new_york"][0]])
        c = by_ts[self._ts(blocks["new_york"][0])]
        assert c["vwap"] == pytest.approx(expected, abs=0.01)


def timezone_utc():
    from datetime import timezone
    return timezone.utc


_CSV_HEADER = (
    "time,open,high,low,close,Volume,EMA 9,EMA 21,EMA 55,EMA 200,VWAP,HOD,LOD,"
    "NY ORB High,NY ORB Low,Bar Type 1 Label,Bar Type 2 Label,Bar Type 3 Label\n"
)


def _csv_row(dt_et: datetime, price: float, *, vwap_pine: str = "") -> str:
    # Constant sentinel ORB so every row survives csv_to_replay's ORB-required skip.
    return (f"{dt_et.isoformat()},{price},{price},{price},{price},100,,,,,{vwap_pine},,,"
            f"9999,1,0,1,0\n")


class TestCsvToReplayVwapReset:
    """convert() (CSV/Pine-export path): same reset semantics, plus VWAP passthrough."""

    def _write_csv(self, tmp_path: Path, rows: list[str]) -> Path:
        csv_path = tmp_path / "MES_5.csv"
        csv_path.write_text(_CSV_HEADER + "".join(rows), encoding="utf-8")
        return csv_path

    def test_sub_session_transitions_do_not_reset(self, tmp_path):
        day_start = datetime(2026, 6, 9, 18, 0, tzinfo=_ET)
        rows = []
        for i in range(36):  # asian @ 1000
            rows.append(_csv_row(day_start + timedelta(minutes=5 * i), 1000.0))
        london_first = day_start + timedelta(hours=9)
        for i in range(26):  # london @ 2000
            rows.append(_csv_row(london_first + timedelta(minutes=5 * i), 2000.0))
        ny_first = day_start + timedelta(hours=15, minutes=30)
        for i in range(30):  # new_york @ 3000
            rows.append(_csv_row(ny_first + timedelta(minutes=5 * i), 3000.0))

        csv_path = self._write_csv(tmp_path, rows)
        jsonl_path = convert(csv_path, tmp_path / "out")
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        by_ts_et = {datetime.fromisoformat(c["timestamp"]).astimezone(_ET).replace(tzinfo=None): c
                    for c in candles}

        london_candle = by_ts_et[london_first.replace(tzinfo=None)]
        assert london_candle["session"] == "london"
        assert london_candle["vwap"] < 1100

        ny_candle = by_ts_et[ny_first.replace(tzinfo=None)]
        assert ny_candle["session"] == "new_york"
        assert ny_candle["vwap"] < 1600

    def test_next_day_1800_et_resets(self, tmp_path):
        day1_start = datetime(2026, 6, 9, 18, 0, tzinfo=_ET)
        day2_start = day1_start + timedelta(hours=24)
        rows = [_csv_row(day1_start + timedelta(minutes=5 * i), 1000.0) for i in range(10)]
        rows += [_csv_row(day2_start + timedelta(minutes=5 * i), 5000.0) for i in range(3)]

        csv_path = self._write_csv(tmp_path, rows)
        jsonl_path = convert(csv_path, tmp_path / "out")
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        by_ts_et = {datetime.fromisoformat(c["timestamp"]).astimezone(_ET).replace(tzinfo=None): c
                    for c in candles}

        first_day2 = by_ts_et[day2_start.replace(tzinfo=None)]
        assert first_day2["vwap"] == pytest.approx(5000.0, abs=1.0)

    def test_pine_vwap_passthrough_still_authoritative(self, tmp_path):
        """When Pine's own VWAP column is present, it must win over computed VWAP —
        this fix must not disturb the existing passthrough priority."""
        day_start = datetime(2026, 6, 9, 18, 0, tzinfo=_ET)
        rows = [_csv_row(day_start, 1000.0, vwap_pine="1234.56")]
        rows += [_csv_row(day_start + timedelta(minutes=5), 1000.0)]  # no passthrough -> computed

        csv_path = self._write_csv(tmp_path, rows)
        jsonl_path = convert(csv_path, tmp_path / "out")
        candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]

        assert candles[0]["vwap"] == pytest.approx(1234.56)
        assert candles[1]["vwap"] == pytest.approx(1000.0)


class TestCsvPolygonVwapParity:
    """csv_to_replay and polygon_to_replay must produce identical VWAP from
    identical OHLCV once both key off the same day-boundary helper."""

    def test_same_bars_same_vwap(self, tmp_path):
        # polygon_to_replay.derive_candles() withholds candles until EMA200 and
        # the first NY ORB are warmed up (skips, doesn't fall back); csv_to_replay
        # has no such warmup requirement (constant sentinel ORB + EMA fallback
        # heuristic instead of a skip). Prepend a climbing warmup run — as in
        # TestPolygonDeriveCandlesVwapReset — so the polygon path actually emits
        # candles across the test day; the CSV path ignores the extra rows'
        # absence and simply has more (non-overlapping) history before them.
        warmup_start = datetime(2026, 6, 8, 2, 0, tzinfo=_ET)
        warmup = _mk_bars(280, warmup_start)
        warmup_end = warmup_start + timedelta(minutes=5 * 280)
        day_start = warmup_end.replace(hour=18, minute=0, second=0, microsecond=0)
        if day_start <= warmup_end:
            day_start += timedelta(days=1)

        blocks = [
            (36, day_start, 1000.0),
            (26, day_start + timedelta(hours=9), 2000.0),
            (30, day_start + timedelta(hours=15, minutes=30), 3000.0),
            (4, day_start + timedelta(hours=23), 4000.0),
        ]
        raw_bars = list(warmup)
        rows = []
        for n, start, price in blocks:
            for i in range(n):
                ts_dt = start + timedelta(minutes=5 * i)
                ts = int(ts_dt.timestamp())
                raw_bars.append({"ts": ts, "open": price, "high": price,
                                  "low": price, "close": price, "volume": 100})
                rows.append(_csv_row(ts_dt, price))

        polygon_candles = derive_candles(raw_bars, "MES", 5)
        polygon_by_ts = {datetime.fromisoformat(c["timestamp"]).astimezone(_ET).replace(tzinfo=None): c["vwap"]
                          for c in polygon_candles}

        csv_path = tmp_path / "MES_5.csv"
        csv_path.write_text(_CSV_HEADER + "".join(rows), encoding="utf-8")
        jsonl_path = convert(csv_path, tmp_path / "out")
        csv_candles = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
        csv_by_ts = {datetime.fromisoformat(c["timestamp"]).astimezone(_ET).replace(tzinfo=None): c["vwap"]
                     for c in csv_candles}

        shared_ts = set(polygon_by_ts) & set(csv_by_ts)
        assert len(shared_ts) > 50, "expected substantial timestamp overlap between the two paths"
        for ts in shared_ts:
            assert polygon_by_ts[ts] == pytest.approx(csv_by_ts[ts], abs=0.01), (
                f"VWAP mismatch at {ts}: polygon={polygon_by_ts[ts]} csv={csv_by_ts[ts]}"
            )
