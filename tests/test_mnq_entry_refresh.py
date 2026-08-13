"""
tests/test_mnq_entry_refresh.py

Entry-refresh Phase 1 shadow lane (2026-07-13, PR #265's follow-up build).
Scoped narrowly to MNQ + orb_reclaim only by default — see
context/mnq_entry_refresh.py, execution/entry_refresh_shadow.py.

Coverage:
  - Pure decision function: cap enforcement, adverse-setup-invalid rejection,
    every outcome in the required enum reachable via direct (possibly
    adversarial/degenerate) inputs, translation preserves direction/R/RR.
  - Mode + config resolution: fails closed on garbage/"live"/"demo"; config
    load-time validation rejects invalid mode and non-positive cap.
  - IMPORTANT, disclosed finding: a real orb_reclaim candidate must clear
    min_rr_ratio (>=2.0 in this repo's default config) to exist at all, and
    `ENTRY_DETACHED_FROM_PRICE` only fires once price is fully outside
    [stop, target] — so a REAL trigger's detachment (measured in R of the
    original risk) is lower-bounded by the setup's own R:R (>=2.0), not by
    some small drift past entry. A max_detachment_r=1.0 cap (this module's
    documented default) will therefore reject essentially all real-shaped
    RR>=2 incidents as REJECTED_TOO_LATE out of the box — this is expected,
    not a bug, and is exactly the kind of thing Phase 1's shadow lane exists
    to measure before any cap is tuned. Tests below prove REJECTED_TOO_LATE
    under the real default AND prove REFRESHED is reachable once the cap is
    widened enough to admit a realistic RR-shaped setup, so both branches are
    exercised through the full runner path, not just the pure function.
  - Runner integration: off leaves existing behavior untouched; observe_only
    attaches a pure audit dict and NEVER opens a shadow position even when
    REFRESHED; shadow additionally opens/tracks/resolves a hypothetical
    position; scoped ONLY to configured instrument+strategy (a different
    strategy/instrument is untouched); proven — not inferred — that risk and
    broker are never reached from this lane (monkeypatch-raises technique).
  - Shadow resolution: parity with PaperBroker._resolve_runner's own math on
    an identical bar sequence (same shared compute_trailed_stop call), MFE/MAE
    tracking, TIMEOUT after the configured age, restart-safe state file
    round-trip, fail-soft on corrupt JSON, one-pending-per-(instrument,
    strategy) dedupe.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from config.settings import ConfigError, _validate_config
from context.mnq_entry_refresh import (
    VALID_MODES,
    entry_refresh_instruments,
    entry_refresh_max_detachment_r,
    entry_refresh_mode,
    entry_refresh_strategies,
    is_entry_refresh_candidate,
    refresh_detached_entry,
)
from execution.broker_interface import BracketOrder
from execution.entry_refresh_shadow import (
    EVIDENCE_FILENAME,
    STATE_FILENAME,
    append_entry_refresh_shadow_evidence,
    close_shadow_position,
    get_pending_shadow_position,
    open_shadow_position,
    resolve_shadow_position,
)
from execution.paper_broker import NextBarOHLC, PaperBroker
from strategy.signal_engine import DecisionEngine, SetupDetail
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


# ─── Pure module: mode / scope / config resolution ────────────────────────────

def test_default_mode_is_off(tmp_path):
    cfg = _base_config(tmp_path)
    assert cfg.entry_refresh_mode == "off"
    assert entry_refresh_mode(cfg) == "off"


@pytest.mark.parametrize("mode", VALID_MODES)
def test_valid_modes_pass_through(tmp_path, mode):
    cfg = replace(_base_config(tmp_path), entry_refresh_mode=mode)
    assert entry_refresh_mode(cfg) == mode


@pytest.mark.parametrize("bad", ["live", "demo", "not_a_real_mode", "", None])
def test_invalid_or_not_yet_supported_values_fail_closed_to_off(tmp_path, bad):
    cfg = replace(_base_config(tmp_path), entry_refresh_mode=bad)
    assert entry_refresh_mode(cfg) == "off"


def test_config_validation_rejects_demo_and_live_at_load_time(tmp_path):
    for bad in ("demo", "live"):
        cfg = replace(_base_config(tmp_path), entry_refresh_mode=bad, max_staleness_seconds=60)
        with pytest.raises(ConfigError, match="ENTRY_REFRESH_MODE"):
            _validate_config(cfg)


def test_config_validation_accepts_every_valid_mode(tmp_path):
    for mode in VALID_MODES:
        _validate_config(replace(_base_config(tmp_path), entry_refresh_mode=mode, max_staleness_seconds=60))


def test_config_validation_rejects_non_positive_cap(tmp_path):
    cfg = replace(_base_config(tmp_path), entry_refresh_max_detachment_r=0.0, max_staleness_seconds=60)
    with pytest.raises(ConfigError, match="ENTRY_REFRESH_MAX_DETACHMENT_R"):
        _validate_config(cfg)


def test_default_scope_is_mnq_orb_reclaim_only(tmp_path):
    cfg = _base_config(tmp_path)
    assert entry_refresh_instruments(cfg) == frozenset({"MNQ"})
    assert entry_refresh_strategies(cfg) == frozenset({"orb_reclaim"})
    assert entry_refresh_max_detachment_r(cfg) == 1.0


@pytest.mark.parametrize(
    "instrument,strategy,expected",
    [
        ("MNQ", "orb_reclaim", True),
        ("MNQ1!", "orb_reclaim", True),
        ("MNQ", "vwap_hold", False),  # not in default ENTRY_REFRESH_STRATEGIES
        ("MNQ", "orb_breakout", False),
        ("MES", "orb_reclaim", False),  # not in default ENTRY_REFRESH_INSTRUMENTS
        (None, "orb_reclaim", False),
        ("MNQ", None, False),
    ],
)
def test_is_entry_refresh_candidate_default_scope(tmp_path, instrument, strategy, expected):
    cfg = _base_config(tmp_path)
    assert is_entry_refresh_candidate(instrument, strategy, cfg) is expected


def test_scope_is_configurable_beyond_the_default(tmp_path):
    cfg = replace(
        _base_config(tmp_path),
        entry_refresh_strategies=("orb_reclaim", "vwap_hold"),
        entry_refresh_instruments=("MNQ", "MES"),
    )
    assert is_entry_refresh_candidate("MES", "vwap_hold", cfg) is True
    assert is_entry_refresh_candidate("MES", "orb_breakout", cfg) is False


# ─── Pure decision function: refresh_detached_entry ───────────────────────────

def test_long_refreshed_within_cap_preserves_r_and_rr():
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,  # risk=10, reward=30, RR=3
        live_price=105.0, tick=0.25, max_detachment_r=1.0,       # gap=5 -> 0.5R
    )
    assert d.outcome == "REFRESHED"
    assert d.detachment_r == pytest.approx(0.5)
    assert d.refreshed_entry == 105.0
    assert d.refreshed_stop == 95.0
    assert d.refreshed_target == 135.0
    assert d.refreshed_rr == pytest.approx(3.0)  # RR invariant under translation
    assert (d.refreshed_target - d.refreshed_entry) == pytest.approx(target := 130.0 - 100.0)


def test_short_refreshed_within_cap_mirrors_long():
    d = refresh_detached_entry(
        direction="SHORT", entry=100.0, stop=110.0, target=70.0,  # risk=10, reward=30
        live_price=95.0, tick=0.25, max_detachment_r=1.0,         # gap=5 -> 0.5R
    )
    assert d.outcome == "REFRESHED"
    assert d.refreshed_entry == 95.0
    assert d.refreshed_stop == 105.0
    assert d.refreshed_target == 65.0
    assert d.refreshed_rr == pytest.approx(3.0)


def test_detachment_beyond_cap_is_rejected_too_late():
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,
        live_price=115.0, tick=0.25, max_detachment_r=1.0,  # gap=15 -> 1.5R > 1.0R cap
    )
    assert d.outcome == "REJECTED_TOO_LATE"
    assert d.detachment_r == pytest.approx(1.5)
    assert d.refreshed_entry is None


def test_price_already_crossed_original_stop_is_setup_invalid_not_too_late():
    """Adverse invalidation is a different failure mode than lateness — the
    thesis is broken, translating into it would be unsound at any cap."""
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,
        live_price=85.0, tick=0.25, max_detachment_r=999.0,  # huge cap, still rejected
    )
    assert d.outcome == "REJECTED_SETUP_INVALID"
    assert "stop" in d.reason.lower()


def test_price_at_entry_or_behind_is_setup_invalid():
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,
        live_price=99.0, tick=0.25, max_detachment_r=1.0,
    )
    assert d.outcome == "REJECTED_SETUP_INVALID"


def test_degenerate_zero_risk_is_setup_invalid():
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=100.0, target=130.0,
        live_price=110.0, tick=0.25, max_detachment_r=1.0,
    )
    assert d.outcome == "REJECTED_SETUP_INVALID"


def test_bad_direction_is_setup_invalid():
    d = refresh_detached_entry(
        direction="SIDEWAYS", entry=100.0, stop=90.0, target=130.0,
        live_price=110.0, tick=0.25, max_detachment_r=1.0,
    )
    assert d.outcome == "REJECTED_SETUP_INVALID"


def test_min_rr_enforced_even_though_unreachable_under_pure_translation():
    """Translation preserves RR exactly, so this can't fire from realistic
    inputs — proven directly here as a defense-in-depth contract test."""
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,
        live_price=105.0, tick=0.25, max_detachment_r=1.0, min_rr=10.0,  # impossible min
    )
    assert d.outcome == "REJECTED_BAD_RR"


def test_max_stop_ticks_enforced():
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,  # risk=10pts=40 ticks
        live_price=105.0, tick=0.25, max_detachment_r=1.0, max_stop_ticks=10.0,
    )
    assert d.outcome == "REJECTED_STOP_TOO_WIDE"


def test_to_audit_dict_is_json_shaped():
    d = refresh_detached_entry(
        direction="LONG", entry=100.0, stop=90.0, target=130.0,
        live_price=105.0, tick=0.25, max_detachment_r=1.0,
    )
    audit = d.to_audit_dict()
    assert audit["outcome"] == "REFRESHED"
    assert set(audit) >= {
        "outcome", "direction", "original_entry", "original_stop", "original_target",
        "live_price", "detachment_ticks", "detachment_r", "refreshed_entry",
        "refreshed_stop", "refreshed_target", "refreshed_rr", "reason",
    }


# ─── Runner integration ────────────────────────────────────────────────────────

def _detached_orb_reclaim(entry, stop, target, direction="LONG"):
    """A realistic RR-shaped orb_reclaim candidate (RR=2.5, clears the test
    config's min_rr_ratio=2.0) whose entry/stop/target are set FAR from
    _base_payload's close=19505.25, so the real
    `_entry_bracket_straddles_price` gate genuinely rejects it — not a fixture
    shortcut, the actual production gate fires on this input."""
    return SetupDetail(
        direction=direction, entry=entry, stop=stop, target=target,
        rr_ratio=abs(target - entry) / abs(entry - stop), strategy="orb_reclaim",
    )


def _patch_detached_candidate(monkeypatch, entry, stop, target, direction="LONG"):
    setup = _detached_orb_reclaim(entry, stop, target, direction)
    monkeypatch.setattr(
        DecisionEngine, "_find_setup_candidates", lambda self, *a, **kw: [setup]
    )


def test_off_mode_leaves_existing_behavior_untouched_and_no_audit(tmp_path, monkeypatch):
    import json

    today = date(2026, 5, 23)
    cfg = _base_config(tmp_path)  # entry_refresh_mode defaults to "off"
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert "ENTRY_DETACHED_FROM_PRICE" in result["failed_gates"]

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    row = next(r for r in rows if r.get("decision") == "NO_TRADE")
    assert "entry_refresh_audit" not in row
    assert not (Path(cfg.log_dir) / STATE_FILENAME).exists()


def test_observe_only_attaches_audit_but_never_opens_a_shadow_position(tmp_path, monkeypatch):
    """Even when the geometry WOULD be REFRESHED, observe_only must not open
    a shadow position — mirrors the mnq_orb_reclaim_proof observe_only
    contract (pure audit, zero side effects beyond the journal row)."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path),
        entry_refresh_mode="observe_only",
        entry_refresh_max_detachment_r=3.0,  # wide enough to admit this RR=2.5 setup
    )
    # gap from entry(19495.25) to close(19505.25) = 10pts = 40 ticks; risk=10pts=40
    # ticks -> detachment_r = 1.0, comfortably inside the 3.0R test cap.
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    row = next(r for r in rows if r.get("decision") == "NO_TRADE")
    audit = row["entry_refresh_audit"]
    assert audit["mode"] == "observe_only"
    assert audit["outcome"] == "REFRESHED"
    assert not (Path(cfg.log_dir) / STATE_FILENAME).exists()


