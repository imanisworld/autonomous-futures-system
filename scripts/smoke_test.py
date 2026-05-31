#!/usr/bin/env python3
"""
scripts/smoke_test.py

Live smoke test against a running Railway (or local) instance.
Fires structured payloads at every risk path and prints PASS / FAIL.

Usage:
    # Against local server:
    python scripts/smoke_test.py

    # Against Railway:
    SMOKE_BASE_URL=https://your-app.railway.app \
    SMOKE_SECRET=your-secret \
    python scripts/smoke_test.py

Exit code 0 = all passed, 1 = one or more failed.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import requests
except ImportError:
    print("requests not installed — run: pip install requests")
    sys.exit(1)

BASE_URL = os.getenv("SMOKE_BASE_URL", "http://localhost:8000").rstrip("/")
SECRET   = os.getenv("SMOKE_SECRET", "dev-secret")
TIMEOUT  = 10

_PASS = "\033[92m PASS\033[0m"
_FAIL = "\033[91m FAIL\033[0m"
_SKIP = "\033[93m SKIP\033[0m"

_results: list[tuple[str, bool, str]] = []


def _post(payload: dict) -> dict:
    resp = requests.post(
        f"{BASE_URL}/webhook/alert",
        json=payload,
        headers={"X-Webhook-Secret": SECRET},
        timeout=TIMEOUT,
    )
    return resp.json()


def _get(path: str) -> dict:
    resp = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
    return resp.json()


def check(name: str, passed: bool, detail: str = "") -> None:
    tag = _PASS if passed else _FAIL
    line = f"{tag}  {name}"
    if detail:
        line += f"  ({detail})"
    print(line)
    _results.append((name, passed, detail))


def _base_payload(**overrides) -> dict:
    now = datetime.now(timezone.utc)
    data = {
        "ticker": "MNQ1!",
        "timestamp": now.isoformat(),
        "open": 19480.0,
        "high": 19510.0,
        "low": 19475.0,
        "close": 19505.25,
        "volume": 4200,
        "avg_volume": 3800,
        "vwap": 19495.0,
        "orb_high": 19498.0,
        "orb_low": 19462.0,
        "orb_status": "reclaimed_high",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "STRONG",
        "previous_day_high": 19520.0,
        "previous_day_low": 19440.0,
        "previous_day_close": 19475.0,
        "current_bar_type": "two_up",
        "previous_bar_type": "two_up",
        "two_bars_back_type": "two_up",
    }
    data.update(overrides)
    return data


# ─── Test functions ────────────────────────────────────────────────────────────

def test_health():
    """GET /health returns 200 with paper_mode=true."""
    r = _get("/health")
    check("health: ok=true",         r.get("ok") is True)
    check("health: paper_mode=true", r.get("paper_mode") is True)
    check("health: live_trading=false", r.get("live_trading_enabled") is False)


def test_bad_secret():
    """Webhook with wrong secret → 401."""
    resp = requests.post(
        f"{BASE_URL}/webhook/alert",
        json=_base_payload(),
        headers={"X-Webhook-Secret": "wrong-secret"},
        timeout=TIMEOUT,
    )
    check("auth: bad secret → 401", resp.status_code == 401,
          f"got {resp.status_code}")


def test_missing_secret():
    """Webhook with no secret → 401."""
    resp = requests.post(
        f"{BASE_URL}/webhook/alert",
        json=_base_payload(),
        timeout=TIMEOUT,
    )
    check("auth: missing secret → 401", resp.status_code == 401,
          f"got {resp.status_code}")


def test_valid_payload_gets_decision():
    """Valid TRENDING payload returns a decision field."""
    r = _post(_base_payload())
    check("alert: decision field present", "decision" in r,
          f"keys={list(r.keys())}")
    check("alert: ok=true",               r.get("ok") is True)
    decision = r.get("decision", "")
    check("alert: decision is known code",
          decision in ("TRADE", "NO_TRADE", "RISK_REJECTED",
                       "BLOCKED_MAX_TRADES", "BLOCKED_LOSS_LOCKOUT",
                       "BLOCKED_OPEN_POSITION", "BLOCKED_DATA_QUALITY"),
          f"decision={decision}")


def test_choppy_no_trade():
    """CHOPPY market_condition → NO_TRADE."""
    r = _post(_base_payload(market_condition="CHOPPY"))
    check("no_trade: CHOPPY → NO_TRADE or RISK_REJECTED",
          r.get("decision") in ("NO_TRADE", "RISK_REJECTED"),
          f"got {r.get('decision')}")


def test_data_quality_ohlc_contradiction():
    """high < low → BLOCKED_DATA_QUALITY."""
    now = datetime.now(timezone.utc)
    r = _post(_base_payload(high=19400.0, low=19500.0, timestamp=now.isoformat()))
    check("data_quality: OHLC contradiction → BLOCKED_DATA_QUALITY",
          r.get("decision") == "BLOCKED_DATA_QUALITY",
          f"got {r.get('decision')}, gates={r.get('failed_gates')}")


def test_status_today():
    """GET /status/today returns required fields."""
    r = _get("/status/today")
    for field in ("date", "trade_count", "max_trades_per_day", "has_open_position",
                  "wins", "losses", "realized_pnl_dollars", "account_balance"):
        check(f"status/today: {field} present", field in r, f"missing from {list(r.keys())}")


def test_status_history():
    """GET /status/history returns a days list."""
    r = _get("/status/history?days=3")
    check("status/history: days is list",  isinstance(r.get("days"), list))
    check("status/history: ≤ 3 entries",  len(r.get("days", [])) <= 3)


def test_adaptive_endpoint():
    """GET /status/adaptive returns committee report with required keys."""
    r = _get("/status/adaptive")
    for field in ("overall_status", "sample_size", "sample_sufficiency",
                  "top_recommendations", "generated_at", "agents"):
        check(f"adaptive: {field} present", field in r)
    check("adaptive: overall_status valid",
          r.get("overall_status") in ("OK", "WARNING", "CRITICAL"))


def test_adaptive_history():
    """GET /status/adaptive/history returns days list."""
    r = _get("/status/adaptive/history?days=7")
    check("adaptive/history: days is list", isinstance(r.get("days"), list))


def test_missing_required_fields():
    """Payload missing required fields → 422 Unprocessable Entity."""
    resp = requests.post(
        f"{BASE_URL}/webhook/alert",
        json={"ticker": "MNQ1!"},  # massively incomplete
        headers={"X-Webhook-Secret": SECRET},
        timeout=TIMEOUT,
    )
    check("validation: missing fields → 422",
          resp.status_code == 422,
          f"got {resp.status_code}")


def test_dashboard_renders():
    """GET / returns HTML with 200."""
    resp = requests.get(f"{BASE_URL}/", timeout=TIMEOUT)
    check("dashboard: 200", resp.status_code == 200)
    check("dashboard: HTML content-type",
          "text/html" in resp.headers.get("content-type", ""))
    check("dashboard: contains RiskSentinel",
          "RiskSentinel" in resp.text)


# ─── Runner ───────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'─'*55}")
    print(f"  Smoke test → {BASE_URL}")
    print(f"{'─'*55}\n")

    suites = [
        test_health,
        test_bad_secret,
        test_missing_secret,
        test_valid_payload_gets_decision,
        test_choppy_no_trade,
        test_data_quality_ohlc_contradiction,
        test_status_today,
        test_status_history,
        test_adaptive_endpoint,
        test_adaptive_history,
        test_missing_required_fields,
        test_dashboard_renders,
    ]

    for fn in suites:
        print(f"\n── {fn.__name__} ──")
        try:
            fn()
        except Exception as exc:
            check(fn.__name__, False, f"exception: {exc}")

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    total  = len(_results)

    print(f"\n{'─'*55}")
    print(f"  {passed}/{total} passed", end="")
    if failed:
        print(f"   \033[91m{failed} FAILED\033[0m")
    else:
        print("   \033[92mall green\033[0m")
    print(f"{'─'*55}\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
