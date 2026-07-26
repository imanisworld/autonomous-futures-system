from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from research.replay_12hr_miyagi_honest_fill import (
    DAY_ONLY_EXIT_REASON,
    EOD_BAR_MISSING,
    TRIGGER_NOT_HIT,
    _metrics,
    recover_entry,
    replay_signal,
    run_replay,
)


ET = ZoneInfo("America/New_York")
DAY = date(2026, 1, 8)


def bar(hour, minute, *, o, h, low, c, day=DAY):
    return {
        "ts": datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET),
        "open": o,
        "high": h,
        "low": low,
        "close": c,
    }


def signal(direction="LONG", *, instrument="MNQ", day=DAY, trigger=100.0, stop=None, target=None):
    if direction == "LONG":
        stop = 90.0 if stop is None else stop
        target = 110.0 if target is None else target
    else:
        stop = 110.0 if stop is None else stop
        target = 90.0 if target is None else target
    return {
        "date": day,
        "instrument": instrument,
        "direction": direction,
        "entry_trigger": trigger,
        "stop": stop,
        "target": target,
        "target_2": target + 10 if direction == "LONG" else target - 10,
    }


def test_recovers_first_bar_that_crosses_trigger_long():
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),   # below trigger, no cross
        bar(9, 35, o=97, h=101, low=96, c=100),  # crosses 100 on the high
        bar(9, 40, o=100, h=105, low=99, c=104),
    ]
    entry = recover_entry(signal("LONG"), bars)
    assert entry["bar"]["ts"].minute == 35


def test_recovers_first_bar_that_crosses_trigger_short():
    bars = [
        bar(9, 30, o=105, h=108, low=103, c=106),
        bar(9, 35, o=106, h=107, low=99, c=101),  # low dips to/through 100
    ]
    entry = recover_entry(signal("SHORT"), bars)
    assert entry["bar"]["ts"].minute == 35


def test_trigger_never_hit_is_no_fill():
    bars = [bar(9, 30, o=95, h=98, low=93, c=97)]
    row = replay_signal(signal("LONG"), bars)
    assert row["filled"] is False
    assert row["result"] == "NO_FILL"
    assert row["exit_reason"] == TRIGGER_NOT_HIT
    assert row["net_pnl"] == 0.0


def test_fill_price_is_exact_trigger_plus_adverse_slippage_no_ioc_cap():
    """No IOC tolerance for Miyagi -- fills at trigger +/- slippage even when
    the crossing bar's range extends far beyond the trigger."""
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=250, low=97, c=200),  # huge range, still fills at trigger
        bar(9, 40, o=200, h=210, low=199, c=205),
        bar(15, 55, o=205, h=206, low=204, c=205),
    ]
    row = replay_signal(signal("LONG"), bars, slippage_ticks=2)
    assert row["filled"] is True
    assert row["base_entry_price"] == 100.0
    assert row["fill_entry_price"] == 100.5  # +2 ticks * 0.25


def test_entry_bar_itself_never_resolves_its_own_bracket():
    """Entry bar's high reaches the target within the same bar, but that
    must not count -- only bars strictly after the entry bar can resolve."""
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=111, low=97, c=108),  # crosses trigger AND reaches target(110) same bar
        bar(9, 40, o=108, h=109, low=95, c=96),  # dips through stop(90)? no; but shows next-bar used
        bar(15, 55, o=96, h=97, low=91, c=93),   # eventually hits stop region at EOD bar
    ]
    row = replay_signal(signal("LONG"), bars)
    # Target hit within the entry bar itself must be ignored -- no stop/target
    # is reached on any LATER bar either, so this resolves via day-only-flatten.
    assert row["exit_reason"] == DAY_ONLY_EXIT_REASON
    assert row["exit_bar_ts"] == bars[-1]["ts"].isoformat()


def test_stop_wins_on_same_bar_stop_and_target_ambiguity():
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=101, low=97, c=100),  # entry bar
        bar(9, 40, o=100, h=115, low=85, c=90),  # both stop(90) and target(110) touched
    ]
    row = replay_signal(signal("LONG"), bars)
    assert row["exit_reason"] == "STOP"


def test_post_fill_wrong_side_stop_fails_closed():
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=101, low=97, c=100),
    ]
    row = replay_signal(signal("LONG", stop=101.0), bars)
    assert row["filled"] is False
    assert row["exit_reason"] == "POST_FILL_INVALID_STOP"
    assert row["net_pnl"] == 0.0


def test_day_only_flatten_at_exact_1555_bar_close():
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=101, low=97, c=100),
        bar(15, 55, o=104, h=105, low=103, c=104),
    ]
    row = replay_signal(signal("LONG"), bars, slippage_ticks=1)
    assert row["exit_reason"] == DAY_ONLY_EXIT_REASON
    assert row["base_exit_price"] == 104
    assert row["net_pnl"] is not None


def test_eod_bar_missing_when_feed_ends_before_1555():
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=101, low=97, c=100),
        bar(15, 40, o=100, h=101, low=99, c=100),
    ]
    row = replay_signal(signal("LONG"), bars, slippage_ticks=1)
    assert row["exit_reason"] == EOD_BAR_MISSING
    assert row["result"] == "UNRESOLVED"
    assert row["net_pnl"] is None
    assert row["filled"] is True