def test_shadow_mode_default_cap_rejects_a_realistic_rr_shaped_incident_as_too_late(tmp_path, monkeypatch):
    """The disclosed finding: with the documented default cap (1.0R) and a
    realistic RR=2.5 candidate, ENTRY_DETACHED_FROM_PRICE can only fire once
    price is already past target — i.e. detachment >= RR = 2.5R, which is
    always > the 1.0R cap. REJECTED_TOO_LATE, no shadow position opened."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(_base_config(tmp_path), entry_refresh_mode="shadow")  # default cap 1.0R
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [json.loads(line) for line in journal_path.read_text().splitlines()]
    row = next(r for r in rows if r.get("decision") == "NO_TRADE")
    assert row["entry_refresh_audit"]["outcome"] == "REJECTED_TOO_LATE"
    assert get_pending_shadow_position(cfg.log_dir, "MNQ", "orb_reclaim") is None


def test_shadow_mode_opens_a_position_when_refreshed_and_a_second_detached_event_is_deduped(
    tmp_path, monkeypatch
):
    import json

    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path), entry_refresh_mode="shadow", entry_refresh_max_detachment_r=3.0,
    )
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    pos = get_pending_shadow_position(cfg.log_dir, "MNQ", "orb_reclaim")
    assert pos is not None
    assert pos["direction"] == "LONG"
    assert pos["entry"] == 19505.25  # == the bar's close (live price)
    assert pos["stop"] == 19495.25  # 19469.25 + offset(26.0)
    assert pos["target"] == 19530.25  # 19504.25 + offset(26.0)

    # A SECOND detached event on a later bar while one is still pending must
    # not open a duplicate — dedupe proven, not just documented. close stays
    # >= the candidate's target(19504.25) so ENTRY_DETACHED_FROM_PRICE fires
    # again; high/low are kept comfortably inside the pending shadow's
    # [stop 19495.25, target 19530.25] band so this bar's own
    # shadow-resolution pass (hook A) does NOT legitimately resolve the
    # position — isolating the dedupe behavior from ordinary resolution.
    result2 = process_alert(
        _base_payload(
            timestamp="2026-05-23T15:15:00+00:00",
            close=19506.0, high=19507.0, low=19505.0,
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result2["decision"] == "NO_TRADE"
    assert "ENTRY_DETACHED_FROM_PRICE" in result2["failed_gates"]
    pos_after = get_pending_shadow_position(cfg.log_dir, "MNQ", "orb_reclaim")
    assert pos_after == pos  # unchanged — still the FIRST position, not overwritten


def test_occupied_shadow_slot_still_writes_the_paired_modified_arm(tmp_path, monkeypatch):
    """Every campaign control arm must keep its modified counterpart.

    The control row is written unconditionally, so if the modified row were
    dropped whenever the shadow slot happened to be occupied, A/B coverage would
    be conditioned on slot availability — a selection effect in the evidence.
    """
    import json

    monkeypatch.setenv("FORWARD_EVIDENCE_CAMPAIGN", "forward_ab_2026_08_v1")
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path), entry_refresh_mode="shadow", entry_refresh_max_detachment_r=3.0,
    )
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert get_pending_shadow_position(cfg.log_dir, "MNQ", "orb_reclaim") is not None

    # Second detached event while the first shadow position is still pending:
    # open_shadow_position() dedupes and returns False.
    process_alert(
        _base_payload(
            timestamp="2026-05-23T15:15:00+00:00",
            close=19506.0, high=19507.0, low=19505.0,
        ),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )

    rows = [
        json.loads(line)
        for line in (Path(cfg.log_dir) / "forward_ab_2026_08_v1.jsonl").read_text().splitlines()
    ]
    orb = [r for r in rows if r["strategy"] == "orb_reclaim" and r["record_type"] == "CANDIDATE"]

    by_event: dict[str, set[str]] = {}
    for row in orb:
        by_event.setdefault(row["event_id"], set()).add(row["variant"])
    assert len(by_event) == 2, "two distinct detached events"
    for event_id, variants in by_event.items():
        assert variants == {"control", "modified"}, f"unpaired arm on {event_id}"

    occupied = [r for r in orb if r["reject_reason"] == "SHADOW_SLOT_OCCUPIED"]
    assert len(occupied) == 1
    assert occupied[0]["variant"] == "modified"
    assert occupied[0]["fillable_state"] == "REJECTED"
    assert occupied[0]["terminal_state"] == "REJECTED"
    assert occupied[0]["hypothetical_fill_price"] is None
    assert "SHADOW_SLOT_OCCUPIED" in occupied[0]["failed_gates"]
    # The blocked arm must not be counted as an economic outcome.
    assert occupied[0]["net_pnl_dollars"] is None

    # The first event's modified arm is untouched by the fix.
    filled = [r for r in orb if r["variant"] == "modified" and r["fillable_state"] == "FILLED"]
    assert len(filled) == 1
    assert filled[0]["reject_reason"] is None


def test_failed_shadow_state_write_is_not_reported_as_an_occupied_slot(tmp_path, monkeypatch):
    """A persistence failure must never be filed as clean campaign evidence.

    open_shadow_position() returns False for two different causes — an occupied
    slot and an OSError writing the shadow state. Labelling both
    SHADOW_SLOT_OCCUPIED would make an infrastructure fault indistinguishable
    from a legitimately-blocked arm when the campaign is analysed.

    No mocking: planting a directory where the atomic temp file must be written
    makes the real write raise IsADirectoryError (an OSError), which the real
    fail-soft handler swallows into False.
    """
    import json

    monkeypatch.setenv("FORWARD_EVIDENCE_CAMPAIGN", "forward_ab_2026_08_v1")
    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path), entry_refresh_mode="shadow", entry_refresh_max_detachment_r=3.0,
    )
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    blocked_tmp = (Path(cfg.log_dir) / STATE_FILENAME).with_suffix(".tmp")
    blocked_tmp.mkdir(parents=True)

    process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )

    # The write genuinely failed: no state file, so no pending position exists.
    assert not (Path(cfg.log_dir) / STATE_FILENAME).exists()
    assert get_pending_shadow_position(cfg.log_dir, "MNQ", "orb_reclaim") is None

    rows = [
        json.loads(line)
        for line in (Path(cfg.log_dir) / "forward_ab_2026_08_v1.jsonl").read_text().splitlines()
    ]
    orb = [r for r in rows if r["strategy"] == "orb_reclaim" and r["record_type"] == "CANDIDATE"]
    modified = [r for r in orb if r["variant"] == "modified"]

    # The arm is still written, so the control does not end up orphaned.
    assert len(modified) == 1
    assert {r["variant"] for r in orb} == {"control", "modified"}

    row = modified[0]
    assert row["reject_reason"] != "SHADOW_SLOT_OCCUPIED"
    assert row["reject_reason"] == "SHADOW_STATE_OPEN_FAILED"
    assert row["exit_reason"] == "SHADOW_STATE_OPEN_FAILED"
    assert "SHADOW_STATE_OPEN_FAILED" in row["failed_gates"]
    assert row["fillable_state"] == "REJECTED"
    assert row["terminal_state"] == "REJECTED"
    assert row["hypothetical_fill_price"] is None
    assert row["net_pnl_dollars"] is None
    assert row["gross_pnl_dollars"] is None


def test_shadow_lane_never_reaches_risk_or_broker(tmp_path, monkeypatch):
    """Proves — not infers — the shadow lane cannot trigger a real
    TRADE_INTENT/risk/broker path, same monkeypatch-raises technique as
    PR #259's duplicate-campaign test."""
    import webhook.runner as runner_module

    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path), entry_refresh_mode="shadow", entry_refresh_max_detachment_r=3.0,
    )
    _patch_detached_candidate(monkeypatch, entry=19479.25, stop=19469.25, target=19504.25)

    class _RiskMustNotBeConstructed:
        def __init__(self, *a, **kw):
            raise AssertionError("RiskEngine must not be constructed by the entry-refresh shadow lane")

    def _broker_must_not_execute(*a, **kw):
        raise AssertionError("broker.execute_bracket must not be called by the entry-refresh shadow lane")

    monkeypatch.setattr(runner_module, "RiskEngine", _RiskMustNotBeConstructed)
    monkeypatch.setattr(PaperBroker, "execute_bracket", _broker_must_not_execute)

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert result["decision"] == "NO_TRADE"
    assert get_pending_shadow_position(cfg.log_dir, "MNQ", "orb_reclaim") is not None


