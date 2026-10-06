from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from options_evidence import strategy_epochs as se

REGISTRY = se.DEFAULT_REGISTRY_PATH

# Every FROZEN/RETIRED epoch hash is pinned here. Editing a frozen definition
# *and* its stored hash together would otherwise pass load-time validation;
# this pin makes such an edit a visible test change in review.
FROZEN_PINS = {
    ("options_122", "122-IEX-E1"): "2938a9074a7e",
}


def _raw() -> dict:
    return json.loads(REGISTRY.read_text())


def _write(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "epochs.json"
    path.write_text(json.dumps(raw))
    return path


def _epoch(**overrides) -> dict:
    definition = {
        "setup": {"family": "STRAT_3_2_2", "timeframe": "1h"},
        "trigger": {"rule": "break"},
        "target": {"t1": "1R"},
        "filters": {"spy_qqq": "aligned"},
        "authority": {"observation_only": True, "execution_authority": False},
    }
    thresholds = {"min_rr": 1.0}
    raw = {
        "strategy": "322",
        "epoch": "2026Q4_v1",
        "status": "FROZEN",
        "definition": definition,
        "thresholds": thresholds,
        "effective_from": "2026-10-01T13:30:00Z",
        "effective_until": None,
        "source_commit": "a" * 40,
        "preregistration_doc": "docs/prereg.md",
        "oos_reference": {
            "source": "untouched OOS 2025H2",
            "artifact_path": "docs/research-evidence/x/result.json",
            "artifact_sha256": "b" * 64,
            "r_outcomes": [1.0, -1.0, 2.0, -1.0, 0.5],
            "cost_model": "ask entry / bid exit, $0.65 per contract",
        },
        "supersedes": None,
    }
    raw.update(overrides)
    raw["definition_sha256"] = se.definition_hash(raw["definition"], raw["thresholds"])
    return raw


def _registry(tmp_path, *epochs) -> se.EpochRegistry:
    return se.load_registry(_write(tmp_path, {"schema": se.REGISTRY_SCHEMA, "epochs": list(epochs)}))


def test_committed_registry_loads_and_frozen_hashes_are_pinned():
    registry = se.load_registry(REGISTRY)
    frozen = {
        e.key: e.definition_sha256[:12]
        for e in registry.epochs
        if e.status in (se.EpochStatus.FROZEN, se.EpochStatus.RETIRED)
    }
    assert frozen == FROZEN_PINS


def test_committed_122_epoch_is_observation_only_with_no_oos_reference():
    epoch = se.load_registry(REGISTRY).get("options_122", "122-IEX-E1")
    assert epoch is not None
    assert epoch.observation_only is True
    assert epoch.definition["authority"]["execution_authority"] is False
    assert epoch.definition["target"]["strategy_stop"] == "UNRESOLVED"
    assert epoch.oos_reference is None
    assert epoch.thresholds == {
        "cadence_seconds": 60,
        "max_capture_lag_seconds": 120,
        "sip_reconcile_delay_minutes": 16,
    }


def test_in_place_material_edit_is_refused(tmp_path):
    raw = _raw()
    raw["epochs"][0]["thresholds"]["max_capture_lag_seconds"] = 180
    with pytest.raises(se.RegistryError, match="material change requires a new epoch"):
        se.load_registry(_write(tmp_path, raw))


def test_provenance_edit_does_not_change_definition_hash():
    raw = _epoch()
    edited = copy.deepcopy(raw)
    edited["notes"] = "clarified wording"
    edited["preregistration_doc"] = "docs/other.md"
    assert se.definition_hash(raw["definition"], raw["thresholds"]) == se.definition_hash(
        edited["definition"], edited["thresholds"]
    )


@pytest.mark.parametrize("section", se.MATERIAL_SECTIONS)
def test_every_material_section_changes_the_hash(section):
    raw = _epoch()
    changed = copy.deepcopy(raw["definition"])
    changed[section] = {**changed[section], "changed": True}
    assert se.definition_hash(changed, raw["thresholds"]) != raw["definition_sha256"]


def test_missing_or_extra_definition_sections_are_refused(tmp_path):
    raw = _epoch()
    del raw["definition"]["filters"]
    raw["definition_sha256"] = se.definition_hash(raw["definition"], raw["thresholds"])
    with pytest.raises(se.RegistryError, match="missing material sections"):
        _registry(tmp_path, raw)
    raw = _epoch()
    raw["definition"]["comment"] = "x"
    with pytest.raises(se.RegistryError, match="non-material keys"):
        _registry(tmp_path, raw)


def test_registry_cannot_grant_execution_authority(tmp_path):
    raw = _epoch()
    raw["definition"]["authority"]["execution_authority"] = True
    raw["definition_sha256"] = se.definition_hash(raw["definition"], raw["thresholds"])
    with pytest.raises(se.RegistryError, match="execution_authority must be false"):
        _registry(tmp_path, raw)


def test_frozen_epoch_requires_preregistration_provenance(tmp_path):
    for field in ("effective_from", "source_commit", "preregistration_doc"):
        raw = _epoch(**{field: None})
        with pytest.raises(se.RegistryError, match="require effective_from"):
            _registry(tmp_path, raw)
    with pytest.raises(se.RegistryError, match="40-hex"):
        _registry(tmp_path, _epoch(source_commit="abc123"))


def test_draft_epoch_needs_no_dates(tmp_path):
    registry = _registry(tmp_path, _epoch(status="DRAFT", effective_from=None, source_commit=None))
    assert registry.get("322", "2026Q4_v1").status is se.EpochStatus.DRAFT


def test_relabel_same_definition_under_new_epoch_is_refused(tmp_path):
    first = _epoch(effective_until="2026-11-01T00:00:00Z")
    second = _epoch(epoch="2026Q4_v2", effective_from="2026-11-01T00:00:00Z", supersedes="2026Q4_v1")
    with pytest.raises(se.RegistryError, match="relabel"):
        _registry(tmp_path, first, second)


def test_overlapping_epochs_are_refused_and_sequential_ones_load(tmp_path):
    first = _epoch()
    second = _epoch(
        epoch="2026Q4_v2",
        effective_from="2026-11-01T00:00:00Z",
        thresholds={"min_rr": 1.5},
        supersedes="2026Q4_v1",
    )
    with pytest.raises(se.RegistryError, match="overlap"):
        _registry(tmp_path, first, second)
    first["effective_until"] = "2026-11-01T00:00:00Z"
    registry = _registry(tmp_path, first, second)
    assert [e.epoch for e in registry.for_strategy("322")] == ["2026Q4_v1", "2026Q4_v2"]


def test_duplicate_key_and_unknown_supersedes_are_refused(tmp_path):
    with pytest.raises(se.RegistryError, match="duplicate"):
        _registry(tmp_path, _epoch(status="DRAFT"), _epoch(status="DRAFT", thresholds={"min_rr": 2}))
    with pytest.raises(se.RegistryError, match="supersedes unknown"):
        _registry(tmp_path, _epoch(supersedes="nope"))


def test_oos_reference_must_be_hash_pinned_and_finite(tmp_path):
    raw = _epoch()
    raw["oos_reference"]["artifact_sha256"] = "short"
    with pytest.raises(se.RegistryError, match="sha256"):
        _registry(tmp_path, raw)
    raw = _epoch()
    raw["oos_reference"]["r_outcomes"] = []
    with pytest.raises(se.RegistryError, match="non-empty"):
        _registry(tmp_path, raw)
    raw = _epoch()
    raw["thresholds"] = {"x": float("nan")}
    with pytest.raises((se.RegistryError, ValueError)):
        _registry(tmp_path, raw)


def test_resolve_epoch_matches_only_frozen_covering_definition(tmp_path):
    raw = _epoch()
    registry = _registry(tmp_path, raw)
    at = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    ok = se.resolve_epoch(
        registry, strategy="322", definition=raw["definition"], thresholds=raw["thresholds"], at=at
    )
    assert ok.label == "2026Q4_v1" and ok.registered

    drifted = se.resolve_epoch(
        registry, strategy="322", definition=raw["definition"], thresholds={"min_rr": 1.1}, at=at
    )
    assert drifted.label == se.UNREGISTERED_EPOCH and not drifted.registered

    before = se.resolve_epoch(
        registry,
        strategy="322",
        definition=raw["definition"],
        thresholds=raw["thresholds"],
        at=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert before.label == se.UNREGISTERED_EPOCH


def test_draft_epoch_never_stamps_new_signals(tmp_path):
    raw = _epoch(status="DRAFT")
    registry = _registry(tmp_path, raw)
    res = se.resolve_epoch(
        registry,
        strategy="322",
        definition=raw["definition"],
        thresholds=raw["thresholds"],
        at=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )
    assert res.label == se.UNREGISTERED_EPOCH
    assert "DRAFT" in res.reason


def test_historical_records_are_never_relabelled(tmp_path):
    registry = _registry(tmp_path, _epoch())
    assert se.epoch_label_for_record(registry, {"strategy": "322"}) == se.LEGACY_UNVERSIONED
    assert (
        se.epoch_label_for_record(registry, {"strategy": "322", "strategy_epoch": "old"})
        == se.UNREGISTERED_EPOCH
    )
    assert (
        se.epoch_label_for_record(registry, {"strategy": "322", "strategy_epoch": "2026Q4_v1"})
        == "2026Q4_v1"
    )


def test_reserved_labels_cannot_be_registered(tmp_path):
    with pytest.raises(se.RegistryError, match="reserved"):
        _registry(tmp_path, _epoch(epoch=se.LEGACY_UNVERSIONED))
