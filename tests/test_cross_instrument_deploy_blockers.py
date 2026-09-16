"""Regressions for the two cross-instrument OFF-deployment blockers.

1. Code provenance: ``cross_instrument_observation.generating_sha`` must read
   the sanctioned release manifest (``release_manifest.json`` ``repo.commit``)
   when no release env var is set, exactly like the forward campaign, and must
   still fail CLOSED as ``unknown`` when neither proves a SHA — so a row from an
   unproven release can never become quality eligible.

2. Status authority: ``/status/observation-feeds`` must return the
   quality-gated report. Raw campaign ``READY FOR REVIEW`` may only appear as
   informational ``raw_status``; zero-count populations stay visible.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from execution import cross_instrument_observation as cio
from execution import forward_evidence_campaign as fec
from execution.cross_instrument_evidence_quality import CODE_PROVENANCE_UNKNOWN, assess_evidence_row

EPOCH = "deploy-blockers-epoch"
MANIFEST_SHA = "b" * 40
ENV_SHA = "c" * 40
_ENV_NAMES = ("AFS_RELEASE_SHA", "RELEASE_SHA", "GIT_SHA")


def _write_manifest(directory: Path, commit) -> Path:
    path = directory / "release_manifest.json"
    payload = {"schema_version": 1, "repo": {"commit": commit, "branch": "main"}}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def no_release_env(monkeypatch):
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """A cwd with no manifest, and the repo-root manifest lookup pointed at an
    empty dir, so only what the test writes can be found."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    root = tmp_path / "root"
    root.mkdir()
    (root / "execution").mkdir()
    monkeypatch.setattr(cio, "__file__", str(root / "execution" / "cross_instrument_observation.py"))
    return cwd


# ── 1. code provenance ──────────────────────────────────────────────────────

def test_env_sha_takes_precedence_over_manifest(no_release_env, isolated_cwd, monkeypatch):
    _write_manifest(isolated_cwd, MANIFEST_SHA)
    monkeypatch.setenv("AFS_RELEASE_SHA", ENV_SHA)
    assert cio.generating_sha() == (ENV_SHA, "environment:AFS_RELEASE_SHA")


def test_manifest_only_release_returns_repo_commit_with_manifest_provenance(no_release_env, isolated_cwd):
    _write_manifest(isolated_cwd, MANIFEST_SHA)
    sha, provenance = cio.generating_sha()
    assert sha == MANIFEST_SHA
    assert provenance == "manifest:release_manifest.json"
    # Same answer the already-deployed forward campaign gives for this release.
    assert fec.generating_sha() == (MANIFEST_SHA, "manifest:release_manifest.json")


def test_release_root_manifest_is_found_when_cwd_has_none(no_release_env, isolated_cwd, tmp_path):
    _write_manifest(tmp_path / "root", MANIFEST_SHA)
    assert cio.generating_sha() == (MANIFEST_SHA, "manifest:release_manifest.json")


@pytest.mark.parametrize("manifest_body", [
    None,                                   # no manifest at all
    "not json",                             # malformed
    json.dumps({"repo": {}}),               # no commit
    json.dumps({"repo": {"commit": ""}}),   # blank commit
    json.dumps({"repo": {"commit": 42}}),   # wrong type
    json.dumps({"repo": "x"}),              # wrong shape
    json.dumps([]),                         # wrong top-level type
])
def test_missing_or_malformed_manifest_fails_closed_as_unknown(no_release_env, isolated_cwd, manifest_body):
    if manifest_body is not None:
        (isolated_cwd / "release_manifest.json").write_text(manifest_body, encoding="utf-8")
    assert cio.generating_sha() == (None, "unknown")


def test_unknown_provenance_row_cannot_become_quality_eligible(no_release_env, isolated_cwd, tmp_path):
    sha, provenance = cio.generating_sha()
    row = {
        "campaign_id": cio.CAMPAIGN_ID, "evidence_schema_version": cio.SCHEMA_VERSION,
        "record_type": "OUTCOME", "candidate_id": "unknown-sha", "strategy": "strat_212",
        "instrument": "M2K", "variant": "observer", "evidence_epoch": EPOCH,
        "collection_mode": cio.STRUCTURAL_OUTCOME, "source_timeframe": "15",
        "signal_timestamp": "2026-08-10T15:00:00+00:00", "exit_timestamp": "2026-08-10T15:15:00+00:00",
        "result": "WIN", "pnl_r": 2.0, "generating_git_sha": sha, "provenance_status": provenance,
    }
    quality = assess_evidence_row(row, tmp_path / "logs")
    assert quality["eligible"] is False
    assert CODE_PROVENANCE_UNKNOWN in quality["issues"]


