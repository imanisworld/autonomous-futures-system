"""
tests/test_health_digest_auth.py

The health digest is an EXTERNAL cron watchdog: it must be able to read the
gated status endpoints (401 since the site-access gate landed) and find its
Discord route without the service's EnvironmentFile. Guards the 2026-07-03..06
silent failure (cron ran without .env → "no webhook configured" + 401 broker
reads → red digest that never reached Discord) and its 07-10 recurrence (a
release-relative .env alone doesn't survive an atomic release swap that leaves
the fresh release folder without one).
"""
from __future__ import annotations

import urllib.request

from scripts.health_digest import _gate_cookie, _get_json, _load_env


def test_gate_cookie_matches_the_apps_token(monkeypatch):
    """Parity with webhook.app._gate_token — the digest duplicates the HMAC on
    purpose (importing the app would couple the watchdog to the thing it
    watches), so drift in the derivation must fail HERE, loudly."""
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("SITE_ACCESS_CODE", "open-sesame")
    from webhook.app import _gate_token
    assert _gate_cookie() == f"vp_access={_gate_token()}"


def test_gate_cookie_absent_when_gate_unconfigured(monkeypatch):
    monkeypatch.delenv("SITE_ACCESS_CODE", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    assert _gate_cookie() is None
    monkeypatch.setenv("SITE_ACCESS_CODE", "code")
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    assert _gate_cookie() is None


def test_get_json_sends_cookie_when_gate_configured(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("SITE_ACCESS_CODE", "open-sesame")
    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["cookie"] = req.get_header("Cookie")
        raise OSError("stop here — header captured")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert _get_json("/status/broker-account") is None  # fail-soft preserved
    assert seen["cookie"] == _gate_cookie()


def test_get_json_sends_no_cookie_when_unconfigured(monkeypatch):
    monkeypatch.delenv("SITE_ACCESS_CODE", raising=False)
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["cookie"] = req.get_header("Cookie")
        raise OSError("stop")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert _get_json("/health") is None
    assert seen["cookie"] is None


def test_post_discord_sends_real_user_agent(monkeypatch):
    """Discord's edge 403s urllib's default Python-urllib UA (box-verified
    2026-07-06: same webhook, curl 204 / bare urllib 403) — the post must carry
    an explicit User-Agent or delivery silently fails."""
    from scripts.health_digest import _post_discord
    seen = {}

    def _fake_urlopen(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        raise OSError("stop — header captured")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    assert _post_discord("https://discord.test/hook", "hi") is False  # fail-soft
    assert seen["ua"] == "afs-health-digest/1.0"


def test_load_env_reads_repo_dotenv_without_overriding(tmp_path, monkeypatch):
    # conftest disables dotenv globally (PYTHON_DOTENV_DISABLED=1) so tests never
    # ingest the developer's real .env; re-enable it against this tmp .env only.
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "DISCORD_ROUTE_HEARTBEAT=https://discord.test/hook\n"
        "ALREADY_SET=from-file\n"
    )
    monkeypatch.delenv("DISCORD_ROUTE_HEARTBEAT", raising=False)
    monkeypatch.setenv("ALREADY_SET", "from-env")
    _load_env()
    import os
    assert os.getenv("DISCORD_ROUTE_HEARTBEAT") == "https://discord.test/hook"
    assert os.getenv("ALREADY_SET") == "from-env"  # env wins over file


def test_load_env_falls_back_to_shared_dir_when_release_folder_has_no_dotenv(
    tmp_path, monkeypatch
):
    """Guards the 07-10 recurrence: an atomic release swap can land the cron's
    cwd in a fresh release folder with no ``.env`` of its own — the relative
    load alone silently no-ops in that case (confirmed live: cwd resolved to
    the current release folder, no .env present, DISCORD_WEBHOOK_URL invisible
    to the cron run). AFS_SHARED_DIR is the one location an atomic release
    swap never touches, so it must be checked too."""
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    monkeypatch.chdir(release_dir)  # no .env here — simulates a fresh release
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / ".env").write_text("DISCORD_WEBHOOK_URL=https://discord.test/shared-hook\n")
    monkeypatch.setenv("AFS_SHARED_DIR", str(shared_dir))
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    _load_env()
    import os
    assert os.getenv("DISCORD_WEBHOOK_URL") == "https://discord.test/shared-hook"


def test_load_env_relative_dotenv_takes_precedence_over_shared(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    (release_dir / ".env").write_text("DISCORD_WEBHOOK_URL=https://discord.test/release-hook\n")
    monkeypatch.chdir(release_dir)
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()
    (shared_dir / ".env").write_text("DISCORD_WEBHOOK_URL=https://discord.test/shared-hook\n")
    monkeypatch.setenv("AFS_SHARED_DIR", str(shared_dir))
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    _load_env()
    import os
    assert os.getenv("DISCORD_WEBHOOK_URL") == "https://discord.test/release-hook"
