"""Tests for fail-closed verification of frozen option quote dataset bytes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from options_manager.quotes import (
    QuoteRetentionInput,
    build_quote_manifest,
    quote_record_json,
    retain_quote,
    retention_rule_from_mapping,
    verify_quote_manifest_files,
)

RULE_PATH = Path("options_manager/quotes/quote_retention_rule_v1.json")


def _dataset():
    rule = retention_rule_from_mapping(json.loads(RULE_PATH.read_text()))
    rule_sha = hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()
    record = retain_quote(
        QuoteRetentionInput(
            contract_id="SPY261120C00550000",
            underlying="SPY",
            expiration="2026-11-20",
            strike=550.0,
            right="CALL",
            bid=4.80,
            ask=5.00,
            quote_ts="2026-09-18T14:00:00+00:00",
            decision_ts="2026-09-18T14:01:00+00:00",
            source="fixture:option_chain_snapshot",
            volume=500,
            open_interest=1500,
            delta=0.50,
            iv=0.25,
        ),
        rule=rule,
        rule_sha256=rule_sha,
    )
    files = {"quotes/day.jsonl": quote_record_json(record).encode()}
    return build_quote_manifest(files, rule=rule, rule_sha256=rule_sha), files


def test_manifest_verifier_accepts_exact_frozen_bytes():
    manifest, files = _dataset()
    verify_quote_manifest_files(manifest, files)


def test_manifest_verifier_rejects_dataset_byte_tamper():
    manifest, files = _dataset()
    files["quotes/day.jsonl"] += b"\n"
    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_quote_manifest_files(manifest, files)


def test_manifest_verifier_rejects_missing_or_extra_dataset_files():
    manifest, files = _dataset()
    with pytest.raises(ValueError, match="dataset file missing"):
        verify_quote_manifest_files(manifest, {})
    files["quotes/unmanifested.jsonl"] = b""
    with pytest.raises(ValueError, match="unmanifested dataset files"):
        verify_quote_manifest_files(manifest, files)
