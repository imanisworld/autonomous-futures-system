#!/usr/bin/env python3
"""Replay one saved TradingView webhook payload through the FastAPI app path.

This is a local regression harness for quote-panel, bracket verification, and
Tradovate cancel/flatten diagnostics. It posts the fixture to /webhook/alert via
FastAPI TestClient, then prints the decision, fill/risk summary, latest webhook
state, optional quote/bracket probes, and any captured broker diagnostics.

Safe defaults:
  - BROKER=paper unless --broker is provided
  - LIVE_TRADING_ENABLED=false
  - Discord notifications disabled
  - live quote attach disabled for deterministic replay
  - logs written to /tmp/afs-single-alert-regression unless --log-dir is given
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_SECRET = "local-regression-secret"
DIAGNOSTIC_PATTERNS = (
    "Tradovate",
    "placeOSO",
    "bracket",
    "verify",
    "NAKED POSITION",
    "auto-flatten",
    "flatten",
    "liquidateposition",
    "cancel",
    "ORDER FAILED",
    "BLOCKED",
)


class CaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@contextmanager
def capture_logs() -> Any:
    handler = CaptureHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(min(old_level, logging.DEBUG) if old_level else logging.DEBUG)
    try:
        yield handler
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def load_payload(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON fixture: {path}\n{exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"Fixture must contain a JSON object: {path}")
    return data


def setup_env(args: argparse.Namespace) -> str:
    secret = args.secret or os.getenv("WEBHOOK_SECRET") or DEFAULT_SECRET
    os.environ["WEBHOOK_SECRET"] = secret
    os.environ["LIVE_TRADING_ENABLED"] = "false"
    os.environ["DISCORD_NOTIFICATIONS_ENABLED"] = "false"
    os.environ["LIVE_QUOTE_ENABLED"] = "false"
    os.environ["BROKER"] = args.broker
    os.environ["LOG_DIR"] = str(args.log_dir)
    return secret


def patch_app_config(app_module: Any, args: argparse.Namespace) -> None:
    # app._config is loaded at import time; keep CLI overrides aligned with env.
    app_module._config.log_dir = str(args.log_dir)
    app_module._config.live_quote_enabled = False
    app_module._config.discord_notifications_enabled = False
    app_module._config.max_staleness_seconds = args.max_staleness_seconds
    if args.expected_timeframe_minutes is not None:
        app_module._config.expected_timeframe_minutes = args.expected_timeframe_minutes


def compact_json(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def summarize_result(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": data.get("ok"),
        "decision": data.get("decision"),
        "resolution": data.get("resolution"),
        "failed_gates": data.get("failed_gates") or [],
        "risk": data.get("risk"),
        "fill": data.get("fill"),
        "confidence_score": data.get("confidence_score"),
        "confluence": data.get("confluence"),
    }


def diagnostic_lines(records: list[logging.LogRecord]) -> list[str]:
    lines: list[str] = []
    for record in records:
        rendered = f"{record.levelname}:{record.name}:{record.getMessage()}"
        if any(pattern.lower() in rendered.lower() for pattern in DIAGNOSTIC_PATTERNS):
            lines.append(rendered)
    return lines


def instrument_from_payload(payload: dict[str, Any]) -> str:
    ticker = str(payload.get("ticker") or "MES").upper()
    if "MNQ" in ticker or re.match(r"^NQ", ticker):
        return "MNQ"
    if "MES" in ticker or re.match(r"^ES", ticker):
        return "MES"
    if "MGC" in ticker:
        return "MGC"
    if "MCL" in ticker:
        return "MCL"
    return ticker.replace("1!", "").replace("!", "") or "MES"


def print_section(title: str, value: Any) -> None:
    print(f"\n== {title} ==")
    if isinstance(value, str):
        print(value)
    else:
        print(compact_json(value))


def run(args: argparse.Namespace) -> int:
    args.fixture = args.fixture.resolve()
    args.log_dir = args.log_dir.resolve()
    args.log_dir.mkdir(parents=True, exist_ok=True)

    payload = load_payload(args.fixture)
    secret = setup_env(args)

    try:
        from fastapi.testclient import TestClient
        import webhook.app as app_module
    except Exception as exc:
        raise SystemExit(f"Could not import FastAPI app/TestClient: {exc}") from exc

    patch_app_config(app_module, args)

    client = TestClient(app_module.app)
    headers = {"X-Webhook-Secret": secret}

    print_section("Fixture", {"path": str(args.fixture), "ticker": payload.get("ticker"), "timestamp": payload.get("timestamp")})
    print_section("Harness Config", {
        "broker": args.broker,
        "log_dir": str(args.log_dir),
        "max_staleness_seconds": app_module._config.max_staleness_seconds,
        "expected_timeframe_minutes": app_module._config.expected_timeframe_minutes,
        "live_trading_enabled": app_module._config.live_trading_enabled,
        "live_quote_enabled": app_module._config.live_quote_enabled,
        "discord_notifications_enabled": app_module._config.discord_notifications_enabled,
    })

    with capture_logs() as captured:
        response = client.post("/webhook/alert", json=payload, headers=headers)

    try:
        data = response.json()
    except Exception:
        data = {"raw": response.text}

    print_section("HTTP", {"status_code": response.status_code})

    latest = client.get("/status/latest-webhook")
    try:
        latest_data = latest.json()
    except Exception:
        latest_data = {"raw": latest.text}

    # /webhook/alert fast-ACKs ({"queued": true}) and runs the decision pipeline
    # in the background (commit 3fc7b55), so the response no longer carries the
    # decision — it lands in latest-webhook. Summarize from there on a queued ACK.
    summary_source: dict[str, Any] = data if isinstance(data, dict) else {}
    if summary_source.get("queued") and not summary_source.get("decision"):
        summary_source = (latest_data.get("result") or {}) if isinstance(latest_data, dict) else {}
    print_section("Decision Summary", summarize_result(summary_source))

    if args.full_result:
        print_section("Full Result", data)
    print_section("Latest Webhook", {
        "status_code": latest.status_code,
        "received_at": latest_data.get("received_at") if isinstance(latest_data, dict) else None,
        "ticker": ((latest_data.get("payload") or {}).get("ticker") if isinstance(latest_data, dict) else None),
        "decision": ((latest_data.get("result") or {}).get("decision") if isinstance(latest_data, dict) else None),
        "failed_gates": ((latest_data.get("result") or {}).get("failed_gates") if isinstance(latest_data, dict) else None),
    })

    if args.probe_quote:
        inst = args.instrument or instrument_from_payload(payload)
        quote = client.get(f"/status/quote?instrument={inst}")
        try:
            quote_data = quote.json()
        except Exception:
            quote_data = {"raw": quote.text}
        print_section("Quote Probe", {"instrument": inst, "status_code": quote.status_code, "body": quote_data})

    if args.probe_bracket:
        inst = args.instrument or instrument_from_payload(payload)
        bracket = client.get(f"/status/test-bracket?instrument={inst}")
        try:
            bracket_data = bracket.json()
        except Exception:
            bracket_data = {"raw": bracket.text}
        print_section("Bracket Probe", {"instrument": inst, "status_code": bracket.status_code, "body": bracket_data})

    lines = diagnostic_lines(captured.records)
    print_section("Bracket / Tradovate / Cancel Diagnostics", lines or "No matching diagnostics captured.")

    if response.status_code >= 400:
        return 1
    if isinstance(data, dict) and data.get("ok") is False:
        return 1
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay one webhook payload fixture through webhook.app /webhook/alert.",
    )
    parser.add_argument("fixture", type=Path, help="Path to a concrete JSON webhook payload fixture.")
    parser.add_argument("--broker", choices=["paper", "tradovate"], default=os.getenv("BROKER", "paper").lower())
    parser.add_argument("--secret", default=None, help="Webhook secret to use; defaults to WEBHOOK_SECRET or a local harness secret.")
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "afs-single-alert-regression",
        help="Isolated journal/log dir for the replay.",
    )
    parser.add_argument("--max-staleness-seconds", type=int, default=0, help="Default 0 disables historical staleness rejection.")
    parser.add_argument("--expected-timeframe-minutes", type=int, default=None, help="Override decision timeframe for this replay.")
    parser.add_argument("--instrument", default=None, help="Instrument for optional quote/bracket probes; defaults from ticker.")
    parser.add_argument("--probe-quote", action="store_true", help="Also call /status/quote for the payload instrument.")
    parser.add_argument("--probe-bracket", action="store_true", help="Also call /status/test-bracket for the payload instrument.")
    parser.add_argument("--full-result", action="store_true", help="Print the full /webhook/alert JSON response.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv or sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
