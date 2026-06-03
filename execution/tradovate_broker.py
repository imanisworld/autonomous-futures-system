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

# Per-instrument tick specs
_TICK_SIZE: dict[str, float] = {
    "MES": 0.25, "ES": 0.25, "MNQ": 0.25, "NQ": 0.25, "MGC": 0.10, "MCL": 0.01,
}
_TICK_VALUE: dict[str, float] = {
    "MES": 1.25, "ES": 12.50, "MNQ": 0.50, "NQ": 5.00, "MGC": 1.00, "MCL": 1.00,
}


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


# ─── Broker ───────────────────────────────────────────────────────────────────

class TradovateBroker(BrokerInterface):
    """BrokerInterface implementation for Tradovate (demo or live)."""

    def __init__(self, config: Optional[TradovateConfig] = None) -> None:
        self.config = config or TradovateConfig.from_env()
        self._token: Optional[_Token] = None
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._last_position: Optional[Position] = None
        self._account_id: Optional[int] = None
        self._contract_cache: dict[str, int] = {}  # root symbol → contract ID
        self._contract_symbol_cache: dict[str, str] = {}  # root → front-month symbol (e.g. MESM6)
        self._resolve_fail_count: int = 0          # consecutive resolve_position failures
        self._position_opened_at: Optional[float] = None  # time.time() when bracket placed
        self._last_price: dict[str, float] = {}   # root symbol → last bar close from TV webhook
        # Set after execute_bracket: did the protective stop+target children verify live?
        # None = not checked, True = both confirmed working, False = one/both missing (naked risk).
        self._last_bracket_confirmed: Optional[bool] = None

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self) -> bool:
        """Obtain or refresh access token. Returns True on success."""
        if self._token and self._token.is_valid(self.config.token_refresh_buffer):
            return True

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
                return False
            # Tradovate returns expirationTime as ISO string e.g. "2026-06-02T18:31:28+00:00"
            expiry_raw = data.get("expirationTime")
            if expiry_raw:
                try:
                    from datetime import datetime, timezone
                    if isinstance(expiry_raw, str):
                        # Parse ISO 8601 — handle both +00:00 and Z suffixes
                        expiry_str = expiry_raw.replace("Z", "+00:00")
                        expires_at = datetime.fromisoformat(expiry_str).timestamp()
                    else:
                        # Fallback: treat as milliseconds if it's a number
                        expires_at = float(expiry_raw) / 1000.0
                except Exception:
                    expires_at = time.time() + 3600
            else:
                expires_at = time.time() + 3600
            self._token = _Token(access_token=token, expires_at=expires_at)
            self._session.headers["Authorization"] = f"Bearer {token}"
            logger.info("Tradovate authenticated (env=%s)", self.config.env)
            # Cache account ID
            self._resolve_account_id()
            return True
        except Exception as exc:
            logger.exception("Tradovate authentication error: %s", exc)
            return False

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
                    "price": float(order.target),
                },
                "bracket2": {
                    "action": close_action,
                    "orderType": "Stop",
                    "stopPrice": float(order.stop),
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
            logger.info("Tradovate bracket placed: orderId=%s instrument=%s dir=%s", order_id, order.instrument, order.direction)

            self._last_position = Position(
                instrument=order.instrument,
                direction=order.direction,
                entry_price=order.entry,
                stop=order.stop,
                target=order.target,
                quantity=qty,
                open=True,
            )
            self._position_opened_at = time.time()
            self._resolve_fail_count = 0

            # Verify BOTH protective children (stop + target) actually went live.
            # A market entry can fill while its bracket children are rejected,
            # leaving a naked position. If either is unconfirmed we do NOT leave
            # the entry exposed: alert loudly AND auto-flatten immediately —
            # a few ticks of slippage beats unbounded unprotected risk.
            stop_ok, target_ok = self._verify_bracket_children(
                contract_id=contract_id, close_action=close_action, order=order,
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

    # Order statuses that mean a protective child is live and guarding the position.
    _LIVE_ORDER_STATUSES = {"working", "pending", "accepted", "suspended"}

    def _verify_bracket_children(
        self, contract_id, close_action: str, order: BracketOrder,
        retries: int = 4, delay: float = 0.5,
    ) -> tuple[bool, bool]:
        """Detect whether a working Stop AND Limit exist on the close side.

        Pure detection — no side effects. Polls the account's orders (children
        can register a beat after the market entry fills) and returns
        (stop_ok, target_ok). On uncertainty (e.g. /order/list keeps failing) it
        returns False for the unseen side so the caller flattens — when in doubt,
        do not leave a position unprotected. Best-effort: query errors never raise.
        """
        want_action = str(close_action).capitalize()
        stop_ok = target_ok = False
        for attempt in range(max(1, retries)):
            try:
                orders = self._get("/order/list")
            except Exception as exc:
                logger.warning("bracket verify: /order/list failed (attempt %d): %s", attempt + 1, exc)
                orders = []
            stop_ok = target_ok = False
            for o in (orders if isinstance(orders, list) else []):
                if o.get("contractId") != contract_id:
                    continue
                if self._account_id is not None and o.get("accountId") not in (None, self._account_id):
                    continue
                if str(o.get("action", "")).capitalize() != want_action:
                    continue
                if str(o.get("ordStatus", "")).lower() not in self._LIVE_ORDER_STATUSES:
                    continue
                otype = str(o.get("orderType", "")).lower()
                if otype == "stop":
                    stop_ok = True
                elif otype == "limit":
                    target_ok = True
            if stop_ok and target_ok:
                logger.info("bracket verify: stop+target confirmed live for %s", order.instrument)
                return True, True
            if attempt + 1 < retries:
                time.sleep(delay)
        return stop_ok, target_ok

    def _handle_naked_position(
        self, order: BracketOrder, qty: int, *, stop_ok: bool, target_ok: bool,
    ) -> Fill:
        """Entry filled without a confirmed bracket: alert, then flatten NOW.

        Returns a CANCELLED Fill — no protected position remains. The realized
        slippage of the safety-flatten is the price of never holding naked risk.
        """
        self._alert_naked_position(order, stop_ok=stop_ok, target_ok=target_ok)
        try:
            flat = self.flatten_position()  # cancel-all + market liquidate
            if flat.get("close_sent") or flat.get("cancelled_orders"):
                logger.error("NAKED POSITION auto-flattened for %s: %s", order.instrument, flat)
            else:
                logger.critical(
                    "NAKED POSITION flatten produced no close for %s — MANUAL INTERVENTION NEEDED: %s",
                    order.instrument, flat,
                )
        except Exception as exc:
            logger.critical("CRITICAL: naked-position flatten FAILED for %s: %s", order.instrument, exc)
        return Fill(
            instrument=order.instrument,
            direction=order.direction,
            contracts=qty,
            entry_price=order.entry,
            exit_price=None,
            exit_reason="NAKED_BRACKET_AUTO_FLATTENED",
            result="CANCELLED",
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

    def get_position(self) -> Optional[Position]:
        """Query Tradovate for open positions."""
        try:
            if not self._authenticate():
                return self._last_position
            data = self._get("/position/list")
            for pos in (data if isinstance(data, list) else []):
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
                return p
        except Exception as exc:
            logger.warning("Tradovate get_position failed: %s", exc)
        return self._last_position if (self._last_position and self._last_position.open) else None

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

    def cancel_all(self) -> None:
        """Cancel all open orders."""
        try:
            if not self._authenticate():
                return
            self._post("/order/cancelallorders", {"accountId": self._account_id})
            logger.info("Tradovate: all orders cancelled")
        except Exception as exc:
            logger.warning("Tradovate cancel_all failed: %s", exc)
        self._last_position = None

    def flatten_position(self) -> dict:
        """Cancel all orders then liquidate any open position at market."""
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

            # Step 1 — cancel all pending orders
            try:
                self._post("/order/cancelallorders", {"accountId": self._account_id})
                result["cancelled_orders"] = True
            except Exception as exc:
                logger.warning("Tradovate flatten cancel step failed: %s", exc)

            # Step 2 — liquidate via Tradovate's liquidateposition endpoint
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
                result["close_sent"] = True
        except Exception as exc:
            logger.exception("Tradovate flatten_position failed: %s", exc)
            result["error"] = str(exc)
        self._last_position = None
        return result

    def resolve_position(self) -> Optional[Fill]:
        """Check if a bracket child order (stop or target) has filled."""
        if not self._last_position or not self._last_position.open:
            return None
        try:
            pos = self.get_position()
            if pos and pos.open:
                self._resolve_fail_count = 0  # successful check — reset counter
                return None  # still open
            # Position is gone — capture before clearing, then look at fill history
            last = self._last_position
            fills = self._get(f"/fill/list?accountId={self._account_id}")
            if isinstance(fills, list) and fills:
                last_fill = fills[-1]
                fill_price = float(last_fill.get("price", last.stop))
            else:
                fill_price = last.stop

            instrument = last.instrument
            tick_size = _TICK_SIZE.get(instrument, 0.25)
            tick_value = _TICK_VALUE.get(instrument, 1.25)
            ticks = (fill_price - last.entry_price) / tick_size if last.direction == "LONG" else (last.entry_price - fill_price) / tick_size
            pnl_dollars = round(ticks * tick_value * last.quantity, 2)
            result = "WIN" if pnl_dollars > 0 else "LOSS" if pnl_dollars < 0 else "BREAKEVEN"
            exit_reason = "TARGET_HIT" if pnl_dollars > 0 else "STOP_HIT"
            self._last_position = None
            self._resolve_fail_count = 0
            self._position_opened_at = None
            return Fill(
                instrument=instrument,
                direction=last.direction,
                contracts=last.quantity,
                entry_price=last.entry_price,
                exit_price=fill_price,
                exit_reason=exit_reason,
                result=result,
                pnl_ticks=round(ticks, 2),
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
        return f"TradovateBroker({'live' if self.is_live() else 'demo'})"

    def get_capabilities(self) -> BrokerCapabilities:
        balance = self.get_account_balance()
        return BrokerCapabilities(
            broker_name=self.get_broker_name(),
            asset_class="futures",
            account_mode="live" if self.is_live() else "demo",
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
