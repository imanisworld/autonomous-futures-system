"""tests/test_session_snapshot.py

Proves ops.session_snapshot composes existing read-only tooling without
crashing when data is missing (no release manifest, no journals) and that
it never silently invents a value -- everything unavailable is UNKNOWN or
carries an explicit error, and the deployed-release check never touches the
network or a broker.
"""

from __future__ import annotations

from pathlib import Path

from ops import session_snapshot


def test_deployed_release_state_is_unknown_without_a_manifest(tmp_path):
    result = session_snapshot.deployed_release_state(repo_root=tmp_path)
    assert result["status"] == "UNKNOWN"
    assert "release_manifest.json" in result["reason"]


def test_risk_config_posture_reads_paper_mode_and_contract_caps():
    # A synthetic minimal risk_rules.yaml would need to satisfy every
    # load_config() validation rule (a maintenance trap); reuse this repo's
    # own real, already-valid risk_rules.yaml instead.
    repo_root = Path(__file__).resolve().parents[1]
    posture = session_snapshot.risk_config_posture(str(repo_root / "risk_rules.yaml"))
    assert posture["ok"] is True
    assert posture["live_trading_enabled"] is False
    assert isinstance(posture["paper_mode"], bool)
    assert isinstance(posture["max_contracts_per_instrument"], dict)
    assert isinstance(posture["max_daily_loss"], float)


def test_risk_config_posture_reports_error_not_crash_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    posture = session_snapshot.risk_config_posture(str(tmp_path / "missing.yaml"))
    assert posture["ok"] is False
    assert posture["live_trading_enabled"] == "UNKNOWN"


def test_entry_tolerance_reads_per_instrument_env_override(monkeypatch):
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    result = session_snapshot.entry_tolerance_by_instrument()
    assert result == {"MES": 16.0, "MNQ": 32.0}


def test_build_runtime_snapshot_never_raises_and_labels_env_caveat(tmp_path):
    (tmp_path / "bars_MES_2026-07-20.jsonl").write_text("")
    snapshot = session_snapshot.build_runtime_snapshot(tmp_path, repo_root=tmp_path)
    assert snapshot["read_only"] is True
    assert "deployed_release" in snapshot
    assert "caveat" in snapshot
    assert "not a confirmed read of" in snapshot["caveat"]
