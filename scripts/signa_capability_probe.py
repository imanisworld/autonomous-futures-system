"""Read-only Signa API capability/entitlement probe.

This diagnostic is intentionally isolated from all strategy, risk, broker, and
execution paths. It performs only GET requests against a small allowlist of
currently documented Signa market-intelligence endpoints and the legacy signal
endpoint used by this repository. It never prints the API key or raw provider
payload values; output is limited to HTTP/result classification and response
shape metadata.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


DEFAULT_BASE_URL = "https://app.getsigna.ai"
DEFAULT_SYMBOL = "AAPL"
DEFAULT_TIMEFRAME = "1d"


@dataclass(frozen=True)
class ProbeRequest:
    name: str
    path: str
    params: dict[str, Any]
    auth_required: bool = True


def build_probe_requests(symbol: str, timeframe: str) -> tuple[ProbeRequest, ...]:
    symbol = symbol.strip().upper()
    timeframe = timeframe.strip() or DEFAULT_TIMEFRAME
    return (
        ProbeRequest("health", "/api/v1/health", {}, auth_required=False),
        ProbeRequest("quote", f"/api/v1/quote/{symbol}", {}),
        ProbeRequest("signal_current", f"/api/v1/signals/{symbol}", {"timeframe": timeframe}),
        ProbeRequest("signal_legacy", "/api/v1/signal", {"sym": symbol, "timeframe": timeframe}),
        ProbeRequest("analysis", "/api/v1/analysis", {"sym": symbol}),
        ProbeRequest("earnings", "/api/v1/earnings", {"symbol": symbol}),
        ProbeRequest("options_flow", f"/api/options-flow/{symbol}", {}),
        ProbeRequest("darkpool", f"/api/options-flow/darkpool/{symbol}", {}),
        ProbeRequest("market_tide", "/api/options-flow/tide", {}),
        ProbeRequest("political_trades", "/api/v1/political-trades", {"ticker": symbol, "limit": 5}),
    )


def classify_http_status(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "available"
    if status_code == 401:
        return "auth_failed"
    if status_code == 403:
        return "forbidden_or_not_entitled"
    if status_code == 404:
        return "not_found"
    if status_code == 429:
        return "rate_limited"
    if 500 <= status_code < 600:
        return "provider_error"
    return "unexpected_status"


def response_shape(value: Any, *, depth: int = 0) -> Any:
    """Return field/type structure only; never provider values."""
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(key): response_shape(item, depth=depth + 1) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [] if not value else [response_shape(value[0], depth=depth + 1)]
    if value is None:
        return "null"
    return type(value).__name__


def probe_signa_capabilities(
    *,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
    timeout: float = 5.0,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("SIGNA_API_KEY is required for the entitlement probe")

    close_client = client is None
    http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout, follow_redirects=True)
    results: list[dict[str, Any]] = []
    try:
        for request in build_probe_requests(symbol, timeframe):
            headers = {"Authorization": f"Bearer {api_key}"} if request.auth_required else {}
            started = time.monotonic()
            try:
                response = http.get(request.path, params=request.params, headers=headers)
                elapsed_ms = round((time.monotonic() - started) * 1000.0, 1)
                payload: Any = None
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                results.append(
                    {
                        "name": request.name,
                        "method": "GET",
                        "path": request.path,
                        "status_code": response.status_code,
                        "status": classify_http_status(response.status_code),
                        "elapsed_ms": elapsed_ms,
                        "content_type": response.headers.get("content-type"),
                        "shape": response_shape(payload) if payload is not None else None,
                    }
                )
            except httpx.HTTPError as exc:
                results.append(
                    {
                        "name": request.name,
                        "method": "GET",
                        "path": request.path,
                        "status_code": None,
                        "status": "network_error",
                        "elapsed_ms": round((time.monotonic() - started) * 1000.0, 1),
                        "error_type": type(exc).__name__,
                        "shape": None,
                    }
                )
    finally:
        if close_client:
            http.close()

    return {
        "probe": "signa_capability_probe_v1",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url.rstrip("/"),
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe,
        "results": results,
        "notes": {
            "gex": "Signa currently advertises GEX access, but the public API reference does not document a standalone GEX endpoint; do not guess one.",
            "safety": "read-only GET allowlist; no strategy, risk, broker, order, or execution integration",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Signa API capability probe")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--timeframe", default=DEFAULT_TIMEFRAME)
    parser.add_argument("--base-url", default=os.getenv("SIGNA_BASE_URL", DEFAULT_BASE_URL))
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    api_key = os.getenv("SIGNA_API_KEY", "").strip()
    if not api_key:
        print(json.dumps({"ok": False, "error": "SIGNA_API_KEY is not configured"}))
        return 2

    report = probe_signa_capabilities(
        api_key=api_key,
        base_url=args.base_url,
        symbol=args.symbol,
        timeframe=args.timeframe,
        timeout=args.timeout,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
