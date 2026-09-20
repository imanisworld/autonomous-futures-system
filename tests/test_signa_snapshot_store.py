from __future__ import annotations

import inspect
from datetime import datetime, timezone

from sources.signa_snapshot_store import (
    SignaSnapshotStore,
    make_params_hash,
    make_snapshot_bucket,
    make_snapshot_id,
    normalize_symbol,
)


def test_shared_snapshot_store_dedupes_same_bucket_and_params(tmp_path):
    store = SignaSnapshotStore(tmp_path / "signa.sqlite")
    first = store.record_snapshot(
        endpoint="/api/v1/action-card",
        symbol="qqq",
        timeframe="1d",
        params={"include": ["score", "grade"], "limit": 1},
        snapshot_bucket="2026-09-20T16:00:00Z",
        retrieved_at="2026-09-20T16:04:12Z",
        data_as_of="2026-09-20T16:03:00Z",
        payload={"symbol": "QQQ", "score": 87},
    )
    second = store.record_snapshot(
        endpoint="api/v1/action-card/",
        symbol="QQQ",
        timeframe="1D",
        params={"limit": 1, "include": ["score", "grade"]},
        snapshot_bucket="2026-09-20T16:00:00Z",
        retrieved_at="2026-09-20T16:05:00Z",
        payload={"symbol": "QQQ", "score": 88},
    )

    assert second.id == first.id
    assert second.snapshot_id == first.snapshot_id
    assert second.payload == {"symbol": "QQQ", "score": 87}
    assert second.observation_only is True
    assert second.trade_authority is False


def test_shared_snapshot_store_records_new_bucket_separately(tmp_path):
    store = SignaSnapshotStore(tmp_path / "signa.sqlite")
    a = store.record_snapshot(
        endpoint="signal-index",
        symbol="SPY",
        timeframe="intraday",
        snapshot_bucket="2026-09-20T16:00:00Z",
        retrieved_at="2026-09-20T16:04:00Z",
        payload={"bias": "long"},
    )
    b = store.record_snapshot(
        endpoint="signal-index",
        symbol="SPY",
        timeframe="intraday",
        snapshot_bucket="2026-09-20T16:15:00Z",
        retrieved_at="2026-09-20T16:16:00Z",
        payload={"bias": "mixed"},
    )

    assert b.snapshot_id != a.snapshot_id
    assert [row.snapshot_id for row in store.latest(symbol="spy", endpoint="signal-index")] == [
        b.snapshot_id,
        a.snapshot_id,
    ]


def test_find_fresh_reuses_recent_snapshot_and_rejects_stale(tmp_path):
    store = SignaSnapshotStore(tmp_path / "signa.sqlite")
    store.record_snapshot(
        endpoint="market-tide",
        symbol="VIX",
        timeframe="intraday",
        params={"region": "us"},
        retrieved_at="2026-09-20T15:45:00Z",
        payload={"vol_regime": "calm"},
    )

    fresh = store.find_fresh(
        endpoint="market-tide",
        symbol="vix",
        timeframe="intraday",
        params={"region": "us"},
        now="2026-09-20T16:00:00Z",
        max_age_seconds=1800,
    )
    stale = store.find_fresh(
        endpoint="market-tide",
        symbol="vix",
        timeframe="intraday",
        params={"region": "us"},
        now="2026-09-20T16:20:01Z",
        max_age_seconds=1800,
    )

    assert fresh is not None
    assert fresh.payload["vol_regime"] == "calm"
    assert stale is None


def test_snapshot_reference_is_safe_for_consumers(tmp_path):
    store = SignaSnapshotStore(tmp_path / "signa.sqlite")
    stored = store.record_snapshot(
        endpoint="enhanced-signal",
        symbol="TLT",
        payload={"direction": "SHORT"},
        retrieved_at=datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc),
        http_status=200,
    )

    ref = stored.to_reference()

    assert ref["snapshot_id"] == stored.snapshot_id
    assert ref["symbol"] == "TLT"
    assert ref["observation_only"] is True
    assert ref["trade_authority"] is False
    assert "payload" not in ref


def test_snapshot_id_and_param_hash_are_stable():
    params_hash = make_params_hash({"b": 2, "a": 1})
    same_hash = make_params_hash({"a": 1, "b": 2})
    assert params_hash == same_hash

    snapshot_id = make_snapshot_id(
        source="Signa",
        endpoint="/Action-Card/",
        symbol="qqq",
        timeframe="1D",
        params_hash=params_hash,
        snapshot_bucket="2026-09-20T16:00:00Z",
    )
    same_snapshot_id = make_snapshot_id(
        source="signa",
        endpoint="action-card",
        symbol="QQQ",
        timeframe="1d",
        params_hash=same_hash,
        snapshot_bucket="2026-09-20T16:00:00Z",
    )

    assert snapshot_id == same_snapshot_id
    assert snapshot_id.startswith("signa_")
    assert normalize_symbol(" qqq ") == "QQQ"
    assert make_snapshot_bucket("2026-09-20T16:14:59Z") == "2026-09-20T16:00:00Z"


def test_snapshot_store_has_no_runtime_authority_imports():
    import sources.signa_snapshot_store as module

    text = inspect.getsource(module)
    forbidden = (
        "risk_rules",
        "RiskEngine",
        "Tradovate",
        "PaperBroker",
        "submit_order",
        "cancel_order",
        "webhook.app",
        "execution",
    )
    for token in forbidden:
        assert token not in text
