from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from scripts.strat_htf_magnitude_ftfc_audit import (
    ET,
    Bar,
    Context,
    HBar,
    Offsets,
    Side,
    arm,
    build_htf,
    is_hammer,
    resolve_from,
    stop_market_fill,
    trading_date,
)


def _bar(ts, o, h, l, c):
    return Bar(ts, o, h, l, c, None, trading_date(ts))


def _session(start_utc, n, price=20000.0, step=1.0):
    bars = []
    for k in range(n):
        ts = start_utc + timedelta(minutes=15 * k)
        p = price + k * step
        bars.append(_bar(ts, p, p + 2, p - 2, p + 0.5))
    return bars


def test_htf_bars_aggregate_and_bucket_on_session_anchors():
    # Sun 2026-09-20 18:00 ET = 22:00Z, full session to 17:00 ET = 92 bars
    bars = _session(datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc), 92)
    h4 = build_htf("4h", bars)
    assert [hb.start.astimezone(ET).hour for hb in h4] == [18, 22, 2, 6, 10, 14]
    assert all(hb.complete for hb in h4)
    assert [hb.i1 - hb.i0 for hb in h4] == [16, 16, 16, 16, 16, 12]
    for hb in h4:
        seg = bars[hb.i0:hb.i1]
        assert hb.high == max(b.high for b in seg) and hb.low == min(b.low for b in seg)
        assert hb.open == seg[0].open and hb.close == seg[-1].close
    h1 = build_htf("60m", bars)
    assert len(h1) == 23 and all(hb.complete for hb in h1)
    d = build_htf("daily", bars)
    assert len(d) == 1 and d[0].complete and d[0].td == "2026-09-21"


def test_missing_15m_bar_makes_htf_bar_incomplete():
    bars = _session(datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc), 92)
    holed = bars[:5] + bars[6:]
    assert not build_htf("60m", holed)[1].complete
    assert not build_htf("4h", holed)[0].complete
    assert not build_htf("daily", holed)[0].complete


def test_stop_entry_matches_paper_broker_semantics():
    ts = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    side = Side("LONG", 20010.0, 19990.0, 20050.0)
    touch = _bar(ts, 20000.0, 20012.0, 19998.0, 20011.0)
    gap = _bar(ts, 20015.0, 20020.0, 20014.0, 20018.0)
    miss = _bar(ts, 20000.0, 20009.75, 19995.0, 20005.0)
    assert stop_market_fill("MNQ", touch, side, 20050.0) == (20010.25, "FILLED")   # trigger + 1 tick
    assert stop_market_fill("MNQ", gap, side, 20050.0) == (20015.25, "FILLED")     # open + 1 tick
    assert stop_market_fill("MNQ", miss, side, 20050.0)[0] is None


def test_fill_bar_stop_is_a_loss_and_target_never_on_fill_bar():
    t0 = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    bars = [_bar(t0, 100, 130, 80, 120), _bar(t0 + timedelta(minutes=15), 120, 125, 118, 124)]
    out = resolve_from("MNQ", bars, Offsets([]), 0, "LONG", 101.0, 90.0, 125.0, "2026-09-21")
    assert out["result"] == "LOSS" and out["exit_reason"] == "FILL_BAR_STOP"
    bars2 = [_bar(t0, 100, 130, 95, 120), _bar(t0 + timedelta(minutes=15), 120, 126, 118, 124)]
    out2 = resolve_from("MNQ", bars2, Offsets([]), 0, "LONG", 101.0, 90.0, 125.0, "2026-09-21")
    assert out2["result"] == "WIN" and out2["exit_i"] == 1


def test_time_exit_and_roll_exit():
    t0 = datetime(2026, 9, 21, 20, 30, tzinfo=timezone.utc)  # 16:30 ET, td 09-21
    bars = [_bar(t0, 100, 101, 99, 100), _bar(t0 + timedelta(minutes=15), 100, 101, 99, 100.5),
            _bar(datetime(2026, 9, 21, 22, 0, tzinfo=timezone.utc), 100, 101, 99, 100)]  # next td
    out = resolve_from("MNQ", bars, Offsets([]), 0, "LONG", 100.0, 90.0, 120.0, "2026-09-21")
    assert (out["result"], out["exit_i"], out["exit_price"]) == ("TIME_EXIT", 1, 100.25)
    roll = Offsets([(bars[1].ts, 50.0)])
    out = resolve_from("MNQ", bars, roll, 0, "LONG", 100.0, 90.0, 120.0, "2026-09-22")
    assert (out["result"], out["exit_i"]) == ("ROLL_EXIT", 0)


def test_arming_uses_only_completed_bars_and_future_mutation_is_irrelevant():
    start = datetime(2026, 9, 1, 22, 0, tzinfo=timezone.utc)
    bars = []
    for day in range(12):
        s = start + timedelta(days=day)
        if s.astimezone(ET).weekday() in (4, 5):  # no Fri/Sat evening sessions
            continue
        bars += _session(s, 92, price=20000.0 + day * 7 * (-1) ** day, step=0.5 * (-1) ** day)
    h = build_htf("60m", bars)
    ctx = Context(bars, Offsets([]))
    j = 40
    before = [(a.setup, [(s.direction, s.trigger, s.stop, s.magnitude) for s in a.sides]) for a in arm("60m", h, j, 0.25, ctx, Offsets([]))]
    cut = h[j].i0
    mutated = bars[:cut] + [replace(b, high=b.high + 500, low=b.low - 500) for b in bars[cut:]]
    h2 = build_htf("60m", mutated)
    ctx2 = Context(mutated, Offsets([]))
    after = [(a.setup, [(s.direction, s.trigger, s.stop, s.magnitude) for s in a.sides]) for a in arm("60m", h2, j, 0.25, ctx2, Offsets([]))]
    assert before == after


def test_ftfc_at_trigger_uses_only_opens_at_or_before_trigger_bar():
    bars = _session(datetime(2026, 9, 20, 22, 0, tzinfo=timezone.utc), 92)
    ctx = Context(bars, Offsets([]))
    state = ctx.ftfc(40, bars[40].open)
    mutated = bars[:41] + [replace(b, open=b.open * 2) for b in bars[41:]]
    assert Context(mutated, Offsets([])).ftfc(40, bars[40].open) == state


def test_hammer_definition():
    t = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
    mk = lambda o, h, l, c: HBar(t, 0, 1, o, h, l, c, "2026-09-21", True)
    assert is_hammer(mk(9.8, 10.0, 7.0, 9.9))
    assert not is_hammer(mk(8.0, 10.0, 7.0, 9.9))
