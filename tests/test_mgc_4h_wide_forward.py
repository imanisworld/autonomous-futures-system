"""MGC 4H wide forward observation evaluator (prereg 2026-09-23)."""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research import mgc_4h_wide_forward as mf

UTC = timezone.utc
REPO = Path(__file__).resolve().parents[1]


def test_frozen_constants_match_prereg():
    doc = (REPO / mf.PREREG_DOC).read_text()
    assert mf.SCORING_START == datetime(2026, 9, 24, 22, 0, tzinfo=UTC) and "2026-09-24T22:00:00Z" in doc
    assert mf.DEADLINE == date(2027, 6, 30) and "2027-06-30" in doc
    assert (mf.MIN_TERMINAL_TRADES, mf.MIN_OBS_DAYS) == (150, 60)
    assert (mf.PF_MIN, mf.TOP3_SHARE_MAX, mf.MAX_DRAWDOWN) == (1.30, 0.50, 6000.0)
    assert (mf.COST_PER_TRADE, mf.ATR_MULT, mf.TF_MINUTES, mf.WARMUP_DAYS) == (4.00, 2.0, 240, 90)
    assert (mf.NULL_A_DRAWS, mf.NULL_B_DRAWS, mf.GAP_DAY_SHARE_MAX) == (500, 200, 0.10)


def test_structural_setups_are_the_configured_mgc_populations():
    s = mf.structural_setups()
    assert "strat_22_continuation_observed" in s and "strat_212" in s
    assert "ema_pullback_trend" not in s and "orb_false_break_fade" not in s


def test_front_chain_uses_previous_day_volume_and_never_rolls_back():
    d = [date(2026, 11, 20) + timedelta(days=i) for i in range(4)]
    vol = {
        d[0]: {"MGCZ6": 100, "MGCG7": 10},
        d[1]: {"MGCZ6": 40, "MGCG7": 90},   # G7 leads today; used from d2
        d[2]: {"MGCZ6": 95, "MGCG7": 20},   # Z6 back on top: must not roll backwards
        d[3]: {"MGCZ6": 5, "MGCG7": 99},
    }
    f = mf.front_chain(vol, 2026)
    assert [f[x] for x in d] == ["MGCZ6", "MGCZ6", "MGCG7", "MGCG7"]


def test_contract_tickers_cover_bimonthly_gold_months():
    t = mf.contract_tickers(date(2026, 6, 1), date(2026, 12, 1))
    assert "MGCZ6" in t and "MGCG7" in t and "MGCU6" not in t


def test_resample_session_anchors_at_1800_et():
    start = int(datetime(2026, 9, 21, 22, 0, tzinfo=UTC).timestamp())  # 18:00 EDT
    raw = [{"ts": start + 900 * i, "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i, "close": 1.5 + i, "volume": 1}
           for i in range(20)]
    out = mf.resample_session(raw, 240)
    assert [b["ts"] for b in out] == [start, start + 4 * 3600]
    assert out[0]["open"] == 1.0 and out[0]["close"] == raw[15]["close"] and out[0]["volume"] == 16


def _bars(rows):
    t0 = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)
    return [{"ts": (t0 + timedelta(minutes=15 * i)).isoformat(), "open": r[0], "high": r[1], "low": r[2], "close": r[3]}
            for i, r in enumerate(rows)]


