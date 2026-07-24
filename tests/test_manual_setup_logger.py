import json
from pathlib import Path

import pytest

from research.manual_setup_logger import (
    DuplicateRecordError,
    ValidationError,
    evidence_path,
    is_study_eligible,
    record_resolution,
    record_setup,
    setup_id_for,
)


SIGNAL_TS = "2026-07-23T13:30:00+00:00"


def _setup(**updates):
    value = {
        "strategy": "4HR Re-Trigger",
        "contract_version": "v1.0",
        "signal_timestamp": SIGNAL_TS,
        "instrument": "MNQ",
        "direction": "LONG",
        "study_phase": "post_activation",
        "original_bracket": {
            "entry": 23000,
            "stop": 22940,
            "t1": 23040,
            "t2": None,
        },
        "decision": "SKIPPED",
        "skip_reason": "preflight evidence only",
        "context": {
            "signa": {
                "available": True,
                "observed_at": SIGNAL_TS,
                "source": "operator_signa_screen",
                "data": {
                    "grade": "A",
                    "weekly_direction": "UP",
                    "data": {"direction": "LONG"},
                    "engine": {"direction": "BULLISH"},
                    "signa": {"action": "BUY"},
                },
            }
        },
        "provenance": {
            "source": "manual_morning_checklist",
            "recorded_by": "operator",
        },
    }
    value.update(updates)
    return value


def _observer_row(**updates):
    value = {
        "kind": "strategy_context_observation",
        "timestamp": SIGNAL_TS,
        "instrument": "MNQ",
        "supply_demand_confluence": {
            "available": True,
            "zone": "near_demand",
        },
        "vwap": {"value": 22995, "price_vs_vwap": "above"},
    }
    value.update(updates)
    return value


def _write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_setup_is_observe_only_append_only_and_has_stable_id(tmp_path):
    row = record_setup(_setup(), log_dir=tmp_path)
    saved = json.loads(evidence_path(tmp_path).read_text())

    assert row["setup_id"] == setup_id_for(_setup())
    assert saved == row
    assert row["observation_only"] is True
    assert row["gate_authoritative"] is False
    assert row["execution_authorized"] is False
    assert row["actual_execution"]["status"] == "NOT_TAKEN"
    assert row["shadow_outcome"]["status"] == "PENDING"
    assert row["context"]["zone"]["available"] is False
    assert row["context"]["signa"]["data"]["grade"] == "A"
    assert row["context"]["signa"]["internal_agreement"] is True
    assert row["context"]["signa"]["agreement_evaluable"] is True


@pytest.mark.parametrize(
    "directions, expected_agreement, expected_evaluable",
    [
        (
            ("LONG", "BULLISH", "BUY"),
            True,
            True,
        ),
        (
            ("WAIT", "NEUTRAL", "HOLD"),
            True,
            True,
        ),
        (
            ("WAIT", "BEARISH", "BUY"),
            False,
            True,
        ),
        (
            ("LONG", None, "BUY"),
            False,
            False,
        ),
        (
            ("LONG", "BULLISH", "AVOID"),
            False,
            False,
        ),
    ],
)
def test_signa_internal_agreement_is_derived_from_three_fields(
    tmp_path, directions, expected_agreement, expected_evaluable
):
    data_direction, engine_direction, signa_action = directions
    payload = _setup(
        context={
            "signa": {
                "available": True,
                "observed_at": SIGNAL_TS,
                "source": "signa_dual_timeframe_snapshot",
                "data": {
                    "data": {"direction": data_direction},
                    "engine": {"direction": engine_direction},
                    "signa": {"action": signa_action},
                },
            }
        }
    )
    row = record_setup(payload, log_dir=tmp_path)
    signa = row["context"]["signa"]
    assert signa["internal_agreement"] is expected_agreement
    assert signa["agreement_evaluable"] is expected_evaluable
    assert signa["direction_components"]["data.direction"]["raw"] == (
        data_direction
    )


def test_missing_signa_is_not_misclassified_as_internal_conflict(tmp_path):
    row = record_setup(_setup(context={}), log_dir=tmp_path)
    signa = row["context"]["signa"]
    assert signa["available"] is False
    assert signa["internal_agreement"] is False
    assert signa["agreement_evaluable"] is False


