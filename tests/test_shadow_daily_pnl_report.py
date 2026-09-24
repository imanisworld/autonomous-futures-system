"""ops/shadow_daily_pnl_report.py — one trading day of SHADOW_OUTCOME rows in
dollars. Evidence only; the report must never claim authority."""
from __future__ import annotations

import json
from datetime import date

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
    assert "Best: MNQ strat_22_continuation_observed" in text
    assert "Worst: MES ema_pullback_trend" in text
    assert "no orders" in text
    empty = sdp.format_digest(sdp.build_report([], DAY))
    assert "No shadow trades" in empty


def test_main_writes_files(tmp_path, capsys):
    _write(tmp_path, "journal_2026-09-23.jsonl", [_row("w", "MNQ", "WIN", 40)])
    assert sdp.main(["--log-dir", str(tmp_path), "--day", "2026-09-23"]) == 0
    assert (tmp_path / "shadow_daily_pnl_2026-09-23.json").exists()
    assert json.loads((tmp_path / "shadow_daily_pnl_latest.json").read_text())["total"]["wins"] == 1
    assert "Shadow P&L" in capsys.readouterr().out
