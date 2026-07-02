"""Tests for scripts/session_audit.py — synthetic journal lines through the full report."""

import json

from scripts.session_audit import build_report, mask_ids


def _ctx(close=100.0, trend="UP", pdh="below", pdl="above", pdc=99.0):
    return {
        "close": close,
        "trend": {"direction": trend, "strength": "STRONG"},
        "previous_day": {"high": 110.0, "low": 90.0, "close": pdc,
                         "price_vs_pdh": pdh, "price_vs_pdl": pdl},
        "signa": {"grade": "A", "score": 70.0, "daily_direction": "WAIT",
                  "weekly_direction": None},
        "htf": {"daily_direction": "UP", "four_hour_direction": "UP"},
    }


def _write_journal(tmp_path):
    lines = [
        # 1) filled TRADE (loss) with ORDER_IDS
        {"ts": "2026-06-24T10:00:00+00:00", "instrument": "MES", "decision": "TRADE",
         "market_condition": "TRENDING", "regime": "FULL_LONG", "signa_status": "PASS",
         "failed_gates": [],
         "setup": {"direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 102.0,
                   "rr_ratio": 2.0, "strategy": "pdh_reclaim", "contracts": 1},
         "context": _ctx()},
        {"ts": "2026-06-24T10:00:05+00:00", "type": "ORDER_IDS", "instrument": "MES",
         "order_ids": {"instrument": "MES", "entry": 522911741704,
                       "target": 522911741705, "stop": 522911741706}},
        {"ts": "2026-06-24T10:30:00+00:00", "type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "LOSS", "entry_price": 100.0, "exit_price": 99.0,
                     "exit_reason": "STOP_HIT", "pnl_ticks": -4.0,
                     "pnl_dollars": -23.75, "contracts": 1}},
        # 2) IOC-cancelled TRADE
        {"ts": "2026-06-24T11:00:00+00:00", "instrument": "MNQ", "decision": "TRADE",
         "market_condition": "TRENDING", "regime": "RESTRICTED", "signa_status": "PASS",
         "failed_gates": ["REGIME_NOT_FULL"],
         "setup": {"direction": "SHORT", "entry": 200.0, "stop": 201.0, "target": 197.0,
                   "rr_ratio": 3.0, "strategy": "vwap_hold", "contracts": 1},
         "context": _ctx(trend="DOWN")},
        {"ts": "2026-06-24T11:00:10+00:00", "type": "OUTCOME", "instrument": "MNQ",
         "outcome": {"result": "CANCELLED", "entry_price": 200.0, "exit_price": None,
                     "exit_reason": "execution_failed:CANCELLED", "pnl_ticks": 0.0,
                     "pnl_dollars": 0.0, "contracts": 1}},
        # 3) phantom-cleared TRADE (auto-reconcile)
        {"ts": "2026-06-24T12:00:00+00:00", "instrument": "MES", "decision": "TRADE",
         "market_condition": "TRENDING", "regime": "RESTRICTED", "signa_status": "NEUTRAL",
         "failed_gates": ["REGIME_RESTRICTED"],
         "setup": {"direction": "SHORT", "entry": 101.0, "stop": 102.0, "target": 98.0,
                   "rr_ratio": 3.0, "strategy": "vwap_hold", "contracts": 1},
         "context": _ctx(trend="DOWN")},
        {"ts": "2026-06-24T13:00:00+00:00", "type": "OUTCOME", "instrument": "MES",
         "outcome": {"result": "CANCELLED", "entry_price": 101.0, "exit_price": None,
                     "exit_reason": "auto-reconcile: journal showed open but broker is "
                                    "flat (phantom cleared)",
                     "pnl_ticks": 0.0, "pnl_dollars": 0.0, "contracts": 1}},
        # 4) NO_TRADE near-miss: formed LONG setup blocked by gates
        {"ts": "2026-06-24T14:00:00+00:00", "instrument": "MNQ", "decision": "NO_TRADE",
         "reason": "gated", "market_condition": "TRENDING",
         "failed_gates": ["ENTRY_DETACHED_FROM_PRICE", "REGIME_NOT_FULL"],
         "setup": {"direction": "LONG", "entry": 205.0, "stop": 204.0, "target": 208.0,
                   "rr_ratio": 3.0, "strategy": "orb_breakout", "contracts": 1},
         "context": _ctx()},
        # 5) NO_TRADE with LONG shadow candidates only (one non-executable)
        {"ts": "2026-06-24T15:00:00+00:00", "instrument": "MNQ", "decision": "NO_TRADE",
         "reason": "not trending", "market_condition": "RANGE_BOUND",
         "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"], "setup": None,
         "shadow_candidates": [
             {"strategy": "ema_pullback_trend", "direction": "LONG", "entry": 206.0},
             {"strategy": "strat_22_reversal_observed", "direction": "LONG", "entry": 206.5},
         ],
         "context": _ctx()},
        # 6) RISK_REJECTED
        {"ts": "2026-06-24T16:00:00+00:00", "instrument": "MES", "decision": "RISK_REJECTED",
         "reason": "Stop is 223 ticks from entry — max 60 ticks allowed for MES",
         "market_condition": "TRENDING", "regime": "RESTRICTED",
         "failed_gates": ["REGIME_NOT_FULL"],
         "setup": {"direction": "SHORT", "entry": 100.0, "stop": 155.75, "target": 90.0,
                   "rr_ratio": 2.0, "strategy": "strat_122", "contracts": 1},
         "context": _ctx(trend="DOWN")},
    ]
    p = tmp_path / "journal_2026-06-24.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    return p


