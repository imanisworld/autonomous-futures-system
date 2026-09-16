from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys


def test_cli_is_deterministic_and_writes_manifest(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    bars_dir = tmp_path / "bars"
    bars_dir.mkdir()
    candidates = tmp_path / "candidates.jsonl"

    start = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    rows = []
    px = 20000.0
    for i in range(110):
        ts = start + timedelta(minutes=5 * i)
        o = px
        c = px + (1.0 if i % 3 else -0.25)
        rows.append({
            "timestamp": ts.isoformat(),
            "open": o,
            "high": max(o, c) + 0.5,
            "low": min(o, c) - 0.5,
            "close": c,
            "volume": 100 + i,
            "instrument": "MNQ",
            "market_condition": "TRENDING",
            "trend_direction": "UP",
            "trend_strength": "STRONG",
            "ema_9": c - 1,
            "ema_21": c - 2,
            "ema_55": c - 3,
        })
        px = c
    (bars_dir / "MNQ_test.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    sig1 = datetime(2026, 9, 2, 2, 0, tzinfo=timezone.utc)
    sig2 = datetime(2026, 9, 2, 2, 15, tzinfo=timezone.utc)
    crows = [
        {
            "candidate_id": "w",
            "instrument": "MNQ",
            "strategy": "demo",
            "session": "asian",
            "direction": "LONG",
            "signal_ts": sig1.isoformat(),
            "outcome_label": "WIN",
        },
        {
            "candidate_id": "l",
            "instrument": "MNQ",
            "strategy": "demo",
            "session": "asian",
            "direction": "LONG",
            "signal_ts": sig2.isoformat(),
            "outcome_label": "LOSS",
        },
    ]
    candidates.write_text("\n".join(json.dumps(r) for r in crows) + "\n")

    def run(out):
        return subprocess.run(
            [
                sys.executable,
                str(repo / "scripts/asian_precursor_audit.py"),
                "--candidates", str(candidates),
                "--bars", str(bars_dir),
                "--instrument", "MNQ",
                "--out-dir", str(out),
            ],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )

    out1 = tmp_path / "out1"
    out2 = tmp_path / "out2"
    run(out1)
    run(out2)

    assert (out1 / "features.jsonl").read_bytes() == (out2 / "features.jsonl").read_bytes()
    assert (out1 / "summary.json").read_bytes() == (out2 / "summary.json").read_bytes()
    m1 = json.loads((out1 / "manifest.json").read_text())
    m2 = json.loads((out2 / "manifest.json").read_text())
    assert m1["feature_sha256"] == m2["feature_sha256"]
    assert m1["summary_sha256"] == m2["summary_sha256"]
    assert m1["feature_rows"] == 2
    assert m1["bar_rows"] == 110


def test_research_module_has_no_runtime_execution_imports():
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "research/asian_precursor_audit.py").read_text()
    forbidden = (
        "execution.",
        "risk.",
        "webhook.",
        "Tradovate",
        "PaperBroker",
        "RiskEngine",
        "DecisionEngine",
        "requests",
        "httpx",
    )
    assert all(token not in text for token in forbidden)
