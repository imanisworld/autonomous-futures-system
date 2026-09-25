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
    A 429 retry first waits the delay Discord asked for (``retry_after`` /
    ``Retry-After``), bounded by ``max_retry_wait``; a longer requested wait
    drops the message instead of blocking the caller.
  - Webhook URLs/tokens are never logged: every logged error is passed
    through ``redact_webhooks`` first.
  - A failed delivery is NEVER re-notified through Discord (no notification
    loops during a Discord outage).
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

logger = logging.getLogger(__name__)

# transport(url, message) -> None ; must raise on failure. ``message`` is plain
# text or an already-built webhook body (dict, e.g. an embed card).
Transport = Callable[[str, "str | dict"], None]
UpsertTransport = Callable[[str, "str | dict", Optional[str]], Optional[str]]

_DEFAULT_ROUTES_PATH = Path(__file__).resolve().parent.parent / "config" / "notification_routes.yaml"

# Discord webhook URL: /api/webhooks/<id>/<token>. The token is the secret.
_WEBHOOK_URL = re.compile(
    r"https?://(?:[\w-]+\.)?discord(?:app)?\.com/api(?:/v\d+)?/webhooks/[^\s'\"<>]+",
    re.IGNORECASE,
)
_REDACTED_WEBHOOK = "<discord-webhook-redacted>"

# Default ceiling on a server-requested 429 wait for inline callers (the
# signal/error routes run on the alert path). Background senders pass more.
DEFAULT_MAX_RETRY_WAIT = 2.0


def redact_webhooks(text: object) -> str:
    """Replace any Discord webhook URL (id + secret token) in ``text``."""
    return _WEBHOOK_URL.sub(_REDACTED_WEBHOOK, str(text))


def retry_after_seconds(exc: BaseException) -> Optional[float]:
    """Seconds Discord asked us to wait on a 429, or None when not a 429.

    Reads the JSON body's ``retry_after`` first (Discord's documented field),
    then the ``Retry-After`` header. A 429 without a usable value returns 1.0.
    """
    response = getattr(exc, "response", None)
    if getattr(response, "status_code", None) != 429:
        return None
    try:
        value = float((response.json() or {}).get("retry_after"))
        if value >= 0:
            return value
    except Exception:  # noqa: BLE001 - body may be missing or not JSON
        pass
    try:
        value = float(response.headers.get("Retry-After"))
        if value >= 0:
            return value
    except Exception:  # noqa: BLE001
        pass
    return 1.0


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


def _default_transport(url: str, message: "str | dict", *, source: str = "") -> None:
    import httpx

    from notifications.discord_card import post_card_or_text

    def _post(body: dict) -> None:
        response = httpx.post(url, json=body, timeout=5)
        response.raise_for_status()

    # Plain text is laid out as a paper-collection-style card; a 400 falls
    # back to the original text so layout can never drop an alert.
    post_card_or_text(_post, message, source=source)

def _with_wait(url: str) -> str:
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["wait"] = "true"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _message_url(url: str, message_id: str) -> str:
    parts = urlsplit(url)
    path = parts.path.rstrip("/") + f"/messages/{message_id}"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _default_upsert_transport(
    url: str,
    message: "str | dict",
    message_id: Optional[str],
    *,
    source: str = "",
) -> Optional[str]:
    """Create or edit one Discord webhook message and return its message id.

    A stale/deleted message id is repaired by creating a replacement. This is
    presentation-only state and never feeds trading, risk, evidence, or broker
    decisions.
    """
    import httpx

    from notifications.discord_card import card_payload, text_payload

    body = card_payload(message, source=source)

    if message_id:
        response = httpx.patch(_message_url(url, message_id), json=body, timeout=5)
        if response.status_code == 404:
            message_id = None
        else:
            if response.status_code == 400 and not isinstance(message, dict):
                response = httpx.patch(_message_url(url, message_id), json=text_payload(message), timeout=5)
            response.raise_for_status()
            return str(message_id)

    response = httpx.post(_with_wait(url), json=body, timeout=5)
    if response.status_code == 400 and not isinstance(message, dict):
        response = httpx.post(_with_wait(url), json=text_payload(message), timeout=5)
    response.raise_for_status()
    payload: Any = response.json()
    value = payload.get("id") if isinstance(payload, dict) else None
    return str(value) if value else None