def test_observe_bar_stamps_manifest_sha_on_written_rows(no_release_env, isolated_cwd, tmp_path, monkeypatch):
    from webhook.payload import AlertPayload
    from webhook.state_builder import build_market_state

    _write_manifest(isolated_cwd, MANIFEST_SHA)
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)
    state = build_market_state(AlertPayload(
        ticker="M2K1!", timestamp="2026-09-15T14:30:00+00:00", timeframe="15",
        open=2300.0, high=2305.0, low=2295.0, close=2302.0, volume=1000, avg_volume=900, vwap=2298.0,
        market_condition="TRENDING", trend_direction="UP", trend_strength="MODERATE",
        previous_day_high=2320.0, previous_day_low=2280.0, previous_day_close=2299.0,
    ))
    log_dir = tmp_path / "logs"
    summary = cio.observe_bar(
        log_dir, state,
        [{"strategy": "strat_22_continuation_observed", "direction": "LONG", "entry": 2306.0, "stop": 2294.0, "target": 2330.0}],
        timeframe="15", source="test", include_strat_212_122=False,
    )
    assert summary["written"] == 1
    row = cio.read_evidence(log_dir)[0]
    assert row["generating_git_sha"] == MANIFEST_SHA
    assert row["provenance_status"] == "manifest:release_manifest.json"


# ── 2. status endpoint authority ────────────────────────────────────────────

def _dirty_terminal_rows(log_dir: Path, n: int = 30) -> None:
    """Thirty raw WIN rows across ten days with NO code provenance: the raw
    campaign gate calls this READY FOR REVIEW; the quality gate must not."""
    for i in range(n):
        day = date(2026, 8, 1) + timedelta(days=i % 10)
        cio._append_evidence(log_dir, {
            "evidence_schema_version": cio.SCHEMA_VERSION, "campaign_id": cio.CAMPAIGN_ID,
            "record_type": "OUTCOME", "candidate_id": f"dirty-{i}", "strategy": "strat_212",
            "instrument": "M2K", "variant": "observer", "evidence_epoch": EPOCH,
            "collection_mode": cio.STRUCTURAL_OUTCOME, "source_timeframe": "15",
            "signal_timestamp": datetime(day.year, day.month, day.day, 14, 30, tzinfo=timezone.utc).isoformat(),
            "exit_timestamp": datetime(day.year, day.month, day.day, 14, 45, tzinfo=timezone.utc).isoformat(),
            "result": "WIN", "pnl_r": 2.0, "generating_git_sha": None, "provenance_status": "unknown",
        })


@pytest.fixture
def armed(monkeypatch):
    monkeypatch.setenv(cio.ENV_NAME, cio.CAMPAIGN_ID)
    monkeypatch.setenv(cio.EPOCH_ENV_NAME, EPOCH)


def test_status_endpoint_report_is_quality_gated_not_raw(tmp_path, monkeypatch, armed):
    import asyncio
    import webhook.app as app_module

    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    _dirty_terminal_rows(tmp_path)

    body = asyncio.run(app_module.status_observation_feeds())
    report = body["report"]

    # Authority markers.
    assert report["quality_gate_authoritative"] is True
    assert report["raw_campaign_status_is_informational"] is True
    assert report["quality_rule"] == "only quality-eligible terminal outcomes may satisfy the review gate"

    # Dirty rows: raw gate would say READY, authoritative status must not.
    pop = next(p for p in report["populations"] if p["instrument"] == "M2K" and p["strategy"] == "strat_212")
    assert pop["terminal_outcomes"] == 30
    assert pop["raw_status"] == "READY FOR REVIEW"
    assert pop["status"] == "QUALITY BLOCKED"
    assert pop["quality_eligible_terminal_outcomes"] == 0
    assert pop["quality_blocked_terminal_outcomes"] == 30
    assert all(p["status"] != "READY FOR REVIEW" for p in report["populations"])

    # Zero-count populations remain visible, each with its own identity.
    zero = [p for p in report["populations"] if p["terminal_outcomes"] == 0]
    assert len(zero) == len(report["populations"]) - 1
    assert {(p["strategy"], p["instrument"], p["variant"], p["evidence_epoch"]) for p in report["populations"]} \
        == {(p["strategy"], p["instrument"], p["variant"], p["evidence_epoch"]) for p in cio.configured_populations(EPOCH)}
    assert all(p["quality_gate_authoritative"] is True for p in report["populations"])

    # Feed-health fields are untouched by the report swap.
    assert set(body["instruments"]) == set(cio.OBSERVATION_UNIVERSE)
    assert body["campaign"] == {"id": cio.CAMPAIGN_ID, "enabled": True, "evidence_epoch": EPOCH}


def test_status_endpoint_never_calls_raw_build_report_directly(tmp_path, monkeypatch, armed):
    import asyncio
    import webhook.app as app_module
    from execution import cross_instrument_evidence_quality as quality

    monkeypatch.setattr(app_module._config, "log_dir", str(tmp_path))
    calls: list[str] = []
    real_quality = quality.build_quality_report
    real_raw = app_module._cio.build_report
    monkeypatch.setattr(quality, "build_quality_report", lambda *a, **k: calls.append("quality") or real_quality(*a, **k))
    # The raw builder is legitimately reached only from INSIDE the quality wrapper.
    monkeypatch.setattr(app_module._cio, "build_report", lambda *a, **k: calls.append("raw") or real_raw(*a, **k))

    body = asyncio.run(app_module.status_observation_feeds())
    assert calls == ["quality", "raw"], calls          # quality entered first; raw only as its inner call
    assert body["report"]["quality_gate_authoritative"] is True