def test_different_strategy_on_mnq_is_out_of_default_scope(tmp_path, monkeypatch):
    """A different MNQ strategy candidate under the same active shadow mode
    gets zero audit/shadow — confirms the gate is keyed on the configured
    strategy set, not just instrument."""
    import json

    today = date(2026, 5, 23)
    cfg = replace(
        _base_config(tmp_path), entry_refresh_mode="shadow", entry_refresh_max_detachment_r=3.0,
    )
    setup = SetupDetail(
        direction="LONG", entry=19479.25, stop=19469.25, target=19504.25,
        rr_ratio=4.0, strategy="vwap_hold",  # not in default ENTRY_REFRESH_STRATEGIES
    )
    monkeypatch.setattr(DecisionEngine, "_find_setup_candidates", lambda self, *a, **kw: [setup])

    result = process_alert(
        _base_payload(timestamp="2026-05-23T15:00:00+00:00"),
        config=cfg, log_dir=cfg.log_dir, for_date=today,
    )
    assert "ENTRY_DETACHED_FROM_PRICE" in result["failed_gates"]

    journal_path = next(Path(cfg.log_dir).glob("journal_*.jsonl"))
    rows = [__import__("json").loads(line) for line in journal_path.read_text().splitlines()]
    row = next(r for r in rows if r.get("decision") == "NO_TRADE")
    assert "entry_refresh_audit" not in row
    assert get_pending_shadow_position(cfg.log_dir, "MNQ", "vwap_hold") is None


