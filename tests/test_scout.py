"""Tests for the read-only Scout adapter (PAPER / CONTEXT ONLY).

Verifies parsing fidelity, symbol mapping, the four classifications, the
paper-eligibility gate, fail-closed behavior, and the hard safety contract
(Scout never authorizes execution; no broker code is imported or called).
"""

from __future__ import annotations

import json
import pathlib

import sources.scout as scout_mod
from journal.journal_logger import JournalLogger
from sources.scout import (
    BLOCKED,
    CONFIRMATION,
    CONFLICT,
    WATCH_ONLY,
    ScoutSignal,
    classify_scout,
    scout_paper_eligible,
    parse_scout_alert,
)

BUY_A = """[3] NQ1! • BUY
A (9/12)
NQ1!-260608-2315-651
Continuation • Bullish Trend
Entry: 29454.50
TP1: 29488.00 (+0.1%)
TP2: 29521.25 (+0.2%)
Stop: 29410.00 (-0.2%)
R:R: 1:0.7
Vol: ✅
Vola: ✅
Trend: ⚠️
Mom: ✅
Nova Insight: Strong directional bias with solid structure confirmation.
23:15:00"""

SELL_B = """[3] NQ1! • SELL
B (7/12)
NQ1!-260608-1900-648
Reversal • Bearish Trend
Entry: 29542.00
TP1: 29506.25 (+0.1%)
TP2: 29470.50 (+0.2%)
Stop: 29589.75 (0.2%)
R:R: 1:0.8
Vol: ❌
Vola: ✅
Trend: ⚠️
Mom: ⚠️
Nova Insight: Moderate setup — validate confirmation before execution.
19:00:00"""


# ── Parsing fidelity ─────────────────────────────────────────────────────────

def test_buy_example_parses_all_fields():
    n = parse_scout_alert(BUY_A)
    assert n.ok is True
    assert n.source == "scout"
    assert n.symbol_raw == "NQ1!"
    assert n.symbol_mapped == "MNQ"          # NQ1! -> MNQ
    assert n.side == "BUY"
    assert n.grade == "A"
    assert n.score == 9
    assert n.score_max == 12
    assert n.setup_type == "Continuation"
    assert n.bias.startswith("Bullish")
    assert n.entry == 29454.50
    assert n.tp1 == 29488.00
    assert n.tp2 == 29521.25
    assert n.stop == 29410.00
    assert n.rr == 0.7                        # reward/risk from "1:0.7"
    assert n.volume_pass is True
    assert n.volatility_pass is True
    assert n.trend_pass is False              # ⚠️ is not a clean pass
    assert n.momentum_pass is True
    assert n.alert_id == "NQ1!-260608-2315-651"
    assert n.alert_time == "23:15:00"
    assert "directional bias" in n.insight


def test_sell_example_parses_all_fields():
    n = parse_scout_alert(SELL_B)
    assert n.ok is True
    assert n.side == "SELL"
    assert n.symbol_mapped == "MES" if n.symbol_raw == "ES1!" else n.symbol_mapped == "MNQ"
    assert n.grade == "B"
    assert n.score == 7
    assert n.score_max == 12
    assert n.setup_type == "Reversal"
    assert n.entry == 29542.00
    assert n.stop == 29589.75
    assert n.rr == 0.8
    assert n.volume_pass is False             # ❌
    assert n.volatility_pass is True
    assert n.momentum_pass is False           # ⚠️
    assert n.internal_side() == "SHORT"


def test_nq_maps_to_mnq_and_es_maps_to_mes():
    assert parse_scout_alert(BUY_A).symbol_mapped == "MNQ"
    es = SELL_B.replace("NQ1!", "ES1!")
    assert parse_scout_alert(es).symbol_mapped == "MES"


# ── Classification rules (item 10) ───────────────────────────────────────────

def _gates_ok(scout, **kw):
    base = dict(
        internal_signal_side="LONG",
        internal_signal_present=True,
        session_allowed=True,
        risk_allowed=True,
    )
    base.update(kw)
    return classify_scout(scout, **base)


def test_buy_becomes_confirmation_only_when_internal_buy_exists():
    n = parse_scout_alert(BUY_A)
    # Internal LONG present -> CONFIRMATION
    res = _gates_ok(n, internal_signal_side="LONG", internal_signal_present=True)
    assert res["final_decision"] == CONFIRMATION
    # No internal signal -> NOT confirmation (watch-only instead)
    res2 = _gates_ok(n, internal_signal_side=None, internal_signal_present=False)
    assert res2["final_decision"] != CONFIRMATION


def test_sell_becomes_conflict_when_internal_buy_exists():
    n = parse_scout_alert(SELL_B)
    res = _gates_ok(n, internal_signal_side="LONG", internal_signal_present=True)
    assert res["final_decision"] == CONFLICT
    assert "conflict" in res["reason"].lower()


def test_scout_only_signal_becomes_watch_only():
    n = parse_scout_alert(BUY_A)
    res = _gates_ok(n, internal_signal_side=None, internal_signal_present=False)
    assert res["final_decision"] == WATCH_ONLY


