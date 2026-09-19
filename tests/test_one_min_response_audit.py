from __future__ import annotations

import json
from types import SimpleNamespace

from context.one_min_response_audit import append_observer_response_audit


def _payload(*, timeframe="1m"):
    return SimpleNamespace(
        timestamp="2026-09-21T13:31:00+00:00",
        ticker="MNQ1!",
        timeframe=timeframe,
        event_id="evt-1",
    )


def test_no_observer_event_writes_nothing(tmp_path):
    out = append_observer_response_audit(
        str(tmp_path),
        _payload(),
        {
            "decision": "ONE_MIN_CONTEXT",
            "fill": None,
            "risk": None,
            "execution_reachable": False,
            "one_min_trigger": None,
            "one_min_322_observer": None,
        },
    )
    assert out is None
    assert not list((tmp_path / "tf1m").glob("observer_response_audit_*.jsonl"))


def test_4hr_touch_persists_actual_response_safety_fields(tmp_path):
    event = {
        "event": "TRIGGER_TOUCH",
        "strategy": "strat_4hr_retrigger",
        "arm_key": "arm-4hr",
        "trade_authorized": False,
        "external_broker": False,
    }
    out = append_observer_response_audit(
        str(tmp_path),
        _payload(),
        {
            "decision": "ONE_MIN_CONTEXT",
            "fill": None,
            "risk": None,
            "resolution": None,
            "execution_reachable": False,
            "one_min_trigger": event,
            "one_min_322_observer": None,
        },
    )
    assert out is not None
    path = tmp_path / "tf1m" / "observer_response_audit_2026-09-21.jsonl"
    row = json.loads(path.read_text().strip())
    assert row["response"] == {
        "decision": "ONE_MIN_CONTEXT",
        "fill_is_none": True,
        "risk_is_none": True,
        "execution_reachable": False,
        "resolution": None,
    }
    assert row["one_min_trigger"]["arm_key"] == "arm-4hr"


def test_322_arm_persists_five_min_context_response(tmp_path):
    event = {
        "event": "ARMED",
        "strategy": "strat_322_first_live",
        "trade_authorized": False,
        "paper_fill_authorized": False,
        "external_broker": False,
    }
    append_observer_response_audit(
        str(tmp_path),
        _payload(timeframe="5m"),
        {
            "decision": "FIVE_MIN_CONTEXT",
            "fill": None,
            "risk": None,
            "resolution": None,
            "execution_reachable": False,
            "one_min_trigger": None,
            "one_min_322_observer": event,
        },
    )
    path = tmp_path / "tf1m" / "observer_response_audit_2026-09-21.jsonl"
    row = json.loads(path.read_text().strip())
    assert row["payload"]["timeframe"] == "5m"
    assert row["response"]["decision"] == "FIVE_MIN_CONTEXT"
    assert row["one_min_322_observer"]["event"] == "ARMED"


def test_violation_is_recorded_not_silently_normalized(tmp_path):
    event = {"event": "TRIGGER_TOUCH", "strategy": "strat_322_first_live"}
    append_observer_response_audit(
        str(tmp_path),
        _payload(),
        {
            "decision": "TRADE",
            "fill": {"result": "OPEN"},
            "risk": {"approved": True},
            "execution_reachable": True,
            "one_min_322_observer": event,
        },
    )
    path = tmp_path / "tf1m" / "observer_response_audit_2026-09-21.jsonl"
    row = json.loads(path.read_text().strip())
    assert row["response"]["decision"] == "TRADE"
    assert row["response"]["fill_is_none"] is False
    assert row["response"]["risk_is_none"] is False
    assert row["response"]["execution_reachable"] is True


def test_app_wires_response_audit_and_failure_is_fail_soft(monkeypatch, tmp_path):
    import notifications.discord_router as router_module
    import webhook.app as app

    result = {
        "decision": "ONE_MIN_CONTEXT",
        "fill": None,
        "risk": None,
        "execution_reachable": False,
        "one_min_trigger": {"event": "TRIGGER_TOUCH"},
        "one_min_322_observer": None,
    }
    cfg = SimpleNamespace(
        log_dir=str(tmp_path),
        live_quote_enabled=False,
        discord_notify_decisions=set(),
    )
    monkeypatch.setattr(app, "_config", cfg)
    monkeypatch.setattr(app, "process_alert", lambda *a, **k: dict(result))
    monkeypatch.setattr(app, "_record_latest_webhook", lambda *a, **k: None)
    monkeypatch.setattr(app, "notify_discord", lambda **k: None)

    class Router:
        def is_enabled(self, channel):
            return False

        def send(self, *args, **kwargs):
            raise AssertionError("send should not be reached")

    monkeypatch.setattr(router_module, "DiscordRouter", Router)

    calls = []
    monkeypatch.setattr(
        app,
        "append_observer_response_audit",
        lambda log_dir, payload, response: calls.append(
            (log_dir, response["decision"])
        ),
    )
    app._handle_alert_blocking(_payload())
    assert calls == [(str(tmp_path), "ONE_MIN_CONTEXT")]

    monkeypatch.setattr(
        app,
        "append_observer_response_audit",
        lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
    )
    app._handle_alert_blocking(_payload())

def test_response_audit_is_append_only(tmp_path):
    event = {"event": "TRIGGER_TOUCH", "strategy": "strat_4hr_retrigger"}
    result = {
        "decision": "ONE_MIN_CONTEXT",
        "fill": None,
        "risk": None,
        "execution_reachable": False,
        "one_min_trigger": event,
        "one_min_322_observer": None,
    }
    append_observer_response_audit(str(tmp_path), _payload(), result)
    append_observer_response_audit(str(tmp_path), _payload(), result)
    path = tmp_path / "tf1m" / "observer_response_audit_2026-09-21.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    assert len(rows) == 2


def test_response_audit_module_has_no_execution_imports():
    import ast
    from pathlib import Path

    path = Path(__file__).parents[1] / "context" / "one_min_response_audit.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(name.startswith(("execution", "risk", "webhook.runner")) for name in imported)