def test_resolve_wide_stop_first_and_trailing():
    # MGC tick 0.10 = $1.00 -> $10 per point. ATR 1.0 -> risk 2.0 points.
    # fill at 100, run to 103 (+1.5R), then fall: trail stop = 103 - 2 = 101 -> +$10.
    fwd = _bars([(100, 100.5, 99.5, 100.2), (100.2, 103, 100.1, 102.8), (102.8, 102.9, 100.5, 100.6)])
    r = mf.resolve_wide("LONG", 100.0, 1.0, False, fwd)
    assert r["result"] == "WIN" and r["gross"] == pytest.approx(10.0)
    # stop touched on the fill bar -> loss of 2 points = $20
    r = mf.resolve_wide("LONG", 100.0, 1.0, False, _bars([(100, 100.2, 97.9, 98)]))
    assert r["result"] == "LOSS" and r["gross"] == pytest.approx(-20.0)
    # never filled -> None ; no ATR -> None
    assert mf.resolve_wide("LONG", 105.0, 1.0, False, _bars([(100, 101, 99, 100)])) is None
    assert mf.resolve_wide("LONG", 100.0, None, False, fwd) is None
    # short mirror: fill 100, fall to 97, rally: trail = 97 + 2 = 99 -> +$10
    fwd_s = _bars([(100, 100.5, 99.5, 99.8), (99.8, 99.9, 97, 97.2), (97.2, 99.5, 97.1, 99.4)])
    r = mf.resolve_wide("SHORT", 100.0, 1.0, False, fwd_s)
    assert r["gross"] == pytest.approx(10.0)


def test_resolve_wide_expires_at_last_close():
    fwd = _bars([(100, 100.5, 99.8, 100.2), (100.2, 100.9, 100.0, 100.7)])
    r = mf.resolve_wide("LONG", 100.0, 1.0, False, fwd)
    assert r["result"] == "EXPIRED" and r["gross"] == pytest.approx(7.0)


def test_gap_days_flags_missing_regular_slot_only():
    from research.prereg929_forward_corpus import obs_day_window

    d = date(2026, 10, 1)
    lo, hi = obs_day_window(d)
    ts = list(range(int(lo.timestamp()), int(hi.timestamp()), 900))
    assert mf.gap_days([{"ts": t} for t in ts], d, d) == []
    g = mf.gap_days([{"ts": t} for t in ts if t != ts[10]], d, d)
    assert [x["obs_day"] for x in g] == ["2026-10-01"]


def test_touches_gap_window():
    assert mf._touches_gap("2026-10-01T14:00:00+00:00", "2026-10-01T15:00:00+00:00", {"2026-10-01"})
    assert not mf._touches_gap("2026-10-01T14:00:00+00:00", "2026-10-01T15:00:00+00:00", {"2026-10-05"})


def _run(trades, gaps=(), candles=None):
    run = mf.Run(fetched_at="2027-01-10T00:00:00+00:00", cutoff="2027-01-09T22:00:00+00:00", chain=[["MGCG7", "2026-11-26"]],
                 skipped_front=[], raw_sha256="x" * 64, gap_days=[{"obs_day": g} for g in gaps])
    run.trades = trades
    run.scoring_candles = candles or []
    return run


def _trades(n, fn, start=datetime(2026, 9, 25, 2, 0, tzinfo=UTC)):
    out = []
    for i in range(n):
        t = start + timedelta(hours=12 * i)
        net = fn(i)
        out.append({"strategy": "strat_212", "direction": "LONG" if i % 2 else "SHORT", "signal_ts": t.isoformat(),
                    "day": (t + timedelta(hours=6)).date().isoformat(), "result": "WIN" if net > 0 else "LOSS",
                    "exit_ts": (t + timedelta(hours=1)).isoformat(), "net": net, "mirror_net": -net - 8})
    return out


def test_counts_report_is_blind():
    rep = mf.counts_report(_run(_trades(10, lambda i: 50.0)), as_of=datetime(2026, 10, 5, tzinfo=UTC))
    blob = json.dumps(rep)
    for frag in ('"net"', "pnl", '"pf"', "WIN", "LOSS", "drawdown"):
        assert frag not in blob
    assert rep["terminal_trades"] == 10 and rep["sample"]["look_allowed"] is False
    with pytest.raises(AssertionError):
        mf.assert_blind({"total_net": 1})


def _step0(tmp_path, verdict="PASS"):
    p = tmp_path / "s0.json"
    p.write_text(json.dumps({"prereg": mf.PREREG_ID, "kind": "step0_parity", "verdict": verdict,
                             "generated_at": "2027-01-10T00:00:00+00:00"}))
    return p


