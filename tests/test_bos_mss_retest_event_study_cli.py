from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def test_cli_runs_without_pythonpath_and_writes_manifest(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "MNQ"
    out_dir = tmp_path / "out"
    data_dir.mkdir()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(9):
        ts = base + timedelta(minutes=5 * i)
        rows.append(
            {
                "timestamp": ts.isoformat(),
                "open": 100 + i,
                "high": 101 + i,
                "low": 99 + i,
                "close": 100 + i,
                "volume": 10,
            }
        )
    path = data_dir / "MNQ_2026-01-01.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    env = {"PATH": str(Path(sys.executable).parent)}
    proc = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/bos_mss_retest_event_study.py"),
            "--instrument",
            "MNQ",
            "--data-dir",
            str(data_dir),
            "--out-dir",
            str(out_dir),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    manifest = json.loads((out_dir / "mnq_bos_mss_retest_manifest.json").read_text())
    summary = json.loads((out_dir / "mnq_bos_mss_retest_summary.json").read_text())
    assert manifest["research_only"] is True
    assert manifest["trade_strategy_defined"] is False
    assert manifest["choch_claimed"] is False
    assert manifest["input_rows"] == 9
    assert len(manifest["input_files"]) == 1
    assert len(manifest["input_files"][0]["sha256"]) == 64
    assert summary["study_phase"] == "MNQ_PHASE_1"
    assert summary["build"]["complete_15m_bars"] == 3


def test_cli_rejects_reversed_date_window(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts/bos_mss_retest_event_study.py"),
            "--data-dir",
            str(tmp_path),
            "--out-dir",
            str(tmp_path / "out"),
            "--start-date",
            "2026-01-02",
            "--end-date",
            "2026-01-01",
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0
    assert "--end-date cannot be before --start-date" in proc.stderr
