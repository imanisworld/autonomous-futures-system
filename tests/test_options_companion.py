"""Tests for the Signa-driven companion options paper lane (options_companion/).

Pattern matches the repo: sync tests that drive async code via ``asyncio.run``,
mock providers (no live network), frozen-dataclass state via the conftest fixture.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from context.market_context import MarketState, SignaContext
from options_companion.chain_provider import ChainContract, ChainSnapshot, OptionQuote
from options_companion.evaluator import CompanionConfig, evaluate_companion
from options_companion.mapping import map_companion_candidates
from options_companion.resolver import resolve_open_companions
from options_companion.selection import CompanionSelection, SelectionRejected, select_contract
from options_companion.signa_gate import evaluate_companion_signa
from options_companion.status import companion_summary
from options_companion.store import OptionsCompanionStore

# 11:00 ET (before the 14:00 same-day cutoff); ET date = 2026-06-23.
NOW = datetime(2026, 6, 23, 15, 0, tzinfo=timezone.utc)
AFTER_CUTOFF = datetime(2026, 6, 23, 19, 0, tzinfo=timezone.utc)  # 15:00 ET
TODAY = date(2026, 6, 23)


# ─── helpers ──────────────────────────────────────────────────────────────────


class MockChainProvider:
    """In-memory ChainProvider for selection/resolution tests."""

    def __init__(self, snapshot: ChainSnapshot | None = None, quotes: dict | None = None):
        self.snapshot = snapshot
        self.quotes = quotes or {}
        self.last_error = None
        self.chain_calls: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def fetch_chain(self, underlying: str, *, max_dte: int) -> ChainSnapshot:
        self.chain_calls.append(underlying)
        if self.snapshot is not None:
            return self.snapshot
        return ChainSnapshot(underlying, error="no_data")

    async def fetch_quote(self, option_symbol: str) -> OptionQuote:
        return self.quotes.get(option_symbol, OptionQuote(option_symbol, error="no_quote"))


def _contract(symbol, dte, strike, ctype, bid, ask, delta=None, *, base=TODAY):
    return ChainContract(
        symbol=symbol,
        expiry=date.fromordinal(base.toordinal() + dte),
        strike=strike,
        contract_type=ctype,
        bid=bid,
        ask=ask,
        delta=delta,
    )


def _good_chain(underlying="QQQ", ctype="CALL"):
    """A clean chain: same-day, mid 1.05 (premium $105), tight spread, delta in band."""
    return ChainSnapshot(
        underlying=underlying,
        underlying_price=500.0,
        contracts=[
            _contract(f"{underlying}_0", 0, 505, ctype, 1.00, 1.10, delta=0.38),
            _contract(f"{underlying}_1", 0, 510, ctype, 0.40, 0.50, delta=0.20),
            _contract(f"{underlying}_2", 1, 505, ctype, 1.50, 1.60, delta=0.40),
        ],
    )


def _state(fresh_market_state, *, instrument="MNQ", direction_session="new_york",
           grade="A", daily="UP"):
    st = replace(
        fresh_market_state,
        instrument=instrument,
        session=direction_session,
        signa=SignaContext(grade=grade, score=78.0, daily_direction=daily, weekly_direction=daily)
        if grade is not None or daily is not None
        else None,
    )
    return st


def _run(coro):
    return asyncio.run(coro)


def _evaluate(state, store, provider, *, instrument, direction, now=NOW, config=None):
    return _run(
        evaluate_companion(
            state=state,
            futures_instrument=instrument,
            futures_direction=direction,
            provider=provider,
            store=store,
            config=config or CompanionConfig(),
            now=now,
        )
    )


# ─── 1. futures non-fills never reach this lane (runner-level hook gating) ───────
# The create hook lives ONLY in the OPEN success branch of process_alert, so any
# non-fill decision (NO_TRADE / RISK_REJECTED / BLOCKED_*) produces NO companion
# row. These tests drive the REAL runner to prove the wiring, not just the helpers.


class TestRunnerHookGating:
    def _companion_cfg(self, tmp_path, *, enabled: bool):
        from config.settings import load_config

        return replace(
            load_config(),
            max_staleness_seconds=10_000_000,  # fixed-timestamp bars aren't stale
            options_companion_enabled=enabled,
            options_companion_sqlite_path=str(tmp_path / "companion.sqlite"),
        )

    def _no_trade_payload(self):
        from tests.test_webhook import _base_payload

        # A CHOPPY MNQ bar forms no tradable setup -> NO_TRADE (no fill).
        return _base_payload(
            timestamp=datetime(2026, 6, 5, 1, 0, tzinfo=timezone.utc).isoformat(),
            market_condition="CHOPPY",
            trend_direction="DOWN",
        )

    def test_non_fill_creates_no_companion_row_when_enabled(self, tmp_path):
        from webhook.runner import process_alert

        cfg = self._companion_cfg(tmp_path, enabled=True)
        result = process_alert(
            self._no_trade_payload(), config=cfg, log_dir=str(tmp_path / "logs"),
            for_date=date(2026, 6, 5),
        )
        # Sanity: this bar did NOT open a position.
        assert (result.get("fill") or {}).get("status") != "OPEN"
        # The create hook never fired -> zero rows. (The per-webhook resolve hook
        # ran but had no OPEN rows to touch, so it adds nothing either.)
        store = OptionsCompanionStore(tmp_path / "companion.sqlite")
        assert store.all_rows() == []
        assert "companion" not in result  # create hook attaches only on OPEN

    def test_disabled_lane_writes_nothing(self, tmp_path):
        from webhook.runner import process_alert

        cfg = self._companion_cfg(tmp_path, enabled=False)
        db = tmp_path / "companion.sqlite"
        result = process_alert(
            self._no_trade_payload(), config=cfg, log_dir=str(tmp_path / "logs"),
            for_date=date(2026, 6, 5),
        )
        assert "companion" not in result
        # Disabled lane never even touches the ledger file.
        assert not db.exists()


class TestMapping:
    def test_unknown_root_yields_no_candidate(self):
        assert map_companion_candidates("CL", "LONG") == []

    def test_non_directional_yields_no_candidate(self):
        assert map_companion_candidates("MNQ", "FLAT") == []

    def test_mnq_long_is_qqq_call(self):
        assert map_companion_candidates("MNQU6", "LONG") == [("QQQ", "CALL")]

    def test_mes_short_is_spy_put(self):
        assert map_companion_candidates("MES", "SHORT") == [("SPY", "PUT")]

    def test_no_spx_row_in_v1(self, fresh_market_state, tmp_path):
        # SPX deferred: a MES trade produces exactly one SPY candidate, no SPX.
        cands = map_companion_candidates("MES", "SHORT")
        assert [u for u, _ in cands] == ["SPY"]


class TestEvaluatorMappingGate:
    def test_unmapped_instrument_creates_no_row(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        provider = MockChainProvider(_good_chain())
        state = _state(fresh_market_state, instrument="CL")
        out = _evaluate(state, store, provider, instrument="CL", direction="LONG")
        assert out["candidates"] == []
        assert store.all_rows() == []
        assert provider.chain_calls == []  # never even fetched a chain


# ─── 2 / 3 / 4. Signa gate ───────────────────────────────────────────────────


class TestSignaGate:
    def test_mnq_long_grade_a_up_creates_qqq_call(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        provider = MockChainProvider(_good_chain("QQQ", "CALL"))
        state = _state(fresh_market_state, instrument="MNQ", grade="A", daily="UP")
        out = _evaluate(state, store, provider, instrument="MNQ", direction="LONG")
        rows = store.all_rows()
        assert len(rows) == 1
        assert rows[0].status == "OPEN"
        assert rows[0].underlying == "QQQ"
        assert rows[0].contract_type == "CALL"

    def test_grade_b_passes(self, fresh_market_state):
        state = _state(fresh_market_state, grade="B", daily="UP")
        assert evaluate_companion_signa(state, "LONG").passed

    def test_signa_opposes_is_rejected_row(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        provider = MockChainProvider(_good_chain())
        state = _state(fresh_market_state, instrument="MNQ", grade="A", daily="DOWN")
        out = _evaluate(state, store, provider, instrument="MNQ", direction="LONG")
        rows = store.all_rows()
        assert len(rows) == 1
        assert rows[0].status == "REJECTED"
        assert rows[0].risk_failed_rule == "signa_opposes"
        assert provider.chain_calls == []  # rejected before fetching a chain

    @pytest.mark.parametrize(
        "grade,daily,rule",
        [
            ("C", "UP", "signa_grade"),
            ("F", "UP", "signa_grade"),
            (None, "UP", "signa_grade"),       # missing/stale grade
            ("A", "NEUTRAL", "signa_daily_neutral"),
            ("A", None, "signa_daily_neutral"),  # missing daily direction
        ],
    )
    def test_fail_closed_cases(self, fresh_market_state, grade, daily, rule):
        state = _state(fresh_market_state, grade=grade, daily=daily)
        res = evaluate_companion_signa(state, "LONG")
        assert not res.passed
        assert res.failed_rule == rule

    def test_missing_signa_fails_closed(self, fresh_market_state):
        state = replace(fresh_market_state, signa=None)
        res = evaluate_companion_signa(state, "LONG")
        assert not res.passed
        assert res.failed_rule == "signa_missing"


# ─── 5. MES short -> SPY put (no SPX) ────────────────────────────────────────


class TestMesShort:
    def test_mes_short_down_creates_spy_put_only(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        provider = MockChainProvider(_good_chain("SPY", "PUT"))
        state = _state(fresh_market_state, instrument="MES", grade="A", daily="DOWN")
        out = _evaluate(state, store, provider, instrument="MES", direction="SHORT")
        rows = store.all_rows()
        assert len(rows) == 1
        assert rows[0].underlying == "SPY"
        assert rows[0].contract_type == "PUT"
        assert all(r.underlying != "SPX" for r in rows)


# ─── 6. quote / spread rejections ────────────────────────────────────────────


class TestSelectionData:
    def test_missing_quotes_market_data_unavailable(self):
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[_contract("X", 0, 505, "CALL", None, None, delta=0.38)],
        )
        res = select_contract(snap, "CALL", now=NOW)
        assert isinstance(res, SelectionRejected)
        assert res.failed_rule == "market_data_unavailable"

    def test_wide_spread_rejected(self):
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[_contract("X", 0, 505, "CALL", 1.00, 1.40, delta=0.38)],
        )
        res = select_contract(snap, "CALL", now=NOW)
        assert isinstance(res, SelectionRejected)
        assert res.failed_rule == "spread_too_wide"

    def test_chain_error_is_unavailable(self):
        res = select_contract(ChainSnapshot("QQQ", error="credentials_missing"), "CALL", now=NOW)
        assert isinstance(res, SelectionRejected)
        assert res.failed_rule == "market_data_unavailable"


# ─── 7. expiry rules ─────────────────────────────────────────────────────────


class TestExpiry:
    def test_same_day_chosen_before_cutoff(self):
        snap = _good_chain()
        res = select_contract(snap, "CALL", now=NOW)
        assert isinstance(res, CompanionSelection)
        assert res.dte == 0

    def test_after_cutoff_falls_to_next_dte(self):
        snap = _good_chain()
        res = select_contract(snap, "CALL", now=AFTER_CUTOFF)
        assert isinstance(res, CompanionSelection)
        assert res.dte == 1

    def test_no_valid_expiry_when_all_far(self):
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[_contract("X", 5, 505, "CALL", 1.0, 1.1, delta=0.38)],
        )
        res = select_contract(snap, "CALL", now=NOW, max_dte=2)
        assert isinstance(res, SelectionRejected)
        assert res.failed_rule == "no_valid_expiry"

    def test_after_cutoff_only_zero_dte_rejects(self):
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[_contract("X", 0, 505, "CALL", 1.0, 1.1, delta=0.38)],
        )
        res = select_contract(snap, "CALL", now=AFTER_CUTOFF)
        assert isinstance(res, SelectionRejected)
        assert res.failed_rule == "no_valid_expiry"


# ─── 8. strike selection ─────────────────────────────────────────────────────


class TestStrike:
    def test_delta_in_band_chosen(self):
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[
                _contract("lo", 0, 520, "CALL", 0.20, 0.25, delta=0.15),
                _contract("mid", 0, 505, "CALL", 1.00, 1.10, delta=0.38),
                _contract("hi", 0, 495, "CALL", 3.00, 3.10, delta=0.70),
            ],
        )
        res = select_contract(snap, "CALL", now=NOW)
        assert isinstance(res, CompanionSelection)
        assert res.option_symbol == "mid"
        assert 0.30 <= abs(res.delta) <= 0.45

    def test_nearest_otm_fallback_without_greeks(self):
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[
                _contract("itm", 0, 495, "CALL", 6.0, 6.1),
                _contract("otm1", 0, 502, "CALL", 1.0, 1.1),
                _contract("otm2", 0, 510, "CALL", 0.4, 0.5),
            ],
        )
        res = select_contract(snap, "CALL", now=NOW)
        assert isinstance(res, CompanionSelection)
        assert res.option_symbol == "otm1"  # nearest strike >= spot


# ─── 9. premium caps ─────────────────────────────────────────────────────────


class TestPremiumCaps:
    def test_oversized_premium_rejected(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        # mid 3.05 -> $305 > $250 flat cap
        snap = ChainSnapshot(
            "QQQ", underlying_price=500.0,
            contracts=[_contract("big", 0, 505, "CALL", 3.00, 3.10, delta=0.38)],
        )
        provider = MockChainProvider(snap)
        state = _state(fresh_market_state, instrument="MNQ", grade="A", daily="UP")
        _evaluate(state, store, provider, instrument="MNQ", direction="LONG")
        rows = store.all_rows()
        assert len(rows) == 1
        assert rows[0].status == "REJECTED"
        assert rows[0].risk_failed_rule == "premium_per_contract"

    def test_per_underlying_cap_override(self):
        from risk.options_risk_engine import (
            OptionsDailyState,
            OptionsRiskConfig,
            OptionsRiskEngine,
            OptionTradePlan,
        )

        cfg = OptionsRiskConfig(
            enabled=True,
            allowed_underlyings=["SPX"],
            min_rr_ratio=2.0,
            max_premium_per_contract=250.0,
            max_total_premium=250.0,
            premium_caps_by_underlying={"SPX": 500.0},
            require_confluence_grade="",
        )
        engine = OptionsRiskEngine(cfg)
        # $400 premium: blocked by flat $250 but allowed under SPX $500 override.
        plan = OptionTradePlan(
            underlying="SPX", symbol="SPXW_x", contract_type="CALL", side="BUY",
            quantity=1, entry_premium=4.00, stop_premium=2.00, target_premium=8.00,
            strategy="companion", session="new_york", timestamp=NOW,
        )
        res = engine.validate(plan, OptionsDailyState(), broker_is_live=False)
        assert res.approved


# ─── 10. daily + per-underlying open limits ──────────────────────────────────


class TestLimits:
    def test_daily_limit_blocks_fourth(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        # 3 already-resolved QQQ trades today (counted) -> 4th rejected.
        for i in range(3):
            store.record(
                futures_instrument="MNQ", futures_direction="LONG", underlying="QQQ",
                status="WIN", created_at=NOW, paper_pnl_dollars=10.0, entry_mark=1.0,
            )
        provider = MockChainProvider(_good_chain("QQQ", "CALL"))
        state = _state(fresh_market_state, instrument="MNQ", grade="A", daily="UP")
        _evaluate(state, store, provider, instrument="MNQ", direction="LONG")
        new_rows = [r for r in store.all_rows() if r.status == "REJECTED"]
        assert any(r.risk_failed_rule == "daily_trade_limit" for r in new_rows)

    def test_open_qqq_blocks_qqq_not_spy(self, fresh_market_state, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        store.record(
            futures_instrument="MNQ", futures_direction="LONG", underlying="QQQ",
            status="OPEN", created_at=NOW, entry_mark=1.0,
        )
        # QQQ candidate -> blocked (max_open_positions per underlying = 1)
        p1 = MockChainProvider(_good_chain("QQQ", "CALL"))
        s1 = _state(fresh_market_state, instrument="MNQ", grade="A", daily="UP")
        _evaluate(s1, store, p1, instrument="MNQ", direction="LONG")
        # SPY candidate -> allowed (different underlying)
        p2 = MockChainProvider(_good_chain("SPY", "PUT"))
        s2 = _state(fresh_market_state, instrument="MES", grade="A", daily="DOWN")
        _evaluate(s2, store, p2, instrument="MES", direction="SHORT")

        qqq = [r for r in store.all_rows() if r.underlying == "QQQ"]
        spy = [r for r in store.all_rows() if r.underlying == "SPY"]
        assert any(r.status == "REJECTED" and r.risk_failed_rule == "max_open_positions" for r in qqq)
        assert any(r.status == "OPEN" for r in spy)


# ─── 11. bracket math ────────────────────────────────────────────────────────


class TestBracket:
    def test_bracket_is_2r(self):
        snap = _good_chain()
        res = select_contract(snap, "CALL", now=NOW)
        assert isinstance(res, CompanionSelection)
        assert res.stop_mark == pytest.approx(res.entry_mark * 0.5)
        assert res.target_mark == pytest.approx(res.entry_mark * 2.0)
        rr = (res.target_mark - res.entry_mark) / (res.entry_mark - res.stop_mark)
        assert rr == pytest.approx(2.0)


# ─── 12. resolution ──────────────────────────────────────────────────────────


class TestResolution:
    def _open_row(self, store):
        return store.record(
            futures_instrument="MNQ", futures_direction="LONG", underlying="QQQ",
            status="OPEN", option_symbol="QQQ_0", contract_type="CALL",
            expiry=TODAY.isoformat(), strike=505.0, dte=0,
            entry_mark=1.00, stop_mark=0.50, target_mark=2.00, created_at=NOW,
        )

    def test_win_when_mid_at_target(self, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        rid = self._open_row(store)
        provider = MockChainProvider(quotes={"QQQ_0": OptionQuote("QQQ_0", bid=2.00, ask=2.10)})
        _run(resolve_open_companions(provider, store, now=NOW))
        row = [r for r in store.all_rows() if r.id == rid][0]
        assert row.status == "WIN"
        assert row.paper_pnl_dollars == pytest.approx(100.0)  # (2.00-1.00)*100

    def test_loss_when_mid_at_stop(self, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        rid = self._open_row(store)
        provider = MockChainProvider(quotes={"QQQ_0": OptionQuote("QQQ_0", bid=0.45, ask=0.50)})
        _run(resolve_open_companions(provider, store, now=NOW))
        row = [r for r in store.all_rows() if r.id == rid][0]
        assert row.status == "LOSS"
        assert row.paper_pnl_dollars == pytest.approx(-50.0)  # (0.50-1.00)*100

    def test_expired_when_open_past_expiry(self, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        rid = self._open_row(store)
        # mid between stop/target, but now is the day AFTER expiry -> EXPIRED
        provider = MockChainProvider(quotes={"QQQ_0": OptionQuote("QQQ_0", bid=1.00, ask=1.10)})
        next_day = datetime(2026, 6, 24, 15, 0, tzinfo=timezone.utc)
        _run(resolve_open_companions(provider, store, now=next_day))
        row = [r for r in store.all_rows() if r.id == rid][0]
        assert row.status == "EXPIRED"

    def test_open_stays_open_midband(self, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        rid = self._open_row(store)
        provider = MockChainProvider(quotes={"QQQ_0": OptionQuote("QQQ_0", bid=1.20, ask=1.30)})
        _run(resolve_open_companions(provider, store, now=NOW))
        row = [r for r in store.all_rows() if r.id == rid][0]
        assert row.status == "OPEN"


# ─── status summary ──────────────────────────────────────────────────────────


class TestStatus:
    def test_summary_counts(self, tmp_path):
        store = OptionsCompanionStore(tmp_path / "c.sqlite")
        store.record(futures_instrument="MNQ", futures_direction="LONG", underlying="QQQ",
                     status="WIN", paper_pnl_dollars=100.0, created_at=NOW)
        store.record(futures_instrument="MNQ", futures_direction="LONG", underlying="QQQ",
                     status="LOSS", paper_pnl_dollars=-50.0, created_at=NOW)
        store.record(futures_instrument="MES", futures_direction="SHORT", underlying="SPY",
                     status="REJECTED", risk_failed_rule="signa_grade", created_at=NOW)
        summary = companion_summary(store)
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert summary["rejected"] == 1
        assert summary["win_rate_percent"] == 50.0
        assert summary["total_paper_pnl_dollars"] == pytest.approx(50.0)
