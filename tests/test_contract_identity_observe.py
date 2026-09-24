"""Contract-identity guard, OBSERVE ONLY (#960 requirement, design #966 steps 1–4).

Proves: the normalizer never guesses; the payload, BracketOrder and bar
history carry `contract_hint` unchanged; the Tradovate adapter logs the
alert-vs-routed verdict right after contract resolution and still sends the
identical order; the alert-time observation records evidence without orders
and never changes a decision. Nothing here blocks.
"""
from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date

import pytest

import execution.tradovate_supervisor as supervisor
from context.bar_history import BarHistory
from execution import contract_identity as ci
from execution.broker_interface import BracketOrder
from execution.tradovate_broker import TradovateBroker, TradovateConfig
from webhook.payload import AlertPayload

D = date(2026, 9, 24)


# ── 1. normalizer (#966 §7.1) ────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("CME_MINI:MNQZ2026", "MNQZ2026"),
    ("MNQZ2026", "MNQZ2026"),
    ("mesh2027", "MESH2027"),
    ("  CME_MINI:MESZ2026 ", "MESZ2026"),
])
def test_normalize_accepts_full_and_bare_four_digit_forms(raw, expected):
    assert ci.normalize(raw) == expected


def test_one_digit_year_expands_only_with_a_date_context():
    assert ci.normalize("MNQZ6") is None                         # no context → never guess
    assert ci.normalize("MNQZ6", context_date=D) == "MNQZ2026"
    assert ci.normalize("MESH7", context_date=D) == "MESH2027"
    assert ci.normalize("MNQH5", context_date=D) == "MNQH2025"   # previous year still in window
    assert ci.normalize("MNQZ4", context_date=D) == "MNQZ2034"   # single year in the 10-year window


@pytest.mark.parametrize("raw", [
    None, "", "MNQ", "MNQ1!", "CME_MINI:MNQ1!", "MNQZ", "MNQZ26", "MNQZ20266",
    "M2KZ2026",           # unsupported root for this guard
    "MCLX2026",           # unsupported root
    "MNQF2027",           # non-quarterly month
    "ES:Z2026",
])
def test_normalize_rejects_everything_it_cannot_prove(raw):
    assert ci.normalize(raw, context_date=D) is None


# ── 2. compare verdicts ──────────────────────────────────────────────────────

def test_compare_verdicts():
    assert ci.compare("CME_MINI:MNQZ2026", "MNQZ6", context_date=D).status == ci.MATCH
    assert ci.compare("CME_MINI:MNQU2026", "MNQZ6", context_date=D).status == ci.MISMATCH
    assert ci.compare("CME_MINI:MESZ2026", "MNQZ6", context_date=D).status == ci.MISMATCH
    assert ci.compare(None, "MNQZ6", context_date=D).status == ci.UNKNOWN
    assert ci.compare("   ", "MNQZ6", context_date=D).status == ci.UNKNOWN
    assert ci.compare("MNQ1!", "MNQZ6", context_date=D).status == ci.UNNORMALIZABLE
    assert ci.compare("CME_MINI:MNQZ2026", "MNQ", context_date=D).status == ci.UNNORMALIZABLE
    v = ci.compare("CME_MINI:MNQZ2026", "MNQZ6", context_date=D)
    assert (v.hint, v.routed, v.hint_normalized, v.routed_normalized) == (
        "CME_MINI:MNQZ2026", "MNQZ6", "MNQZ2026", "MNQZ2026")


# ── 3. payload / order / bar-history plumbing ────────────────────────────────

def _payload(**extra) -> AlertPayload:
    data = {"ticker": "MNQ1!", "timestamp": "2026-09-24T14:30:00+00:00", "timeframe": "15",
            "open": 20000.0, "high": 20010.0, "low": 19990.0, "close": 20005.0}
    data.update(extra)
    return AlertPayload(**data)


def test_payload_hint_is_optional_and_preserved():
    assert _payload().contract_hint is None
    assert _payload(contract_hint="CME_MINI:MNQZ2026").contract_hint == "CME_MINI:MNQZ2026"