def test_duplicate_signal_is_rejected_even_if_levels_change(tmp_path):
    record_setup(_setup(), log_dir=tmp_path)
    changed = _setup(
        original_bracket={"entry": 23001, "stop": 22941, "t1": 23041, "t2": None}
    )
    with pytest.raises(DuplicateRecordError):
        record_setup(changed, log_dir=tmp_path)


def test_exact_context_join_copies_zone_vwap_and_hash_but_not_signa(tmp_path):
    context_path = tmp_path / "strategy_context_observations.jsonl"
    _write_rows(context_path, [_observer_row()])
    payload = _setup(context={})

    row = record_setup(payload, log_dir=tmp_path, context_path=context_path)

    assert row["context"]["zone"]["data"]["zone"] == "near_demand"
    assert row["context"]["vwap"]["data"]["price_vs_vwap"] == "above"
    assert row["context"]["signa"]["available"] is False
    assert row["context"]["signa"]["internal_agreement"] is False
    assert row["context"]["signa"]["agreement_evaluable"] is False
    assert row["context"]["signa"]["missing_reason"] == (
        "observer_does_not_record_signa"
    )
    assert row["context"]["joined_observer"]["match"] == (
        "exact_instrument_and_timestamp"
    )
    assert len(row["context"]["joined_observer"]["row_sha256"]) == 64


def test_context_join_rejects_nearest_or_duplicate_match(tmp_path):
    context_path = tmp_path / "strategy_context_observations.jsonl"
    _write_rows(
        context_path,
        [_observer_row(timestamp="2026-07-23T13:15:00+00:00")],
    )
    with pytest.raises(ValidationError, match="no exact"):
        record_setup(
            _setup(context={}), log_dir=tmp_path, context_path=context_path
        )

    _write_rows(context_path, [_observer_row(), _observer_row()])
    with pytest.raises(ValidationError, match="multiple exact"):
        record_setup(
            _setup(context={}), log_dir=tmp_path, context_path=context_path
        )


@pytest.mark.parametrize(
    "update, message",
    [
        ({"signal_timestamp": "2026-07-23T13:30:00"}, "timezone"),
        ({"instrument": "NQ"}, "instrument"),
        ({"direction": "UP"}, "direction"),
        ({"decision": "SKIPPED", "skip_reason": None}, "skip_reason"),
        (
            {
                "original_bracket": {
                    "entry": 23000,
                    "stop": 23010,
                    "t1": 23040,
                    "t2": None,
                }
            },
            "inconsistent",
        ),
    ],
)
def test_setup_validation_fails_closed(tmp_path, update, message):
    with pytest.raises(ValidationError, match=message):
        record_setup(_setup(**update), log_dir=tmp_path)


def test_manual_context_records_age_without_claiming_freshness(tmp_path):
    context = {
        "zone": {
            "available": True,
            "observed_at": "2026-07-23T13:15:00+00:00",
            "source": "operator_chart",
            "data": {"location": "demand"},
        }
    }
    row = record_setup(_setup(context=context), log_dir=tmp_path)
    assert row["context"]["zone"]["age_seconds_at_signal"] == 900
    assert row["context"]["zone"]["causal_at_signal"] is True
    assert row["context"]["zone"]["exact_signal_timestamp"] is False


def test_resolution_for_skipped_setup_preserves_original_bracket(tmp_path):
    setup = record_setup(_setup(), log_dir=tmp_path)
    resolution = record_resolution(
        {
            "setup_id": setup["setup_id"],
            "actual_execution": {"fill": None},
            "shadow_outcome": {
                "result": "T1_FIRST",
                "resolved_at": "2026-07-23T14:15:00Z",
                "exit_price": 23040,
                "t1_hit": True,
            },
            "provenance": {
                "source": "five_min_replay",
                "recorded_by": "operator",
            },
        },
        log_dir=tmp_path,
    )

    assert resolution["actual_execution"]["status"] == "NOT_TAKEN"
    assert resolution["shadow_outcome"]["original_bracket"] == (
        setup["original_bracket"]
    )
    assert len(evidence_path(tmp_path).read_text().splitlines()) == 2


