"""Tests for the provider-neutral external dead-man heartbeat."""
from __future__ import annotations

import importlib.util
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).parent.parent
MODULE_PATH = ROOT / "ops" / "external_heartbeat.py"


def _load():
    spec = importlib.util.spec_from_file_location("afs_external_heartbeat_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


hb = _load()


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def getcode(self):
        return self.status


def _write_tick(path: Path, *, when: datetime, verdict: str = "OK") -> None:
    path.write_text(
        __import__("json").dumps({"utc": when.isoformat(), "verdict": verdict}),
        encoding="utf-8",
    )


def test_disabled_without_url_does_nothing(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv(hb.URL_ENV, raising=False)
    called = []
    monkeypatch.setattr(hb, "send_heartbeat", lambda *_a, **_k: called.append(True))

    rc = hb.main(["--watcher-state", str(tmp_path / "missing.json")])

    assert rc == 0
    assert called == []
    assert "disabled" in capsys.readouterr().out


def test_fresh_tick_pings_even_when_watcher_verdict_is_blocked(monkeypatch, tmp_path):
    state = tmp_path / "latest_tick.json"
    _write_tick(state, when=datetime.now(timezone.utc), verdict="BLOCKED")
    monkeypatch.setenv(hb.URL_ENV, "https://heartbeat.example.test/opaque-token")
    sent = []
    monkeypatch.setattr(
        hb,
        "send_heartbeat",
        lambda url, *, timeout_seconds: sent.append((url, timeout_seconds)),
    )

    rc = hb.main(["--watcher-state", str(state)])

    assert rc == 0
    assert sent == [("https://heartbeat.example.test/opaque-token", hb.DEFAULT_TIMEOUT_SECONDS)]


def test_stale_tick_suppresses_ping(monkeypatch, tmp_path):
    state = tmp_path / "latest_tick.json"
    _write_tick(state, when=datetime.now(timezone.utc) - timedelta(hours=1))
    monkeypatch.setenv(hb.URL_ENV, "https://heartbeat.example.test/opaque-token")
    sent = []
    monkeypatch.setattr(hb, "send_heartbeat", lambda *_a, **_k: sent.append(True))

    rc = hb.main(["--watcher-state", str(state)])

    assert rc == 3
    assert sent == []


def test_missing_or_malformed_watcher_state_is_not_a_success(tmp_path):
    missing = tmp_path / "missing.json"
    assert hb.watcher_is_fresh(missing)[0] is False

    bad = tmp_path / "bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert hb.watcher_is_fresh(bad)[0] is False


def test_http_url_is_rejected(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv(hb.URL_ENV, "http://heartbeat.example.test/token")

    rc = hb.main(["--watcher-state", str(tmp_path / "unused.json")])

    assert rc == 2
    assert "HTTPS" in capsys.readouterr().err


def test_request_failure_never_logs_configured_url(monkeypatch, tmp_path, capsys):
    state = tmp_path / "latest_tick.json"
    _write_tick(state, when=datetime.now(timezone.utc))
    secret_url = "https://heartbeat.example.test/secret-token"
    monkeypatch.setenv(hb.URL_ENV, secret_url)

    def fail(_url, *, timeout_seconds):
        raise RuntimeError(f"provider failed for {secret_url} after {timeout_seconds}")

    monkeypatch.setattr(hb, "send_heartbeat", fail)

    rc = hb.main(["--watcher-state", str(state)])
    captured = capsys.readouterr()

    assert rc == 4
    assert secret_url not in captured.out
    assert secret_url not in captured.err
    assert "RuntimeError" in captured.err


def test_dry_run_validates_freshness_without_network(monkeypatch, tmp_path):
    state = tmp_path / "latest_tick.json"
    _write_tick(state, when=datetime.now(timezone.utc))
    monkeypatch.setenv(hb.URL_ENV, "https://heartbeat.example.test/token")

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("network should not be called in dry-run")

    monkeypatch.setattr(hb, "send_heartbeat", should_not_run)

    assert hb.main(["--watcher-state", str(state), "--dry-run"]) == 0


def test_systemd_units_are_opt_in_and_do_not_control_runtime():
    service = (ROOT / "deploy" / "systemd" / "afs-external-heartbeat.service").read_text()
    timer = (ROOT / "deploy" / "systemd" / "afs-external-heartbeat.timer").read_text()

    assert "EnvironmentFile=-/root/afs-shared/.env" in service
    assert "ops/external_heartbeat.py" in service
    assert "NoNewPrivileges=true" in service
    assert "ProtectSystem=strict" in service
    assert "PrivateTmp=false" in service
    assert "OnUnitActiveSec=5min" in timer
    assert "OnBootSec=8min" in timer

    combined = service + "\n" + timer
    for forbidden in (
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
        "systemctl kill",
        "ExecStartPost",
        "ExecStop",
        "/order/",
    ):
        assert forbidden not in combined
