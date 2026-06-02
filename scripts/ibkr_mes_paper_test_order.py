#!/usr/bin/env python3
"""Place one MES paper bracket test order through local IBKR Gateway/TWS.

This script is intentionally scoped to the IBKR paper endpoint:
127.0.0.1:7497. It aborts if the connected account does not look like an
IBKR paper account.
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Iterable

from execution.ibkr_broker import front_month_contract


HOST = "127.0.0.1"
PAPER_PORT = 7497
CLIENT_ID = 91
SYMBOL = "MES"
EXCHANGE = "CME"
CURRENCY = "USD"
QTY = 1
TICK_SIZE = 0.25
STOP_POINTS = 4.0
TARGET_POINTS = 12.0


def round_to_tick(price: float, tick_size: float = TICK_SIZE) -> float:
    return round(round(price / tick_size) * tick_size, 2)


def first_paper_account(accounts: Iterable[str]) -> str:
    paper_accounts = [account for account in accounts if account.upper().startswith("DU")]
    if not paper_accounts:
        raise RuntimeError(
            "No IBKR paper account found. Refusing to place an order. "
            f"Managed accounts seen: {list(accounts)!r}"
        )
    return paper_accounts[0]


def get_market_price(ib, contract) -> float:
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(3)

    price = ticker.marketPrice()
    if price is None or math.isnan(price) or price <= 0:
        bid = getattr(ticker, "bid", None)
        ask = getattr(ticker, "ask", None)
        if bid and ask and not math.isnan(bid) and not math.isnan(ask):
            price = (float(bid) + float(ask)) / 2
        else:
            last = getattr(ticker, "last", None)
            if last and not math.isnan(last):
                price = float(last)

    ib.cancelMktData(contract)

    if price is None or math.isnan(price) or price <= 0:
        raise RuntimeError("Could not get a valid MES market price from IBKR.")
    return round_to_tick(float(price))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send one MES long bracket order to IBKR paper trading."
    )
    parser.add_argument(
        "--client-id",
        type=int,
        default=CLIENT_ID,
        help=f"IBKR client id. Default: {CLIENT_ID}",
    )
    args = parser.parse_args()

    try:
        from ib_insync import IB, Future, LimitOrder, StopOrder
    except Exception as exc:
        print(f"ib_insync is required. Install it with: pip install ib_insync\n{exc}")
        return 1

    ib = IB()
    try:
        print(f"Connecting to IBKR paper endpoint {HOST}:{PAPER_PORT}...")
        ib.connect(HOST, PAPER_PORT, clientId=args.client_id, timeout=8)

        accounts = ib.managedAccounts()
        account = first_paper_account(accounts)
        print(f"Using paper account: {account}")

        contract_month = front_month_contract()
        contract = Future(
            SYMBOL,
            lastTradeDateOrContractMonth=contract_month,
            exchange=EXCHANGE,
            currency=CURRENCY,
        )
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify {SYMBOL} {contract_month} contract.")
        contract = qualified[0]

        entry = get_market_price(ib, contract)
        stop = round_to_tick(entry - STOP_POINTS)
        target = round_to_tick(entry + TARGET_POINTS)

        parent_id = ib.client.getReqId()
        take_profit_id = ib.client.getReqId()
        stop_loss_id = ib.client.getReqId()

        parent = LimitOrder(
            "BUY",
            QTY,
            entry,
            orderId=parent_id,
            transmit=False,
            account=account,
        )
        take_profit = LimitOrder(
            "SELL",
            QTY,
            target,
            orderId=take_profit_id,
            parentId=parent_id,
            transmit=False,
            account=account,
        )
        stop_loss = StopOrder(
            "SELL",
            QTY,
            stop,
            orderId=stop_loss_id,
            parentId=parent_id,
            transmit=True,
            account=account,
        )

        print(
            "Placing MES LONG paper bracket: "
            f"entry={entry}, stop={stop}, target={target}, qty={QTY}"
        )

        trades = [
            ib.placeOrder(contract, parent),
            ib.placeOrder(contract, take_profit),
            ib.placeOrder(contract, stop_loss),
        ]
        ib.sleep(5)

        print("Orders submitted:")
        for name, trade in zip(("entry", "take_profit", "stop_loss"), trades):
            order = trade.order
            status = trade.orderStatus
            print(
                f"- {name}: order_id={order.orderId}, "
                f"status={status.status}, filled={status.filled}, "
                f"remaining={status.remaining}, avg_fill={status.avgFillPrice}"
            )

        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        if ib.isConnected():
            ib.disconnect()
            print("Disconnected from IBKR.")


if __name__ == "__main__":
    raise SystemExit(main())
