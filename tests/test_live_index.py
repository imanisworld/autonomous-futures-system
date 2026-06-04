"""Tests for the display-only reference quote source (ES=F/NQ=F HTTP proxy)."""

import quotes.live_index as live_index


def test_index_symbol_mapping():
    assert live_index.index_symbol_for("MES") == "ES=F"
    assert live_index.index_symbol_for("MNQ") == "NQ=F"
    assert live_index.index_symbol_for("MNQ1!") == "NQ=F"
    assert live_index.index_symbol_for("MESM6") == "ES=F"   # front-month suffix
    assert live_index.index_symbol_for("BTCUSD") is None


def test_unmapped_instrument_returns_none(monkeypatch):
    live_index._cache.clear()
    assert live_index.get_live_quote("DOGEUSD") is None


def test_fresh_quote_has_status_source_and_age(monkeypatch):
    calls = []

    def fake_fetch(symbol):
        calls.append(symbol)
        return 25180.75

    monkeypatch.setattr(live_index, "_fetch_price", fake_fetch)
    live_index._cache.clear()

    q1 = live_index.get_live_quote("MNQ")
    q2 = live_index.get_live_quote("MNQ")  # within TTL → cached, no second fetch

    assert q1["price"] == 25180.75
    assert q1["symbol"] == "NQ=F"
    assert q1["source"] == "ES=F/NQ=F HTTP proxy"
    assert q1["status"] == "FRESH"
    assert q1["kind"] == "reference"
    assert isinstance(q1["age_seconds"], int)
    assert q2["status"] == "FRESH"
    assert calls == ["NQ=F"]   # second call served from cache


def test_upstream_failure_with_no_cache_is_unavailable(monkeypatch):
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: None)
    live_index._cache.clear()

    q = live_index.get_live_quote("MES")
    assert q["status"] == "UNAVAILABLE"
    assert q["price"] is None
    assert q["symbol"] == "ES=F"


def test_upstream_failure_serves_cached_value_as_stale(monkeypatch):
    live_index._cache.clear()
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: 6010.25)
    first = live_index.get_live_quote("MES")
    assert first["status"] == "FRESH"
    assert first["price"] == 6010.25

    # Cache ages past TTL; upstream now fails → serve stale value, not None.
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: None)
    monkeypatch.setattr(live_index, "_CACHE_TTL_SECONDS", -1.0)
    stale = live_index.get_live_quote("MES")
    assert stale["status"] == "STALE"
    assert stale["price"] == 6010.25


def test_reference_feed_is_not_used_by_trading_logic():
    """Hard guarantee: the reference price is display-only. No execution, risk,
    signal, or decision module may import it — only display surfaces (the webhook
    app's post-decision attach + the Discord notifier)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    trading_dirs = ["execution", "strategy", "agent", "context"]
    trading_files = [
        root / "webhook" / "runner.py",
        root / "webhook" / "state_builder.py",
    ]
    for d in trading_dirs:
        trading_files.extend((root / d).rglob("*.py"))

    offenders = [
        str(f.relative_to(root))
        for f in trading_files
        if f.exists() and "live_index" in f.read_text()
    ]
    assert offenders == [], f"reference feed leaked into trading logic: {offenders}"


def test_cached_value_too_old_becomes_unavailable(monkeypatch):
    live_index._cache.clear()
    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: 6010.25)
    live_index.get_live_quote("MES")

    monkeypatch.setattr(live_index, "_fetch_price", lambda symbol: None)
    monkeypatch.setattr(live_index, "_CACHE_TTL_SECONDS", -1.0)
    monkeypatch.setattr(live_index, "_STALE_MAX_SECONDS", -1.0)  # everything too old
    q = live_index.get_live_quote("MES")
    assert q["status"] == "UNAVAILABLE"
    assert q["price"] is None