def test_bracket_order_hint_defaults_to_none():
    order = BracketOrder(instrument="MNQ", direction="LONG", entry=1, stop=0, target=3, rr_ratio=3, strategy="x")
    assert order.contract_hint is None


def test_bar_history_stores_hint_only_when_present(tmp_path):
    bh = BarHistory(log_dir=str(tmp_path))
    with_hint = bh.record("MNQ", ts="2026-09-24T14:30:00+00:00", open=1, high=2, low=0, close=1,
                          timeframe="15", contract_hint="CME_MINI:MNQZ2026")
    without = bh.record("MNQ", ts="2026-09-24T14:45:00+00:00", open=1, high=2, low=0, close=1, timeframe="15")
    assert with_hint["contract_hint"] == "CME_MINI:MNQZ2026"
    assert "contract_hint" not in without


# ── 4. Tradovate adapter: log verdict, send the identical order ──────────────

def _broker(monkeypatch):
    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    for k in ("TRADOVATE_USERNAME", "TRADOVATE_PASSWORD", "TRADOVATE_API_KEY_SECRET"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("TRADOVATE_API_KEY_ID", "1")
    for k in ("TRADOVATE_ENTRY_EXECUTION_MODE", "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
              "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "EXIT_MODE"):
        monkeypatch.delenv(k, raising=False)
    b = TradovateBroker(config=TradovateConfig.from_env())
    monkeypatch.setattr(b, "_authenticate", lambda: True)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)
    monkeypatch.setattr(TradovateBroker, "_trading_date", staticmethod(lambda: D))
    monkeypatch.setattr(b, "_get", lambda path, **k: [{"id": 12, "name": "MNQZ6"}])
    b._account_id = 999
    return b


def _order(hint):
    return BracketOrder(instrument="MNQ", direction="LONG", entry=20000.0, stop=19988.0, target=20036.0,
                        rr_ratio=3.0, strategy="orb_breakout", contract_hint=hint)


@pytest.mark.parametrize("hint,status", [
    ("CME_MINI:MNQZ2026", ci.MATCH),
    ("CME_MINI:MNQU2026", ci.MISMATCH),     # the #960 window: alert on old contract, routed to new
    (None, ci.UNKNOWN),
    ("MNQ1!", ci.UNNORMALIZABLE),
])
def test_adapter_logs_verdict_and_never_changes_or_blocks_the_order(monkeypatch, caplog, hint, status):
    baseline = _broker(monkeypatch)
    TradovateBroker._reset_client_order_registry()
    base_bodies: list[dict] = []
    monkeypatch.setattr(baseline, "_post", lambda path, body, **kw: base_bodies.append((path, body)) or {})
    baseline.execute_bracket(_order(None))

    b = _broker(monkeypatch)
    TradovateBroker._reset_client_order_registry()
    bodies: list[dict] = []
    monkeypatch.setattr(b, "_post", lambda path, body, **kw: bodies.append((path, body)) or {})
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="execution.tradovate_broker"):
        b.execute_bracket(_order(hint))

    lines = [r.getMessage() for r in caplog.records if "CONTRACT_IDENTITY observe" in r.getMessage()]
    assert len(lines) == 1 and f"status={status}" in lines[0] and "not enforced" in lines[0]
    assert "routed=MNQZ6" in lines[0]
    # Observe-only: the exact same requests go to Tradovate whatever the verdict.
    assert bodies == base_bodies and any(p == "/order/placeOSO" for p, _ in bodies)


def test_adapter_observe_failure_never_blocks(monkeypatch):
    b = _broker(monkeypatch)
    TradovateBroker._reset_client_order_registry()
    bodies: list = []
    monkeypatch.setattr(b, "_post", lambda path, body, **kw: bodies.append(path) or {})
    monkeypatch.setattr("execution.contract_identity.compare", lambda *a, **k: 1 / 0)
    b.execute_bracket(_order("CME_MINI:MNQZ2026"))
    assert "/order/placeOSO" in bodies


