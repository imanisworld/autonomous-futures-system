from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from research.mnq_sustained_trend_continuation_v1 import (
    CapacityGate,
    Episode,
    ResearchTrade,
    SustainedTrendContinuationV1,
    compute_arm_metrics,
    resolve_trade_on_bar,
)


UTC = timezone.utc
T0 = datetime(2026, 1, 2, 15, 0, tzinfo=UTC)


def _bar(
    ts: datetime,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    session: str = "new_york",
) -> dict:
    return {
        "timestamp": ts.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1000,
        "session": session,
    }


def _metric_bars(first_open: float = 94.0) -> list[dict]:
    closes = [100.0, 106.0, 112.0, 118.0, 124.0, 130.0, 125.0, 120.0]
    out = []
    for i, close in enumerate(closes):
        out.append(
            _bar(
                T0 + timedelta(minutes=15 * i),
                open_=first_open if i == 0 else closes[i - 1],
                high=max(close, closes[i - 1] if i else first_open) + 1.0,
                low=min(close, closes[i - 1] if i else first_open) - 1.0,
                close=close,
            )
        )
    return out


def _episode(
    *,
    state: str = "ARMED",
    pullback_low: float | None = None,
    pullback_high: float | None = None,
    ready_time: datetime | None = None,
) -> Episode:
    return Episode(
        arm_bar_ts=T0.isoformat(),
        arm_close_time=T0,
        arm_window_open=60.0,
        arm_close=140.0,
        atr20=10.0,
        net_displacement=80.0,
        path=100.0,
        efficiency=0.8,
        up_transitions=6,
        arm_move=80.0,
        midpoint=100.0,
        previous_15m_close=140.0,
        state=state,
        pullback_low=pullback_low,
        pullback_high=pullback_high,
        has_qualifying_pullback=state == "PULLBACK_READY",
        pullback_ready_time=ready_time,
    )


def test_insufficient_15m_history_cannot_arm():
    detector = SustainedTrendContinuationV1()
    events = []
    for i in range(19):
        price = 100.0 + i
        events.extend(
            detector.on_15m(
                _bar(
                    T0 + timedelta(minutes=15 * i),
                    open_=price - 0.5,
                    high=price + 5.0,
                    low=price - 5.0,
                    close=price,
                ),
                close_time=T0 + timedelta(minutes=15 * (i + 1)),
            )
        )
    assert not any(event.event == "ARMED" for event in events)


def test_exact_arm_boundaries_pass():
    metrics = compute_arm_metrics(_metric_bars(), atr20=13.0)
    assert metrics.net_displacement == pytest.approx(26.0)
    assert metrics.path == pytest.approx(40.0)
    assert metrics.efficiency == pytest.approx(0.65)
    assert metrics.up_transitions == 5
    assert metrics.passes is True


def test_arm_thresholds_fail_independently():
    below_displacement = compute_arm_metrics(_metric_bars(), atr20=13.01)
    assert below_displacement.efficiency >= 0.65
    assert below_displacement.up_transitions >= 5
    assert below_displacement.passes is False

    below_efficiency = compute_arm_metrics(_metric_bars(first_open=94.1), atr20=12.9)
    assert below_efficiency.net_displacement >= 2.0 * below_efficiency.atr20
    assert below_efficiency.efficiency < 0.65
    assert below_efficiency.up_transitions >= 5
    assert below_efficiency.passes is False

    closes = [100.0, 110.0, 120.0, 130.0, 140.0, 139.0, 138.0, 137.0]
    bars = [
        _bar(
            T0 + timedelta(minutes=15 * i),
            open_=97.0 if i == 0 else closes[i - 1],
            high=max(closes[i], 97.0 if i == 0 else closes[i - 1]) + 1,
            low=min(closes[i], 97.0 if i == 0 else closes[i - 1]) - 1,
            close=closes[i],
        )
        for i in range(8)
    ]
    direction_fail = compute_arm_metrics(bars, atr20=20.0)
    assert direction_fail.net_displacement >= 40.0
    assert direction_fail.efficiency >= 0.65
    assert direction_fail.up_transitions == 4
    assert direction_fail.passes is False


