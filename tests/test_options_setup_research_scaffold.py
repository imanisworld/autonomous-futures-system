from __future__ import annotations

import json
from datetime import timedelta

import pytest

from options_evidence import research_features as rf
from options_evidence import signal as sg
from tests.test_options_prospective_signal import (
    CATCH_EPOCH,
    CATCH_STRATEGY,
    REGISTRY,
    T0,
    caught,
    identity,
    opened,
    trigger,
)

TRIG = T0 + timedelta(minutes=10)


def triggered(ticker: str | None = None):
    """A verified prospective catch; distinct tickers give distinct signals."""
    journal = sg.SignalJournal(REGISTRY)
    if ticker is None:
        return caught(journal, at=TRIG)
    s = opened(journal, strategy=CATCH_STRATEGY, strategy_epoch=CATCH_EPOCH, identity=identity(ticker=ticker))
    s = trigger(journal, s, at=TRIG)
    s = journal.append(sg.observation_event(journal, s.signal_id, detected_at=TRIG + timedelta(seconds=4),
                                            prospective_catch=True, capture_late=False, gap_through=False,
                                            trigger_source="alpaca_iex"))
    return journal.append(sg.integrity_event(journal, s.signal_id, detected_at=TRIG + timedelta(seconds=4),
                                             signal_integrity="VALID", data_integrity="VALID"))


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


def _measured(value=None, status="UNAVAILABLE"):
    return {
        "value": value if status in ("OBSERVED", "DERIVED") else None,
        "status": status,
        "reason": "" if status in ("OBSERVED", "DERIVED") else "not measured",
        "source": "test" if status == "OBSERVED" else "",
    }


def _records(s, *, epoch=None, data="VALID", result=1.0):
    signal_record = sg.to_record(s)
    if epoch is not None:
        signal_record = {**signal_record, "strategy_epoch": epoch}
    outcome = {
        "schema": "options-outcome-evidence-v1",
        "signal_id": s.signal_id,
        "structure_id": s.structure_id,
        "strategy_epoch": s.strategy_epoch,
        "executed": False,
        "pnl_basis": "paper_equivalent",
        "resolution_state": s.resolution.value if s.resolution else None,
        "prospective_catch": s.is_prospective_catch,
        "data_integrity": data,
        "signal_integrity": "VALID",
        "execution_integrity": "NOT_APPLICABLE",
        "result_r": _measured(result, "DERIVED") if result is not None else _measured(),
    }
    row = rf.build_feature_row(s, all_factors())
    return signal_record, outcome, row

