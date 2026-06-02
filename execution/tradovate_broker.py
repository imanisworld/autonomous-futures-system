"""Tradovate REST API broker adapter.

Connects to Tradovate's cloud API — no local Gateway process required.
Works from any server (Hetzner, Railway) with outbound HTTPS.

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

# MES contract spec
_MES_TICK  = 0.25   # minimum price increment
_MES_DOLLAR_PER_TICK = 1.25  # $1.25 per tick per contract


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
            cid=int(os.getenv("TRADOVATE_API_KEY_ID", "0")),
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
        """Find the front-month contract ID for MES (or other CME micro)."""
        root = instrument.replace("1!", "").upper()
        data = self._get(f"/contract/find?name={root}")
        if isinstance(data, dict) and "id" in data:
            return int(data["id"])
        # Try searching
        results = self._get(f"/contractLibrary/search?text={root}&live=false")
        if isinstance(results, list) and results:
            return int(results[0]["id"])
        raise ValueError(f"Contract not found for {instrument}")

    # ── BrokerInterface implementation ────────────────────────────────────────

    def is_live(self) -> bool:
        return self.config.env == "live"

    def execute_bracket(self, order: BracketOrder) -> Fill:
        """Place entry market order with attached stop and target (OSO bracket)."""
        try:
            if not self._authenticate():
                return self._cancelled_fill(order, "TRADOVATE_AUTH_FAILED")

            contract_id = self._find_contract_id(order.instrument)
            action = "Buy" if order.direction == "LONG" else "Sell"
            close_action = "Sell" if order.direction == "LONG" else "Buy"
            qty = max(1, int(order.contracts or 1))

            # Place bracket via OSO (On Submit, send bracket child orders)
            body = {
                "accountSpec": self.config.username,
                "accountId": self._account_id,
                "action": action,
                "symbol": order.instrument.replace("1!", ""),
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
            order_id = result.get("orderId") or (result.get("orderStatus", {}) or {}).get("orderId")
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
                p = Position(
                    instrument=pos.get("contractId", "MES"),
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
                liq = self._post("/order/liquidateposition", {
                    "accountId": self._account_id,
                    "contractId": pos.instrument,
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
                return None  # still open
            # Position is gone — look at fill history to determine outcome
            fills = self._get(f"/fill/list?accountId={self._account_id}")
            if isinstance(fills, list) and fills:
                last_fill = fills[-1]
                fill_price = float(last_fill.get("price", self._last_position.stop))
            else:
                fill_price = self._last_position.stop

            entry = self._last_position.entry_price
            direction = self._last_position.direction
            qty = self._last_position.quantity
            ticks = (fill_price - entry) / _MES_TICK if direction == "LONG" else (entry - fill_price) / _MES_TICK
            pnl_dollars = ticks * _MES_DOLLAR_PER_TICK * qty
            result = "WIN" if pnl_dollars > 0 else "LOSS"
            exit_reason = "TARGET_HIT" if pnl_dollars > 0 else "STOP_HIT"
            self._last_position = None
            return Fill(
                instrument=self._last_position.instrument if self._last_position else "MES",
                direction=direction,
                contracts=qty,
                entry_price=entry,
                exit_price=fill_price,
                exit_reason=exit_reason,
                result=result,
                pnl_ticks=ticks,
                pnl_dollars=pnl_dollars,
            )
        except Exception as exc:
            logger.warning("Tradovate resolve_position failed: %s", exc)
            return None

    def get_account_balance(self) -> Optional[float]:
        try:
            if not self._authenticate():
                return None
            data = self._get(f"/cashBalance/getCashBalanceSnapshot?accountId={self._account_id}")
            return float(data.get("totalCashValue", data.get("cashBalance", 0)))
        except Exception as exc:
            logger.warning("Tradovate get_account_balance failed: %s", exc)
            return None

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