# ─── Shadow execution: state persistence ───────────────────────────────────────

def test_open_get_close_round_trip(tmp_path):
    log_dir = str(tmp_path)
    assert get_pending_shadow_position(log_dir, "MNQ", "orb_reclaim") is None
    open_shadow_position(
        log_dir, instrument="MNQ", strategy="orb_reclaim", direction="LONG",
        entry=100.0, stop=95.0, target=115.0, entry_ts="2026-05-23T15:00:00+00:00",
    )
    pos = get_pending_shadow_position(log_dir, "MNQ", "orb_reclaim")
    assert pos is not None and pos["entry"] == 100.0
    close_shadow_position(log_dir, "MNQ", "orb_reclaim")
    assert get_pending_shadow_position(log_dir, "MNQ", "orb_reclaim") is None


def test_open_is_a_noop_when_one_is_already_pending(tmp_path):
    log_dir = str(tmp_path)
    open_shadow_position(
        log_dir, instrument="MNQ", strategy="orb_reclaim", direction="LONG",
        entry=100.0, stop=95.0, target=115.0, entry_ts="t1",
    )
    open_shadow_position(
        log_dir, instrument="MNQ", strategy="orb_reclaim", direction="SHORT",
        entry=200.0, stop=205.0, target=185.0, entry_ts="t2",
    )
    pos = get_pending_shadow_position(log_dir, "MNQ", "orb_reclaim")
    assert pos["entry"] == 100.0  # first one wins, second was a no-op