def test_pullback_cannot_consume_arm_bar_or_earlier_bar():
    detector = SustainedTrendContinuationV1()
    detector.episode = _episode()
    same_bar = _bar(T0, open_=139, high=141, low=130, close=138)
    assert detector.on_15m(same_bar, close_time=T0) == []
    assert detector.episode is not None
    assert detector.episode.pullback_count == 0

    earlier = _bar(T0 - timedelta(minutes=15), open_=139, high=141, low=130, close=138)
    assert detector.on_15m(earlier, close_time=T0 - timedelta(minutes=15)) == []
    assert detector.episode.pullback_count == 0


def test_pullback_ready_after_qualifying_completed_bar():
    detector = SustainedTrendContinuationV1()
    detector.episode = _episode()
    bar = _bar(T0 + timedelta(minutes=15), open_=139, high=141, low=130, close=138)
    events = detector.on_15m(bar, close_time=T0 + timedelta(minutes=15))
    assert [event.event for event in events] == ["PULLBACK_READY"]
    assert detector.episode is not None
    assert detector.episode.state == "PULLBACK_READY"


def test_greater_than_50pct_retrace_invalidates():
    detector = SustainedTrendContinuationV1()
    detector.episode = Episode(
        **{
            **_episode().__dict__,
            "arm_window_open": 100.0,
            "arm_close": 140.0,
            "arm_move": 40.0,
            "midpoint": 120.0,
            "previous_15m_close": 140.0,
        }
    )
    bar = _bar(
        T0 + timedelta(minutes=15),
        open_=130.0,
        high=135.0,
        low=119.75,
        close=125.0,
    )
    events = detector.on_15m(bar, close_time=T0 + timedelta(minutes=15))
    assert [event.event for event in events] == ["ARM_INVALIDATED_RETRACE"]
    assert detector.episode is None


def test_midpoint_close_violation_invalidates():
    detector = SustainedTrendContinuationV1()
    detector.episode = Episode(
        **{
            **_episode().__dict__,
            "arm_window_open": 100.0,
            "arm_close": 140.0,
            "arm_move": 40.0,
            "midpoint": 120.0,
            "previous_15m_close": 140.0,
        }
    )
    bar = _bar(
        T0 + timedelta(minutes=15),
        open_=125.0,
        high=126.0,
        low=119.0,
        close=119.5,
    )
    events = detector.on_15m(bar, close_time=T0 + timedelta(minutes=15))
    assert [event.event for event in events] == ["ARM_INVALIDATED_MIDPOINT"]
    assert detector.episode is None


def test_fourth_completed_15m_bar_expires():
    detector = SustainedTrendContinuationV1()
    detector.episode = _episode()
    for i in range(1, 4):
        events = detector.on_15m(
            _bar(
                T0 + timedelta(minutes=15 * i),
                open_=140.0 - i,
                high=141.0,
                low=130.0,
                close=139.0 - i,
            ),
            close_time=T0 + timedelta(minutes=15 * i),
        )
        if i == 1:
            assert [event.event for event in events] == ["PULLBACK_READY"]
    fourth = detector.on_15m(
        _bar(
            T0 + timedelta(minutes=60),
            open_=136.0,
            high=140.0,
            low=130.0,
            close=135.0,
        ),
        close_time=T0 + timedelta(minutes=60),
    )
    assert [event.event for event in fourth] == ["ARM_EXPIRED"]
    assert detector.episode is None


def test_5m_wick_above_pullback_high_does_not_trigger_without_close():
    detector = SustainedTrendContinuationV1()
    detector.episode = _episode(
        state="PULLBACK_READY",
        pullback_low=100.5,
        pullback_high=129.0,
        ready_time=T0,
    )
    bar = _bar(
        T0 + timedelta(minutes=5),
        open_=128.0,
        high=131.0,
        low=127.0,
        close=129.0,
    )
    assert detector.on_5m(bar, close_time=T0 + timedelta(minutes=5)) == []
    assert detector.episode is not None


