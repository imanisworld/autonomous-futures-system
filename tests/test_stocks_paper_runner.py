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
    assert records[1].raw_entry_price == 50.3
    assert records[1].raw_exit_price == 48.5
    assert records[1].modeled_entry_price > 50.3  # buy leg slipped worse (higher)
    assert records[1].modeled_exit_price < 48.5  # sell leg slipped worse (lower)
    assert records[1].entry_slippage_dollars > 0
    assert records[1].exit_slippage_dollars > 0
    assert records[1].regulatory_fees_dollars > 0
    assert records[1].total_friction_dollars == (
        records[1].entry_slippage_dollars + records[1].exit_slippage_dollars + records[1].regulatory_fees_dollars
    )
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
    records_after_first = read_all_records(journal_path)

    # Rerun with DIFFERENT (mutated) bars for the same date -- if a rerun could
    # ever alter the original decision, feeding wildly different price action
    # (a crash instead of a breakout) would prove it. It must not.
    mutated_qqq_bars = _flat_first_hour(100.0) + [_bar(60, 100.0, 100.1, 90.0, 90.5, 1000)]
    mutated_tqqq_bars = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.1, 30.0, 31.0, 500)]
    mutated_sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 30.0, 20.0, 29.0, 500)]

    second = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=mutated_qqq_bars, tqqq_bars_full_day=mutated_tqqq_bars,
        sqqq_bars_full_day=mutated_sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T18:00:00-04:00", **COMMON_KWARGS,
    )
    assert second.ok is True
    assert second.journaled is False
    assert "already journaled" in second.message
    lines_after_second = len(journal_path.read_text(encoding="utf-8").splitlines())
    assert lines_after_second == lines_after_first  # append-only, but nothing new written on a dedup hit
    assert read_all_records(journal_path) == records_after_first  # original decision byte-for-byte unchanged


def test_decision_never_sees_bars_after_the_cutoff(tmp_path):
    # Two runs with an IDENTICAL decision window (first hour + confirmation bar)
    # but wildly DIFFERENT bars after the cutoff -- one keeps rallying, one
    # crashes. If the decision peeked at future bars this would diverge; it
    # must not, since the decision is only supposed to see bars through the
    # confirmation bar.
    common_prefix_qqq = _flat_first_hour(100.0) + [_bar(60, 100.0, 101.5, 100.0, 101.2, 1000)]
    common_prefix_tqqq = _flat_vehicle_first_hour(50.0) + [_bar(60, 50.0, 50.5, 49.8, 50.3, 500)]
    common_prefix_sqqq = _flat_vehicle_first_hour(20.0) + [_bar(60, 20.0, 20.1, 19.9, 20.0, 500)]

    rallies_after = [_bar(65, 101.2, 105.0, 101.0, 104.5, 1000)]
    rallies_after_tqqq = [_bar(65, 50.3, 60.0, 50.2, 59.0, 500)]
    rallies_after_sqqq = [_bar(65, 20.0, 20.1, 15.0, 15.5, 500)]

    crashes_after = [_bar(65, 101.2, 101.3, 50.0, 50.5, 1000)]
    crashes_after_tqqq = [_bar(65, 50.3, 50.4, 10.0, 10.5, 500)]
    crashes_after_sqqq = [_bar(65, 20.0, 60.0, 20.0, 59.0, 500)]

    journal_a = tmp_path / "journal_a.jsonl"
    result_a = run_paper_session(
        date="2026-07-06",
        qqq_bars_full_day=common_prefix_qqq + rallies_after,
        tqqq_bars_full_day=common_prefix_tqqq + rallies_after_tqqq,
        sqqq_bars_full_day=common_prefix_sqqq + rallies_after_sqqq,
        journal_path=journal_a, recorded_at="2026-07-06T16:05:00-04:00", **COMMON_KWARGS,
    )
    journal_b = tmp_path / "journal_b.jsonl"
    result_b = run_paper_session(
        date="2026-07-06",
        qqq_bars_full_day=common_prefix_qqq + crashes_after,
        tqqq_bars_full_day=common_prefix_tqqq + crashes_after_tqqq,
        sqqq_bars_full_day=common_prefix_sqqq + crashes_after_sqqq,
        journal_path=journal_b, recorded_at="2026-07-06T16:05:00-04:00", **COMMON_KWARGS,
    )

    assert result_a.decision == result_b.decision == "TRADE"
    decision_a = read_all_records(journal_a)[0]
    decision_b = read_all_records(journal_b)[0]
    # Every decision-time field must be identical -- only what happens AFTER
    # the cutoff (final_status / net_pnl) is allowed to differ.
    for field_name in (
        "direction", "vehicle_symbol", "decision", "reason", "entry_trigger",
        "stop_price", "target_1", "target_2", "qqq_price", "status",
    ):
        assert getattr(decision_a, field_name) == getattr(decision_b, field_name), field_name
    # And the two runs DO diverge afterward -- proving the bars-after-cutoff
    # weren't simply ignored everywhere, only during decision-making.
    assert result_a.final_status != result_b.final_status or result_a.net_pnl_dollars != result_b.net_pnl_dollars


def test_entry_time_is_strictly_after_the_decision_bar(tmp_path):
    journal_path = tmp_path / "journal.jsonl"
    decision_bar_timestamp = "2026-07-06T10:30:00-04:00"  # the confirmation bar itself
    qqq_bars = _flat_first_hour(100.0) + [
        _bar(60, 100.0, 101.5, 100.0, 101.2, 1000),  # decision bar
        _bar(65, 101.2, 101.6, 101.0, 101.4, 1000),  # first bar the lifecycle may use
    ]
    tqqq_bars = _flat_vehicle_first_hour(50.0) + [
        _bar(60, 50.0, 50.5, 49.8, 50.3, 500),
        _bar(65, 50.3, 51.0, 50.2, 50.8, 500),
    ]
    sqqq_bars = _flat_vehicle_first_hour(20.0) + [_bar(m, 20, 20.1, 19.9, 20.0, 500) for m in (60, 65)]

    result = run_paper_session(
        date="2026-07-06", qqq_bars_full_day=qqq_bars, tqqq_bars_full_day=tqqq_bars,
        sqqq_bars_full_day=sqqq_bars, journal_path=journal_path,
        recorded_at="2026-07-06T16:05:00-04:00", **COMMON_KWARGS,
    )
    assert result.decision == "TRADE"
    records = read_all_records(journal_path)
    lifecycle_record = records[-1]
    assert lifecycle_record.entry_time is not None
    assert lifecycle_record.entry_time > decision_bar_timestamp
    # the entry price must be the LATER bar's open (50.3 pre-slippage), never
    # the decision bar's own open (50.0)
    assert lifecycle_record.raw_entry_price == 50.3


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
