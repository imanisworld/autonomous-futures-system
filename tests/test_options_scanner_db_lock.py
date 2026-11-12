"""Options scanner SQLite lock hardening.

The control case shows a bare ``sqlite3.connect`` raising ``database is locked``
when another connection holds the write lock. ScanStorage waits that out and
retries lock errors; one bad candidate does not abort ``resolve_open_candidates``.
"""
from __future__ import annotations

import asyncio
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

from alert_ranker.contract_marks import record_contract_mark
from alert_ranker.scorer import ScoreResult
from alert_ranker.storage import ScanStorage
from tests.test_alert_ranker_lifecycle import OPEN_TIME, FakeMarketData, make_scanner


def _score(ticker: str = "SPY") -> ScoreResult:
    return ScoreResult(ticker=ticker, direction="LONG", score=3, pattern="lock-test")


def _scan(storage: ScanStorage, ticker: str = "SPY") -> int:
    return storage.record_scan(
        _score(ticker),
        source="test",
        alert_sent=False,
        alert_suppression_reason="",
    )


def test_bare_sqlite_connect_raises_when_the_write_lock_is_held(tmp_path):
    """Pre-fix failure mode: connect waits only for its timeout, then raises.

    ScanStorage previously called ``sqlite3.connect(path)`` with the library
    default timeout. A short timeout here is the same error, without sleeping
    for the full default 5 seconds.
    """
    path = tmp_path / "bare.sqlite"
    holder = sqlite3.connect(path)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        other = sqlite3.connect(path, timeout=0.05)
        with pytest.raises(sqlite3.OperationalError, match="locked|busy"):
            other.execute("CREATE TABLE locked_probe (id INTEGER)")
        other.close()
    finally:
        holder.close()


def test_scan_storage_uses_wal_busy_timeout_and_waits_out_a_brief_lock(tmp_path):
    path = tmp_path / "scanner.sqlite"
    storage = ScanStorage(path)
    probe = sqlite3.connect(path)
    try:
        mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        probe.close()
    assert str(mode).lower() == "wal"
    with storage._connect() as conn:
        busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert int(busy) == 30000

    started = threading.Event()
    release = threading.Event()

    def _hold():
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("BEGIN IMMEDIATE")
        started.set()
        release.wait(2)
        conn.commit()
        conn.close()

    thread = threading.Thread(target=_hold)
    thread.start()
    assert started.wait(2)
    threading.Timer(0.2, release.set).start()
    scan_id = _scan(storage)
    release.set()
    thread.join(2)
    assert scan_id > 0


def test_concurrent_writers_complete(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    scan_id = _scan(storage, "SEED")
    shadow_id = storage.record_shadow_setup(
        _score("SEED"),
        scan_id=scan_id,
        setup_inputs={},
        provider_snapshot={},
        selected_contract={},
    )
    errors: list[BaseException] = []

    def _scans():
        try:
            for i in range(25):
                _scan(storage, f"T{i % 5}")
        except BaseException as exc:  # noqa: BLE001 — the test records it
            errors.append(exc)

    def _marks():
        try:
            for i in range(25):
                record_contract_mark(
                    storage,
                    shadow_id=shadow_id,
                    option_symbol="SPY",
                    timestamp=datetime(2026, 11, 6, 12, 0, i, tzinfo=timezone.utc),
                    mid=1.0 + i,
                )
        except BaseException as exc:  # noqa: BLE001 — the test records it
            errors.append(exc)

    threads = [threading.Thread(target=_scans), threading.Thread(target=_marks)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert errors == []
    assert all(not thread.is_alive() for thread in threads)


def test_write_retries_lock_errors_and_not_other_operational_errors(tmp_path, monkeypatch):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    calls = {"n": 0}
    real_connect = storage._connect

    @contextmanager
    def locked_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        with real_connect() as conn:
            yield conn

    monkeypatch.setattr(storage, "_connect", locked_once)
    assert _scan(storage) > 0
    assert calls["n"] == 2

    calls["n"] = 0

    @contextmanager
    def not_a_lock():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: missing")
        yield  # pragma: no cover

    monkeypatch.setattr(storage, "_connect", not_a_lock)
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        storage.write("probe", lambda conn: conn.execute("SELECT 1"))
    assert calls["n"] == 1


def test_resolve_open_candidates_isolates_one_locked_candidate(tmp_path, monkeypatch):
    scanner, storage = make_scanner(tmp_path, FakeMarketData(price=110.0))
    ids = []
    for ticker in ("AAA", "BBB"):
        result = _score(ticker)
        scan_id = storage.record_scan(
            result, source="test", alert_sent=False, alert_suppression_reason=""
        )
        ids.append(
            storage.record_shadow_setup(
                result,
                scan_id=scan_id,
                setup_inputs={"price": 100.0},
                provider_snapshot={},
                selected_contract={"contract_key": ticker},
                status="OPEN",
            )
        )

    def _resolve(**_kwargs):
        return ("WIN", {"pnl_dollars": 1.0, "exit_mark": 2.0, "entry_mark": 1.0})

    monkeypatch.setattr("alert_ranker.scanner_legacy.resolve_open_setup", _resolve)
    real_update = storage.update_shadow_outcome

    def _update(shadow_id, **kwargs):
        if shadow_id == ids[0]:
            raise sqlite3.OperationalError("database is locked")
        return real_update(shadow_id, **kwargs)

    monkeypatch.setattr(storage, "update_shadow_outcome", _update)
    counts = asyncio.run(scanner.resolve_open_candidates(now=OPEN_TIME, scheduled=False))
    assert counts["checked"] == 2
    assert counts["errors"] == 1
    assert counts["resolved"] == 1
    assert storage.get_shadow_setup(ids[0]).status == "OPEN"
    assert storage.get_shadow_setup(ids[1]).status == "WIN"