def test_first_completed_5m_close_above_pullback_high_triggers_once():
    detector = SustainedTrendContinuationV1()
    detector.episode = _episode(
        state="PULLBACK_READY",
        pullback_low=100.5,
        pullback_high=129.0,
        ready_time=T0,
    )
    bar = _bar(
        T0 + timedelta(minutes=5),
        open_=128.0,
        high=131.0,
        low=127.0,
        close=130.0,
    )
    first = detector.on_5m(bar, close_time=T0 + timedelta(minutes=5))
    assert [event.event for event in first] == ["TRIGGERED"]
    assert detector.episode is None
    second = detector.on_5m(
        _bar(
            T0 + timedelta(minutes=10),
            open_=130.0,
            high=133.0,
            low=129.0,
            close=132.0,
        ),
        close_time=T0 + timedelta(minutes=10),
    )
    assert second == []


@pytest.mark.parametrize(
    ("pullback_low", "expected"),
    [(100.5, "TRIGGERED"), (100.25, "STOP_CAP_REJECTED")],
)
def test_stop_cap_120_ticks_admits_121_rejects(pullback_low, expected):
    detector = SustainedTrendContinuationV1()
    detector.episode = _episode(
        state="PULLBACK_READY",
        pullback_low=pullback_low,
        pullback_high=129.0,
        ready_time=T0,
    )
    events = detector.on_5m(
        _bar(
            T0 + timedelta(minutes=5),
            open_=129.0,
            high=131.0,
            low=128.0,
            close=130.0,
        ),
        close_time=T0 + timedelta(minutes=5),
    )
    assert [event.event for event in events] == [expected]
    assert events[0].stop_ticks == pytest.approx(120.0 if expected == "TRIGGERED" else 121.0)


def test_capacity_gate_blocks_busy_and_fourth_fill():
    gate = CapacityGate()
    day = "2026-01-02"
    assert gate.classify_trigger(day) == "FILLED"
    assert gate.classify_trigger(day) == "SKIPPED_BUSY"
    gate.mark_closed()
    assert gate.classify_trigger(day) == "FILLED"
    gate.mark_closed()
    assert gate.classify_trigger(day) == "FILLED"
    gate.mark_closed()
    assert gate.classify_trigger(day) == "SKIPPED_MAX_TRADES"


def test_same_bar_stop_and_target_resolves_stop_first():
    trade = ResearchTrade(
        observation_day="2026-01-02",
        session="new_york",
        arm_bar_ts=T0.isoformat(),
        trigger_ts=(T0 + timedelta(minutes=5)).isoformat(),
        entry=110.0,
        stop=100.0,
        target=130.0,
        stop_ticks=40.0,
    )
    out = resolve_trade_on_bar(
        trade,
        _bar(
            T0 + timedelta(minutes=10),
            open_=110.0,
            high=131.0,
            low=99.0,
            close=120.0,
        ),
        close_time=T0 + timedelta(minutes=10),
    )
    assert out is trade
    assert trade.result == "LOSS"
    assert trade.exit_price == 100.0


def test_research_implementation_has_no_external_broker_or_runtime_imports():
    repo = Path(__file__).resolve().parents[1]
    paths = [
        repo / "research/mnq_sustained_trend_continuation_v1.py",
        repo / "scripts/mnq_sustained_trend_continuation_v1.py",
    ]
    forbidden_prefixes = (
        "execution.tradovate_broker",
        "execution.webull",
        "webhook",
        "risk.risk_engine",
    )
    for path in paths:
        tree = ast.parse(path.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(
            name.startswith(prefix)
            for name in imported
            for prefix in forbidden_prefixes
        ), (path, imported)


def test_offline_runner_fails_closed_on_missing_5m_coverage(tmp_path):
    from scripts.mnq_sustained_trend_continuation_v1 import _validate_5m_coverage

    root15 = tmp_path / "15"
    root5 = tmp_path / "5"
    root15.mkdir()
    root5.mkdir()
    day = "2026-01-02"
    path15 = root15 / f"MNQ_{day}.jsonl"
    path5 = root5 / f"MNQ_{day}.jsonl"
    path15.write_text(
        json.dumps(
            _bar(
                T0,
                open_=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
            )
        )
        + "\n"
    )
    # A 5m bar completely outside the measured 15m interval is not coverage.
    path5.write_text(
        json.dumps(
            _bar(
                T0 + timedelta(hours=2),
                open_=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
            )
        )
        + "\n"
    )
    with pytest.raises(RuntimeError, match="5m trigger coverage is incomplete"):
        _validate_5m_coverage([path15], {day: path5})
