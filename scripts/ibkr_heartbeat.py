#!/usr/bin/env python3
"""Local IBKR Gateway heartbeat for Sunday-night paper trading runs."""

from __future__ import annotations

import argparse
import logging
import time

from config.settings import load_config
from execution.ibkr_broker import IBKRBroker
from notifications.system_notifier import notify_system


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor local IBKR Gateway/TWS connectivity.")
    parser.add_argument("--interval-seconds", type=int, default=900)
    parser.add_argument("--once", action="store_true", help="Run one health check and exit.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = load_config()

    def status_callback(message: str) -> None:
        notify_system(message, config=config)

    broker = IBKRBroker(status_callback=status_callback)
    last_connected: bool | None = None

    while True:
        health = broker.health_check()
        connected = bool(health["connected"])
        logging.info("IBKR heartbeat: %s", health)

        if connected != last_connected:
            state = "connected" if connected else "disconnected"
            notify_system(f"IBKR heartbeat: {state}", config=config)
            last_connected = connected

        if not connected:
            broker.connect()

        if args.once:
            return 0 if connected else 1
        time.sleep(max(15, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
