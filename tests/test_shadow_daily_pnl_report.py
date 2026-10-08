"""ops/shadow_daily_pnl_report.py — one trading day of SHADOW_OUTCOME rows in
dollars. Evidence only; the report must never claim authority."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from ops import shadow_daily_pnl_report as sdp

DAY = date(2026, 9, 23)


def _row(key, inst, result, ticks, *, strategy="strat_22_continuation_observed", day="2026-09-23"):
    return {
        "type": "SHADOW_OUTCOME", "candidate_key": key, "candidate_day": day,
        "instrument": inst, "strategy": strategy,
        "shadow_outcome": {"result": result, "pnl_ticks": ticks},
    }


def _write(tmp_path, name, rows):
    (tmp_path / name).write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_reads_adjacent_utc_files_filters_by_candidate_day_and_dedupes(tmp_path):
    _write(tmp_path, "journal_2026-09-22.jsonl", [
        _row("asia1", "MNQ", "WIN", 40),                       # Asia session, prior UTC date
        _row("old", "MNQ", "WIN", 40, day="2026-09-22"),        # other trading day
        {"type": "NO_TRADE", "instrument": "MNQ"},
    ])
    _write(tmp_path, "journal_2026-09-23.jsonl", [
        _row("a", "MNQ", "LOSS", -20),
        _row("a", "MNQ", "LOSS", -20),                          # duplicate key
    ])
    with (tmp_path / "journal_2026-09-23.jsonl").open("a") as fh:
        fh.write("not json\n")                                  # corrupt line skipped
    rows = sdp.load_outcomes(tmp_path, DAY)
    assert sorted(r["candidate_key"] for r in rows) == ["a", "asia1"]


def test_dollars_costs_and_open_nofill():
    rows = [
        _row("w", "MNQ", "WIN", 40),       # +$20 gross
        _row("l", "MNQ", "LOSS", -20),     # -$10 gross
        _row("o", "MNQ", "OPEN", None),
        _row("n", "MNQ", "NO_FILL", None),
        _row("m", "MES", "WIN", 8, strategy="range_break_close"),  # +$10 gross
        _row("g", "MCL", "WIN", 30),       # gross only
    ]
    rep = sdp.build_report(rows, DAY)
    mnq = rep["by_instrument"]["MNQ"]
    assert (mnq["closed"], mnq["wins"], mnq["losses"], mnq["open"], mnq["no_fill"]) == (2, 1, 1, 1, 1)
    assert mnq["gross_usd"] == 10.0
    assert mnq["net_usd"] == round(20 - 1.98 + (-10 - 1.98), 2)       # 1.48 + 1 tick ($0.50)
    assert rep["by_instrument"]["MES"]["net_usd"] == round(10 - 1.48 - 1.25, 2)
    assert rep["by_instrument"]["MCL"]["net_usd"] is None
    assert rep["by_instrument"]["MCL"]["gross_usd"] == 30.0
    # total net covers costed markets only
    assert rep["total"]["net_usd"] == round(mnq["net_usd"] + rep["by_instrument"]["MES"]["net_usd"], 2)
    assert rep["total"]["gross_usd"] == 50.0
    assert rep["by_strategy"]["MES"]["range_break_close"]["closed"] == 1
    assert rep["authority"] == "evidence_only"


def test_digest_plain_english_and_empty_day():
    rep = sdp.build_report([_row("w", "MNQ", "WIN", 40), _row("l", "MES", "LOSS", -8, strategy="ema_pullback_trend")], DAY)
    text = sdp.format_digest(rep)
    assert "Shadow P&L" in text and "after costs" in text
    assert "Best: strat_22_continuation_observed" in text
    assert "Worst: ema_pullback_trend" in text
    for instrument in sdp.REPORT_INSTRUMENTS:
        assert instrument in text
    assert "M2K" in text and "no shadow outcomes recorded" in text
    assert "no orders" in text
    empty = sdp.format_digest(sdp.build_report([], DAY))
    assert "No shadow outcomes recorded" in empty
    for instrument in sdp.REPORT_INSTRUMENTS:
        assert instrument in empty


def test_main_writes_files(tmp_path, capsys):
    _write(tmp_path, "journal_2026-09-23.jsonl", [_row("w", "MNQ", "WIN", 40)])
    assert sdp.main(["--log-dir", str(tmp_path), "--day", "2026-09-23"]) == 0
    assert (tmp_path / "shadow_daily_pnl_2026-09-23.json").exists()
    assert json.loads((tmp_path / "shadow_daily_pnl_latest.json").read_text())["total"]["wins"] == 1
    assert "Shadow P&L" in capsys.readouterr().out


def _filled(key, inst, result, ticks, bar_ts, fill, exit_, *, direction="SHORT", strategy="ema_pullback_trend",
            resolved="2026-09-23T23:45:00+00:00"):
    row = _row(key, inst, result, ticks, strategy=strategy)
    row.update({"direction": direction, "candidate_bar_ts": bar_ts, "resolved_at_bar_ts": resolved})
    row["shadow_outcome"].update({"bars_to_fill": fill, "bars_to_exit": exit_})
    return row


def _bars(tmp_path, inst, start_hour, count):
    rows = [
        {"ts": f"2026-09-23T{start_hour + i // 4:02d}:{(i % 4) * 15:02d}:00+00:00", "high": 1.0, "low": 0.0}
        for i in range(count)
    ]
    _write(tmp_path, f"bars_{inst}_2026-09-23.jsonl", rows)


def test_stacked_trades_are_left_out_of_the_one_at_a_time_view(tmp_path):
    _bars(tmp_path, "MES", 1, 24)  # 01:00 … 06:45
    rows = [
        # held: bar 01:00, fills 01:15, exits 02:00
        _filled("a", "MES", "LOSS", -8, "2026-09-23T01:00:00+00:00", 1, 4),
        # fills 01:45 while "a" is still live → stacked
        _filled("b", "MES", "LOSS", -8, "2026-09-23T01:30:00+00:00", 1, 2),
        # fills 02:00 on "a"'s exit bar → stacked (same-bar order unknowable)
        _filled("c", "MES", "WIN", 16, "2026-09-23T01:45:00+00:00", 1, 3),
        # fills 03:15 after "a" exited → held again
        _filled("d", "MES", "WIN", 16, "2026-09-23T03:00:00+00:00", 1, 2),
        # other direction is its own setup → not stacked
        _filled("e", "MES", "LOSS", -8, "2026-09-23T01:30:00+00:00", 1, 2, direction="LONG"),
        _row("n", "MES", "NO_FILL", None),
    ]
    rep = sdp.build_report(rows, DAY, log_dir=tmp_path)
    fs = rep["first_signal"]
    assert fs["stacked"] == 2 and fs["timing_approx"] == 0
    mes = fs["by_instrument"]["MES"]
    assert (mes["closed"], mes["wins"], mes["losses"], mes["no_fill"], mes["stacked"]) == (3, 1, 2, 1, 2)
    assert rep["by_instrument"]["MES"]["closed"] == 5          # raw total untouched
    text = sdp.format_digest(rep)
    assert "Total one at a time: 3 trades" in text and "2 overlapping trades left out" in text
    assert "MES one at a time: 3 trades" in text


def test_stacking_uses_real_bar_gaps_and_falls_back_to_15m(tmp_path):
    # Feed gap: after 01:15 the next stored bar is 05:00, so "a" exits at 05:00,
    # and "b" (fills 05:00) is stacked. A naive 15m count would put a's exit at 01:30.
    _write(tmp_path, "bars_MES_2026-09-23.jsonl", [
        {"ts": "2026-09-23T01:15:00+00:00", "high": 1.0, "low": 0.0},
        {"ts": "2026-09-23T05:00:00+00:00", "high": 1.0, "low": 0.0},
    ])
    rows = [
        _filled("a", "MES", "LOSS", -8, "2026-09-23T01:00:00+00:00", 1, 2),
        _filled("b", "MES", "LOSS", -8, "2026-09-23T04:45:00+00:00", 1, 1),
    ]
    assert sdp.build_report(rows, DAY, log_dir=tmp_path)["first_signal"]["stacked"] == 1
    # No bar files: 15-minute fallback (a exits 01:30, b fills 05:00) and flagged.
    rep = sdp.build_report(rows, DAY, log_dir=tmp_path / "missing")
    assert rep["first_signal"]["stacked"] == 0 and rep["first_signal"]["timing_approx"] == 2
    assert "overlap timing estimated for 2 trades" in sdp.format_digest(rep)


def test_open_trade_blocks_the_rest_of_the_day(tmp_path):
    rows = [
        _filled("a", "MNQ", "OPEN", None, "2026-09-23T01:00:00+00:00", 1, None),
        _filled("b", "MNQ", "WIN", 40, "2026-09-23T20:00:00+00:00", 1, 2),
    ]
    rep = sdp.build_report(rows, DAY)
    assert rep["first_signal"]["stacked"] == 1
    assert rep["first_signal"]["total"]["open"] == 1 and rep["first_signal"]["total"]["closed"] == 0


def test_open_and_never_filled_always_shown():
    text = sdp.format_digest(sdp.build_report([_row("w", "MNQ", "WIN", 40)], DAY))
    assert "(0 still open, 0 never filled)" in text


def test_late_rows_up_to_resolver_lookback_are_read(tmp_path):
    _write(tmp_path, "journal_2026-09-26.jsonl", [_row("late", "MES", "NO_FILL", None)])  # +3 days: in lookback
    _write(tmp_path, "journal_2026-09-27.jsonl", [_row("never", "MES", "NO_FILL", None)])  # resolver can't write this
    assert [r["candidate_key"] for r in sdp.load_outcomes(tmp_path, DAY)] == ["late"]


def test_final_pass_compares_to_first_and_keeps_latest(tmp_path, capsys):
    _write(tmp_path, "journal_2026-09-23.jsonl", [_row("w", "MNQ", "WIN", 40)])
    assert sdp.main(["--log-dir", str(tmp_path), "--day", "2026-09-23"]) == 0
    latest = (tmp_path / "shadow_daily_pnl_latest.json").read_text()
    # Late rows land the next UTC day.
    _write(tmp_path, "journal_2026-09-24.jsonl", [
        _row("l", "MNQ", "LOSS", -20), _row("n", "MNQ", "NO_FILL", None),
    ])
    capsys.readouterr()
    assert sdp.main(["--log-dir", str(tmp_path), "--day", "2026-09-23", "--final"]) == 0
    final = json.loads((tmp_path / "shadow_daily_pnl_2026-09-23_final.json").read_text())
    assert final["pass"] == "final"
    change = final["change_since_first_pass"]
    assert (change["candidates"], change["closed"], change["losses"], change["no_fill"]) == (2, 1, 1, 1)
    assert change["gross_usd"] == -10.0
    assert (tmp_path / "shadow_daily_pnl_latest.json").read_text() == latest      # first pass untouched
    assert json.loads((tmp_path / "shadow_daily_pnl_2026-09-23.json").read_text())["pass"] == "first"
    out = capsys.readouterr().out
    assert "· final" in out and "Final pass: +1 trades" in out and "never filled 0 → 1" in out


def test_final_pass_without_first_and_rejects_today(tmp_path, capsys):
    assert sdp.main(["--log-dir", str(tmp_path), "--day", "2026-09-23", "--final"]) == 0
    assert "no first-pass report on file" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        sdp.main(["--log-dir", str(tmp_path), "--day", "2999-01-01", "--final"])


def test_open_overlapping_trades_are_not_counted_as_left_out(tmp_path):
    # Final-pass shape: a held trade, a closed overlap and an overlap still open.
    _bars(tmp_path, "MNQ", 1, 24)
    rows = [
        _filled("a", "MNQ", "LOSS", -8, "2026-09-23T01:00:00+00:00", 1, 4),
        _filled("b", "MNQ", "WIN", 16, "2026-09-23T01:15:00+00:00", 1, 2),
        _filled("c", "MNQ", "OPEN", None, "2026-09-23T01:30:00+00:00", 1, None),
    ]
    rep = sdp.build_report(rows, DAY, log_dir=tmp_path)
    fs, total = rep["first_signal"], rep["by_instrument"]["MNQ"]
    assert (fs["stacked"], fs["stacked_open"]) == (1, 1)
    mnq = fs["by_instrument"]["MNQ"]
    # kept + left out == the market's trade count (open trades are not trades yet)
    assert mnq["closed"] + mnq["stacked"] == total["closed"] == 2
    text = sdp.format_digest(rep)
    assert "MNQ one at a time: 1 trades" in text
    assert "(1 overlapping trades left out, 1 overlapping trades still open)" in text


def test_only_open_overlaps_print_no_one_at_a_time_line(tmp_path):
    # Nothing closed was left out, so the view would just repeat the total.
    _bars(tmp_path, "MNQ", 1, 24)
    rows = [
        _filled("a", "MNQ", "LOSS", -8, "2026-09-23T01:00:00+00:00", 1, 4),
        _filled("c", "MNQ", "OPEN", None, "2026-09-23T01:30:00+00:00", 1, None),
    ]
    rep = sdp.build_report(rows, DAY, log_dir=tmp_path)
    assert (rep["first_signal"]["stacked"], rep["first_signal"]["stacked_open"]) == (0, 1)
    assert "one at a time" not in sdp.format_digest(rep)


def test_one_at_a_time_lines_stay_under_their_market_on_the_card(tmp_path):
    from notifications.discord_card import text_card

    _bars(tmp_path, "MES", 1, 24)
    rows = [
        _filled("a", "MES", "LOSS", -8, "2026-09-23T01:00:00+00:00", 1, 4),
        _filled("b", "MES", "LOSS", -8, "2026-09-23T01:30:00+00:00", 1, 2),
    ]
    card = text_card(sdp.format_digest(sdp.build_report(rows, DAY, log_dir=tmp_path)))
    names = [field["name"] for field in card["fields"]]
    assert "One trade at a time" not in card.get("description", "")
    assert names.index("Total one at a time") == names.index("All markets") + 1
    assert names.index("MES one at a time") == names.index("MES (Micro S&P 500)") + 1


# ── your limits: 3 per day session + 3 per night session, one position ──────
from datetime import datetime, timezone  # noqa: E402


def _cap_row(key, inst, result, ticks, cand_ts, n_fill, n_exit, *, day="2026-09-23", **extra):
    row = _row(key, inst, result, ticks, day=day)
    row.update({"candidate_bar_ts": cand_ts, "direction": "LONG", **extra})
    row["shadow_outcome"].update({"bars_to_fill": n_fill, "bars_to_exit": n_exit})
    return row


def test_trading_session_boundaries_in_new_york_time():
    utc = timezone.utc
    assert sdp.trading_session(datetime(2026, 9, 23, 13, 29, tzinfo=utc)) == (date(2026, 9, 23), "night")  # 09:29 ET
    assert sdp.trading_session(datetime(2026, 9, 23, 13, 30, tzinfo=utc)) == (date(2026, 9, 23), "day")    # 09:30 ET
    assert sdp.trading_session(datetime(2026, 9, 23, 20, 59, tzinfo=utc)) == (date(2026, 9, 23), "day")    # 16:59 ET
    assert sdp.trading_session(datetime(2026, 9, 23, 21, 30, tzinfo=utc)) == (date(2026, 9, 23), "halt")   # 17:30 ET
    assert sdp.trading_session(datetime(2026, 9, 23, 22, 0, tzinfo=utc)) == (date(2026, 9, 24), "night")   # 18:00 ET resets


def _t(key, inst, fill_h, exit_h, net, session="day", tdate=date(2026, 9, 23)):
    base = datetime(2026, 9, 23, tzinfo=timezone.utc)
    return {"key": key, "instrument": inst, "fill": base + timedelta(hours=fill_h),
            "exit": base + timedelta(hours=exit_h), "trading_date": tdate, "session": session,
            "how": "win" if net > 0 else "loss", "net_usd": net, "won": net > 0,
            "approx_timing": False, "assumed_close": False}


def test_cap_takes_first_three_per_session_and_one_position_at_a_time():
    trades = [
        _t("a", "MNQ", 14.0, 14.5, 10), _t("b", "MES", 14.25, 14.75, 5),   # b fills while a is open
        _t("c", "MNQ", 15.0, 15.5, -4), _t("d", "MNQ", 16.0, 16.5, 7),
        _t("e", "MNQ", 17.0, 17.5, 99),                                      # 4th fill in the day session
        _t("f", "MNQ", 3.0, 3.5, -2, session="night"),                        # night has its own 3
        _t("g", "MNQ", 21.5, 21.75, 50, session="halt"),                      # 17:00-18:00 ET never counts
    ]
    trades.sort(key=lambda t: (t["fill"], t["key"]))
    assert [t["key"] for t in sdp.apply_session_cap(trades, "account")] == ["f", "a", "c", "d"]
    # each market separately: MES has its own position and its own 3
    assert [t["key"] for t in sdp.apply_session_cap(trades, "per_market")] == ["f", "a", "b", "c", "d"]


def test_same_bar_as_previous_exit_is_still_in_the_trade():
    trades = [_t("a", "MNQ", 14.0, 15.0, 1), _t("b", "MNQ", 15.0, 15.5, 1), _t("c", "MNQ", 15.25, 16.0, 1)]
    assert [t["key"] for t in sdp.apply_session_cap(trades, "account")] == ["a", "c"]


def test_cap_summary_drawdown_follows_closing_order():
    trades = [_t("a", "MNQ", 14, 18, 30), _t("b", "MNQ", 15, 16, -50), _t("c", "MNQ", 16.5, 17, -20)]
    s = sdp._cap_summary(trades)
    # closing order b(-50), c(-20), a(+30): 0 → -50 → -70 → -40, deepest drop 70
    assert (s["trades"], s["wins"], s["losses"], s["net_usd"], s["max_drawdown_usd"]) == (3, 1, 2, -40.0, 70.0)


def _bar_file(tmp_path, inst, day_iso, bars):
    _write(tmp_path, f"bars_{inst}_{day_iso}.jsonl", bars)


def _open_row(**kw):
    row = _cap_row("o", "MNQ", "OPEN", None, "2026-09-23T14:00:00+00:00", 1, None,
                  entry=100.0, stop=90.0, target=120.0, resolved_at_bar_ts="2026-09-23T15:00:00+00:00")
    row.update(kw)
    return row


def test_open_trade_is_played_out_on_stored_bars(tmp_path):
    _bar_file(tmp_path, "MNQ", "2026-09-23", [
        {"ts": "2026-09-23T14:15:00+00:00", "high": 101, "low": 99, "close": 100},
        {"ts": "2026-09-23T15:00:00+00:00", "high": 101, "low": 99, "close": 100},   # already seen by the resolver
        {"ts": "2026-09-23T16:00:00+00:00", "high": 125, "low": 89, "close": 110},   # stop and target: stop first
    ])
    got = sdp._settle_open(_open_row(), date(2026, 9, 23), tmp_path, {})
    assert got[1:] == (90.0, "stop")


def test_open_trade_without_a_hit_closes_at_5pm_et(tmp_path):
    _bar_file(tmp_path, "MNQ", "2026-09-23", [
        {"ts": "2026-09-23T16:00:00+00:00", "high": 105, "low": 95, "close": 104},
        {"ts": "2026-09-23T20:45:00+00:00", "high": 108, "low": 103, "close": 107},  # last bar before 17:00 ET
        {"ts": "2026-09-23T22:00:00+00:00", "high": 200, "low": 1, "close": 150},    # next trading date: ignored
    ])
    when, price, how = sdp._settle_open(_open_row(), date(2026, 9, 23), tmp_path, {})
    assert (price, how) == (107, "close")
    assert when.astimezone(sdp.ET).hour == 17
    trades = sdp.capped_trades([_open_row()], tmp_path)
    assert trades[0]["how"] == "close" and trades[0]["net_usd"] == round(28 * 0.5 - 1.98, 2)  # +7 pts = 28 ticks


def test_open_trade_without_bars_is_counted_but_not_priced(tmp_path):
    trades = sdp.capped_trades([_open_row()], tmp_path)
    s = sdp._cap_summary(trades)
    assert (s["trades"], s["unpriced"], s["net_usd"]) == (1, 1, None)


def test_load_outcomes_range_matches_single_days(tmp_path):
    _write(tmp_path, "journal_2026-09-23.jsonl", [_row("a", "MNQ", "WIN", 4), _row("b", "MNQ", "WIN", 4, day="2026-09-24")])
    _write(tmp_path, "journal_2026-09-25.jsonl", [_row("a", "MNQ", "LOSS", -4), _row("c", "MES", "WIN", 2, day="2026-09-25")])
    both = sdp.load_outcomes_range(tmp_path, date(2026, 9, 23), date(2026, 9, 25))
    singles = [r for d in (23, 24, 25) for r in sdp.load_outcomes(tmp_path, date(2026, 9, d))]
    key = lambda r: (r["candidate_key"], r["shadow_outcome"]["result"])  # noqa: E731
    assert sorted(map(key, both)) == sorted(map(key, singles)) == [("a", "LOSS"), ("b", "WIN"), ("c", "WIN")]


def test_report_with_log_dir_carries_your_limits_and_digest_lines(tmp_path):
    rows = [
        _cap_row("w1", "MNQ", "WIN", 40, "2026-09-23T14:00:00+00:00", 1, 2),
        _cap_row("w2", "MNQ", "WIN", 40, "2026-09-23T15:00:00+00:00", 1, 2),
        _cap_row("w3", "MNQ", "LOSS", -20, "2026-09-23T16:00:00+00:00", 1, 2),
        _cap_row("w4", "MNQ", "WIN", 40, "2026-09-23T17:00:00+00:00", 1, 2),     # 4th in the day session
    ]
    _write(tmp_path, "journal_2026-09-23.jsonl", rows)
    rep = sdp.build_report(sdp.load_outcomes(tmp_path, DAY), DAY, log_dir=tmp_path)
    today = rep["capped"]["today"]
    assert today["account"]["trades"] == 3 and today["every_signal"]["trades"] == 4
    assert today["account"]["net_usd"] == round(2 * (20 - 1.98) + (-10 - 1.98), 2)
    assert rep["capped"]["week_to_date"]["account"] == today["account"]          # Wednesday, nothing earlier
    text = sdp.format_digest(rep)
    assert "**Your limits (what-if)**" in text and "Today, whole account: 3 trades, 2 won, 1 lost" in text
    assert "it does not change what the bot trades" in text
    assert "Week, every signal, no limits: 4 trades" in text
    # DAY is the first day of the running total, so it equals today.
    assert rep["capped"]["since_start"]["account"] == today["account"]
    assert "Running total (since Wed Sep 23):" in text and "Total, whole account: 3 trades" in text
    for word in ("LONG", "SHORT", "strat_"):
        assert word not in text.split("**Your limits (what-if)**")[1]


def test_running_total_is_one_selection_sliced_into_weeks(tmp_path):
    # Wed Sep 23 .. Wed Sep 30: two calendar weeks, one selection pass.
    days = ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30"]
    for i, d in enumerate(days):
        rows = [_cap_row(f"{d}-{k}", "MNQ", "WIN" if k % 2 else "LOSS", 40 if k % 2 else -20,
                         f"{d}T{14 + k}:00:00+00:00", 1, 2, day=d) for k in range(5)]
        _write(tmp_path, f"journal_{d}.jsonl", rows)
    late = sdp.capped_report(tmp_path, date(2026, 9, 30))
    week1 = sdp.capped_report(tmp_path, date(2026, 9, 25))["week_to_date"]
    week2 = late["week_to_date"]
    total = late["since_start"]
    assert late["selection_start"] == "2026-09-23"
    for view in ("account", "per_market", "every_signal"):
        assert total[view]["trades"] == week1[view]["trades"] + week2[view]["trades"]
        assert total[view]["net_usd"] == round(week1[view]["net_usd"] + week2[view]["net_usd"], 2)
    assert total["account"]["trades"] == 6 * 3 and total["every_signal"]["trades"] == 6 * 5


def test_running_total_absent_before_start(tmp_path):
    rep = sdp.capped_report(tmp_path, date(2026, 9, 22))
    assert "since_start" not in rep and rep["selection_start"] == "2026-09-21"
    assert "Running total" not in "\n".join(sdp._capped_lines(rep))


def test_coverage_counts_and_priced_denominator(tmp_path):
    rows = [
        _cap_row("p", "MNQ", "WIN", 40, "2026-09-23T14:00:00+00:00", 1, 2),
        # Resolver left it open and no stored bars exist: unpriced, assumed flat at 17:00 ET.
        _cap_row("u", "MNQ", "OPEN", None, "2026-09-23T16:00:00+00:00", 1, None,
                 entry=100.0, stop=90.0, target=120.0, resolved_at_bar_ts="2026-09-23T17:00:00+00:00"),
    ]
    _write(tmp_path, "journal_2026-09-23.jsonl", rows)
    rep = sdp.capped_report(tmp_path, DAY)
    acct = rep["since_start"]["account"]
    assert (acct["trades"], acct["priced"], acct["unpriced"]) == (2, 1, 1)
    assert acct["estimated_fill_times"] == 2          # no bar files: 15-minute fallback
    assert acct["assumed_flat_at_close"] == 1 and acct["played_out_on_bars"] == 0
    text = "\n".join(sdp._capped_lines(rep))
    assert "dollars cover 1 of 2 trades; 1 could not be priced" in text
    assert "(whole-account trades above: 2 with estimated fill times; 1 assumed closed at 5:00 PM ET)" in text


def test_report_without_log_dir_has_no_capped_section():
    rep = sdp.build_report([_row("w", "MNQ", "WIN", 40)], DAY)
    assert "capped" not in rep and "Your limits" not in sdp.format_digest(rep)


def test_bars_after_the_candidate_day_are_always_checked(tmp_path):
    # Night trade from a UTC evening: the resolver stamps resolved_at with the next
    # UTC day's first bar but never checked it — the stop there must count.
    row = _cap_row("n", "MNQ", "OPEN", None, "2026-10-05T23:00:00+00:00", 1, None, day="2026-10-05",
                   entry=100.0, stop=95.0, target=110.0, resolved_at_bar_ts="2026-10-06T00:00:00+00:00")
    _bar_file(tmp_path, "MNQ", "2026-10-05", [{"ts": "2026-10-05T23:15:00+00:00", "high": 101, "low": 99, "close": 100}])
    _bar_file(tmp_path, "MNQ", "2026-10-06", [
        {"ts": "2026-10-06T00:00:00+00:00", "high": 101, "low": 90, "close": 92},
        {"ts": "2026-10-06T00:15:00+00:00", "high": 120, "low": 92, "close": 118},
    ])
    trades = sdp.capped_trades([row], tmp_path)
    assert trades[0]["how"] == "stop" and trades[0]["net_usd"] < 0 and not trades[0]["won"]


def test_capped_failure_never_breaks_the_report(tmp_path, monkeypatch):
    monkeypatch.setattr(sdp, "capped_report", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad row")))
    rep = sdp.build_report([_row("w", "MNQ", "WIN", 40)], DAY, log_dir=tmp_path)
    assert rep["capped"]["error"].startswith("ValueError")
    text = sdp.format_digest(rep)
    assert "All markets:" in text and "not shown today" in text


def test_first_pass_digest_says_running_trades_come_later(tmp_path):
    _write(tmp_path, "journal_2026-09-23.jsonl", [_cap_row("w1", "MNQ", "WIN", 40, "2026-09-23T14:00:00+00:00", 1, 2)])
    rep = sdp.build_report(sdp.load_outcomes(tmp_path, DAY), DAY, log_dir=tmp_path)
    assert "first pass" in sdp.format_digest(rep)
    rep["pass"] = "final"
    assert "first pass" not in sdp.format_digest(rep)
    assert "(6:00 PM Tue Sep 22 – 5:00 PM Wed Sep 23 ET)" in sdp.format_digest(rep)
