"""Tradovate REST API broker adapter.

Connects to Tradovate's cloud API — no local Gateway process required.
Works from any server with outbound HTTPS (deployed on Hetzner VPS).

Environments:
    demo → https://demo.tradovateapi.com/v1   (paper sim, free)
    live → https://live.tradovateapi.com/v1   (real money)

Required env vars:
    TRADOVATE_ENV           — "demo" or "live" (default: "demo")
    TRADOVATE_USERNAME      — account email
    TRADOVATE_PASSWORD      — account password
    TRADOVATE_API_KEY_ID    — CID from Tradovate app credentials
    TRADOVATE_API_KEY_SECRET — secret UUID from Tradovate app credentials
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import requests

from execution.broker_interface import (
    BracketOrder,
    BrokerCapabilities,
    BrokerInterface,
    Fill,
    Position,
)

logger = logging.getLogger(__name__)

AUTH_HEALTHY = "healthy"
AUTH_TEMPORARY_FAILURE = "temporary_failure"
AUTH_RATE_LIMITED = "rate_limited"
AUTH_TOKEN_INVALID = "token_invalid"
AUTH_CREDENTIALS_REJECTED = "credentials_rejected"
AUTH_COOLDOWN = "cooldown"


@dataclass(frozen=True)
class AuthResult:
    status: str
    detail: Optional[str] = None
    retry_after_seconds: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == AUTH_HEALTHY

# Per-instrument tick specs
_TICK_SIZE: dict[str, float] = {
    "MES": 0.25, "ES": 0.25, "MNQ": 0.25, "NQ": 0.25, "MGC": 0.10, "MCL": 0.01,
}
_TICK_VALUE: dict[str, float] = {
    "MES": 1.25, "ES": 12.50, "MNQ": 0.50, "NQ": 5.00, "MGC": 1.00, "MCL": 1.00,
}


def _round_to_tick(price: float, instrument: str) -> float:
    """Snap a price to the instrument's tick grid (e.g. MNQ 0.25).

    Tradovate rejects Stop/Limit child orders whose price is not an exact
    multiple of the contract tick. Strategy stops/targets are frequently
    VWAP-anchored (``vwap.value ± n·tick``) or risk-multiples
    (``entry + risk·2.2``), which land *between* ticks; the strategy's
    ``round(x, 4)`` rounds decimals but does NOT snap to the grid. An off-tick
    protective child is silently rejected, leaving a naked entry that the
    safety net has to flatten — the repeated "NAKED POSITION … STOP" alerts.
    Round to the nearest tick so the bracket children are accepted.
    """
    root = instrument.replace("1!", "").upper()
    tick = _TICK_SIZE.get(root, 0.25)
    if tick <= 0:
        return float(price)
    return round(round(float(price) / tick) * tick, 4)


def _parse_api_key_id(value: str | None) -> int:
    raw = (value or "0").strip().strip("\"'")
    if not raw:
        return 0
    if raw.isdigit():
        return int(raw)
    raise ValueError(
        "TRADOVATE_API_KEY_ID must be the numeric CID only "
        f"(example: 13833), got {raw!r}"
    )


# ─── Config ───────────────────────────────────────────────────────────────────

@dataclass
class TradovateConfig:
    env: str = "demo"           # "demo" | "live"
    username: str = ""
    password: str = ""
    cid: int = 0                # API key ID
    secret: str = ""            # API key secret UUID
    app_id: str = "RiskSentinel"
    app_version: str = "1.0"
    token_refresh_buffer: int = 300  # seconds before expiry to refresh

    @classmethod
    def from_env(cls) -> "TradovateConfig":
        return cls(
            env=os.getenv("TRADOVATE_ENV", "demo").strip().lower(),
            username=os.getenv("TRADOVATE_USERNAME", "").strip(),
            password=os.getenv("TRADOVATE_PASSWORD", "").strip(),
            cid=_parse_api_key_id(os.getenv("TRADOVATE_API_KEY_ID", "0")),
            secret=os.getenv("TRADOVATE_API_KEY_SECRET", "").strip(),
        )

    @property
    def base_url(self) -> str:
        if self.env == "live":
            return "https://live.tradovateapi.com/v1"
        return "https://demo.tradovateapi.com/v1"


# ─── Auth token ───────────────────────────────────────────────────────────────

@dataclass
class _Token:
    access_token: str
    expires_at: float  # unix timestamp

    def is_valid(self, buffer: int = 300) -> bool:
        return time.time() < (self.expires_at - buffer)


# ─── Shared auth session (ONE Tradovate session per process) ────────────────────
# Tradovate allows only TWO concurrent sessions per account. Every call to
# /auth/accesstokenrequest opens a NEW session — so if each TradovateBroker
# instance (webhook runner, dashboard checks, position resolution, manual
# actions) authenticated independently, they'd evict each other AND the user's
# browser login, surfacing as `not_authenticated`. We therefore SHARE one token
# + one circuit breaker across all instances that use the same credentials, and
# RENEW that token in place (/auth/renewAccessToken) instead of re-requesting a
# brand-new session. Only a genuine renewal failure falls back to a fresh login.

@dataclass
class _SharedAuth:
    token: Optional[_Token] = None
    fail_count: int = 0
    cooldown_until: float = 0.0
    last_error: Optional[str] = None
    last_renewed_at: Optional[float] = None
    lock: threading.RLock = field(default_factory=threading.RLock)


_SHARED_AUTH: dict[tuple, _SharedAuth] = {}
_SHARED_AUTH_REGISTRY_LOCK = threading.Lock()


def _get_shared_auth(key: tuple) -> _SharedAuth:
    with _SHARED_AUTH_REGISTRY_LOCK:
        st = _SHARED_AUTH.get(key)
        if st is None:
            st = _SharedAuth()
            _SHARED_AUTH[key] = st
        return st


def _reset_shared_auth() -> None:
    """Test helper — clear all process-shared Tradovate auth state."""
    with _SHARED_AUTH_REGISTRY_LOCK:
        _SHARED_AUTH.clear()


# ─── Broker ───────────────────────────────────────────────────────────────────

class TradovateBroker(BrokerInterface):
    """BrokerInterface implementation for Tradovate (demo or live)."""

    def __init__(self, config: Optional[TradovateConfig] = None) -> None:
        self.config = config or TradovateConfig.from_env()
        # ONE shared session/token + circuit breaker per (env, account) so the
        # whole process holds a SINGLE Tradovate session (see _SharedAuth above).
        self._auth_state = _get_shared_auth(
            (self.config.base_url, self.config.username, self.config.cid)
        )
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._last_position: Optional[Position] = None
        # OSO order ids for the current position, set by execute_bracket:
        # {"instrument", "entry", "target", "stop"}. Lets resolve_position scope
        # fills to OUR specific orders (correct partial-fill averaging; can't
        # grab an overlapping trade's fill). None → resolve falls back to
        # price-matching. Cleared whenever the position goes flat/resolves.
        self._last_order_ids: Optional[dict] = None
        self._account_id: Optional[int] = None
        self._contract_cache: dict[str, int] = {}  # root symbol → contract ID
        self._contract_symbol_cache: dict[str, str] = {}  # root → front-month symbol (e.g. MESM6)
        self._resolve_fail_count: int = 0          # consecutive resolve_position failures
        self._position_opened_at: Optional[float] = None  # time.time() when bracket placed
        self._last_price: dict[str, float] = {}   # root symbol → last bar close from TV webhook
        # Set after execute_bracket: did the protective stop+target children verify live?
        # None = not checked, True = both confirmed working, False = one/both missing (naked risk).
        self._last_bracket_confirmed: Optional[bool] = None
        # Auth circuit-breaker + token state now live in self._auth_state, shared
        # across all instances with the same creds (see _SharedAuth). They are
        # exposed via the _token / _auth_fail_count / _auth_cooldown_until /
        # _last_auth_error properties below so existing callers keep working.

    # ── Auth ──────────────────────────────────────────────────────────────────

    # Token + circuit-breaker state are process-shared (proxied to _auth_state).
    @property
    def _token(self) -> Optional[_Token]:
        return self._auth_state.token

    @_token.setter
    def _token(self, value: Optional[_Token]) -> None:
        self._auth_state.token = value

    @property
    def _auth_fail_count(self) -> int:
        return self._auth_state.fail_count

    @_auth_fail_count.setter
    def _auth_fail_count(self, value: int) -> None:
        self._auth_state.fail_count = value

    @property
    def _auth_cooldown_until(self) -> float:
        return self._auth_state.cooldown_until

    @_auth_cooldown_until.setter
    def _auth_cooldown_until(self, value: float) -> None:
        self._auth_state.cooldown_until = value

    @property
    def _last_auth_error(self) -> Optional[str]:
        return self._auth_state.last_error

    @_last_auth_error.setter
    def _last_auth_error(self, value: Optional[str]) -> None:
        self._auth_state.last_error = value

    # Auth circuit-breaker tuning.
    _AUTH_MAX_FAILURES = 3        # consecutive failures before backing off
    _AUTH_COOLDOWN_SECONDS = 900  # 15m quiet period (Tradovate locks on repeated fails)

    def _note_auth_failure(self, reason: str) -> None:
        self._auth_fail_count += 1
        self._last_auth_error = reason
        if self._auth_fail_count >= self._AUTH_MAX_FAILURES:
            self._auth_cooldown_until = time.time() + self._AUTH_COOLDOWN_SECONDS
            logger.error(
                "Tradovate auth failed %d× (%s) — backing off %ds to avoid a lockout. "
                "Verify TRADOVATE_USERNAME / PASSWORD / API_KEY_ID / API_KEY_SECRET "
                "(API keys expire).",
                self._auth_fail_count, reason, self._AUTH_COOLDOWN_SECONDS,
            )

    @staticmethod
    def _http_failure_result(exc: Exception, *, login: bool = False) -> AuthResult:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status == 429:
            retry = None
            try:
                retry = int(response.headers.get("Retry-After"))
            except (AttributeError, TypeError, ValueError):
                pass
            return AuthResult(AUTH_RATE_LIMITED, "HTTP 429", retry)
        if status in {401, 403}:
            kind = AUTH_CREDENTIALS_REJECTED if login else AUTH_TOKEN_INVALID
            return AuthResult(kind, f"HTTP {status}")
        if status is None or status >= 500:
            return AuthResult(AUTH_TEMPORARY_FAILURE, str(exc))
        return AuthResult(AUTH_TEMPORARY_FAILURE, f"HTTP {status}: {exc}")

    @staticmethod
    def _parse_expiry(expiry_raw) -> float:
        """Parse Tradovate's expirationTime (ISO 8601 string, or ms number)."""
        if not expiry_raw:
            return time.time() + 3600
        try:
            from datetime import datetime
            if isinstance(expiry_raw, str):
                # Handle both +00:00 and Z suffixes.
                return datetime.fromisoformat(expiry_raw.replace("Z", "+00:00")).timestamp()
            # Fallback: treat as milliseconds if it's a number.
            return float(expiry_raw) / 1000.0
        except Exception:
            return time.time() + 3600

    def _apply_token(self, tok: _Token) -> None:
        """Point THIS instance's HTTP session at the shared token, and make sure
        the account id is resolved (so a brand-new instance reusing the shared
        session is immediately usable)."""
        self._session.headers["Authorization"] = f"Bearer {tok.access_token}"
        if self._account_id is None:
            self._resolve_account_id()

    def _authenticate(self) -> bool:
        return self.authenticate_result().ok

    def authenticate_result(self) -> AuthResult:
        """Ensure THIS instance has a usable Tradovate session.

        Order of preference (all under a shared lock so only one session is ever
        opened per process):
          1. Reuse the shared token if still valid → just bind this session.
          2. Honor the circuit breaker (don't hammer auth after repeated 401s).
          3. RENEW the existing token in place (same session) if it's stale.
          4. Only as a last resort, open a fresh session (accesstokenrequest).
        """
        with self._auth_state.lock:
            tok = self._auth_state.token
            # 1. Valid shared token → reuse it for this instance.
            if tok and tok.is_valid(self.config.token_refresh_buffer):
                self._apply_token(tok)
                return AuthResult(AUTH_HEALTHY)

            # 2. Circuit breaker: after repeated failures, refuse to hit the auth
            # API until the cooldown elapses so we don't get the account locked.
            if time.time() < self._auth_cooldown_until:
                return AuthResult(
                    AUTH_COOLDOWN,
                    self._last_auth_error,
                    max(1, int(self._auth_cooldown_until - time.time())),
                )

            # 3. Token exists but is stale → renew in place (keeps SAME session).
            if tok is not None:
                renewed = self.renew_token_result()
                if renewed.ok:
                    return renewed
                # A timeout, 429, or provider outage must never create another
                # Tradovate session. Only an explicitly invalid token may.
                if renewed.status != AUTH_TOKEN_INVALID:
                    return renewed

            # 4. Last resort — no token, or renewal explicitly proved it invalid.
            return self.login_result()

    def _renew_token(self) -> bool:
        return self.renew_token_result().ok

    def renew_token_result(self) -> AuthResult:
        """Extend the existing 90-minute token via /auth/renewAccessToken — this
        keeps the SAME Tradovate session (no new session created). The classified
        result lets callers distinguish an invalid token from a temporary outage."""
        tok = self._auth_state.token
        if tok is None:
            return AuthResult(AUTH_TOKEN_INVALID, "no token")
        url = f"{self.config.base_url}/auth/renewAccessToken"
        try:
            resp = self._session.get(
                url, timeout=10,
                headers={"Authorization": f"Bearer {tok.access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()
            new_token = data.get("accessToken")
            if not new_token:
                logger.warning(
                    "Tradovate token renewal returned no accessToken."
                )
                return AuthResult(AUTH_TOKEN_INVALID, "renewal returned no accessToken")
            self._auth_state.token = _Token(
                access_token=new_token,
                expires_at=self._parse_expiry(data.get("expirationTime")),
            )
            self._apply_token(self._auth_state.token)
            # Healthy renewal clears the breaker.
            self._auth_fail_count = 0
            self._auth_cooldown_until = 0.0
            self._last_auth_error = None
            self._auth_state.last_renewed_at = time.time()
            logger.info("Tradovate token renewed in place (env=%s)", self.config.env)
            return AuthResult(AUTH_HEALTHY)
        except Exception as exc:
            result = self._http_failure_result(exc)
            logger.warning("Tradovate token renewal failed (%s): %s", result.status, exc)
            return result

    def _login(self) -> bool:
        return self.login_result().ok

    def login_result(self) -> AuthResult:
        """Open a NEW Tradovate session via /auth/accesstokenrequest.

        Each call creates a new session and Tradovate only allows two concurrent
        — so this is the last resort, used only when there is no token or renewal
        failed. Returns True on success."""
        url = f"{self.config.base_url}/auth/accesstokenrequest"
        body = {
            "name": self.config.username,
            "password": self.config.password,
            "appId": self.config.app_id,
            "appVersion": self.config.app_version,
            "cid": self.config.cid,
            "sec": self.config.secret,
            "deviceId": "risksentinel-server",
        }
        try:
            resp = self._session.post(url, json=body, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            token = data.get("accessToken")
            if not token:
                err = data.get("errorText", "no accessToken in response")
                logger.error("Tradovate auth failed: %s", err)
                self._note_auth_failure(f"rejected: {err}")
                return AuthResult(AUTH_CREDENTIALS_REJECTED, str(err))
            self._auth_state.token = _Token(
                access_token=token,
                expires_at=self._parse_expiry(data.get("expirationTime")),
            )
            self._apply_token(self._auth_state.token)
            # Success — clear the circuit breaker.
            self._auth_fail_count = 0
            self._auth_cooldown_until = 0.0
            self._last_auth_error = None
            logger.info("Tradovate authenticated — new session (env=%s)", self.config.env)
            return AuthResult(AUTH_HEALTHY)
        except Exception as exc:
            result = self._http_failure_result(exc, login=True)
            logger.warning("Tradovate authentication error (%s): %s", result.status, exc)
            # Only rejected credentials count toward the 15-minute auth breaker.
            # Provider outages and rate limits keep their own retry behavior.
            if result.status == AUTH_CREDENTIALS_REJECTED:
                self._note_auth_failure("credentials_rejected (401)")
            return result

    def keep_alive(self) -> bool:
        return self.keep_alive_result().ok

    def keep_alive_result(self) -> AuthResult:
        """Background-timer entry point: keep the shared session token fresh so it
        never expires from inactivity.

        Renewing (/auth/renewAccessToken) extends the SAME session WITHOUT using
        the API key, so as long as we renew before the ~90-min token lapses we
        never re-login — which means the recurring API-key 401 (the key expiring)
        never bites. Only if there is no token, or renewal explicitly proves the
        session invalid, do we fall back to a fresh login. Temporary failures and
        rate limits retain the existing session and retry later.
        """
        with self._auth_state.lock:
            if self._auth_state.token is not None:
                result = self.renew_token_result()
                if result.ok or result.status != AUTH_TOKEN_INVALID:
                    return result
            if time.time() < self._auth_state.cooldown_until:
                return AuthResult(
                    AUTH_COOLDOWN,
                    self._last_auth_error,
                    max(1, int(self._auth_state.cooldown_until - time.time())),
                )
            return self.login_result()

    def reliability_heartbeat(self) -> AuthResult:
        """Confirm auth, account access, and position-state readability.

        This is intentionally a small REST heartbeat. It never creates a fresh
        session after a temporary provider/network failure.
        """
        auth = self.authenticate_result()
        if not auth.ok:
            return auth
        try:
            account_resp = self._session.get(f"{self.config.base_url}/account/list", timeout=8)
            account_resp.raise_for_status()
            accounts = account_resp.json()
            if not isinstance(accounts, list) or not accounts:
                return AuthResult(AUTH_TEMPORARY_FAILURE, "account/list returned no accounts")
            self._account_id = accounts[0].get("id")

            position_resp = self._session.get(f"{self.config.base_url}/position/list", timeout=8)
            position_resp.raise_for_status()
            if not isinstance(position_resp.json(), list):
                return AuthResult(AUTH_TEMPORARY_FAILURE, "position/list returned malformed data")
            return AuthResult(AUTH_HEALTHY)
        except Exception as exc:
            result = self._http_failure_result(exc)
            if result.status == AUTH_TOKEN_INVALID and self._auth_state.token is not None:
                # The heartbeat proved this cached bearer token is unusable.
                # Mark it stale so the next supervised auth cycle renews it,
                # then opens at most one replacement session if renewal confirms
                # the invalid-token response.
                self._auth_state.token.expires_at = 0.0
            return result

    def _resolve_account_id(self) -> None:
        try:
            resp = self._session.get(f"{self.config.base_url}/account/list", timeout=8)
            resp.raise_for_status()
            accounts = resp.json()
            if accounts:
                self._account_id = accounts[0].get("id")
                logger.info("Tradovate account ID: %s", self._account_id)
        except Exception as exc:
            logger.warning("Could not resolve Tradovate account ID: %s", exc)

    def _get(self, path: str, **kwargs) -> dict:
        if not self._authenticate():
            raise RuntimeError("Tradovate not authenticated")
        resp = self._session.get(f"{self.config.base_url}{path}", timeout=10, **kwargs)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict, **kwargs) -> dict:
        if not self._authenticate():
            raise RuntimeError("Tradovate not authenticated")
        resp = self._session.post(f"{self.config.base_url}{path}", json=body, timeout=10, **kwargs)
        resp.raise_for_status()
        return resp.json()

    # ── Contract resolution ───────────────────────────────────────────────────

    def _find_contract_id(self, instrument: str) -> int:
        """Find the front-month contract ID for MES (or other CME micro). Cached."""
        root = instrument.replace("1!", "").upper()
        if root in self._contract_cache:
            return self._contract_cache[root]
        # Cache miss — use /contract/suggest (find/search endpoints 404 on Tradovate REST)
        last_exc: Exception = RuntimeError("no attempts made")
        for attempt in range(3):
            try:
                results = self._get(f"/contract/suggest?t={root}&l=1")
                if isinstance(results, list) and results:
                    self._contract_cache[root] = int(results[0]["id"])
                    self._contract_symbol_cache[root] = str(results[0].get("name") or root)
                    return self._contract_cache[root]
                raise ValueError(f"Contract not found for {instrument}")
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))  # 1.5s, 3.0s
        raise last_exc

    # ── BrokerInterface implementation ────────────────────────────────────────

    @property
    def is_live(self) -> bool:
        return self.config.env == "live"

    def execute_bracket(self, order: BracketOrder) -> Fill:
        """Place entry market order with attached stop and target (OSO bracket)."""
        try:
            # ── Safety: TRADOVATE_ENV=live requires explicit LIVE_TRADING_ENABLED=true ──
            if self.config.env == "live":
                live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower()
                if live_enabled != "true":
                    logger.error(
                        "BLOCKED real-money order: TRADOVATE_ENV=live but "
                        "LIVE_TRADING_ENABLED=%s — set LIVE_TRADING_ENABLED=true to allow",
                        live_enabled,
                    )
                    return self._cancelled_fill(order, "LIVE_TRADING_NOT_ENABLED")

            from execution.tradovate_supervisor import tradovate_order_ready
            if not tradovate_order_ready():
                logger.error("BLOCKED Tradovate order: reliability supervisor is not ready")
                return self._cancelled_fill(order, "BROKER_NOT_READY")

            if not self._authenticate():
                return self._cancelled_fill(order, "TRADOVATE_AUTH_FAILED")

            contract_id = self._find_contract_id(order.instrument)
            root = order.instrument.replace("1!", "").upper()
            # Tradovate placeOSO needs the specific contract symbol (e.g. MESM6),
            # NOT the root (MES) — the root is rejected with UnknownReason.
            contract_symbol = self._contract_symbol_cache.get(root, root)
            action = "Buy" if order.direction == "LONG" else "Sell"
            close_action = "Sell" if order.direction == "LONG" else "Buy"
            qty = max(1, int(order.contracts or 1))

            # Snap protective prices to the contract tick grid. Off-tick Stop/
            # Limit children are rejected by Tradovate, which would leave the
            # market entry naked (see _round_to_tick). The entry itself is a
            # Market order, so it never carries a price to reject.
            tick_target = _round_to_tick(order.target, root)
            tick_stop = _round_to_tick(order.stop, root)
            if tick_target != float(order.target) or tick_stop != float(order.stop):
                logger.info(
                    "Tick-rounded bracket for %s: target %s→%s stop %s→%s",
                    root, order.target, tick_target, order.stop, tick_stop,
                )

            # Place bracket via OSO (On Submit, send bracket child orders)
            body = {
                "accountSpec": self.config.username,
                "accountId": self._account_id,
                "action": action,
                "symbol": contract_symbol,
                "orderQty": qty,
                "orderType": "Market",
                "isAutomated": True,
                "bracket1": {
                    "action": close_action,
                    "orderType": "Limit",
                    "price": tick_target,
                },
                "bracket2": {
                    "action": close_action,
                    "orderType": "Stop",
                    "stopPrice": tick_stop,
                },
            }

            result = self._post("/order/placeOSO", body)
            # Detect API-level rejection — Tradovate returns errorText/failureReason on bad payloads
            error_text = result.get("errorText") or result.get("failureReason") or result.get("errorCode")
            if error_text:
                logger.error(
                    "Tradovate placeOSO REJECTED: %s | instrument=%s dir=%s body=%s",
                    error_text, order.instrument, order.direction, body,
                )
                return self._cancelled_fill(order, f"TRADOVATE_REJECTED")
            order_id = result.get("orderId") or (result.get("orderStatus", {}) or {}).get("orderId")
            if not order_id:
                logger.error(
                    "Tradovate placeOSO returned no orderId — possible silent rejection: %s",
                    result,
                )
                return self._cancelled_fill(order, "TRADOVATE_NO_ORDER_ID")
            # OSO confirms the bracket children with their own order IDs:
            # oso1 = bracket1 (Limit/target), oso2 = bracket2 (Stop).
            target_id = result.get("oso1Id")
            stop_id = result.get("oso2Id")
            logger.info(
                "Tradovate bracket placed: entry=%s target=%s stop=%s instrument=%s dir=%s",
                order_id, target_id, stop_id, order.instrument, order.direction,
            )

            self._last_position = Position(
                instrument=order.instrument,
                direction=order.direction,
                entry_price=order.entry,
                stop=tick_stop,
                target=tick_target,
                quantity=qty,
                open=True,
            )
            # Remember the OSO order ids so resolve_position can scope fills to
            # exactly these orders (correct partial-fill averaging; immune to an
            # overlapping trade's fill at a similar price).
            self._last_order_ids = {
                "instrument": order.instrument,
                "entry": order_id,
                "target": target_id,
                "stop": stop_id,
            }
            self._position_opened_at = time.time()
            self._resolve_fail_count = 0

            # Verify BOTH protective children (stop + target) actually went live.
            # A market entry can fill while its bracket children are rejected,
            # leaving a naked position. If either is unconfirmed we do NOT leave
            # the entry exposed: alert loudly AND auto-flatten immediately —
            # a few ticks of slippage beats unbounded unprotected risk.
            stop_ok, target_ok = self._verify_bracket_children(
                stop_id=stop_id, target_id=target_id, order=order,
            )
            self._last_bracket_confirmed = stop_ok and target_ok
            if not self._last_bracket_confirmed:
                return self._handle_naked_position(order, qty, stop_ok=stop_ok, target_ok=target_ok)

            logger.info("Tradovate bracket fully confirmed (entry+stop+target): %s", order.instrument)
            return Fill(
                instrument=order.instrument,
                direction=order.direction,
                contracts=qty,
                entry_price=order.entry,
                exit_price=None,
                exit_reason=None,
                result="OPEN",
                pnl_ticks=None,
                pnl_dollars=None,
            )
        except Exception as exc:
            logger.exception("Tradovate execute_bracket failed: %s", exc)
            return self._cancelled_fill(order, "TRADOVATE_ORDER_ERROR")

    # ordStatus values that mean a child order is dead (NOT protecting the position).
    _DEAD_ORDER_STATUSES = {"rejected", "canceled", "cancelled", "expired"}

    def _verify_bracket_children(
        self, stop_id, target_id, order: BracketOrder,
        retries: int = 3, delay: float = 0.5,
    ) -> tuple[bool, bool]:
        """Confirm the OSO's stop + target child orders are live.

        Tradovate's placeOSO returns oso1Id (target/Limit) and oso2Id (stop/Stop)
        — their presence is the broker's first acknowledgement the children
        exist, but we still require /order/item confirmation. A child id is
        live only when /order/item returns a non-dead status. A missing id or
        unreadable child status fails closed so the naked-position handler
        auto-flattens instead of trusting an unverified bracket.
        """
        return self._child_live(stop_id, retries, delay), self._child_live(target_id, retries, delay)

    def _child_live(self, order_id, retries: int, delay: float) -> bool:
        """True if a bracket child order exists and is not explicitly dead."""
        if not order_id:
            return False  # OSO did not return this child → it never placed
        for attempt in range(max(1, retries)):
            try:
                o = self._get(f"/order/item?id={order_id}")
                status = str(o.get("ordStatus", "")).lower()
                if status in self._DEAD_ORDER_STATUSES:
                    logger.error(
                        "bracket child id=%s DEAD status=%s reason=%s order=%s",
                        order_id, status,
                        o.get("rejectReason") or o.get("text") or o.get("cxlRejReason"),
                        o,
                    )
                    return False
                if status:  # any other known status (Working/Pending/Filled/...) = live
                    return True
            except Exception as exc:
                logger.warning("bracket verify: /order/item id=%s failed: %s", order_id, exc)
            if attempt + 1 < retries:
                time.sleep(delay)
        # OSO returned the id but we couldn't read its status. Fail closed:
        # unverified protection is treated as missing protection.
        return False

    def _handle_naked_position(
        self, order: BracketOrder, qty: int, *, stop_ok: bool, target_ok: bool,
    ) -> Fill:
        """Entry filled without a confirmed bracket: alert, then flatten NOW.

        Returns a CANCELLED Fill — no protected position remains. The realized
        slippage of the safety-flatten is the price of never holding naked risk.
        """
        self._alert_naked_position(order, stop_ok=stop_ok, target_ok=target_ok)
        close_confirmed = False
        try:
            flat = self.flatten_position()  # cancel-all + market liquidate
            # Only a CONFIRMED close (close_sent, per the failure-checked flatten)
            # lets us claim flat. cancelled_orders alone is NOT a close.
            close_confirmed = bool(flat.get("close_sent"))
            if close_confirmed:
                logger.error("NAKED POSITION auto-flattened for %s: %s", order.instrument, flat)
            else:
                logger.critical(
                    "NAKED POSITION flatten produced no confirmed close for %s — "
                    "treating as STILL OPEN; MANUAL VERIFICATION NEEDED: %s",
                    order.instrument, flat,
                )
        except Exception as exc:
            logger.critical("CRITICAL: naked-position flatten FAILED for %s: %s", order.instrument, exc)
            close_confirmed = False
        # Fail CLOSED: if we could not confirm the close, assume the position is
        # LIVE. Returning OPEN makes the runner track it (blocks new trades, keeps
        # trying to resolve, surfaces it) instead of falsely booking it flat.
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=qty,
            entry_price=order.entry,
            exit_price=None,
            exit_reason=(
                "NAKED_BRACKET_AUTO_FLATTENED" if close_confirmed
                else "NAKED_FLATTEN_UNCONFIRMED"
            ),
            result="CANCELLED" if close_confirmed else "OPEN",
            pnl_ticks=None,
            pnl_dollars=None,
        )

    def _alert_naked_position(self, order: BracketOrder, *, stop_ok: bool, target_ok: bool) -> None:
        """Log + Discord-alert that an entry filled without a confirmed bracket."""
        missing = [name for name, ok in (("STOP", stop_ok), ("TARGET", target_ok)) if not ok]
        msg = (
            f"🚨 NAKED POSITION: {order.direction} {order.contracts}x {order.instrument} "
            f"entry filled but bracket child(ren) NOT confirmed live: {', '.join(missing)}. "
            f"Auto-flattening NOW (cancel-all + market close). Verify flat in Tradovate."
        )
        logger.error(msg)
        try:
            from config.settings import load_config
            from notifications.system_notifier import notify_system
            notify_system(msg, config=load_config())
        except Exception as exc:
            logger.warning("naked-position Discord alert failed: %s", exc)

    def get_position_snapshot(self) -> tuple[bool, Optional[Position]]:
        """Return ``(confirmed, position)`` from a direct Tradovate API read.

        ``confirmed=True, position=None`` is the only definitive-flat result.
        Authentication failures, API errors, and malformed responses return
        ``confirmed=False`` so safety callers never mistake uncertainty for flat.
        """
        try:
            if not self._authenticate():
                return False, self._last_position
            data = self._get("/position/list")
            if not isinstance(data, list):
                return False, self._last_position
            for pos in data:
                net = pos.get("netPos", 0)
                if net == 0:
                    continue
                direction = "LONG" if net > 0 else "SHORT"
                entry = pos.get("netPrice") or pos.get("avgPrice") or 0.0
                # contractId is an integer — look up the name from the contract cache
                contract_id = pos.get("contractId")
                instrument = self._contract_id_to_name(contract_id) or (
                    self._last_position.instrument if self._last_position else "MES"
                )
                p = Position(
                    instrument=instrument,
                    direction=direction,
                    entry_price=float(entry),
                    stop=self._last_position.stop if self._last_position else 0.0,
                    target=self._last_position.target if self._last_position else 0.0,
                    quantity=abs(int(net)),
                    open=True,
                )
                self._last_position = p
                return True, p
            self._last_position = None
            self._last_order_ids = None
            return True, None
        except Exception as exc:
            logger.warning("Tradovate get_position failed: %s", exc)
            cached = self._last_position if (self._last_position and self._last_position.open) else None
            return False, cached

    def get_position(self) -> Optional[Position]:
        """Query Tradovate for an open position, retaining the legacy interface."""
        _, position = self.get_position_snapshot()
        return position

    def _contract_id_to_name(self, contract_id: int | None) -> str | None:
        """Reverse-lookup contract name from ID. Falls back to None."""
        if contract_id is None:
            return None
        try:
            data = self._get(f"/contract/item?id={contract_id}")
            name = data.get("name", "") or data.get("root", "")
            # Strip expiry suffix — e.g. "MESM26" → "MES"
            for root in ("MES", "ES", "MNQ", "NQ", "MGC", "MCL"):
                if str(name).upper().startswith(root):
                    return root
        except Exception:
            pass
        return None

    def _cancel_working_orders(self) -> int:
        """Cancel every Working/Pending order on the account, one by one.

        Tradovate has NO /order/cancelallorders endpoint (404) — you must list
        orders and cancel each via /order/cancelorder. Returns count cancelled.
        """
        cancelled = 0
        try:
            orders = self._get("/order/list")
        except Exception as exc:
            logger.warning("cancel: /order/list failed: %s", exc)
            return 0
        live = {"working", "pending", "accepted", "suspended"}
        for o in (orders if isinstance(orders, list) else []):
            if self._account_id is not None and o.get("accountId") not in (None, self._account_id):
                continue
            if str(o.get("ordStatus", "")).lower() not in live:
                continue
            oid = o.get("id")
            if not oid:
                continue
            try:
                self._post("/order/cancelorder", {"orderId": oid})
                cancelled += 1
            except Exception as exc:
                logger.warning("cancel: /order/cancelorder id=%s failed: %s", oid, exc)
        return cancelled

    def cancel_all(self) -> None:
        """Cancel all open orders."""
        try:
            if not self._authenticate():
                return
            n = self._cancel_working_orders()
            logger.info("Tradovate: cancelled %d working order(s)", n)
        except Exception as exc:
            logger.warning("Tradovate cancel_all failed: %s", exc)
        self._last_position = None
        self._last_order_ids = None

    def flatten_position(self) -> dict:
        """Liquidate any open position at market, then cancel working orders."""
        result: dict = {"cancelled_orders": False, "close_sent": False, "position_was": None}
        try:
            # ── Safety: same live-env guard as execute_bracket ──
            if self.config.env == "live":
                live_enabled = os.getenv("LIVE_TRADING_ENABLED", "false").strip().lower()
                if live_enabled != "true":
                    logger.error("BLOCKED flatten: TRADOVATE_ENV=live but LIVE_TRADING_ENABLED=%s", live_enabled)
                    result["error"] = "LIVE_TRADING_NOT_ENABLED"
                    return result

            if not self._authenticate():
                result["error"] = "Tradovate not authenticated"
                return result

            # Step 1 — re-poll position and liquidate first. Canceling bracket
            # children before the close can briefly make a real position naked.
            pos = self.get_position()
            if pos and pos.open:
                result["position_was"] = {
                    "instrument": pos.instrument,
                    "direction": pos.direction,
                    "qty": pos.quantity,
                }
                # liquidateposition requires integer contractId, not symbol string
                try:
                    contract_id = self._find_contract_id(pos.instrument)
                except Exception as exc:
                    logger.warning("Could not resolve contract ID for liquidation: %s", exc)
                    contract_id = None
                if contract_id is None:
                    result["error"] = f"Could not resolve contract ID for {pos.instrument} — position not closed"
                    return result
                liq = self._post("/order/liquidateposition", {
                    "accountId": self._account_id,
                    "contractId": contract_id,
                    "admin": False,
                })
                logger.info("Tradovate liquidateposition response: %s", liq)
                # Tradovate can return HTTP 200 with a failure body (no exception),
                # so a clean POST does NOT mean the position closed. Only report
                # close_sent on a response with no failure marker — fail closed.
                fail = None
                if isinstance(liq, dict):
                    fail = liq.get("failureReason") or liq.get("failureText") or liq.get("errorText")
                if fail:
                    logger.error(
                        "Tradovate liquidate REJECTED for %s: %s — position NOT closed",
                        pos.instrument, fail,
                    )
                    result["close_sent"] = False
                    result["error"] = f"liquidate rejected: {fail}"
                else:
                    result["close_sent"] = True

            # Step 2 — after liquidation request, cancel any remaining working
            # orders (including bracket children that may still be live).
            try:
                n = self._cancel_working_orders()
                result["cancelled_orders"] = n > 0
                result["cancelled_count"] = n
            except Exception as exc:
                logger.warning("Tradovate flatten cancel step failed: %s", exc)
        except Exception as exc:
            logger.exception("Tradovate flatten_position failed: %s", exc)
            result["error"] = str(exc)
        self._last_position = None
        self._last_order_ids = None
        return result

    def resolve_position(self) -> Optional[Fill]:
        """Check if a bracket child order (stop or target) has filled."""
        if not self._last_position or not self._last_position.open:
            return None
        last = self._last_position
        try:
            if not self._authenticate():
                # Can't reach Tradovate — leave the position open and let the
                # escalation counter flag a true orphan. Never book a guess.
                self._resolve_fail_count += 1
                return None
            # Resolve OUR contract id so closure is judged on THIS instrument only.
            # A live position on a different contract (e.g. MNQ open while we
            # resolve MES) must not read as "still open" for this one.
            try:
                our_cid = self._find_contract_id(last.instrument)
            except Exception:
                our_cid = None
            positions = self._get("/position/list")
            positions = positions if isinstance(positions, list) else []
            our_open = False
            for p in positions:
                if (p.get("netPos", 0) or 0) == 0:
                    continue  # flat line item
                cid = p.get("contractId")
                if our_cid is not None and cid is not None and cid != our_cid:
                    continue  # a different instrument's position — ignore
                our_open = True
                break
            if our_open:
                self._resolve_fail_count = 0  # successful check — genuinely open
                return None
            # ── Our contract is FLAT on Tradovate → the OSO bracket closed it. ──
            # ── Determine WHICH bracket child closed the position, by matching our
            # journaled target/stop prices against the FILLED Limit/Stop orders for
            # THIS contract. Never price the exit from "last account fill" — with
            # overlapping orders that grabs an unrelated entry and fabricates wins
            # (the 30208.75-on-two-trades bug).
            instrument = last.instrument
            tick_size = _TICK_SIZE.get(instrument, 0.25)
            tick_value = _TICK_VALUE.get(instrument, 1.25)
            tol = tick_size * 2
            # Use /fill/list (reliably carries `price`; /order/list often omits the
            # limit price). Match the EXIT fill to our journaled target/stop — never
            # "the last fill", which grabs an unrelated entry and fabricates wins
            # (the 30208.75-on-two-trades bug).
            fills = self._get(f"/fill/list?accountId={self._account_id}")
            ours = [
                f for f in (fills if isinstance(fills, list) else [])
                if (our_cid is None or f.get("contractId") == our_cid) and f.get("price") is not None
            ]

            def _wavg(group: list) -> float:
                """Quantity-weighted average fill price (averages partial fills).

                A 2-lot can fill in two prints at different prices; the dollar-
                accurate entry/exit is the size-weighted mean, not one leg.
                Falls back to a simple mean when `qty` is absent.
                """
                qtot = sum(abs(float(f.get("qty") or 0)) for f in group)
                if qtot > 0:
                    return sum(float(f["price"]) * abs(float(f.get("qty") or 0)) for f in group) / qtot
                return sum(float(f["price"]) for f in group) / len(group)

            exit_price = entry_fill_px = exit_reason = None
            target_hit = stop_hit = False

            # ── Preferred: scope fills to OUR exact OSO order ids ──
            # Ties each fill to the order we placed, so partial fills average
            # correctly AND an overlapping trade's fill at a similar price can
            # never be mistaken for ours. Only engages when the live fills carry
            # orderId and belong to this instrument; otherwise we price-match.
            ids = self._last_order_ids
            if (ids and ids.get("instrument") == instrument
                    and any(f.get("orderId") is not None for f in ours)):
                by_order = lambda oid: [f for f in ours if oid is not None and f.get("orderId") == oid]
                target_fills, stop_fills = by_order(ids.get("target")), by_order(ids.get("stop"))
                if target_fills:
                    exit_price, target_hit, exit_reason = _wavg(target_fills), True, "TARGET_HIT"
                elif stop_fills:
                    exit_price, stop_hit, exit_reason = _wavg(stop_fills), True, "STOP_HIT"
                if exit_reason is not None:
                    entry_fills = by_order(ids.get("entry"))
                    entry_fill_px = _wavg(entry_fills) if entry_fills else last.entry_price

            # ── Fallback: price-match against journaled target/stop ──
            if exit_reason is None:
                exit_fill = None
                for f in ours:
                    px = float(f["price"])
                    if last.target is not None and abs(px - last.target) <= tol:
                        exit_fill, target_hit = f, True
                        break
                    if last.stop is not None and abs(px - last.stop) <= tol:
                        exit_fill, stop_hit = f, True
                        break
                if exit_fill is None:
                    # Flat but no fill matches our bracket prices yet (settle window) or
                    # closed manually/liquidated. Retry a few bars; then book BREAKEVEN
                    # at entry rather than fabricate a win from an unrelated fill.
                    self._resolve_fail_count += 1
                    if self._resolve_fail_count < 3:
                        logger.warning(
                            "resolve_position: %s flat but no matching bracket fill yet "
                            "(attempt %d) — retrying", instrument, self._resolve_fail_count,
                        )
                        return None
                    entry_fill_px = last.entry_price
                    exit_price = last.entry_price
                    exit_reason = "FORCE_CLOSE_UNMATCHED"
                else:
                    exit_price = float(exit_fill["price"])
                    exit_reason = "TARGET_HIT" if target_hit else "STOP_HIT"
                    # Actual entry fill = the fill closest to our intended entry among the
                    # rest (so P&L matches Tradovate to the dollar, not the planned entry).
                    others = [float(f["price"]) for f in ours if f is not exit_fill]
                    entry_fill_px = (
                        min(others, key=lambda p: abs(p - last.entry_price)) if others else last.entry_price
                    )

            signed_ticks = (
                (exit_price - entry_fill_px) if last.direction == "LONG"
                else (entry_fill_px - exit_price)
            ) / tick_size
            pnl_dollars = round(signed_ticks * tick_value * last.quantity, 2)
            result = "WIN" if pnl_dollars > 0 else "LOSS" if pnl_dollars < 0 else "BREAKEVEN"
            self._last_position = None
            self._last_order_ids = None
            self._resolve_fail_count = 0
            self._position_opened_at = None
            return Fill(
                instrument=instrument,
                direction=last.direction,
                contracts=last.quantity,
                entry_price=entry_fill_px,
                exit_price=exit_price,
                exit_reason=exit_reason,
                result=result,
                pnl_ticks=round(signed_ticks, 2),
                pnl_dollars=pnl_dollars,
            )
        except Exception as exc:
            self._resolve_fail_count += 1
            hours_open = (
                (time.time() - self._position_opened_at) / 3600
                if self._position_opened_at else 0
            )
            # Escalate: warn every failure, error after 5 consecutive failures
            if self._resolve_fail_count >= 5:
                logger.error(
                    "ORPHANED POSITION: resolve_position has failed %d consecutive times "
                    "(%.1fh since entry, instrument=%s). Token may be expired and "
                    "not refreshing. Manual check required. Error: %s",
                    self._resolve_fail_count,
                    hours_open,
                    self._last_position.instrument if self._last_position else "unknown",
                    exc,
                )
            else:
                logger.warning(
                    "Tradovate resolve_position failed (attempt %d, %.1fh open): %s",
                    self._resolve_fail_count, hours_open, exc,
                )
            return None

    def get_account_balance(self) -> Optional[float]:
        try:
            if not self._authenticate():
                return None
            # Resolve account ID if not yet known
            if self._account_id is None:
                self._resolve_account_id()
            if self._account_id is None:
                logger.warning("Tradovate get_account_balance: account ID unknown")
                return None
            # Try cash-balance snapshot first; field names and shape vary by API version
            try:
                data = self._get(f"/cashBalance/getCashBalanceSnapshot?accountId={self._account_id}")
                # Response may be a list of snapshots or a single dict
                rows = data if isinstance(data, list) else [data]
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    for key in ("totalCashValue", "cashBalance", "netLiq", "balance", "amount"):
                        val = row.get(key)
                        if val is not None:
                            return float(val)
            except Exception:
                pass
            # Fallback: account list carries netLiq directly
            accounts = self._get("/account/list")
            if isinstance(accounts, list):
                for acct in accounts:
                    if acct.get("id") == self._account_id:
                        for key in ("netLiq", "balance", "cashBalance"):
                            val = acct.get(key)
                            if val is not None:
                                return float(val)
            return None
        except Exception as exc:
            logger.warning("Tradovate get_account_balance failed: %s", exc)
            return None

    def get_account_summary(self) -> dict:
        """Live account truth for the dashboard mirror: equity, open/realized P&L,
        and the current open position — read straight from Tradovate (the source
        of truth), so the UI never diverges from the broker. Fail-soft."""
        out: dict = {
            "ok": False, "env": self.config.env, "account_id": None,
            "equity": None, "open_pnl": None, "realized_pnl": None, "position": None,
        }
        try:
            if not self._authenticate():
                out["error"] = "not_authenticated"
                return out
            if self._account_id is None:
                self._resolve_account_id()
            out["account_id"] = self._account_id
            d: dict = {}
            try:
                snap = self._get(f"/cashBalance/getCashBalanceSnapshot?accountId={self._account_id}")
                rows = snap if isinstance(snap, list) else [snap]
                d = next((r for r in rows if isinstance(r, dict)), {})
            except Exception:
                d = {}

            def _pick(*keys):
                for k in keys:
                    v = d.get(k)
                    if v is not None:
                        try:
                            return float(v)
                        except (TypeError, ValueError):
                            pass
                return None

            out["equity"] = _pick("totalCashValue", "netLiq", "cashBalance", "balance", "amount")
            out["open_pnl"] = _pick("openPnL", "openPnl", "unrealizedPnL", "unrealizedPnl")
            out["realized_pnl"] = _pick("realizedPnL", "realizedPnl", "totalPnL", "totalPnl")
            if out["equity"] is None:
                out["equity"] = self.get_account_balance()
            try:
                pos = self.get_position()
                if pos and pos.open:
                    out["position"] = {
                        "instrument": pos.instrument, "direction": pos.direction,
                        "qty": pos.quantity, "entry": pos.entry_price,
                    }
            except Exception:
                pass
            out["ok"] = out["equity"] is not None
        except Exception as exc:
            logger.warning("Tradovate get_account_summary failed: %s", exc)
            out["error"] = str(exc)
        return out

    def get_quote(self, instrument: str = "MES") -> dict:
        """Return last known price for the instrument.

        Tradovate's REST API has no live quote endpoints — market data is
        WebSocket-only. This method authenticates to confirm connectivity and
        returns the contract name, but price comes from the latest webhook
        payload (set via set_last_price) rather than a direct API call.
        """
        try:
            if not self._authenticate():
                return {"ok": False, "error": "not_authenticated"}
            root = instrument.replace("1!", "").upper()

            # Resolve contract name for display
            symbol = root
            try:
                contract_id = self._find_contract_id(root)
                contract = self._get(f"/contract/item?id={contract_id}")
                symbol = contract.get("name", root)
            except Exception:
                pass

            # Price comes from latest TradingView webhook bar close
            price = self._last_price.get(root)
            return {
                "ok": price is not None,
                "instrument": instrument,
                "symbol": symbol,
                "price": price,
                "source": "tradingview_bar_close",
                "error": None if price is not None else "no_bar_received_yet",
            }
        except Exception as exc:
            logger.warning("Tradovate get_quote failed: %s", exc)
            return {"ok": False, "error": str(exc)}

    def get_broker_name(self) -> str:
        return f"TradovateBroker({'live' if self.is_live else 'demo'})"

    def get_capabilities(self) -> BrokerCapabilities:
        balance = self.get_account_balance()
        return BrokerCapabilities(
            broker_name=self.get_broker_name(),
            asset_class="futures",
            account_mode="live" if self.is_live else "demo",
            starting_capital=balance,
            available_cash=balance,
            estimated_margin_required=None,
            max_dollars_risk_per_trade=None,
            supports_brackets=True,
            supports_options=False,
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _cancelled_fill(order: BracketOrder, reason: str) -> Fill:
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=getattr(order, "contracts", 1) or 1,
            entry_price=order.entry,
            exit_price=None,
            exit_reason=reason,
            result="CANCELLED",
            pnl_ticks=None,
            pnl_dollars=None,
        )
