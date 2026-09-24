"""JW — journal write failure around a broker order (#950 audit gap 11).

Formerly "FI-5" (#999); renamed because #1000 uses FI-5..FI-9 for other cases.

The runner sends the order (``webhook/runner.py`` ~3066) BEFORE it writes the
authoritative ``decision="TRADE"`` row (~3321) — the only row any reader treats
as an open position. ``JournalLogger._append`` swallows write errors into
``log_error``, which itself is unguarded. Faults are injected at the FILE
level (the module's ``open``), so the production try/except runs for real;
nothing in ``journal/`` or ``webhook/`` is patched.

Safe requirement (operator D1/D2/D3, 2026-09-24):
  (i)   a lost TRADE row never leads to a second entry;
  (ii)  the failure produces a durable signal outside the failed journal: an
        error-log line AND an operator alert via ``send_operational_alert``;
  (iii) the call that lost its TRADE row does not report a clean OPEN trade;
  D2    a failed pre-submit (TRADE_INTENT) write means no order is sent;
  D3    the same holds on the PaperBroker path.

Fixed by the JW fix (operator F1 process-wide latch, F2 cleared only by a
restart, F3 one retry, F4 OUTCOME is critical where the journal feeds a
RiskEngine). The six former strict-xfail defect tests now pass.
"""
from __future__ import annotations

import builtins
import errno
import json
from pathlib import Path
from typing import Callable

import pytest

import journal.journal_logger as jl
from tests.fault_injection._harness import (
    FakeBook, FaultRecord, FaultSetupError, journal_open, journal_rows, make_broker, mes_payload,
    patch_broker_class, real_broker_cfg, run_alert,
)

SECOND_BAR = "2026-05-23T14:45:00+00:00"


@pytest.fixture(autouse=True)
def _clean_latch():
    """The journal-write latch and its alert flag are process-wide by design
    (F1/F2); every test must start and end with a clean process."""
    from webhook import runner
    jl._reset_write_failure_latch()
    runner._journal_alert_sent = False
    yield
    jl._reset_write_failure_latch()
    runner._journal_alert_sent = False


class _DiskFull:
    """Replaces ``open`` inside journal/journal_logger.py only. Writes whose
    JSON row matches ``row_pred`` raise ENOSPC; ``error_log_fails`` makes the
    error-log append raise too. Reads and the lock file pass through."""

    def __init__(self, row_pred: Callable[[dict], bool], *, error_log_fails: bool = False,
                 max_faults: int | None = None):
        self.row_pred = row_pred
        self.error_log_fails = error_log_fails
        self.max_faults = max_faults
        self.row_faults = 0
        self.error_log_faults = 0

    def __call__(self, file, mode="r", *args, **kwargs):
        real = builtins.open(file, mode, *args, **kwargs)
        name = Path(str(file)).name
        if "a" not in mode or name == ".journal.lock":
            return real
        if name.startswith("journal_") and name.endswith(".jsonl"):
            return _Handle(real, self._journal_write)
        if self.error_log_fails and not name.startswith("journal_"):
            return _Handle(real, self._error_write)
        return real

    def _journal_write(self, data: str) -> None:
        try:
            row = json.loads(data)
        except ValueError:
            return
        if self.row_pred(row) and (self.max_faults is None or self.row_faults < self.max_faults):
            self.row_faults += 1
            raise OSError(errno.ENOSPC, "No space left on device")

    def _error_write(self, _data: str) -> None:
        self.error_log_faults += 1
        raise OSError(errno.ENOSPC, "No space left on device")


