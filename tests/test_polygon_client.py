"""Tests for the Polygon/Massive futures client, contract roll mapping, and
the BarHistory backfill merge. No network: HTTP is faked via httpx transports."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pytest

from context.bar_history import BarHistory
from sources.polygon_client import (
    PolygonError,
    PolygonFuturesClient,
    _ns_to_utc,
    contract_schedule,
    front_contract,
)


# ── contract roll mapping ─────────────────────────────────────────────────────
class TestFrontContract:
    def test_known_live_contract(self):
        # Verified against the live API 2026-06-09: front month is MESM6.
        assert front_contract("MES", date(2026, 6, 9)) == "MESM6"

    def test_known_historical_contract(self):
        # Verified against the live API: Sep 2024 bars exist under MESU4.
        assert front_contract("MES", date(2024, 9, 3)) == "MESU4"

    def test_rolls_before_expiry(self):
        # June 2026 expiry = 3rd Friday = 2026-06-19; roll 8 days prior = 06-11.
        assert front_contract("MES", date(2026, 6, 10)) == "MESM6"
        assert front_contract("MES", date(2026, 6, 11)) == "MESU6"

    def test_year_rollover(self):
        # Mid-December after the Z roll → next year's March contract.
        assert front_contract("MNQ", date(2025, 12, 20)) == "MNQH6"

    def test_non_quarterly_symbol_rejected(self):
        with pytest.raises(PolygonError):
            front_contract("MGC", date(2026, 6, 9))  # gold is not quarterly

    def test_schedule_spans_rolls(self):
        segs = contract_schedule("MES", date(2026, 3, 1), date(2026, 7, 1))
        tickers = [t for t, _, _ in segs]
        assert tickers == ["MESH6", "MESM6", "MESU6"]
        # Segments are contiguous and cover the whole range.
        assert segs[0][1] == date(2026, 3, 1)
        assert segs[-1][2] == date(2026, 7, 1)
        for (_, _, prev_end), (_, next_start, _) in zip(segs, segs[1:]):
            assert next_start == prev_end.fromordinal(prev_end.toordinal() + 1)


# ── timestamp normalization ───────────────────────────────────────────────────
class TestNsTimestamps:
    def test_nanoseconds_parse_to_correct_utc(self):
        # Real value from the live API: 2026-06-09 17:00 UTC (13:00 ET).
        dt = _ns_to_utc(1781024400000000000)
        assert dt == datetime(2026, 6, 9, 17, 0, tzinfo=timezone.utc)
        # NOT year 1781 — the epoch-unit trap from the bar-history bug.
        assert dt.year == 2026

    def test_garbage_returns_none(self):
        assert _ns_to_utc(None) is None
        assert _ns_to_utc("not-a-number") is None


# ── fetch + pagination ────────────────────────────────────────────────────────
def _fake_client(pages: list[dict], calls: list[str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.headers.get("Authorization") == "Bearer test-key"
        return httpx.Response(200, json=pages.pop(0))
    return httpx.Client(transport=httpx.MockTransport(handler))


def _row(ns: int, o=100.0, h=101.0, lo=99.0, c=100.5, v=10):
    return {"ticker": "MESM6", "window_start": ns, "open": o, "high": h,
            "low": lo, "close": c, "volume": v}


class TestFetchBars:
    def test_paginates_sorts_and_normalizes(self):
        ns1 = int(datetime(2026, 6, 9, 13, 0, tzinfo=timezone.utc).timestamp() * 1e9)
        ns2 = ns1 - 900 * int(1e9)  # one 15m bar earlier
        pages = [
            {"results": [_row(ns1)], "next_url": "https://api.polygon.io/futures/v1/aggs/MESM6?cursor=abc"},
            {"results": [_row(ns2, c=99.0)]},
        ]
        calls: list[str] = []
        client = PolygonFuturesClient(api_key="test-key", client=_fake_client(pages, calls))
        bars = client.fetch_bars("MESM6", date(2026, 6, 9), date(2026, 6, 9))
        assert len(calls) == 2  # followed next_url
        assert len(bars) == 2
        assert bars[0].ts < bars[1].ts  # oldest → newest despite newest-first API
        assert bars[0].close == 99.0
        assert bars[1].ts.year == 2026

    def test_unconfigured_raises(self):
        client = PolygonFuturesClient(api_key="")
        assert not client.configured
        with pytest.raises(PolygonError):
            client.fetch_bars("MESM6", date(2026, 6, 9), date(2026, 6, 9))

    def test_malformed_rows_skipped_never_fabricated(self):
        ns = int(datetime(2026, 6, 9, 13, 0, tzinfo=timezone.utc).timestamp() * 1e9)
        pages = [{"results": [_row(ns), {"window_start": ns, "open": "junk"},
                              {"no_window_start": True}]}]
        calls: list[str] = []
        client = PolygonFuturesClient(api_key="test-key", client=_fake_client(pages, calls))
        bars = client.fetch_bars("MESM6", date(2026, 6, 9), date(2026, 6, 9))
        assert len(bars) == 1

    def test_429_retries_then_succeeds(self):
        ns = int(datetime(2026, 6, 9, 13, 0, tzinfo=timezone.utc).timestamp() * 1e9)
        responses = [
            httpx.Response(429, json={"status": "ERROR", "error": "rate limited"}),
            httpx.Response(429, json={"status": "ERROR", "error": "rate limited"}),
            httpx.Response(200, json={"results": [_row(ns)]}),
        ]
        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)
        client = PolygonFuturesClient(
            api_key="test-key",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
            retry_sleep_seconds=0.01,
        )
        bars = client.fetch_bars("MESM6", date(2026, 6, 9), date(2026, 6, 9))
        assert len(bars) == 1 and not responses  # consumed all three responses

    def test_http_error_raises_polygon_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="unauthorized")
        client = PolygonFuturesClient(
            api_key="test-key",
            client=httpx.Client(transport=httpx.MockTransport(handler)),
        )
        with pytest.raises(PolygonError, match="403"):
            client.fetch_bars("MESM6", date(2026, 6, 9), date(2026, 6, 9))


# ── BarHistory backfill merge ─────────────────────────────────────────────────
class TestMergeBackfill:
    def _bar(self, hour: int, minute: int, close: float) -> dict:
        ts = datetime(2026, 6, 9, hour, minute, tzinfo=timezone.utc)
        return {"ts": ts.isoformat(), "open": close, "high": close + 1,
                "low": close - 1, "close": close, "volume": 10, "timeframe": "15m"}

    def test_fills_gap_in_order_live_bars_win(self, tmp_path):
        hist = BarHistory(log_dir=str(tmp_path))
        d = date(2026, 6, 9)
        # Live ingestion recorded 13:00 and 14:00 — the 13:15..13:45 bars gapped.
        hist.record("MES", ts="2026-06-09T13:00:00+00:00", open=1, high=2, low=0,
                    close=100.0, timeframe="15m", for_date=d)
        hist.record("MES", ts="2026-06-09T14:00:00+00:00", open=1, high=2, low=0,
                    close=104.0, timeframe="15m", for_date=d)
        backfill = [
            self._bar(13, 15, 101.0),
            self._bar(13, 30, 102.0),
            self._bar(13, 45, 103.0),
            # Collides with a live bar — must NOT replace it.
            {**self._bar(14, 0, 999.0)},
        ]
        added = hist.merge_backfill("MES", d, backfill)
        assert added == 3
        bars = hist.recent("MES", 10, for_date=d, lookback_days=1)
        assert [b["close"] for b in bars] == [100.0, 101.0, 102.0, 103.0, 104.0]
        # Chronological order restored, backfilled bars tagged, live bars untouched.
        assert [b["ts"] for b in bars] == sorted(b["ts"] for b in bars)
        assert bars[1]["source"] == "polygon"
        assert "source" not in bars[0]
        assert bars[4]["close"] == 104.0  # live bar won the collision

    def test_idempotent(self, tmp_path):
        hist = BarHistory(log_dir=str(tmp_path))
        d = date(2026, 6, 9)
        bars = [self._bar(13, 15, 101.0)]
        assert hist.merge_backfill("MES", d, bars) == 1
        assert hist.merge_backfill("MES", d, bars) == 0

    def test_gap_detection_clears_after_backfill(self, tmp_path):
        hist = BarHistory(log_dir=str(tmp_path))
        d = date(2026, 6, 9)
        hist.record("MES", ts="2026-06-09T13:00:00+00:00", open=1, high=2, low=0,
                    close=100.0, timeframe="15m", for_date=d)
        gap = hist.detect_gap("MES", "2026-06-09T14:00:00+00:00", 15, for_date=d)
        assert gap["gapped"] and gap["missing_bars"] == 3
        hist.merge_backfill("MES", d, [self._bar(13, 15, 101.0),
                                       self._bar(13, 30, 102.0),
                                       self._bar(13, 45, 103.0)])
        gap = hist.detect_gap("MES", "2026-06-09T14:00:00+00:00", 15, for_date=d)
        assert not gap["gapped"]

    def test_file_is_valid_jsonl_after_merge(self, tmp_path):
        hist = BarHistory(log_dir=str(tmp_path))
        d = date(2026, 6, 9)
        hist.merge_backfill("MES", d, [self._bar(13, 15, 101.0)])
        path = tmp_path / "bars_MES_2026-06-09.jsonl"
        lines = path.read_text().strip().splitlines()
        assert all(json.loads(line) for line in lines)
