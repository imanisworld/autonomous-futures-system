"""Offline configuration and reachability preflight for the options scanner.

This command never calls Public.com. It reports configuration presence with
values redacted and exercises the same structural path guard used by the
Public market-data client.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Any

from .config import _as_bool, load_config
from .market_data import (
    PUBLIC_ALLOWED_PREFIXES,
    PUBLIC_AUTH_TOKEN_PATH,
    PUBLIC_MARKETDATA_PREFIX,
    _assert_read_only_path,
)


_EXPECTED_AUTH_PATH = "/userapiauthservice/personal/access-tokens"
_EXPECTED_MARKETDATA_PREFIX = "/userapigateway/marketdata"
_MARKETDATA_ENDPOINTS = ("quotes", "option-expirations", "option-chain")
_BLOCKED_PATHS = (
    "/userapigateway/trading/redacted-account/orders",
    "/accounts/me",
    "/positions",
    "/balances",
    "/transactions",
)


def _configured(value: str | None) -> bool:
    return bool((value or "").strip())


def _redacted_state(configured: bool) -> str:
    return "configured (redacted)" if configured else "missing"


def _boundary_report() -> dict[str, Any]:
    failures: list[str] = []
    if PUBLIC_AUTH_TOKEN_PATH != _EXPECTED_AUTH_PATH:
        failures.append("unexpected auth-token path")
    if PUBLIC_MARKETDATA_PREFIX != _EXPECTED_MARKETDATA_PREFIX:
        failures.append("unexpected market-data prefix")
    if PUBLIC_ALLOWED_PREFIXES != (PUBLIC_MARKETDATA_PREFIX,):
        failures.append("market-data allowlist contains an unexpected prefix")

    allowed_paths = [
        f"{PUBLIC_MARKETDATA_PREFIX}/<redacted-account-pin>/{endpoint}"
        for endpoint in _MARKETDATA_ENDPOINTS
    ]
    for path in allowed_paths:
        try:
            _assert_read_only_path(path, PUBLIC_ALLOWED_PREFIXES)
        except ValueError:
            failures.append(f"expected market-data path was rejected: {path}")

    for path in _BLOCKED_PATHS:
        try:
            _assert_read_only_path(path, PUBLIC_ALLOWED_PREFIXES)
        except ValueError:
            continue
        failures.append(f"non-market-data path was accepted: {path}")

    return {
        "ok": not failures,
        "network_called": False,
        "reachable_path_families": {
            "auth_token": [PUBLIC_AUTH_TOKEN_PATH],
            "account_scoped_market_data": allowed_paths,
        },
        "trading_account_order_paths": "blocked" if not failures else "boundary check failed",
        "failures": failures,
    }


def build_preflight_report(
    environ: Mapping[str, str] | Iterable[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    if environ is None:
        config = load_config()
        env = os.environ
    else:
        env = dict(environ)
        config = load_config(environ=env.items())

    scanner_enabled = _as_bool(env.get("OPTIONS_SCANNER_ENABLED"), False)
    canonical_secret = _configured(env.get("PUBLIC_API_SECRET_KEY"))
    legacy_secret = _configured(env.get("PUBLIC_API_KEY"))
    account_pin = _configured(config.public_account_id)

    missing: list[str] = []
    if not scanner_enabled:
        missing.append("OPTIONS_SCANNER_ENABLED=true")
    if config.market_data_provider != "public":
        missing.append("OPTIONS_MARKET_DATA_PROVIDER=public")
    if not (canonical_secret or legacy_secret):
        missing.append("PUBLIC_API_SECRET_KEY")
    if not account_pin:
        missing.append("PUBLIC_ACCOUNT_ID")

    boundary = _boundary_report()
    ready = not missing and boundary["ok"]
    secret_source = None
    if canonical_secret:
        secret_source = "PUBLIC_API_SECRET_KEY"
    elif legacy_secret:
        secret_source = "PUBLIC_API_KEY (legacy fallback)"

    return {
        "status": "ok" if ready else "configuration_missing" if missing else "boundary_failed",
        "ready_for_local_advisory_start": ready,
        "configuration": {
            "OPTIONS_SCANNER_ENABLED": scanner_enabled,
            "OPTIONS_MARKET_DATA_PROVIDER": config.market_data_provider,
            "PUBLIC_API_SECRET_KEY": _redacted_state(canonical_secret or legacy_secret),
            "secret_source": secret_source,
            "PUBLIC_ACCOUNT_ID": _redacted_state(account_pin),
        },
        "missing_configuration": missing,
        "boundary": boundary,
        "advisory_only": True,
        "trading_reachable": False,
        "note": "Offline structural check only; no credential or market-data request was sent.",
    }


def _print_safe_summary(report: Mapping[str, Any]) -> None:
    """Print fixed status tokens only; never serialize environment-derived data."""
    ready = report.get("ready_for_local_advisory_start") is True
    print(f"OPTIONS SCANNER PREFLIGHT: {'OK' if ready else 'BLOCKED'}")
    missing = set(report.get("missing_configuration") or [])
    for requirement in (
        "OPTIONS_SCANNER_ENABLED=true",
        "OPTIONS_MARKET_DATA_PROVIDER=public",
        "PUBLIC_API_SECRET_KEY",
        "PUBLIC_ACCOUNT_ID",
    ):
        print(f"{'MISSING' if requirement in missing else 'OK'} {requirement}")
    boundary_ok = (report.get("boundary") or {}).get("ok") is True
    print("network_called: false")
    print(
        "trading_account_order_paths: "
        + ("blocked" if boundary_ok else "boundary check failed")
    )
    print("reachable_path_families: auth_token, account_scoped_market_data")


def main() -> int:
    report = build_preflight_report()
    _print_safe_summary(report)
    return 0 if report["ready_for_local_advisory_start"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
