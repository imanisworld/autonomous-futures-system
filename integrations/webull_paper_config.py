"""Webull paper adapter Phase 0 configuration validation.

This module is deliberately inert:
- no Webull SDK import;
- no network calls;
- no order construction;
- no strategy/risk/execution wiring;
- no secret values stored on the returned config object.

It exists only to answer: "does the current environment describe a paper-only
Webull candidate, and is it safe to proceed to a later read-only probe?"
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Mapping


_FALSE_VALUES = {"", "0", "false", "no", "off", "disabled"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
_SECRET_ENV_NAMES = frozenset({"WEBULL_APP_KEY", "WEBULL_APP_SECRET"})


def _env_value(env: Mapping[str, str], name: str) -> str:
    return str(env.get(name, "") or "").strip()


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _env_value(env, name).lower()
    if raw in _TRUE_VALUES:
        return True
    if raw in _FALSE_VALUES:
        return False
    return bool(default)


@dataclass(frozen=True)
class WebullPaperConfig:
    """Secret-safe Webull paper configuration status.

    The app key and secret are intentionally represented only as booleans.
    """

    app_key_configured: bool
    app_secret_configured: bool
    trading_mode: str
    live_trading_enabled: bool
    api_enabled: bool
    paper_trading_enabled: bool
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def secrets_configured(self) -> bool:
        return self.app_key_configured and self.app_secret_configured

    @property
    def paper_only_safe(self) -> bool:
        return (
            self.trading_mode == "paper"
            and self.live_trading_enabled is False
            and self.paper_trading_enabled is True
            and not self.errors
        )

    @property
    def network_calls_allowed(self) -> bool:
        """True only when a later adapter may attempt Webull calls.

        Phase 0 expects this to be False. Later phases may require an explicit
        operator change to WEBULL_API_ENABLED=true, but only after read-only
        probe code exists and is reviewed.
        """

        return self.api_enabled and self.paper_only_safe

    def redacted_summary(self) -> dict[str, object]:
        """Return a status dict safe for logs, tests, and operator output."""

        return {
            "WEBULL_APP_KEY": "PRESENT_REDACTED" if self.app_key_configured else "MISSING",
            "WEBULL_APP_SECRET": "PRESENT_REDACTED" if self.app_secret_configured else "MISSING",
            "WEBULL_TRADING_MODE": self.trading_mode or "MISSING",
            "WEBULL_LIVE_TRADING_ENABLED": self.live_trading_enabled,
            "WEBULL_API_ENABLED": self.api_enabled,
            "WEBULL_PAPER_TRADING_ENABLED": self.paper_trading_enabled,
            "paper_only_safe": self.paper_only_safe,
            "network_calls_allowed": self.network_calls_allowed,
            "errors": list(self.errors),
        }


def load_webull_paper_config(env: Mapping[str, str] | None = None) -> WebullPaperConfig:
    """Load Webull paper configuration from a mapping or ``os.environ``.

    Secret values are never returned. This function does not import the Webull
    SDK and does not perform network I/O.
    """

    source = os.environ if env is None else env
    trading_mode = _env_value(source, "WEBULL_TRADING_MODE").lower()
    live_trading_enabled = _env_bool(source, "WEBULL_LIVE_TRADING_ENABLED", False)
    api_enabled = _env_bool(source, "WEBULL_API_ENABLED", False)
    paper_trading_enabled = _env_bool(source, "WEBULL_PAPER_TRADING_ENABLED", False)
    app_key_configured = bool(_env_value(source, "WEBULL_APP_KEY"))
    app_secret_configured = bool(_env_value(source, "WEBULL_APP_SECRET"))

    errors: list[str] = []
    if trading_mode != "paper":
        errors.append("WEBULL_TRADING_MODE must equal paper")
    if live_trading_enabled:
        errors.append("WEBULL_LIVE_TRADING_ENABLED must be false")
    if not paper_trading_enabled:
        errors.append("WEBULL_PAPER_TRADING_ENABLED must be true")
    if api_enabled and not app_key_configured:
        errors.append("WEBULL_API_ENABLED requires WEBULL_APP_KEY to be configured")
    if api_enabled and not app_secret_configured:
        errors.append("WEBULL_API_ENABLED requires WEBULL_APP_SECRET to be configured")

    return WebullPaperConfig(
        app_key_configured=app_key_configured,
        app_secret_configured=app_secret_configured,
        trading_mode=trading_mode,
        live_trading_enabled=live_trading_enabled,
        api_enabled=api_enabled,
        paper_trading_enabled=paper_trading_enabled,
        errors=tuple(errors),
    )


def redacted_webull_env_names() -> tuple[str, ...]:
    """Names whose values must never be printed."""

    return tuple(sorted(_SECRET_ENV_NAMES))
