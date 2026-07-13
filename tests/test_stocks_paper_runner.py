"""
tests/test_stocks_paper_runner.py

stocks_advisory/paper_runner.py tests -- the end-to-end integration
point tying qqq_signal_builder -> tqqq_sqqq_decision (untouched) ->
paper_simulator -> paper_journal together for one full trading day.
Proves the once-per-day dedup gate, the decision+lifecycle journal
entries, cross-day stale-position cleanup, and no
Robinhood/broker/execution/futures/options_manager coupling.
"""

from __future__ import annotations

import ast
from pathlib import Path

import stocks_advisory.paper_runner as paper_runner_module
from stocks_advisory.backtest_models import Bar
from stocks_advisory.paper_journal import PaperJournalRecord, append_record, read_all_records
from stocks_advisory.paper_runner import STRATEGY_VERSION, run_paper_session


def _bar(minute_offset: int, o: float, h: float, l: float, c: float, v: int = 1000) -> Bar:
    hour = 9 + (30 + minute_offset) // 60
    minute = (30 + minute_offset) % 60
    return Bar(
        timestamp=f"2026-07-06T{hour:02d}:{minute:02d}:00-04:00",
        open=o, high=h, low=l, close=c, volume=v,
    )


def _flat_first_hour(base: float = 100.0) -> list[Bar]:
    # Symmetric high/low around `base` with close==base keeps the typical
    # price ((h+l+c)/3) exactly `base` for every bar -- a non-zero
    # first-hour range (satisfies allowed_min_first_hour_range) while
    # keeping the causal VWAP trivially predictable at exactly `base`
    # through the whole first hour.
    return [_bar(i * 5, base, base + 0.2, base - 0.2, base, 1000) for i in range(12)]


def _flat_vehicle_first_hour(base: float) -> list[Bar]:
    return [_bar(i * 5, base, base, base, base, 500) for i in range(12)]


COMMON_KWARGS = dict(
    qqq_previous_day_close=99.5,
    qqq_previous_day_high=100.0,
    qqq_previous_day_low=98.5,
    qqq_relative_volume=1.1,
    allowed_max_gap_percent=2.0,
    allowed_min_first_hour_range=0.05,
    allowed_max_first_hour_range=5.0,
    data_source="test-fixture",
)


def test_take_paper_then_stop_hit_journals_decision_and_exit(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    qqq_bars = _flat_first_hour(100.0) + [
        _bar(60, 100.0, 101.5, 100.0, 101.2, 1000),   # confirmation: breaks up, above VWAP -> TAKE_PAPER
        _bar(65, 101.2, 101.6, 101.0, 101.4, 1000),   # still above stop -> confirms entry
        _bar(70, 101.4, 101.4, 98.0, 99.0, 1000),     # closes back below VWAP -> stop hit
    ]
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [
        _bar(60, 50.0, 50.5, 49.8, 50.3, 500),
        _bar(65, 50.3, 51.0, 50.2, 50.8, 500),        # entry fills at this bar's OPEN (50.3)
        _bar(70, 50.8, 51.0, 48.0, 48.5, 500),        # exit at this bar's CLOSE (48.5)
    ]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(m, 20, 20.1, 19.9, 20.0, 500) for m in (60, 65, 70)]

    result = run_paper_session(
        date="2026-07-06",
        qqq_bars_full_day=qqq_bars,
        tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars,
        journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00",
        **COMMON_KWARGS,
    )
    assert result.ok is True
    assert result.decision == "TRADE"
    assert result.final_status == "exited"
    assert result.net_pnl_dollars is not None
    assert result.net_pnl_dollars < 0  # this scenario is a losing trade

    records = read_all_records(journal_path)
    assert len(records) == 2
    assert records[0].decision == "TRADE"
    assert records[0].status == "watching"
    assert records[0].direction == "long_tqqq"
    assert records[0].vehicle_symbol == "TQQQ"
    assert records[1].status == "exited"
    assert records[1].modeled_entry_price == 50.3
    assert records[1].modeled_exit_price == 48.5
    assert "stop hit" in records[1].exit_reason


def test_no_trade_journals_single_record(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    # small oscillation so the first-hour range is non-zero but the confirmation bar stays inside it
    qqq_bars = [
        _bar(i * 5, 100.0, 100.1, 99.9, 100.0, 1000) for i in range(12)
    ] + [_bar(60, 100.0, 100.05, 99.95, 100.0, 1000)]  # confirmation bar stays inside the range
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.1, 49.9, 50.0, 500)]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 20.1, 19.9, 20.0, 500)]

    result = run_paper_session(
        date="2026-07-06",
        qqq_bars_full_day=qqq_bars,
        tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars,
        journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00",
        **COMMON_KWARGS,
    )
    assert result.ok is True
    assert result.decision == "NO_TRADE"
    records = read_all_records(journal_path)
    assert len(records) == 1
    assert records[0].status == "no_trade"