class DiscordRouter:
    """Resolves logical route names to env-configured URLs and delivers messages."""

    def __init__(
        self,
        routes: Optional[Mapping[str, Route]] = None,
        routes_path: str | os.PathLike[str] | None = None,
        transport: Optional[Transport] = None,
        upsert_transport: Optional[UpsertTransport] = None,
        env: Optional[Mapping[str, str]] = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.routes: dict[str, Route] = dict(routes) if routes is not None else load_routes(routes_path)
        self._transport = transport or _default_transport
        if upsert_transport is not None:
            self._upsert_transport = upsert_transport
        elif transport is None:
            self._upsert_transport = None
        else:
            # Custom transports historically support POST only. Keep tests and
            # alternate transports fail-soft rather than performing real HTTP.
            def _compat(url: str, message: "str | dict", message_id: Optional[str]) -> Optional[str]:
                transport(url, message)
                return message_id
            self._upsert_transport = _compat
        self._env = env if env is not None else os.environ
        self._sleep = sleep

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
    def upsert(
        self,
        route_name: str,
        message: "str | dict",
        *,
        message_id: Optional[str] = None,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
    ) -> tuple[bool, Optional[str]]:
        """Create or edit one persistent message on a logical route.

        Returns (delivered, message_id). Delivery problems are fail-soft,
        matching send(). The returned id is presentation metadata only.
        """
        route = self.routes.get(route_name)
        if route is None:
            raise ValueError(
                f"Unknown notification route '{route_name}'. "
                f"Known routes: {', '.join(self.routes)}"
            )

        url = str(self._env.get(route.env_var, "")).strip()
        if not url:
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
            return False, message_id

        for attempt in (1, 2):
            try:
                if self._upsert_transport is None:
                    new_id = _default_upsert_transport(
                        url, message, message_id, source=f"{route.name} route"
                    )
                else:
                    new_id = self._upsert_transport(url, message, message_id)
                return True, (new_id or message_id)
            except Exception as exc:
                error = redact_webhooks(exc)
                if attempt == 1:
                    wait = retry_after_seconds(exc)
                    if wait is not None and wait > max_retry_wait:
                        logger.error(
                            "Discord route '%s' rate limited (retry after %.2fs > %.2fs cap): %s; message dropped.",
                            route.name, wait, max_retry_wait, error,
                        )
                        return False, message_id
                    if wait is not None:
                        logger.warning(
                            "Discord route '%s' rate limited: %s; retrying once after %.2fs.",
                            route.name, error, wait,
                        )
                        self._sleep(wait)
                    else:
                        logger.warning(
                            "Discord route '%s' upsert attempt 1 failed: %s; retrying once.",
                            route.name, error,
                        )
                    continue
                logger.error(
                    "Discord route '%s' upsert failed after retry: %s; message dropped.",
                    route.name, error,
                )
                return False, message_id
        return False, message_id

    def send(
        self,
        route_name: str,
        message: "str | dict",
        metadata: Optional[dict] = None,
        *,
        max_retry_wait: float = DEFAULT_MAX_RETRY_WAIT,
    ) -> bool:
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
                if self._transport is _default_transport:
                    _default_transport(url, message, source=f"{route.name} route")
                else:
                    self._transport(url, message)
                return True
            except Exception as exc:  # noqa: BLE001 - delivery must never propagate
                error = redact_webhooks(exc)
                if attempt == 1:
                    wait = retry_after_seconds(exc)
                    if wait is not None and wait > max_retry_wait:
                        logger.error(
                            "Discord route '%s' rate limited (retry after %.2fs > %.2fs cap): %s; message dropped.",
                            route.name, wait, max_retry_wait, error,
                        )
                        return False
                    if wait is not None:
                        logger.warning(
                            "Discord route '%s' rate limited: %s; retrying once after %.2fs.",
                            route.name, error, wait,
                        )
                        self._sleep(wait)
                    else:
                        logger.warning("Discord route '%s' send attempt 1 failed: %s; retrying once.", route.name, error)
                    continue
                logger.error("Discord route '%s' send failed after retry: %s; message dropped.", route.name, error)
                return False
        return False


def reset_disabled_route_warnings() -> None:
    """Test/maintenance helper — clears the one-shot disabled-route warning set."""
    _warned_disabled.clear()
