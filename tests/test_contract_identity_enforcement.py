"""U8: exact contract identity on the execution-eligible Tradovate path."""
from __future__ import annotations

import types
from datetime import date

import pytest

import execution.tradovate_broker as tb
import execution.tradovate_supervisor as supervisor
from execution import no_fill_taxonomy as nft
from execution.broker_interface import BracketOrder
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from tests.fault_injection._harness import (
    FakeBook,
    journal_rows,
    make_broker,
    mes_payload,
    real_broker_cfg,
    run_alert,
)

D = date(2026, 9, 24)  # MNQ front month = MNQZ6 (U6 rolled off on 2026-09-10)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("CONTRACT_IDENTITY_GUARD_ENFORCED", raising=False)
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "6")


def _broker(monkeypatch, *, suggest=({"id": 12, "name": "MNQZ6"},)):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_API_KEY_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    for k in ("TRADOVATE_ENTRY_EXECUTION_MODE", "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
              "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "EXIT_MODE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRADOVATE_EXPECTED_ACCOUNT_ID", "999")
    TradovateBroker._reset_client_order_registry()
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "get_account_balance", lambda: 50_000.0)
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: D))
    gets: list[str] = []
    monkeypatch.setattr(b, "_get", lambda path, **k: gets.append(path) or list(suggest))
    monkeypatch.setattr(tb.time, "sleep", lambda *a, **k: None)
    posts: list[str] = []
    monkeypatch.setattr(b, "_post", lambda path, body, **kw: posts.append(path) or {})
    b._account_id = 999
    return b, gets, posts


def _order(hint, instrument="MNQ"):
    return BracketOrder(instrument=instrument, direction="LONG", entry=20000.0, stop=19988.0,
                        target=20036.0, rr_ratio=3.0, strategy="orb_breakout", contract_hint=hint)


# ─── Enforcement flag (#966 §6) ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "hint,status",
    [
        (None, "CONTRACT_IDENTITY_UNKNOWN"),
        ("MNQ1!", "CONTRACT_IDENTITY_UNNORMALIZABLE"),
        ("CME_MINI:MNQU2026", "CONTRACT_IDENTITY_MISMATCH"),
    ],
)
def test_enforced_guard_blocks_before_any_order_request(monkeypatch, hint, status):
    monkeypatch.setenv("CONTRACT_IDENTITY_GUARD_ENFORCED", "true")
    b, _, posts = _broker(monkeypatch)
    fill = b.execute_bracket(_order(hint))
    assert fill.result == "CANCELLED" and fill.exit_reason == status
    assert posts == []


def test_enforced_guard_allows_exact_match(monkeypatch):
    monkeypatch.setenv("CONTRACT_IDENTITY_GUARD_ENFORCED", "true")
    b, _, posts = _broker(monkeypatch)
    b.execute_bracket(_order("CME_MINI:MNQZ2026"))
    assert "/order/placeOSO" in posts


