from __future__ import annotations

import uvicorn
import pytest

from options_manager.app import _assert_safe_bind, run


def test_loopback_bind_is_allowed_without_secret():
    for host in ("127.0.0.1", "::1", "localhost"):
        _assert_safe_bind(host, "")


def test_non_loopback_ip_without_secret_fails_closed():
    for host in ("0.0.0.0", "192.168.1.50"):
        with pytest.raises(RuntimeError, match="INGEST_SECRET"):
            _assert_safe_bind(host, "")


def test_non_loopback_hostname_without_secret_fails_closed():
    with pytest.raises(RuntimeError, match="INGEST_SECRET"):
        _assert_safe_bind("options.internal.example", "")


def test_non_loopback_bind_with_secret_is_allowed():
    _assert_safe_bind("0.0.0.0", "configured")
    _assert_safe_bind("options.internal.example", "configured")


def test_run_defaults_to_loopback(monkeypatch):
    calls = {}

    def _fake_uvicorn_run(app, *, host, port):
        calls.update(app=app, host=host, port=port)

    monkeypatch.delenv("OPTIONS_MANAGER_BIND_HOST", raising=False)
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)
    monkeypatch.setattr(uvicorn, "run", _fake_uvicorn_run)

    run()

    assert calls["app"] == "options_manager.app:app"
    assert calls["host"] == "127.0.0.1"


def test_run_refuses_public_bind_without_secret(monkeypatch):
    monkeypatch.setenv("OPTIONS_MANAGER_BIND_HOST", "0.0.0.0")
    monkeypatch.delenv("OPTIONS_MANAGER_INGEST_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="INGEST_SECRET"):
        run()


def test_run_allows_explicit_public_bind_with_secret(monkeypatch):
    calls = {}

    def _fake_uvicorn_run(app, *, host, port):
        calls.update(app=app, host=host, port=port)

    monkeypatch.setenv("OPTIONS_MANAGER_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("OPTIONS_MANAGER_INGEST_SECRET", "configured")
    monkeypatch.setattr(uvicorn, "run", _fake_uvicorn_run)

    run()

    assert calls["host"] == "0.0.0.0"
