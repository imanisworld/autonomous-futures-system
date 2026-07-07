"""Fill-fiction guard on the live runner-shadow evidence stream.

2026-07-02 incident: a journal-open MES orb_reclaim position whose IOC entry
NEVER filled at Tradovate (decision close 7588.75 vs cap 7588.0; the reconciler
phantom-cleared it minutes later) still produced an armed=true evidence row in
runner_shadow_evidence.jsonl. Left ungated, the EXIT_MODE=runner_live promotion
review would accumulate fill-fiction evidence — the same artifact class PR #150
exposed in replay. These tests pin the gate: definitive broker no-fill →
evidence suppressed; unreadable fill status → row kept but tagged
fill_confirmed=null; confirmed fill → fill_confirmed=true and eligible as proof.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from journal.journal_logger import JournalLogger
from ops.runner_shadow_evidence import EVIDENCE_FILENAME, runner_shadow_status
from webhook.payload import AlertPayload


def _payload(**overrides) -> AlertPayload:
    data = {
        "ticker": "MNQ1!",
        "timestamp": "2026-05-23T14:30:00+00:00",
        "timeframe": "15",
        "open": 19510.0,
        # High clears entry + 1R (19540) so the shadow trail arms and moves,
        # but stays below the 19580 target.
        "high": 19560.0,
        "low": 19505.0,
        "close": 19550.0,
        "volume": 4200,
        "avg_volume": 3800,
        "vwap": 19495.0,
        "orb_high": 19498.0,
        "orb_low": 19462.0,
        "orb_status": "above",
        "market_condition": "TRENDING",
        "trend_direction": "UP",
        "trend_strength": "MODERATE",
        "previous_day_high": 19520.0,
        "previous_day_low": 19440.0,
        "previous_day_close": 19475.0,
    }
    data.update(overrides)
    return AlertPayload(**data)


def _seed_open_trade(journal: JournalLogger, for_date: date, *, entry_order_id=None):
    journal._append({
        # Processing-time ts must be fresh (the paper stale-position safety net
        # force-closes >8h-old positions); the originating bar time goes in
        # context.timestamp, which get_open_position exposes as bar_ts.
        "ts": datetime.now(timezone.utc).isoformat(),
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "TRADE",
        "reason": "fill-gate test",
        "market_condition": "TRENDING",
        "context": {"timestamp": f"{for_date.isoformat()}T14:25:00+00:00"},
        "setup": {
            "direction": "LONG",
            "entry": 19500.0,
            "stop": 19460.0,
            "target": 19580.0,
            "rr_ratio": 2.0,
            "strategy": "orb_reclaim",
            "notes": None,
            "contracts": 1,
        },
        "risk_check": {"result": "APPROVED", "failed_rule": None, "reason": None},
        "outcome": None,
    }, for_date)
    if entry_order_id is not None:
        journal.log_order_ids(
            instrument="MNQ",
            session="new_york",
            order_ids={"entry": entry_order_id, "stop": 222, "target": 333},
            for_date=for_date,
        )


class _FakeTradovateBroker:
    """Inert stand-in for the bar-resolution path: restore/resolve do nothing,
    entry_order_filled returns a canned answer (None when no order id, mirroring
    the real broker's contract)."""

    fill_answer = None
    last_checked_order_id = "UNSET"

    def __init__(self, config=None):
        self._last_position = None
        self._last_order_ids = None

    def resolve_position(self):
        return None

    def entry_order_filled(self, order_id):
        type(self).last_checked_order_id = order_id
        if order_id is None:
            return None
        return type(self).fill_answer


def _run_tradovate_bar(config, tmp_path, monkeypatch, *, fill_answer, entry_order_id):
    import execution.tradovate_broker as tb
    from webhook.runner import process_alert

    monkeypatch.setenv("BROKER", "tradovate")
    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    monkeypatch.delenv("EXIT_MODE", raising=False)
    _FakeTradovateBroker.fill_answer = fill_answer
    _FakeTradovateBroker.last_checked_order_id = "UNSET"
    monkeypatch.setattr(tb, "TradovateBroker", _FakeTradovateBroker)

    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    journal = JournalLogger(log_dir=log_dir)
    _seed_open_trade(journal, today, entry_order_id=entry_order_id)

    result = process_alert(
        _payload(),
        config=replace(config, paper_mode=False),
        log_dir=log_dir,
        for_date=today,
    )
    assert result["decision"] == "BLOCKED_OPEN_POSITION"
    return Path(log_dir) / EVIDENCE_FILENAME, log_dir


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_unfilled_entry_suppresses_shadow_evidence(config, tmp_path, monkeypatch):
    """Broker says the entry NEVER filled → no evidence row (the 07-02 class)."""
    evidence, _ = _run_tradovate_bar(
        config, tmp_path, monkeypatch, fill_answer=False, entry_order_id=111,
    )
    assert _FakeTradovateBroker.last_checked_order_id == 111
    assert _rows(evidence) == []


def test_unreadable_fill_status_tags_row_instead_of_dropping(config, tmp_path, monkeypatch):
    """No persisted entry order id → fill status unreadable → keep the row
    tagged fill_confirmed=null, and it must never count as promotion proof."""
    evidence, log_dir = _run_tradovate_bar(
        config, tmp_path, monkeypatch, fill_answer=True, entry_order_id=None,
    )
    rows = _rows(evidence)
    assert len(rows) == 1
    assert rows[0]["armed"] is True
    assert rows[0]["fill_confirmed"] is None

    status = runner_shadow_status(log_dir)
    assert status["proof_sufficient"] is False
    assert status["live_trailing_blocked"] is True


def test_confirmed_fill_records_proof_eligible_evidence(config, tmp_path, monkeypatch):
    evidence, log_dir = _run_tradovate_bar(
        config, tmp_path, monkeypatch, fill_answer=True, entry_order_id=111,
    )
    rows = _rows(evidence)
    assert len(rows) == 1
    assert rows[0]["armed"] is True
    assert rows[0]["moved"] is True
    assert rows[0]["fill_confirmed"] is True

    status = runner_shadow_status(log_dir)
    assert status["state"] == "proof_sufficient"
    assert status["proof_sufficient"] is True


def test_paper_shadow_evidence_is_fill_confirmed_by_construction(config, tmp_path, monkeypatch):
    """Paper sim entries fill by construction — rows carry fill_confirmed=true
    so the status reader needs no special case for paper evidence."""
    from webhook.runner import process_alert

    monkeypatch.setenv("RUNNER_SHADOW_ENABLED", "true")
    monkeypatch.delenv("EXIT_MODE", raising=False)
    log_dir = str(tmp_path / "logs")
    today = date(2026, 5, 23)
    _seed_open_trade(JournalLogger(log_dir=log_dir), today)

    result = process_alert(_payload(), config=config, log_dir=log_dir, for_date=today)
    assert result["decision"] == "BLOCKED_OPEN_POSITION"

    rows = _rows(Path(log_dir) / EVIDENCE_FILENAME)
    assert len(rows) == 1
    assert rows[0]["fill_confirmed"] is True