def test_expired_when_no_bars_remain_after_confirmation(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    qqq_bars = _flat_first_hour(100.0) + [_bar(60, 100.0, 101.5, 100.0, 101.2, 1000)]  # nothing after confirmation
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.5, 49.8, 50.3, 500)]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 20.1, 19.9, 20.0, 500)]

    result = run_paper_session(
        date="2026-07-06",
        qqq_bars_full_day=qqq_bars,
        tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars,
        journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00",
        **COMMON_KWARGS,
    )
    assert result.ok is True
    assert result.decision == "TRADE"
    assert result.final_status == "expired"
    records = read_all_records(journal_path)
    assert records[-1].status == "expired"
    assert records[-1].gross_pnl_dollars == 0.0


def test_dedup_refuses_second_evaluation_same_day(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    qqq_bars = _flat_first_hour(100.0) + [_bar(60, 100.0, 101.5, 100.0, 101.2, 1000)]
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.5, 49.8, 50.3, 500)]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 20.1, 19.9, 20.0, 500)]

    first = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=qqq_bars, tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00", **COMMON_KWARGS,
    )
    assert first.journaled is True
    lines_after_first = len(journal_path.read_text(encoding="utf-8").splitlines())

    second = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=qqq_bars, tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T18:00:00-04:00", **COMMON_KWARGS,
    )
    assert second.ok is True
    assert second.journaled is False
    assert "already journaled" in second.message
    lines_after_second = len(journal_path.read_text(encoding="utf-8").splitlines())
    assert lines_after_second == lines_after_first  # append-only, but nothing new written on a dedup hit


def test_before_first_hour_closes_is_not_journaled(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    qqq_bars = _flat_first_hour(100.0)[:6]  # only half the first hour
    tqqq_bars = _flat_vehicle_first_hour(50.0)[:6]
    sqqq_bars = _flat_vehicle_first_hour(20.0)[:6]

    result = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=qqq_bars, tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T10:00:00-04:00", **COMMON_KWARGS,
    )
    assert result.ok is False
    assert result.journaled is False
    assert "has not closed yet" in result.message
    assert read_all_records(journal_path) == []


def test_malformed_data_after_first_hour_is_journaled_as_invalid(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    qqq_bars = _flat_first_hour(100.0) + [_bar(60, 100.0, 101.5, 100.0, 101.2, 1000)]
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.5, 49.8, 50.3, 500)]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 20.1, 19.9, 20.0, 500)]
    kwargs = dict(COMMON_KWARGS)
    kwargs["qqq_previous_day_close"] = 0.0  # invalid -> build_qqq_signal rejects

    result = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=qqq_bars, tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00", **kwargs,
    )
    assert result.ok is True
    assert result.decision == "INVALID"
    records = read_all_records(journal_path)
    assert len(records) == 1
    assert records[0].decision == "INVALID"
    assert "previous-day close" in records[0].reason


def test_stale_open_position_from_prior_day_is_force_expired(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    # Seed a WATCHING record for an EARLIER day that was never resolved.
    stale = PaperJournalRecord(
        trade_date="2026-07-01",
        strategy_version=STRATEGY_VERSION,
        recorded_at="2026-07-01T10:35:00-04:00",
        data_source="test-fixture",
        signal_symbol="QQQ",
        qqq_price=100.0,
        direction="long_tqqq",
        vehicle_symbol="TQQQ",
        decision="TRADE",
        reason="QQQ broke above first-hour high and holds above VWAP",
        entry_trigger="QQQ above first-hour high 100.00 and above VWAP",
        stop_price=100.0,
        status="watching",
    )
    append_record(journal_path, stale)

    # NO_TRADE day today so we isolate the prior-position cleanup behavior.
    qqq_bars = [_bar(i * 5, 100.0, 100.1, 99.9, 100.0, 1000) for i in range(12)] + [
        _bar(60, 100.0, 100.05, 99.95, 100.0, 1000)
    ]
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.1, 49.9, 50.0, 500)]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 20.1, 19.9, 20.0, 500)]

    result = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=qqq_bars, tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00", **COMMON_KWARGS,
    )
    assert result.ok is True
    assert result.resolved_prior_positions == ("2026-07-01",)

    records = read_all_records(journal_path)
    prior_records = [r for r in records if r.trade_date == "2026-07-01"]
    assert len(prior_records) == 2
    assert prior_records[-1].status == "expired"
    assert prior_records[-1].gross_pnl_dollars == 0.0
    assert "no overnight hold" in prior_records[-1].notes


def test_no_broker_execution_futures_options_manager_import():
    source = Path(paper_runner_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_prefixes = ("broker", "execution", "futures", "options_manager", "robinhood")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.lower().startswith(forbidden_prefixes), alias.name
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").lower()
            assert not module.startswith(forbidden_prefixes), module
