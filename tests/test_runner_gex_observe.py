"""The runner's GEX observation hook is observe-only, default-off, fail-soft.

These guard the contract that GEXsniper enrichment NEVER blocks the pipeline and
NEVER touches gating — it only contributes a journal record when enabled.
"""

from __future__ import annotations

from webhook.runner import _maybe_observe_gex


class _Cfg:
    """Minimal config stub exposing only the gex_* attributes the hook reads."""

    def __init__(self, enabled: bool):
        self.gex_api_enabled = enabled
        self.gex_symbol_map = {"MNQ": "NDX", "MES": "SPX"}
        self.gex_base_url = "https://api.gexsniper.com/v1"
        self.gex_timeout_seconds = 3.0


def test_disabled_by_default_returns_none(fresh_market_state):
    assert _maybe_observe_gex(fresh_market_state, _Cfg(enabled=False)) is None


def test_enabled_without_key_records_reason_no_network(fresh_market_state):
    # No GEX_API_KEY -> observe_gex fails soft before any HTTP call and still
    # returns a journalable record so the gap is visible, never raising.
    observed = _maybe_observe_gex(fresh_market_state, _Cfg(enabled=True))
    assert observed is not None
    assert observed["ok"] is False
    assert observed["error"] == "missing_api_key"


def test_helper_never_raises_on_internal_error(fresh_market_state):
    class Boom:
        gex_api_enabled = True

        def __getattr__(self, name):  # any other attr access explodes
            raise RuntimeError("boom")

    # Must swallow the error and return None rather than break the pipeline.
    assert _maybe_observe_gex(fresh_market_state, Boom()) is None