def test_look_refusals(tmp_path):
    run = _run(_trades(10, lambda i: 50.0))
    with pytest.raises(mf.LookRefused, match="step-0"):
        mf.look_report(run, as_of=datetime(2027, 1, 10, tzinfo=UTC), step0_path=None)
    with pytest.raises(mf.LookRefused, match="BLOCKED"):
        mf.look_report(run, as_of=datetime(2027, 1, 10, tzinfo=UTC), step0_path=_step0(tmp_path, "FAIL"))
    with pytest.raises(mf.LookRefused, match="minimum sample"):
        mf.look_report(run, as_of=datetime(2027, 1, 10, tzinfo=UTC), step0_path=_step0(tmp_path))


def _candles(n=400, start=datetime(2026, 9, 25, 2, 0, tzinfo=UTC)):
    return [{"ts": (start + timedelta(hours=4 * i)).isoformat(), "close": 100.0, "atr": 1.0} for i in range(n)]


class _FlatPath:
    def forward(self, after_ts, day):
        t0 = datetime.fromisoformat(after_ts)
        return [{"ts": (t0 + timedelta(minutes=15 * k)).isoformat(), "open": 100, "high": 100.05, "low": 99.95, "close": 100}
                for k in range(3)]


def test_look_verdicts(tmp_path, monkeypatch):
    # Strong, evenly spread series -> random nulls are flat (PF ~0), so criteria 7/8 pass.
    good = _trades(200, lambda i: 60.0 if i % 3 else -40.0)
    run = _run(good, candles=_candles())
    run._path = _FlatPath()
    monkeypatch.setattr(mf, "_candle_days", lambda r: {f"d{i}" for i in range(80)})
    rep = mf.look_report(run, as_of=datetime(2027, 1, 10, tzinfo=UTC), step0_path=_step0(tmp_path))
    assert rep["verdict"] == "FORWARD_EVIDENCE", rep["criteria"]
    # Same sample but losing -> REJECTED
    bad = _run(_trades(200, lambda i: 20.0 if i % 3 == 0 else -30.0), candles=_candles())
    bad._path = _FlatPath()
    assert mf.look_report(bad, as_of=datetime(2027, 1, 10, tzinfo=UTC), step0_path=_step0(tmp_path))["verdict"] == "REJECTED"
    # Too many gap days -> INSUFFICIENT_DATA whatever the P&L
    gappy = _run(good, gaps=[f"2026-10-{i:02d}" for i in range(1, 15)], candles=_candles())
    gappy._path = _FlatPath()
    assert mf.look_report(gappy, as_of=datetime(2027, 1, 10, tzinfo=UTC), step0_path=_step0(tmp_path))["verdict"] == "INSUFFICIENT_DATA"


def test_look_at_deadline_below_minimum_is_insufficient_sample(tmp_path, monkeypatch):
    run = _run(_trades(30, lambda i: 60.0), candles=_candles())
    run._path = _FlatPath()
    monkeypatch.setattr(mf, "_candle_days", lambda r: {f"d{i}" for i in range(30)})
    rep = mf.look_report(run, as_of=datetime(2027, 7, 2, tzinfo=UTC), step0_path=_step0(tmp_path))
    assert rep["verdict"] == "INSUFFICIENT_SAMPLE"


def test_cli_look_guards(tmp_path, capsys):
    import importlib

    cli = importlib.import_module("scripts.mgc_4h_wide_forward")
    assert cli.main(["look", "--out", str(tmp_path / "l.json")]) == 3
    assert cli.main(["look", "--out", str(tmp_path / "l.json"), "--confirm-single-look"]) == 3
    (tmp_path / "l.json").write_text("{}")
    assert cli.main(["look", "--out", str(tmp_path / "l.json"), "--confirm-single-look",
                     "--step0-report", str(_step0(tmp_path))]) == 3
    assert "already happened" in capsys.readouterr().err
