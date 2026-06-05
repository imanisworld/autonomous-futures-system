"""Integration: rolling bar history feeds regime + gap detection through
process_alert (Phase 3).

Verifies the user-facing win — the system judges regime from CONTINUOUS recent
price action, so a directional move that Pine mislabels CHOPPY is no longer
chop-blocked, even when the single payload has no 3-bar strat run.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from config.settings import load_config
from context.bar_history import BarHistory
from tests.test_webhook import _base_payload
from webhook.runner import process_alert


def _cfg():
    # Relax staleness so fixed-timestamp bars aren't rejected by the quality gate.
    return replace(load_config(), max_staleness_seconds=10_000_000)


def _seed_downtrend(log_dir, base):
    bh = BarHistory(log_dir=log_dir)
    for i, c in enumerate([7600, 7590, 7580, 7572, 7565]):
        t = base + timedelta(minutes=15 * i)
        bh.record("MES", ts=t.isoformat(), open=c + 2, high=c + 3, low=c - 3,
                  close=c, volume=1500, timeframe="15")


def test_window_vetoes_chop_label_on_continuous_downtrend(tmp_path):
    log_dir = str(tmp_path / "logs")
    base = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
    _seed_downtrend(log_dir, base)

    # Fresh CHOPPY-labeled bar continuing the decline, NO 3-bar strat run.
    fresh = (base + timedelta(minutes=90)).isoformat()
    payload = _base_payload(
        ticker="MES1!", timestamp=fresh, market_condition="CHOPPY",
        trend_direction="DOWN", trend_strength="MODERATE",
        open=7559.0, high=7560.0, low=7556.0, close=7557.0, vwap=7575.0,
        orb_high=7561.5, orb_low=7541.75, orb_status="inside",
        current_bar_type="inside_bar", previous_bar_type="two_down",
        two_bars_back_type="inside_bar",
    )
    result = process_alert(payload, config=_cfg(), log_dir=log_dir)

    # The window read the continuous downtrend...
    assert result["window_direction"] == "DOWN"
    # ...so CHOPPY is no longer the blocker.
    gates = result.get("failed_gates") or []
    assert not any("MARKET_CONDITION" in str(g) for g in gates)


def test_no_prior_history_leaves_window_none(tmp_path):
    """First bar for an instrument has no window — must not crash, window None."""
    log_dir = str(tmp_path / "logs")
    # Default _base_payload is a valid MNQ bar.
    payload = _base_payload(
        timestamp="2026-06-05T01:00:00+00:00",
        market_condition="CHOPPY", trend_direction="DOWN",
    )
    result = process_alert(payload, config=_cfg(), log_dir=log_dir)
    assert result["window_direction"] is None


def test_gap_surfaced_in_result(tmp_path):
    log_dir = str(tmp_path / "logs")
    base = datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc)
    BarHistory(log_dir=log_dir).record(
        "MNQ", ts=base.isoformat(), open=19500, high=19505, low=19495,
        close=19500, volume=1500, timeframe="15")

    # Next ingested bar is 60 min later → 3 missing 15m bars.
    payload = _base_payload(
        timestamp=(base + timedelta(minutes=60)).isoformat(),
        market_condition="TRENDING", trend_direction="DOWN",
    )
    result = process_alert(payload, config=_cfg(), log_dir=log_dir)
    assert result["bar_gap"]["gapped"] is True
    assert result["bar_gap"]["missing_bars"] == 3
