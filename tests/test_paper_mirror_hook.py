from __future__ import annotations

import json
from dataclasses import replace

import pytest

from execution import paper_mirror_hook as hook
from execution.broker_interface import BracketOrder, Fill
from execution.paper_broker import NextBarOHLC, PaperBroker


def _sync_dispatch(fn):
    fn()


@pytest.fixture
def sync(monkeypatch):
    monkeypatch.setattr(hook, "_dispatch_override", _sync_dispatch)
    yield


def order(**over) -> BracketOrder:
    v = dict(instrument="MNQ", direction="LONG", entry=100.0, stop=95.0, target=110.0, rr_ratio=2.0, strategy="t", contracts=1)
    v.update(over)
    return BracketOrder(**v)


def test_off_by_default_never_imports_mirror(monkeypatch, sync):
    monkeypatch.delenv("WEBULL_FUTURES_MIRROR_ENABLED", raising=False)
    calls = []
    monkeypatch.setattr(hook, "_record", lambda row: calls.append(row))
    fill = Fill(instrument="MNQ", direction="LONG", contracts=1, entry_price=100.0, exit_price=None, exit_reason=None, result="OPEN", pnl_ticks=None, pnl_dollars=None, paper_order_id="PAPER-1")
    hook.after_entry(order(), fill)
    hook.after_exit(replace(fill, exit_price=110.0, result="WIN"))
    assert calls == []


def test_entry_and_exit_mirror_when_enabled(monkeypatch, sync, tmp_path):
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", "true")
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_LOG_DIR", str(tmp_path))
    seen = []

    class R:
        status = "SUBMITTED"; leg = "entry"; client_order_id = "cid"; broker_order_id = "b"; symbol = "MNQZ6"
        side = "BUY"; quantity = 1; order_type = "LIMIT"; limit_price = 100.0; reason = None

    import execution.webull_sandbox_futures_mirror as m
    monkeypatch.setattr(m, "mirror_entry", lambda o, **kw: (seen.append(("entry", kw.get("source_id"))), R)[1])
    monkeypatch.setattr(m, "mirror_exit", lambda f, **kw: (seen.append(("exit", kw.get("source_id"))), R)[1])

    fill = Fill(instrument="MNQ", direction="LONG", contracts=1, entry_price=100.0, exit_price=None, exit_reason=None, result="OPEN", pnl_ticks=None, pnl_dollars=None, paper_order_id="PAPER-7")
    hook.after_entry(order(), fill, lane="unit")
    exited = Fill(instrument="MNQ", direction="LONG", contracts=1, entry_price=100.0, exit_price=110.0, exit_reason="TARGET_HIT", result="WIN", pnl_ticks=40.0, pnl_dollars=20.0, paper_order_id="PAPER-7")
    hook.after_exit(exited, lane="unit")
    cancelled = Fill(instrument="MNQ", direction="LONG", contracts=1, entry_price=100.0, exit_price=None, exit_reason="ENTRY_NOT_TRIGGERED", result="CANCELLED", pnl_ticks=None, pnl_dollars=None, paper_order_id="PAPER-8")
    hook.after_exit(cancelled, lane="unit")   # no-fill → nothing
    hook.after_entry(order(), cancelled, lane="unit")  # not OPEN → nothing

    assert seen == [("entry", "PAPER-7"), ("exit", "PAPER-7")]
    rows = [json.loads(l) for f in tmp_path.glob("webull_mirror_*.jsonl") for l in f.read_text().splitlines()]
    assert [r["event"] for r in rows] == ["entry", "exit"]
    assert rows[0]["status"] == "SUBMITTED" and rows[0]["symbol"] == "MNQZ6" and rows[0]["lane"] == "unit"
    assert rows[1]["result"] == "WIN" and rows[1]["pnl_dollars"] == 20.0


def test_hook_exception_never_escapes(monkeypatch, sync, tmp_path):
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", "true")
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_LOG_DIR", str(tmp_path))
    import execution.webull_sandbox_futures_mirror as m
    def boom(*a, **k): raise RuntimeError("network")
    monkeypatch.setattr(m, "mirror_entry", boom)
    fill = Fill(instrument="MNQ", direction="LONG", contracts=1, entry_price=100.0, exit_price=None, exit_reason=None, result="OPEN", pnl_ticks=None, pnl_dollars=None, paper_order_id="PAPER-9")
    hook.after_entry(order(), fill)
    rows = [json.loads(l) for f in tmp_path.glob("webull_mirror_*.jsonl") for l in f.read_text().splitlines()]
    assert rows and rows[0]["status"] == "ERROR" and rows[0]["reason"] == "hook:RuntimeError"


def test_paper_broker_returns_identical_fills_with_mirror_on(monkeypatch, sync, tmp_path):
    """Wiring must not change PaperBroker behaviour — same Fill either way."""
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_LOG_DIR", str(tmp_path))
    import execution.webull_sandbox_futures_mirror as m
    monkeypatch.setattr(m, "mirror_entry", lambda o, **kw: None)
    monkeypatch.setattr(m, "mirror_exit", lambda f, **kw: None)

    def run(flag: str):
        monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", flag)
        b = PaperBroker(starting_balance=1000.0)
        f1 = b.execute_bracket(order(), paper_order_id="PAPER-X")
        f2 = b.resolve_position(NextBarOHLC(open=101.0, high=112.0, low=99.0))
        return f1, f2

    off = run("false")
    on = run("true")
    assert off[0] == on[0]
    assert off[1] == on[1]
    assert on[0].result == "OPEN" and on[1] is not None and on[1].result == "WIN"
    rows = [json.loads(l) for f in tmp_path.glob("webull_mirror_*.jsonl") for l in f.read_text().splitlines()]
    assert [r["event"] for r in rows] == ["entry", "exit"]
    assert rows[0]["source_id"] == "PAPER-X" == rows[1]["source_id"]
