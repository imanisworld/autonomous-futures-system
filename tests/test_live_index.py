"""Tests for the live index quote source (display price for Discord alerts)."""

import quotes.live_index as live_index


def test_index_symbol_mapping():
    assert live_index.index_symbol_for("MES") == "ES=F"
    assert live_index.index_symbol_for("MNQ") == "NQ=F"
    assert live_index.index_symbol_for("MNQ1!") == "NQ=F"
    assert live_index.index_symbol_for("MESM6") == "ES=F"   # front-month suffix
    assert live_index.index_symbol_for("BTCUSD") is None


def test_get_live_quote_uses_fetch_and_caches(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return 25180.75

    monkeypatch.setattr(live_index, "_fetch_price", fake_fetch)
    live_index._cache.clear()

    q1 = live_index.get_live_quote("MNQ")
    q2 = live_index.get_live_quote("MNQ")  # within TTL → cached, no second fetch

    assert q1 == {"price": 25180.75, "symbol": "NQ=F", "source": "yahoo:NQ=F"}
    assert q2 == q1
    assert calls == ["NQ=F"]


def test_get_live_quote_fails_soft(monkeypatch):
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: None)
    live_index._cache.clear()

    assert live_index.get_live_quote("MES") is None
    assert live_index.get_live_quote("DOGEUSD") is None  # unmapped


def test_get_live_quote_serves_stale_on_upstream_failure(monkeypatch):
    live_index._cache.clear()
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: 6010.25)
    first = live_index.get_live_quote("MES")
    assert first["price"] == 6010.25

    # Upstream now fails; expire the cache age but keep the value available.
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: None)
    monkeypatch.setattr(live_index, "_CACHE_TTL_SECONDS", -1.0)
    stale = live_index.get_live_quote("MES")
    assert stale["price"] == 6010.25  # served stale rather than None
