"""Independent checks written by the verifier (not by Futures). Contract = AFS-0025.
All go through the genuine execution/paper_broker.py (blob 2a67d276).

These sit alongside tests/test_resolve_bracket_exact_eod.py. The original
no-earlier-bar test also passes on the old date-only guard; the same-date
bars after 15:55 ET in this file are the checks that fail on that guard.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest
from scripts import edge_decomposition_audit as audit
from execution import paper_broker as pb_mod
from execution.paper_broker import PaperBroker

ET = ZoneInfo("America/New_York"); UTC = ZoneInfo("UTC")
L322 = audit.LANES["322_mnq"]; L4HR = audit.LANES["4hr_mnq"]; CARRY = audit.LANES["orb_reclaim_mnq"]


def test_resolver_uses_genuine_paper_broker():
    assert audit.PaperBroker is PaperBroker
    src = Path(pb_mod.__file__).read_bytes()
    import hashlib
    assert hashlib.sha1(b"blob %d\0" % len(src) + src).hexdigest().startswith("2a67d276")


def _bars(rows_et, minutes=5):
    rows = []
    for dt_et, o, h, l, c in rows_et:
        dt = dt_et.astimezone(UTC)
        rows.append({"timestamp": dt.isoformat(), "_dt": dt, "open": o, "high": h, "low": l, "close": c,
                     "session": "new_york"})
    return audit.Bars(corpus_dir=Path("."), instrument="MNQ", rows=rows, files=[Path("s")],
                      by_ts={r["timestamp"]: i for i, r in enumerate(rows)},
                      by_dt={r["_dt"]: i for i, r in enumerate(rows)},
                      file_of_idx=[0] * len(rows), bars_per_day=len(rows))


def _span(a, b, ohlc, step=5):
    out, t = [], a
    while t <= b:
        out.append((t, *ohlc)); t += timedelta(minutes=step)
    return out


def _run(lane, bars, idx, d, e, s, t, fm="market", slip=1.0):
    c = audit._candidate(lane, bars, idx, d, e, s, t)
    return audit.resolve_bracket(lane, bars, c, fill_model=fm, slippage_ticks=slip, tolerance_ticks=32.0)


MLK = datetime(2025, 1, 20, tzinfo=ET)


def _mlk_prearmed_bars():
    # decision bar 10:35 (idx 0) arms the stop-sell at 21658.5; 10:40 bar trades through it
    rows = [(MLK.replace(hour=10, minute=35), 21690.0, 21700.0, 21670.0, 21687.0),
            (MLK.replace(hour=10, minute=40), 21687.0, 21697.0, 21641.0, 21657.5)]
    rows += _span(MLK.replace(hour=10, minute=45), MLK.replace(hour=12, minute=55), (21660.0, 21700.0, 21600.0, 21650.0))
    rows += _span(MLK.replace(hour=18), MLK.replace(hour=19, minute=45), (21600.0, 21640.0, 21560.0, 21590.0))
    rows.append((MLK.replace(hour=19, minute=50), 21560.0, 21565.0, 21530.0, 21540.0))
    return _bars(rows)


@pytest.mark.parametrize("lane", [L322, L4HR])
def test_mlk_stop_market_prearmed_path_fails_closed(lane):
    bars = _mlk_prearmed_bars()
    res = _run(lane, bars, 0, "SHORT", 21658.5, 21780.0, 21538.0, fm="stop_market", slip=3.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING, res
    assert res["fill_entry"] == pytest.approx(21658.5 - 0.75)
    assert (bars.et(res["exit_idx"]).hour, bars.et(res["exit_idx"]).minute) == (18, 0)


def test_short_1555_bar_touching_both_resolves_as_stop():
    d = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(d.replace(hour=15, minute=40), d.replace(hour=15, minute=50), (100.0, 100.5, 99.5, 100.25))
    rows.append((d.replace(hour=15, minute=55), 100.0, 106.0, 89.0, 95.0))
    bars = _bars(rows)
    res = _run(L322, bars, 0, "SHORT", 100.0, 105.0, 90.0, slip=3.0)
    assert res["status"] == "RESOLVED" and res["exit_reason"] == "STOP_HIT" and res["result"] == "LOSS"
    assert res["exit_price"] == pytest.approx(105.75) and res["fill_entry"] == pytest.approx(99.25)
    assert res["exit_bar_ts"] == bars.rows[-1]["timestamp"]


@pytest.mark.parametrize("day,utc_hhmm", [
    (datetime(2025, 7, 15, tzinfo=ET), "19:55"),   # EDT (summer)
    (datetime(2026, 1, 13, tzinfo=ET), "20:55"),   # EST (winter)
    (datetime(2026, 3, 9, tzinfo=ET), "19:55"),    # first Monday after spring-forward (EDT)
    (datetime(2025, 11, 3, tzinfo=ET), "20:55"),   # first Monday after fall-back (EST)
])
def test_normal_day_flattens_on_1555_et_wall_time_across_dst(day, utc_hhmm):
    rows = _span(day.replace(hour=15, minute=30), day.replace(hour=15, minute=55), (100.0, 100.5, 99.5, 100.25))
    rows += _span(day.replace(hour=16), day.replace(hour=16, minute=55), (100.0, 111.0, 99.5, 110.5))  # would hit target
    rows += _span(day.replace(hour=18), day.replace(hour=18, minute=10), (100.0, 111.0, 99.5, 110.5))
    bars = _bars(rows)
    res = _run(L322, bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "RESOLVED" and res["exit_reason"] == audit.DAY_ONLY_EXIT_REASON
    assert res["exit_bar_ts"].split("T")[1][:5] == utc_hhmm
    assert res["exit_price"] == pytest.approx(100.25) and res["result"] == "BREAKEVEN"


def test_summer_utc_2055_bar_is_1655_et_not_eod():
    # 2025-07-15: a bar stamped 20:55Z is 16:55 ET (NOT the EOD bar in summer).
    d = datetime(2025, 7, 15, tzinfo=ET)
    rows = _span(d.replace(hour=15, minute=35), d.replace(hour=15, minute=50), (100.0, 100.5, 99.5, 100.25))
    rows.append((datetime(2025, 7, 15, 20, 55, tzinfo=UTC).astimezone(ET), 100.0, 111.0, 99.5, 110.5))
    res = _run(L322, _bars(rows), 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING


def test_1555_missing_with_1605_and_evening_bars_after():
    d = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(d.replace(hour=15, minute=35), d.replace(hour=15, minute=50), (100.0, 100.5, 99.5, 100.25))
    rows.append((d.replace(hour=16, minute=5), 100.0, 100.5, 99.5, 100.25))
    rows += _span(d.replace(hour=18), d.replace(hour=18, minute=30), (100.0, 111.0, 99.5, 110.5))
    bars = _bars(rows)
    res = _run(L322, bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING
    assert bars.et(res["exit_idx"]).strftime("%H:%M") == "16:05"


def test_stop_before_1555_on_normal_day_unchanged():
    d = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(d.replace(hour=10, minute=40), d.replace(hour=11, minute=0), (100.0, 100.5, 99.5, 100.25))
    rows.append((d.replace(hour=11, minute=5), 100.0, 100.5, 94.0, 94.5))
    rows += _span(d.replace(hour=11, minute=10), d.replace(hour=15, minute=55), (95.0, 95.5, 94.5, 95.0))
    bars = _bars(rows)
    res = _run(L322, bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "RESOLVED" and res["exit_reason"] == "STOP_HIT"
    assert bars.et(res["exit_idx"]).strftime("%H:%M") == "11:05"


def test_next_calendar_day_bars_are_not_walked():
    # The trade date is 2026-03-02. The next calendar date's 10:00 bar trades
    # through the target. Day-only resolution must not book that target.
    # This also passes on the old date-only guard: a later ET date already
    # stops the walk. The same-date bars after 15:55 are what that guard misses.
    trade = datetime(2026, 3, 2, tzinfo=ET)
    nxt = datetime(2026, 3, 3, 10, 0, tzinfo=ET)
    quiet = (100.0, 100.5, 99.5, 100.25)
    target_bar = (nxt, 100.0, 111.0, 99.5, 110.5)

    present = _span(trade.replace(hour=15, minute=40), trade.replace(hour=15, minute=55), quiet)
    present.append(target_bar)
    bars = _bars(present)
    res = _run(L322, bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "RESOLVED" and res["exit_reason"] == audit.DAY_ONLY_EXIT_REASON
    exit_et = bars.et(res["exit_idx"])
    assert exit_et.date() == trade.date() and (exit_et.hour, exit_et.minute) == (15, 55)
    assert res["result"] != "WIN"

    missing = _span(trade.replace(hour=15, minute=40), trade.replace(hour=15, minute=50), quiet)
    missing.append(target_bar)
    bars = _bars(missing)
    res = _run(L322, bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "UNRESOLVED" and res["reason"] == audit.EOD_BAR_MISSING
    assert "exit_bar_ts" not in res and "result" not in res and res.get("exit_reason") is None
    assert bars.et(res["exit_idx"]).date() == nxt.date()


def test_non_day_only_lane_still_carries_past_1600_and_overnight():
    d = datetime(2026, 3, 2, tzinfo=ET)
    rows = _span(d.replace(hour=15, minute=15), d.replace(hour=15, minute=45), (100.0, 100.5, 99.5, 100.25), step=15)
    rows += _span(d.replace(hour=18), d.replace(hour=18, minute=15), (100.0, 100.5, 99.5, 100.25), step=15)
    rows.append((d.replace(hour=18, minute=30), 100.0, 111.0, 99.5, 110.5))
    bars = _bars(rows)
    res = _run(CARRY, bars, 0, "LONG", 100.0, 95.0, 110.0)
    assert res["status"] == "RESOLVED" and res["exit_reason"] == "TARGET_HIT"
    assert bars.et(res["exit_idx"]).strftime("%H:%M") == "18:30"
