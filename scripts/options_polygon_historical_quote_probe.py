#!/usr/bin/env python3
"""Read-only Polygon/Massive historical option-quote entitlement probe.

Checks whether the configured POLYGON_API_KEY can read historical option quotes
from GET /v3/quotes/{optionsTicker}. It never places orders, never changes
account state, never writes files, and never prints the API key.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import sys

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BASE_URL = "https://api.polygon.io"


@dataclass(frozen=True)
class ProbeResult:
    verdict: str
    configured: bool
    option_ticker: str
    date: str
    http_status: int | None = None
    api_status: str | None = None
    result_count: int = 0
    reason: str | None = None


def normalize_option_ticker(value: str) -> str:
    ticker = str(value or "").strip().upper()
    if not ticker:
        raise ValueError("option ticker is required")
    if not ticker.startswith("O:"):
        ticker = f"O:{ticker}"
    return ticker


def probe_historical_option_quotes(
    *,
    option_ticker: str,
    date: str,
    api_key: str | None = None,
    base_url: str = DEFAULT_BASE_URL,
    client: httpx.Client | None = None,
) -> ProbeResult:
    key = (api_key if api_key is not None else os.getenv("POLYGON_API_KEY", "")).strip()
    ticker = normalize_option_ticker(option_ticker)
    if not key:
        return ProbeResult(
            verdict="BLOCKED",
            configured=False,
            option_ticker=ticker,
            date=date,
            reason="POLYGON_API_KEY not configured",
        )

    url = f"{base_url.rstrip('/')}/v3/quotes/{ticker}"
    params = {
        "timestamp": date,
        "limit": 1,
        "order": "asc",
        "sort": "timestamp",
    }
    close_client = client is None
    active = client or httpx.Client()
    try:
        try:
            response = active.get(
                url,
                params=params,
                headers={"Authorization": f"Bearer {key}"},
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                verdict="BLOCKED",
                configured=True,
                option_ticker=ticker,
                date=date,
                reason=type(exc).__name__,
            )
        try:
            body = response.json()
        except ValueError:
            body = {}


        if response.status_code == 200:
            return ProbeResult(
                verdict="ENTITLED",
                configured=True,
                option_ticker=ticker,
                date=date,
                http_status=response.status_code,
                api_status=str(body.get("status") or "") or None,
                result_count=len(body.get("results") or []),
            )

        api_status = str(body.get("status") or "") or None
        message = str(body.get("message") or body.get("error") or "").strip()
        if response.status_code == 403 and api_status == "NOT_AUTHORIZED":
            reason = "historical_options_quotes_not_entitled"
        elif response.status_code == 401:
            reason = "polygon_auth_failed"
        elif response.status_code == 404:
            reason = "historical_options_quote_endpoint_or_contract_not_found"
        else:
            reason = f"polygon_http_{response.status_code}"
            if message:
                reason = f"{reason}:{message[:120]}"

        return ProbeResult(
            verdict="BLOCKED",
            configured=True,
            option_ticker=ticker,
            date=date,
            http_status=response.status_code,
            api_status=api_status,
            result_count=0,
            reason=reason,
        )
    finally:
        if close_client:
            active.close()


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--option-ticker", required=True)
    parser.add_argument("--date", required=True, help="Historical UTC date YYYY-MM-DD")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)


    result = probe_historical_option_quotes(
        option_ticker=args.option_ticker,
        date=args.date,
        base_url=args.base_url,
    )
    print(json.dumps(asdict(result), sort_keys=True, indent=2))
    return 0 if result.verdict == "ENTITLED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
