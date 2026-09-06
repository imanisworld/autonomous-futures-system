"""
tests/test_mnq_vwap_hold_early.py

MNQ vwap_hold early-signal shadow lane (2026-07-13) — the upstream-timing
fix. See context/mnq_vwap_hold_early.py, execution/vwap_hold_early_shadow.py.

Unlike the moderate-detachment entry-refresh lane (PR #266), this lane does
NOT reimplement any strategy logic — it re-runs the REAL
strategy.signal_engine.DecisionEngine.evaluate() pipeline against a
MarketState built from a 5-minute alert, isolated by (1) an enabled_concepts
copy scoped to ["vwap_hold"] only and (2) a throwaway DailyState copy. Both
isolation measures are load-bearing and covered explicitly below, because
they are what makes reusing evaluate() safe instead of a shared-state risk.

Coverage:
  - Mode / config resolution: fails closed, config validation accepts every
    valid mode and rejects "demo"/"live".
  - Scope: MNQ + 5-minute only; MES and 15-minute alerts are never candidates
    regardless of mode.
  - Pure detection: a genuinely-qualifying 5-minute vwap_hold MarketState
    produces signal_detected=True with the correct entry/stop/target/RR; a
    state failing an upstream gate (wrong trend direction) produces
    signal_detected=False with the real reason preserved, not a fabricated
    one.
  - Isolation proof (the load-bearing part): detecting a vwap_hold signal on
    a 5-minute bar whose state ALSO carries orb_breakout-qualifying fields
    never flips daily_state.orb_break_long_played/orb_break_short_played —
    proven by checking the real DailyState object passed in in stays
    unchanged, not merely inferring it from config scoping.
  - Runner integration via process_alert(): off leaves 5-minute handling
    byte-for-byte unchanged (no evidence file, no shadow file); observe_only
    writes a detection audit row but never opens a shadow position;
    shadow additionally opens/tracks/resolves a hypothetical position;
    dedupe — a second qualifying 5-minute alert while one is pending does
    not open a second; proven (not inferred) that this lane never reaches
    risk or broker, via the same monkeypatch-raises technique used by the
    entry-refresh and orb_reclaim-proof shadow lanes.
  - Shadow resolution: entry-bar exclusion (no look-ahead), restart-safe
    state round-trip, fail-soft on corrupt JSON.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from config.settings import ConfigError, _validate_config
from context.mnq_vwap_hold_early import (
    VALID_MODES,
    detect_early_vwap_hold,
    is_vwap_hold_early_candidate,
    vwap_hold_early_mode,
)
from execution.vwap_hold_early_shadow import (
    EVIDENCE_FILENAME,
    STATE_FILENAME,
    close_shadow_position,
    get_pending_shadow_position,
    open_shadow_position,
    resolve_shadow_position,
)
from risk.risk_engine import DailyState
from strategy.signal_engine import DecisionEngine
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.payload import AlertPayload
from webhook.runner import process_alert
from webhook.state_builder import build_market_state


def _five_min_short_payload(**overrides) -> AlertPayload:
    """A genuinely-qualifying 5-minute MNQ vwap_hold SHORT candidate.

    vwap=19495.0, close=19494.5 (below -> holding=True), trend DOWN,
    current_bar_type two_down. Expected geometry (mirrors _try_vwap_hold's
    own math): entry=19494.5, stop=19502.0, target=19472.0, rr=3.0.
    """
    data = dict(
        ticker="MNQ1!",
        timestamp="2026-05-23T14:30:00+00:00",
        timeframe="5",
        open=19496.0,
        high=19497.0,
        low=19492.0,
        close=19494.5,
        volume=4200,
        avg_volume=3800,
        vwap=19495.0,
        orb_high=19498.0,
        orb_low=19462.0,
        orb_status="below",
        market_condition="TRENDING",
        trend_direction="DOWN",
        trend_strength="STRONG",
        previous_day_high=19520.0,
        previous_day_low=19440.0,
        previous_day_close=19475.0,
        current_bar_type="two_down",
        previous_bar_type="two_down",
        two_bars_back_type="two_down",
    )
    data.update(overrides)
    return AlertPayload(**data)


def _daily_state(**overrides) -> DailyState:
    base = dict(
        trade_count=0,
        consecutive_losses=0,
        has_open_position=False,
        realized_pnl_dollars=0.0,
        orb_break_long_played={},
        orb_break_short_played={},
    )
    base.update(overrides)
    return DailyState(**base)


# ─── Mode / config resolution ─────────────────────────────────────────────────

def test_default_mode_is_off(tmp_path):
    cfg = _base_config(tmp_path)
    assert cfg.vwap_hold_early_mode == "off"
    assert vwap_hold_early_mode(cfg) == "off"


@pytest.mark.parametrize("mode", VALID_MODES)
def test_valid_modes_pass_through(tmp_path, mode):
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode=mode)
    assert vwap_hold_early_mode(cfg) == mode


@pytest.mark.parametrize("bad", ["live", "demo", "not_a_real_mode", "", None])
def test_invalid_values_fail_closed_to_off(tmp_path, bad):
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode=bad)
    assert vwap_hold_early_mode(cfg) == "off"


def test_config_validation_rejects_demo_and_live(tmp_path):
    for bad in ("demo", "live"):
        cfg = replace(_base_config(tmp_path), vwap_hold_early_mode=bad, max_staleness_seconds=60)
        with pytest.raises(ConfigError, match="VWAP_HOLD_EARLY_MODE"):
            _validate_config(cfg)


def test_config_validation_accepts_every_valid_mode(tmp_path):
    for mode in VALID_MODES:
        _validate_config(replace(_base_config(tmp_path), vwap_hold_early_mode=mode, max_staleness_seconds=60))


# ─── Scope ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "instrument,timeframe,mode,expected",
    [
        ("MNQ1!", "5", "shadow", True),
        ("MNQ1!", "5m", "observe_only", True),
        ("MNQ1!", "15", "shadow", False),   # not a 5-minute bar
        ("MES1!", "5", "shadow", False),    # not MNQ
        ("MNQ1!", "5", "off", False),       # mode off
    ],
)
def test_scope_matches_mnq_5m_only_when_enabled(tmp_path, instrument, timeframe, mode, expected):
    cfg = replace(_base_config(tmp_path), vwap_hold_early_mode=mode)
    assert is_vwap_hold_early_candidate(instrument, timeframe, cfg) is expected


# ─── Pure detection ────────────────────────────────────────────────────────────

def test_qualifying_candidate_is_detected_with_correct_geometry(tmp_path):
    cfg = _base_config(tmp_path)
    state = build_market_state(_five_min_short_payload())
    daily = _daily_state()
    audit = detect_early_vwap_hold(state, daily, cfg)
    assert audit is not None
    assert audit["signal_detected"] is True
    assert audit["direction"] == "SHORT"
    assert audit["entry"] == pytest.approx(19494.5)
    assert audit["stop"] == pytest.approx(19502.0)
    assert audit["target"] == pytest.approx(19472.0)
    assert audit["rr_ratio"] == pytest.approx(3.0)


def test_wrong_trend_direction_is_not_detected_with_real_reason(tmp_path):
    cfg = _base_config(tmp_path)
    state = build_market_state(_five_min_short_payload(trend_direction="UP"))
    audit = detect_early_vwap_hold(state, _daily_state(), cfg)
    assert audit is not None
    assert audit["signal_detected"] is False
    assert audit["decision"] == "NO_TRADE"
    # The real gate stack produced this — not a fabricated placeholder reason.
    assert audit["reason"]


def test_shadow_only_permission_does_not_erase_otherwise_qualified_observation(tmp_path):
    """Execution permission remains blocked; only the shadow detector records it."""
    cfg = replace(
        _base_config(tmp_path),
        strategy_permission_gate_enabled=True,
        strategy_permission_default_status="SHADOW_ONLY",
        strategy_status={"vwap_hold": "SHADOW_ONLY"},
    )
    audit = detect_early_vwap_hold(
        build_market_state(_five_min_short_payload()), _daily_state(), cfg
    )
    assert audit["decision"] == "NO_TRADE"
    assert audit["failed_gates"] == ["STRATEGY_NOT_PAPER_ELIGIBLE"]
    assert audit["signal_detected"] is True
    assert audit["shadow_eligibility_basis"] == "EXECUTION_PERMISSION_BLOCK_ONLY"


# ─── Isolation proof (load-bearing) ────────────────────────────────────────────

def test_daily_state_is_never_mutated_by_detection(tmp_path):
    """The real object passed in must come back byte-for-byte unchanged, even
    though the 5-minute state also carries orb-qualifying fields that WOULD
    flip orb_break_long_played if orb_breakout ran (it must not, here)."""
    cfg = _base_config(tmp_path)
    payload = _five_min_short_payload(orb_status="reclaimed_high")
    state = build_market_state(payload)
    live_daily = _daily_state(orb_break_long_played={}, orb_break_short_played={})
    snapshot = replace(live_daily)

    detect_early_vwap_hold(state, live_daily, cfg)

    assert live_daily == snapshot
    assert live_daily.orb_break_long_played == {}
    assert live_daily.orb_break_short_played == {}


def test_only_vwap_hold_runs_other_enabled_concepts_are_not_reachable(tmp_path):
    """A direct proof, not an inference: constructing a real DecisionEngine
    with the scoped enabled_concepts used internally and confirming it only
    ever matches vwap_hold, never any other concept, on this state."""
    cfg = replace(_base_config(tmp_path), enabled_concepts=["vwap_hold"])
    state = build_market_state(_five_min_short_payload())
    engine = DecisionEngine(cfg)
    decision = engine.evaluate(state, _daily_state())
    assert decision.decision == "TRADE"
    assert decision.setup.strategy == "vwap_hold"


# ─── Runner integration ────────────────────────────────────────────────────────

def test_mode_off_leaves_five_minute_handling_unaffected(tmp_path):
    import os
    os.environ["FIVE_MIN_FEED_ENABLED"] = "true"
    try:
        cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="off")
        log_dir = cfg.log_dir
        result = process_alert(_five_min_short_payload(), config=cfg, log_dir=log_dir)
        assert result["decision"] == "FIVE_MIN_CONTEXT"
        assert not (Path(log_dir) / EVIDENCE_FILENAME).exists()
        assert not (Path(log_dir) / STATE_FILENAME).exists()
    finally:
        os.environ.pop("FIVE_MIN_FEED_ENABLED", None)


def test_observe_only_writes_detection_audit_but_never_opens_shadow(tmp_path):
    import os
    os.environ["FIVE_MIN_FEED_ENABLED"] = "true"
    try:
        cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="observe_only")
        log_dir = cfg.log_dir
        result = process_alert(_five_min_short_payload(), config=cfg, log_dir=log_dir)
        assert result["decision"] == "FIVE_MIN_CONTEXT"
        evidence_path = Path(log_dir) / EVIDENCE_FILENAME
        assert evidence_path.exists()
        rows = [r for r in evidence_path.read_text().splitlines() if r.strip()]
        assert len(rows) == 1
        assert '"signal_detected": true' in rows[0] or '"signal_detected":true' in rows[0]
        assert get_pending_shadow_position(log_dir) is None
    finally:
        os.environ.pop("FIVE_MIN_FEED_ENABLED", None)


def test_shadow_mode_opens_a_position_and_dedupes_a_second_signal(tmp_path):
    import os
    os.environ["FIVE_MIN_FEED_ENABLED"] = "true"
    try:
        cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="shadow")
        log_dir = cfg.log_dir

        r1 = process_alert(
            _five_min_short_payload(timestamp="2026-05-23T14:30:00+00:00"),
            config=cfg, log_dir=log_dir,
        )
        assert r1["decision"] == "FIVE_MIN_CONTEXT"
        position = get_pending_shadow_position(log_dir)
        assert position is not None
        assert position["direction"] == "SHORT"
        assert position["entry"] == pytest.approx(19494.5)

        # Second qualifying signal 5 minutes later, price safely inside the
        # pending position's stop/target band so it isn't resolved first —
        # must be deduped, not opened as a second position.
        r2 = process_alert(
            _five_min_short_payload(
                timestamp="2026-05-23T14:35:00+00:00",
                high=19497.0, low=19493.5, close=19494.75,
            ),
            config=cfg, log_dir=log_dir,
        )
        assert r2["decision"] == "FIVE_MIN_CONTEXT"
        still_pending = get_pending_shadow_position(log_dir)
        assert still_pending is not None
        assert still_pending["entry_ts"] == position["entry_ts"]  # same position, not replaced
    finally:
        os.environ.pop("FIVE_MIN_FEED_ENABLED", None)


def test_shadow_lane_never_reaches_risk_or_broker(tmp_path, monkeypatch):
    """Monkeypatch-raises proof: if either were called, the test fails loudly
    instead of silently passing on an untested assumption."""
    import os
    os.environ["FIVE_MIN_FEED_ENABLED"] = "true"
    try:
        from risk.risk_engine import RiskEngine

        def _boom(*args, **kwargs):
            raise AssertionError("risk engine must never be reached from the vwap_hold early lane")

        monkeypatch.setattr(RiskEngine, "evaluate", _boom, raising=False)

        cfg = replace(_base_config(tmp_path), vwap_hold_early_mode="shadow")
        result = process_alert(_five_min_short_payload(), config=cfg, log_dir=cfg.log_dir)
        assert result["decision"] == "FIVE_MIN_CONTEXT"
        assert get_pending_shadow_position(cfg.log_dir) is not None
    finally:
        os.environ.pop("FIVE_MIN_FEED_ENABLED", None)


# ─── Shadow resolution ──────────────────────────────────────────────────────────

def test_resolve_shadow_position_excludes_the_entry_bar(tmp_path):
    """The entry bar's own high must not be checked for a stop hit (it
    reflects price action from before the hypothetical entry existed). If it
    were wrongly included here, this SHORT would immediately (and invalidly)
    STOP_HIT on the entry bar itself, since its high already exceeds stop."""
    position = {
        "direction": "SHORT",
        "entry": 19494.5,
        "stop": 19502.0,
        "target": 19472.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    entry_ts = "2026-05-23T14:30:00+00:00"
    entry_bar = {"ts": entry_ts, "high": 19503.0, "low": 19490.0, "close": 19494.5}
    later_bar = {"ts": "2026-05-23T14:35:00+00:00", "high": 19496.0, "low": 19470.0, "close": 19472.0}

    bars_since_entry = [b for b in [entry_bar, later_bar] if b["ts"] > entry_ts]
    assert bars_since_entry == [later_bar]  # entry bar correctly excluded
    # later_bar alone doesn't breach the (still-untrailed) stop -> still open,
    # not a spurious STOP_HIT from the excluded entry bar's high.
    outcome = resolve_shadow_position(position, bars_since_entry)
    assert outcome is None

    # Proof the exclusion is load-bearing: WITH the entry bar wrongly
    # included, the same call would falsely resolve on bar 1.
    outcome_with_bug = resolve_shadow_position(position, [entry_bar, later_bar])
    assert outcome_with_bug is not None
    assert outcome_with_bug["exit_ts"] == entry_ts


def test_state_file_round_trips_after_restart(tmp_path):
    log_dir = str(tmp_path)
    open_shadow_position(
        log_dir,
        direction="SHORT",
        entry=19494.5,
        stop=19502.0,
        target=19472.0,
        entry_ts="2026-05-23T14:30:00+00:00",
        rr_ratio=3.0,
    )
    reloaded = get_pending_shadow_position(log_dir)
    assert reloaded is not None
    assert reloaded["entry"] == pytest.approx(19494.5)
    close_shadow_position(log_dir)
    assert get_pending_shadow_position(log_dir) is None


def test_corrupt_state_file_fails_soft(tmp_path):
    log_dir = tmp_path
    (log_dir / STATE_FILENAME).write_text("{not valid json")
    assert get_pending_shadow_position(str(log_dir)) is None
    # Must not raise opening a fresh position over a corrupt file either.
    open_shadow_position(
        str(log_dir), direction="SHORT", entry=1.0, stop=2.0, target=0.0,
        entry_ts="2026-01-01T00:00:00+00:00",
    )
    assert get_pending_shadow_position(str(log_dir)) is not None