def test_population_is_clean_registered_prospective_only():
    registry = REGISTRY
    # One distinct registered catch per case (a repeated signal is a duplicate).
    joined = [
        _records(triggered("SPY")),
        _records(triggered("QQQ"), epoch=""),
        _records(triggered("AAPL"), epoch="122-IEX-E9"),
        _records(triggered("MSFT"), data="DEGRADED"),
        _records(triggered("NVDA"), result=None),
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


def test_population_never_selects_on_result_and_signal_integrity_is_authoritative():
    registry = REGISTRY
    late_source = triggered("TSLA")
    late = late_source.__class__(**{**late_source.__dict__, "signal_integrity": sg.IntegrityStatus.DEGRADED})
    joined = [
        _records(triggered("SPY"), result=1.5),
        _records(triggered("QQQ"), result=-1.0),
        _records(triggered("AAPL"), result=0.0),
        _records(late),
    ]
    pop = rf.research_population(registry, joined)
    assert sorted(r["result_r"] for r in pop.rows) == [-1.0, 0.0, 1.5]
    assert pop.excluded == {"integrity": 1}


def test_lookahead_cutoff_is_trigger_detection_for_adapted_1145_capture(tmp_path):
    from options_evidence import capture_adapter as ca
    from tests.test_options_capture_adapter import _engine, _print, et

    engine = _engine(
        tmp_path,
        iex=[_print(et(2026, 10, 5, 9, 30, 20), 770.10)],
        sip=[_print(et(2026, 10, 5, 9, 30, 20), 770.10, feed="sip")],
    )
    engine.run(now=et(2026, 10, 2, 16, 16))
    engine.run(now=et(2026, 10, 5, 9, 31, 0))
    fold = ca.fold_capture_rows(ca.read_capture_journal(engine.journal.path))
    s = next(x for x in fold.journal.signals() if x.state is sg.LifecycleState.TRIGGERED)
    cutoff = s.trigger_detection_time
    rf.build_feature_row(s, all_factors(gex_regime=("NEG_GAMMA", cutoff)))
    with pytest.raises(rf.ResearchError, match="look-ahead"):
        rf.build_feature_row(s, all_factors(gex_regime=("NEG_GAMMA", cutoff + timedelta(seconds=1))))

def test_population_rejects_forged_scope_and_persisted_feature_lookahead():
    s = triggered()

    sig, out, row = _records(s, result=1.0)
    forged_sig = {**sig, "data_source": "forged:source"}
    pop = rf.research_population(REGISTRY, [(forged_sig, out, row)])
    assert pop.rows == ()
    assert pop.excluded == {"signal_record_invalid": 1}

    sig, out, row = _records(s, result=1.0)
    bad_row = json.loads(json.dumps(row))
    bad_row["factors"]["gex_regime"] = {
        "status": "OBSERVED",
        "value": "NEG_GAMMA",
        "as_of": (s.trigger_detection_time + timedelta(seconds=1)).isoformat(),
        "source": "forged",
        "reason": "",
    }
    pop = rf.research_population(REGISTRY, [(sig, out, bad_row)])
    assert pop.rows == ()
    assert pop.excluded == {"feature_row_lookahead": 1}


def test_population_rejects_malformed_outcome_types_and_counterfactual_basis():
    s = triggered()
    sig, out, row = _records(s, result=1.0)

    bad_exec = {**out, "executed": "false"}
    pop = rf.research_population(REGISTRY, [(sig, bad_exec, row)])
    assert pop.excluded == {"outcome_executed_type": 1}

    counterfactual = {**out, "pnl_basis": "counterfactual"}
    pop = rf.research_population(REGISTRY, [(sig, counterfactual, row)])
    assert pop.excluded == {"outcome_basis": 1}



# ── independent-review blockers on a0569e7 ─────────────────────────────────


def test_duplicate_or_replayed_signal_never_counts_twice():
    base = _records(triggered("SPY"))
    other = _records(triggered("QQQ"))
    pop = rf.research_population(REGISTRY, [base, base, base, other])
    assert [r["signal_id"] for r in pop.rows] == [other[0]["signal_id"]]
    assert pop.excluded == {"duplicate_signal": 3}
    # Conflicting outcomes for one signal: neither is chosen.
    s = triggered("AAPL")
    pop = rf.research_population(REGISTRY, [_records(s, result=1.5), _records(s, result=-1.0)])
    assert pop.rows == () and pop.excluded == {"duplicate_signal": 2}


def test_forged_catch_on_a_signal_that_never_triggered_is_excluded():
    journal = sg.SignalJournal(REGISTRY)
    s = opened(journal, strategy=CATCH_STRATEGY, strategy_epoch=CATCH_EPOCH)
    s = journal.append(sg.state_event(journal, s.signal_id, sg.LifecycleState.EXPIRED,
                                      detected_at=TRIG, reason="no_trigger"))
    s = journal.append(sg.integrity_event(journal, s.signal_id, detected_at=TRIG,
                                          signal_integrity="VALID", data_integrity="VALID"))
    record = {**sg.to_record(s), "prospective_catch": True}
    assert sg.verify_record(record, registry=REGISTRY) == []  # the #1151 record check alone admits it
    outcome = {
        "schema": "options-outcome-evidence-v1", "signal_id": s.signal_id, "structure_id": s.structure_id,
        "strategy_epoch": s.strategy_epoch, "executed": False, "pnl_basis": "paper_equivalent",
        "resolution_state": None, "prospective_catch": True, "data_integrity": "VALID",
        "signal_integrity": "VALID", "execution_integrity": "NOT_APPLICABLE",
        "result_r": _measured(2.0, "DERIVED"),
    }
    row = {**rf.build_feature_row(s, all_factors())}
    pop = rf.research_population(REGISTRY, [(record, outcome, row)])
    assert pop.rows == () and pop.excluded == {"not_prospective_catch": 1}


def test_persisted_feature_row_cannot_carry_extra_keys():
    signal_record, outcome, row = _records(triggered("SPY"))
    pop = rf.research_population(REGISTRY, [(signal_record, outcome, {**row, "score": 5})])
    assert pop.rows == () and pop.excluded == {"feature_row_keys": 1}
