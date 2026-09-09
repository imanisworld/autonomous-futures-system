"""Fail-closed runtime preflight for scheduled Options Paper V1 collection."""

from __future__ import annotations


def build_v1_runtime_preflight(base_cls):
    class V1RuntimePreflightScanner(base_cls):
        async def scan_watchlist(self, *, source="scheduled", context=None, now=None):
            if source == "scheduled":
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
