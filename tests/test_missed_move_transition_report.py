from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import missed_move_transition_report as mod


ET = timezone(timedelta(hours=-4))


def _bar(ts: datetime, open_: float, high: float, low: float, close: float, volume: int) -> dict[str, object]:
    return {
        "time": ts.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "Volume": volume,
    }


def _transition_segment(*, start: datetime, base: float, resolution: str) -> list[dict[str, object]]:
    bars = [
        _bar(start + timedelta(minutes=5 * 0), base + 5.0, base + 6.0, base + 0.0, base + 4.0, 1000),
        _bar(start + timedelta(minutes=5 * 1), base + 4.0, base + 7.0, base + 1.0, base + 3.0, 950),
        _bar(start + timedelta(minutes=5 * 2), base + 3.0, base + 6.0, base + 0.5, base + 5.0, 900),
        _bar(start + timedelta(minutes=5 * 3), base + 5.0, base + 8.0, base + 1.0, base + 2.0, 980),
        _bar(start + timedelta(minutes=5 * 4), base + 2.0, base + 6.5, base + 0.25, base + 4.0, 1025),
        _bar(start + timedelta(minutes=5 * 5), base + 4.0, base + 7.5, base + 0.75, base + 3.0, 990),
        _bar(start + timedelta(minutes=5 * 6), base + 1.0, base + 4.0, base - 1.5, base + 1.5, 1700),
        _bar(start + timedelta(minutes=5 * 7), base + 0.5, base + 3.0, base - 1.0, base + 2.0, 1800),
        _bar(start + timedelta(minutes=5 * 8), base + 1.8, base + 2.5, base + 1.5, base + 2.1, 1400),
    ]
    if resolution == "win":
        bars.append(_bar(start + timedelta(minutes=5 * 9), base + 2.0, base + 4.5, base + 0.5, base + 3.5, 1500))
    elif resolution == "loss":
        bars.append(_bar(start + timedelta(minutes=5 * 9), base + 2.0, base + 2.2, base - 2.0, base + 1.0, 1500))
    else:
        bars.append(_bar(start + timedelta(minutes=5 * 9), base + 1.9, base + 2.1, base + 1.7, base + 1.95, 1500))
    return bars


def _write_csv(path: Path) -> None:
    rows = []
    rows.extend(
        _transition_segment(
            start=datetime(2026, 7, 8, 7, 0, tzinfo=ET),
            base=100.0,
            resolution="win",
        )
    )
    rows.extend(
        _transition_segment(
            start=datetime(2026, 7, 8, 10, 0, tzinfo=ET),
            base=200.0,
            resolution="loss",
        )
    )
    header = "time,open,high,low,close,Volume"
    body = "\n".join(
        ",".join(str(row[key]) for key in ("time", "open", "high", "low", "close", "Volume"))
        for row in rows
    )
    path.write_text(f"{header}\n{body}\n", encoding="utf-8")


def test_transition_report_scans_csv_and_summarizes_metrics(tmp_path):
    csv_path = tmp_path / "CME_MINI_MNQ1!, 5.csv"
    _write_csv(csv_path)

    bars = mod._load_csv(csv_path)
    rows = mod._scan(bars, instrument="MNQ", timeframe="5m", max_forward_bars=4)
    distribution = mod._distribution(rows, "5m")
    markdown = mod._markdown(csv_path, mod._summary(bars), rows, distribution)

    assert len(rows) == 2
    assert distribution["candidate_count"] == 2
    assert distribution["filled_count"] == 2
    assert distribution["fill_rate"] == 1.0
    assert distribution["result_counts"]["WIN"] == 1
    assert distribution["result_counts"]["LOSS"] == 1
    assert distribution["win_rate"] == 0.5
    assert distribution["win_loss_ratio"] == 1.0
    assert distribution["session_split"]["london"]["candidates"] == 1
    assert distribution["session_split"]["ny"]["candidates"] == 1
    assert distribution["mfe_points"]["avg"] is not None
    assert distribution["mae_points"]["avg"] is not None
    assert distribution["strategy_net_pnl_dollars"] is not None
    assert distribution["matched_baseline_net_pnl_dollars"] is not None
    assert all("net_pnl_dollars" in row["matched_baseline"] for row in rows)
    assert "fill_rate" in markdown
    assert "Session Split" in markdown
    assert "transition_failed_breakdown_reclaim" in markdown


def test_transition_report_main_writes_only_requested_outputs(tmp_path):
    csv_path = tmp_path / "CME_MINI_MNQ1!, 5.csv"
    out_md = tmp_path / "transition_report.md"
    out_json = tmp_path / "transition_report.json"
    _write_csv(csv_path)

    before = {p.name for p in tmp_path.iterdir()}
    rc = mod.main(
        [
            str(csv_path),
            "--instrument",
            "MNQ",
            "--timeframe",
            "5m",
            "--max-forward-bars",
            "4",
            "--commission-round-trip",
            "1.48",
            "--slippage-ticks-per-leg",
            "1",
            "--output",
            str(out_md),
            "--json-output",
            str(out_json),
        ]
    )
    after = {p.name for p in tmp_path.iterdir()}

    assert rc == 0
    assert out_md.exists()
    assert out_json.exists()
    assert after - before == {out_md.name, out_json.name}

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    assert payload["distribution"]["candidate_count"] == 2
    assert payload["distribution"]["fill_rate"] == 1.0
    assert payload["distribution"]["win_rate"] == 0.5
    assert payload["distribution"]["cost_model"]["commission_round_trip_dollars"] == 1.48
    assert payload["distribution"]["cost_model"]["slippage_ticks_per_leg"] == 1.0
    assert "fill_rate" in out_md.read_text(encoding="utf-8")
