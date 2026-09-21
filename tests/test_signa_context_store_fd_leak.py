"""SignaContextStore must close every SQLite connection it opens.

2026-09-21: the store returned bare connections; ``with conn:`` commits but
does not close, so each ``context_for_tickers`` call on a dashboard poll
leaked one descriptor per ticker until the scanner process hit its ulimit
and every scan failed with ``unable to open database file``.
"""
from __future__ import annotations

import os
import sys

import pytest

from alert_ranker.signa_context_store import SignaContextStore


def _open_fds() -> int:
    if sys.platform.startswith("linux"):
        return len(os.listdir("/proc/self/fd"))
    if sys.platform == "darwin":
        return len(os.listdir("/dev/fd"))
    pytest.skip("fd counting unsupported on this platform")


def test_context_for_tickers_does_not_leak_descriptors(tmp_path):
    store = SignaContextStore(tmp_path / "scanner.sqlite")
    for symbol in ("AAPL", "NVDA", "SPY", "QQQ"):
        store.record({"ticker": symbol, "source": "signa_v2", "endpoint": "e", "status": "ok",
                      "direction": "bullish", "payload": {"request_ok": True}})
    # warm up (schema/migration), then measure a steady-state batch
    store.context_for_tickers(["AAPL", "NVDA", "SPY", "QQQ"])
    before = _open_fds()
    for _ in range(50):
        store.context_for_tickers(["AAPL", "NVDA", "SPY", "QQQ"])
        store.latest(limit=5)
    after = _open_fds()
    assert after - before <= 1, f"leaked {after - before} descriptors over 50 iterations"
