"""Webull sandbox-paper configuration validation.

This module is deliberately inert: no SDK import, no network calls, no order
construction, and no strategy/risk/execution wiring. It validates that Webull
sandbox configuration is isolated from live credentials and fails closed on
missing or ambiguous safety flags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping

_FALSE_VALUES = {"0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
WEBULL_SANDBOX_HOST = "api.sandbox.webull.com"
_SECRET_ENV_NAMES = frozenset({"WEBULL_SANDBOX_APP_KEY", "WEBULL_SANDBOX_APP_SECRET"})


def _env_value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name, "") or "").strip()


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> tuple[bool, bool]:
    """Return ``(value, explicitly_valid)``; missing/unknown is invalid."""
    raw = _env_value(env, name).lower()
    if raw in _TRUE_VALUES:
        return True, True
    if raw in _FALSE_VALUES:
        return False, True
    return bool(default), False


@dataclass(frozen=True)
class WebullPaperConfig:
    sandbox_app_key_configured: bool
    sandbox_app_secret_configured: bool
    sandbox_base_url: str
    trading_mode: str
    sandbox_live_trading_enabled: bool
    sandbox_api_enabled: bool
    sandbox_paper_trading_enabled: bool
    live_api_enabled: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def secrets_configured(self) -> bool:
        return self.sandbox_app_key_configured and self.sandbox_app_secret_configured

    @property
    def paper_only_safe(self) -> bool:
        return (
            self.sandbox_base_url == WEBULL_SANDBOX_HOST
            and self.trading_mode == "paper"
            and self.sandbox_live_trading_enabled is False
            and self.sandbox_paper_trading_enabled is True
            and self.live_api_enabled is False
            and self.secrets_configured
            and not self.errors
        )

    @property
    def network_calls_allowed(self) -> bool:
        return self.sandbox_api_enabled and self.paper_only_safe

    def redacted_summary(self) -> dict[str, object]:
        return {
            "WEBULL_SANDBOX_APP_KEY": (
                "PRESENT_REDACTED" if self.sandbox_app_key_configured else "MISSING"
            ),
            "WEBULL_SANDBOX_APP_SECRET": (
                "PRESENT_REDACTED" if self.sandbox_app_secret_configured else "MISSING"
            ),
            "WEBULL_SANDBOX_BASE_URL": self.sandbox_base_url or "MISSING",
            "WEBULL_SANDBOX_TRADING_MODE": self.trading_mode or "MISSING",
            "WEBULL_SANDBOX_LIVE_TRADING_ENABLED": self.sandbox_live_trading_enabled,
            "WEBULL_SANDBOX_API_ENABLED": self.sandbox_api_enabled,
            "WEBULL_SANDBOX_PAPER_TRADING_ENABLED": self.sandbox_paper_trading_enabled,
            "WEBULL_API_LIVE_ENABLED": self.live_api_enabled,
            "paper_only_safe": self.paper_only_safe,
            "network_calls_allowed": self.network_calls_allowed,
            "errors": list(self.errors),
        }


def load_webull_paper_config(env: Mapping[str, str] | None = None) -> WebullPaperConfig:
    source = os.environ if env is None else env

    sandbox_base_url = _env_value(source, "WEBULL_SANDBOX_BASE_URL").lower().rstrip("/")
    trading_mode = _env_value(source, "WEBULL_SANDBOX_TRADING_MODE").lower()
    sandbox_live_enabled, sandbox_live_valid = _env_bool(
        source, "WEBULL_SANDBOX_LIVE_TRADING_ENABLED", False
    )
    sandbox_api_enabled, sandbox_api_valid = _env_bool(
        source, "WEBULL_SANDBOX_API_ENABLED", False
    )
    sandbox_paper_enabled, sandbox_paper_valid = _env_bool(
        source, "WEBULL_SANDBOX_PAPER_TRADING_ENABLED", False
    )
    live_api_enabled, live_api_valid = _env_bool(source, "WEBULL_API_LIVE_ENABLED", False)
    key_configured = bool(_env_value(source, "WEBULL_SANDBOX_APP_KEY"))
    secret_configured = bool(_env_value(source, "WEBULL_SANDBOX_APP_SECRET"))

    errors: list[str] = []
    if sandbox_base_url != WEBULL_SANDBOX_HOST:
        errors.append(f"WEBULL_SANDBOX_BASE_URL must equal {WEBULL_SANDBOX_HOST}")
    if trading_mode != "paper":
        errors.append("WEBULL_SANDBOX_TRADING_MODE must equal paper")
    if not sandbox_live_valid:
        errors.append("WEBULL_SANDBOX_LIVE_TRADING_ENABLED must be explicitly true or false")
    elif sandbox_live_enabled:
        errors.append("WEBULL_SANDBOX_LIVE_TRADING_ENABLED must be false")
    if not sandbox_api_valid:
        errors.append("WEBULL_SANDBOX_API_ENABLED must be explicitly true or false")
    if not sandbox_paper_valid:
        errors.append("WEBULL_SANDBOX_PAPER_TRADING_ENABLED must be explicitly true or false")
    elif not sandbox_paper_enabled:
        errors.append("WEBULL_SANDBOX_PAPER_TRADING_ENABLED must be true")
    if not live_api_valid:
        errors.append("WEBULL_API_LIVE_ENABLED must be explicitly true or false")
    elif live_api_enabled:
        errors.append("WEBULL_API_LIVE_ENABLED must be false")
    if not key_configured:
        errors.append("WEBULL_SANDBOX_APP_KEY must be configured")
    if not secret_configured:
        errors.append("WEBULL_SANDBOX_APP_SECRET must be configured")

    return WebullPaperConfig(
        sandbox_app_key_configured=key_configured,
        sandbox_app_secret_configured=secret_configured,
        sandbox_base_url=sandbox_base_url,
        trading_mode=trading_mode,
        sandbox_live_trading_enabled=sandbox_live_enabled,
        sandbox_api_enabled=sandbox_api_enabled,
        sandbox_paper_trading_enabled=sandbox_paper_enabled,
        live_api_enabled=live_api_enabled,
        errors=tuple(errors),
    )


def redacted_webull_env_names() -> tuple[str, ...]:
    return tuple(sorted(_SECRET_ENV_NAMES))
