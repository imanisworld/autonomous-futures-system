from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from alert_ranker.market_data import OptionChain, OptionContractQuote, PUBLIC_OPTION_CHAIN_SOURCE
from options_manager.contracts import selector_rule_from_mapping
from scripts.options_public_selector_parity_probe import (
    RULE_PATH,
    choose_probe_expirations,
    direction_results_prove_parity,
    evaluate_capture,
)


def _rule():
    raw = RULE_PATH.read_bytes()
    return selector_rule_from_mapping(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def _quote(symbol: str, *, right="CALL", strike=550.0, delta=0.5):
    return OptionContractQuote(
        symbol=symbol,
        option_type=right,
        strike=strike,
        bid=4.8,
        ask=5.0,
        mid=4.9,
        last=4.9,
        volume=500.0,
        open_interest=1500.0,
        delta=delta,
        implied_volatility=0.25,
        quote_timestamp="2026-09-18T14:00:00+00:00",
        bid_timestamp="2026-09-18T14:00:00+00:00",
        ask_timestamp="2026-09-18T14:00:01+00:00",
        source=PUBLIC_OPTION_CHAIN_SOURCE,
    )


def _chain(expiration: str):
    return OptionChain(
        underlying="SPY",
        expiration=expiration,
        calls=(_quote("CALL-A"),),
        puts=(_quote("PUT-A", right="PUT", delta=-0.5),),
        error=None,
    )


def test_probe_expirations_use_nearest_two_preferred_when_available():
    result = choose_probe_expirations(
        ["2026-10-02", "2026-11-20", "2026-12-18", "2027-01-15"],
        today=date(2026, 9, 18),
        min_dte=14,
        preferred_min_dte=45,
    )
    assert result == ["2026-11-20", "2026-12-18"]


def test_probe_expirations_fall_back_to_nearest_min_dte_band():
    result = choose_probe_expirations(
        ["2026-09-25", "2026-10-02", "2026-10-16", "2026-10-30"],
        today=date(2026, 9, 18),
        min_dte=14,
        preferred_min_dte=45,
    )
    assert result == ["2026-10-02", "2026-10-16"]


def test_capture_is_byte_stable_and_replay_forward_identical_for_both_rights():
    rule, rule_sha = _rule()
    chains = [_chain("2026-11-20"), _chain("2026-12-18")]
    call = evaluate_capture(
        chains=chains,
        rule=rule,
        rule_sha256=rule_sha,
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        direction="CALL",
    )

    put = evaluate_capture(
        chains=chains,
        rule=rule,
        rule_sha256=rule_sha,
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        direction="PUT",
    )

    for result in (call, put):
        assert result["input_round_trip_byte_stable"] is True
        assert result["replay_forward_result_byte_parity"] is True
        assert result["selection"]["status"] == "SELECTED"
        assert result["selection"]["expiration"] == "2026-11-20"
        assert result["selector_input_rows"] == 4

    assert call["selection"]["contract_id"] == "CALL-A"
    assert put["selection"]["contract_id"] == "PUT-A"
    assert call["selector_input_sha256"] != put["selector_input_sha256"]


def test_direct_probe_help_works_without_pythonpath():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/options_public_selector_parity_probe.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "Read-only Public" in result.stdout


def test_no_contract_outcome_can_still_prove_replay_forward_parity():
    rule, rule_sha = _rule()
    empty_quality_chain = OptionChain(
        underlying="SPY",
        expiration="2026-11-20",
        calls=(
            OptionContractQuote(
                symbol="ILLIQUID",
                option_type="CALL",
                strike=550.0,
                bid=4.8,
                ask=5.0,
                mid=4.9,
                last=4.9,
                volume=0.0,
                open_interest=0.0,
                delta=0.5,
                implied_volatility=0.25,
                quote_timestamp="2026-09-18T14:00:00+00:00",
                bid_timestamp="2026-09-18T14:00:00+00:00",
                ask_timestamp="2026-09-18T14:00:01+00:00",
                source=PUBLIC_OPTION_CHAIN_SOURCE,
            ),
        ),
        puts=(),
        error=None,
    )
    result = evaluate_capture(
        chains=[empty_quality_chain],
        rule=rule,
        rule_sha256=rule_sha,
        decision_ts="2026-09-18T14:01:00+00:00",
        underlying_price=550.0,
        direction="CALL",
    )
    assert result["selection"]["status"] == "NO_CONTRACT"
    assert direction_results_prove_parity([result]) is True
