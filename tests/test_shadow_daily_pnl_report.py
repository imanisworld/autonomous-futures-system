"""ops/shadow_daily_pnl_report.py — one trading day of SHADOW_OUTCOME rows in
dollars. Evidence only; the report must never claim authority."""
from __future__ import annotations

import json
from datetime import date

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