class _Handle:
    def __init__(self, real, check):
        self._real, self._check = real, check

    def write(self, data: str) -> int:
        self._check(data)
        return self._real.write(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._real.close()
        return False

    def __getattr__(self, item):
        return getattr(self._real, item)


def _is_trade(row: dict) -> bool:
    return row.get("decision") == "TRADE" and row.get("type") is None


def _is_intent(row: dict) -> bool:
    return row.get("decision") == "TRADE_INTENT"


def _capture_alerts(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        "notifications.discord_notifier.send_operational_alert",
        lambda cfg, msg, *a, **k: sent.append(msg) or True,
    )
    return sent


def _error_log_lines(log_dir: Path) -> list[str]:
    return [
        line for p in Path(log_dir).glob("*") if p.is_file() and not p.name.startswith("journal_")
        and p.suffix in (".log", ".txt")
        for line in p.read_text().splitlines() if "Failed to write journal entry" in line
    ]


def _trade_rows(log_dir: Path) -> int:
    return sum(1 for r in journal_rows(log_dir) if _is_trade(r))


def _tradovate(monkeypatch, config, *, recheck: bool):
    book = FakeBook(place_mode="fill", children=True)
    broker = make_broker(monkeypatch, book)
    # make_broker clears BROKER; the production path under test is BROKER=tradovate.
    monkeypatch.setenv("BROKER", "tradovate")
    patch_broker_class(monkeypatch, book)
    return book, broker, real_broker_cfg(config, working_order_recheck=recheck)


def _first_trade(monkeypatch, config, tmp_path, *, recheck: bool, error_log_fails: bool = False):
    book, broker, cfg = _tradovate(monkeypatch, config, recheck=recheck)
    alerts = _capture_alerts(monkeypatch)
    fault = _DiskFull(_is_trade, error_log_fails=error_log_fails)
    monkeypatch.setattr(jl, "open", fault, raising=False)
    log_dir = tmp_path / "logs"
    raised = None
    try:
        first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    except Exception as exc:  # noqa: BLE001 — JW-2 records what escapes
        first, raised = {"decision": f"RAISED {type(exc).__name__}"}, exc
    if fault.row_faults < 1:
        raise FaultSetupError("the TRADE-row write was never attempted, so nothing was injected")
    if book.place_calls() != 1 or book.net_pos != 1:
        raise FaultSetupError(f"expected one filled entry before the lost row; {book.describe()}")
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    return book, broker, cfg, log_dir, first, raised, alerts, fault


def _record(case: str, injected: str, expected: str) -> FaultRecord:
    return FaultRecord(
        case=case, initial_journal="flat", initial_broker="flat, no orders",
        injected_failure=injected, expected_safe_state=expected,
    )


# ── JW-1: TRADE row lost, error log writable ──────────────────────────────────
@pytest.mark.parametrize("recheck", [
    pytest.param(True, id="recheck_on"),  # FI-3 broker-position recheck is the only backstop
    pytest.param(False, id="recheck_off"),
])
def test_jw1_lost_trade_row_never_leads_to_second_entry(config, tmp_path, monkeypatch, recheck):
    book, broker, cfg, log_dir, first, _, _, _ = _first_trade(
        monkeypatch, config, tmp_path, recheck=recheck)
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec = _record(f"JW-1 lost TRADE row (recheck={'on' if recheck else 'off'})",
                  "ENOSPC on the TRADE-row write after the entry filled",
                  "(i) no second entry")
    rec.actual_state = (f"first={first['decision']} second={second['decision']} "
                        f"gate={second.get('gate_reason')} journal_open={journal_open(log_dir)} "
                        f"broker {book.describe()}")
    rec.new_entry_blocked = str(book.place_calls() == 1)
    rec.journal_broker_diverge = str(not journal_open(log_dir) and book.net_pos != 0)
    assert book.place_calls() == 1, str(rec)


def test_jw1_lost_trade_row_raises_durable_signal(config, tmp_path, monkeypatch):
    book, _, _, log_dir, first, _, alerts, _ = _first_trade(
        monkeypatch, config, tmp_path, recheck=True)
    errors = _error_log_lines(log_dir)
    rec = _record("JW-1 durable failure signal", "ENOSPC on the TRADE-row write",
                  "(ii) error-log line AND an operator alert")
    rec.actual_state = (f"first={first['decision']} error_log_lines={len(errors)} "
                        f"operator_alerts={len(alerts)} broker {book.describe()}")
    if not errors:
        raise FaultSetupError("the swallow path did not write the error log; harness is wrong")
    assert alerts, str(rec)


def test_jw1_lost_trade_row_not_reported_as_clean_open(config, tmp_path, monkeypatch):
    book, _, _, log_dir, first, _, _, _ = _first_trade(monkeypatch, config, tmp_path, recheck=True)
    fill = first.get("fill") or {}
    rec = _record("JW-1 result of the call that lost its row", "ENOSPC on the TRADE-row write",
                  "(iii) not reported as a clean TRADE/OPEN")
    rec.actual_state = (f"decision={first['decision']} fill_status={fill.get('status')} "
                        f"trade_rows={_trade_rows(log_dir)} broker {book.describe()}")
    clean_open = first["decision"] == "TRADE" and fill.get("status") == "OPEN"
    assert not clean_open, str(rec)


# ── JW-2: TRADE row lost AND the error log is unwritable (disk full) ──────────
def test_jw2_disk_full_after_submit_is_handled(config, tmp_path, monkeypatch):
    book, broker, cfg, log_dir, first, raised, alerts, fault = _first_trade(
        monkeypatch, config, tmp_path, recheck=True, error_log_fails=True)
    if fault.error_log_faults < 1:
        raise FaultSetupError("the error-log write was never attempted")
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec = _record("JW-2 disk full (journal + error log)", "ENOSPC on TRADE row and on error log",
                  "no unhandled exception after the order; (i) and an operator alert")
    rec.actual_state = (f"first={first['decision']} raised={raised!r} alerts={len(alerts)} "
                        f"second={second['decision']} broker {book.describe()}")
    rec.new_entry_blocked = str(book.place_calls() == 1)
    assert raised is None, str(rec)
    assert alerts, str(rec)
    assert book.place_calls() == 1, str(rec)


# ── JW-3: pre-submit (TRADE_INTENT) write fails — D2: no order is sent ────────
def test_jw3_failed_intent_write_sends_no_order(config, tmp_path, monkeypatch):
    book, broker, cfg = _tradovate(monkeypatch, config, recheck=True)
    _capture_alerts(monkeypatch)
    fault = _DiskFull(_is_intent)
    monkeypatch.setattr(jl, "open", fault, raising=False)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    if fault.row_faults < 1:
        raise FaultSetupError("the TRADE_INTENT write was never attempted")
    rec = _record("JW-3 TRADE_INTENT write fails", "ENOSPC on the pre-submit TRADE_INTENT row",
                  "D2: no broker order")
    rec.actual_state = f"decision={first['decision']} broker {book.describe()}"
    rec.new_entry_blocked = str(book.place_calls() == 0)
    assert book.place_calls() == 0, str(rec)


# ── JW-4: PaperBroker path (D3) — the journal is the only position record ─────
def _paper_cfg(config):
    from dataclasses import replace
    return replace(real_broker_cfg(config, working_order_recheck=True), paper_mode=True)


def _paper_trades(results: list[dict]) -> int:
    return sum(1 for r in results if r.get("decision") == "TRADE")


def test_jw4_paper_lost_trade_row_never_leads_to_second_entry(config, tmp_path, monkeypatch):
    from webhook import runner
    monkeypatch.delenv("BROKER", raising=False)
    alerts = _capture_alerts(monkeypatch)
    cfg = _paper_cfg(config)
    log_dir = tmp_path / "logs"
    fault = _DiskFull(_is_trade)
    monkeypatch.setattr(jl, "open", fault, raising=False)
    first = runner.process_alert(mes_payload(), config=cfg, log_dir=str(log_dir))
    if fault.row_faults < 1:
        raise FaultSetupError("the paper TRADE-row write was never attempted")
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    second = runner.process_alert(mes_payload(SECOND_BAR), config=cfg, log_dir=str(log_dir))
    rec = _record("JW-4 PaperBroker lost TRADE row", "ENOSPC on the paper TRADE-row write",
                  "(i) no second paper entry; (ii) operator alert")
    rec.actual_state = (f"first={first['decision']} second={second['decision']} "
                        f"alerts={len(alerts)} journal_open={journal_open(log_dir)}")
    rec.new_entry_blocked = str(_paper_trades([second]) == 0)
    assert _paper_trades([second]) == 0, str(rec)
    assert alerts, str(rec)


# ── Controls: failures in the safe direction ─────────────────────────────────
def test_jwc1_lost_order_ids_row_keeps_position_open(config, tmp_path, monkeypatch):
    book, broker, cfg = _tradovate(monkeypatch, config, recheck=True)
    _capture_alerts(monkeypatch)
    fault = _DiskFull(lambda r: r.get("type") == "ORDER_IDS")
    monkeypatch.setattr(jl, "open", fault, raising=False)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    if fault.row_faults < 1:
        raise FaultSetupError("the ORDER_IDS write was never attempted")
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec = _record("JW-C1 lost ORDER_IDS row", "ENOSPC on the ORDER_IDS row only",
                  "journal still open; next entry blocked")
    rec.actual_state = (f"first={first['decision']} second={second['decision']} "
                        f"journal_open={journal_open(log_dir)} broker {book.describe()}")
    assert journal_open(log_dir), str(rec)
    assert book.place_calls() == 1, str(rec)


def test_jwc2_lost_outcome_row_keeps_journal_open(config, tmp_path, monkeypatch):
    """A lost OUTCOME row fails in the safe direction: the journal still shows
    the position open, so the open-position gate keeps blocking new entries."""
    from datetime import date
    from journal.journal_logger import JournalLogger
    book, broker, cfg = _tradovate(monkeypatch, config, recheck=True)
    _capture_alerts(monkeypatch)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    if first.get("decision") != "TRADE" or not journal_open(log_dir):
        raise FaultSetupError(f"control needs a clean open trade first: {first.get('decision')}")
    day = date.fromisoformat(sorted(Path(log_dir).glob("journal_*.jsonl"))[-1].stem[len("journal_"):])
    fault = _DiskFull(lambda r: r.get("type") == "OUTCOME")
    monkeypatch.setattr(jl, "open", fault, raising=False)
    JournalLogger(log_dir=str(log_dir)).log_outcome(
        instrument="MES", session="new_york", result="WIN", entry_price=5898.5,
        exit_price=5904.0, exit_reason="TARGET_HIT", pnl_ticks=22.0, pnl_dollars=27.5,
        for_date=day,
    )
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    if fault.row_faults < 1:
        raise FaultSetupError("the OUTCOME write was never attempted")
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    rec = _record("JW-C2 lost OUTCOME row", "ENOSPC on the OUTCOME row via the real log_outcome",
                  "journal stays open (safe direction); no new entry")
    rec.actual_state = (f"second={second['decision']} outcome_faults={fault.row_faults} "
                        f"journal_open={journal_open(log_dir)} broker {book.describe()}")
    assert journal_open(log_dir), str(rec)
    assert book.place_calls() == 1, str(rec)


# ── JW fix behaviour (prereg tests a–e) ───────────────────────────────────────
def test_jw_fix_a_noncritical_row_failure_does_not_latch(config, tmp_path, monkeypatch):
    """(a) Telemetry rows keep the historical swallow: they never halt trading."""
    from journal.journal_logger import JournalLogger
    fault = _DiskFull(lambda r: r.get("type") == "BLOCK_VISIBILITY")
    monkeypatch.setattr(jl, "open", fault, raising=False)
    JournalLogger(log_dir=str(tmp_path / "logs")).log_block_visibility({"why": "test"})
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    if fault.row_faults < 1:
        raise FaultSetupError("the BLOCK_VISIBILITY write was never attempted")
    assert jl.journal_write_failed() is None


def test_jw_fix_b_single_failure_then_retry_success_is_clean(config, tmp_path, monkeypatch):
    """(b) One transient failure on the TRADE row is absorbed by the retry."""
    book, broker, cfg = _tradovate(monkeypatch, config, recheck=True)
    alerts = _capture_alerts(monkeypatch)
    fault = _DiskFull(_is_trade, max_faults=1)
    monkeypatch.setattr(jl, "open", fault, raising=False)
    log_dir = tmp_path / "logs"
    first = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload())
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    if fault.row_faults != 1:
        raise FaultSetupError(f"expected exactly one injected fault, got {fault.row_faults}")
    assert first["decision"] == "TRADE"
    assert _trade_rows(log_dir) == 1
    assert jl.journal_write_failed() is None
    assert alerts == []
    assert book.place_calls() == 1


