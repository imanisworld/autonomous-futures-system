from __future__ import annotations

import ast
import json
from pathlib import Path

import context.futures_signa_shadow as shadow_module
from context.futures_signa_shadow import (
    append_futures_signa_shadow,
    direction_relation,
    enabled_from_env,
    evidence_path,
    timeframe_from_env,
)
from sources.signa_observation import SignaActionCardObservation


class FakeClient:
    def __init__(self, observation=None):
        self.calls = []
        self.observation = observation or SignaActionCardObservation(
            ok=True,
            symbol="QQQ",
            timeframe="1h",
            direction="LONG",
            score=84,
            grade="B",
            confidence=76,
            entry_low=500.0,
            entry_high=501.0,
            stop_loss=496.0,
            targets=(506.0, 510.0),
            reward_to_risk=2.0,
            component_scores={"flow": 81.0},
            model_version="test",
            data_as_of="2026-09-14T15:00:00Z",
            retrieved_at="2026-09-14T15:01:00Z",
        )

    def fetch_action_card(self, symbol, timeframe):
        self.calls.append((symbol, timeframe))
        return self.observation


def _kwargs(tmp_path):
    return {
        "log_dir": tmp_path,
        "instrument": "MNQ1!",
        "decision_timestamp": "2026-09-14T15:00:00+00:00",
        "session": "new_york",
        "decision": "NO_TRADE",
        "decision_reason": "no_setup",
        "strategy": "orb_breakout",
        "trade_direction": "LONG",
        "timeframe_minutes": 15,
    }


def test_disabled_means_no_request_and_no_write(tmp_path) -> None:
    client = FakeClient()
    result = append_futures_signa_shadow(
        **_kwargs(tmp_path),
        env={"FUTURES_SIGNA_SHADOW_ENABLED": "false"},
        client=client,
    )
    assert result is None
    assert client.calls == []
    assert not evidence_path(tmp_path).exists()


def test_enabled_requires_explicit_proven_timeframe_before_request(tmp_path) -> None:
    client = FakeClient()
    result = append_futures_signa_shadow(
        **_kwargs(tmp_path),
        env={"FUTURES_SIGNA_SHADOW_ENABLED": "true"},
        client=client,
    )
    assert client.calls == []
    assert result["signa_v2_ok"] is False
    assert result["signa_v2_error"] == "timeframe_unset_or_unproven"
    row = json.loads(evidence_path(tmp_path).read_text().strip())
    assert row["gate_authoritative"] is False
    assert row["execution_authoritative"] is False


def test_mnq_maps_to_qqq_and_persists_namespaced_action_card(tmp_path) -> None:
    client = FakeClient()
    result = append_futures_signa_shadow(
        **_kwargs(tmp_path),
        env={
            "FUTURES_SIGNA_SHADOW_ENABLED": "true",
            "FUTURES_SIGNA_SHADOW_TIMEFRAME": "1h",
        },
        client=client,
    )
    assert client.calls == [("QQQ", "1h")]
    assert result["instrument"] == "MNQ"
    assert result["proxy_symbol"] == "QQQ"
    assert result["signa_v2_direction"] == "LONG"
    assert result["signa_v2_confidence"] == 76
    assert result["signa_v2_component_scores"] == {"flow": 81.0}
    assert result["observation_only"] is True
    assert result["gate_authoritative"] is False
    assert result["risk_authoritative"] is False
    assert result["broker_authoritative"] is False
    assert result["execution_authoritative"] is False


def test_mes_maps_to_spy(tmp_path) -> None:
    client = FakeClient(
        SignaActionCardObservation(ok=True, symbol="SPY", timeframe="1d", direction="SHORT")
    )
    result = append_futures_signa_shadow(
        **{**_kwargs(tmp_path), "instrument": "MES"},
        env={
            "FUTURES_SIGNA_SHADOW_ENABLED": "true",
            "FUTURES_SIGNA_SHADOW_TIMEFRAME": "1d",
        },
        client=client,
    )
    assert client.calls == [("SPY", "1d")]
    assert result["proxy_symbol"] == "SPY"


def test_unsupported_instrument_is_ignored(tmp_path) -> None:
    client = FakeClient()
    result = append_futures_signa_shadow(
        **{**_kwargs(tmp_path), "instrument": "MGC"},
        env={
            "FUTURES_SIGNA_SHADOW_ENABLED": "true",
            "FUTURES_SIGNA_SHADOW_TIMEFRAME": "1h",
        },
        client=client,
    )
    assert result is None
    assert client.calls == []
    assert not evidence_path(tmp_path).exists()


def test_provider_failure_fails_soft_and_records_error(tmp_path) -> None:
    class BrokenClient:
        def fetch_action_card(self, symbol, timeframe):
            raise RuntimeError("boom")

    result = append_futures_signa_shadow(
        **_kwargs(tmp_path),
        env={
            "FUTURES_SIGNA_SHADOW_ENABLED": "true",
            "FUTURES_SIGNA_SHADOW_TIMEFRAME": "1h",
        },
        client=BrokenClient(),
    )
    assert result["signa_v2_ok"] is False
    assert result["signa_v2_error"] == "RuntimeError"
    assert result["decision"] == "NO_TRADE"


def test_default_client_is_process_local(monkeypatch) -> None:
    created = []

    class FakeSignaClient:
        pass

    def factory():
        value = FakeSignaClient()
        created.append(value)
        return value

    monkeypatch.setattr(shadow_module, "_shared_client", None)
    monkeypatch.setattr(shadow_module, "SignaV2Client", factory)
    first = shadow_module.default_client()
    second = shadow_module.default_client()
    assert first is second
    assert created == [first]


def test_timeframe_has_no_default_and_only_probe_scope_is_allowed() -> None:
    assert timeframe_from_env({}) is None
    assert timeframe_from_env({"FUTURES_SIGNA_SHADOW_TIMEFRAME": "1h"}) == "1h"
    assert timeframe_from_env({"FUTURES_SIGNA_SHADOW_TIMEFRAME": "1d"}) == "1d"
    assert timeframe_from_env({"FUTURES_SIGNA_SHADOW_TIMEFRAME": "4h"}) is None
    assert enabled_from_env({"FUTURES_SIGNA_SHADOW_ENABLED": "1"}) is True


def test_direction_relation_is_descriptive_only() -> None:
    assert direction_relation("LONG", "LONG") == "ALIGNED"
    assert direction_relation("SHORT", "DOWN") == "ALIGNED"
    assert direction_relation("LONG", "SHORT") == "OPPOSED"
    assert direction_relation("SHORT", "WAIT") == "NEUTRAL"
    assert direction_relation(None, "LONG") == "MISSING"


def test_module_has_no_strategy_risk_broker_or_execution_imports() -> None:
    source = Path("context/futures_signa_shadow.py").read_text()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = {"strategy", "risk", "execution", "webhook"}
    assert not any(name.split(".")[0] in forbidden for name in imported)
    assert "execute_bracket" not in source
    assert "validate(" not in source