def test_blocked_resolution_still_happens_before_observation(monkeypatch, caplog):
    """Unchanged ordering: a failed contract resolution blocks as before and
    no identity line is written for an order that never resolved."""
    b = _broker(monkeypatch)
    monkeypatch.setattr(b, "_get", lambda path, **k: [{"id": 11, "name": "MNQU6"}])
    monkeypatch.setattr("execution.tradovate_broker.time.sleep", lambda *_: None)
    posted: list = []
    monkeypatch.setattr(b, "_post", lambda *a, **k: posted.append(a))
    with caplog.at_level(logging.INFO):
        fill = b.execute_bracket(_order("CME_MINI:MNQZ2026"))
    assert fill.exit_reason == "CONTRACT_RESOLUTION_FAILED" and posted == []
    assert not any("CONTRACT_IDENTITY observe" in r.getMessage() for r in caplog.records)


# ── 5. alert-time observation (evidence without orders) ──────────────────────

def _read_rows(tmp_path):
    path = tmp_path / ci.OBSERVE_FILENAME
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def _strip(result):
    return {k: v for k, v in result.items() if k not in ("timestamp", "processed_at", "latency_ms")}


def test_alert_observation_records_verdict_and_never_changes_the_decision(tmp_path, config):
    from webhook.runner import process_alert

    cfg = replace(config, enabled_concepts=[])
    a = process_alert(_payload(), config=cfg, log_dir=str(tmp_path / "a"), for_date=D)
    b = process_alert(_payload(contract_hint="CME_MINI:MNQZ2026"), config=cfg,
                      log_dir=str(tmp_path / "b"), for_date=D)
    assert a.get("decision") == b.get("decision")
    assert a.get("failed_gates") == b.get("failed_gates")
    # MNQ without a hint still gets a row: UNKNOWN, so false-null rates are visible.
    missing = _read_rows(tmp_path / "a")
    assert len(missing) == 1 and missing[0]["status"] == ci.UNKNOWN and missing[0]["hint"] is None
    assert missing[0]["instrument"] == "MNQ" and missing[0]["enforced"] is False
    rows = _read_rows(tmp_path / "b")
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "alert" and row["enforced"] is False and row["instrument"] == "MNQ"
    assert row["hint"] == "CME_MINI:MNQZ2026" and row["status"] in (ci.MATCH, ci.MISMATCH)
    assert row["routed_normalized"] and row["routed_normalized"].startswith("MNQ")
    bars = [json.loads(line) for line in (tmp_path / "b" / "bars_MNQ_2026-09-24.jsonl").read_text().splitlines()]
    assert bars[-1]["contract_hint"] == "CME_MINI:MNQZ2026"


def test_alert_observation_failure_never_breaks_ingestion(tmp_path, config, monkeypatch):
    from webhook.runner import process_alert

    monkeypatch.setattr("execution.contract_identity.record_observation", lambda *a, **k: 1 / 0)
    cfg = replace(config, enabled_concepts=[])
    out = process_alert(_payload(contract_hint="CME_MINI:MNQZ2026"), config=cfg,
                        log_dir=str(tmp_path), for_date=D)
    assert out.get("decision")


def test_no_roll_constant_or_price_conversion_added():
    src = open(ci.__file__, encoding="utf-8").read()
    assert "_ROLL_DAYS" not in src.replace("``_ROLL_DAYS``", "") and "timedelta" not in src
    assert "roll_days" not in src


def test_alert_observation_scope_unsupported_root_without_hint_writes_nothing(tmp_path):
    from webhook.runner import _observe_alert_contract_identity

    _observe_alert_contract_identity(_payload(ticker="ES1!", close=6000, open=6000, high=6001, low=5999),
                                     "ES", str(tmp_path))
    assert _read_rows(tmp_path) == []
    _observe_alert_contract_identity(_payload(ticker="MES1!", close=6000, open=6000, high=6001, low=5999),
                                     "MES", str(tmp_path))
    rows = _read_rows(tmp_path)
    assert len(rows) == 1 and rows[0]["instrument"] == "MES" and rows[0]["status"] == ci.UNKNOWN
    assert rows[0]["routed"] and rows[0]["routed"].startswith("MES")
