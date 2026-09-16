from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


def _journal_row():
    return {
        "instrument": "MES",
        "decision": "NO_TRADE",
        "context": {
            "timeframe": "15",
            "timestamp": "2026-09-01T23:00:00+00:00",
            "session": "asian",
            "market_condition": "RANGE_BOUND",
            "structural_market_condition": "STRUCTURAL_RANGE",
            "structural_direction": None,
            "trend": {"direction": "UP", "strength": "STRONG"},
            "shadow_candidates": [
                {
                    "strategy": "demo",
                    "direction": "LONG",
                    "entry": 100.0,
                    "stop": 95.0,
                    "target": 110.0,
                }
            ],
        },
    }


def _write_fixture(data_dir: Path) -> None:
    bars = [
        {
            "instrument": "MES",
            "timeframe": "15",
            "ts": "2026-09-01T23:00:00+00:00",
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
        },
        {
            "instrument": "MES",
            "timeframe": "15",
            "ts": "2026-09-01T23:15:00+00:00",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
        },
        {
            "instrument": "MES",
            "timeframe": "15",
            "ts": "2026-09-01T23:30:00+00:00",
            "open": 101.0,
            "high": 111.0,
            "low": 100.0,
            "close": 110.0,
        },
    ]
    (data_dir / "bars_MES_fixture.jsonl").write_text(
        "\n".join(json.dumps(row) for row in bars) + "\n"
    )
    (data_dir / "journal_2026-09-01.jsonl").write_text(
        json.dumps(_journal_row()) + "\n"
    )


def _write_canonical_polygon_fixture(data_dir: Path) -> None:
    bars = [
        {
            "instrument": "MES",
            "timeframe": "15m",
            "timestamp": "2026-09-01T23:00:00+00:00",
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
        },
        {
            "instrument": "MES",
            "timeframe": "15m",
            "timestamp": "2026-09-01T23:15:00+00:00",
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
        },
        {
            "instrument": "MES",
            "timeframe": "15m",
            "timestamp": "2026-09-01T23:30:00+00:00",
            "open": 101.0,
            "high": 111.0,
            "low": 100.0,
            "close": 110.0,
        },
    ]
    (data_dir / "MES_2026-09-01.jsonl").write_text(
        "\n".join(json.dumps(row) for row in bars) + "\n"
    )
    (data_dir / "journal_2026-09-01.jsonl").write_text(
        json.dumps(_journal_row()) + "\n"
    )


def _run(repo: Path, data_dir: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
    out_dir.mkdir()
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/mes_asian_d_ema_baseline.py"),
            "--data-dir",
            str(data_dir),
            "--start-date",
            "2026-09-01",
            "--end-date",
            "2026-09-01",
            "--out",
            str(out_dir / "baseline.jsonl"),
            "--precursor-out",
            str(out_dir / "precursor.jsonl"),
            "--manifest-out",
            str(out_dir / "manifest.json"),
        ],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def test_direct_cli_without_pythonpath_is_deterministic(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_fixture(data_dir)

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    _run(repo, data_dir, out1)
    _run(repo, data_dir, out2)

    assert (out1 / "baseline.jsonl").read_bytes() == (out2 / "baseline.jsonl").read_bytes()
    assert (out1 / "precursor.jsonl").read_bytes() == (out2 / "precursor.jsonl").read_bytes()

    baseline = [json.loads(line) for line in (out1 / "baseline.jsonl").read_text().splitlines()]
    precursor = [json.loads(line) for line in (out1 / "precursor.jsonl").read_text().splitlines()]
    assert len(baseline) == 1
    assert len(precursor) == 1
    assert baseline[0]["result"] == "WIN"
    assert baseline[0]["instrument"] == "MES"
    assert baseline[0]["source_variant"] == "D0_D_EMA_CANONICAL_IOC"
    assert precursor[0]["outcome_label"] == "WIN"
    assert precursor[0]["candidate_id"] == baseline[0]["candidate_id"]

    m1 = json.loads((out1 / "manifest.json").read_text())
    m2 = json.loads((out2 / "manifest.json").read_text())
    assert m1["population_summary"] == m2["population_summary"]
    assert m1["outputs"]["baseline"]["sha256"] == m2["outputs"]["baseline"]["sha256"]
    assert (
        m1["outputs"]["precursor_terminal_cohort"]["sha256"]
        == m2["outputs"]["precursor_terminal_cohort"]["sha256"]
    )
    assert m1["population_summary"]["wins"] == 1
    assert m1["population_summary"]["selected_candidates"] == 1
    assert m1["population_summary"]["precursor_session"] == "asian"
    assert m1["economics"] == {
        "point_value": 5.0,
        "tick_size": 0.25,
        "tick_value": 1.25,
    }
    assert m1["fill_assumptions"]["broker"] == "execution.paper_broker.PaperBroker"
    assert m1["fill_assumptions"]["entry_fill_model"] == "ioc_limit"
    assert m1["fill_assumptions"]["market_price"] == "decision_bar_close"
    assert m1["fill_assumptions"]["ioc_tolerance_ticks"] == 16.0
    assert m1["fill_assumptions"]["ioc_tolerance_points"] == 4.0
    assert m1["fill_assumptions"]["entry_bar_reused_for_exit"] is False
    assert m1["journal_parse_skips"] == 0


def test_cli_accepts_canonical_polygon_replay_filename_and_timestamp_field(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_canonical_polygon_fixture(data_dir)

    out = tmp_path / "out"
    _run(repo, data_dir, out)

    baseline = [json.loads(line) for line in (out / "baseline.jsonl").read_text().splitlines()]
    manifest = json.loads((out / "manifest.json").read_text())
    assert len(baseline) == 1
    assert baseline[0]["signal_ts"] == "2026-09-01T23:00:00+00:00"
    assert baseline[0]["result"] == "WIN"
    assert any(
        entry["path"].endswith("MES_2026-09-01.jsonl")
        for entry in manifest["inputs"]["bars"]
    )
    assert manifest["accepted_bar_input_shapes"] == [
        "bars_MES_*.jsonl with ts",
        "polygon_to_replay MES_*.jsonl with timestamp",
    ]


def test_cli_refuses_permissive_parse_behavior_by_default(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_fixture(data_dir)
    with (data_dir / "journal_2026-09-01.jsonl").open("a") as fh:
        fh.write("{not-json}\n")

    out = tmp_path / "out"
    out.mkdir()
    proc = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/mes_asian_d_ema_baseline.py"),
            "--data-dir",
            str(data_dir),
            "--start-date",
            "2026-09-01",
            "--end-date",
            "2026-09-01",
            "--out",
            str(out / "baseline.jsonl"),
            "--precursor-out",
            str(out / "precursor.jsonl"),
            "--manifest-out",
            str(out / "manifest.json"),
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "invalid JSON" in proc.stderr
