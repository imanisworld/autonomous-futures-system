"""
tests/test_discord_router.py

Coverage for notifications/discord_router.py — ported from the live box
(2026-06-30 reconciliation) where it had no dedicated test file either.
"""

from __future__ import annotations

import pytest

from notifications.discord_router import DiscordRouter, Route, RouteConfigError


def _routes():
    return {
        "heartbeat": Route(name="heartbeat", env_var="DISCORD_ROUTE_HEARTBEAT", required=True),
        "error": Route(name="error", env_var="DISCORD_ROUTE_ERROR", required=True),
        "daily_report": Route(name="daily_report", env_var="DISCORD_ROUTE_DAILY_REPORT", required=False),
    }


class TestIsEnabled:
    def test_true_when_env_var_set(self):
        router = DiscordRouter(routes=_routes(), env={"DISCORD_ROUTE_DAILY_REPORT": "https://x.invalid"})
        assert router.is_enabled("daily_report") is True

    def test_false_when_env_var_unset(self):
        router = DiscordRouter(routes=_routes(), env={})
        assert router.is_enabled("daily_report") is False

    def test_false_for_unknown_route(self):
        router = DiscordRouter(routes=_routes(), env={})
        assert router.is_enabled("nonexistent") is False


class TestMissingRequiredRoutes:
    def test_lists_unconfigured_required_routes(self):
        router = DiscordRouter(routes=_routes(), env={"DISCORD_ROUTE_HEARTBEAT": "https://x.invalid"})
        assert router.missing_required_routes() == ["error"]

    def test_check_startup_raises_when_required_missing(self):
        router = DiscordRouter(routes=_routes(), env={})
        with pytest.raises(RouteConfigError):
            router.check_startup()

    def test_check_startup_ok_when_required_present(self):
        router = DiscordRouter(
            routes=_routes(),
            env={"DISCORD_ROUTE_HEARTBEAT": "https://x.invalid", "DISCORD_ROUTE_ERROR": "https://y.invalid"},
        )
        router.check_startup()  # must not raise


class TestSend:
    def test_unknown_route_raises_value_error(self):
        router = DiscordRouter(routes=_routes(), env={})
        with pytest.raises(ValueError):
            router.send("nonexistent", "message")

    def test_disabled_optional_route_returns_false_without_raising(self):
        router = DiscordRouter(routes=_routes(), env={})
        assert router.send("daily_report", "message") is False

    def test_required_route_missing_returns_false_without_raising(self):
        router = DiscordRouter(routes=_routes(), env={})
        assert router.send("error", "message") is False

    def test_successful_delivery_calls_transport_once(self):
        calls = []
        router = DiscordRouter(
            routes=_routes(),
            env={"DISCORD_ROUTE_ERROR": "https://x.invalid"},
            transport=lambda url, msg: calls.append((url, msg)),
        )
        assert router.send("error", "hello") is True
        assert calls == [("https://x.invalid", "hello")]

    def test_retries_once_then_succeeds(self):
        attempts = []

        def flaky(url, msg):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("transient")

        router = DiscordRouter(
            routes=_routes(), env={"DISCORD_ROUTE_ERROR": "https://x.invalid"}, transport=flaky
        )
        assert router.send("error", "hello") is True
        assert len(attempts) == 2

    def test_fails_after_retry_exhausted_without_raising(self):
        def always_fails(url, msg):
            raise RuntimeError("down")

        router = DiscordRouter(
            routes=_routes(), env={"DISCORD_ROUTE_ERROR": "https://x.invalid"}, transport=always_fails
        )
        assert router.send("error", "hello") is False


class TestLoadRoutes:
    def test_missing_file_raises_route_config_error(self):
        with pytest.raises(RouteConfigError):
            from notifications.discord_router import load_routes

            load_routes("/nonexistent/path/notification_routes.yaml")

    def test_loads_real_config_file(self):
        from notifications.discord_router import load_routes

        routes = load_routes()
        for name in ("heartbeat", "signal", "error"):
            assert name in routes
            assert routes[name].required is True
        for name in ("signa", "daily_report", "deployment"):
            assert name in routes
            assert routes[name].required is False
