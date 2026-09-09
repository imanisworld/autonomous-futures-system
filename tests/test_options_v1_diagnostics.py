from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from alert_ranker.contract_marks import record_contract_mark
from alert_ranker.storage import ScanStorage
from alert_ranker.v1_diagnostics import (
    build_diagnostics_report,
    build_v1_diagnostics_capture,
    diagnostic_snapshots,
    directional_entry_extension,
    friction_pnl_dollars,
    record_diagnostic_snapshot,
    sample_status,
)


def _insert_shadow(
    storage: ScanStorage,
    *,
    timestamp: datetime,
    shadow_id: int = 1,
    direction: str = "LONG",
    status: str = "WIN",
    lane: str = "ACTIVE",
    outcome: dict | None = None,
) -> None:
    selected = {
        "paper_policy_id": "OPTIONS_PAPER_V1",
        "paper_policy_status": "VALID",
        "paper_evidence_lane": lane,
        "risk_budget_consumed": lane != "COUNTERFACTUAL",
        "contract": "TEST260101C00100000",
        "option_mark": 2.0,
        "option_bid": 1.9,
        "option_ask": 2.0,
        "contracts": 1,
        "planned_risk_dollars": 50.0,
        "setup_type": "DAILY_212_CONTINUATION",
        "setup_timeframe": "DAILY",
    }
    setup_inputs = {
        "setup_type": "DAILY_212_CONTINUATION",
        "setup_timeframe": "DAILY",
        "setup_entry_trigger": 99.5,
        "price": 100.0,
    }
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO options_shadow_journal (
                id, timestamp, scan_id, ticker, direction, score, pattern, status,
                setup_inputs_json, provider_snapshot_json, selected_contract_json,
                outcome_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                shadow_id,
                timestamp.isoformat(),
                shadow_id,
                "SPY",
                direction,
                8,
                "2-1-2",
                status,
                json.dumps(setup_inputs),
                "{}",
                json.dumps(selected),
                json.dumps(outcome or {}),
            ),
        )


def test_directional_entry_extension_handles_long_and_short():
    long_abs, long_pct = directional_entry_extension("LONG", 101.0, 100.0)
    short_abs, short_pct = directional_entry_extension("SHORT", 99.0, 100.0)
    assert long_abs == 1.0
    assert short_abs == 1.0
    assert long_pct == 1.0
    assert short_pct == 1.0


def test_friction_overlay_is_additive_stress_not_canonical_fill_change():
    base = friction_pnl_dollars(
        entry_ask=2.0,
        exit_bid=2.5,
        contracts=1,
    )
    fee = friction_pnl_dollars(
        entry_ask=2.0,
        exit_bid=2.5,
        contracts=1,
        fee_per_contract_per_leg=0.65,
    )
    stressed = friction_pnl_dollars(
        entry_ask=2.0,
        exit_bid=2.5,
        contracts=1,
        fee_per_contract_per_leg=0.65,
        extra_slippage_per_share_per_leg=0.01,
    )
    assert base == 50.0
    assert fee == 48.7
    assert stressed == 46.7


def test_report_calculates_mae_mfe_entry_extension_and_quality(tmp_path):
    storage = ScanStorage(tmp_path / "diag.sqlite")
    t0 = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    outcome = {
        "resolved_at": (t0 + timedelta(minutes=10)).isoformat(),
        "exit_mark": 2.5,
        "option_bid_at_resolution": 2.5,
        "resolution_ambiguity": "PATH_UNOBSERVED_BETWEEN_SNAPSHOTS",
        "intra_interval_path_known": False,
    }
    _insert_shadow(storage, timestamp=t0, outcome=outcome)

    for minutes, bid, underlying in ((0, 1.9, 100.0), (5, 1.5, 98.0), (10, 2.6, 104.0)):
        stamp = t0 + timedelta(minutes=minutes)
        record_contract_mark(
            storage,
            shadow_id=1,
            option_symbol="TEST260101C00100000",
            timestamp=stamp,
            bid=bid,
            ask=bid + 0.1,
            mid=bid + 0.05,
            delta=0.4,
            gamma=0.02,
            theta=-0.03,
            implied_volatility=0.25,
            quote_timestamp=stamp.isoformat(),
        )
        record_diagnostic_snapshot(
            storage,
            shadow_id=1,
            timestamp=stamp,
            event="ENTRY" if minutes == 0 else ("RESOLUTION" if minutes == 10 else "MARK"),
            underlying_price=underlying,
            setup_entry_trigger=99.5,
            option_bid=bid,
            option_ask=bid + 0.1,
            option_mid=bid + 0.05,
            quote_timestamp=stamp.isoformat(),
            delta=0.4,
            gamma=0.02,
            theta=-0.03,
            implied_volatility=0.25,
            setup_type="DAILY_212_CONTINUATION",
            setup_timeframe="DAILY",
        )

    report = build_diagnostics_report(storage)
    trade = report["trades"][0]
    assert trade["directional_entry_extension"] == 0.5
    assert trade["option_mae_percent"] == 25.0
    assert trade["option_mfe_percent"] == 30.0
    assert trade["underlying_mae"] == 2.0
    assert trade["underlying_mfe"] == 4.0
    assert trade["friction_pnl_dollars"]["RECORDED_EXECUTABLE"] == 50.0
    assert trade["friction_pnl_dollars"]["FEE_STRESS_065"] == 48.7
    assert trade["friction_pnl_dollars"]["FEE_065_PLUS_1C_SLIPPAGE"] == 46.7
    assert trade["evidence_quality"] == "MEDIUM"
    assert trade["evidence_flags"] == ["INTRA_INTERVAL_PATH_UNOBSERVED"]

    summary = report["strategy_summary"][0]
    assert summary["n_closed_priced"] == 1
    assert summary["sample_status"] == "INSUFFICIENT"
    assert summary["friction_total_pnl_dollars"]["RECORDED_EXECUTABLE"] == 50.0