def test_jw_fix_c_latch_blocks_entries_but_not_resolution(config, tmp_path, monkeypatch):
    """(c) With the latch set, an open paper position still resolves (its
    OUTCOME row is written) while no new entry is taken."""
    from webhook import runner
    monkeypatch.delenv("BROKER", raising=False)
    _capture_alerts(monkeypatch)
    cfg = _paper_cfg(config)
    log_dir = tmp_path / "logs"
    first = runner.process_alert(mes_payload(), config=cfg, log_dir=str(log_dir))
    if first.get("decision") != "TRADE" or not journal_open(log_dir):
        raise FaultSetupError(f"needs an open paper trade first: {first.get('decision')}")
    jl._latch_write_failure("test: latched by a lost critical row")
    # A later bar that trades through the target (5904.0) resolves the position.
    far = mes_payload(SECOND_BAR).model_copy(update={"high": 5930.0, "close": 5925.0})
    second = runner.process_alert(far, config=cfg, log_dir=str(log_dir))
    outcomes_written = [r for r in journal_rows(log_dir) if r.get("type") == "OUTCOME"]
    assert outcomes_written, f"resolution was blocked: second={second.get('decision')}"
    assert second.get("resolution") is not None
    assert _trade_rows(log_dir) == 1, "no new entry while latched"


