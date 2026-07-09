"""
Read-only HTTP exposure for the fixture-candidate inventory.

These tests keep the status surface narrow: it reports the hand-authored
inventory and summary counts, but it does not scan, broker, execute, or
write anything.
"""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

import options_manager.http_api as http_api_module
from options_manager.config import OptionsManagerConfig
from options_manager.http_api import STATUS_SECRET_HEADER, create_options_status_app
from options_manager.storage import init_options_storage
from options_manager.validation import PROOF_PACKET_FORWARD_CAPTURE_FIELDS

SECRET = "fixture-candidate-status-secret"
EXPECTED_TICKERS = ("HOOD", "EBAY", "AMD", "ORCL", "FITB", "BAC")
EXPECTED_COUNTS_BY_STATUS = {
    "pending_proof_fixture": 1,
    "special_case_fixture": 3,
    "incomplete": 2,
    "scalp_noise": 2,
    "management_case": 1,
    "reject": 3,
}
EXPECTED_MISSING_FORWARD_CAPTURE_FIELDS = tuple(
    name for name in PROOF_PACKET_FORWARD_CAPTURE_FIELDS if name != "ticker"
)


def _config(**overrides) -> OptionsManagerConfig:
    base = dict(http_status_secret=SECRET)
    base.update(overrides)
    return replace(OptionsManagerConfig(), **base)


def _client(tmp_path, config: OptionsManagerConfig | None = None) -> TestClient:
    path = str(tmp_path / "options_status_test.sqlite")
    config = config or _config()
    init_options_storage(path, config)
    return TestClient(create_options_status_app(config, db_path=path))


def _fixture_response(tmp_path):
    client = _client(tmp_path)
    return client.get(
        "/options/status/fixture-candidates",
        headers={STATUS_SECRET_HEADER: SECRET},
    )


def test_fixture_candidate_status_exposes_requested_tickers(tmp_path):
    response = _fixture_response(tmp_path)
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "FOUND"
    assert tuple(body["candidates"].keys()) == EXPECTED_TICKERS

    candidates = body["candidates"]
    assert candidates["HOOD"]["fixture_status"] == "pending_proof_fixture"
    assert candidates["EBAY"]["fixture_status"] == "special_case_fixture"
    assert candidates["AMD"]["fixture_status"] == "special_case_fixture"
    assert candidates["ORCL"]["fixture_status"] == "incomplete"
    assert candidates["FITB"]["fixture_status"] == "special_case_fixture"
    assert candidates["BAC"]["fixture_status"] == "incomplete"


def test_fixture_candidate_status_exposes_proof_packet_readiness(tmp_path):
    response = _fixture_response(tmp_path)
    body = response.json()

    for ticker, candidate in body["candidates"].items():
        assert candidate["proof_packet_ready"] is False, ticker
        assert (
            tuple(candidate["missing_forward_capture_fields"])
            == EXPECTED_MISSING_FORWARD_CAPTURE_FIELDS
        ), ticker

    hood_missing = body["candidates"]["HOOD"]["missing_forward_capture_fields"]
    assert "ticker" not in hood_missing
    assert "entry_trigger" in hood_missing
    assert "underlying_invalidation" in hood_missing
    assert "premium_stop" in hood_missing
    assert "qqq_context" in hood_missing
    assert "source_references" in hood_missing
    assert "actual_entry_time" not in hood_missing


def test_fixture_candidate_status_exposes_summary_counts(tmp_path):
    response = _fixture_response(tmp_path)
    body = response.json()

    assert body["summary"] == {
        "total_candidates": 12,
        "counts_by_status": EXPECTED_COUNTS_BY_STATUS,
    }
    assert sum(body["summary"]["counts_by_status"].values()) == 12


def test_fixture_candidate_status_uses_allowlisted_read_only_shape(tmp_path):
    response = _fixture_response(tmp_path)
    body = response.json()

    assert set(body.keys()) == {
        "status",
        "found",
        "source",
        "summary",
        "candidates",
        "submitted",
        "executable",
        "broker",
        "broker_order_id",
        "warnings",
    }
    assert body["submitted"] is False
    assert body["executable"] is False
    assert body["broker"] is None
    assert body["broker_order_id"] is None
    assert body["warnings"] == []

    allowed_candidate_keys = {
        "ticker",
        "window",
        "fixture_status",
        "best_future_use",
        "proof_confirmed",
        "proof_missing",
        "reason_not_first_proof",
        "promotion_requirements",
        "notes",
        "proof_packet_ready",
        "missing_forward_capture_fields",
    }
    for candidate in body["candidates"].values():
        assert set(candidate.keys()) == allowed_candidate_keys


def test_fixture_candidate_status_requires_status_secret(tmp_path):
    client = _client(tmp_path)

    missing = client.get("/options/status/fixture-candidates")
    assert missing.status_code == 401

    wrong = client.get(
        "/options/status/fixture-candidates",
        headers={STATUS_SECRET_HEADER: "wrong"},
    )
    assert wrong.status_code == 401


def test_fixture_candidate_status_returns_503_when_status_api_unconfigured(tmp_path):
    client = _client(tmp_path, _config(http_status_secret=""))

    response = client.get(
        "/options/status/fixture-candidates",
        headers={STATUS_SECRET_HEADER: SECRET},
    )
    assert response.status_code == 503


def test_http_status_api_imports_inventory_without_scanner_or_broker_paths():
    tree = ast.parse(Path(http_api_module.__file__).read_text())
    modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)

    forbidden = (
        "options_manager.scanner",
        "execution",
        "webhook",
        "risk_engine",
        "requests",
        "httpx",
        "socket",
    )
    for module in modules:
        for fragment in forbidden:
            assert fragment not in module
    assert "validation.fixture_status" in modules
    assert "validation" not in modules


def test_fixture_candidate_status_does_not_reference_write_or_order_actions():
    tree = ast.parse(Path(http_api_module.__file__).read_text())
    names = {
        node.id if isinstance(node, ast.Name) else node.attr
        for node in ast.walk(tree)
        if isinstance(node, (ast.Name, ast.Attribute))
    }
    forbidden = {
        "append_confirmation_consumed_event",
        "append_ticket_created_event",
        "scan_watchlist_strat_212",
        "place_order",
        "submit_order",
        "cancel_order",
        "preview_order",
    }
    assert not (names & forbidden)
