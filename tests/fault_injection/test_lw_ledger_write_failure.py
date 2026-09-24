"""LW — a lost OUTCOME row in a wide-stop LANE journal (#950 audit gap 13).

The ARMED demo lane (and the paper forward collector) rebuild their balance,
drawdown and daily loss from OUTCOME rows in their own lane journal
(``collector._lane_daily_state`` -> ``get_account_state_since`` /
``get_daily_state``), and feed that to both RiskEngine checks before an order.
The OUTCOME write goes through ``JournalLogger.log_outcome`` -> ``_append``,
which swallows a failed write. So a lost LOSS makes the lane's daily-loss cap
and drawdown halt see less loss than really happened.

Faults are injected at the FILE level (``open`` inside journal_logger), as in
test_jw_journal_write_failure.py; no production code is patched. Losses are
seeded as REAL lane-journal OUTCOME rows (unlike FI-7i, which mocks the daily
state), so the production reconstruction path runs.

Safe requirement: (i) the next candidate is not approved while the lane's true
loss is at or past its limit, and (ii) the operator is alerted.

Formerly drafted as "FI-6"; renamed because #1000 uses FI-5..FI-9.
Not duplicated from #1000: pending-save/submit-raise branches (FI-7c..7f).
"""
from __future__ import annotations

import builtins
import errno
import json
from pathlib import Path

import pytest

import journal.journal_logger as jl
from context import wide_stop_forward_collector as collector
from context import wide_stop_ledger_paper as contract
from tests.fault_injection._harness import FaultSetupError
from tests.fault_injection import _p2_harness as p2
from tests.fault_injection._p2_harness import FakeDemoBroker, fixture_bars, run_fixture_bar

KNOWN_DEFECT = pytest.mark.xfail(strict=True, raises=AssertionError, reason="LW defect (unfixed)")
STRATEGY = "strat_322_first_live"


class _DiskFull:
    """``open`` inside journal/journal_logger.py: OUTCOME-row appends raise ENOSPC."""

    def __init__(self):
        self.faults = 0

    def __call__(self, file, mode="r", *args, **kwargs):
        real = builtins.open(file, mode, *args, **kwargs)
        name = Path(str(file)).name
        if "a" in mode and name.startswith("journal_") and name.endswith(".jsonl"):
            return _Handle(real, self)
        return real


class _Handle:
    def __init__(self, real, owner):
        self._real, self._owner = real, owner

    def write(self, data: str) -> int:
        try:
            row = json.loads(data)
        except ValueError:
            row = {}
        if row.get("type") == "OUTCOME":
            self._owner.faults += 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return self._real.write(data)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._real.close()
        return False

    def __getattr__(self, item):
        return getattr(self._real, item)


def _ledger():
    ledger = contract.ledger_for("MNQ", STRATEGY)
    if ledger is None:
        raise FaultSetupError(f"{STRATEGY} is not a wide-stop ledger member")
    return ledger


def _lane_root(tmp_path, paper: bool = False):
    """The demo lane journals under its isolated root; the paper collector
    journals under the plain log dir."""
    if paper:
        return tmp_path
    import context.wide_stop_demo_runtime as demo
    return demo.isolated_log_dir(tmp_path)


def _outcome(tmp_path, dollars: float, day, monkeypatch=None, *, paper: bool = False) -> int:
    """Journal one lane OUTCOME (negative = LOSS) the way the demo lane does
    (JournalLogger.log_outcome). With ``monkeypatch`` the write is made under
    a full disk; returns the number of injected faults."""
    fault = _DiskFull()
    if monkeypatch is not None:
        monkeypatch.setattr(jl, "open", fault, raising=False)
    collector._lane_journal(_lane_root(tmp_path, paper), _ledger()).log_outcome(
        instrument="MNQ", session="new_york", result="LOSS" if dollars < 0 else "WIN",
        entry_price=20_000.0, exit_price=20_000.0 + dollars / 2.0,
        exit_reason="STOP_HIT" if dollars < 0 else "TARGET_HIT",
        pnl_ticks=dollars / 0.5, pnl_dollars=float(dollars), for_date=day,
        strategy=STRATEGY,
    )
    if monkeypatch is not None:
        monkeypatch.setattr(jl, "open", builtins.open, raising=False)
    return fault.faults