def test_state_file_survives_a_fresh_read_restart_safe(tmp_path):
    log_dir = str(tmp_path)
    open_shadow_position(
        log_dir, instrument="MNQ", strategy="orb_reclaim", direction="LONG",
        entry=100.0, stop=95.0, target=115.0, entry_ts="t1",
    )
    # Simulate a restart: nothing but the file on disk survives.
    pos = get_pending_shadow_position(str(tmp_path), "MNQ", "orb_reclaim")
    assert pos is not None and pos["direction"] == "LONG"


def test_corrupt_state_file_is_fail_soft(tmp_path):
    (tmp_path / STATE_FILENAME).write_text("{not json")
    assert get_pending_shadow_position(str(tmp_path), "MNQ", "orb_reclaim") is None
    # open must still succeed despite the corrupt prior file
    open_shadow_position(
        str(tmp_path), instrument="MNQ", strategy="orb_reclaim", direction="LONG",
        entry=100.0, stop=95.0, target=115.0, entry_ts="t1",
    )
    assert get_pending_shadow_position(str(tmp_path), "MNQ", "orb_reclaim") is not None


def test_independent_instruments_and_strategies_do_not_collide(tmp_path):
    log_dir = str(tmp_path)
    open_shadow_position(
        log_dir, instrument="MNQ", strategy="orb_reclaim", direction="LONG",
        entry=100.0, stop=95.0, target=115.0, entry_ts="t1",
    )
    open_shadow_position(
        log_dir, instrument="MNQ", strategy="vwap_hold", direction="SHORT",
        entry=200.0, stop=205.0, target=185.0, entry_ts="t2",
    )
    assert get_pending_shadow_position(log_dir, "MNQ", "orb_reclaim")["entry"] == 100.0
    assert get_pending_shadow_position(log_dir, "MNQ", "vwap_hold")["entry"] == 200.0