def test_entry_on_the_exact_1555_bar_resolves_same_bar_per_day_only_contract():
    """Documented carve-out: if the trigger is hit for the very first time on
    the 15:55 bar itself, that same bar's stop/target take precedence over
    (but can still resolve into) the day-only flatten."""
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(15, 55, o=98, h=112, low=97, c=111),  # crosses trigger(100) and hits target(110)
    ]
    row = replay_signal(signal("LONG"), bars)
    assert row["filled"] is True
    assert row["exit_reason"] == "TARGET"
    assert row["exit_bar_ts"] == bars[-1]["ts"].isoformat()


def test_entry_on_exact_1555_bar_with_no_stop_or_target_hit_day_only_flattens():
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(15, 55, o=98, h=101, low=97, c=99),  # crosses trigger(100), no stop/target hit
    ]
    row = replay_signal(signal("LONG"), bars)
    assert row["exit_reason"] == DAY_ONLY_EXIT_REASON
    assert row["base_exit_price"] == 99  # that bar's own close


def test_instrument_specific_point_value_mnq_vs_mes():
    bars_common = [
        bar(9, 30, o=95, h=98, low=93, c=97),
        bar(9, 35, o=98, h=101, low=97, c=100),
        bar(9, 40, o=100, h=111, low=99, c=110),
    ]
    mnq_row = replay_signal(signal("LONG", instrument="MNQ"), bars_common, slippage_ticks=0)
    mes_row = replay_signal(signal("LONG", instrument="MES"), bars_common, slippage_ticks=0)
    # gross_pnl = (target - trigger) * point_value = 10 * point_value
    assert mnq_row["gross_pnl"] == pytest.approx(20.0)  # 10 * 2.0
    assert mes_row["gross_pnl"] == pytest.approx(50.0)  # 10 * 5.0


def test_short_direction_gross_pnl_sign_and_commission_deducted():
    bars = [
        bar(9, 30, o=105, h=108, low=103, c=106),
        bar(9, 35, o=106, h=107, low=99, c=101),  # crosses trigger(100) short
        bar(9, 40, o=101, h=102, low=89, c=90),  # hits target(90)
    ]
    row = replay_signal(signal("SHORT"), bars, slippage_ticks=2)
    assert row["gross_pnl"] == pytest.approx(20.0)  # (100-90)*2.0
    assert row["commission"] == 1.24
    assert row["net_pnl"] == pytest.approx(row["gross_pnl"] - row["slippage_cost"] - 1.24)


def test_calendar_halves_and_direction_split_reported():
    d1 = date(2025, 1, 2)
    d2 = date(2025, 1, 8)
    signals = [signal("LONG", day=d1), signal("SHORT", day=d2)]
    bars = [
        bar(9, 30, o=95, h=98, low=93, c=97, day=d1),
        bar(9, 35, o=98, h=101, low=97, c=100, day=d1),
        bar(9, 40, o=100, h=111, low=99, c=110, day=d1),
        bar(9, 30, o=105, h=108, low=103, c=106, day=d2),
        bar(9, 35, o=106, h=107, low=99, c=101, day=d2),
        bar(9, 40, o=101, h=102, low=89, c=90, day=d2),
    ]
    report = run_replay(signals, bars, study_start=date(2025, 1, 1), study_end=date(2025, 1, 9))
    assert report["halves"]["H1"]["n"] == 1
    assert report["halves"]["H2"]["n"] == 1
    assert report["directions"]["LONG"]["n"] == 1
    assert report["directions"]["SHORT"]["n"] == 1


def test_metrics_excludes_eod_bar_missing_from_win_loss_but_counts_as_fill():
    win_row = replay_signal(
        signal("LONG"),
        [
            bar(9, 30, o=95, h=98, low=93, c=97),
            bar(9, 35, o=98, h=101, low=97, c=100),
            bar(9, 40, o=100, h=111, low=99, c=110),
        ],
        slippage_ticks=2,
    )
    unresolved_row = replay_signal(
        signal("LONG", day=date(2025, 1, 3)),
        [
            bar(9, 30, o=95, h=98, low=93, c=97, day=date(2025, 1, 3)),
            bar(9, 35, o=98, h=101, low=97, c=100, day=date(2025, 1, 3)),
            bar(15, 40, o=100, h=101, low=99, c=100, day=date(2025, 1, 3)),
        ],
        slippage_ticks=2,
    )
    metrics = _metrics([win_row, unresolved_row])
    assert metrics["n"] == 2
    assert metrics["fills"] == 2
    assert metrics["eod_bar_missing"] == 1
    assert metrics["resolved_fills"] == 1
    assert metrics["wins"] == 1
    assert metrics["win_rate"] == 1.0
    assert metrics["expectancy_per_signal"] == pytest.approx(metrics["net_pnl"] / 2)
    assert metrics["expectancy_per_fill"] == pytest.approx(metrics["net_pnl"] / 1)


def test_no_fill_rows_do_not_pollute_win_loss_denominators():
    no_fill_row = replay_signal(signal("LONG"), [bar(9, 30, o=95, h=98, low=93, c=97)])
    metrics = _metrics([no_fill_row])
    assert metrics["no_fill"] == 1
    assert metrics["fills"] == 0
    assert metrics["resolved_fills"] == 0
    assert metrics["win_rate"] is None