def _loss(tmp_path, dollars: float, day, monkeypatch=None, *, paper: bool = False) -> int:
    return _outcome(tmp_path, -abs(dollars), day, monkeypatch, paper=paper)


def _capture_alerts(monkeypatch) -> list[str]:
    sent: list[str] = []
    monkeypatch.setattr(
        "notifications.discord_notifier.send_operational_alert",
        lambda cfg, msg, *a, **k: sent.append(msg) or True,
    )
    return sent


def _run(config, tmp_path, monkeypatch):
    broker = FakeDemoBroker()
    events = run_fixture_bar(config, tmp_path, monkeypatch, fixture_bars(), broker)
    rules = [e.get("lane_failed_rule") for e in events]
    return broker, rules


# ── Daily-loss cap ($600 on the 3-2-2 ledger) ─────────────────────────────────
# A small WIN between the two losses resets the consecutive-loss count, so the
# daily-loss cap is the gate under test (a first draft hit consecutive_loss_limit
# instead; per the prereg stop rule only the seeding was changed).
DAILY_SEEN, DAILY_WIN, DAILY_LOST = 500.0, 50.0, 250.0  # net -450 > -600; -700 <= -600


def _daily_seed(tmp_path, *, paper: bool = False):
    _loss(tmp_path, DAILY_SEEN, p2.FIXTURE_DAY, paper=paper)
    _outcome(tmp_path, DAILY_WIN, p2.FIXTURE_DAY, paper=paper)


def test_lw_daily_setup_recorded_loss_under_cap_still_submits(config, tmp_path, monkeypatch):
    """Setup check: the seeded loss alone is under the cap, so the fixture submits."""
    _daily_seed(tmp_path)
    broker, rules = _run(config, tmp_path, monkeypatch)
    if broker.execute_calls != 1:
        raise FaultSetupError(f"fixture did not submit under the cap: {rules}")


def test_lw_daily_control_recorded_losses_block_at_cap(config, tmp_path, monkeypatch):
    """Control: with BOTH losses recorded the real RiskEngine blocks at the cap."""
    _daily_seed(tmp_path)
    _loss(tmp_path, DAILY_LOST, p2.FIXTURE_DAY)
    broker, rules = _run(config, tmp_path, monkeypatch)
    assert broker.execute_calls == 0, rules
    assert "max_daily_loss" in rules, rules


@KNOWN_DEFECT
def test_lw_daily_lost_outcome_does_not_reopen_the_lane(config, tmp_path, monkeypatch):
    alerts = _capture_alerts(monkeypatch)
    _daily_seed(tmp_path)
    if _loss(tmp_path, DAILY_LOST, p2.FIXTURE_DAY, monkeypatch) < 1:
        raise FaultSetupError("the lost OUTCOME write was never attempted")
    broker, rules = _run(config, tmp_path, monkeypatch)
    state = f"execute_calls={broker.execute_calls} rules={rules} alerts={len(alerts)}"
    assert broker.execute_calls == 0, state
    assert alerts, state


# ── Drawdown halt (20% of $5,000 = $1,000 on the 3-2-2 ledger) ────────────────
DD_DAY_EARLIER = "2026-06-12"
DD_SEEN, DD_LOST = 850.0, 250.0  # 850 < 1000 <= 850 + 250; both on an earlier day


def _earlier_epoch(monkeypatch):
    from datetime import date
    monkeypatch.setattr(p2, "FIXTURE_EPOCH", "2026-06-01T00:00:00+00:00")
    return date.fromisoformat(DD_DAY_EARLIER)


def test_lw_drawdown_setup_recorded_loss_under_halt_still_submits(config, tmp_path, monkeypatch):
    day = _earlier_epoch(monkeypatch)
    _loss(tmp_path, DD_SEEN, day)
    broker, rules = _run(config, tmp_path, monkeypatch)
    if broker.execute_calls != 1:
        raise FaultSetupError(f"fixture did not submit under the halt: {rules}")


