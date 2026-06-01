#!/usr/bin/env python3
"""
scripts/ibkr_watchdog.py

Monitors IB Gateway Docker container and restarts it if unhealthy.
Runs every 15 minutes via systemd timer.

Checks:
  1. Docker container is running
  2. API port (4003) is accepting connections
  3. Restarts container if either check fails
  4. Sends Discord alert on restart

Usage:
    python scripts/ibkr_watchdog.py            # check + restart if needed
    python scripts/ibkr_watchdog.py --dry-run  # check only, no restart
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")
CONTAINER_NAME = os.getenv("IBKR_CONTAINER_NAME", "ibgateway")
IBKR_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.getenv("IBKR_PORT", "4003"))
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")


def _container_running() -> bool:
    try:
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", CONTAINER_NAME],
            capture_output=True, text=True, timeout=10,
        )
        return result.stdout.strip().lower() == "true"
    except Exception:
        return False


def _port_open() -> bool:
    try:
        with socket.socket() as s:
            s.settimeout(2.0)
            return s.connect_ex((IBKR_HOST, IBKR_PORT)) == 0
    except Exception:
        return False


def _restart_container() -> bool:
    try:
        result = subprocess.run(
            ["docker", "restart", CONTAINER_NAME],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0
    except Exception:
        return False


def _send_discord(message: str) -> None:
    if not DISCORD_WEBHOOK_URL:
        return
    try:
        import httpx
        body = json.dumps({"content": message}).encode()
        httpx.post(
            DISCORD_WEBHOOK_URL,
            content=body,
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="IB Gateway watchdog")
    parser.add_argument("--dry-run", action="store_true", help="Check only, no restart")
    args = parser.parse_args()

    now = datetime.now(_ET).strftime("%Y-%m-%d %H:%M ET")
    running = _container_running()
    port_ok = _port_open()

    print(f"[{now}] container={running} port={port_ok}")

    if running and port_ok:
        print("✓ IB Gateway healthy")
        return

    reason = []
    if not running:
        reason.append("container not running")
    if not port_ok:
        reason.append(f"port {IBKR_PORT} not responding")
    reason_str = ", ".join(reason)

    print(f"✗ IB Gateway unhealthy: {reason_str}")

    if args.dry_run:
        print("DRY RUN — skipping restart")
        return

    print(f"Restarting {CONTAINER_NAME}...")
    success = _restart_container()

    if success:
        time.sleep(15)  # Wait for Gateway to initialize
        port_after = _port_open()
        status = "recovered" if port_after else "restarted but port still down"
        msg = f"⚠️ **IB Gateway watchdog** [{now}]\nReason: {reason_str}\nStatus: {status}"
        print(f"Restart {status}")
    else:
        msg = f"🚨 **IB Gateway watchdog** [{now}]\nReason: {reason_str}\nStatus: restart FAILED"
        print("Restart failed")

    _send_discord(msg)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
