"""Default-off Signa v2 observation enrichment for the options scanner.

This wrapper adds namespaced Signa v2 telemetry to the scanner's normalized
raw data. It is deliberately non-authoritative: it never changes setup status,
scanner score, risk, contract selection, alert eligibility, or execution.

Enable only with ``OPTIONS_SIGNA_V2_OBSERVE_ENABLED=true`` (or an injected
config attribute of the same meaning). Default is OFF.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from sources.signa_v2_client import SignaV2Client


_TRUE = {"1", "true", "yes", "on"}
DEFAULT_TIMEFRAME = "1d"


def signa_v2_observe_enabled(config: Any) -> bool:
    explicit = getattr(config, "signa_v2_observe_enabled", None)
    if explicit is not None:
        return bool(explicit)
    return os.getenv("OPTIONS_SIGNA_V2_OBSERVE_ENABLED", "").strip().lower() in _TRUE


def signa_v2_timeframe(config: Any) -> str:
    explicit = getattr(config, "signa_v2_timeframe", None)
    if explicit:
        return str(explicit).strip() or DEFAULT_TIMEFRAME
    return os.getenv("OPTIONS_SIGNA_V2_TIMEFRAME", DEFAULT_TIMEFRAME).strip() or DEFAULT_TIMEFRAME


def signa_v2_symbol(config: Any, ticker: str) -> str:
    ticker = str(ticker or "").strip().upper()
    mapping = getattr(config, "signa_symbol_map", None)
    if isinstance(mapping, dict):
        mapped = mapping.get(ticker)
        if mapped:
            return str(mapped).strip().upper()
    return ticker


def build_signa_v2_observer(base_cls):
    """Return a scanner subclass that appends v2 telemetry when explicitly enabled."""

    class SignaV2ObservationScanner(base_cls):
        def __init__(self, *args, signa_v2_client=None, **kwargs):
            super().__init__(*args, **kwargs)
            self._signa_v2_client = signa_v2_client

        async def _build_normalized_data(self, ticker, context, now):
            data = await super()._build_normalized_data(ticker, context, now)
            if not signa_v2_observe_enabled(self.config):
                return data

            symbol = signa_v2_symbol(self.config, ticker)
            timeframe = signa_v2_timeframe(self.config)
            client = self._signa_v2_client
            if client is None:
                # Build once and keep it: the client's per-(symbol, timeframe)
                # TTL cache only helps if it outlives a single scan.
                client = SignaV2Client(
                    base_url=getattr(self.config, "signa_base_url", "https://app.getsigna.ai"),
                    timeout=getattr(self.config, "signa_timeout_seconds", 3.0),
                )
                self._signa_v2_client = client

            try:
                observation = await asyncio.to_thread(client.fetch_action_card, symbol, timeframe)
                telemetry = observation.telemetry_fields()
            except Exception as exc:  # noqa: BLE001 - observational lane must fail soft
                telemetry = {
                    "signa_v2_ok": False,
                    "signa_v2_symbol": symbol,
                    "signa_v2_timeframe": timeframe,
                    "signa_v2_error": type(exc).__name__,
                }

            # Namespace-only update. No legacy Signa keys and no scanner/risk keys
            # are overwritten, so the scorer's signa component remains exactly 0.
            data.update(telemetry)
            return data

    return SignaV2ObservationScanner
