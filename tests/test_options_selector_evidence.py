from __future__ import annotations

import hashlib

from alert_ranker.market_data import (
    OptionChain,
    OptionContractQuote,
    PUBLIC_OPTION_CHAIN_SOURCE,
)
from alert_ranker.options_production_selector_replay import replay_production_selector
from alert_ranker.options_selector_evidence import (
    blocked_selector_evidence,
    build_selector_evidence_capture,
    finalize_selector_evidence,
)
from alert_ranker.storage import ScanStorage


def _quote() -> OptionContractQuote:
    return OptionContractQuote(
        symbol="SPY261120C00550000",
        option_type="CALL",
        strike=550.0,
        bid=4.8,
        ask=5.0,
        mid=4.9,
        last=4.9,
        volume=500.0,
        open_interest=1500.0,
        delta=0.5,
        implied_volatility=0.25,
        quote_timestamp="2026-09-18T14:00:00+00:00",
        bid_timestamp="2026-09-18T14:00:00+00:00",
        ask_timestamp="2026-09-18T14:00:01+00:00",
        source=PUBLIC_OPTION_CHAIN_SOURCE,
    )


def test_capture_preserves_replay_bytes_side_timestamps_and_underlying_provenance():
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(_quote(),),
        puts=(),
    )
    evidence = build_selector_evidence_capture(
        ticker="SPY",
        production_direction="LONG",
        expirations=["2026-10-16", "2026-11-20", "2026-12-18"],
        chosen_expiration="2026-11-20",
        chain=chain,
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        underlying_snapshot={
            "provider": "public",
            "price": 550.0,
            "quote_timestamp": "2026-09-18T14:00:30+00:00",
            "price_source": "market_data_snapshot",
        },
    )

    assert evidence["status"] == "CAPTURED"
    assert evidence["evidence_version"] == 2
    assert evidence["selector_authority"] == "OPTIONS_PAPER_V1"
    assert evidence["canonical_selector_role"] == "reference_only_not_production_authority"
    assert len(evidence["production_selector_code_sha256"]) == 64
    assert evidence["chosen_expiration"] == "2026-11-20"
    assert evidence["selector_input_rows"] == 1
    assert hashlib.sha256(
        (evidence["selector_input_json"] + "\n").encode("utf-8")
    ).hexdigest() == evidence["selector_input_sha256"]
    assert evidence["canonical_selector_result"]["status"] == "SELECTED"
    assert evidence["canonical_selector_result"]["contract_id"] == "SPY261120C00550000"
    supplement = evidence["chain_supplement"][0]
    assert supplement["bid_timestamp"] == "2026-09-18T14:00:00+00:00"
    assert supplement["ask_timestamp"] == "2026-09-18T14:00:01+00:00"
    assert supplement["source"] == PUBLIC_OPTION_CHAIN_SOURCE
    assert supplement["implied_volatility"] == 0.25
    assert evidence["underlying"]["snapshot"]["price_source"] == "market_data_snapshot"


def test_finalize_binds_actual_production_selection_and_refreshes_hash():
    blocked = blocked_selector_evidence(
        ticker="SPY",
        production_direction="LONG",
        decision_ts="2026-09-18T14:01:00+00:00",
        reason_code="selector_capture_invalid:ValueError",
    )
    original_hash = blocked["evidence_sha256"]
    final = finalize_selector_evidence(
        blocked,
        production_selection={
            "status": "DATA_INVALID",
            "reason": "no_liquid_contract",
            "contract": None,
        },
    )

    assert final["status"] == "DATA_BLOCKED"
    assert final["reason_code"] == "selector_capture_invalid:ValueError"
    assert final["production_selection"]["reason"] == "no_liquid_contract"
    assert final["evidence_sha256"] != original_hash


def test_storage_is_append_only_and_round_trips_evidence(tmp_path):
    storage = ScanStorage(tmp_path / "options.sqlite")
    evidence = blocked_selector_evidence(
        ticker="QQQ",
        production_direction="SHORT",
        decision_ts="2026-09-18T15:01:00+00:00",
        reason_code="missing_required_selector_input",
    )
    first = storage.record_selector_evidence(evidence)
    second = storage.record_selector_evidence(evidence)

    assert second > first
    rows = storage.latest_selector_evidence(limit=10)
    assert len(rows) == 2
    assert rows[0]["ticker"] == "QQQ"
    assert rows[0]["status"] == "DATA_BLOCKED"
    assert rows[0]["reason_code"] == "missing_required_selector_input"
    assert rows[0]["_storage_id"] == second


def test_production_replay_can_match_when_reference_canonical_selector_differs():
    call_40 = OptionContractQuote(
        symbol="SPY261120C00555000",
        option_type="CALL",
        strike=555.0,
        bid=5.00,
        ask=5.10,
        mid=5.05,
        last=5.05,
        volume=900,
        open_interest=2500,
        delta=0.40,
        implied_volatility=0.25,
        quote_timestamp="2026-09-18T14:00:00+00:00",
        bid_timestamp="2026-09-18T14:00:00+00:00",
        ask_timestamp="2026-09-18T14:00:01+00:00",
        source=PUBLIC_OPTION_CHAIN_SOURCE,
    )
    call_50 = OptionContractQuote(
        symbol="SPY261120C00550000",
        option_type="CALL",
        strike=550.0,
        bid=5.00,
        ask=5.10,
        mid=5.05,
        last=5.05,
        volume=900,
        open_interest=2500,
        delta=0.50,
        implied_volatility=0.25,
        quote_timestamp="2026-09-18T14:00:00+00:00",
        bid_timestamp="2026-09-18T14:00:00+00:00",
        ask_timestamp="2026-09-18T14:00:01+00:00",
        source=PUBLIC_OPTION_CHAIN_SOURCE,
    )
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(call_40, call_50),
        puts=(),
    )
    evidence = build_selector_evidence_capture(
        ticker="SPY",
        production_direction="LONG",
        expirations=["2026-11-20"],
        chosen_expiration="2026-11-20",
        chain=chain,
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=552.0,
        underlying_snapshot={"provider": "public", "price_source": "market_data_snapshot"},
    )

    # Reference selector targets 0.50 delta; production V1 targets 0.40.
    assert evidence["canonical_selector_result"]["contract_id"] == "SPY261120C00550000"

    replay = replay_production_selector(evidence)
    assert replay["status"] == "VALID"
    assert replay["contract"]["symbol"] == "SPY261120C00555000"

    final = finalize_selector_evidence(
        evidence,
        production_selection={
            "status": "VALID",
            "reason": "",
            "contract": replay["contract"],
        },
    )
    assert final["status"] == "CAPTURED"
    assert final["production_replay_parity"] is True
    assert final["production_replay_result"]["contract"]["symbol"] == "SPY261120C00555000"


def test_finalize_fails_closed_when_production_replay_mismatches():
    chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(_quote(),),
        puts=(),
    )
    evidence = build_selector_evidence_capture(
        ticker="SPY",
        production_direction="LONG",
        expirations=["2026-11-20"],
        chosen_expiration="2026-11-20",
        chain=chain,
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        underlying_snapshot={"provider": "public", "price_source": "market_data_snapshot"},
    )
    final = finalize_selector_evidence(
        evidence,
        production_selection={
            "status": "VALID",
            "reason": "",
            "contract": {"symbol": "WRONG"},
        },
    )
    assert final["status"] == "DATA_BLOCKED"
    assert final["reason_code"] == "production_replay_mismatch"
    assert final["production_replay_parity"] is False