def test_jw_fix_d_restart_with_flat_journal_is_caught_by_fi3(config, tmp_path, monkeypatch):
    """(d) Documents the restart limit (F2): a new process has no latch, so on
    Tradovate the FI-3 broker-position recheck is what stops a second entry."""
    book, broker, cfg, log_dir, _, _, _, _ = _first_trade(monkeypatch, config, tmp_path, recheck=True)
    from webhook import runner
    jl._reset_write_failure_latch()  # simulate the restart
    runner._journal_alert_sent = False
    second = run_alert(monkeypatch, broker, cfg, log_dir, mes_payload(SECOND_BAR))
    assert book.place_calls() == 1, f"second={second['decision']} broker {book.describe()}"
    assert second["decision"] == "ORDER_SUPPRESSED"


def test_jw_fix_e_lost_main_book_outcome_latches_and_alerts(config, tmp_path, monkeypatch):
    """(e) F4: a lost OUTCOME in the main book (whose journal feeds the
    RiskEngine) latches and alerts; the journal stays open (safe direction)."""
    from webhook import runner
    monkeypatch.delenv("BROKER", raising=False)
    alerts = _capture_alerts(monkeypatch)
    cfg = _paper_cfg(config)
    log_dir = tmp_path / "logs"
    first = runner.process_alert(mes_payload(), config=cfg, log_dir=str(log_dir))
    if first.get("decision") != "TRADE":
        raise FaultSetupError(f"needs an open paper trade first: {first.get('decision')}")
    fault = _DiskFull(lambda r: r.get("type") == "OUTCOME")
    monkeypatch.setattr(jl, "open", fault, raising=False)
    far = mes_payload(SECOND_BAR).model_copy(update={"high": 5930.0, "close": 5925.0})
    runner.process_alert(far, config=cfg, log_dir=str(log_dir))
    monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    if fault.row_faults < 1:
        raise FaultSetupError("the resolution never attempted an OUTCOME write")
    assert jl.journal_write_failed() is not None
    assert journal_open(log_dir)
    third = runner.process_alert(
        mes_payload("2026-05-23T15:00:00+00:00"), config=cfg, log_dir=str(log_dir))
    assert third["decision"] in ("BLOCKED_OPEN_POSITION", "BLOCKED_JOURNAL_UNWRITABLE")
    assert _trade_rows(log_dir) == 1
    assert alerts, "an operator alert must be sent"
