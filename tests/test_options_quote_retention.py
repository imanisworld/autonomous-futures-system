"""Tests for fail-closed option quote retention and manifest generation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from options_manager.quotes import (
    QuoteRetentionInput,
    build_quote_manifest,
    quote_manifest_json,
    quote_record_json,
    retain_quote,
    retention_rule_from_mapping,
)
from options_manager.validation.demo_qualification import _hash_check

RULE_PATH = Path("options_manager/quotes/quote_retention_rule_v1.json")


def _rule():
    return retention_rule_from_mapping(json.loads(RULE_PATH.read_text()))


def _rule_sha():
    return hashlib.sha256(RULE_PATH.read_bytes()).hexdigest()


def _quote(**overrides):
    values = dict(contract_id="SPY261120C00550000", underlying="SPY", expiration="2026-11-20", strike=550.0, right="CALL", bid=4.80, ask=5.00, quote_ts="2026-09-18T14:00:00+00:00", decision_ts="2026-09-18T14:01:00+00:00", source="fixture:option_chain_snapshot", volume=500, open_interest=1500, delta=0.50, iv=0.25)
    values.update(overrides)
    return QuoteRetentionInput(**values)


def _retain(**overrides):
    return retain_quote(_quote(**overrides), rule=_rule(), rule_sha256=_rule_sha())


def test_valid_quote_is_ok_and_carries_rule_provenance():
    record = _retain(); assert record.status == "OK"; assert record.reason_code == "quote_retained"; assert record.rule_sha256 == _rule_sha(); assert record.quote_age_seconds == 60.0


def test_missing_quote_timestamp_is_retained_as_missing():
    record = _retain(quote_ts=None); assert record.status == "MISSING"; assert record.reason_code == "required_field_missing"; assert "quote_ts" in record.missing_fields


def test_missing_bid_or_ask_is_retained_as_missing():
    assert _retain(bid=None).status == "MISSING"; assert _retain(ask=None).status == "MISSING"


def test_stale_quote_is_retained_but_not_ok():
    record = _retain(quote_ts="2026-09-18T13:40:00+00:00"); assert record.status == "STALE"; assert record.reason_code == "quote_stale"; assert record.quote_age_seconds == 1260.0


def test_future_quote_is_retained_but_not_ok():
    record = _retain(quote_ts="2026-09-18T14:02:00+00:00"); assert record.status == "FUTURE"; assert record.reason_code == "quote_after_decision"


def test_wide_spread_is_recorded_and_not_ok():
    record = _retain(bid=1.0, ask=2.0); assert record.status == "WIDE_SPREAD"; assert record.reason_code == "spread_too_wide"; assert record.spread_percent > 20.0


def test_free_text_source_is_rejected():
    record = _retain(source="some vendor endpoint"); assert record.status == "INVALID"; assert record.reason_code == "source_not_allowed"


def test_naive_timestamp_is_invalid():
    record = _retain(quote_ts="2026-09-18T14:00:00"); assert record.status == "INVALID"; assert record.reason_code == "timestamp_invalid"


def test_invalid_liquidity_numeric_types_fail_closed():
    record = _retain(volume=True); assert record.status == "INVALID"; assert record.reason_code == "liquidity_field_invalid"


def test_canonical_json_is_byte_stable():
    first = quote_record_json(_retain()).encode("utf-8"); second = quote_record_json(_retain()).encode("utf-8"); assert first == second; assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_manifest_is_reproducible_and_sorted():
    a = quote_record_json(_retain(contract_id="A")).encode(); b = quote_record_json(_retain(contract_id="B")).encode()
    first = build_quote_manifest({"quotes/b.jsonl": b, "quotes/a.jsonl": a}, rule=_rule(), rule_sha256=_rule_sha())
    second = build_quote_manifest({"quotes/a.jsonl": a, "quotes/b.jsonl": b}, rule=_rule(), rule_sha256=_rule_sha())
    assert first == second; assert [entry["path"] for entry in first["files"]] == ["quotes/a.jsonl", "quotes/b.jsonl"]


def test_manifest_hashes_exact_bytes_and_counts_rows():
    row = quote_record_json(_retain()).encode(); payload = row + row
    manifest = build_quote_manifest({"quotes/day.jsonl": payload}, rule=_rule(), rule_sha256=_rule_sha())
    entry = manifest["files"][0]; assert entry["sha256"] == hashlib.sha256(payload).hexdigest(); assert entry["row_count"] == 2; assert manifest["max_quote_age_seconds"] == 900


def test_manifest_serialization_is_canonical_for_gate_hash(tmp_path):
    row = quote_record_json(_retain()).encode()
    manifest = build_quote_manifest({"quotes/day.jsonl": row}, rule=_rule(), rule_sha256=_rule_sha())
    first = quote_manifest_json(manifest).encode("utf-8")
    second = quote_manifest_json(dict(reversed(list(manifest.items())))).encode("utf-8")
    assert first == second
    path = tmp_path / "option_quotes_manifest.json"
    path.write_bytes(first)
    evidence_sha = hashlib.sha256(first).hexdigest()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == evidence_sha


def test_canonical_manifest_passes_demo_gate_hash_and_tamper_fails(tmp_path):
    row = quote_record_json(_retain()).encode()
    manifest = build_quote_manifest({"quotes/day.jsonl": row}, rule=_rule(), rule_sha256=_rule_sha())
    path = tmp_path / "option_quotes_manifest.json"
    payload = quote_manifest_json(manifest).encode("utf-8")
    path.write_bytes(payload)
    claimed = hashlib.sha256(payload).hexdigest()
    section = {"option_quotes_manifest_path": path.name, "option_quotes_manifest_sha256": claimed}

    blockers: list[str] = []
    check = _hash_check(tmp_path, section, "option_quotes_manifest_path", "option_quotes_manifest_sha256", blockers, "data_integrity")
    assert blockers == []
    assert check["claimed_sha256"] == claimed
    assert check["actual_sha256"] == claimed

    path.write_bytes(payload + b"\n")
    blockers = []
    check = _hash_check(tmp_path, section, "option_quotes_manifest_path", "option_quotes_manifest_sha256", blockers, "data_integrity")
    assert blockers == ["data_integrity.option_quotes_manifest_sha256 does not match current bytes"]
    assert check["actual_sha256"] != claimed


def test_manifest_rejects_unknown_source():
    record = json.loads(quote_record_json(_retain())); record["source"] = "unknown:endpoint"; payload = (json.dumps(record, sort_keys=True) + "\n").encode()
    try: build_quote_manifest({"quotes/day.jsonl": payload}, rule=_rule(), rule_sha256=_rule_sha())
    except ValueError as exc: assert "unsupported source" in str(exc)
    else: raise AssertionError("unknown source must fail closed")


def test_manifest_rejects_row_rule_sha_mismatch():
    record = json.loads(quote_record_json(_retain())); record["rule_sha256"] = "0" * 64; payload = (json.dumps(record, sort_keys=True) + "\n").encode()
    try: build_quote_manifest({"quotes/day.jsonl": payload}, rule=_rule(), rule_sha256=_rule_sha())
    except ValueError as exc: assert "rule_sha256" in str(exc)
    else: raise AssertionError("row with mismatched rule SHA must fail closed")


def test_manifest_rejects_schema_drift():
    record = json.loads(quote_record_json(_retain())); del record["quote_ts"]; payload = (json.dumps(record, sort_keys=True) + "\n").encode()
    try: build_quote_manifest({"quotes/day.jsonl": payload}, rule=_rule(), rule_sha256=_rule_sha())
    except ValueError as exc: assert "schema mismatch" in str(exc)
    else: raise AssertionError("row schema drift must fail closed")


def test_rule_rejects_unknown_allowed_source():
    raw = json.loads(RULE_PATH.read_text()); raw["allowed_sources"] = ["invented:source"]
    try: retention_rule_from_mapping(raw)
    except ValueError as exc: assert "unsupported quote source" in str(exc)
    else: raise AssertionError("unknown rule source must fail closed")


def test_public_option_chain_source_is_frozen_and_accepted():
    record = _retain(source="public:/userapigateway/marketdata/{accountId}/option-chain"); assert record.status == "OK"; assert record.source == "public:/userapigateway/marketdata/{accountId}/option-chain"
