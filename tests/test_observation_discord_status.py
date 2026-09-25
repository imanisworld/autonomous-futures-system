from __future__ import annotations

import json

from notifications import observation_notifier as obs
from notifications import observation_status as status
from notifications.discord_router import DiscordRouter, Route

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
