from __future__ import annotations

from datetime import datetime, timedelta, timezone

from context.structural_regime import (
    INSUFFICIENT,
    RANGE_ACTIVE,
    TREND_UP,
    classify_structural_regime,
    observe_structured_range_candidates,
)


def _bars(values, start=datetime(2026, 7, 1, tzinfo=timezone.utc)):
    out = []
    for idx, (high, low, close) in enumerate(values):
        out.append(
            {
                "ts": (start + timedelta(minutes=15 * idx)).isoformat(),
                "open": (high + low) / 2,
                "high": high,
                "low": low,
                "close": close,
            }
        )
    return out


def test_confirmed_three_swing_trend_is_shared_for_mes_and_mnq():
    closes = [
        100, 102, 105, 103, 101,
        103, 106, 109, 107, 104,
        106, 110, 113, 111, 108,
        110, 114, 117, 115, 113,
    ]
    values = [(close + 1, close - 1, close) for close in closes]
    bars = _bars(values)
    mes = classify_structural_regime(bars, instrument="MES")
    mnq = classify_structural_regime(bars, instrument="MNQ")
    assert mes.condition == TREND_UP
    assert mnq.condition == TREND_UP
    assert mes.gate_authoritative is False
    assert mes.to_dict(current_market_condition="RANGE_BOUND")["structural_mismatch"] is True


def test_pivot_is_not_visible_until_right_bars_close():
    values = [(101 + i, 99 + i, 100 + i) for i in range(9)]
    result = classify_structural_regime(_bars(values), instrument="MES")
    assert result.condition == INSUFFICIENT


def test_active_range_observation_is_never_executable(monkeypatch):
    from context import structural_regime as module

    regime = module.StructuralRegime(
        RANGE_ACTIVE,
        None,
        "active",
        {"range_high": 110.0, "range_low": 100.0, "active_range_trigger": "SWEEP_RECLAIM_LOW"},
    )
    candidates = observe_structured_range_candidates(regime, _bars([(105, 99, 101)]), instrument="MNQ")
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["observation_only"] is True
    assert candidate["selected"] is False
    assert candidate["attempted"] is False
    assert candidate["risk_evaluated"] is False
    assert candidate["broker_evaluated"] is False


def test_repeated_boundaries_with_sweep_classify_active_range():
    closes = [110, 115, 120, 115, 110, 105, 100, 105] * 4 + [110]
    values = [(close + 0.5, close - 0.5, close) for close in closes]
    values[-1] = (111, 96, 101)  # sweep beyond tolerance, then reclaim the floor
    result = classify_structural_regime(
        _bars(values), instrument="MES", pivot_width=1
    )
    assert result.condition == RANGE_ACTIVE
    assert result.inputs["active_range_trigger"] == "SWEEP_RECLAIM_LOW"
    assert result.to_dict(current_market_condition="RANGE_BOUND")["structural_mismatch"] is False


def test_gap_fails_closed_when_contiguous_tail_is_too_short():
    bars = _bars([(101 + i, 99 + i, 100 + i) for i in range(12)])
    bars[-1]["ts"] = (datetime(2026, 7, 3, tzinfo=timezone.utc)).isoformat()
    result = classify_structural_regime(bars, instrument="MNQ")
    assert result.condition == INSUFFICIENT
    assert result.inputs["bar_gap_detected"] is True


def test_runner_journals_structural_fields_without_reaching_risk_or_broker(
    monkeypatch, tmp_path
):
    from dataclasses import replace
    from datetime import date

    import webhook.runner as runner
    from config.settings import load_config
    from journal.journal_logger import JournalLogger
    from tests.test_e2e_scenarios import _base_payload

    class _RiskMustNotBeConstructed:
        def __init__(self, *args, **kwargs):
            raise AssertionError("structural observation must not reach risk")

    def _broker_must_not_execute(*args, **kwargs):
        raise AssertionError("structural observation must not reach broker")

    monkeypatch.setattr(runner, "RiskEngine", _RiskMustNotBeConstructed)
    monkeypatch.setattr(runner.PaperBroker, "execute_bracket", _broker_must_not_execute)
    cfg = replace(load_config(), max_staleness_seconds=10_000_000)
    day = date(2026, 5, 23)
    log_dir = str(tmp_path / "logs")
    payload = _base_payload(market_condition="RANGE_BOUND", trend_strength="MODERATE")

    result = runner.process_alert(payload, config=cfg, log_dir=log_dir, for_date=day)

    assert result["decision"] == "NO_TRADE"
    assert result["failed_gates"] == ["MARKET_CONDITION_NOT_TRENDING"]
    journal = JournalLogger(log_dir=log_dir)
    entry = journal._read_entries(journal._journal_path(day))[-1]
    context = entry["context"]
    assert context["market_condition"] == "RANGE_BOUND"
    assert context["structural_market_condition"] == INSUFFICIENT
    assert context["structural_gate_authoritative"] is False
