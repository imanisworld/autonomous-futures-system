"""tests/test_promotion_gate.py

Proves the strategy promotion proof gate never emits VALIDATED, classifies
UNSAFE/BROKEN/WAIT correctly from journal-derived evidence, and matches
inventory rows despite naming punctuation differences (e.g.
"ORB Breakout (MNQ)" vs. "orb_breakout").
"""

from __future__ import annotations

import json
from pathlib import Path

from ops.promotion_gate import build_promotion_report, load_strategy_inventory_row


def _write_journal(log_dir: Path, day: str, rows: list[dict]) -> None:
    (log_dir / f"journal_{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _approved_trade(ts: str, strategy: str, pnl_setup=None, instrument="MNQ") -> dict:
    setup = {"strategy": strategy, "direction": "LONG", "entry": 100, "stop": 99, "target": 102, "contracts": 1}
    if pnl_setup:
        setup.update(pnl_setup)
    return {
        "ts": ts, "decision": "TRADE", "instrument": instrument,
        "risk_check": {"result": "APPROVED"}, "setup": setup,
    }


def _outcome(ts: str, result: str, pnl: float, instrument="MNQ") -> dict:
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": {"result": result, "pnl_dollars": pnl, "exit_reason": "target" if result == "WIN" else "stop"}}


INVENTORY_MD = """
## Master Table

| Strategy | Rules | Detector | Verdict |
|---|---|---|---|
| ORB Breakout (MNQ) | ✅ | ✅ | **WAIT** |
| ORB Reclaim (MES) | ✅ | ✅ | **PAPER PROOF** |
"""


def test_never_emits_validated_even_with_strong_positive_sample(tmp_path):
    rows = []
    for i in range(20):
        ts_trade = f"2026-07-{1 + i:02d}T10:00:00+00:00"
        ts_out = f"2026-07-{1 + i:02d}T10:30:00+00:00"
        rows.append(_approved_trade(ts_trade, "orb_breakout"))
        rows.append(_outcome(ts_out, "WIN", 25.0))
    _write_journal(tmp_path, "2026-07-01", rows)
    report = build_promotion_report("orb_breakout", journal_dir=tmp_path, inventory_path=tmp_path / "missing.md")
    assert report["classification"] != "VALIDATED"
    assert "never emits VALIDATED" in report["classification_hard_cap"]


def test_negative_net_pnl_at_adequate_sample_is_broken(tmp_path):
    rows = []
    for i in range(15):
        ts_trade = f"2026-07-{1 + i:02d}T10:00:00+00:00"
        ts_out = f"2026-07-{1 + i:02d}T10:30:00+00:00"
        rows.append(_approved_trade(ts_trade, "vwap_hold"))
        rows.append(_outcome(ts_out, "LOSS", -20.0))
    _write_journal(tmp_path, "2026-07-01", rows)
    report = build_promotion_report("vwap_hold", journal_dir=tmp_path)
    assert report["classification"] == "BROKEN"


def test_thin_sample_is_wait(tmp_path):
    rows = [
        _approved_trade("2026-07-01T10:00:00+00:00", "orb_reclaim"),
        _outcome("2026-07-01T10:30:00+00:00", "WIN", 10.0),
    ]
    _write_journal(tmp_path, "2026-07-01", rows)
    report = build_promotion_report("orb_reclaim", journal_dir=tmp_path)
    assert report["classification"] == "WAIT"
    assert report["performance"]["sample_size"] == 1


def test_incomplete_bracket_is_unsafe(tmp_path):
    rows = []
    for i in range(15):
        ts_trade = f"2026-07-{1 + i:02d}T10:00:00+00:00"
        ts_out = f"2026-07-{1 + i:02d}T10:30:00+00:00"
        rows.append(_approved_trade(ts_trade, "orb_breakout", pnl_setup={"stop": None}))
        rows.append(_outcome(ts_out, "WIN", 10.0))
    _write_journal(tmp_path, "2026-07-01", rows)
    report = build_promotion_report("orb_breakout", journal_dir=tmp_path)
    assert report["classification"] == "UNSAFE"


def test_zero_fills_is_wait_not_validated_or_broken(tmp_path):
    rows = [
        {"ts": "2026-07-01T10:00:00+00:00", "decision": "NO_TRADE", "instrument": "MNQ",
         "setup": {"strategy": "orb_breakout", "direction": "LONG"}, "reason": "no rr"},
    ]
    _write_journal(tmp_path, "2026-07-01", rows)
    report = build_promotion_report("orb_breakout", journal_dir=tmp_path)
    assert report["execution"]["zero_executable_fills"] is True
    assert report["classification"] == "WAIT"


def test_inventory_row_matches_despite_punctuation_and_instrument_suffix(tmp_path):
    inventory = tmp_path / "inventory.md"
    inventory.write_text(INVENTORY_MD)
    row = load_strategy_inventory_row("orb_breakout", inventory)
    assert row is not None
    assert row["name"] == "ORB Breakout (MNQ)"


def test_inventory_row_none_when_no_match(tmp_path):
    inventory = tmp_path / "inventory.md"
    inventory.write_text(INVENTORY_MD)
    row = load_strategy_inventory_row("totally_unrelated_strategy_xyz", inventory)
    assert row is None
