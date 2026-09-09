"""Fail-closed runtime preflight for scheduled Options Paper V1 collection."""

from __future__ import annotations

import os

APPROVED_AGGREGATE_RISK = 1000.0
APPROVED_MIN_DTE = 14


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _v1_collection_enabled() -> bool:
    return _truthy("OPTIONS_PAPER_V1_COLLECTION_ENABLED")


def _float_env(name: str) -> float | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def build_v1_runtime_preflight(base_cls):
    class V1RuntimePreflightScanner(base_cls):
        async def scan_watchlist(self, *, source="scheduled", context=None, now=None):
            if source == "scheduled" and _v1_collection_enabled():
                # Freeze the approved policy at runtime, not only in source
                # constants. This prevents a stale box env or the legacy
                # short-DTE companion from contaminating the V1 population.
                manager_budget = _float_env(
                    "OPTIONS_MANAGER_MAX_AGGREGATE_OPEN_RISK_DOLLARS"
                )
                if manager_budget != APPROVED_AGGREGATE_RISK:
                    self.last_skip_reason = "v1_manager_aggregate_risk_mismatch"
                    return []
                manager_min_dte = _float_env("OPTIONS_MANAGER_RISK_MIN_DTE_DAYS")
                if manager_min_dte != APPROVED_MIN_DTE:
                    self.last_skip_reason = "v1_manager_min_dte_mismatch"
                    return []
                if _truthy("OPTIONS_COMPANION_ENABLED"):
                    self.last_skip_reason = "v1_options_companion_must_be_disabled"
                    return []
                if not getattr(self.config, "bar_context_enabled", False):
                    self.last_skip_reason = "v1_requires_bar_context"
                    return []
                if not getattr(self.config, "bar_context_configured", False):
                    self.last_skip_reason = "v1_bar_context_unconfigured"
                    return []
                if not getattr(self.config, "market_data_configured", False):
                    self.last_skip_reason = "v1_market_data_unconfigured"
                    return []
            return await super().scan_watchlist(source=source, context=context, now=now)

    V1RuntimePreflightScanner.__name__ = base_cls.__name__
    V1RuntimePreflightScanner.__qualname__ = base_cls.__qualname__
    return V1RuntimePreflightScanner
