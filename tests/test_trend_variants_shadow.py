"""Synthetic-bar tests for paper/shadow CONTROL_R3_v2, V-A, V-B, and V-C.

No network, no broker, and no MNQ bars dated 2026-06-29 or later.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from research.trend_r3_forward import trend_variants_shadow as mod

EXPECTED_HASHES = {
    "trend_shadow.py": "92a666eaa2b57e148f345ae9e0b7e6dbf3c58bb4fab48852960e3c79c3d37973",
    "shadow_daily.py": "b20d29f22a6701d1f9f27388ce431d4b6f36d62f68cd2059826aff15e265b737",
    "FROZEN_RULES_R3.md": "5b00285c5d973c183a453092a3137b4a53229a20d2783d49ed5c1fb6b53332af",
    "va_predict.py": "6afeac36129f887455c4b1bdf178754e7a3c79209693b82ffac10f6374264950",
    "vc_predict.py": "a450da2bdb00e53f3ef40fb849192d48d204c7aff25af452ca212cae38df285f",
}
_FROZEN_DIR = Path(mod.__file__).resolve().parent


def _frame(close, start="2026-10-13", half=8.0, low=None, high=None, open_=None, roll_at=()):
    close = np.asarray(close, dtype=float)
    n = len(close)
    index = pd.date_range(start, periods=n, freq="D")
    if open_ is None:
        open_ = np.r_[close[0], close[:-1]]
    else:
        open_ = np.asarray(open_, dtype=float)
    if high is None:
        high = np.maximum(open_, close) + half
    else:
        high = np.asarray(high, dtype=float)
    if low is None:
        low = np.minimum(open_, close) - half
    else:
        low = np.asarray(low, dtype=float)
    roll = np.zeros(n, dtype=bool)
    for i in roll_at:
        roll[i] = True
    return pd.DataFrame(
        {
            "o": open_,
            "h": high,
            "l": low,
            "c": close,
            "roll": roll,
            "tk": "MNQZ6",
            "adj": 0.0,
            "ro": open_,
            "rc": close,
            "diff": 0.0,
        },
        index=index,
    )


def _closes_for_signs(signs, base=100.0, step=10.0):
    close = np.full(len(signs), base, dtype=float)
    for i, sign in enumerate(signs):
        if i < 100 or sign is None:
            continue
        ref = close[i - 100]
        if sign > 0:
            close[i] = ref + step
        elif sign < 0:
            close[i] = ref - step
        else:
            close[i] = ref
    return close


def _date(start, i):
    return pd.date_range(start, periods=i + 1, freq="D")[i]


def _trade_core(trade):
    keys = (
        "root",
        "entry",
        "exit",
        "side",
        "net",
        "why",
        "inherited",
        "entry_raw",
        "entry_tk",
        "exit_raw",
        "exit_tk",
        "rolls",
    )
    return tuple(trade[key] for key in keys)


def test_frozen_hashes():
    for name, digest in EXPECTED_HASHES.items():
        path = _FROZEN_DIR / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert mod.verify_frozen_hashes() is None
    for path, digest in mod.FROZEN_HASHES.items():
        assert path.name in EXPECTED_HASHES
        assert digest == EXPECTED_HASHES[path.name]


def test_hash_mismatch_exits_2(tmp_path, capsys):
    bad = tmp_path / "trend_shadow.py"
    bad.write_bytes(b"not-the-frozen-file")
    with pytest.raises(SystemExit) as exc:
        mod.verify_frozen_hashes({bad: "0" * 64})
    assert exc.value.code == 2
    assert "HASH_MISMATCH" in capsys.readouterr().out


def test_alpha_is_0167_for_each_variant():
    assert mod.EVAL_ALPHA == 0.0167
    for arm in ("V-A", "V-B", "V-C"):
        assert mod.EVAL_ALPHA_BY_ARM[arm] == 0.0167
    assert mod.EVAL_PERMUTATIONS == 10_000
    assert "0.0167" in mod.__doc__
    assert "0.025" not in mod.__doc__
    assert "UNDERPOWERED" in mod.__doc__
    assert "PROMISING BUT UNPROVEN" in mod.__doc__
    assert "INCONCLUSIVE" in mod.__doc__
    assert "433" in mod.__doc__


def test_driver_is_not_activated_and_does_not_touch_r3_log(tmp_path, monkeypatch, capsys):
    assert mod.VARIANTS_START is None
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shadow_log.jsonl").write_text("ORIGINAL\n")
    assert mod.main() == 0
    assert "NOT_ACTIVATED" in capsys.readouterr().out
    assert not (tmp_path / "shadow_variants_log.jsonl").exists()
    assert (tmp_path / "shadow_log.jsonl").read_text() == "ORIGINAL\n"
    assert mod.cont is mod.trend_shadow.cont
    assert mod.run is mod.trend_shadow.run


def test_va_single_bar_change_does_not_flip_two_bars_do_and_init_is_flat():
    start = "2026-01-01"
    signs = [None] * 100
    signs += [1] * 8  # 100-107 confirm and hold long
    signs += [-1]  # 108 single-bar change
    signs += [1] * 4  # 109-112 back to +1, c stays long
    signs += [-1, -1]  # 113-114 confirmed flip
    signs += [1]
    close = _closes_for_signs(signs)
    frame = _frame(close, start=start, half=5.0)
    campaign_start = _date(start, 99)
    # Bars 108 and 109 disagree, so forward init is flat until the next bar confirms.
    signs_b = [None] * 100 + [1] * 9 + [-1] + [-1] + [0]
    # indices: 100-108 +1, 109 -1, 110 -1
    close_b = _closes_for_signs(signs_b)
    frame_b = _frame(close_b, start=start, half=5.0)
    init_start = _date(start, 109)
    result_b = mod.run_arm(frame_b, "MNQ", mod.ARM_VA, start=init_start)
    entries_b = [event for event in result_b["events"] if event["kind"] == "entry"]
    assert entries_b
    assert entries_b[0]["side"] == -1
    assert pd.Timestamp(entries_b[0]["date"]) == _date(start, 111)
    assert all(pd.Timestamp(event["date"]) >= _date(start, 111) for event in entries_b)

    result = mod.run_arm(frame, "MNQ", mod.ARM_VA, start=campaign_start)
    exits_before_confirm = [
        trade
        for trade in result["trades"]
        if trade["why"] == "flip" and pd.Timestamp(trade["exit"]) <= _date(start, 114)
    ]
    assert exits_before_confirm == []
    flip = next(trade for trade in result["trades"] if trade["why"] == "flip")
    assert pd.Timestamp(flip["exit"]) == _date(start, 115)
    assert flip["side"] == 1
    # The lone +1 after that flip does not undo the confirmed short.
    assert result["state"]["pos"] == -1


def test_va_init_uses_agreed_sign():
    start = "2026-01-01"
    signs = [None] * 100 + [1] * 12
    frame = _frame(_closes_for_signs(signs), start=start, half=5.0)
    result = mod.run_arm(frame, "MNQ", mod.ARM_VA, start=_date(start, 109))
    entries = [event for event in result["events"] if event["kind"] == "entry"]
    assert entries[0]["side"] == 1
    assert pd.Timestamp(entries[0]["date"]) == _date(start, 111)


def test_va_stopped_flat_until_c_flips():
    start = "2026-01-01"
    signs = [None] * 100 + [1] * 12 + [-1, -1, -1]
    close = _closes_for_signs(signs, base=200.0, step=10.0)
    low = np.minimum(np.r_[close[0], close[:-1]], close) - 5.0
    low[106] = 0.0  # crash while c is still +1
    frame = _frame(close, start=start, half=5.0, low=low)
    result = mod.run_arm(frame, "MNQ", mod.ARM_VA, start=_date(start, 99))
    assert result["trades"][0]["why"] == "stop"
    assert result["trades"][0]["side"] == 1
    stop_day = pd.Timestamp(result["trades"][0]["exit"])
    later_longs = [
        event
        for event in result["events"]
        if event["kind"] == "entry" and event["side"] == 1 and pd.Timestamp(event["date"]) > stop_day
    ]
    assert later_longs == []
    shorts = [event for event in result["events"] if event["kind"] == "entry" and event["side"] == -1]
    assert shorts
    assert pd.Timestamp(shorts[0]["date"]) == _date(start, 114)


def test_vb_cap_binds_then_trail_and_atr_cannot_loosen_past_cap():
    point_value = 2.0
    fill = 100.5
    wide, frozen_wide, cap = mod.stop_components(mod.ARM_VB, 1, 100.0, 100.0, fill, point_value)
    assert frozen_wide < cap
    assert wide == pytest.approx(cap)
    assert cap == pytest.approx(fill - 150.0)
    short_stop, short_frozen, short_cap = mod.stop_components(
        mod.ARM_VB, -1, 100.0, 100.0, 99.5, point_value
    )
    assert short_frozen > short_cap
    assert short_stop == pytest.approx(short_cap)
    trail, frozen_tight, cap_again = mod.stop_components(
        mod.ARM_VB, 1, 400.0, 100.0, fill, point_value
    )
    assert trail == pytest.approx(frozen_tight)
    assert trail > cap_again
    loosened, frozen_loose, cap_floor = mod.stop_components(
        mod.ARM_VB, 1, 400.0, 200.0, fill, point_value
    )
    assert frozen_loose < cap_floor
    assert loosened == pytest.approx(cap_floor)
    for root, points in (("MNQ", 150.0), ("MES", 60.0), ("M2K", 60.0), ("MGC", 30.0), ("MCL", 3.0)):
        assert mod.cap_points(root) == pytest.approx(points)


def test_vb_entry_stop_gap_worst_loss_and_roll_shift():
    start = "2026-10-13"
    n = 130
    close = np.full(n, 200.0)
    close[100:] = 210.0
    open_ = np.r_[close[0], close[:-1]]
    low = np.minimum(open_, close) - 80.0
    high = np.maximum(open_, close) + 80.0
    # No-gap entry-bar stop at the cap. Open stays above the cap.
    low = low.copy()
    low[101] = 60.5
    frame = _frame(close, start=start, low=low, high=high, open_=open_)
    stopped = mod.run_arm(frame, "MNQ", mod.ARM_VB, start=_date(start, 99))
    assert stopped["trades"][0]["why"] == "stop"
    assert stopped["trades"][0]["exit_px"] == pytest.approx(60.5)
    assert stopped["trades"][0]["net"] == pytest.approx(-303.96)
    assert abs(stopped["trades"][0]["net"] + 304) < 1

    low_hold = np.minimum(open_, close) - 10.0
    high_hold = np.maximum(open_, close) + 80.0
    open_gap = open_.copy()
    open_gap[102] = 40.0
    low_gap = low_hold.copy()
    low_gap[102] = 30.0
    high_gap = high_hold.copy()
    high_gap[102] = 45.0
    gapped = mod.run_arm(
        _frame(close, start=start, low=low_gap, high=high_gap, open_=open_gap),
        "MNQ",
        mod.ARM_VB,
        start=_date(start, 99),
    )
    assert gapped["trades"][0]["why"] == "stop"
    assert gapped["trades"][0]["exit_px"] == pytest.approx(40.0)

    # Cap is stored on the adjusted fill, so a +50 roll adjustment shifts it by 50.
    diff = 50.0
    raw_open = open_.copy()
    adj_open = raw_open.copy()
    adj_open[:102] += diff
    adj_close = close.copy()
    adj_close[:102] += diff
    held = mod.run_arm(
        _frame(adj_close, start=start, low=low_hold + diff, high=high_hold + diff, open_=adj_open, roll_at=(102,)),
        "MNQ",
        mod.ARM_VB,
        start=_date(start, 99),
    )
    entry = next(event for event in held["events"] if event["kind"] == "entry")
    raw_fill = raw_open[101] + 0.5
    assert entry["cap_px"] == pytest.approx((raw_fill + diff) - 150.0)
    assert entry["cap_px"] - (raw_fill - 150.0) == pytest.approx(diff)
    assert any(event["kind"] == "roll" for event in held["events"])
    assert held["stops"][0]["stop"] == pytest.approx(entry["cap_px"])


def test_control_matches_frozen_run_on_a_fresh_start():
    start = "2026-10-13"
    n = 160
    close = np.full(n, 100.0)
    close[100:130] = 120.0
    close[130:] = 80.0
    low = np.minimum(np.r_[close[0], close[:-1]], close) - 8.0
    low = low.copy()
    low[120] = 0.0
    frame = _frame(close, start=start, low=low, roll_at=(110,))
    frozen = mod.control_fresh_trades(frame, "MNQ")
    ours = mod.run_arm(frame, "MNQ", mod.ARM_CONTROL, start=None)
    assert frozen
    assert all(not trade["inherited"] for trade in frozen)
    assert all(pd.Timestamp(trade["entry"]) >= pd.Timestamp("2026-10-12") for trade in frozen)
    assert [_trade_core(trade) for trade in ours["trades"]] == [_trade_core(trade) for trade in frozen]


def test_logging_dedup_leaves_shadow_log_untouched(tmp_path):
    shadow = tmp_path / "shadow_log.jsonl"
    shadow.write_text("ORIGINAL\n")
    variants = tmp_path / "shadow_variants_log.jsonl"
    record = {
        "key": "V-A|entry|MNQ|2026-11-06",
        "rule": "V-A",
        "type": "entry",
        "instrument": "MNQ",
    }
    other = dict(record, key="V-C|entry|MNQ|2026-11-06", rule="V-C")
    assert len(mod.append_log(variants, [record, other])) == 2
    assert mod.append_log(variants, [record]) == []
    lines = [line for line in variants.read_text().splitlines() if line]
    assert len(lines) == 2
    assert shadow.read_text() == "ORIGINAL\n"
    with pytest.raises(RuntimeError):
        mod.append_log(shadow, [record])
    assert shadow.read_text() == "ORIGINAL\n"


def test_monthly_keeps_zeros_uses_partial_baseline_and_roll_month():
    start = pd.Timestamp("2026-11-15")
    marks = {
        pd.Period("2026-11", freq="M"): -10.0,
        pd.Period("2026-12", freq="M"): -14.96,
        pd.Period("2027-02", freq="M"): 0.0,
    }
    closed = {pd.Period("2027-02", freq="M"): -4.0}
    monthly = mod.monthly_instrument_pnl(start, marks, closed)
    assert pd.Period("2026-11", freq="M") not in monthly
    assert monthly[pd.Period("2026-12", freq="M")] == pytest.approx(-4.96)
    assert monthly[pd.Period("2027-01", freq="M")] == pytest.approx(0.0)
    assert monthly[pd.Period("2027-02", freq="M")] == pytest.approx(-4.0 + (0.0 - (-14.96)))

    # Engine: flat adjusted prices, one roll in December, so that month carries the roll cost.
    series_start = "2026-07-24"
    n = 220
    close = np.full(n, 100.0)
    close[100:] = 110.0
    index = pd.date_range(series_start, periods=n, freq="D")
    december = np.where((index.month == 12) & (index.year == 2026))[0]
    roll_at = int(december[10])
    frame = _frame(close, start=series_start, half=80.0, roll_at=(roll_at,))
    campaign_start = pd.Timestamp("2026-11-01")
    result = mod.run_arm(frame, "MNQ", mod.ARM_CONTROL, start=campaign_start)
    assert result["state"]["pos"] == 1
    monthly_engine = mod.monthly_instrument_pnl(
        campaign_start, mod.month_end_marks(result["bars"]), mod.closed_by_month(result["trades"])
    )
    point_value, tick = mod.trend_shadow.SPEC["MNQ"]
    roll_cost = mod.trend_shadow.COM * 2 + 2 * (2 * tick) * point_value
    assert monthly_engine[pd.Period("2026-12", freq="M")] == pytest.approx(-roll_cost)
    assert monthly_engine[pd.Period("2027-01", freq="M")] == pytest.approx(0.0)
    variant = dict(monthly_engine)
    control = {month: 0.0 for month in monthly_engine}
    diff = mod.monthly_difference(variant, control)
    assert diff[pd.Period("2026-12", freq="M")] == pytest.approx(-roll_cost)


def test_kill_stops_on_synthetic_losses_do_not_stop_control():
    start = pd.Timestamp("2026-11-06")
    days = [start + pd.Timedelta(days=i) for i in range(20)]
    trades = {"MNQ": [{"exit": day, "net": -150.0, "entry": day} for day in days]}
    bars = {"MNQ": [{"date": day, "mark": 0.0} for day in days]}
    kill_day, reason = mod.scan_kills(bars, trades, start)
    assert reason == "closed_pnl"
    assert kill_day == days[-1]

    dd_day, dd_reason = mod.scan_kills(
        {"MNQ": [{"date": start, "mark": 0.0}]},
        {"MNQ": [{"exit": start, "net": -10_400.01, "entry": start}]},
        start,
    )
    assert dd_reason == "max_dd"
    assert dd_day == start

    horizon = start + pd.Timedelta(weeks=52)
    week_day, week_reason = mod.scan_kills(
        {"MNQ": [{"date": start, "mark": 0.0}, {"date": horizon, "mark": 0.0}]},
        {"MNQ": []},
        start,
    )
    assert week_reason == "weeks_52"
    assert week_day == horizon

    series_start = "2026-10-13"
    n = 140
    close = np.full(n, 200.0)
    close[100:104] = 210.0
    close[104:] = 190.0
    open_ = np.r_[close[0], close[:-1]].copy()
    open_[102] = 200.0 - 8_000.0
    low = np.minimum(open_, close) - 5.0
    high = np.maximum(open_, close) + 5.0
    frame = _frame(close, start=series_start, low=low, high=high, open_=open_)
    campaign = mod.run_campaign({"MNQ": frame}, start=_date(series_start, 99))
    assert "V-C" in campaign["kills"]
    assert "max_dd" in campaign["kills"]["V-C"]["reason"]
    assert mod.ARM_CONTROL not in campaign["kills"]
    control_entries = [
        event["date"]
        for event in campaign["arms"][mod.ARM_CONTROL]["MNQ"]["events"]
        if event["kind"] == "entry"
    ]
    variant_entries = [
        event["date"]
        for event in campaign["arms"][mod.ARM_VC]["MNQ"]["events"]
        if event["kind"] == "entry"
    ]
    assert len(control_entries) >= 2
    assert variant_entries == [control_entries[0]]
    summary = mod.render_summary(campaign, "2026-11-05T12:00:00-05:00")
    for arm in mod.ARMS:
        assert arm in summary
    assert "killed" in summary
    assert "not edge evidence" in summary


def test_vc_never_loosens_and_giveback_timing_gap_and_flat_until_flip():
    point_value = 2.0
    for side, ext, fill, atr in (
        (1, 100.0, 100.5, 10.0),
        (1, 130.0, 100.5, 50.0),
        (1, 90.0, 100.5, 5.0),
        (-1, 100.0, 99.5, 10.0),
        (-1, 70.0, 99.5, 40.0),
        (-1, 120.0, 99.5, 8.0),
    ):
        stop, frozen, _extra = mod.stop_components(mod.ARM_VC, side, ext, atr, fill, point_value)
        if side > 0:
            assert stop >= frozen - 1e-9
        else:
            assert stop <= frozen + 1e-9
        favourable = ext > fill if side > 0 else ext < fill
        if favourable:
            giveback = fill + 0.5 * (ext - fill)
            expected = max(frozen, giveback) if side > 0 else min(frozen, giveback)
        else:
            expected = frozen
        assert stop == pytest.approx(expected)

    start = "2026-10-13"
    n = 130
    close = np.full(n, 200.0)
    close[100:] = 210.0
    open_ = np.r_[close[0], close[:-1]].copy()
    low = np.minimum(open_, close) - 80.0
    high = np.maximum(open_, close) + 80.0
    # Entry bar 101. Favourable close on 102 must not stop even if its low
    # would hit a giveback computed from that same close. Bar 103 hits giveback.
    close = close.copy()
    close[102] = 230.0
    open_[102] = 210.0
    low = low.copy()
    low[101] = 200.0
    low[102] = 180.0
    low[103] = 200.0
    open_[103] = 230.0
    high = high.copy()
    high[103] = 235.0
    long_frame = _frame(close, start=start, low=low, high=high, open_=open_)
    long_result = mod.run_arm(long_frame, "MNQ", mod.ARM_VC, start=_date(start, 99))
    assert long_result["trades"][0]["why"] == "stop"
    assert str(pd.Timestamp(long_result["trades"][0]["exit"]).date()) == str(_date(start, 103).date())
    entry_fill = open_[101] + 0.5
    giveback = entry_fill + 0.5 * (230.0 - entry_fill)
    assert long_result["trades"][0]["exit_px"] == pytest.approx(giveback)
    assert all(row["stop"] + 1e-9 >= row["frozen"] for row in long_result["stops"] if row["side"] > 0)

    open_gap = open_.copy()
    open_gap[103] = giveback - 15.0
    low_gap = low.copy()
    low_gap[103] = giveback - 20.0
    gap = mod.run_arm(
        _frame(close, start=start, low=low_gap, high=high, open_=open_gap),
        "MNQ",
        mod.ARM_VC,
        start=_date(start, 99),
    )
    assert gap["trades"][0]["exit_px"] == pytest.approx(open_gap[103])

    # After the stop, the same sign does not re-enter; the opposite sign does.
    close_flat = close.copy()
    close_flat[104:] = 190.0
    flat = mod.run_arm(
        _frame(close_flat, start=start, low=low, high=high, open_=open_),
        "MNQ",
        mod.ARM_VC,
        start=_date(start, 99),
    )
    stop_day = pd.Timestamp(flat["trades"][0]["exit"])
    follow = [
        event
        for event in flat["events"]
        if event["kind"] == "entry" and pd.Timestamp(event["date"]) > stop_day
    ]
    assert follow
    assert follow[0]["side"] == -1
    assert not any(event["side"] == 1 for event in follow)
