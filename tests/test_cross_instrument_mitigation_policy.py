"""Policy regressions for conservative cross-instrument observation mitigations."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from execution import cross_instrument_observation as cio

EPOCH = "mitigation-epoch"

_STRUCTURAL_FAMILIES = {
    "strat_212",
    "strat_122",
    "strat_22_continuation_observed",
    "strat_22_reversal_observed",
    "strat_312_observed",
    "strat_322_reversal_observed",
    "impulse_first_pullback_observed",
    "trend_consolidation_break_observed",
    "transition_failed_breakdown_reclaim",
}


def test_mbt_structural_families_are_signal_only_while_outcome_horizon_unproven(monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    pops = cio.configured_populations()
    mbt = {p["strategy"]: p for p in pops if p["instrument"] == "MBT"}
    for strategy in _STRUCTURAL_FAMILIES:
        assert mbt[strategy]["collection_mode"] == cio.SIGNAL_METRICS
    # The same families remain structural-outcome populations on a proven
    # non-24/7 observation root; the downgrade is MBT-specific, not global.
    m2k = {p["strategy"]: p for p in pops if p["instrument"] == "M2K"}
    for strategy in _STRUCTURAL_FAMILIES:
        assert m2k[strategy]["collection_mode"] == cio.STRUCTURAL_OUTCOME


def test_mbt_structural_candidate_never_opens_pending_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    state = SimpleNamespace(
        instrument="MBT",
        timestamp=datetime(2026, 9, 15, 14, 30, tzinfo=timezone.utc),
        session="new_york",
        market_condition="TRENDING",
        strat=None,
        ohlc=SimpleNamespace(open=60000.0, high=60100.0, low=59900.0, close=60050.0, timeframe="15"),
    )
    result = cio.observe_bar(
        tmp_path,
        state,
        [{
            "strategy": "strat_22_continuation_observed",
            "direction": "LONG",
            "entry": 60100.0,
            "stop": 59900.0,
            "target": 60500.0,
        }],
        timeframe="15",
        source="test",
        include_strat_212_122=False,
    )
    assert result["signal"] == 1
    rows = cio.read_evidence(tmp_path)
    assert len(rows) == 1
    assert rows[0]["record_type"] == "SIGNAL"
    assert rows[0]["bracket_authoritative"] is False
    state_file = json.loads((tmp_path / cio.STATE_FILENAME).read_text())
    assert state_file["pending"] == {}
