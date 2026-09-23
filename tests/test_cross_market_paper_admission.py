"""Cross-market paper admission (prereg 2026-09-23): evaluator + switched-off broker gate."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research import cross_market_paper_admission as cma
from execution import paper_market_admission as pma

UTC = timezone.utc
REPO = Path(__file__).resolve().parents[1]


# ─── evaluator ───────────────────────────────────────────────────────────────

def _row(market="M2K", setup="strat_212", signal=None, exit_=None, gross=10.0, result="WIN", n=0, **kw):
    signal = signal or datetime(2026, 9, 17, 14, 0, tzinfo=UTC)
    exit_ = exit_ or signal + timedelta(minutes=30)
    row = {
        "record_type": "OUTCOME", "evidence_epoch": cma.EPOCH, "instrument": market, "strategy": setup,
        "entry_filled": True, "result": result, "gross_pnl_dollars_1_contract": gross,
        "signal_timestamp": signal.isoformat(), "exit_timestamp": exit_.isoformat(),
        "trading_date": signal.date().isoformat(), "candidate_id": f"c{n}",
    }
    row.update(kw)
    return row


def _series(n, start, *, gross_fn, market="M2K", setup="strat_212", step_hours=24, id_offset=0):
    rows = []
    for i in range(n):
        s = start + timedelta(hours=step_hours * i)
        g = gross_fn(i)
        rows.append(_row(market, setup, signal=s, gross=g, result="WIN" if g > 0 else "LOSS", n=id_offset + i))
    return rows


def test_costs_match_prereg_table():
    assert {m: cma.cost_per_trade(m) for m in cma.MARKETS} == {"M2K": 2.48, "MGC": 4.0, "MCL": 4.0, "MBT": 7.0}


def test_trade_filter_scope():
    assert cma.trade_from_row(_row()) is not None
    assert cma.trade_from_row(_row(market="MNQ")) is None  # out of scope
    assert cma.trade_from_row(_row(setup="ema_pullback_trend")) is None  # signal_metrics family
    assert cma.trade_from_row(_row(result="NO_FILL")) is None
    assert cma.trade_from_row(_row(entry_filled=False)) is None
    assert cma.trade_from_row(_row(evidence_epoch="other")) is None
    t = cma.trade_from_row(_row(result="EXPIRED", gross=-3.0, exit_timestamp="",
                                resolved_at_bar_ts="2026-09-17T16:00:00+00:00"))
    assert t is not None and t.net == round(-3.0 - 2.48, 2)
    assert t.exit_ts == datetime(2026, 9, 17, 16, 0, tzinfo=UTC)


def test_duplicate_candidates_counted_once():
    rows = [_row(n=1), _row(n=1)]
    assert len(cma.load_trades(rows)[("M2K", "strat_212")]) == 1


def test_checkpoints_are_fridays_1700_et():
    cps = cma.checkpoints_through(datetime(2026, 10, 3, tzinfo=UTC))
    assert cps == [date(2026, 9, 25), date(2026, 10, 2)]
    assert all(d.weekday() == 4 for d in cps)
    assert cma.checkpoints_through(datetime(2026, 9, 25, 20, 59, tzinfo=UTC)) == []  # 16:59 ET


def _good(i):  # wins 3 of 4, clearly above the PF hurdle after costs
    return 30.0 if i % 4 else -20.0


def test_screening_until_minimums_then_confirmed_on_fresh_trades():
    start = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)
    screen = _series(45, start, gross_fn=_good, step_hours=12)  # ~22 days
    rep = cma.evaluate(screen, datetime(2026, 9, 26, tzinfo=UTC))
    assert rep["pairs"]["M2K:strat_212"]["status"] == "SCREENING"  # too few days by 09-25
    as_of = datetime(2026, 10, 10, tzinfo=UTC)
    rep = cma.evaluate(screen, as_of)
    pair = rep["pairs"]["M2K:strat_212"]
    d = pair["stage_a_pass_checkpoint"]
    assert d is not None and pair["status"] == "CONFIRMING"
    cut = cma.checkpoint_instant(date.fromisoformat(d))
    # Trades already in the screen never count toward confirmation.
    fresh = _series(40, cut + timedelta(hours=1), gross_fn=_good, step_hours=6, id_offset=1000)
    rep = cma.evaluate(screen + fresh, cut + timedelta(days=30))
    pair = rep["pairs"]["M2K:strat_212"]
    assert pair["status"] == "CONFIRMED"
    assert pair["stage_b"]["trades"] == 40 and pair["stage_b"]["pass"] is True


def test_rejected_when_fresh_trades_fail_and_never_rescreened():
    start = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)
    screen = _series(45, start, gross_fn=_good, step_hours=12)
    d = cma.evaluate(screen, datetime(2026, 10, 10, tzinfo=UTC))["pairs"]["M2K:strat_212"]["stage_a_pass_checkpoint"]
    cut = cma.checkpoint_instant(date.fromisoformat(d))
    bad = _series(40, cut + timedelta(hours=1), gross_fn=lambda i: -10.0 if i % 3 else 12.0,
                  step_hours=6, id_offset=2000)
    later_good = _series(80, cut + timedelta(days=12), gross_fn=_good, step_hours=6, id_offset=3000)
    rep = cma.evaluate(screen + bad + later_good, cut + timedelta(days=60))
    assert rep["pairs"]["M2K:strat_212"]["status"] == "REJECTED"


def test_stage_a_criteria_block_concentrated_or_losing_pairs():
    start = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)
    # One huge day carries everything -> A6 fails (and halves fail).
    rows = _series(44, start, gross_fn=lambda i: -3.0, step_hours=12)
    rows.append(_row(signal=start + timedelta(days=1), gross=900.0, n=999))
    m = cma.evaluate(rows, datetime(2026, 10, 30, tzinfo=UTC))["pairs"]["M2K:strat_212"]
    assert m["status"] == "SCREENING"
    assert m["stage_a_latest"]["criteria"]["A6_top3_days_le_60pct"] is False


def test_deadline_outcomes():
    after = datetime(2027, 4, 2, tzinfo=UTC)
    rep = cma.evaluate([], after)
    assert set(rep["status_counts"]) == {"NOT_ADMITTED"} and rep["status_counts"]["NOT_ADMITTED"] == 36


def test_all_36_pairs_reported():
    rep = cma.evaluate([], datetime(2026, 9, 30, tzinfo=UTC))
    assert len(rep["pairs"]) == 36 and rep["status_counts"] == {"SCREENING": 36}


def test_setups_are_the_structural_outcome_families_in_config():
    cfg = json.loads((REPO / "config" / "cross_instrument_observation.json").read_text())
    structural = {
        p["strategy"] for p in cfg["populations"]
        if p["collection_mode"] == "structural_outcome" and set(cma.MARKETS) <= set(p["instruments"])
    }
    assert set(cma.SETUPS) == structural


def test_cli_prints_every_pair(tmp_path, capsys):
    import importlib

    cli = importlib.import_module("scripts.cross_market_paper_admission")
    log = tmp_path / "obs.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [_row(n=1), _row(n=2, gross=-5.0, result="LOSS")]) + "\nnot json\n")
    out = tmp_path / "s.json"
    assert cli.main(["--log", str(log), "--as-of", "2026-09-30T00:00:00Z", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert printed.count("SCREENING") >= 36
    assert json.loads(out.read_text())["pairs"]["M2K:strat_212"]["stage_a_latest"]["trades"] == 2


# ─── admission file ──────────────────────────────────────────────────────────

def test_shipped_admission_file_is_empty():
    data = json.loads((REPO / "config" / "paper_market_admission.json").read_text())
    assert data["admitted"] == []
    assert pma.admitted_markets() == frozenset()


def _adm(tmp_path, rows):
    p = tmp_path / "adm.json"
    p.write_text(json.dumps({"admitted": rows}))
    return p


def test_admission_rows_validated(tmp_path):
    ok = {"market": "m2k", "setup": "strat_212", "prereg_status": "CONFIRMED", "operator_go": "2026-11-01 operator"}
    p = _adm(tmp_path, [
        ok,
        {**ok, "market": "MGC", "prereg_status": "CONFIRMING"},  # not confirmed
        {**ok, "market": "MCL", "operator_go": ""},  # no GO
        {**ok, "market": "MNQ"},  # base market row ignored
        {**ok, "market": "ZZZ"},  # unknown
        "junk",
    ])
    assert pma.admitted_markets(p) == frozenset({"M2K"})
    assert pma.paper_order_allowed("M2KZ6", p) == (True, "M2K")
    assert pma.paper_order_allowed("MGC1!", p) == (False, "MGC")
    assert pma.paper_order_allowed("MNQ", p) == (True, "MNQ")
    assert pma.paper_order_allowed("MES1!", _adm(tmp_path, [])) == (True, "MES")
    assert pma.paper_order_allowed("SPY", p) == (False, None)


def test_admission_file_problems_fail_closed(tmp_path):
    assert pma.admitted_markets(tmp_path / "missing.json") == frozenset()
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert pma.admitted_markets(bad) == frozenset()
    bad.write_text(json.dumps({"admitted": {"market": "M2K"}}))
    assert pma.admitted_markets(bad) == frozenset()


# ─── broker gate + single-source ticks ───────────────────────────────────────

def test_broker_tick_tables_match_old_values_and_single_source():
    from config import futures_contracts as fc
    from execution import tradovate_broker as tb

    old_size = {"MES": 0.25, "ES": 0.25, "MNQ": 0.25, "NQ": 0.25, "MGC": 0.10, "MCL": 0.01}
    old_value = {"MES": 1.25, "ES": 12.50, "MNQ": 0.50, "NQ": 5.00, "MGC": 1.00, "MCL": 1.00}
    for root, v in old_size.items():
        assert tb._TICK_SIZE[root] == v
    for root, v in old_value.items():
        assert tb._TICK_VALUE[root] == v
    assert tb._TICK_SIZE is fc.TICK_SIZE and tb._TICK_VALUE is fc.TICK_VALUE
    assert tb._round_to_tick(212.37, "M2K") == 212.4  # was snapped to 0.25 before
    assert tb._round_to_tick(86937.0, "MBT") == 86935.0


def _broker(monkeypatch, admission_path):
    from execution import tradovate_supervisor as supervisor
    from execution.tradovate_broker import TradovateBroker, TradovateConfig

    monkeypatch.setenv("TRADOVATE_ENV", "demo")
    monkeypatch.setattr(pma, "ADMISSION_FILE", admission_path)
    broker = TradovateBroker(config=TradovateConfig(env="demo"))
    broker._account_id = 1
    monkeypatch.setattr(broker, "_authenticate", lambda: True)
    monkeypatch.setattr(supervisor, "tradovate_order_ready", lambda: True)

    def no_contract(_):
        raise AssertionError("gate must stop the order before contract lookup")

    monkeypatch.setattr(broker, "_find_contract_id", no_contract)
    monkeypatch.setattr(broker, "_post", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no order post")))
    return broker


def _order(instrument, entry=100.0, stop=99.0, target=102.0):
    from execution.broker_interface import BracketOrder

    return BracketOrder(instrument=instrument, direction="LONG", entry=entry, stop=stop, target=target,
                        rr_ratio=2.0, strategy="test")


@pytest.mark.parametrize("instrument", ["M2K", "MGC1!", "MCL", "MBTV6"])
def test_broker_refuses_unadmitted_markets(monkeypatch, tmp_path, instrument):
    broker = _broker(monkeypatch, _adm(tmp_path, []))
    fill = broker.execute_bracket(_order(instrument))
    assert "MARKET_NOT_ADMITTED" in json.dumps(fill.__dict__, default=str)


def test_broker_refuses_admitted_market_without_roll_rule(monkeypatch, tmp_path):
    row = {"market": "MGC", "setup": "strat_212", "prereg_status": "CONFIRMED", "operator_go": "x"}
    broker = _broker(monkeypatch, _adm(tmp_path, [row]))
    fill = broker.execute_bracket(_order("MGC"))
    assert "NO_ROLL_POLICY" in json.dumps(fill.__dict__, default=str)


def test_broker_lets_admitted_quarterly_market_past_the_gate(monkeypatch, tmp_path):
    row = {"market": "M2K", "setup": "strat_212", "prereg_status": "CONFIRMED", "operator_go": "x"}
    broker = _broker(monkeypatch, _adm(tmp_path, [row]))
    fill = broker.execute_bracket(_order("M2K", entry=2200.0, stop=2195.0, target=2210.0))
    text = json.dumps(fill.__dict__, default=str)
    # Past the admission gate: it stopped at contract lookup (stubbed to fail), not the gate.
    assert "MARKET_NOT_ADMITTED" not in text and "NO_ROLL_POLICY" not in text
    assert "gate must stop the order before contract lookup" in text or "CONTRACT" in text.upper()


def test_broker_mnq_passes_gate_with_empty_admission(monkeypatch, tmp_path):
    broker = _broker(monkeypatch, _adm(tmp_path, []))
    fill = broker.execute_bracket(_order("MNQ", entry=19500.0, stop=19460.0, target=19580.0))
    text = json.dumps(fill.__dict__, default=str)
    assert "MARKET_NOT_ADMITTED" not in text and "NO_ROLL_POLICY" not in text
    assert "gate must stop the order before contract lookup" in text or "CONTRACT" in text.upper()


def test_contract_name_reverse_lookup_exact_roots(monkeypatch):
    from execution.tradovate_broker import TradovateBroker, TradovateConfig

    broker = TradovateBroker(config=TradovateConfig(env="demo"))
    for name, root in (("MESZ26", "MES"), ("MNQZ6", "MNQ"), ("M2KZ6", "M2K"), ("MBTV6", "MBT"),
                       ("MGCZ6", "MGC"), ("NQZ6", "NQ"), ("XYZ", None)):
        monkeypatch.setattr(broker, "_get", lambda _url, n=name: {"name": n})
        assert broker._contract_id_to_name(1) == root
