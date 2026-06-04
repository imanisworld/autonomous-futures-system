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
            exit_fill = None
            target_hit = stop_hit = False
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
