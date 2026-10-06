from __future__ import annotations

import ast
import copy
import json
import types
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
        "authority": {
            "observation_only": True,
            "execution_authority": False,
            "risk_reservation": False,
            "trade_alerts": False,
        },
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
    with pytest.raises(se.RegistryError):
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


# ── hardening regressions (fail-closed, observation-only) ────────────────────


def _rehash(raw: dict) -> dict:
    raw["definition_sha256"] = se.definition_hash(raw["definition"], raw["thresholds"])
    return raw


def _write_text(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "epochs.json"
    path.write_text(text)
    return path


@pytest.mark.parametrize(
    "key, value",
    [
        ("observation_only", False),
        ("observation_only", 1),
        ("observation_only", "true"),
        ("execution_authority", True),
        ("execution_authority", 0),
        ("execution_authority", None),
        ("execution_authority", "false"),
        ("risk_reservation", True),
        ("risk_reservation", 0),
        ("trade_alerts", True),
        ("trade_alerts", "false"),
    ],
)
def test_authority_values_must_be_exact_observation_only_booleans(tmp_path, key, value):
    raw = _epoch()
    raw["definition"]["authority"][key] = value
    with pytest.raises(se.RegistryError, match=f"authority.{key} must be"):
        _registry(tmp_path, _rehash(raw))


@pytest.mark.parametrize("key", ["observation_only", "execution_authority", "risk_reservation", "trade_alerts"])
def test_every_authority_key_must_be_stated(tmp_path, key):
    raw = _epoch()
    del raw["definition"]["authority"][key]
    with pytest.raises(se.RegistryError, match=f"authority.{key} must be stated"):
        _registry(tmp_path, _rehash(raw))


@pytest.mark.parametrize("key", ["tradable", "active", "trading_authority", "proof_window_open"])
def test_unknown_authority_like_keys_are_refused(tmp_path, key):
    raw = _epoch()
    raw["definition"]["authority"][key] = False
    with pytest.raises(se.RegistryError, match="authority has unknown keys"):
        _registry(tmp_path, _rehash(raw))
    raw = _epoch()
    raw[key] = True  # same key at epoch level
    with pytest.raises(se.RegistryError, match="unknown keys"):
        _registry(tmp_path, raw)


def test_authority_must_be_an_object(tmp_path):
    raw = _epoch()
    raw["definition"]["authority"] = ["observation_only"]
    with pytest.raises(se.RegistryError, match="authority must be an object"):
        _registry(tmp_path, _rehash(raw))


def test_unknown_registry_and_oos_keys_are_refused(tmp_path):
    path = _write(tmp_path, {"schema": se.REGISTRY_SCHEMA, "epochs": [_epoch()], "active": True})
    with pytest.raises(se.RegistryError, match="unknown keys"):
        se.load_registry(path)
    raw = _epoch()
    raw["oos_reference"]["tradable"] = True
    with pytest.raises(se.RegistryError, match="oos_reference has unknown keys"):
        _registry(tmp_path, raw)
    raw = _epoch()
    raw["observation_only"] = True  # derived, never read from the file
    with pytest.raises(se.RegistryError, match="unknown keys"):
        _registry(tmp_path, raw)


def test_duplicate_json_object_keys_are_refused(tmp_path):
    text = json.dumps({"schema": se.REGISTRY_SCHEMA, "epochs": [_epoch()]})
    # A later duplicate would otherwise silently win.
    text = text.replace('"execution_authority": false', '"execution_authority": true, "execution_authority": false')
    assert text.count('"execution_authority"') == 2
    with pytest.raises(se.RegistryError, match="duplicate JSON key"):
        se.load_registry(_write_text(tmp_path, text))


def test_non_finite_json_constants_are_refused(tmp_path):
    text = json.dumps({"schema": se.REGISTRY_SCHEMA, "epochs": [_epoch()]}).replace("[1.0,", "[NaN,")
    with pytest.raises(se.RegistryError, match="non-finite"):
        se.load_registry(_write_text(tmp_path, text))


@pytest.mark.parametrize(
    "outcomes, match",
    [
        (["1.0", -1.0], "must be a number, not str"),
        ([True, -1.0], "must be a number, not bool"),
        ([None], "must be a number"),
        ([[1.0]], "must be a number"),
        ([{"r": 1.0}], "must be a number"),
        ([10**400], "out of float range"),
        ("1.0,-1.0", "non-empty list"),
        ({"a": 1.0}, "non-empty list"),
        (1.0, "non-empty list"),
        ([], "non-empty list"),
    ],
)
def test_malformed_oos_outcomes_are_refused(tmp_path, outcomes, match):
    raw = _epoch()
    raw["oos_reference"]["r_outcomes"] = outcomes
    with pytest.raises(se.RegistryError, match=match):
        _registry(tmp_path, raw)


def test_oos_reference_fields_must_be_real_strings(tmp_path):
    for key, value in (("source", 7), ("artifact_path", "  "), ("cost_model", " fees"), ("artifact_sha256", 5)):
        raw = _epoch()
        raw["oos_reference"][key] = value
        with pytest.raises(se.RegistryError):
            _registry(tmp_path, raw)
    raw = _epoch(oos_reference=["not", "an", "object"])
    with pytest.raises(se.RegistryError, match="oos_reference must be an object"):
        _registry(tmp_path, raw)


@pytest.mark.parametrize(
    "field, value",
    [
        ("source_commit", "a" * 40 + "\n"),
        ("source_commit", "A" * 40),
        ("strategy", "322\n"),
        ("epoch", "2026Q4_v1\n"),
        ("strategy", 322),
        ("epoch", None),
        ("preregistration_doc", "   "),
        ("preregistration_doc", "docs/prereg.md\n"),
        ("preregistration_doc", ["docs/prereg.md"]),
        ("supersedes", ["2026Q4_v0"]),
        ("supersedes", "2026Q4_v1"),
        ("notes", ["x"]),
        ("status", "frozen"),
        ("status", ["FROZEN"]),
    ],
)
def test_identifiers_and_text_fields_are_strict(tmp_path, field, value):
    raw = _epoch(**{field: value})
    with pytest.raises(se.RegistryError):
        _registry(tmp_path, raw)


def test_definition_sha256_must_be_a_string_hash(tmp_path):
    raw = _epoch()
    raw["definition_sha256"] = raw["definition_sha256"] + "\n"
    with pytest.raises(se.RegistryError, match="material change requires a new epoch"):
        _registry(tmp_path, raw)
    raw["definition_sha256"] = None
    with pytest.raises(se.RegistryError):
        _registry(tmp_path, raw)
    del raw["definition_sha256"]
    with pytest.raises(se.RegistryError, match="missing required keys"):
        se.load_registry(_write(tmp_path, {"schema": se.REGISTRY_SCHEMA, "epochs": [raw]}))


@pytest.mark.parametrize("value", ["60", True, None, [60], {"s": 60}])
def test_thresholds_must_be_finite_numbers(tmp_path, value):
    raw = _epoch(thresholds={"min_rr": value})
    with pytest.raises(se.RegistryError, match="finite number"):
        _registry(tmp_path, _rehash(raw))


@pytest.mark.parametrize(
    "value",
    ["9999-12-31T23:59:59-14:00", "0001-01-01T00:00:00+14:00", "2026-10-01T13:30:00", "not a date", 20261001],
)
def test_unrepresentable_or_naive_times_raise_registry_error(tmp_path, value):
    with pytest.raises(se.RegistryError):
        _registry(tmp_path, _epoch(effective_from=value))


def test_malformed_files_raise_registry_error_not_internal_errors(tmp_path):
    deep = "[" * 100_000 + "]" * 100_000
    for text in (deep, "{", "[]", '"x"', json.dumps({"schema": se.REGISTRY_SCHEMA, "epochs": {}})):
        with pytest.raises(se.RegistryError):
            se.load_registry(_write_text(tmp_path, text))
    with pytest.raises(se.RegistryError):
        se.load_registry(_write(tmp_path, {"schema": se.REGISTRY_SCHEMA, "epochs": ["x"]}))
    with pytest.raises(se.RegistryError):
        se.load_registry(_write(tmp_path, {"schema": se.REGISTRY_SCHEMA, "epochs": [], "notes": 3}))
    with pytest.raises(se.RegistryError, match="cannot read"):
        se.load_registry(tmp_path / "missing.json")


def test_loaded_definition_and_thresholds_are_read_only(tmp_path):
    raw = _epoch()
    raw["definition"]["setup"]["windows"] = ["09:30", "10:30"]
    epoch = _registry(tmp_path, _rehash(raw)).get("322", "2026Q4_v1")
    assert isinstance(epoch.definition, types.MappingProxyType)
    with pytest.raises(TypeError):
        epoch.definition["authority"]["execution_authority"] = True  # type: ignore[index]
    with pytest.raises(TypeError):
        epoch.definition["setup"] = {}  # type: ignore[index]
    with pytest.raises(TypeError):
        epoch.thresholds["min_rr"] = 0.1  # type: ignore[index]
    assert epoch.definition["setup"]["windows"] == ("09:30", "10:30")
    # Freezing does not change the hash, and the frozen form still hashes.
    assert se.definition_hash(epoch.definition, epoch.thresholds) == epoch.definition_sha256
    assert epoch.thresholds == {"min_rr": 1.0}


def test_mutating_the_source_mapping_after_load_cannot_change_the_epoch(tmp_path):
    raw = _epoch()
    definition = copy.deepcopy(raw["definition"])
    epoch = se.StrategyEpoch(
        strategy="322", epoch="e1", status=se.EpochStatus.DRAFT, definition=definition,
        thresholds={"min_rr": 1.0}, definition_sha256=raw["definition_sha256"], effective_from=None,
        effective_until=None, source_commit=None, preregistration_doc=None, oos_reference=None,
        supersedes=None, observation_only=True,
    )
    definition["authority"]["execution_authority"] = True
    assert epoch.definition["authority"]["execution_authority"] is False


_OBS_AUTHORITY = {
    "observation_only": True,
    "execution_authority": False,
    "risk_reservation": False,
    "trade_alerts": False,
}


def _direct(**kw) -> se.StrategyEpoch:
    base = dict(
        strategy="322", epoch="e1", status=se.EpochStatus.FROZEN, definition={"authority": dict(_OBS_AUTHORITY)},
        thresholds={}, definition_sha256="c" * 64, effective_from=datetime(2026, 10, 1, tzinfo=timezone.utc),
        effective_until=None, source_commit="a" * 40, preregistration_doc="p.md", oos_reference=None,
        supersedes=None, observation_only=True,
    )
    base.update(kw)
    return se.StrategyEpoch(**base)


def test_valid_direct_construction_is_accepted():
    epoch = _direct()
    assert se.assert_observation_only(epoch) is epoch
    assert se.validate_epochs([epoch]).get("322", "e1") is epoch


@pytest.mark.parametrize(
    "overrides, match",
    [
        ({"observation_only": False}, "observation_only must be true"),
        ({"observation_only": 1}, "observation_only must be true"),
        ({"observation_only": None}, "observation_only must be true"),
        ({"definition": {}}, "authority must be an object"),
        ({"definition": None}, "definition must be a mapping"),
        ({"definition": {"authority": {**_OBS_AUTHORITY, "execution_authority": True}}}, "execution_authority must be false"),
        ({"definition": {"authority": {**_OBS_AUTHORITY, "risk_reservation": True}}}, "risk_reservation must be false"),
        ({"definition": {"authority": {**_OBS_AUTHORITY, "trade_alerts": True}}}, "trade_alerts must be false"),
        ({"definition": {"authority": {**_OBS_AUTHORITY, "observation_only": False}}}, "observation_only must be true"),
        ({"definition": {"authority": {**_OBS_AUTHORITY, "execution_authority": 0}}}, "execution_authority must be false"),
        ({"definition": {"authority": {**_OBS_AUTHORITY, "tradable": False}}}, "unknown keys"),
        ({"definition": {"authority": {k: v for k, v in _OBS_AUTHORITY.items() if k != "trade_alerts"}}}, "trade_alerts must be stated"),
        ({"status": "DRAFT"}, "status must be an EpochStatus"),
        ({"status": None}, "status must be an EpochStatus"),
    ],
)
def test_direct_construction_cannot_bypass_authority_invariants(overrides, match):
    with pytest.raises(se.RegistryError, match=match):
        _direct(**overrides)


def test_post_construction_tampering_is_caught_at_validation_and_registry_boundaries():
    for field, value in (
        ("observation_only", False),
        ("definition", {"authority": {**_OBS_AUTHORITY, "execution_authority": True}}),
        ("status", "FROZEN"),
    ):
        epoch = _direct()
        object.__setattr__(epoch, field, value)  # bypasses __post_init__
        with pytest.raises(se.RegistryError):
            se.assert_observation_only(epoch)
        with pytest.raises(se.RegistryError):
            se.validate_epochs([epoch])
        with pytest.raises(se.RegistryError):
            se.EpochRegistry((epoch,))
    with pytest.raises(se.RegistryError):
        se.EpochRegistry(("not an epoch",))  # type: ignore[arg-type]


def test_validate_epochs_refuses_malformed_direct_construction():
    for bad in (_direct(supersedes=["x"]), _direct(supersedes="e1"), _direct(effective_from=None), _direct(strategy=3)):
        with pytest.raises(se.RegistryError):
            se.validate_epochs([bad])
    with pytest.raises(se.RegistryError):
        se.validate_epochs(["not an epoch"])  # type: ignore[list-item]


def test_resolve_epoch_fails_closed_on_malformed_running_definition(tmp_path):
    raw = _epoch()
    registry = _registry(tmp_path, raw)
    at = datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    cases = [
        dict(strategy="322", definition=raw["definition"], thresholds={"min_rr": float("nan")}),
        dict(strategy="322", definition=raw["definition"], thresholds={"min_rr": object()}),
        dict(strategy="322", definition={**raw["definition"], "sizing": {"qty": 9}}, thresholds=raw["thresholds"]),
        dict(strategy="322", definition=["setup"], thresholds=raw["thresholds"]),
        dict(strategy="322", definition=raw["definition"], thresholds=None),
        dict(strategy=["322"], definition=raw["definition"], thresholds=raw["thresholds"]),
    ]
    for kwargs in cases:
        res = se.resolve_epoch(registry, at=at, **kwargs)
        assert res.label == se.UNREGISTERED_EPOCH and not res.registered
    # The frozen (loaded) definition itself still resolves.
    epoch = registry.get("322", "2026Q4_v1")
    assert se.resolve_epoch(
        registry, strategy="322", definition=epoch.definition, thresholds=epoch.thresholds, at=at
    ).label == "2026Q4_v1"


def test_resolve_epoch_rejects_invalid_at_with_registry_error(tmp_path):
    raw = _epoch()
    registry = _registry(tmp_path, raw)
    for at in (datetime(2026, 10, 6), "2026-10-06T00:00:00Z", None):
        with pytest.raises(se.RegistryError):
            se.resolve_epoch(registry, strategy="322", definition=raw["definition"],
                             thresholds=raw["thresholds"], at=at)  # type: ignore[arg-type]


def test_record_labels_are_type_strict_and_ignore_draft_and_policy_epoch(tmp_path):
    registry = _registry(
        tmp_path,
        _epoch(),
        _epoch(epoch="draft_v2", status="DRAFT", thresholds={"min_rr": 3.0}, supersedes="2026Q4_v1"),
    )
    label = se.epoch_label_for_record
    assert label(registry, {"strategy": "322", "strategy_epoch": ""}) == se.LEGACY_UNVERSIONED
    assert label(registry, {"strategy": "322", "strategy_epoch": ["2026Q4_v1"]}) == se.UNREGISTERED_EPOCH
    assert label(registry, {"strategy": 322, "strategy_epoch": "2026Q4_v1"}) == se.UNREGISTERED_EPOCH
    assert label(registry, {"strategy_epoch": "2026Q4_v1"}) == se.UNREGISTERED_EPOCH
    assert label(registry, {"strategy": "322", "strategy_epoch": "draft_v2"}) == se.UNREGISTERED_EPOCH
    assert label(registry, ["strategy_epoch"]) == se.UNREGISTERED_EPOCH  # type: ignore[arg-type]
    # policy_epoch is never promoted to an epoch stamp.
    assert label(registry, {"strategy": "322", "policy_epoch": "2026Q4_v1"}) == se.LEGACY_UNVERSIONED


def _collector_policy_epoch() -> str:
    source = (Path(__file__).resolve().parents[1] / "scripts" / "options_122_prospective_collect.py").read_text()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "POLICY_EPOCH" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("collector POLICY_EPOCH not found")


def test_collector_policy_epoch_reconciles_with_registry_but_never_labels_rows():
    """The collector stamps rows with ``policy_epoch`` (not ``strategy_epoch``).

    Its value names the registered FROZEN options_122 epoch, but the rows carry
    no ``strategy``/``strategy_epoch``, so they read as LEGACY_UNVERSIONED and
    enter no epoch population until the collector stamps via ``resolve_epoch``.
    """
    registry = se.load_registry(REGISTRY)
    policy_epoch = _collector_policy_epoch()
    epoch = registry.get("options_122", policy_epoch)
    assert epoch is not None and epoch.status is se.EpochStatus.FROZEN
    for row in (
        {"record_type": "ARMED", "collector_id": "c", "policy_epoch": policy_epoch, "setup_id": "s"},
        {"record_type": "RESOLUTION", "policy_epoch": policy_epoch, "strategy": "options_122"},
    ):
        assert se.epoch_label_for_record(registry, row) == se.LEGACY_UNVERSIONED
