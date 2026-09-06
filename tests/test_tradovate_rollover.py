"""
tests/test_tradovate_rollover.py

Verification-only tests for the EXISTING front-month rollover logic
(_front_month_symbol / _find_contract_id). No rollover behavior was changed —
these lock what is already shipped:

- quarterly MES/MNQ roll selection (roll off a contract once inside its
  8-day pre-expiration window, including the December→March year wrap);
- the resolved specific contract symbol (never the bare root) is what routes
  into the order payload;
- the computed front-month symbol is preferred over the nearest-expiry
  (expiring) contract when both are listed;
- LiquidationOnly / BackMonthProhibited rejections classify explicitly and
  fail closed (covered in test_tradovate_execution_modes.py's provider-failure
  matrix; re-asserted here at the taxonomy level for the roll context).
"""
from __future__ import annotations

from datetime import date

import execution.tradovate_supervisor as supervisor
from execution.broker_interface import BracketOrder
from execution.no_fill_taxonomy import (
    NO_FILL_LIQUIDATION_ONLY,
    classify_provider_failure,
)
from execution.tradovate_broker import (
    TradovateBroker,
    TradovateConfig,
    _front_month_symbol,
    _third_friday,
)


# ── quarterly selection ──────────────────────────────────────────────────────

def test_third_friday_reference_dates():
    assert _third_friday(2026, 6) == date(2026, 6, 19)
    assert _third_friday(2026, 12) == date(2026, 12, 18)


def test_front_month_before_roll_window_keeps_current_quarter():
    # 2026-06-01 is 18 days before June expiry (2026-06-19) — outside the
    # 8-day roll window, so June (M) is still the front month.
    assert _front_month_symbol("MES", date(2026, 6, 1)) == "MESM6"
    assert _front_month_symbol("MNQ", date(2026, 6, 1)) == "MNQM6"


def test_front_month_inside_roll_window_rolls_forward():
    # 2026-06-12 is inside the 8-day window before 2026-06-19 → September (U).
    assert _front_month_symbol("MES", date(2026, 6, 12)) == "MESU6"
    assert _front_month_symbol("MNQ", date(2026, 6, 12)) == "MNQU6"


def test_front_month_year_wrap_december_to_march():
    # Inside December 2026's roll window → March 2027 (H7).
    assert _front_month_symbol("MES", date(2026, 12, 15)) == "MESH7"


def test_front_month_non_quarterly_root_returns_none():
    assert _front_month_symbol("MCL", date(2026, 6, 1)) is None


# ── resolved symbol routes into the order (never the bare root) ──────────────

