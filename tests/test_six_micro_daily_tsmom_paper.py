"""Paper ledger for the six-micro daily momentum amendment. No broker."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from research.six_micro_daily_tsmom_paper import (
    FIRST_ELIGIBLE,
    ROOTS,
    PaperLedger,
    SessionPrint,
    frozen_rule_sha256,
    paper_pnl,
    reject_rule_edit,
    signal_from_closes,
)
from sources.polygon_client import front_contract


def _warmup(ledger: PaperLedger, root: str, contract_for) -> None:
    start = date(2026, 1, 1)
    for offset in range(61):
        session = start + timedelta(days=offset)
        close = 100.0 + offset
        ledger.on_session(SessionPrint(
            session=session,
            root=root,
            contract=contract_for(session),
            rth_open=close,
            session_close=close,
        ))


def _scheduled(root: str):
    def contract_for(session: date) -> str:
        return front_contract(root, session)
    return contract_for


def test_signal_needs_sixty_sessions_and_exact_sign():
    assert signal_from_closes([1.0] * 60) == 0
    rising = [float(100 + i) for i in range(61)]
    assert signal_from_closes(rising) == 1
    falling = [float(200 - i) for i in range(61)]
    assert signal_from_closes(falling) == -1


def test_mgc_pnl_uses_contract_config_not_a_handwritten_table():
    # entry 2000 + 2 ticks, exit 2010 - 2 ticks, tick 0.10, $1 per tick.
    assert paper_pnl("MGC", 1, 2000.0, 2010.0) == pytest.approx(93.04)


def test_mym_and_rule_edits_are_refused(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    with pytest.raises(ValueError, match="six frozen micros"):
        ledger.on_session(SessionPrint(
            session=FIRST_ELIGIBLE, root="MYM", contract="MYMZ6",
            rth_open=1.0, session_close=1.0,
        ))
    with pytest.raises(PermissionError):
        reject_rule_edit({"lookback_sessions": 20})


def test_journal_cannot_pretend_to_be_the_single_look(tmp_path: Path):
    blocked = tmp_path / "docs" / "research-evidence" / "trial"
    with pytest.raises(ValueError, match="single-look"):
        PaperLedger(blocked)


def test_ineligible_session_does_not_open_and_exact_front_is_required(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    _warmup(ledger, "MNQ", _scheduled("MNQ"))
    early = ledger.on_session(SessionPrint(
        session=date(2026, 10, 9),
        root="MNQ",
        contract=front_contract("MNQ", date(2026, 10, 9)),
        rth_open=200.0,
        session_close=201.0,
    ))
    assert ledger.round_turns == 0
    assert ledger.scoring_round_turns == 0
    assert early[0]["kind"] == "PRE_REGISTRATION"
    assert early[0]["counts_toward_forty"] is False
    stored = ledger.state["closes"]["MNQ"][-1]
    assert stored["session"] == "2026-10-09"
    assert stored["pre_registration"] is True
    assert stored["counts_toward_forty"] is False
    assert ledger.on_session(SessionPrint(
        session=date(2026, 10, 9),
        root="MNQ",
        contract=front_contract("MNQ", date(2026, 10, 9)),
        rth_open=200.0,
        session_close=201.0,
    )) == []
    assert not any(row["kind"] == "OPEN" for row in early)
    mismatch = ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE, root="MNQ", contract="MNQ_NOT_FRONT",
        rth_open=202.0, session_close=203.0,
    ))
    assert mismatch[0]["kind"] == "FRONT_MISMATCH"
    assert ledger.state["positions"].get("MNQ") is None


def test_round_turns_on_mnq_and_mgc_share_one_count(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    before = frozen_rule_sha256()
    for root, contract_for in (
        ("MNQ", _scheduled("MNQ")),
        ("MGC", lambda _session: "MGCZ6"),
    ):
        _warmup(ledger, root, contract_for)
        opened = ledger.on_session(SessionPrint(
            session=FIRST_ELIGIBLE,
            root=root,
            contract=contract_for(FIRST_ELIGIBLE),
            rth_open=300.0,
            session_close=301.0,
        ))
        assert any(row["kind"] == "OPEN" for row in opened)
        for step in range(1, 21):
            session = FIRST_ELIGIBLE + timedelta(days=step)
            events = ledger.on_session(SessionPrint(
                session=session,
                root=root,
                contract=contract_for(session),
                rth_open=300.0 + step,
                session_close=301.0 + step,
            ))
        assert any(row["kind"] == "ROUND_TURN" for row in events)
        assert ledger.state["positions"].get(root) is None
    assert ledger.round_turns == 2
    assert ledger.scoring_round_turns == 2
    assert all(
        row["pre_registration"] is True
        for row in ledger.state["closes"]["MNQ"]
        if row["session"] <= "2026-10-09"
    )
    assert frozen_rule_sha256() == before
    text = Path("research/six_micro_daily_tsmom_paper.py").read_text(encoding="utf-8")
    assert "tradovate" not in text.lower()
    assert "MYM" not in ROOTS
    assert set(ROOTS) == {"MNQ", "MES", "M2K", "MGC", "MCL", "MBT"}
