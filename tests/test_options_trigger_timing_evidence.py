"""AFS-0339: Daily trigger first-SCAN timing evidence, default OFF."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from alert_ranker.multisetup_scanner import build_multisetup_scanner
from alert_ranker.trigger_timing_evidence import (
    _first_sight_r,
    first_observed_daily_trigger,
)


def _observe(tmp_path: Path, *, direction="CALL", price=102.0, now=None):
    return first_observed_daily_trigger(
        journal=tmp_path / "first-sight.jsonl",
        ticker="AAPL",
        timeframe="1D",
        setup_type="DAILY_222",
        direction=direction,
        trigger=100.0,
        invalidation=95.0 if direction == "CALL" else 105.0,
        live_price=price,
        now=now or datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc),
    )


def test_first_scan_is_durable_and_stable_across_rescans(tmp_path):
    first = _observe(tmp_path, price=102.0)
    second = _observe(
        tmp_path, price=104.0, now=datetime(2026, 10, 20, 14, 5, tzinfo=timezone.utc)
    )
    assert first == second
    assert first["trigger_known_at"] == "2026-10-20T14:00:00+00:00"
    assert first["first_eval_price_vs_trigger_r"] == pytest.approx(0.4)
    rows = (tmp_path / "first-sight.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1

    # A restarted scanner still reads the original record.
    assert _observe(tmp_path, price=110.0) == first


@pytest.mark.parametrize(
    ("direction", "price", "expected"),
    [("CALL", 102.0, 0.4), ("CALL", 98.0, -0.4),
     ("PUT", 98.0, 0.4), ("PUT", 102.0, -0.4)],
)
def test_signed_first_eval_movement_in_r(direction, price, expected):
    assert _first_sight_r(
        direction=direction,
        live_price=price,
        trigger=100.0,
        invalidation=95.0 if direction == "CALL" else 105.0,
    ) == pytest.approx(expected)


def test_missing_price_or_zero_risk_cannot_invent_a_distance():
    assert _first_sight_r(
        direction="CALL", live_price=None, trigger=100.0, invalidation=95.0
    ) is None
    assert _first_sight_r(
        direction="PUT", live_price=99.0, trigger=100.0, invalidation=100.0
    ) is None


def test_bad_journal_is_fail_closed_no_false_first_sight(tmp_path):
    path = tmp_path / "first-sight.jsonl"
    path.write_text("{invalid json\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        _observe(tmp_path)


def _test_scan(tmp_path, *, enabled: bool):
    Scanner = build_multisetup_scanner(object)

    class Stub(Scanner):
        async def _build_normalized_data(self, ticker, context, now):
            return {
                "price": self.price,
                "caller_field": "unchanged",
                "paper_setup_candidates": [{
                    "setup_type": "DAILY_222",
                    "setup_timeframe": "1D",
                    "setup_status": "TRIGGERED",
                    "setup_direction": "CALL",
                    "setup_entry_trigger": 100.0,
                    "underlying_invalidation": 95.0,
                    "target_1": 110.0,
                }],
            }

        async def _process_normalized_candidate(self, ticker, normalized, *, source, now):
            self.captured.append((source, dict(normalized)))
            return normalized

    scanner = Stub()
    scanner.captured = []
    scanner.price = 102.0
    scanner.config = SimpleNamespace(
        timezone="America/New_York",
        paper_v1_trigger_timing_observe_enabled=enabled,
        paper_v1_trigger_timing_journal=str(tmp_path / "first-sight.jsonl"),
    )
    now = datetime(2026, 10, 20, 14, 0, tzinfo=timezone.utc)
    asyncio.run(scanner.scan_ticker("AAPL", source="test", now=now))
    return scanner


def test_flag_off_does_not_touch_candidate_fields_or_write_a_journal(tmp_path):
    scanner = _test_scan(tmp_path, enabled=False)
    assert len(scanner.captured) == 2
    _, daily = scanner.captured[1]
    assert daily["caller_field"] == "unchanged"
    assert daily["setup_entry_trigger"] == 100.0
    assert "trigger_known_at" not in daily
    assert "first_eval_price_vs_trigger_r" not in daily
    assert "trigger_timing_evidence_status" not in daily
    assert not (tmp_path / "first-sight.jsonl").exists()


def test_corrupt_timing_journal_cannot_interrupt_existing_scan(tmp_path):
    (tmp_path / "first-sight.jsonl").write_text("{not valid JSON\n", encoding="utf-8")
    scanner = _test_scan(tmp_path, enabled=True)
    assert len(scanner.captured) == 2
    _, daily = scanner.captured[1]
    assert daily["setup_entry_trigger"] == 100.0
    assert daily["trigger_timing_evidence_status"] == "ERROR"
    assert "trigger_known_at" not in daily
    assert "first_eval_price_vs_trigger_r" not in daily


def test_enabled_observer_fields_flow_to_daily_setup_inputs(tmp_path):
    scanner = _test_scan(tmp_path, enabled=True)
    assert len(scanner.captured) == 2
    source, daily = scanner.captured[1]
    assert source == "test:daily"
    assert daily["trigger_known_at"] == "2026-10-20T14:00:00+00:00"
    assert daily["first_eval_price_vs_trigger_r"] == pytest.approx(0.4)
    assert daily["trigger_timing_evidence_status"] == "FIRST_SCAN_OBSERVED"
    assert "trigger_known_at" not in scanner.captured[0][1]
    assert len((tmp_path / "first-sight.jsonl").read_text().splitlines()) == 1
