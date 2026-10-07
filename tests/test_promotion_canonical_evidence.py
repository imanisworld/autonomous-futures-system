"""U5: promotion / demo qualification consume canonical evidence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.project_check import canonical_promotion_evidence as cpe
from ops.project_check.demo_qualification import _apply_canonical_demo_facts
from ops.project_check.promotion import (
    HARD_CAP_CLASSIFICATION,
    _apply_canonical_facts,
    build_promotion_report,
)
from tests.canonical_bundle_helpers import make_promotion_bundle


@pytest.fixture(autouse=True)
def _pinned_runtime(monkeypatch):
    for name in (
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
        "ENTRY_FILL_MODEL",
        "MAX_CONTRACTS_HARD_CAP",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES", "16")
    monkeypatch.setenv("ENTRY_FILL_MODEL", "ioc_limit")
    monkeypatch.setenv("MAX_CONTRACTS_HARD_CAP", "1")


def _packet(bundles: list[str] | None, **overrides) -> dict:
    payload = {
        "identity_parity": {
            "candidate_identity_parity": True,
            "direction_parity": True,
            "entry_stop_target_parity": True,
            "timeframe_parity": True,
            "lookahead_or_partial_bar_dependency": False,
        },
        "runtime_parity": {"replay_live_logic_confirmed": True},
        "execution_context_claimed": {
            "instrument": "MNQ",
            "entry_fill_model": "ioc_limit",
            "entry_tolerance_ticks": 32,
            "contract_qty": 1,
            "commission_slippage_assumptions": "frozen execution_assumptions",
        },
        "stated_classification": "PROMISING BUT UNPROVEN",
    }
    if bundles is not None:
        payload["canonical_evidence"] = {"bundles": bundles}
    for key, value in overrides.items():
        payload[key] = value
    return payload


def _report(tmp_path: Path, payload: dict) -> dict:
    path = tmp_path / "facts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return build_promotion_report(strategy="example", repo_root=tmp_path, evidence_path=path)


@pytest.fixture
def bundle(tmp_path):
    rel, _ = make_promotion_bundle(tmp_path)
    return rel


def test_canonical_bundle_drives_a_passing_report(tmp_path, bundle):
    report = _report(tmp_path, _packet([bundle]))
    assert report["gate_pass"] is True, report["classification"]["blockers"]
    derived = report["canonical_evidence"]["derived"]
    assert derived["execution"]["fills"] == 40
    assert report["execution"]["resolved_outcomes"] == 40
    assert report["research_result"]["sample"] == 40
    assert report["identity_parity"]["causal_data_availability"] is True
    assert report["quantity_evidence"]["source"] == "canonical_evidence"
    assert derived["untouched_oos_proven"] is True


def test_author_booleans_alone_cannot_pass_without_canonical_evidence(tmp_path):
    payload = _packet(
        None,
        execution={
            "entry_attempts": 40,
            "entry_attempt_contract_quantities": [1] * 40,
            "fills": 40,
            "cancellations": 0,
            "rejects_or_known_no_fills": 0,
            "resolved_outcomes": 40,
            "legitimately_open": 0,
        },
    )
    payload["identity_parity"]["causal_data_availability"] = True
    report = _report(tmp_path, payload)
    assert report["gate_pass"] is False
    assert any("canonical evidence missing" in b for b in report["classification"]["blockers"])
    assert report["classification"]["effective_classification"] == HARD_CAP_CLASSIFICATION


@pytest.mark.parametrize("stated", ["PROMISING BUT UNPROVEN", "VALIDATED"])
def test_positive_classification_never_survives_a_hard_blocker(tmp_path, bundle, stated):
    payload = _packet([bundle], stated_classification=stated)
    payload["identity_parity"]["lookahead_or_partial_bar_dependency"] = True
    report = _report(tmp_path, payload)
    assert report["gate_pass"] is False
    assert report["ok"] is False
    assert report["classification"]["effective_classification"] == HARD_CAP_CLASSIFICATION


def test_negative_stated_classification_is_kept(tmp_path):
    report = _report(tmp_path, _packet(None, stated_classification="BROKEN"))
    assert report["classification"]["effective_classification"] == "BROKEN"


def test_supplied_counts_contradicting_canonical_evidence_block(tmp_path, bundle):
    payload = _packet([bundle], execution={"fills": 41, "entry_attempts": 41})
    report = _report(tmp_path, payload)
    blockers = report["classification"]["blockers"]
    assert report["gate_pass"] is False
    assert any("execution.fills=41 contradicts canonical evidence 40" in b for b in blockers)


def test_supplied_research_result_must_match_canonical(tmp_path, bundle):
    derived = _report(tmp_path, _packet([bundle]))["canonical_evidence"]["derived"]
    good = _packet([bundle], research_result=dict(derived["research_result"]))
    assert _report(tmp_path, good)["gate_pass"] is True
    bad = _packet([bundle], research_result={"net_pnl": derived["research_result"]["net_pnl"] + 5})
    report = _report(tmp_path, bad)
    assert report["gate_pass"] is False
    assert any("research_result.net_pnl" in b for b in report["classification"]["blockers"])


@pytest.mark.parametrize(
    "field,value,needle",
    [
        ("instrument", "MES", "claimed instrument"),
        ("contract_qty", 2, "do not all match claimed contract_qty"),
        ("entry_fill_model", "market", "contradicts the canonical evidence execution assumption"),
    ],
)
def test_claimed_execution_context_must_match_evidence(tmp_path, bundle, field, value, needle):
    payload = _packet([bundle])
    payload["execution_context_claimed"][field] = value
    report = _report(tmp_path, payload)
    assert report["gate_pass"] is False
    assert any(needle in b for b in report["classification"]["blockers"]), report[
        "classification"
    ]["blockers"]


def test_causal_false_attestation_still_blocks(tmp_path, bundle):
    payload = _packet([bundle])
    payload["identity_parity"]["causal_data_availability"] = False
    report = _report(tmp_path, payload)
    assert report["gate_pass"] is False


@pytest.mark.parametrize(
    "field",
    [
        "candidate_identity_parity",
        "direction_parity",
        "entry_stop_target_parity",
        "timeframe_parity",
        "lookahead_or_partial_bar_dependency",
    ],
)
def test_attested_only_facts_remain_required(tmp_path, bundle, field):
    payload = _packet([bundle])
    payload["identity_parity"].pop(field)
    assert _report(tmp_path, payload)["gate_pass"] is False


def test_tampered_bundle_is_not_promotion_quality(tmp_path, bundle):
    raw = tmp_path / bundle / "candidate_raw.json"
    payload = json.loads(raw.read_text())
    payload["members"][0]["net_pnl"] = 999.0
    raw.write_text(json.dumps(payload))
    report = _report(tmp_path, _packet([bundle]))
    assert report["gate_pass"] is False
    assert any("not PROMOTION_QUALITY" in b for b in report["classification"]["blockers"])


@pytest.mark.parametrize("bundles", [[], [""], ["docs/research-evidence/T-missing"]])
def test_missing_or_unknown_bundle_blocks(tmp_path, bundles):
    report = _report(tmp_path, _packet(bundles))
    assert report["gate_pass"] is False


def test_duplicate_bundle_listing_blocks(tmp_path, bundle):
    report = _report(tmp_path, _packet([bundle, bundle]))
    assert any("more than once" in b for b in report["classification"]["blockers"])


def test_multi_instrument_evidence_blocks():
    derived = {
        "execution": {"fills": 2},
        "research_result": {"net_pnl": 1.0, "win_rate": None, "profit_factor": None, "sample": 2},
        "causal_data_availability": True,
        "instruments": ["MES", "MNQ"],
        "entry_fill_model": "market",
    }
    *_, blockers = _apply_canonical_facts(
        derived,
        execution={},
        identity_parity={},
        research_result={},
        execution_context_claimed={"instrument": "MNQ"},
    )
    assert any("exactly one instrument" in b for b in blockers)


def test_cli_exit_status_is_nonzero_without_canonical_evidence(tmp_path):
    from scripts.project_check import main

    facts = tmp_path / "facts.json"
    facts.write_text(json.dumps(_packet(None)))
    code = main(["promotion", "--strategy", "x", "--evidence-file", str(facts), "--json"])
    assert code != 0


# ─── Demo qualification facts ───────────────────────────────────────────────


def _derived(**overrides) -> dict:
    base = {
        "execution": {"fills": 40, "resolved_outcomes": 40},
        "futures_replay_path": True,
        "execution_model": {
            "entry_fill_model": "market",
            "pessimistic_same_bar": True,
            "adverse_slippage_ticks": 1.0,
            "commission_round_turn_dollars_per_contract": 1.48,
        },
        "untouched_oos_proven": True,
        "code_sha": "c" * 40,
    }
    base.update(overrides)
    return base


def test_demo_derives_replay_path_and_realism_from_canonical_evidence():
    blockers: list[str] = []
    merged = _apply_canonical_demo_facts({}, _derived(), blockers)
    assert blockers == []
    assert merged["canonical_replay"]["real_paper_broker"] is True
    assert merged["execution_realism"]["commission_round_turn_dollars"] == 1.48
    assert merged["execution_realism"]["pessimistic_same_bar"] is True
    assert merged["validation"]["untouched_validation_window"] is True
    assert merged["replay_provenance"]["code_sha"] == "c" * 40
    assert merged["execution"]["resolved_outcomes"] == 40


def test_demo_untouched_window_claim_needs_an_oos_bundle():
    blockers: list[str] = []
    merged = _apply_canonical_demo_facts(
        {"validation": {"untouched_validation_window": True}},
        _derived(untouched_oos_proven=False),
        blockers,
    )
    assert merged["validation"]["untouched_validation_window"] is False
    assert any("untouched_oos canonical bundle" in b for b in blockers)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("execution_realism", "commission_round_turn_dollars", 2.5),
        ("execution_realism", "baseline_adverse_slippage_ticks", 2),
        ("canonical_replay", "real_paper_broker", False),
        ("replay_provenance", "code_sha", "d" * 40),
    ],
)
def test_demo_supplied_facts_contradicting_evidence_block(section, key, value):
    blockers: list[str] = []
    _apply_canonical_demo_facts({section: {key: value}}, _derived(), blockers)
    assert any(f"{section}.{key}" in b for b in blockers), blockers


def test_demo_without_canonical_evidence_is_unchanged_and_blocked_upstream():
    blockers: list[str] = []
    payload = {"validation": {"untouched_validation_window": True}}
    assert _apply_canonical_demo_facts(payload, None, blockers) == payload
    assert blockers == []


def test_attested_only_facts_are_listed():
    assert "identity_parity.lookahead_or_partial_bar_dependency" in cpe.ATTESTED_ONLY_FACTS


# ─── Cross-bundle strategy identity (U4 integration) ────────────────────────


def _second_bundle_with_strategy(tmp_path, bundle, monkeypatch, strategy_identity):
    import shutil
    from dataclasses import replace

    from ops import evidence_identity

    second = bundle + "-copy"
    shutil.copytree(tmp_path / bundle, tmp_path / second)
    original = evidence_identity.classify_evidence_bundle

    def classify(root, bundle_dir, **kwargs):
        if Path(bundle_dir).name.endswith("-copy"):
            result = original(root, root / bundle, **kwargs)
            return replace(
                result, identity={**result.identity, "strategy_identity": strategy_identity}
            )
        return original(root, bundle_dir, **kwargs)

    monkeypatch.setattr(evidence_identity, "classify_evidence_bundle", classify)
    return second


def test_bundles_with_different_strategy_identities_block(tmp_path, bundle, monkeypatch):
    second = _second_bundle_with_strategy(tmp_path, bundle, monkeypatch, "other_strategy")
    facts = cpe.derive_canonical_facts(tmp_path, {"bundles": [bundle, second]})
    assert any("disagree on strategy_identity" in b for b in facts["blockers"])
    assert facts["derived"]["strategy_identity"] is None


def test_bundles_sharing_strategy_identity_do_not_block_on_it(tmp_path, bundle, monkeypatch):
    second = _second_bundle_with_strategy(tmp_path, bundle, monkeypatch, "example")
    facts = cpe.derive_canonical_facts(tmp_path, {"bundles": [bundle, second]})
    assert not any("strategy_identity" in b for b in facts["blockers"])
    assert facts["derived"]["strategy_identity"] == "example"
