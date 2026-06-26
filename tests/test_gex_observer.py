"""Tests for the Public-fed observe-only GEX producer + chain gamma/OI parsing."""

from datetime import date
from types import SimpleNamespace

import pytest

from options_companion.chain_provider import ChainContract, ChainSnapshot, _parse_chain
from sources.gex_observer import map_underlying, observe_gex


def _cfg(**over):
    base = dict(
        gex_observe_enabled=True,
        gex_observe_max_dte=7,
        gex_observe_symbol_map={"MNQ": "QQQ", "NQ": "QQQ", "MES": "SPY", "ES": "SPY"},
        public_base_url="https://api.public.com",
    )
    base.update(over)
    return SimpleNamespace(**base)


class _FakeProvider:
    """Async-context chain provider returning a canned snapshot."""

    def __init__(self, snapshot=None, raises=False):
        self._snapshot = snapshot
        self._raises = raises

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def fetch_chain(self, underlying, *, max_dte):
        if self._raises:
            raise RuntimeError("boom")
        return self._snapshot


def _snapshot_with_gamma():
    exp = date(2026, 1, 1)
    # call gamma dominates → positive net GEX; mids give parity spot ≈ 700.
    contracts = [
        ChainContract("c700", exp, 700, "CALL", bid=4.9, ask=5.1, delta=0.5, gamma=0.05, open_interest=5000),
        ChainContract("p700", exp, 700, "PUT", bid=4.9, ask=5.1, delta=-0.5, gamma=0.05, open_interest=1000),
        ChainContract("c710", exp, 710, "CALL", bid=1.9, ask=2.1, delta=0.3, gamma=0.04, open_interest=8000),
        ChainContract("p690", exp, 690, "PUT", bid=1.9, ask=2.1, delta=-0.3, gamma=0.04, open_interest=2000),
    ]
    return ChainSnapshot(underlying="QQQ", contracts=contracts)


# ── mapping ──────────────────────────────────────────────────────────────────

def test_map_underlying_known_and_unknown():
    cfg = _cfg()
    assert map_underlying("MNQH6", cfg) == "QQQ"
    assert map_underlying("MES", cfg) == "SPY"
    assert map_underlying("CL", cfg) is None


# ── gating ───────────────────────────────────────────────────────────────────

def test_observe_disabled_returns_none():
    cfg = _cfg(gex_observe_enabled=False)
    assert observe_gex("MNQ", cfg, provider=_FakeProvider(), use_cache=False) is None


def test_unmapped_instrument_returns_none():
    cfg = _cfg()
    assert observe_gex("CL", cfg, provider=_FakeProvider(_snapshot_with_gamma()), use_cache=False) is None


# ── happy path ───────────────────────────────────────────────────────────────

def test_observe_computes_record_from_chain():
    cfg = _cfg()
    rec = observe_gex("MNQ", cfg, provider=_FakeProvider(_snapshot_with_gamma()), use_cache=False)
    assert rec is not None
    assert rec["ok"] is True
    assert rec["underlying"] == "QQQ"
    assert rec["net_gex"] > 0          # call gamma dominates
    assert rec["regime"] == "positive"
    assert rec["spot"] == pytest.approx(700.0, abs=1.0)  # recovered via parity


# ── fail-soft ────────────────────────────────────────────────────────────────

def test_provider_error_returns_none_not_raise():
    cfg = _cfg()
    assert observe_gex("MNQ", cfg, provider=_FakeProvider(raises=True), use_cache=False) is None


def test_snapshot_error_yields_not_ok_record():
    cfg = _cfg()
    snap = ChainSnapshot(underlying="QQQ", error="auth_failed")
    rec = observe_gex("MNQ", cfg, provider=_FakeProvider(snap), use_cache=False)
    # record returned but ok=False → runner hook drops it (only journals ok=True)
    assert rec["ok"] is False
    assert rec["error"] == "auth_failed"


# ── chain parsing: gamma + open interest from a Public-shaped payload ─────────

def test_parse_chain_extracts_gamma_and_open_interest():
    payload = {
        "calls": [
            {
                "instrument": {"symbol": "SPY...C00700"},
                "bid": "4.30", "ask": "4.40", "openInterest": 8686,
                "optionDetails": {
                    "strikePrice": "700",
                    "greeks": {"delta": "0.62", "gamma": "0.0495", "impliedVolatility": "0.1879"},
                },
            }
        ],
        "puts": [],
    }
    contracts = _parse_chain(payload, "2026-06-26")
    assert len(contracts) == 1
    c = contracts[0]
    assert c.gamma == pytest.approx(0.0495)
    assert c.open_interest == pytest.approx(8686)
    assert c.delta == pytest.approx(0.62)
    assert c.iv == pytest.approx(0.1879)