def test_taken_resolution_requires_fill_and_records_exact_cost_total(tmp_path):
    payload = _setup(decision="TAKEN", skip_reason=None)
    setup = record_setup(payload, log_dir=tmp_path)
    with pytest.raises(ValidationError, match="fill is required"):
        record_resolution(
            {
                "setup_id": setup["setup_id"],
                "actual_execution": {},
                "shadow_outcome": {
                    "result": "T1_FIRST",
                    "resolved_at": "2026-07-23T14:00:00Z",
                },
                "provenance": {"source": "manual", "recorded_by": "operator"},
            },
            log_dir=tmp_path,
        )

    resolution = record_resolution(
        {
            "setup_id": setup["setup_id"],
            "actual_execution": {
                "fill": {
                    "price": 23001,
                    "contracts": 1,
                    "filled_at": "2026-07-23T13:31:00Z",
                },
                "costs": {
                    "commission": 1.24,
                    "fees": 0.10,
                    "slippage": 4.00,
                },
            },
            "shadow_outcome": {
                "result": "STOP_FIRST",
                "resolved_at": "2026-07-23T14:00:00Z",
                "stop_hit": True,
            },
            "provenance": {"source": "manual", "recorded_by": "operator"},
        },
        log_dir=tmp_path,
    )
    assert resolution["actual_execution"]["costs"]["total"] == pytest.approx(5.34)


def test_resolution_rejects_fractional_contracts_and_string_booleans(tmp_path):
    setup = record_setup(_setup(decision="TAKEN", skip_reason=None), log_dir=tmp_path)
    base = {
        "setup_id": setup["setup_id"],
        "actual_execution": {
            "fill": {
                "price": 23001,
                "contracts": 1.5,
                "filled_at": "2026-07-23T13:31:00Z",
            },
            "costs": {"commission": 0, "fees": 0, "slippage": 0},
        },
        "shadow_outcome": {
            "result": "T1_FIRST",
            "resolved_at": "2026-07-23T14:00:00Z",
            "t1_hit": True,
        },
        "provenance": {"source": "manual", "recorded_by": "operator"},
    }
    with pytest.raises(ValidationError, match="whole number"):
        record_resolution(base, log_dir=tmp_path)

    base["actual_execution"]["fill"]["contracts"] = 1
    base["actual_execution"]["costs"] = {
        "commission": 0,
        "fees": 0,
        "slippage": 0,
    }
    base["shadow_outcome"]["t1_hit"] = "yes"
    with pytest.raises(ValidationError, match="must be boolean"):
        record_resolution(base, log_dir=tmp_path)


@pytest.mark.parametrize(
    "shadow, message",
    [
        (
            {
                "result": "STOP_FIRST",
                "resolved_at": "2026-07-23T14:00:00Z",
                "stop_hit": False,
            },
            "STOP_FIRST requires",
        ),
        (
            {
                "result": "T1_FIRST",
                "resolved_at": "2026-07-23T14:00:00Z",
                "t1_hit": False,
            },
            "T1_FIRST requires",
        ),
        (
            {
                "result": "NEITHER_BY_CUTOFF",
                "resolved_at": "2026-07-23T20:00:00Z",
                "stop_hit": True,
            },
            "all hit flags",
        ),
        (
            {
                "result": "T1_FIRST",
                "resolved_at": "2026-07-23T14:00:00Z",
                "t1_hit": True,
                "t2_hit": True,
            },
            "without a T2",
        ),
    ],
)
def test_shadow_result_and_hit_flags_must_be_consistent(tmp_path, shadow, message):
    setup = record_setup(_setup(), log_dir=tmp_path)
    with pytest.raises(ValidationError, match=message):
        record_resolution(
            {
                "setup_id": setup["setup_id"],
                "actual_execution": {"fill": None},
                "shadow_outcome": shadow,
                "provenance": {"source": "manual", "recorded_by": "operator"},
            },
            log_dir=tmp_path,
        )


