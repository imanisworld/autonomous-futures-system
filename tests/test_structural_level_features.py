"""Synthetic-bar tests for research/structural_level_features.py (prereg P1).

One test per level rule (§3/§4.1) and per event / reduction rule (§5, §5.1, §6). All bars
are constructed; no journal, corpus or outcome is read.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from context.location_context import build_location_context
from research import structural_level_features as slf
from webhook.state_builder import detect_session as live_detect_session

ET = ZoneInfo("America/New_York")
INST = "MNQ"
TICK = 0.25


def _et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def flat_bars(start_et: datetime, end_et: datetime, price: float, *, drop=(), volume=100.0):
    """15m bars on every slot in [start, end) except the 17:00–18:00 ET halt and weekend."""
    out = []
    t = start_et
    while t < end_et:
        wd, hr = t.weekday(), t.hour
        halted = hr == 17 or wd == 5 or (wd == 4 and hr >= 17) or (wd == 6 and hr < 18)
        if not halted and t not in drop:
            # ±1 pt range on every filler bar so MTR15 is a real, non-zero scale (2.0 pts)
            out.append({"ts": t.astimezone(ZoneInfo("UTC")).isoformat(), "open": price, "high": price + 1.0,
                        "low": price - 1.0, "close": price, "volume": volume})
        t += timedelta(minutes=15)
    return out


def set_bar(bars, when_et: datetime, *, o=None, h=None, l=None, c=None, v=None):
    key = when_et.astimezone(ZoneInfo("UTC")).isoformat()
    for b in bars:
        if b["ts"] == key:
            if o is not None: b["open"] = o
            if h is not None: b["high"] = h
            if l is not None: b["low"] = l
            if c is not None: b["close"] = c
            if v is not None: b["volume"] = v
            return b
    raise KeyError(when_et)


# Tue 2026-09-08 .. Thu 2026-09-10 is a normal mid-week stretch; the prior full week is
# Mon 08-31 .. Fri 09-04.
D0 = _et(2026, 8, 30, 18)      # Sunday reopen (Monday 08-31 trading day)
D_END = _et(2026, 9, 10, 16)


def base_bars(price=20000.0):
    return flat_bars(D0, D_END, price)


# ── sessions / calendar ──────────────────────────────────────────────────────
def test_detect_session_matches_state_builder_over_a_full_week():
    t = _et(2026, 9, 6, 0)
    while t < _et(2026, 9, 13, 0):
        assert slf.detect_session(t) == live_detect_session(t), t
        t += timedelta(minutes=15)


def test_cme_open_hours_skips_halt_and_weekend():
    assert slf.cme_open_hours(_et(2026, 9, 8, 16), _et(2026, 9, 8, 19)) == 2.0   # 17–18 halt excluded
    assert slf.cme_open_hours(_et(2026, 9, 4, 16), _et(2026, 9, 6, 19)) == 2.0   # Fri 16–17 + Sun 18–19


# ── §3 levels ────────────────────────────────────────────────────────────────
def test_pdh_pdl_pdc_previous_trading_day_including_globex():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 8, 20), h=20050.0)           # Tue 20:00 ET = Wed 09-09 trading day → not prev-day for B0 on Wed
    set_bar(bars, _et(2026, 9, 8, 2), h=20030.0)            # Tue 02:00 ET = Tue trading day
    set_bar(bars, _et(2026, 9, 8, 14), l=19970.0, c=19990.0)
    set_bar(bars, _et(2026, 9, 8, 16, 45), c=19995.0)       # last bar of Tue trading day
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10))
    assert ls.levels["PDH"].value == 20030.0
    assert ls.levels["PDL"].value == 19970.0
    assert ls.levels["PDC_BAR"].value == 19995.0


def test_pwh_pwl_previous_full_week_and_not_available_when_incomplete():
    bars = base_bars()
    set_bar(bars, _et(2026, 8, 31, 10), h=20200.0)          # Monday of the prior week
    set_bar(bars, _et(2026, 9, 4, 12), l=19800.0)           # Friday of the prior week
    set_bar(bars, _et(2026, 9, 8, 10), h=20300.0)           # this week — must not count
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10))
    assert (ls.levels["PWH"].value, ls.levels["PWL"].value) == (20200.0, 19800.0)
    assert ls.levels["PWH"].gap_minutes == 0 and ls.levels["PWH"].gap_contaminated is False
    # drop most of Wednesday 09-02 → still AVAILABLE (level exists) but gap-contaminated (§9.4)
    bars2 = [b for b in bars if not b["ts"].startswith("2026-09-02")]
    ls2 = slf.build_levels(bars2, INST, b0_ts=_et(2026, 9, 9, 10))
    assert ls2.levels["PWH"].status == "AVAILABLE" and ls2.levels["PWH"].gap_contaminated is True
    # drop the whole previous week → NOT_AVAILABLE
    bars3 = [b for b in bars if datetime.fromisoformat(b["ts"]) >= _et(2026, 9, 6, 18)]
    assert slf.build_levels(bars3, INST, b0_ts=_et(2026, 9, 9, 10)).levels["PWH"].status == "NOT_AVAILABLE"


def test_onh_onl_frozen_rth_only_and_not_admitted_pre_rth():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 8, 22), h=20040.0)           # overnight of Wed trading day
    set_bar(bars, _et(2026, 9, 9, 5), l=19960.0)
    set_bar(bars, _et(2026, 9, 9, 9, 30), h=20080.0)        # RTH bar: must not enter ONH
    rth = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10))
    assert (rth.levels["ONH"].value, rth.levels["ONL"].value) == (20040.0, 19960.0)
    assert rth.levels["ONH"].status == "AVAILABLE"
    pre = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 6))
    assert pre.levels["ONH"].status == "NOT_IN_WINDOW"


def test_hod_lod_are_trading_day_extremes_and_exploratory():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 8, 22), h=20040.0)
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10))
    assert ls.levels["HOD"].value == 20040.0 and ls.levels["HOD"].exploratory


def test_ny_orb_is_the_0930_bar_and_missing_bar_is_not_available():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 9, 9, 30), h=20020.0, l=19980.0)
    set_bar(bars, _et(2026, 9, 9, 9, 45), h=20060.0, l=19940.0)   # wider — must not be used
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 11))
    assert (ls.levels["NY_ORB_H"].value, ls.levels["NY_ORB_L"].value) == (20020.0, 19980.0)
    assert ls.levels["NY_ORB_H"].status == "AVAILABLE"
    # 09:30 bar itself: not yet valid (valid from 09:45)
    assert slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 9, 30)).levels["NY_ORB_H"].status == "NOT_IN_WINDOW"
    # missing canonical bar → NOT_AVAILABLE even though the 09:45 bar exists
    bars2 = [b for b in bars if b["ts"] != _et(2026, 9, 9, 9, 30).astimezone(ZoneInfo("UTC")).isoformat()]
    ls2 = slf.build_levels(bars2, INST, b0_ts=_et(2026, 9, 9, 11))
    assert ls2.levels["NY_ORB_H"].status == "NOT_AVAILABLE" and ls2.levels["NY_ORB_H"].value is None
    # expired after 17:00 ET (asian session of the next trading day)
    assert slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 18, 30)).levels["NY_ORB_H"].status != "AVAILABLE"


def test_london_orb_is_the_0300_bar_and_expires_at_0930():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 9, 3, 0), h=20015.0, l=19985.0)
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 6))
    assert (ls.levels["LDN_ORB_H"].value, ls.levels["LDN_ORB_L"].value) == (20015.0, 19985.0)
    assert ls.levels["LDN_ORB_H"].status == "AVAILABLE"
    assert slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10)).levels["LDN_ORB_H"].status == "NOT_IN_WINDOW"
    bars2 = [b for b in bars if b["ts"] != _et(2026, 9, 9, 3, 0).astimezone(ZoneInfo("UTC")).isoformat()]
    assert slf.build_levels(bars2, INST, b0_ts=_et(2026, 9, 9, 6)).levels["LDN_ORB_H"].status == "NOT_AVAILABLE"


def test_vwap_resets_at_1800_et_not_at_sub_sessions():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 8, 15), h=30000.0, l=30000.0, c=30000.0, o=30000.0, v=1e9)  # Tue trading day — must be excluded
    set_bar(bars, _et(2026, 9, 8, 18), h=20100.0, l=20100.0, c=20100.0, o=20100.0, v=300.0)  # first bar of Wed trading day
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10))
    n_bars_wed = sum(1 for b in bars if _et(2026, 9, 8, 18) <= datetime.fromisoformat(b["ts"]) <= _et(2026, 9, 9, 10))
    expected = (20100.0 * 300 + 20000.0 * 100 * (n_bars_wed - 1)) / (300 + 100 * (n_bars_wed - 1))  # hlc3 of a ±1 bar = price
    assert abs(ls.levels["VWAP"].value - expected) < 1e-6
    # London → NY boundary must not reset: same accumulator continues
    ls_ldn = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 9, 15))
    assert ls_ldn.levels["VWAP"].value > 20000.0


def test_vwap_is_diagnostic_only_never_anchor_or_cluster():
    """v1.3: VWAP is computed (diagnostic) but NOT_ADMITTED — it can never be an anchor or
    count toward CLUSTER, even when it is the nearest level to entry."""
    bars = _day_with_levels()
    b0 = _et(2026, 9, 9, 11)
    ls = slf.build_levels(bars, INST, b0_ts=b0)
    assert ls.levels["VWAP"].status == "AVAILABLE" and ls.levels["VWAP"].exploratory
    vw = ls.levels["VWAP"].value
    # entry a tick above VWAP: VWAP would be the nearest supportive level if admitted
    a = slf.select_anchor(ls, slf.H_LEVEL_SETS["H5"], "strat", entry=vw + TICK, sign=+1)
    assert a is not None and a.name != "VWAP"
    assert "VWAP" not in slf.H_LEVEL_SETS["H5"] and "VWAP" in slf.DIAGNOSTIC_LEVELS
    lab = slf.label_candidate(bars, INST, direction="LONG", entry=vw + TICK, stop=vw - 10, target=vw + 20,
                              strategy="strat_212", b0_ts=b0)
    for h in lab["hypotheses"].values():
        assert h.get("anchor") != "VWAP" and h.get("opposing") != "VWAP"


def test_lc_zone_matches_live_collector_exactly():
    bars = base_bars()
    # an up-impulse on the 4H grid leaves a demand zone at its base
    for i, (hh, mm) in enumerate([(6, 0), (6, 15), (6, 30), (6, 45)]):
        set_bar(bars, _et(2026, 9, 8, hh, mm), o=19990.0 + i, h=19995.0 + i, l=19985.0 + i, c=19992.0 + i)
    for i, (hh, mm) in enumerate([(10, 0), (10, 15), (10, 30), (10, 45)]):
        set_bar(bars, _et(2026, 9, 8, hh, mm), o=20000.0 + 30 * i, h=20035.0 + 30 * i, l=19998.0 + 30 * i, c=20030.0 + 30 * i)
    b0 = _et(2026, 9, 9, 10)
    ls = slf.build_levels(bars, INST, b0_ts=b0)
    live = build_location_context(bars15=[b for b in bars if datetime.fromisoformat(b["ts"]) <= b0][-960:],
                                  price=ls.close, now=b0, market_condition=None, other_regime=None)
    assert ls.zones_raw == live["zones"] or {k: {kk: vv for kk, vv in v.items() if kk != "relation"} for k, v in live["zones"].items()} == ls.zones_raw
    assert round(ls.mtr15, 4) == live["mtr_15m_points"]
    assert ls.levels["PDH"].value == live["levels"]["pdh"]
    assert ls.levels["ONH"].value == live["levels"]["onh"]


def test_causality_bars_after_b0_are_ignored():
    bars = base_bars()
    set_bar(bars, _et(2026, 9, 9, 11), h=25000.0)
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 10))
    assert ls.levels["HOD"].value == 20001.0
    assert ls.b0_ts == _et(2026, 9, 9, 10)


# ── §5 events ────────────────────────────────────────────────────────────────
def _b(o, h, l, c, ts=None):
    return {"ts": ts or _et(2026, 9, 9, 10), "open": o, "high": h, "low": l, "close": c}


def _lvl(v, **kw):
    return slf.Level("PDL", v, "AVAILABLE", "point", valid_from_idx=0, **kw)


def test_touch_and_proximity_only():
    lvl = _lvl(100.0)
    assert slf.event_touch(_b(101, 102, 100, 101), lvl)
    assert not slf.event_touch(_b(101, 102, 100.25, 101), lvl)
    mtr = 4.0
    assert slf.event_proximity_only(_b(101, 102, 100.5, 101.5), lvl, +1, mtr)      # 1.5 ≤ 2.0 band, no touch
    assert not slf.event_proximity_only(_b(103, 104, 102.5, 103), lvl, +1, mtr)    # 3.0 > band
    assert not slf.event_proximity_only(_b(101, 102, 100, 101), lvl, +1, mtr)      # touched


def test_wick_reject_e3a_depth_cap():
    lvl = _lvl(100.0)
    assert slf.event_wick_reject(_b(101, 101.5, 99.0, 100.75), lvl, +1, TICK, 4.0)      # 1.0 ≤ D_max
    assert not slf.event_wick_reject(_b(101, 101.5, 95.0, 100.75), lvl, +1, TICK, 4.0)  # 5.0 > D_max → break, not sweep
    assert not slf.event_wick_reject(_b(101, 101.5, 99.0, 99.5), lvl, +1, TICK, 4.0)    # closed below
    assert slf.event_wick_reject(_b(99, 101.0, 98.5, 99.25), _lvl(100.0), -1, TICK, 4.0)  # SHORT mirror


def test_sweep_reclaim_e3b_multi_bar_and_k_cap():
    lvl = _lvl(100.0)
    past = [_b(101, 102, 100.5, 101.5), _b(101.5, 101.5, 98.5, 99.0), _b(99, 99.5, 98.75, 99.25), _b(99.25, 100.75, 99, 100.5)]
    assert slf.event_sweep_reclaim(past, lvl, +1, 4.0)
    # run longer than K bars below the level → not a sweep
    past_long = [_b(101, 102, 100.5, 101.5)] + [_b(99, 99.5, 98.75, 99.25)] * 5 + [_b(99.25, 100.75, 99, 100.5)]
    assert not slf.event_sweep_reclaim(past_long, lvl, +1, 4.0)
    # excursion deeper than D_max → break, not sweep
    past_deep = [_b(101, 102, 100.5, 101.5), _b(101.5, 101.5, 94.0, 95.0), _b(95, 100.75, 94.5, 100.5)]
    assert not slf.event_sweep_reclaim(past_deep, lvl, +1, 4.0)
    # B0 still below → nothing
    assert not slf.event_sweep_reclaim(past[:-1] + [_b(99.25, 99.75, 99, 99.5)], lvl, +1, 4.0)


def _break_seq(age: int, retest_now: bool, retest_earlier=False, hold=True):
    """Level 100 broken upward `age` bars before B0; closes held since."""
    lvl = _lvl(100.0)
    bars = [_b(99, 99.5, 98.5, 99.0)] * 3                 # below the level
    bars.append(_b(99.5, 101.5, 99.0, 101.0))             # BREAK bar
    for i in range(age - 1):
        low = 101.25 if not (retest_earlier and i == 0) else 100.2   # τ = 1.0 at mtr 4: 101.25 is outside, 100.2 inside
        bars.append(_b(101, 102, low, 101.5))
    if retest_now:
        bars.append(_b(101, 101.5, 100.5, 100.75 if hold else 99.5))
    else:
        bars.append(_b(101.5, 102, 101.25, 101.75))
    return bars, lvl


def test_break_retest_hold_reject_accept_and_age():
    for age in (2, 5, 8):
        bars, lvl = _break_seq(age, retest_now=True)
        st = slf.event_retest_state(bars, lvl, +1, TICK, 4.0)
        assert st == {"state": "BREAK_RETEST_HOLD", "break_age": age}
    bars, lvl = _break_seq(4, retest_now=True, hold=False)
    assert slf.event_retest_state(bars, lvl, +1, TICK, 4.0)["state"] == "BREAK_RETEST_REJECT"
    bars, lvl = _break_seq(4, retest_now=False)
    assert slf.event_retest_state(bars, lvl, +1, TICK, 4.0)["state"] == "ACCEPT_NO_RETEST"
    bars, lvl = _break_seq(4, retest_now=True, retest_earlier=True)
    assert slf.event_retest_state(bars, lvl, +1, TICK, 4.0)["state"] == "RETESTED_EARLIER"
    bars, lvl = _break_seq(1, retest_now=False)
    assert slf.event_retest_state(bars, lvl, +1, TICK, 4.0)["state"] == "IMMEDIATE"
    bars, lvl = _break_seq(9, retest_now=True)               # older than R → no eligible break
    assert slf.event_retest_state(bars, lvl, +1, TICK, 4.0)["state"] == "none"


def test_break_not_held_is_not_a_break():
    lvl = _lvl(100.0)
    # earlier break (j=2) failed to hold; B0 closing back above is a NEW break at j=0 → IMMEDIATE
    bars = [_b(99, 99.5, 98.5, 99.0), _b(99.5, 101.5, 99.0, 101.0), _b(101, 101, 98, 99.0), _b(99, 101, 99, 100.75)]
    assert slf.find_break(bars, lvl, +1, TICK, 8) == 0
    assert slf.event_retest_state(bars, lvl, +1, TICK, 4.0)["state"] == "IMMEDIATE"
    # and with B0 still below the level there is no held break at all
    bars[-1] = _b(99, 99.75, 98.5, 99.5)
    assert slf.find_break(bars, lvl, +1, TICK, 8) is None


def test_test_count_and_age():
    lvl = _lvl(100.0, formed_ts=_et(2026, 9, 8, 17))
    past = [_b(101, 102, 100, 101), _b(101, 102, 100.5, 101), _b(101, 101, 99.5, 100.5), _b(101, 102, 101, 101.5, ts=_et(2026, 9, 9, 10))]
    assert slf.event_test_count(past, lvl) == 2          # B0 excluded
    assert slf.event_age_hours(past[-1], lvl) == 16.25   # 18:00 → 10:15 ET next day, halt excluded


# ── §5.1 reduction + §6 labels on a full synthetic day ───────────────────────
def _day_with_levels():
    """Wed 09-09, 11:00 ET B0. PDL 19970, ONL 19960, NY ORB 19980/20020, prev week L 19800."""
    bars = base_bars()
    set_bar(bars, _et(2026, 8, 31, 10), h=20200.0)
    set_bar(bars, _et(2026, 9, 4, 12), l=19800.0)
    set_bar(bars, _et(2026, 9, 8, 14), l=19970.0)          # PDL
    set_bar(bars, _et(2026, 9, 9, 5), l=19960.0)           # ONL
    set_bar(bars, _et(2026, 9, 9, 9, 30), h=20020.0, l=19980.0)
    return bars


def test_anchor_is_nearest_supportive_to_entry_with_tie_precedence():
    bars = _day_with_levels()
    ls = slf.build_levels(bars, INST, b0_ts=_et(2026, 9, 9, 11))
    a = slf.select_anchor(ls, slf.MAJOR, "strat", entry=19985.0, sign=+1)
    assert a.name == "NY_ORB_L"                           # 5 pts away; PDL 15, ONL 25
    # own-level exclusion: an orb family cannot anchor on its own range
    a2 = slf.select_anchor(ls, slf.MAJOR, "orb", entry=19985.0, sign=+1)
    assert a2.name == "PDL"
    # tie: PDL and NY_ORB_L both 10 away → higher timeframe (PDL) wins
    a3 = slf.select_anchor(ls, slf.MAJOR, "strat", entry=19975.0, sign=+1)
    assert a3.name == "PDL"


def test_labels_h1_h3_h5_h6_on_synthetic_candidate():
    bars = _day_with_levels()
    b0 = _et(2026, 9, 9, 11)
    # B0 wicks through the NY ORB low and closes back above it
    set_bar(bars, b0, o=19990.0, h=19992.0, l=19978.0, c=19990.0)
    # MTR15 on the ±1 filler bars is 2.0 → proximity/relevance band 1.0 pt, D_max 2.0 pts
    res = slf.label_candidate(bars, INST, direction="LONG", entry=19980.75, stop=19975.0, target=19995.0,
                              strategy="strat_212", b0_ts=b0)
    h = res["hypotheses"]
    assert h["H1"] == {"label": "T", "anchor": "NY_ORB_L"}
    assert h["H3"] == {"label": "T", "anchor": "NY_ORB_L"}
    assert h["H4"]["label"] == "APPLICABLE" and h["H4"]["anchor"] == "NY_ORB_L"
    # the synthetic day also carries a 4H LC_ZONE spanning the ORB low (E8 counts any admitted
    # level whose band intersects the anchor, not only supportive ones) → cluster 2 → T
    assert h["H5"]["anchor"] == "NY_ORB_L" and h["H5"]["cluster"] == 2 and h["H5"]["label"] == "T"
    assert h["H6"]["label"] == "T" and h["H6"]["opposing"] == "PDH"           # nearest opposing = PDH 20001 (filler high); target 19995 before it
    # target beyond the opposing level → F
    res2 = slf.label_candidate(bars, INST, direction="LONG", entry=19980.75, stop=19975.0, target=20080.0,
                               strategy="strat_212", b0_ts=b0)
    assert res2["hypotheses"]["H6"]["label"] == "F"
    # H2: no eligible broken level → NOT_APPLICABLE
    assert h["H2"]["label"] == "NOT_APPLICABLE"


def test_h5_anchor_beyond_relevance_band_is_not_applicable():
    bars = _day_with_levels()
    b0 = _et(2026, 9, 9, 11)
    ls = slf.build_levels(bars, INST, b0_ts=b0)
    far_entry = 19985.0 + 10 * (ls.mtr15 or 1.0) + 50
    res = slf.label_candidate(bars, INST, direction="LONG", entry=far_entry, stop=far_entry - 10, target=far_entry + 20,
                              strategy="strat_212", b0_ts=b0)
    assert res["hypotheses"]["H5"]["label"] == "NOT_APPLICABLE"
    assert res["hypotheses"]["H5"]["reason"] == "anchor beyond relevance band"


def test_h2_labels_t_and_f_are_age_matched_states():
    bars = _day_with_levels()
    # PDL = 19970 was broken DOWNWARD earlier... use an upward break of PDH for a LONG instead:
    set_bar(bars, _et(2026, 9, 8, 13), h=20050.0)          # PDH 20050
    # Wed 10:00 close above PDH (break), holds, retest at 11:00 (age 4)
    set_bar(bars, _et(2026, 9, 9, 10, 0), o=20040.0, h=20060.0, l=20035.0, c=20055.0)
    for hh, mm in ((10, 15), (10, 30), (10, 45)):
        set_bar(bars, _et(2026, 9, 9, hh, mm), o=20056.0, h=20062.0, l=20054.0, c=20058.0)
    b0 = _et(2026, 9, 9, 11)
    set_bar(bars, b0, o=20058.0, h=20060.0, l=20050.5, c=20056.0)     # retest within τ, holds
    ls = slf.build_levels(bars, INST, b0_ts=b0)
    tau = slf.TAU_MTR * ls.mtr15
    assert 20050.5 <= 20050.0 + tau, "synthetic retest must sit within τ"
    res = slf.label_candidate(bars, INST, direction="LONG", entry=20057.0, stop=20045.0, target=20080.0,
                              strategy="strat_212", b0_ts=b0)
    assert res["hypotheses"]["H2"] == {"label": "T", "anchor": "PDH", "break_age": 4}
    set_bar(bars, b0, o=20058.0, h=20062.0, l=20056.0, c=20059.0)     # no retest yet → F, same age
    res2 = slf.label_candidate(bars, INST, direction="LONG", entry=20057.0, stop=20045.0, target=20080.0,
                               strategy="strat_212", b0_ts=b0)
    assert res2["hypotheses"]["H2"] == {"label": "F", "anchor": "PDH", "break_age": 4}


def test_module_has_no_runtime_imports():
    import sys
    banned = ("webhook", "strategy", "execution", "journal", "replay", "broker")
    mods = {m.split(".")[0] for m in sys.modules if m.startswith(banned)}
    # the test file itself imports webhook.state_builder for the session parity check; the
    # feature module's own import list is what matters:
    src = open(slf.__file__).read()
    for b in banned:
        assert f"from {b}" not in src and f"import {b}" not in src, b