# ─── Shadow execution: resolution + parity ─────────────────────────────────────

def _bars(*rows):
    """rows: (ts, high, low[, close])."""
    out = []
    for r in rows:
        ts, high, low = r[0], r[1], r[2]
        d = {"ts": ts, "high": high, "low": low}
        if len(r) > 3:
            d["close"] = r[3]
        out.append(d)
    return out


def test_resolve_shadow_position_matches_paper_broker_runner_mode_on_identical_bars():
    """Parity is guaranteed by construction (both call compute_trailed_stop),
    but proven here end-to-end against PaperBroker's own runner resolution on
    the exact same bar sequence."""
    position = {
        "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 130.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    bars = _bars(
        ("b1", 108.0, 99.0),   # +0.8R favorable, not yet armed
        ("b2", 112.0, 107.0),  # +1.2R favorable -> armed, trail = 112 - 5 = 107
        ("b3", 106.0, 104.0),  # low 104 <= trailed stop 107 -> stop hit
    )
    shadow_result = resolve_shadow_position(position, bars, activation_r=1.0, trail_r=0.5)
    assert shadow_result is not None
    assert shadow_result["result"] == "WIN"
    assert shadow_result["runner_activated"] is True

    broker = PaperBroker(starting_balance=1500.0, slippage_ticks=0.0, runner_mode=True)
    broker.execute_bracket(BracketOrder(
        instrument="MNQ", direction="LONG", entry=100.0, stop=90.0, target=130.0,
        rr_ratio=3.0, strategy="orb_reclaim", contracts=1,
    ))
    fill = None
    for b in bars:
        fill = broker.resolve_position(NextBarOHLC(high=b["high"], low=b["low"]))
        if fill is not None:
            break
    assert fill is not None
    assert fill.result == shadow_result["result"]
    assert fill.exit_price == pytest.approx(shadow_result["exit_price"])


