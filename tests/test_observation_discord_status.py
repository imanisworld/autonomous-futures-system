from __future__ import annotations

import json

from notifications import observation_notifier as obs
from notifications import observation_status as status
from notifications.discord_router import DiscordRouter, Route, _default_upsert_transport

WEBHOOK = "https://discord.invalid/api/webhooks/1/token"
ROUTES = {"observation": Route("observation", "DISCORD_ROUTE_OBSERVATION", False)}
ENV = {"DISCORD_ROUTE_OBSERVATION": WEBHOOK}


def _event(candidate_id: str, *, root: str = "MNQ", kind: str = "CANDIDATE", result: str | None = None,
           ts: str = "2026-09-25T14:30:00+00:00") -> dict:
    row = {
        "candidate_id": candidate_id,
        "record_type": kind,
        "instrument": root,
        "strategy": "strat_212",
        "direction": "LONG",
        "signal_timestamp": ts,
        "trading_date": ts[:10],
        "entry": 20000.0,
        "stop": 19990.0,
        "target": 20020.0,
    }
    if kind == "OUTCOME":
        row.update(
            result=result or "WIN",
            exit_timestamp=ts,
            exit_reason="TARGET_HIT",
            gross_pnl_dollars_1_contract=25.0,
        )
    return row


def test_status_store_deduplicates_and_resets_daily_counts(tmp_path, monkeypatch):
    state_path = tmp_path / "status.json"
    monkeypatch.setenv("DISCORD_OBSERVATION_STATUS_STATE", str(state_path))

    updates, represented = status.build_status_updates([_event("c1"), _event("c1")])
    assert represented == 1 and len(updates) == 1
    assert "Today: 1 setups · 0 resolved" in updates[0].text

    updates, represented = status.build_status_updates(
        [_event("o1", kind="OUTCOME", result="WIN", ts="2026-09-25T15:00:00+00:00")]
    )
    assert represented == 1
    assert "Today: 1 setups · 1 resolved" in updates[0].text
    assert "Outcomes: 1 won · 0 lost" in updates[0].text

    status.record_message_id("MNQ", "12345")
    updates, represented = status.build_status_updates(
        [_event("next-day", ts="2026-09-26T14:30:00+00:00")]
    )
    assert represented == 1
    assert updates[0].message_id == "12345"
    assert "Today: 1 setups · 0 resolved" in updates[0].text


def test_inline_notifier_reuses_persisted_message_id(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_OBSERVATION_STATUS_STATE", str(tmp_path / "status.json"))
    monkeypatch.setattr(obs, "DELIVERY_MODE", "inline")

    calls: list[tuple[str | None, str]] = []

    def upsert_transport(url, message, message_id):
        calls.append((message_id, str(message)))
        return message_id or "msg-1"

    router = DiscordRouter(
        routes=ROUTES,
        transport=lambda url, message: None,
        upsert_transport=upsert_transport,
        env=ENV,
    )

    assert obs.notify_observation([_event("c1")], router=router) == 1
    assert calls[0][0] is None

    assert obs.notify_observation(
        [_event("o1", kind="OUTCOME", result="WIN", ts="2026-09-25T15:00:00+00:00")],
        router=router,
    ) == 1
    assert calls[1][0] == "msg-1"

    raw = json.loads((tmp_path / "status.json").read_text())
    assert raw["MNQ"]["message_id"] == "msg-1"
    assert raw["MNQ"]["setups"] == 1
    assert raw["MNQ"]["resolved"] == 1


def test_one_batch_produces_one_update_per_ticker(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_OBSERVATION_STATUS_STATE", str(tmp_path / "status.json"))
    monkeypatch.setattr(obs, "DELIVERY_MODE", "inline")
    calls = []

    def upsert_transport(url, message, message_id):
        calls.append((message_id, str(message)))
        return f"msg-{len(calls)}"

    router = DiscordRouter(
        routes=ROUTES,
        transport=lambda url, message: None,
        upsert_transport=upsert_transport,
        env=ENV,
    )

    events = [
        _event("m1", root="MNQ"),
        _event("m2", root="MNQ"),
        _event("e1", root="MES"),
        _event("e2", root="MES", kind="OUTCOME", result="LOSS"),
    ]
    assert obs.notify_observation(events, router=router) == 4
    assert len(calls) == 2
    assert any("MNQ observer · active" in message for _, message in calls)
    assert any("MES observer · active" in message for _, message in calls)


class _Resp:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            request = httpx.Request("POST", WEBHOOK)
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("status", request=request, response=response)

    def json(self):
        return self._payload


def test_default_upsert_create_uses_wait_true_and_returns_message_id(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return _Resp(200, {"id": "abc123"})

    monkeypatch.setattr("httpx.post", fake_post)
    out = _default_upsert_transport(WEBHOOK, "hello", None, source="test")
    assert out == "abc123"
    assert calls and "wait=true" in calls[0][0]
    assert calls[0][1]["embeds"][0]["title"] == "hello"


def test_default_upsert_edit_uses_message_endpoint(monkeypatch):
    calls = []

    def fake_patch(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return _Resp(200, {"id": "abc123"})

    monkeypatch.setattr("httpx.patch", fake_patch)
    out = _default_upsert_transport(WEBHOOK, "updated", "abc123", source="test")
    assert out == "abc123"
    assert calls[0][0].endswith("/messages/abc123")
    assert calls[0][1]["embeds"][0]["title"] == "updated"


def test_default_upsert_404_recreates_message(monkeypatch):
    patches, posts = [], []

    def fake_patch(url, json=None, timeout=None):
        patches.append(url)
        return _Resp(404)

    def fake_post(url, json=None, timeout=None):
        posts.append(url)
        return _Resp(200, {"id": "replacement"})

    monkeypatch.setattr("httpx.patch", fake_patch)
    monkeypatch.setattr("httpx.post", fake_post)
    out = _default_upsert_transport(WEBHOOK, "replacement body", "stale-id", source="test")
    assert out == "replacement"
    assert patches[0].endswith("/messages/stale-id")
    assert "wait=true" in posts[0]
