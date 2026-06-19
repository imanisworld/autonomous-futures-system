"""
tests/test_tradovate_reject_reason.py

Capture the bracket-child rejection reason wherever Tradovate puts it. The
2026-06-16 box diagnostic read only failureReason/failureText/admReason and so
logged reason=None on the real failure — a protective STOP rejected as InvalidPrice
("current price outside the price limits set") because it sat too close to the live
price at submit time. The reason for an OSO-child reject lives in `rejectReason`
(enum) / `text` (human string), confirmed via the Tradovate API community.
"""
from __future__ import annotations

from execution.tradovate_broker import TradovateBroker as TB


def test_reject_reason_reads_rejectreason_enum():
    o = {"ordStatus": "Rejected", "rejectReason": "InvalidPrice"}
    assert "rejectReason=InvalidPrice" in TB._extract_reject_reason(o)


def test_reject_reason_reads_human_text():
    o = {"ordStatus": "Rejected", "text": "current price outside the price limits set"}
    assert "current price outside the price limits set" in TB._extract_reject_reason(o)


def test_reject_reason_reads_legacy_failure_fields():
    # The fields the old box diag relied on still work (command/liquidate rejects).
    o = {"ordStatus": "Rejected", "failureReason": "RiskCheck"}
    assert "failureReason=RiskCheck" in TB._extract_reject_reason(o)


def test_reject_reason_unions_multiple_fields():
    o = {"rejectReason": "InvalidPrice", "text": "outside limits"}
    out = TB._extract_reject_reason(o)
    assert "rejectReason=InvalidPrice" in out and "text=outside limits" in out


def test_reject_reason_unknown_when_empty():
    assert TB._extract_reject_reason({"ordStatus": "Rejected"}) == "unknown"
    assert TB._extract_reject_reason(None) == "unknown"
