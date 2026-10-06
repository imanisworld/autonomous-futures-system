"""In-app access gate for the advisory options scanner.

The scanner was designed to bind to localhost and rely on the reverse proxy for
authentication. On 2026-09-21 01:33Z nginx basic auth was removed from every
location on both vhosts (see ``docs/afsvp-security-headers-proposal-2026-09-21.md``),
which left ``/scanner/`` -- shadow-journal reads, Signa context, and the POST /
PATCH mutators -- reachable by anyone. This module makes the scanner enforce
access itself so exposure no longer depends on a single proxy setting.

Rules (evaluated per request, before any route handler runs):

* ``PUBLIC_PATHS`` are always served. They are the sanitized, allowlisted
  surfaces only.
* Every other path requires one of:
    - the access token (``OPTIONS_SCANNER_ACCESS_TOKEN``) in the
      ``X-AFS-Scanner-Token`` header or ``Authorization: Bearer``;
    - the session cookie set by ``POST /login`` with that token;
    - when no token is configured: a *direct* loopback request -- client
      address is loopback **and** no proxy forwarding header is present. A
      request proxied by nginx is not a direct local request, so it is refused.
* Anything else is refused with 401. Nothing here grants broker, order, risk,
  or execution authority; the gate only narrows who can reach existing routes.

The no-token loopback rule is a fail-closed fallback, not the intended
production posture: it relies on the proxy adding a forwarding header. The
operator should set ``OPTIONS_SCANNER_ACCESS_TOKEN`` on the box.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Iterable
from urllib.parse import parse_qs

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

TOKEN_HEADER = "x-afs-scanner-token"
SESSION_COOKIE = "afs_scanner_session"
LOGIN_PATH = "/login"
LOGOUT_PATH = "/logout"

# Allowlisted surfaces safe for an unauthenticated visitor. Do not add
# shadow-journal, Signa, terminal, status, rh-options, or any mutator here.
PUBLIC_PATHS = frozenset({"/health", "/public/status", LOGIN_PATH, LOGOUT_PATH})

# Headers a reverse proxy adds. Their presence means the request did not
# originate on the box itself, even if the TCP peer is 127.0.0.1.
FORWARDING_HEADERS = (
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
    "x-forwarded-host",
    "x-forwarded-proto",
)

_LOOPBACK_HOSTNAMES = frozenset({"localhost"})


def _session_value(token: str) -> str:
    """Cookie value derived from the token; rotating the token revokes sessions."""
    return hmac.new(token.encode("utf-8"), b"afs-options-scanner-session-v1", hashlib.sha256).hexdigest()


def _matches(supplied: str, expected: str) -> bool:
    if not supplied or not expected:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in _LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_direct_local_request(request: Request) -> bool:
    client_host = request.client.host if request.client else None
    if not _is_loopback(client_host):
        return False
    return not any(request.headers.get(name) for name in FORWARDING_HEADERS)


def supplied_token(request: Request) -> str:
    header = request.headers.get(TOKEN_HEADER, "").strip()
    if header:
        return header
    auth = request.headers.get("authorization", "")
    scheme, _, value = auth.partition(" ")
    if scheme.lower() == "bearer":
        return value.strip()
    return ""


def is_authorized(request: Request, token: str) -> bool:
    token = (token or "").strip()
    if token:
        if _matches(supplied_token(request), token):
            return True
        return _matches(request.cookies.get(SESSION_COOKIE, ""), _session_value(token))
    return is_direct_local_request(request)


def gate_mode(token: str) -> str:
    return "token" if (token or "").strip() else "direct_loopback_only"


def _unauthorized(request: Request) -> Response:
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return HTMLResponse(_login_page(request), status_code=401)
    return JSONResponse(
        status_code=401,
        content={"error": "unauthorized", "detail": "options scanner access required"},
    )


def _login_page(request: Request, *, error: bool = False) -> str:
    # Relative form action: behind the ``/scanner/`` proxy prefix an absolute
    # ``/login`` would land on the app host instead of the scanner.
    message = "<p>Invalid access token.</p>" if error else ""
    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>Options Scanner Access</title></head><body>"
        "<h1>Options scanner access required</h1>"
        f"{message}"
        "<form method=\"post\" action=\"login\">"
        "<label>Access token <input type=\"password\" name=\"token\" autocomplete=\"off\"></label>"
        "<button type=\"submit\">Enter</button></form></body></html>"
    )


async def _handle_login(request: Request, token: str) -> Response:
    if request.method == "GET":
        return HTMLResponse(_login_page(request))
    if request.method != "POST":
        return JSONResponse(status_code=405, content={"error": "method_not_allowed"})
    if not token:
        # No token configured: there is nothing to log in with. Direct local
        # access does not need a session.
        return JSONResponse(status_code=503, content={"error": "access_token_not_configured"})
    body = (await request.body()).decode("utf-8", errors="replace")
    supplied = (parse_qs(body).get("token") or [""])[0].strip()
    if not _matches(supplied, token):
        return HTMLResponse(_login_page(request, error=True), status_code=401)
    response = Response(status_code=303, headers={"location": "./"})
    response.set_cookie(
        SESSION_COOKIE,
        _session_value(token),
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=12 * 3600,
    )
    return response


def _handle_logout() -> Response:
    response = JSONResponse(content={"logged_out": True})
    response.delete_cookie(SESSION_COOKIE)
    return response


def install_access_gate(app, token: str, *, public_paths: Iterable[str] = PUBLIC_PATHS) -> None:
    """Register the gate as HTTP middleware on ``app``."""
    configured = (token or "").strip()
    allowed = frozenset(public_paths)

    @app.middleware("http")
    async def _access_gate(request: Request, call_next):
        path = request.url.path
        if path == LOGIN_PATH:
            return await _handle_login(request, configured)
        if path == LOGOUT_PATH:
            return _handle_logout()
        if path in allowed or is_authorized(request, configured):
            return await call_next(request)
        return _unauthorized(request)
