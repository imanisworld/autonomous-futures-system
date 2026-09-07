from __future__ import annotations

import json
from pathlib import Path

from ops.project_check.promotion import build_promotion_report


def _write_evidence(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _pin_runtime(monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_FILL_MODEL", "ioc_limit")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")


def test_report_ok_is_false_when_hard_promotion_blocker_exists(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime(monkeypatch)
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 1,
                "fills": 0,
                "cancellations": 1,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 0,
                "legitimately_open": 0,
            },
            "stated_classification": "PROMISING BUT UNPROVEN",
        },
    )

    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)

    assert report["classification"]["blockers"]
    assert report["gate_pass"] is False
    assert report["promotion_eligible"] is False
    assert report["ok"] is False


def test_report_ok_is_true_only_when_promotion_gate_passes(tmp_path: Path, monkeypatch) -> None:
    _pin_runtime(monkeypatch)
    evidence = _write_evidence(
        tmp_path,
        {
            "execution": {
                "entry_attempts": 1,
                "fills": 1,
                "cancellations": 0,
                "rejects_or_known_no_fills": 0,
                "resolved_outcomes": 1,
                "legitimately_open": 0,
            },
            "stated_classification": "WAIT",
        },
    )

    report = build_promotion_report(strategy="x", repo_root=tmp_path, evidence_path=evidence)

    assert report["classification"]["blockers"] == []
    assert report["gate_pass"] is True
    assert report["promotion_eligible"] is True
    assert report["ok"] is True