def test_t2_first_requires_both_target_flags(tmp_path):
    setup = record_setup(
        _setup(
            original_bracket={
                "entry": 23000,
                "stop": 22940,
                "t1": 23040,
                "t2": 23080,
            }
        ),
        log_dir=tmp_path,
    )
    payload = {
        "setup_id": setup["setup_id"],
        "actual_execution": {"fill": None},
        "shadow_outcome": {
            "result": "T2_FIRST",
            "resolved_at": "2026-07-23T14:30:00Z",
            "t1_hit": True,
            "t2_hit": False,
        },
        "provenance": {"source": "manual", "recorded_by": "operator"},
    }
    with pytest.raises(ValidationError, match="requires both"):
        record_resolution(payload, log_dir=tmp_path)

    payload["shadow_outcome"]["t2_hit"] = True
    result = record_resolution(payload, log_dir=tmp_path)
    assert result["shadow_outcome"]["result"] == "T2_FIRST"


def test_resolution_rejects_impossible_timestamp_order(tmp_path):
    skipped = record_setup(_setup(), log_dir=tmp_path)
    with pytest.raises(ValidationError, match="cannot precede signal_timestamp"):
        record_resolution(
            {
                "setup_id": skipped["setup_id"],
                "actual_execution": {"fill": None},
                "shadow_outcome": {
                    "result": "NEITHER_BY_CUTOFF",
                    "resolved_at": "2026-07-23T13:00:00Z",
                },
                "provenance": {"source": "manual", "recorded_by": "operator"},
            },
            log_dir=tmp_path,
        )

    taken = record_setup(
        _setup(
            strategy="12HR Miyagi",
            decision="TAKEN",
            skip_reason=None,
        ),
        log_dir=tmp_path,
    )
    with pytest.raises(ValidationError, match="filled_at cannot precede"):
        record_resolution(
            {
                "setup_id": taken["setup_id"],
                "actual_execution": {
                    "fill": {
                        "price": 23001,
                        "contracts": 1,
                        "filled_at": "2026-07-23T13:00:00Z",
                    },
                    "costs": {"commission": 0, "fees": 0, "slippage": 0},
                },
                "shadow_outcome": {
                    "result": "T1_FIRST",
                    "resolved_at": "2026-07-23T14:00:00Z",
                    "t1_hit": True,
                },
                "provenance": {"source": "manual", "recorded_by": "operator"},
            },
            log_dir=tmp_path,
        )


def test_resolution_is_append_only_and_deduped(tmp_path):
    setup = record_setup(_setup(), log_dir=tmp_path)
    payload = {
        "setup_id": setup["setup_id"],
        "actual_execution": {"fill": None},
        "shadow_outcome": {
            "result": "NEITHER_BY_CUTOFF",
            "resolved_at": "2026-07-23T20:00:00Z",
        },
        "provenance": {"source": "manual", "recorded_by": "operator"},
    }
    record_resolution(payload, log_dir=tmp_path)
    with pytest.raises(DuplicateRecordError):
        record_resolution(payload, log_dir=tmp_path)


def test_module_has_no_execution_or_broker_dependencies():
    source = Path("research/manual_setup_logger.py").read_text()
    assert "tradovate" not in source.lower()
    assert "paper_broker" not in source
    assert "signal_engine" not in source


# ─── Fix 1: full matched observer row embedded (2026-07-24 preflight FAIL) ──


def _full_observer_row(**updates):
    """Shaped like a real deployed strategy_context_observations.jsonl row
    (context/strategy_context_observer.py) -- includes the 8 fields the
    real-row preflight test found were reachable in the source but absent
    from the prior joined-context contract."""
    value = {
        "kind": "strategy_context_observation",
        "timestamp": SIGNAL_TS,
        "instrument": "MNQ",
        "session": "new_york",
        "close": 23005.5,
        "timeframe": "15",
        "supply_demand_confluence": {"available": True, "zone": "near_demand"},
        "vwap": {"value": 22995, "price_vs_vwap": "above"},
        "market_condition": "TRENDING",
        "structural_regime": {"structural_market_condition": "STRUCTURAL_TREND"},
        "trend_persistence": {"direction": "UP", "same_direction_closes": 3},
        "mnq_mes_agreement": {"agrees": True, "pair": "MES"},
        "overnight_range_location": {"available": True, "location": "inside"},
        "key_level_confluence": {"available": True, "nearest": {"name": "pdh"}},
        "impulse_state": {"direction": "UP", "state": "impulse"},
        "gex": {"gex_flip": 5000.0, "gex_regime": "positive"},
    }
    value.update(updates)
    return value


