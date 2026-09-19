from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.replay_gap_proof import build_gap_proof


def _sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path):
    bars = tmp_path / "bars.jsonl"
    bars.write_text('{"timestamp":"2026-01-02T00:00:00+00:00"}\n')
    manifest = tmp_path / "MANIFEST.json"
    manifest.write_text(json.dumps({
        "instrument": "MNQ",
        "timeframe_minutes": 15,
        "gap_ledger_cme_hours": [],
        "files": {bars.name: {"sha256": _sha256(bars), "rows": 1}},
    }) + "\n")
    journal = tmp_path / "journal.jsonl"
    oid = "PAPER-1"
    rows = [
        {
            "ts": "2026-01-02T00:00:00+00:00",
            "bar_ts": "2026-01-02T00:00:00+00:00",
            "decision": "TRADE",
            "paper_order_id": oid,
            "instrument": "MNQ",
        },
        {
            "ts": "2026-01-02T12:00:00+00:00",
            "type": "OUTCOME",
            "instrument": "MNQ",
            "outcome": {
                "result": "WIN",
                "paper_order_id": oid,
                "execution_audit": {
                    "historical_entry_bar_ts": "2026-01-02T00:00:00+00:00",
                    "historical_resolution_bar_ts": "2026-01-02T00:15:00+00:00",
                },
            },
        },
    ]
    journal.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    deps = tmp_path / "dependencies.json"
    deps.write_text(json.dumps({
        "schema_version": "strategy_dependency_windows_v1",
        "code_sha": "abc123",
        "strategy": "example",
        "instrument": "MNQ",
        "windows": {oid: "2026-01-01T23:45:00+00:00"},
    }) + "\n")
    return manifest, journal, deps, oid

def test_build_gap_proof_binds_journal_and_dependency_sources(tmp_path: Path):
    manifest, journal, deps, oid = _fixture(tmp_path)
    proof = build_gap_proof(
        repo_root=tmp_path,
        strategy="example",
        instrument="MNQ",
        manifest_path=manifest,
        journal_paths=[journal],
        dependency_windows_path=deps,
        code_sha="abc123",
    )
    assert proof["schema_version"] == "replay_gap_proof_v1"
    assert proof["dataset_manifest_sha256"] == _sha256(manifest)
    assert proof["source_journals"][0]["sha256"] == _sha256(journal)
    assert proof["dependency_windows_source"]["sha256"] == _sha256(deps)
    assert proof["resolved_outcomes"] == [{
        "paper_order_id": oid,
        "dependency_start_timestamp": "2026-01-01T23:45:00+00:00",
        "signal_timestamp": "2026-01-02T00:00:00+00:00",
        "entry_timestamp": "2026-01-02T00:00:00+00:00",
        "exit_timestamp": "2026-01-02T00:15:00+00:00",
    }]

def test_build_gap_proof_refuses_missing_historical_resolution(tmp_path: Path):
    manifest, journal, deps, _ = _fixture(tmp_path)
    rows = [json.loads(line) for line in journal.read_text().splitlines()]
    rows[1]["outcome"]["execution_audit"]["historical_resolution_bar_ts"] = None
    journal.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    with pytest.raises(ValueError, match="historical signal/entry/resolution timestamps missing"):
        build_gap_proof(
            repo_root=tmp_path,
            strategy="example",
            instrument="MNQ",
            manifest_path=manifest,
            journal_paths=[journal],
            dependency_windows_path=deps,
            code_sha="abc123",
        )
