from __future__ import annotations

import hashlib
import json
from pathlib import Path

from options_manager.quotes import (
    QuoteRetentionInput,
    build_quote_manifest,
    quote_record_json,
    retain_quote,
    retention_rule_from_mapping,
)

V1 = Path("options_manager/quotes/quote_retention_rule_v1.json")
V2 = Path("options_manager/quotes/quote_retention_rule_v2.json")
V1_SHA256 = "5e4f24fffd727ad90d55d0c6ef15cfd0cefc5767f474d22e9437f60d6f2bff4b"
MASSIVE_SOURCE = "massive:/v3/quotes/{optionsTicker}"


def _load(path: Path):
    raw = path.read_bytes()
    return retention_rule_from_mapping(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def _quote(source: str) -> QuoteRetentionInput:
    return QuoteRetentionInput(
        contract_id="O:AMZN261120C00260000",
        underlying="AMZN",
        expiration="2026-11-20",
        strike=260.0,

        right="CALL",
        bid=13.75,
        ask=14.00,
        quote_ts="2026-09-09T15:17:53.207047867+00:00",
        decision_ts="2026-09-09T15:17:57+00:00",
        source=source,
        volume=234,
        open_interest=1500,
        delta=0.50,
        iv=0.25,
    )


def test_v1_bytes_are_frozen():
    assert hashlib.sha256(V1.read_bytes()).hexdigest() == V1_SHA256


def test_v2_is_semantic_superset_not_rewrite_of_v1():
    v1, _ = _load(V1)
    v2, _ = _load(V2)
    assert v1.rule_id == "quote-retention-v1"
    assert v2.rule_id == "quote-retention-v2"
    assert v2.max_quote_age_seconds == v1.max_quote_age_seconds == 900
    assert v2.max_spread_percent == v1.max_spread_percent == 20.0
    assert set(v1.allowed_sources).issubset(set(v2.allowed_sources))
    assert len(v2.allowed_sources) == len(v1.allowed_sources) + 1
    assert MASSIVE_SOURCE in {source.value for source in v2.allowed_sources}


def test_massive_historical_quote_is_allowed_only_under_v2():
    v1, v1_sha = _load(V1)
    v2, v2_sha = _load(V2)

    old = retain_quote(_quote(MASSIVE_SOURCE), rule=v1, rule_sha256=v1_sha)
    new = retain_quote(_quote(MASSIVE_SOURCE), rule=v2, rule_sha256=v2_sha)

    assert old.status == "INVALID"
    assert old.reason_code == "source_not_allowed"
    assert new.status == "OK"
    assert new.reason_code == "quote_retained"
    assert new.rule_id == "quote-retention-v2"
    assert new.source == MASSIVE_SOURCE


def test_v2_manifest_accepts_massive_provenance_and_hashes_exact_bytes():
    rule, rule_sha = _load(V2)
    row = quote_record_json(
        retain_quote(_quote(MASSIVE_SOURCE), rule=rule, rule_sha256=rule_sha)
    ).encode("utf-8")
    manifest = build_quote_manifest(
        {"quotes/amzn_2026-09-09.jsonl": row},
        rule=rule,
        rule_sha256=rule_sha,
    )
    assert manifest["rule_id"] == "quote-retention-v2"
    assert manifest["rule_sha256"] == rule_sha
    assert manifest["sources"] == [MASSIVE_SOURCE]
    assert manifest["files"][0]["sha256"] == hashlib.sha256(row).hexdigest()


def test_new_canonical_defaults_point_to_v2():
    import options_manager.app as app_module
    import scripts.options_public_quote_timestamp_probe as probe_module
    import scripts.options_quote_dataset_manifest as manifest_module

    assert app_module._QUOTE_RULE_PATH.name == "quote_retention_rule_v2.json"
    assert probe_module.QUOTE_RULE_PATH.name == "quote_retention_rule_v2.json"
    assert manifest_module.DEFAULT_RULE.name == "quote_retention_rule_v2.json"
