"""Minimal fake Tradovate order book for the phase-1 fault-injection tests.

``FakeBook`` stands in for the broker's HTTP layer only: the real
``TradovateBroker`` (and, for runner-level cases, the real runner) runs
unchanged on top of it. Faults are injected per endpoint AFTER the book has
applied the request, so "the broker did it but we never heard back" is
representable.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Optional

import execution.tradovate_broker as tb
import execution.tradovate_supervisor as supervisor
from execution.tradovate_broker import TradovateBroker, TradovateConfig

ACCOUNT_ID = 999
CONTRACT_ID = 4242
ENTRY_ID, TARGET_ID, STOP_ID = 111, 222, 333


class FaultSetupError(Exception):
    """The fake was not in the state the test declared. Deliberately NOT an
    AssertionError, so it can never satisfy ``xfail(raises=AssertionError)``."""


@dataclass
class FaultRecord:
    case: str
    initial_journal: str
    initial_broker: str
    injected_failure: str
    expected_safe_state: str
    actual_state: str = "?"
    new_entry_blocked: str = "?"
    journal_broker_diverge: str = "?"

    def __str__(self) -> str:
        return (
            f"\n[{self.case}]"
            f"\n  initial journal      : {self.initial_journal}"
            f"\n  initial broker       : {self.initial_broker}"
            f"\n  injected failure     : {self.injected_failure}"
            f"\n  expected safe state  : {self.expected_safe_state}"
            f"\n  actual current state : {self.actual_state}"
            f"\n  new entry blocked    : {self.new_entry_blocked}"
            f"\n  journal/broker diverge: {self.journal_broker_diverge}"
        )


class FakeBook:
    """In-memory positions / orders / fills for one account and one contract.

    place_mode: "fill" = the OSO entry fills at once (position opens);
                "rest" = the entry rests as Working (no position).
    children:   whether the OSO bracket children exist as Working orders.
    get_faults / post_faults: endpoint-prefix -> Exception (raised), callable
    (called with the path), or any other value (returned instead of the real
    response). post_faults fire AFTER the book applied the request ("it
    happened, the reply was lost"); post_rejects fire BEFORE it ("it did not
    happen"), e.g. a cancel that never took effect.
    """

    def __init__(self, place_mode: str = "fill", children: bool = True,
                 market_price: float = 5898.5):
        self.place_mode = place_mode
        self.market_price = market_price
        self.children = children
        self.net_pos = 0
        self.net_price = 0.0
        self.orders: dict[int, dict] = {}
        self.fills: list[dict] = []
        self.posts: list[tuple[str, dict]] = []
        self.gets: list[str] = []
        self.get_faults: dict[str, Any] = {}
        self.post_faults: dict[str, Any] = {}
        self.post_rejects: dict[str, Any] = {}

    # ── seeding / inspection ────────────────────────────────────────────────
    def seed_position(self, net_pos: int, price: float) -> None:
        self.net_pos, self.net_price = net_pos, price

    def working_orders(self) -> list[dict]:
        return [o for o in self.orders.values() if o["ordStatus"] == "Working"]

    def place_calls(self) -> int:
        return sum(1 for path, _ in self.posts if path == "/order/placeOSO")

    def require(self, condition: bool, what: str) -> None:
        if not condition:
            raise FaultSetupError(f"fake book not in declared state: {what}")

    def describe(self) -> str:
        return (
            f"netPos={self.net_pos} working_orders={len(self.working_orders())} "
            f"placeOSO_calls={self.place_calls()}"
        )

    # ── HTTP layer ──────────────────────────────────────────────────────────
    @staticmethod
    def _apply(fault: Any, path: str) -> Any:
        if isinstance(fault, BaseException):
            raise fault
        if callable(fault):
            return fault(path)
        return fault

    def get(self, path: str, **_: Any) -> Any:
        self.gets.append(path)
        for prefix, fault in self.get_faults.items():
            if path.startswith(prefix):
                return self._apply(fault, path)
        if path.startswith("/position/list"):
            if self.net_pos == 0:
                return []
            return [{
                "accountId": ACCOUNT_ID, "contractId": CONTRACT_ID,
                "netPos": self.net_pos, "netPrice": self.net_price,
            }]
        if path.startswith("/order/list"):
            return [dict(o) for o in self.orders.values()]
        if path.startswith("/order/item"):
            oid = int(path.split("id=")[1])
            return dict(self.orders[oid])
        if path.startswith("/fill/list"):
            return list(self.fills)
        if path.startswith("/cashBalance"):
            return [{"totalCashValue": 50000.0}]
        if path.startswith("/contract/item"):
            return {"name": "MESZ6"}
        return []

    def post(self, path: str, body: dict, **_: Any) -> Any:
        self.posts.append((path, body))
        if path in self.post_rejects:
            return self._apply(self.post_rejects[path], path)
        if path == "/order/placeOSO":
            result: Any = self._place(body)
        elif path == "/order/cancelorder":
            result = self._cancel(body)
        else:
            result = {}
        if path in self.post_faults:
            return self._apply(self.post_faults[path], path)
        return result

    def _order(self, oid: int, status: str, action: str, qty: int) -> dict:
        return {
            "id": oid, "accountId": ACCOUNT_ID, "contractId": CONTRACT_ID,
            "ordStatus": status, "action": action, "orderQty": qty,
        }

    def _place(self, body: dict) -> dict:
        action = body.get("action", "Buy")
        qty = int(body.get("orderQty") or 1)
        price = float(body.get("price") or self.market_price)
        close = "Sell" if action == "Buy" else "Buy"
        if self.place_mode == "fill":
            self.orders[ENTRY_ID] = self._order(ENTRY_ID, "Filled", action, qty)
            self.net_pos += qty if action == "Buy" else -qty
            self.net_price = price
            self.fills.append({
                "orderId": ENTRY_ID, "contractId": CONTRACT_ID, "price": price, "qty": qty,
            })
        else:
            self.orders[ENTRY_ID] = self._order(ENTRY_ID, "Working", action, qty)
        if self.children:
            self.orders[TARGET_ID] = self._order(TARGET_ID, "Working", close, qty)
            self.orders[STOP_ID] = self._order(STOP_ID, "Working", close, qty)
        return {"orderId": ENTRY_ID, "oso1Id": TARGET_ID, "oso2Id": STOP_ID}

    def _cancel(self, body: dict) -> dict:
        oid = body.get("orderId")
        if oid in self.orders and self.orders[oid]["ordStatus"] == "Working":
            self.orders[oid]["ordStatus"] = "Canceled"
        return {}


def make_broker(
    monkeypatch, book: FakeBook, *, expected_account_id: int | None = ACCOUNT_ID
) -> TradovateBroker:
    """A real TradovateBroker (demo) whose HTTP calls all go to ``book``."""
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    if expected_account_id is None:
        monkeypatch.delenv("TRADOVATE_EXPECTED_ACCOUNT_ID", raising=False)
    else:
        monkeypatch.setenv("TRADOVATE_EXPECTED_ACCOUNT_ID", str(expected_account_id))
    monkeypatch.delenv("EXPECTED_TRADOVATE_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("BROKER", raising=False)  # keeps runner alerts off
    # The client-order-id registry is CLASS-level (process-wide); isolate tests.
    TradovateBroker._reset_client_order_registry()
    b = TradovateBroker(config=TradovateConfig.from_env())
    b._account_id = ACCOUNT_ID
    b._resolve_fail_count = 0
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(b, "_find_contract_id", lambda inst: CONTRACT_ID)
    monkeypatch.setattr(b, "_get", book.get)
    monkeypatch.setattr(b, "_post", book.post)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    monkeypatch.setattr(tb.time, "sleep", lambda *a, **k: None)
    return b


# ── runner-level helpers (reuse the deterministic MES path from test_webhook) ──
def real_broker_cfg(config, *, working_order_recheck: bool):
    from tests.test_webhook import _mes_real_broker_cfg
    return replace(
        _mes_real_broker_cfg(config), working_order_recheck_enabled=working_order_recheck,
    )


def mes_payload(timestamp: Optional[str] = None):
    from tests.test_webhook import _mes_orb_payload
    p = _mes_orb_payload()
    return p.model_copy(update={"timestamp": timestamp}) if timestamp else p


def run_alert(monkeypatch, broker, cfg, log_dir: Path, payload) -> dict:
    from webhook import runner
    monkeypatch.setattr(runner, "_make_broker", lambda **kw: broker)
    return runner.process_alert(payload, config=cfg, log_dir=str(log_dir))


def journal_rows(log_dir: Path) -> list[dict]:
    import json
    rows: list[dict] = []
    for p in sorted(Path(log_dir).glob("journal_*.jsonl")):
        rows += [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    return rows


def journal_open(log_dir: Path) -> bool:
    """Journal's own view: is a position open (latest journal day)?"""
    from datetime import date
    from journal.journal_logger import JournalLogger
    files = sorted(Path(log_dir).glob("journal_*.jsonl"))
    if not files:
        return False
    day = date.fromisoformat(files[-1].stem[len("journal_"):])
    return JournalLogger(log_dir=str(log_dir)).get_daily_state(day).has_open_position


def outcomes(log_dir: Path) -> list[dict]:
    return [r["outcome"] for r in journal_rows(log_dir) if r.get("type") == "OUTCOME"]


def patch_broker_class(monkeypatch, book: FakeBook) -> None:
    """Route TradovateBroker instances the runner builds itself (e.g. the
    per-bar resolver at webhook/runner.py ~1370) to ``book`` as well."""
    monkeypatch.setattr(TradovateBroker, "_authenticate", lambda self: True)
    monkeypatch.setattr(TradovateBroker, "_find_contract_id", lambda self, inst: CONTRACT_ID)
    monkeypatch.setattr(TradovateBroker, "_get", lambda self, path, **k: book.get(path))
    monkeypatch.setattr(TradovateBroker, "_post", lambda self, path, body, **k: book.post(path, body))
