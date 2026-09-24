"""FI-5 / FI-6 — bad or out-of-order 5-minute bar data (#950 audit gap 14).

MNQ 5m bars feed the ARMED wide-stop demo lane. Safe requirement: a bar with a
non-finite or non-positive price is blocked for data quality and never stored,
a bar older than the stored tail is not appended after it, and no demo order
is ever sent with a non-finite or non-positive price.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from tests.fault_injection._harness import FaultRecord
from tests.fault_injection._p2_harness import (
    FIXTURE_STOP_LOW, FakeDemoBroker, fixture_bars, mnq_5m, require, run_fixture_bar,
)

# Fixed in-session bar (the suite's config disables the staleness budget).
BAR_TS = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)

BAD_VALUES = {
    "open_nan": {"open": float("nan")},
    "high_nan": {"high": float("nan")},
    "low_nan": {"low": float("nan")},
}
NON_POSITIVE_LOWS = {"low_zero": {"low": 0.0}, "low_negative": {"low": -5.0}}


def _run(config, tmp_path, monkeypatch, payload):
    from context.five_min_feed import recent_five_min
    from webhook.runner import process_alert

    monkeypatch.setenv("FIVE_MIN_FEED_ENABLED", "true")
    log_dir = str(tmp_path / "logs")
    result = process_alert(payload, config=config, log_dir=log_dir, for_date=BAR_TS.date())
    stored = recent_five_min("MNQ1!", log_dir, 10, for_date=BAR_TS.date())
    return result, stored


def test_fi5_control_clean_bar_is_stored(config, tmp_path, monkeypatch):
    result, stored = _run(config, tmp_path, monkeypatch, mnq_5m(BAR_TS))
    assert result["decision"] == "FIVE_MIN_CONTEXT"
    assert len(stored) == 1


def _assert_blocked(case, overrides, result, stored):
    rec = FaultRecord(
        case=f"FI-5 {case}",
        initial_journal="empty",
        initial_broker="n/a (bar ingestion)",
        injected_failure=f"MNQ 5m payload with {overrides}",
        expected_safe_state="BLOCKED_DATA_QUALITY and no 5m bar stored",
        actual_state=f"decision={result['decision']} stored_bars={stored}",
    )
    assert result["decision"] == "BLOCKED_DATA_QUALITY", str(rec)
    assert stored == [], str(rec)


@pytest.mark.parametrize("case", list(BAD_VALUES))
def test_fi5a_non_finite_price_bar_is_blocked(config, tmp_path, monkeypatch, case):
    result, stored = _run(config, tmp_path, monkeypatch, mnq_5m(BAR_TS, **BAD_VALUES[case]))
    _assert_blocked(case, BAD_VALUES[case], result, stored)


@pytest.mark.parametrize("case", list(NON_POSITIVE_LOWS))
def test_fi5b_non_positive_low_bar_is_blocked(config, tmp_path, monkeypatch, case):
    result, stored = _run(config, tmp_path, monkeypatch, mnq_5m(BAR_TS, **NON_POSITIVE_LOWS[case]))
    _assert_blocked(case, NON_POSITIVE_LOWS[case], result, stored)


def test_fi5_non_finite_price_on_a_15m_bar_is_blocked(config, tmp_path, monkeypatch):
    result, _ = _run(config, tmp_path, monkeypatch,
                     mnq_5m(BAR_TS, timeframe="15m", low=float("nan")))
    assert result["decision"] == "BLOCKED_DATA_QUALITY"
    assert "low" in result["failed_gates"][0]


def test_fi5_infinite_high_is_blocked(config, tmp_path, monkeypatch):
    result, stored = _run(config, tmp_path, monkeypatch, mnq_5m(BAR_TS, high=float("inf")))
    assert result["decision"] == "BLOCKED_DATA_QUALITY"
    assert stored == []


# ── demo lane: no order with a non-finite or non-positive price ───────────────
def _order_prices(order) -> tuple:
    return (order.entry, order.stop, order.target)


def _prices_sane(order) -> bool:
    return all(math.isfinite(p) and p > 0 for p in _order_prices(order))


def test_fi5_control_clean_fixture_submits_a_real_demo_order(config, tmp_path, monkeypatch):
    """Proves the pipeline below is live: the unmodified 3-2-2 fixture passes
    every real risk gate and reaches the (fake) broker."""
    broker = FakeDemoBroker()
    run_fixture_bar(config, tmp_path, monkeypatch, fixture_bars(), broker)
    assert broker.execute_calls == 1
    assert _prices_sane(broker.last_order)


def test_fi5c_nan_stop_candidate_is_never_submitted(config, tmp_path, monkeypatch):
    from context import wide_stop_forward_collector as collector

    real = collector._evaluate_canonical_candidate

    def poisoned(**kwargs):
        decision, state, candidate = real(**kwargs)
        if candidate is not None:
            candidate = dict(candidate, stop=float("nan"))
            decision.setup.stop = float("nan")
        return decision, state, candidate

    monkeypatch.setattr(collector, "_evaluate_canonical_candidate", poisoned)
    broker = FakeDemoBroker()
    events = run_fixture_bar(config, tmp_path, monkeypatch, fixture_bars(), broker)
    rec = FaultRecord(
        case="FI-5c NaN stop reaches the demo pre-submit gates",
        initial_journal="empty demo state",
        initial_broker="flat demo account (fake)",
        injected_failure="real 3-2-2 candidate with setup.stop = NaN",
        expected_safe_state="blocked before the broker; no order sent",
        actual_state=(
            f"execute_calls={broker.execute_calls} order="
            f"{_order_prices(broker.last_order) if broker.last_order else None} "
            f"events={[e.get('lane_result') for e in events]}"
        ),
    )
    assert broker.execute_calls == 0, str(rec)
    blocked = [e.get("lane_failed_rule") for e in events if e.get("lane_result") == "BLOCKED_DEMO"]
    assert blocked == ["incomplete_bracket"], str(rec)


@pytest.mark.parametrize("field", ["entry", "stop", "target"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")],
                         ids=["nan", "inf", "neg_inf"])
def test_fi5c_risk_engine_rejects_non_finite_bracket_price(config, field, value):
    from datetime import datetime, timezone

    from risk.risk_engine import DailyState, RiskEngine, TradeSetup

    prices = {"entry": 20_000.0, "stop": 19_950.0, "target": 20_070.0}
    prices[field] = value
    setup = TradeSetup(
        direction="LONG", rr_ratio=1.4, strategy="strat_4hr_retrigger", instrument="MNQ",
        session="new_york", contracts=1, confluence_grade="B",
        entry_time=datetime(2026, 9, 8, 14, 5, tzinfo=timezone.utc), **prices,
    )
    engine = RiskEngine(config=config)
    result = engine._check_bracket_completeness(setup, DailyState())
    assert result is not None and result.failed_rule == "incomplete_bracket"
    assert field in result.reason


@pytest.mark.parametrize("bad_low", [float("nan"), 0.0], ids=["low_nan", "low_zero"])
def test_fi5d_bad_low_in_bar_history_never_becomes_a_bad_order(config, tmp_path, monkeypatch, bad_low):
    """Preregistered UNKNOWN; first run: PASS (NaN yields no candidate, a zero
    stop is rejected as an incomplete bracket)."""
    bars = fixture_bars()
    hits = 0
    for bar in bars:
        if bar["low"] == FIXTURE_STOP_LOW:
            bar["low"] = bad_low
            hits += 1
    require(hits == 1, "exactly one stop-anchor bar poisoned")
    broker = FakeDemoBroker()
    run_fixture_bar(config, tmp_path, monkeypatch, bars, broker)
    assert broker.execute_calls == 0 or _prices_sane(broker.last_order)


# ── FI-6: out-of-order bar ────────────────────────────────────────────────────
def test_fi6_older_bar_is_not_appended_after_newer(config, tmp_path, monkeypatch):
    newer = BAR_TS
    older = BAR_TS - timedelta(minutes=5)
    first, _ = _run(config, tmp_path, monkeypatch, mnq_5m(newer))
    second, stored = _run(config, tmp_path, monkeypatch, mnq_5m(older))
    require(first["decision"] == "FIVE_MIN_CONTEXT", "newer bar stored first")
    order = [b["ts"] for b in stored]
    rec = FaultRecord(
        case="FI-6 out-of-order 5m bar",
        initial_journal=f"5m history holds {newer.isoformat()}",
        initial_broker="n/a (bar ingestion)",
        injected_failure=f"a bar stamped {older.isoformat()} arrives afterwards",
        expected_safe_state="history stays oldest→newest (older bar rejected or placed in order)",
        actual_state=f"second decision={second['decision']} stored order={order}",
    )
    assert order == sorted(order), str(rec)


def test_fi6_stale_bar_reaches_no_lane_and_the_next_bar_is_stored(config, tmp_path, monkeypatch):
    import context.wide_stop_forward_router as router

    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "paper_sim")
    monkeypatch.setenv("WIDE_STOP_LEDGER_EPOCH_START", "2026-06-01T00:00:00+00:00")
    monkeypatch.setenv("DAILY_22_EPOCH_START", "2026-06-01T00:00:00+00:00")
    fed: list[str] = []
    monkeypatch.setattr(router, "process_paper_five_min_bar",
                        lambda **kw: fed.append(kw["payload"].timestamp))
    newer, older, following = BAR_TS, BAR_TS - timedelta(minutes=5), BAR_TS + timedelta(minutes=5)
    for ts in (newer, older, following):
        _run(config, tmp_path, monkeypatch, mnq_5m(ts))
    _, stored = _run(config, tmp_path, monkeypatch, mnq_5m(following))  # resend: still fine
    require(len(fed) >= 2, "the paper lane is fed live bars in this setup")
    assert [b["ts"] for b in stored] == [newer.isoformat(), following.isoformat()]
    assert older.isoformat() not in fed
    assert fed[:2] == [newer.isoformat(), following.isoformat()]
