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

# Per-instrument tick specs
_TICK_SIZE: dict[str, float] = {
    "MES": 0.25, "ES": 0.25, "MNQ": 0.25, "NQ": 0.25, "MGC": 0.10, "MCL": 0.01,
}
_TICK_VALUE: dict[str, float] = {
    "MES": 1.25, "ES": 12.50, "MNQ": 0.50, "NQ": 5.00, "MGC": 1.00, "MCL": 1.00,
}


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

    @property
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

    def get_quote(self, instrument: str = "MES") -> dict:
        """Get live price snapshot from Tradovate REST API."""
        try:
            if not self._authenticate():
                return {"ok": False, "error": "not_authenticated"}
            root = instrument.replace("1!", "").upper()
            # Find front month contract
            contract_id = None
            symbol = root
            try:
                contract = self._get(f"/contract/find?name={root}")
                symbol = contract.get("name", root)
                contract_id = contract.get("id")
            except Exception as ce:
                logger.warning("Tradovate contract/find failed for %s: %s", root, ce)

            # Get quote via contract ID or name
            quote: dict = {}
            try:
                if contract_id:
                    quote = self._get(f"/quote/item?id={contract_id}")
                else:
                    quote = self._get(f"/quote/find?name={symbol}")
            except Exception as qe:
                return {"ok": False, "error": f"quote_fetch_failed: {qe}", "symbol": symbol}

            bid = quote.get("bid")
            ask = quote.get("ofr") or quote.get("ask")
            last = quote.get("last")
            prev_close = quote.get("prevClose")
            price = last if last is not None else prev_close

            return {
                "ok": True,
                "instrument": instrument,
                "symbol": symbol,
                "price": price,
                "bid": bid,
                "ask": ask,
                "spread": round(float(ask) - float(bid), 2) if ask and bid else None,
                "last": last,
                "prev_close": prev_close,
                "ts": quote.get("timestamp"),
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
