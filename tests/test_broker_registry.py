"""Tests for the read-only broker capability registry."""

from __future__ import annotations

from execution.broker_registry import (
    broker_matrix,
    broker_registry,
    get_broker_descriptor,
    routable_brokers,
    supports_asset_class,
)


def test_registry_orders_by_config_priority(config):
    keys = [broker.key for broker in broker_registry(config)]

    assert keys[:4] == ["paper", "tradovate_sim", "alpaca_options", "ibkr_paper"]


def test_only_paper_is_routable_by_default(config):
    routable = routable_brokers(config)

    assert [broker.key for broker in routable] == ["paper"]
    assert routable[0].execution_route_allowed is True


def test_alpaca_options_is_dormant_not_futures_route(config):
    broker = get_broker_descriptor("alpaca_options", config)

    assert broker is not None
    assert broker.supports_options is True
    assert broker.supports_futures is False
    assert broker.supports_brackets is False
    assert broker.default_enabled is False
    assert broker.execution_route_allowed is False


def test_broker_matrix_serializes_cleanly(config):
    matrix = broker_matrix(config)

    assert matrix["active_default"] == "paper"
    assert matrix["routable"] == ["paper"]
    assert any(item["key"] == "ibkr_paper" for item in matrix["brokers"])


def test_supports_asset_class_finds_options_lanes(config):
    keys = {broker.key for broker in supports_asset_class("options", config)}

    assert "alpaca_options" in keys
    assert "ibkr_paper" in keys
    assert "paper" not in keys


def test_status_brokers_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    from fastapi.testclient import TestClient
    from webhook.app import app

    resp = TestClient(app).get("/status/brokers")

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["active_default"] == "paper"
    assert payload["routable"] == ["paper"]
    assert any(item["key"] == "alpaca_options" for item in payload["brokers"])