@pytest.mark.parametrize("value", ["maybe", "TRUE", "1", "yes", "on"])
def test_unrecognized_or_truthy_flag_value_enforces(monkeypatch, value):
    monkeypatch.setenv("CONTRACT_IDENTITY_GUARD_ENFORCED", value)
    assert tb._contract_identity_enforced() is True


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "FALSE"])
def test_unset_or_false_flag_is_observe_only(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("CONTRACT_IDENTITY_GUARD_ENFORCED", raising=False)
    else:
        monkeypatch.setenv("CONTRACT_IDENTITY_GUARD_ENFORCED", value)
    assert tb._contract_identity_enforced() is False


def test_observe_mode_still_routes_on_mismatch(monkeypatch):
    b, _, posts = _broker(monkeypatch)
    b.execute_bracket(_order("CME_MINI:MNQU2026"))
    assert "/order/placeOSO" in posts


# ─── No fallback becomes routable ──────────────────────────────────────────


def test_root_without_dated_policy_never_asks_for_suggestions(monkeypatch):
    b, gets, posts = _broker(monkeypatch, suggest=({"id": 5, "name": "MGCZ6"},))
    order = BracketOrder(instrument="MGC", direction="LONG", entry=2400.0, stop=2390.0,
                         target=2430.0, rr_ratio=3.0, strategy="t")
    fill = b.execute_bracket(order)
    assert fill.exit_reason == "CONTRACT_RESOLUTION_FAILED"
    assert not any("/contract/suggest" in g for g in gets) and posts == []


def test_wrong_expiry_suggestion_is_never_substituted(monkeypatch):
    b, _, posts = _broker(monkeypatch, suggest=({"id": 11, "name": "MNQU6"}, {"id": 13, "name": "MNQH7"}))
    fill = b.execute_bracket(_order("CME_MINI:MNQZ2026"))
    assert fill.exit_reason == "CONTRACT_RESOLUTION_FAILED" and posts == []


def test_unrecorded_routed_symbol_never_falls_back_to_root(monkeypatch):
    b, _, posts = _broker(monkeypatch)
    # A resolver that returns an id but no exact symbol (the old root fallback).
    monkeypatch.setattr(b, "_find_contract_id", types.MethodType(lambda self, inst: 12, b))
    fill = b.execute_bracket(_order("CME_MINI:MNQZ2026"))
    assert fill.exit_reason == "CONTRACT_IDENTITY_UNRESOLVED" and posts == []


def test_stale_routed_symbol_is_refused(monkeypatch):
    b, _, posts = _broker(monkeypatch)

    def stale(self, inst):
        self._contract_symbol_cache["MNQ"] = "MNQU6"  # expiring contract
        return 11

    monkeypatch.setattr(b, "_find_contract_id", types.MethodType(stale, b))
    fill = b.execute_bracket(_order("CME_MINI:MNQU2026"))
    assert fill.exit_reason == "CONTRACT_IDENTITY_UNRESOLVED" and posts == []


@pytest.mark.parametrize(
    "reason",
    [
        "CONTRACT_METADATA_UNSUPPORTED",
        "CONTRACT_RESOLUTION_FAILED",
        "CONTRACT_IDENTITY_UNRESOLVED",
        "CONTRACT_IDENTITY_UNKNOWN",
        "CONTRACT_IDENTITY_UNNORMALIZABLE",
        "CONTRACT_IDENTITY_MISMATCH",
    ],
)
def test_contract_refusals_are_in_the_no_fill_taxonomy(reason):
    assert nft.classify_no_fill_reason(reason) == nft.NO_FILL_SESSION_OR_RISK_CANCEL


# ─── Journal / evidence carries the exact contract ─────────────────────────


def test_opened_fill_and_trade_journal_row_record_exact_contract(config, tmp_path, monkeypatch):
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book)
    log_dir = tmp_path / "logs"
    run_alert(monkeypatch, broker, real_broker_cfg(config, working_order_recheck=False), log_dir, mes_payload())
    trades = [r for r in journal_rows(log_dir) if r.get("decision") == "TRADE"]
    assert trades, "expected a TRADE row"
    identity = trades[-1]["execution_audit"]["contract_identity"]
    expected = tb._front_month_symbol("MES", broker._trading_date())
    assert identity["routed_contract"] == expected
    assert identity["expected_front_month"] == expected
    assert identity["enforced"] is False
    assert identity["status"] in {"MATCH", "CONTRACT_IDENTITY_UNKNOWN",
                                  "CONTRACT_IDENTITY_UNNORMALIZABLE", "CONTRACT_IDENTITY_MISMATCH"}


def test_reverse_contract_lookup_uses_exact_canonical_symbol(monkeypatch):
    b, _, _ = _broker(monkeypatch)
    monkeypatch.setattr(b, "_get", lambda path, **k: {"name": "MNQZ6"})
    assert b._contract_id_to_name(12) == "MNQ"
    monkeypatch.setattr(b, "_get", lambda path, **k: {"name": "MNQX"})
    assert b._contract_id_to_name(12) is None


def test_position_snapshot_does_not_fabricate_mes_when_contract_identity_unknown(monkeypatch):
    b, _, _ = _broker(monkeypatch)

    def fake_get(path, **_kwargs):
        if path == "/position/list":
            return [{"netPos": 1, "contractId": 77, "netPrice": 20000.0}]
        if path == "/contract/item?id=77":
            return {"name": "UNKNOWN77"}
        raise AssertionError(path)

    monkeypatch.setattr(b, "_get", fake_get)
    confirmed, position = b.get_position_snapshot()
    assert confirmed is False
    assert position is None
    assert b._last_position is None
