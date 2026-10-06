from __future__ import annotations

import json
from datetime import timedelta

import pytest

from options_evidence import research_features as rf
from options_evidence import signal as sg
from options_evidence.strategy_epochs import load_registry
from tests.test_options_prospective_signal import T0, opened

TRIG = T0 + timedelta(minutes=10)


def triggered():
    journal = sg.SignalJournal()
    s = opened(journal)
    return journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.TRIGGERED,
                                         market_time=TRIG, detected_at=TRIG + timedelta(seconds=3),
                                         reason="break"))


def all_factors(**observed):
    out = []
    for name in rf.CANDIDATE_FACTORS:
        if name in observed:
            value, as_of = observed[name]
            out.append(rf.FactorValue(name, rf.FactorStatus.OBSERVED, value, as_of, source="test"))
        else:
            out.append(rf.FactorValue(name, rf.FactorStatus.UNAVAILABLE, reason="not captured"))
    return out


def test_row_requires_every_factor_stated_and_refuses_lookahead():
    s = triggered()
    row = rf.build_feature_row(s, all_factors(gex_regime=("NEG_GAMMA", TRIG)))
    assert row["factors"]["gex_regime"]["status"] == "OBSERVED"
    assert row["factors"]["signa"]["status"] == "UNAVAILABLE"
    assert row["trade_authority"] is False
    with pytest.raises(rf.ResearchError, match="look-ahead"):
        rf.build_feature_row(s, all_factors(gex_regime=("NEG_GAMMA", TRIG + timedelta(minutes=1))))
    with pytest.raises(rf.ResearchError, match="not stated"):
        rf.build_feature_row(s, all_factors()[:-1])


def test_factor_values_cannot_be_silent_or_unknown():
    with pytest.raises(rf.ResearchError, match="unknown factor"):
        rf.FactorValue("secret_sauce", rf.FactorStatus.UNAVAILABLE, reason="x")
    with pytest.raises(rf.ResearchError, match="needs a reason"):
        rf.FactorValue("signa", rf.FactorStatus.UNAVAILABLE)
    with pytest.raises(rf.ResearchError, match="needs value"):
        rf.FactorValue("signa", rf.FactorStatus.OBSERVED, None, TRIG, "x")


def test_1089_rating_is_a_factor_under_test_not_an_outcome():
    s = triggered()
    row = rf.build_feature_row(s, all_factors(observation_rating_1089=(72.0, TRIG)))
    assert "observation_rating_1089" in row["factors"]
    assert "result_r" not in row


def test_scaffold_offers_no_score():
    public = {n for n in dir(rf) if not n.startswith("_")}
    assert not {n for n in public if any(w in n.lower() for w in ("score", "weight", "rank", "grade"))}


def _records(s, *, epoch=None, data="VALID", result=1.0):
    signal_record = sg.to_record(s)
    if epoch is not None:
        signal_record = {**signal_record, "strategy_epoch": epoch}
    outcome = {
        "signal_id": s.signal_id,
        "data_integrity": data,
        "signal_integrity": "VALID",
        "result_r": {"value": result, "status": "DERIVED" if result is not None else "UNAVAILABLE",
                     "reason": "" if result is not None else "gap"},
    }
    row = rf.build_feature_row(s, all_factors())
    return signal_record, outcome, row


def test_population_is_clean_registered_prospective_only():
    registry = load_registry()
    s = triggered()  # options_122 / 122-IEX-E1 (registered)
    joined = [
        _records(s),
        _records(s, epoch=""),
        _records(s, epoch="122-IEX-E9"),
        _records(s, data="DEGRADED"),
        _records(s, result=None),
    ]
    joined[1] = ({k: v for k, v in joined[1][0].items() if k != "strategy_epoch"}, *joined[1][1:])
    pop = rf.research_population(registry, joined)
    assert len(pop.rows) == 1
    assert pop.rows[0]["result_r"] == 1.0
    assert pop.excluded == {
        "integrity": 1,
        "legacy_unversioned": 1,
        "result_unavailable": 1,
        "unregistered_epoch": 1,
    }
    json.dumps(pop.rows)  # serialisable