def test_resolve_shadow_position_tracks_mfe_and_mae():
    position = {
        "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 130.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    bars = _bars(
        ("b1", 106.0, 97.0),   # MFE so far = 6, MAE so far = 3
        ("b2", 89.0, 85.0),    # stop (90) hit this bar
    )
    result = resolve_shadow_position(position, bars, activation_r=1.0, trail_r=0.5)
    assert result["result"] == "LOSS"
    assert result["max_favorable_excursion"] == pytest.approx(6.0)
    assert result["max_adverse_excursion"] >= 3.0


def test_resolve_shadow_position_returns_none_while_still_open():
    position = {
        "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 130.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    bars = _bars(("b1", 105.0, 101.0))
    assert resolve_shadow_position(position, bars) is None


def test_resolve_shadow_position_times_out_after_configured_age():
    stale_opened_at = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
    position = {
        "direction": "LONG", "entry": 100.0, "stop": 90.0, "target": 130.0,
        "opened_at": stale_opened_at,
    }
    bars = _bars(("b1", 105.0, 101.0, 104.0))
    result = resolve_shadow_position(position, bars, timeout_hours=8.0)
    assert result is not None
    assert result["result"] == "TIMEOUT"
    assert result["exit_price"] == pytest.approx(104.0)


def test_resolve_shadow_position_short_direction():
    position = {
        "direction": "SHORT", "entry": 100.0, "stop": 110.0, "target": 70.0,
        "opened_at": datetime.now(timezone.utc).isoformat(),
    }
    bars = _bars(
        ("b1", 93.0, 88.0),   # +1.2R favorable -> armed, trail = 88 + 5 = 93
        ("b2", 95.0, 90.0),   # high 95 >= trailed stop 93 -> stop hit
    )
    result = resolve_shadow_position(position, bars, activation_r=1.0, trail_r=0.5)
    assert result["result"] == "WIN"
    assert result["runner_activated"] is True


# ─── Shadow execution: evidence file ────────────────────────────────────────────

def test_append_entry_refresh_shadow_evidence_writes_a_jsonl_row(tmp_path):
    import json

    append_entry_refresh_shadow_evidence(str(tmp_path), {"instrument": "MNQ", "result": "WIN"})
    path = tmp_path / EVIDENCE_FILENAME
    assert path.exists()
    row = json.loads(path.read_text().splitlines()[0])
    assert row["instrument"] == "MNQ"
    assert row["result"] == "WIN"
    assert "observed_at" in row