def test_failed_gate_forces_blocked():
    n = parse_scout_alert(BUY_A)
    assert _gates_ok(n, session_allowed=False)["final_decision"] == BLOCKED
    assert _gates_ok(n, risk_allowed=False)["final_decision"] == BLOCKED


# ── Paper-eligibility gate (item 12) ─────────────────────────────────────────

def test_rr_below_one_blocks_paper_eligibility():
    # BUY example is A/9 with vol+vola+mom pass, but rr=0.7 < 1.0 -> ineligible
    n = parse_scout_alert(BUY_A)
    assert scout_paper_eligible(
        n, internal_agrees=True, session_allowed=True, risk_allowed=True
    ) is False
    # Same signal but rr lifted to >= 1.0 -> eligible
    good = ScoutSignal(**{**n.__dict__, "rr": 1.2})
    assert scout_paper_eligible(
        good, internal_agrees=True, session_allowed=True, risk_allowed=True
    ) is True


def test_paper_eligibility_requires_internal_agreement_and_gates():
    n = parse_scout_alert(BUY_A)
    good = ScoutSignal(**{**n.__dict__, "rr": 1.2})
    assert scout_paper_eligible(good, internal_agrees=False, session_allowed=True, risk_allowed=True) is False
    assert scout_paper_eligible(good, internal_agrees=True, session_allowed=False, risk_allowed=True) is False
    assert scout_paper_eligible(good, internal_agrees=True, session_allowed=True, risk_allowed=False) is False


# ── Fail-closed (item 4) ─────────────────────────────────────────────────────

def test_malformed_payload_returns_parse_error_without_crashing():
    for bad in ["", "   ", "hello world", "random text no fields", None, 12345]:
        n = parse_scout_alert(bad)  # type: ignore[arg-type]
        assert n.ok is False
        assert n.error is not None
    # And classify of a parse_error is BLOCKED, never a trade
    res = classify_scout(
        parse_scout_alert("garbage"),
        internal_signal_side="LONG",
        internal_signal_present=True,
        session_allowed=True,
        risk_allowed=True,
    )
    assert res["final_decision"] == BLOCKED
    assert res["scout_trade_authorized"] is False


def test_partial_payload_missing_entry_fails_closed():
    n = parse_scout_alert("[3] NQ1! • BUY\nA (9/12)")  # no entry
    assert n.ok is False
    assert "entry" in (n.error or "")


# ── Hard safety contract ─────────────────────────────────────────────────────

def test_scout_never_authorizes_a_trade():
    for side, internal in [(BUY_A, "LONG"), (SELL_B, "SHORT")]:
        res = classify_scout(
            parse_scout_alert(side),
            internal_signal_side=internal,
            internal_signal_present=True,
            session_allowed=True,
            risk_allowed=True,
        )
        # Even on CONFIRMATION, Scout does not authorize execution.
        assert res["scout_trade_authorized"] is False


def test_module_imports_no_broker_or_execution_code():
    # Inspect actual import statements (not prose/docstrings): the adapter must
    # not import any broker/execution/order-placing module.
    src = pathlib.Path(scout_mod.__file__).read_text()
    import_lines = [
        ln.strip().lower()
        for ln in src.splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    for ln in import_lines:
        for forbidden in ("execution", "broker", "tradovate", "order"):
            assert forbidden not in ln, f"Scout must not import {forbidden!r}: {ln!r}"


def test_journal_output_contains_required_audit_fields():
    res = classify_scout(
        parse_scout_alert(BUY_A),
        internal_signal_side="LONG",
        internal_signal_present=True,
        session_allowed=True,
        risk_allowed=True,
    )
    for key in (
        "source", "symbol_raw", "symbol_mapped", "side", "grade", "score",
        "setup_type", "bias", "entry", "tp1", "tp2", "stop", "rr",
        "volume_pass", "volatility_pass", "trend_pass", "momentum_pass",
        "insight", "alert_time", "alert_id",
        "internal_signal_side", "internal_signal_present",
        "session_allowed", "risk_allowed", "rr_acceptable",
        "final_decision", "reason",
    ):
        assert key in res, f"missing audit field: {key}"
    assert res["source"] == "scout"


def test_log_scout_writes_tagged_audit_entry(tmp_path):
    journal = JournalLogger(log_dir=str(tmp_path))
    res = classify_scout(
        parse_scout_alert(BUY_A),
        internal_signal_side="LONG",
        internal_signal_present=True,
        session_allowed=True,
        risk_allowed=True,
    )
    journal.log_scout(res)
    files = list(pathlib.Path(tmp_path).glob("journal_*.jsonl"))
    assert files, "log_scout should write a journal file"
    entries = [json.loads(l) for l in files[0].read_text().splitlines() if l.strip()]
    scout_entries = [e for e in entries if e.get("type") == "SCOUT_SIGNAL"]
    assert len(scout_entries) == 1
    assert scout_entries[0]["source"] == "scout"
    assert scout_entries[0]["scout_trade_authorized"] is False
    assert scout_entries[0]["final_decision"] == CONFIRMATION