def test_join_embeds_the_full_matched_observer_row_verbatim(tmp_path):
    """The core Fix 1 regression: every field on the source row -- not a
    hand-picked subset -- must be reachable from the joined manual setup
    evidence without an analyst reopening the raw context file."""
    context_path = tmp_path / "strategy_context_observations.jsonl"
    observer_row = _full_observer_row()
    _write_rows(context_path, [observer_row])

    row = record_setup(_setup(context={}), log_dir=tmp_path, context_path=context_path)

    embedded = row["context"]["joined_observer"]["row"]
    assert embedded == observer_row
    for field in (
        "market_condition", "structural_regime", "trend_persistence",
        "mnq_mes_agreement", "overnight_range_location",
        "key_level_confluence", "impulse_state", "gex",
    ):
        assert field in embedded, f"{field} missing from embedded observer row"
        assert embedded[field] == observer_row[field]
    # The convenience zone/vwap snapshots must still work unchanged.
    assert row["context"]["zone"]["data"]["zone"] == "near_demand"
    assert row["context"]["vwap"]["data"]["price_vs_vwap"] == "above"


def test_embedded_row_hash_matches_row_sha256_provenance(tmp_path):
    """Hash provenance must still verify correctly after embedding the full
    row: row_sha256 is computed from, and must equal the hash of, the exact
    same row object now embedded at joined_observer.row."""
    import hashlib
    import json as _json

    context_path = tmp_path / "strategy_context_observations.jsonl"
    observer_row = _full_observer_row()
    _write_rows(context_path, [observer_row])

    row = record_setup(_setup(context={}), log_dir=tmp_path, context_path=context_path)
    joined = row["context"]["joined_observer"]

    recomputed = hashlib.sha256(
        _json.dumps(joined["row"], separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    assert recomputed == joined["row_sha256"]
    assert len(joined["row_sha256"]) == 64


# ─── Fix 2: required, enum-validated study_phase ────────────────────────────


def test_study_phase_is_required(tmp_path):
    payload = _setup()
    del payload["study_phase"]
    with pytest.raises(ValidationError, match="study_phase"):
        record_setup(payload, log_dir=tmp_path)


def test_study_phase_invalid_value_rejected(tmp_path):
    with pytest.raises(ValidationError, match="study_phase"):
        record_setup(_setup(study_phase="active"), log_dir=tmp_path)
    with pytest.raises(ValidationError, match="study_phase"):
        record_setup(_setup(study_phase=""), log_dir=tmp_path)
    with pytest.raises(ValidationError, match="study_phase"):
        record_setup(_setup(study_phase=None), log_dir=tmp_path)


def test_study_phase_preflight_persists_and_is_excluded_from_study(tmp_path):
    row = record_setup(_setup(study_phase="preflight"), log_dir=tmp_path)
    assert row["study_phase"] == "preflight"
    assert is_study_eligible(row) is False


def test_study_phase_post_activation_persists_and_is_study_eligible(tmp_path):
    row = record_setup(_setup(study_phase="post_activation"), log_dir=tmp_path)
    assert row["study_phase"] == "post_activation"
    assert is_study_eligible(row) is True


def test_legacy_record_without_study_phase_field_is_not_study_eligible():
    """A record written before this field existed (e.g. by an older schema
    version) has no study_phase key at all. Downstream analysis must treat
    that as excluded, never implicitly eligible -- this is the single
    source of truth every analysis script should call instead of
    reimplementing the rule (and getting the fail-open case wrong)."""
    legacy_record = {
        "schema_version": "manual_setup.v1",
        "kind": "manual_setup",
        "setup_id": "ms_legacy0000000000000000",
        "strategy": "orb_reclaim",
        # no "study_phase" key present at all
    }
    assert "study_phase" not in legacy_record
    assert is_study_eligible(legacy_record) is False