def test_lw_drawdown_control_recorded_losses_hit_the_halt(config, tmp_path, monkeypatch):
    day = _earlier_epoch(monkeypatch)
    _loss(tmp_path, DD_SEEN, day)
    _loss(tmp_path, DD_LOST, day)
    broker, rules = _run(config, tmp_path, monkeypatch)
    assert broker.execute_calls == 0, rules
    assert "max_drawdown" in rules, rules


@KNOWN_DEFECT
def test_lw_drawdown_lost_outcome_does_not_reopen_the_lane(config, tmp_path, monkeypatch):
    alerts = _capture_alerts(monkeypatch)
    day = _earlier_epoch(monkeypatch)
    _loss(tmp_path, DD_SEEN, day)
    if _loss(tmp_path, DD_LOST, day, monkeypatch) < 1:
        raise FaultSetupError("the lost OUTCOME write was never attempted")
    broker, rules = _run(config, tmp_path, monkeypatch)
    state = f"execute_calls={broker.execute_calls} rules={rules} alerts={len(alerts)}"
    assert broker.execute_calls == 0, state
    assert alerts, state


# ── Paper forward collector (operator G1): the same daily-loss case ───────────
def _run_paper(config, tmp_path, monkeypatch):
    payload = p2.arm_fixture(monkeypatch, fixture_bars())
    events = collector.process_five_min_bar(
        payload=payload, cfg=p2.fixture_cfg(config), bars_5m=fixture_bars(),
        log_dir=tmp_path, for_date=p2.FIXTURE_DAY,
    )
    opened = [e for e in events if e.get("fill_status") == "OPEN"]
    rules = [e.get("lane_failed_rule") for e in events]
    return opened, rules


def test_lw_paper_setup_recorded_loss_under_cap_still_fills(config, tmp_path, monkeypatch):
    _daily_seed(tmp_path, paper=True)
    opened, rules = _run_paper(config, tmp_path, monkeypatch)
    if len(opened) != 1:
        raise FaultSetupError(f"paper fixture did not fill under the cap: {rules}")


def test_lw_paper_control_recorded_losses_block_at_cap(config, tmp_path, monkeypatch):
    _daily_seed(tmp_path, paper=True)
    _loss(tmp_path, DAILY_LOST, p2.FIXTURE_DAY, paper=True)
    opened, rules = _run_paper(config, tmp_path, monkeypatch)
    assert not opened, rules
    assert "max_daily_loss" in rules, rules


@KNOWN_DEFECT
def test_lw_paper_lost_outcome_does_not_reopen_the_ledger(config, tmp_path, monkeypatch):
    alerts = _capture_alerts(monkeypatch)
    _daily_seed(tmp_path, paper=True)
    if _loss(tmp_path, DAILY_LOST, p2.FIXTURE_DAY, monkeypatch, paper=True) < 1:
        raise FaultSetupError("the lost OUTCOME write was never attempted")
    opened, rules = _run_paper(config, tmp_path, monkeypatch)
    state = f"opened={len(opened)} rules={rules} alerts={len(alerts)}"
    assert not opened, state
    assert alerts, state


# ── Control C1: the pre-submit demo-state save fails (full disk) ──────────────
def test_lw_c1_failed_presubmit_state_save_sends_no_order(config, tmp_path, monkeypatch):
    """The demo lane saves its pending intent atomically BEFORE the broker call.
    If THAT save fails (full disk), no order may go out; five_min_feed catches
    the error. Only the save carrying a pending intent fails; every other save
    goes through, so the fault is the one under test."""
    import context.wide_stop_demo_runtime_core as core
    real_save = core.demo_state.save_state
    calls = {"pending_saves": 0}

    def _save(log_dir, state):
        if state.get("pending"):
            calls["pending_saves"] += 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return real_save(log_dir, state)

    broker = FakeDemoBroker()
    monkeypatch.setattr(core.demo_state, "save_state", _save)
    raised = None
    try:
        run_fixture_bar(config, tmp_path, monkeypatch, fixture_bars(), broker)
    except OSError as exc:
        raised = exc
    if calls["pending_saves"] < 1:
        raise FaultSetupError("the pre-submit pending save was never attempted")
    assert broker.execute_calls == 0, f"order sent despite failed intent save; raised={raised!r}"
