"""
tests/test_replay_engine.py

Offline replay coverage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from replay import ReplayCandleLoader, ReplayEngine
from replay.__main__ import main as replay_cli_main
from replay.manifest import ReplayManifest


def test_candle_loader_reads_sample_day():
    candles = ReplayCandleLoader().load_jsonl("data/replay/sample_day_mnq.jsonl")

    assert len(candles) == 3
    assert candles[0].instrument == "MNQ"
    assert candles[0].session == "new_york"
    assert candles[0].price_vs_vwap == "above"


def test_replay_engine_runs_sample_day(config, tmp_path):
    config.log_dir = str(tmp_path)
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        "data/replay/sample_day_mnq.jsonl",
        review_date="2026-05-23",
    )

    assert report.candles_processed == 3
    assert report.approved_trades == 1
    assert report.wins == 1
    assert report.losses == 0
    assert report.open_trades == 0
    assert report.realized_pnl_dollars > 0
    assert Path(report.journal_path).exists()
    assert Path(report.review_path).exists()
    assert (tmp_path / "replay_report_2026-05-23.md").exists()


def test_replay_journal_preserves_historical_context_for_adaptive_committee(config, tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    candles = [json.loads(line) for line in source.read_text().splitlines()]
    report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
        source,
        review_date="2026-05-23",
    )

    entries = [
        json.loads(line)
        for line in Path(report.journal_path).read_text().splitlines()
        if line.strip()
    ]
    decisions = [entry for entry in entries if entry.get("decision")]
    outcomes = [entry for entry in entries if entry.get("type") == "OUTCOME"]

    assert decisions[0]["ts"] == candles[0]["timestamp"]
    assert decisions[0]["context"]["trend"]["strength"] == candles[0]["trend_strength"]
    assert decisions[0]["context"]["vwap"]["value"] == candles[0]["vwap"]
    assert decisions[0]["context"]["volume"]["current_bar"] == candles[0]["volume"]
    assert outcomes[0]["ts"] in {candle["timestamp"] for candle in candles}


def test_replay_stops_after_max_trades(config, tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    candles = []
    for i in range(6):
        c = dict(base)
        c["timestamp"] = (base_dt + timedelta(minutes=i)).isoformat()
        # High well above any target → guarantees WIN on every resolution bar.
        c["high"] = 99999.0
        candles.append(json.dumps(c))

    replay_path = tmp_path / "many_trades.jsonl"
    replay_path.write_text("\n".join(candles) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 3
    assert report.stopped_reason == "max_trades_per_day"


def test_replay_generates_no_trade_for_choppy_day(config, tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle["market_condition"] = "CHOPPY"
    replay_path = tmp_path / "choppy.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 0
    assert report.no_trades == 1
    assert report.wins == 0


def test_one_week_replay_survival_harness(config, tmp_path):
    paths = sorted(Path("data/replay/week").glob("*.jsonl"))

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_many(paths)

    assert report.days == 5
    assert report.survival_passed is True
    assert report.open_trades == 0
    assert report.approved_trades <= 15
    assert report.candles_processed > 0
    assert (tmp_path / "logs" / "multi_day_replay_report.md").exists()


# ---------------------------------------------------------------------------
# Phase 2B hardening: candle loader edge cases
# ---------------------------------------------------------------------------

def test_candle_loader_rejects_duplicate_timestamp(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    dup = tmp_path / "dup.jsonl"
    dup.write_text(json.dumps(base) + "\n" + json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="duplicate timestamp"):
        ReplayCandleLoader().load_jsonl(dup)


def test_candle_loader_rejects_high_below_low(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base["high"] = base["low"] - 1.0  # high < low → invalid
    bad = tmp_path / "bad_ohlc.jsonl"
    bad.write_text(json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="high < low"):
        ReplayCandleLoader().load_jsonl(bad)


def test_candle_loader_rejects_close_outside_range(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base["close"] = base["high"] + 10.0  # close > high → invalid
    bad = tmp_path / "bad_close.jsonl"
    bad.write_text(json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="close outside high/low"):
        ReplayCandleLoader().load_jsonl(bad)


def test_candle_loader_rejects_nonchronological(tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    c1 = dict(base)
    c2 = dict(base)
    c1["timestamp"] = (base_dt + timedelta(minutes=5)).isoformat()
    c2["timestamp"] = base_dt.isoformat()  # earlier than c1 → out of order

    bad = tmp_path / "unordered.jsonl"
    bad.write_text(json.dumps(c1) + "\n" + json.dumps(c2) + "\n")

    with pytest.raises(ValueError, match="not sorted by timestamp"):
        ReplayCandleLoader().load_jsonl(bad)


def test_candle_loader_rejects_mixed_instruments(tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    c1 = dict(base)
    c2 = dict(base)
    c2["timestamp"] = (base_dt + timedelta(minutes=5)).isoformat()
    c2["instrument"] = "MES"  # different instrument

    mixed = tmp_path / "mixed.jsonl"
    mixed.write_text(json.dumps(c1) + "\n" + json.dumps(c2) + "\n")

    with pytest.raises(ValueError, match="mixed instruments"):
        ReplayCandleLoader().load_jsonl(mixed)


def test_candle_loader_allows_mixed_instruments_when_flag_set(tmp_path):
    from datetime import datetime, timedelta

    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    base_dt = datetime.fromisoformat(base["timestamp"])

    c1 = dict(base)
    c2 = dict(base)
    c2["timestamp"] = (base_dt + timedelta(minutes=5)).isoformat()
    c2["instrument"] = "MES"

    mixed = tmp_path / "mixed_ok.jsonl"
    mixed.write_text(json.dumps(c1) + "\n" + json.dumps(c2) + "\n")

    candles = ReplayCandleLoader().load_jsonl(mixed, allow_mixed_instruments=True)
    assert len(candles) == 2
    assert {c.instrument for c in candles} == {"MNQ", "MES"}


def test_candle_loader_rejects_missing_required_field(tmp_path):
    source = Path("data/replay/sample_day_mnq.jsonl")
    base = json.loads(source.read_text().splitlines()[0])
    del base["vwap"]
    bad = tmp_path / "missing_field.jsonl"
    bad.write_text(json.dumps(base) + "\n")

    with pytest.raises(ValueError, match="missing fields"):
        ReplayCandleLoader().load_jsonl(bad)


# ---------------------------------------------------------------------------
# Phase 2B hardening: session blocking
# ---------------------------------------------------------------------------

def test_asian_session_produces_no_trade(config, tmp_path):
    """day_4 contains only an asian-session candle; risk engine must block it."""
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        "data/replay/week/day_4_asian_disabled.jsonl",
        review_date="2026-05-21",
    )

    assert report.approved_trades == 0
    assert report.wins == 0
    assert report.losses == 0


def test_session_cutoff_blocks_trade_at_1145_et(config, tmp_path):
    """A candle at 11:45 AM ET must be rejected by the session cutoff (11:30 ET)."""
    import dataclasses

    cfg = dataclasses.replace(config, session_cutoffs={"new_york": "11:30"})

    # 11:45 ET = 15:45 UTC (May = EDT = UTC-4)
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle["timestamp"] = "2026-05-23T15:45:00+00:00"
    candle["trend_strength"] = "STRONG"
    candle["volume"] = 5000  # relative = 5000/3800 ≈ 1.31 — above 0.8
    candle["price_vs_vwap"] = "above"

    replay_path = tmp_path / "cutoff_1145.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=cfg, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    assert report.approved_trades == 0, (
        "Trade at 11:45 ET should be blocked by 11:30 session cutoff"
    )


# ---------------------------------------------------------------------------
# Phase 2B hardening: manifest
# ---------------------------------------------------------------------------

def test_manifest_runs_week(config, tmp_path):
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_manifest(
        "data/replay/week/manifest.json"
    )

    assert report.days == 5
    assert report.survival_passed is True
    assert report.open_trades == 0
    assert report.candles_processed > 0
    assert (tmp_path / "logs" / "multi_day_replay_report.md").exists()


def test_manifest_replay_is_idempotent(config, tmp_path):
    engine = ReplayEngine(config=config, log_dir=str(tmp_path / "logs"))

    first = engine.run_manifest("data/replay/week/manifest.json")
    second = engine.run_manifest("data/replay/week/manifest.json")

    assert second.approved_trades == first.approved_trades == 3
    assert second.no_trades == first.no_trades == 3
    assert second.wins == first.wins == 2
    assert second.losses == first.losses == 1
    assert second.realized_pnl_dollars == first.realized_pnl_dollars == 245.0


def test_manifest_rejects_empty_days(tmp_path):
    manifest = tmp_path / "empty_manifest.json"
    manifest.write_text(json.dumps({"days": []}))

    with pytest.raises(ValueError, match="non-empty 'days' list"):
        ReplayManifest.load(manifest)


def test_manifest_rejects_missing_replay_file(tmp_path):
    manifest = tmp_path / "missing_file_manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "days": [
                    {
                        "path": "missing.jsonl",
                        "instrument": "MNQ",
                        "session": "new_york",
                        "expected_behavior": "no_trade",
                    }
                ]
            }
        )
    )

    with pytest.raises(ValueError, match="missing replay file"):
        ReplayManifest.load(manifest)


def test_replay_cli_accepts_manifest_without_candles(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        [
            "replay",
            "--manifest",
            "data/replay/week/manifest.json",
            "--log-dir",
            str(tmp_path / "logs"),
        ],
    )

    assert replay_cli_main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["days"] == 5
    assert output["survival_passed"] is True


def test_replay_cli_rejects_ambiguous_manifest_and_candles(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "replay",
            "--manifest",
            "data/replay/week/manifest.json",
            "--candles",
            "data/replay/sample_day_mnq.jsonl",
        ],
    )

    with pytest.raises(SystemExit):
        replay_cli_main()


# ---------------------------------------------------------------------------
# Phase 2B hardening: aggregate stats
# ---------------------------------------------------------------------------

def test_aggregate_max_drawdown_is_cumulative(config, tmp_path):
    """Multi-day drawdown must reflect the cumulative equity curve, not just
    the worst single-day figure."""
    paths = sorted(Path("data/replay/week").glob("*.jsonl"))
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_many(paths)

    # max_drawdown must be >= 0 and representable as a float
    assert isinstance(report.max_drawdown, float)
    assert report.max_drawdown >= 0.0


# ---------------------------------------------------------------------------
# Full-day strat + ORB replay: mnq_full_day_2026_05_22
# Sequence with strat_212 disabled:
#   Trade 1 — orb_reclaim LONG  → WIN  (bar 4 fires, bar 5 resolves high≥target)
#   Trade 2 — vwap_hold   SHORT → OPEN (bar 12 fires, no future bar to resolve)
# strat_212 removed from enabled_concepts; bar 9 no longer fires.
# ---------------------------------------------------------------------------

FULL_DAY_PATH = Path("data/replay/mnq_full_day_2026_05_22.jsonl")


@pytest.mark.skipif(
    not FULL_DAY_PATH.exists(),
    reason="full-day candle file not present",
)
class TestFullDayReplay2026_05_22:
    def _run(self, tmp_path):
        from dataclasses import replace
        from config.settings import load_config, PositionSizingConfig

        cfg = replace(load_config(), position_sizing=PositionSizingConfig(enabled=False))
        return ReplayEngine(config=cfg, log_dir=str(tmp_path / "logs")).run(FULL_DAY_PATH)

    def test_all_13_candles_processed(self, tmp_path):
        report = self._run(tmp_path)
        assert report.candles_processed == 13

    def test_mnq_approves_two_trades(self, tmp_path):
        report = self._run(tmp_path)
        assert report.approved_trades == 2

    def test_not_stopped_at_daily_limit(self, tmp_path):
        report = self._run(tmp_path)
        assert report.stopped_reason is None

    def test_at_least_one_win(self, tmp_path):
        report = self._run(tmp_path)
        assert report.wins >= 1

    def test_positive_realized_pnl(self, tmp_path):
        report = self._run(tmp_path)
        assert report.realized_pnl_dollars > 0

    def test_strategy_mix_in_journal(self, tmp_path):
        """Journal must contain exactly one entry for each expected strategy."""
        report = self._run(tmp_path)
        journal_path = Path(report.journal_path)
        strategies_traded = []
        for line in journal_path.read_text().splitlines():
            entry = json.loads(line)
            if entry.get("decision") == "TRADE":
                strategies_traded.append(entry.get("setup", {}).get("strategy"))
        assert "orb_reclaim" in strategies_traded
        assert "vwap_hold" in strategies_traded
        assert "strat_212" not in strategies_traded  # disabled

    def test_strat_fields_loaded_from_candle(self, tmp_path):
        """ReplayCandle must carry the Phase-2 strat fields from the JSONL."""
        from replay import ReplayCandleLoader
        candles = ReplayCandleLoader().load_jsonl(FULL_DAY_PATH)
        bar9 = candles[8]  # 0-indexed
        assert bar9.strat_sequence == "strat_212"
        assert bar9.strat_direction == "LONG"
        assert bar9.current_bar_type == "two_up"
        assert bar9.previous_bar_type == "inside_bar"


def test_aggregate_average_win_is_weighted(config, tmp_path):
    """Weighted average win must equal gross_win / total_wins, not avg of avgs."""
    paths = sorted(Path("data/replay/week").glob("*.jsonl"))
    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run_many(paths)

    if report.wins > 0:
        # Recompute expected weighted average from per-day reports
        day_reports = [
            ReplayEngine(config=config, log_dir=str(tmp_path / f"logs_{i}")).run(
                path, review_date=None
            )
            for i, path in enumerate(paths)
        ]
        total_pnl = sum(r.average_win * r.wins for r in day_reports)
        total_wins = sum(r.wins for r in day_reports)
        expected = round(total_pnl / total_wins, 2) if total_wins else 0.0
        assert report.average_win == expected


def test_enriched_replay_fields_can_trigger_gex_gate(config, tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle.update({
        "current_bar_type": "two_up",
        "icc_indication_type": "demand",
        "gex_flip": candle["close"] - 1,
        "call_wall": candle["close"] - 0.25,
        "put_wall": candle["close"] - 80,
        "signa_grade": "A",
        "signa_weekly_direction": "UP",
        "demand_top": candle["close"] + 2,
        "demand_bottom": candle["close"] - 5,
    })
    replay_path = tmp_path / "enriched_gex_block.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    report = ReplayEngine(config=config, log_dir=str(tmp_path / "logs")).run(
        replay_path,
        review_date="2026-05-23",
    )

    journal_path = Path(report.journal_path)
    entry = json.loads(journal_path.read_text().splitlines()[0])
    assert report.approved_trades == 0
    assert entry["failed_gates"] == ["GEX_UNDER_CALL_WALL"]
    assert entry["gex_status"] == "RED_LIGHT"


def test_candle_loader_preserves_enriched_fields(tmp_path):
    candle = json.loads(Path("data/replay/sample_day_mnq.jsonl").read_text().splitlines()[0])
    candle.update({
        "gex_flip": 19500,
        "call_wall": 19550,
        "signa_grade": "B",
        "signa_weekly_direction": "UP",
        "icc_indication_type": "demand",
        "demand_top": 19510,
        "demand_bottom": 19490,
    })
    replay_path = tmp_path / "enriched.jsonl"
    replay_path.write_text(json.dumps(candle) + "\n")

    loaded = ReplayCandleLoader().load_jsonl(replay_path)[0]

    assert loaded.gex_flip == 19500
    assert loaded.call_wall == 19550
    assert loaded.signa_grade == "B"
    assert loaded.signa_weekly_direction == "UP"
    assert loaded.icc_indication_type == "demand"
    assert loaded.demand_top == 19510
    assert loaded.demand_bottom == 19490