def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setenv("TRADOVATE_USERNAME", "x")
    monkeypatch.setenv("TRADOVATE_PASSWORD", "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    monkeypatch.setenv("TRADOVATE_API_KEY_SECRET", "x")
    monkeypatch.delenv("TRADOVATE_ENTRY_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS", raising=False)
    monkeypatch.delenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", raising=False)
    monkeypatch.delenv("EXIT_MODE", raising=False)
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    b._account_id = 999
    TradovateBroker._reset_client_order_registry()
    return b


def test_resolved_front_month_symbol_preferred_and_routed(monkeypatch):
    b = _broker(monkeypatch)
    desired = _front_month_symbol("MES", __import__("datetime").datetime.now(
        __import__("zoneinfo").ZoneInfo("America/New_York")
    ).date())
    suggest = [
        {"id": 1, "name": "MESZ0"},          # stale/expiring listing first
        {"id": 2, "name": desired},
    ]
    monkeypatch.setattr(b, "_get", lambda path, **k: list(suggest))
    captured = {}

    def fake_post(path, body, **kw):
        captured["body"] = body
        return {}

    monkeypatch.setattr(b, "_post", fake_post)
    order = BracketOrder(
        instrument="MES", direction="LONG", entry=7559.5,
        stop=7557.0, target=7574.5, rr_ratio=6.0, strategy="orb_breakout",
    )
    b.execute_bracket(order)
    # The specific resolved contract symbol — not the root, not the expiring
    # nearest listing — is what reached the order payload (and the journal).
    assert captured["body"]["symbol"] == desired
    assert b._contract_symbol_cache["MES"] == desired
    assert b._contract_cache["MES"] == 2


# ── one long-running broker instance crossing the roll boundary ──────────────
#
# Sept 2026 expiry is Fri 2026-09-18; the 8-day window opens Fri 2026-09-11.
# Before the fix, _find_contract_id returned the cached ID before looking at
# the calendar, so a process that resolved MNQU6 on Sept 9 kept routing MNQU6
# through expiry. The cached ID is now trusted only while the calendar still
# wants the symbol it was resolved for.

_SUGGEST_MNQ = [
    {"id": 11, "name": "MNQU6"},
    {"id": 12, "name": "MNQZ6"},
]


def _stub_suggest(broker, monkeypatch, calls):
    def fake_get(path, **k):
        calls.append(path)
        return list(_SUGGEST_MNQ)
    monkeypatch.setattr(broker, "_get", fake_get)


def test_same_broker_instance_rerolls_u6_to_z6_across_the_boundary(monkeypatch):
    b = _broker(monkeypatch)
    calls: list[str] = []
    _stub_suggest(b, monkeypatch, calls)

    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: date(2026, 9, 9)))
    assert b._find_contract_id("MNQ") == 11
    assert b._contract_symbol_cache["MNQ"] == "MNQU6"
    assert len(calls) == 1

    # Same day, same instance: cached, no second lookup.
    assert b._find_contract_id("MNQ1!") == 11
    assert len(calls) == 1

    # Process is still alive on roll day: the cached U6 must be dropped.
    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: date(2026, 9, 11)))
    assert b._find_contract_id("MNQ") == 12
    assert b._contract_symbol_cache["MNQ"] == "MNQZ6"
    assert b._contract_cache["MNQ"] == 12
    assert len(calls) == 2

    # And Z6 is now the stable cached answer.
    assert b._find_contract_id("MNQ") == 12
    assert len(calls) == 2


def test_rerolled_symbol_reaches_the_order_payload(monkeypatch):
    b = _broker(monkeypatch)
    calls: list[str] = []
    _stub_suggest(b, monkeypatch, calls)
    bodies: list[dict] = []
    monkeypatch.setattr(b, "_post", lambda path, body, **kw: bodies.append(body) or {})
    order = BracketOrder(
        instrument="MNQ", direction="LONG", entry=20000.0,
        stop=19988.0, target=20036.0, rr_ratio=3.0, strategy="orb_breakout",
    )

    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: date(2026, 9, 9)))
    b.execute_bracket(order)
    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: date(2026, 9, 11)))
    TradovateBroker._reset_client_order_registry()  # second submit is a new day, not a duplicate
    b.execute_bracket(order)

    assert [body["symbol"] for body in bodies] == ["MNQU6", "MNQZ6"]


def test_non_quarterly_root_stays_cached(monkeypatch):
    # _front_month_symbol returns None for MCL on every date; None == None keeps
    # the old resolve-once behavior — no per-lookup HTTP call for those roots.
    b = _broker(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(b, "_get", lambda path, **k: calls.append(path) or [{"id": 5, "name": "MCLV6"}])
    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: date(2026, 9, 9)))
    assert b._find_contract_id("MCL") == 5
    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: date(2026, 9, 11)))
    assert b._find_contract_id("MCL") == 5
    assert len(calls) == 1


def test_back_month_prohibited_classifies_liquidation_only():
    assert classify_provider_failure("BackMonthProhibited") == NO_FILL_LIQUIDATION_ONLY
    assert classify_provider_failure("Account is LiquidationOnly") == NO_FILL_LIQUIDATION_ONLY