def test_report_classifications(tmp_path):
    report = build_report([_write_journal(tmp_path)])

    # Section 1: filled loss with amount, IOC-cancel, phantom
    assert "**FILLED-LOSS** -$23.75" in report
    assert "**IOC-CANCELLED**" in report
    assert "**PHANTOM-CLEARED**" in report
    # trend / signa / previous-day context surfaced
    assert "15m trend: UP/STRONG" in report
    assert "daily_direction=WAIT" in report
    assert "vs_pdh=below vs_pdl=above" in report
    assert "direction role: PRIMARY | primary=LONG | daily=UP 4H=UP" in report
    assert "direction role: UNRESOLVED | primary=LONG | daily=UP 4H=UP" in report
    # ORDER_IDS presence: masked ids for the first trade, NO for the others
    assert "order ids logged: yes" in report
    assert "'entry': '…1704'" in report
    assert "order ids logged: NO" in report
    # raw 8+ digit broker ids must never appear
    assert "522911741704" not in report

    # Section 2: per-setup fill rates
    assert "pdh_reclaim | 1 | 0 | 1 | 0 | 0 | 0 | 100%" in report
    assert "vwap_hold | 2 | 0 | 0 | 1 | 1 | 0 | 0%" in report

    # Section 3: RISK_REJECTED detail + NO_TRADE gate aggregates + near-miss
    assert "RISK_REJECTED (1)" in report
    assert "max 60 ticks allowed for MES" in report
    assert "MARKET_CONDITION_NOT_TRENDING=1" in report
    assert "MNQ orb_breakout LONG: 1" in report

    # Section 4: missed-long grouping + non-executable flags
    assert "**orb_breakout**: 1 missed LONG bars" in report
    assert "ENTRY_DETACHED_FROM_PRICE=1" in report
    assert "ema_pullback_trend" in report
    assert "known loser" in report
    assert "strat_22_reversal_observed" in report
    assert "non-executable (observe-only shadow strategy)" in report


def test_mask_ids():
    assert mask_ids("entry 522911741704 ok") == "entry …1704 ok"
    assert mask_ids("price 7444.25") == "price 7444.25"
