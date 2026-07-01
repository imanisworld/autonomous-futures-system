"""
notifications/discord_router.py

Config-driven Discord notification router.

This is the ONLY module that should send directly to Discord. Every other
module asks the router to deliver a message to a *logical route name*
(heartbeat, signal, signa, error, daily_report, deployment). The router maps
that route name to an environment variable (via config/notification_routes.yaml)
and reads the real webhook URL from the environment at send time.

Design guarantees:
  - Real webhook URLs live ONLY in environment variables, never in code or YAML.
  - Logical route names are stable; destinations can change without code edits.
  - send() never raises for a delivery problem — Discord trouble must never
    crash the webhook/decision loop. It raises only for a programming error
    (an unknown route name).
  - One retry maximum on a send failure, then local log and return False.
  - A failed delivery is NEVER re-notified through Discord (no notification
    loops during a Discord outage).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

logger = logging.getLogger(__name__)

# transport(url, message) -> None ; must raise on failure.
Transport = Callable[[str, str], None]

_DEFAULT_ROUTES_PATH = Path(__file__).resolve().parent.parent / "config" / "notification_routes.yaml"

# Remember which optional routes we've already warned about so a disabled
# optional route does not spam the log on every send.
_warned_disabled: set[str] = set()


@dataclass(frozen=True)
class Route:
    name: str
    env_var: str
    required: bool


class RouteConfigError(RuntimeError):
    """Raised when notification_routes.yaml is missing or malformed."""


def load_routes(path: str | os.PathLike[str] | None = None) -> dict[str, Route]:
    """Parse notification_routes.yaml at runtime into a {name: Route} map."""
    routes_path = Path(path) if path else _DEFAULT_ROUTES_PATH
    if not routes_path.exists():
        raise RouteConfigError(f"notification_routes.yaml not found at: {routes_path}")
    with open(routes_path, encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    routes_section = raw.get("routes")
    if not isinstance(routes_section, dict) or not routes_section:
        raise RouteConfigError("notification_routes.yaml must define a non-empty 'routes' mapping.")
    routes: dict[str, Route] = {}
    for name, spec in routes_section.items():
        if not isinstance(spec, dict):
            raise RouteConfigError(f"Route '{name}' must be a mapping with env_var/required.")
        env_var = str(spec.get("env_var", "")).strip()
        if not env_var:
            raise RouteConfigError(f"Route '{name}' is missing 'env_var'.")
        routes[str(name)] = Route(
            name=str(name),
            env_var=env_var,
            required=bool(spec.get("required", False)),
        )
    return routes


def _default_transport(url: str, message: str) -> None:
    import httpx

    response = httpx.post(url, json={"content": message}, timeout=5)
    response.raise_for_status()


class DiscordRouter:
    """Resolves logical route names to env-configured URLs and delivers messages."""

    def __init__(
        self,
        routes: Optional[Mapping[str, Route]] = None,
        routes_path: str | os.PathLike[str] | None = None,
        transport: Optional[Transport] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.routes: dict[str, Route] = dict(routes) if routes is not None else load_routes(routes_path)
        self._transport = transport or _default_transport
        self._env = env if env is not None else os.environ

    # ── Introspection (safe metadata only — never the URL) ───────────────────
    def route_names(self) -> list[str]:
        return list(self.routes.keys())

    def is_enabled(self, route_name: str) -> bool:
        route = self.routes.get(route_name)
        if route is None:
            return False
        return bool(str(self._env.get(route.env_var, "")).strip())

    def configured_route_names(self) -> list[str]:
        """Route names whose env var is set — for /status and startup logs.

        Returns only the logical names, never the underlying URLs.
        """
        return [name for name in self.routes if self.is_enabled(name)]

    def missing_required_routes(self) -> list[str]:
        return [
            route.name
            for route in self.routes.values()
            if route.required and not str(self._env.get(route.env_var, "")).strip()
        ]

    def check_startup(self) -> None:
        """Raise if any REQUIRED route is unconfigured. Call from startup self-check."""
        missing = self.missing_required_routes()
        if missing:
            raise RouteConfigError(
                "Required Discord route(s) not configured: "
                + ", ".join(f"{name} ({self.routes[name].env_var})" for name in missing)
            )

    # ── Delivery ─────────────────────────────────────────────────────────────
    def send(self, route_name: str, message: str, metadata: Optional[dict] = None) -> bool:
        """Deliver a message to a logical route.

        Returns:
            True  — message delivered.
            False — route optional and disabled/skipped, OR delivery failed after
                    one retry, OR a required route is unconfigured at send time.
        Raises:
            ValueError — unknown route name (programming error, surfaced before sending).
        """
        route = self.routes.get(route_name)
        if route is None:
            raise ValueError(
                f"Unknown notification route '{route_name}'. "
                f"Known routes: {', '.join(self.routes)}"
            )

        url = str(self._env.get(route.env_var, "")).strip()
        if not url:
            # A missing REQUIRED route is a configuration problem that the startup
            # self-check is responsible for catching. At send time we must never
            # crash the webhook loop, so we log and return False.
            if route.required:
                logger.error(
                    "Discord route '%s' is required but %s is not set; message dropped.",
                    route.name, route.env_var,
                )
            elif route.name not in _warned_disabled:
                _warned_disabled.add(route.name)
                logger.warning(
                    "Discord route '%s' is optional and disabled (%s unset); skipping.",
                    route.name, route.env_var,
                )
            return False

        # One send attempt + at most one retry, then give up locally. We never
        # report a Discord failure back through Discord — that would loop during
        # an outage.
        for attempt in (1, 2):
            try:
                self._transport(url, message)
                return True
            except Exception as exc:  # noqa: BLE001 - delivery must never propagate
                if attempt == 1:
                    logger.warning("Discord route '%s' send attempt 1 failed: %s; retrying once.", route.name, exc)
                    continue
                logger.error("Discord route '%s' send failed after retry: %s; message dropped.", route.name, exc)
                return False
        return False


def reset_disabled_route_warnings() -> None:
    """Test/maintenance helper — clears the one-shot disabled-route warning set."""
    _warned_disabled.clear()
