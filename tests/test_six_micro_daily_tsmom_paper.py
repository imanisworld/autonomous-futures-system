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


def test_lookback_is_exactly_sixty_sessions():
    # Index 0 differs from index 1. values[-60] is one session short.
    long_only_at_sixty = [100.0] + [200.0] * 60
    assert signal_from_closes(long_only_at_sixty) == 1
    short_only_at_sixty = [200.0] + [100.0] * 60
    assert signal_from_closes(short_only_at_sixty) == -1


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


def test_todays_close_is_excluded_from_the_signal(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    _warmup(ledger, "MGC", lambda _session: "MGCZ6")
    events = ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE,
        root="MGC",
        contract="MGCZ6",
        rth_open=160.0,
        session_close=1.0,
    ))
    opened = [row for row in events if row["kind"] == "OPEN"]
    assert opened and opened[0]["side"] == 1


def test_front_mismatch_stores_the_close_once(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    _warmup(ledger, "MNQ", _scheduled("MNQ"))
    before = len(ledger.state["closes"]["MNQ"])
    bar = SessionPrint(
        session=FIRST_ELIGIBLE,
        root="MNQ",
        contract="MNQ_NOT_FRONT",
        rth_open=202.0,
        session_close=203.0,
    )
    first = ledger.on_session(bar)
    assert first[0]["kind"] == "FRONT_MISMATCH"
    assert len(ledger.state["closes"]["MNQ"]) == before + 1
    assert ledger.state["closes"]["MNQ"][-1]["close"] == 203.0
    assert ledger.state["positions"].get("MNQ") is None
    assert ledger.on_session(bar) == []
    lines = (tmp_path / "round_turns.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum("FRONT_MISMATCH" in line for line in lines) == 1


def test_roll_exit_uses_the_old_contract_open(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    _warmup(ledger, "MGC", lambda _session: "MGCZ6")
    ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE,
        root="MGC",
        contract="MGCZ6",
        rth_open=2000.0,
        session_close=2010.0,
    ))
    rolled = ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE + timedelta(days=1),
        root="MGC",
        contract="MGCG7",
        rth_open=2500.0,
        session_close=2501.0,
        roll_exit_open=2010.0,
    ))
    turn = next(row for row in rolled if row["kind"] == "ROUND_TURN")
    assert turn["pnl"] == pytest.approx(paper_pnl("MGC", 1, 2000.0, 2010.0))
    assert turn["pnl"] != pytest.approx(paper_pnl("MGC", 1, 2000.0, 2500.0))
    assert turn["exit_price_source"] == "old_contract_open"


def test_missing_roll_price_exits_later_and_is_journaled_once(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    _warmup(ledger, "MGC", lambda _session: "MGCZ6")
    ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE,
        root="MGC",
        contract="MGCZ6",
        rth_open=2000.0,
        session_close=2001.0,
    ))
    day2 = FIRST_ELIGIBLE + timedelta(days=1)
    missing = ledger.on_session(SessionPrint(
        session=day2,
        root="MGC",
        contract="MGCG7",
        rth_open=2100.0,
        session_close=2101.0,
    ))
    assert missing[0]["kind"] == "ROLL_PRICE_MISSING"
    assert ledger.state["positions"]["MGC"]["roll_price_missing"] is True
    assert ledger.state["closes"]["MGC"][-1]["session"] == day2.isoformat()
    assert ledger.state["closes"]["MGC"][-1]["close"] == 2101.0
    assert ledger.round_turns == 0
    assert ledger.on_session(SessionPrint(
        session=day2,
        root="MGC",
        contract="MGCG7",
        rth_open=2100.0,
        session_close=2101.0,
    )) == []
    lines = (tmp_path / "round_turns.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum("ROLL_PRICE_MISSING" in line for line in lines) == 1
    day3 = FIRST_ELIGIBLE + timedelta(days=2)
    exited = ledger.on_session(SessionPrint(
        session=day3,
        root="MGC",
        contract="MGCG7",
        rth_open=9999.0,
        session_close=2200.0,
        roll_exit_open=2010.0,
    ))
    turn = next(row for row in exited if row["kind"] == "ROUND_TURN")
    assert turn["pnl"] == pytest.approx(paper_pnl("MGC", 1, 2000.0, 2010.0))
    assert turn["exit_price_source"] == "old_contract_open"
    assert ledger.state["positions"].get("MGC") is None


def test_missing_roll_exits_at_the_next_open_when_the_old_price_never_arrives(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    _warmup(ledger, "MGC", lambda _session: "MGCZ6")
    ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE,
        root="MGC",
        contract="MGCZ6",
        rth_open=2000.0,
        session_close=2001.0,
    ))
    ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE + timedelta(days=1),
        root="MGC",
        contract="MGCG7",
        rth_open=2100.0,
        session_close=2101.0,
    ))
    exited = ledger.on_session(SessionPrint(
        session=FIRST_ELIGIBLE + timedelta(days=2),
        root="MGC",
        contract="MGCG7",
        rth_open=2050.0,
        session_close=2051.0,
    ))
    turn = next(row for row in exited if row["kind"] == "ROUND_TURN")
    assert turn["pnl"] == pytest.approx(paper_pnl("MGC", 1, 2000.0, 2050.0))
    assert turn["exit_price_source"] == "next_session_open"
    assert ledger.round_turns == 1
    assert ledger.state["positions"].get("MGC") is None


def test_exit_before_first_eligible_does_not_count(tmp_path: Path):
    ledger = PaperLedger(tmp_path)
    early_entry = {
        "side": 1,
        "entry_fill": 100.0,
        "entry_open": 100.0,
        "entry_session": "2026-10-09",
        "contract": "MNQZ6",
        "sessions_after": 0,
    }
    event = ledger._exit("MNQ", early_entry, FIRST_ELIGIBLE, 110.0)
    assert ledger.round_turns == 0
    assert event["counts_toward_forty"] is False
    later_entry = {
        "side": 1,
        "entry_fill": 100.0,
        "entry_open": 100.0,
        "entry_session": FIRST_ELIGIBLE.isoformat(),
        "contract": "MNQZ6",
        "sessions_after": 0,
    }
    event = ledger._exit("MNQ", later_entry, date(2026, 10, 9), 110.0)
    assert ledger.round_turns == 0
    assert event["counts_toward_forty"] is False
