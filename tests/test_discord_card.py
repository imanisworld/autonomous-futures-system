"""Presentation-only: plain operator text -> paper-collection-style card."""

from __future__ import annotations

import urllib.error

import pytest

from notifications.discord_card import (
    COLOR_FAIL,
    COLOR_INFO,
    COLOR_PASS,
    COLOR_WARN,
    card_payload,
    card_text,
    post_card_or_text,
    text_card,
)
from notifications.discord_router import DiscordRouter, Route


def test_title_fields_sections_and_footer():
    embed = text_card(
        "**🚨 RiskSentinel feed watchdog — INSTRUMENT FEED STALE**\n"
        "Instrument: MNQ\n"
        "Last bar: 14:05 UTC\n"
        "Feed stopped advancing during RTH.\n"
        "**Action**\n"
        "- check the TradingView alert\n"
        "- check the webhook log\n"
        "-# read-only watchdog",
        source="error route",
    )
    assert embed["title"] == "🚨 RiskSentinel feed watchdog — INSTRUMENT FEED STALE"
    assert embed["color"] == COLOR_FAIL
    assert embed["description"] == "Feed stopped advancing during RTH."
    assert embed["fields"][0] == {"name": "Instrument", "value": "MNQ", "inline": True}
    assert embed["fields"][1] == {"name": "Last bar", "value": "14:05 UTC", "inline": True}
    assert embed["fields"][2] == {
        "name": "Action",
        "value": "- check the TradingView alert\n- check the webhook log",
    }
    assert embed["footer"]["text"] == "read-only watchdog · AFS · error route"


@pytest.mark.parametrize(
    ("title", "color"),
    [
        ("Tradovate session DOWN", COLOR_FAIL),
        ("Tradovate session restored after DOWN", COLOR_PASS),
        ("⚠ Feed gap on MES", COLOR_WARN),
        ("Health digest ✅", COLOR_PASS),
        ("Weekly review 2026-W38", COLOR_INFO),
    ],
)
def test_status_color_from_title(title, color):
    assert text_card(title)["color"] == color


def test_body_words_do_not_color_but_body_emoji_do():
    assert text_card("Daily summary\nErrors: 0")["color"] == COLOR_INFO
    assert text_card("Daily summary\n⚠ one collector stale")["color"] == COLOR_WARN


def test_code_block_and_urls_are_kept_verbatim():
    embed = text_card("Report\nLink: https://example.test/a:b\n```\nkey: value\n```")
    assert embed["fields"][0]["value"] == "https://example.test/a:b"
    assert embed["description"] == "```\nkey: value\n```"


def test_card_stays_inside_discord_limits():
    text = "Huge\n" + "\n".join(f"Key {i}: " + "x" * 900 for i in range(40))
    embed = text_card(text)
    size = len(embed["title"]) + len(embed.get("description", "")) + len(embed["footer"]["text"]) + sum(
        len(f["name"]) + len(f["value"]) for f in embed["fields"]
    )
    assert size <= 6000 and len(embed["fields"]) <= 25


def test_dict_bodies_pass_through_and_mentions_are_off():
    body = {"embeds": [{"title": "already a card"}]}
    assert card_payload(body) is body
    assert card_payload("hello")["allowed_mentions"] == {"parse": []}


def test_card_text_round_trips_the_alert_words():
    text = "Title line\nStatus: OK\nfree text"
    flat = card_text(card_payload(text))
    for word in ("Title line", "Status: OK", "free text"):
        assert word in flat


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://discord.test", code, "x", {}, None)


def test_400_falls_back_to_the_original_text():
    sent = []

    def post(body):
        sent.append(body)
        if "embeds" in body:
            raise _http_error(400)
        return 204

    assert post_card_or_text(post, "Alert\nKey: v") == 204
    assert sent[-1] == {"content": "Alert\nKey: v", "allowed_mentions": {"parse": []}}


@pytest.mark.parametrize("exc", [TimeoutError("slow"), _http_error(500), _http_error(429)])
def test_no_text_resend_when_the_card_may_have_landed(exc):
    sent = []

    def post(body):
        sent.append(body)
        raise exc

    with pytest.raises(type(exc)):
        post_card_or_text(post, "Alert")
    assert len(sent) == 1


def test_router_default_transport_sends_card_with_route_footer(monkeypatch):
    import httpx

    captured = []

    class _Resp:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "post", lambda url, json, timeout: captured.append(json) or _Resp())
    router = DiscordRouter(
        routes={"error": Route(name="error", env_var="E", required=True)},
        env={"E": "https://discord.test/hook"},
    )
    assert router.send("error", "Feed DOWN\nInstrument: MNQ") is True
    embed = captured[0]["embeds"][0]
    assert embed["title"] == "Feed DOWN" and embed["color"] == COLOR_FAIL
    assert embed["footer"]["text"] == "AFS · error route"

    card = {"embeds": [{"title": "Futures · Paper decision"}]}
    assert router.send("error", card) is True
    assert captured[1] is card


def test_long_dot_title_splits_and_boundary_line_moves_to_footer():
    embed = text_card(
        "**🫀 heartbeat · asian session · last bar 5m ago · flat · 2 trade(s) today · P&L $55.00**\n"
        "\n[read-only · no rule change]"
    )
    assert embed["title"] == "🫀 heartbeat"
    assert embed["description"] == "asian session · last bar 5m ago · flat · 2 trade(s) today · P&L $55.00"
    assert embed["footer"]["text"] == "read-only · no rule change"