def test_quality_flags_stale_gap_and_ambiguity_as_low(tmp_path):
    storage = ScanStorage(tmp_path / "low.sqlite")
    t0 = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    outcome = {
        "resolved_at": (t0 + timedelta(minutes=30)).isoformat(),
        "exit_mark": 1.4,
        "resolution_ambiguity": "AMBIGUOUS",
        "intra_interval_path_known": False,
    }
    _insert_shadow(storage, timestamp=t0, status="LOSS", outcome=outcome)
    for minutes, bid in ((0, 1.9), (30, 1.4)):
        stamp = t0 + timedelta(minutes=minutes)
        record_contract_mark(
            storage,
            shadow_id=1,
            option_symbol="TEST260101C00100000",
            timestamp=stamp,
            bid=bid,
            ask=bid + 0.1,
            mid=bid + 0.05,
            quote_timestamp=(stamp - timedelta(minutes=5)).isoformat(),
        )
        record_diagnostic_snapshot(
            storage,
            shadow_id=1,
            timestamp=stamp,
            event="ENTRY" if minutes == 0 else "RESOLUTION",
            underlying_price=100.0,
            setup_entry_trigger=99.5,
            option_bid=bid,
            option_ask=bid + 0.1,
            option_mid=bid + 0.05,
            quote_timestamp=(stamp - timedelta(minutes=5)).isoformat(),
            setup_type="DAILY_212_CONTINUATION",
            setup_timeframe="DAILY",
        )
    trade = build_diagnostics_report(storage)["trades"][0]
    assert trade["evidence_quality"] == "LOW"
    assert "ENTRY_QUOTE_STALE_GT_120S" in trade["evidence_flags"]
    assert "OBSERVATION_GAP_GT_12M" in trade["evidence_flags"]
    assert "AMBIGUOUS_RESOLUTION_PATH" in trade["evidence_flags"]


def test_counterfactual_lane_is_reported_separately(tmp_path):
    storage = ScanStorage(tmp_path / "counter.sqlite")
    t0 = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    _insert_shadow(
        storage,
        timestamp=t0,
        lane="COUNTERFACTUAL",
        status="OPEN",
        outcome={},
    )
    report = build_diagnostics_report(storage)
    assert report["trades"][0]["paper_evidence_lane"] == "COUNTERFACTUAL"
    assert report["strategy_summary"][0]["paper_evidence_lane"] == "COUNTERFACTUAL"


def test_sample_status_labels_are_reporting_only():
    assert sample_status(0) == "INSUFFICIENT"
    assert sample_status(19) == "INSUFFICIENT"
    assert sample_status(20) == "EARLY"
    assert sample_status(49) == "EARLY"
    assert sample_status(50) == "REVIEWABLE"


@pytest.mark.asyncio
async def test_scanner_wrapper_records_entry_and_resolver_snapshots(tmp_path):
    storage = ScanStorage(tmp_path / "wrapper.sqlite")
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)

    class Base:
        def __init__(self):
            self.storage = storage

        async def _process_normalized_candidate(self, ticker, normalized, *, source, now):
            raw = dict(normalized)
            result = SimpleNamespace(raw=raw)
            return SimpleNamespace(shadow_id=7, result=result)

        async def _resolve_v1_candidate(self, setup, underlying_price, now, chain_cache):
            record_contract_mark(
                self.storage,
                shadow_id=setup.id,
                option_symbol="TEST",
                timestamp=now,
                bid=2.1,
                ask=2.2,
                mid=2.15,
                delta=0.4,
                gamma=0.02,
                theta=-0.03,
                implied_volatility=0.25,
                quote_timestamp=now.isoformat(),
            )
            return None

    Wrapped = build_v1_diagnostics_capture(Base)
    scanner = Wrapped()
    await scanner._process_normalized_candidate(
        "SPY",
        {
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "price": 100.5,
            "setup_entry_trigger": 100.0,
            "option_bid": 1.9,
            "option_ask": 2.0,
            "option_quote_timestamp": now.isoformat(),
            "delta": 0.4,
            "gamma": 0.02,
            "theta": -0.03,
            "implied_volatility": 0.25,
            "setup_type": "DAILY_212_CONTINUATION",
            "setup_timeframe": "DAILY",
        },
        source="scheduled",
        now=now,
    )
    setup = SimpleNamespace(
        id=7,
        selected_contract={
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "setup_type": "DAILY_212_CONTINUATION",
            "setup_timeframe": "DAILY",
        },
        setup_inputs={"setup_entry_trigger": 100.0},
    )
    await scanner._resolve_v1_candidate(
        setup,
        101.0,
        now + timedelta(minutes=5),
        {},
    )
    snapshots = diagnostic_snapshots(storage, 7)
    assert [item.event for item in snapshots] == ["ENTRY", "MARK"]
    assert snapshots[0].underlying_price == 100.5
    assert snapshots[1].underlying_price == 101.0
