"""Pins the inverse ORB decision-time replay (2026-09-08).

Two layers: the committed artifact is asserted from a fresh clone; the
regeneration check runs only where the gitignored 5m corpus exists.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import inverse_orb_decision_time_replay as replay
from scripts.vwap_hold_evidence_package import LookaheadError, assert_decision_time_reference

ARTIFACT = Path("scripts/inverse_orb_decision_time_replay_2026-09-08.json")
FINGERPRINT = "f32b1b1d2fd5f5860d476b7c44f479d28abb8dc0445fccc21f8c7f27519e2da2"


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(ARTIFACT.read_text())


def test_population_identity_matches_the_retired_proof(report):
    proof = json.loads(replay.PROOF.read_text())
    assert replay.population_fingerprint(proof["rows"]) == FINGERPRINT
    assert report["population_fingerprint"] == FINGERPRINT
    assert report["population_n"] == 63
    assert report["supersedes"] == "inverse_orb_canonical_ioc_proof_2026-09-07.json"


def test_contract_is_decision_time_and_guarded(report):
    c = report["contract"]
    assert c["ioc_tolerance_ticks"] == 8.0
    assert c["adverse_slippage_ticks"] == 1.0
    assert c["commission_round_trip"] == 1.48
    assert c["pessimistic_same_bar"] is True
    assert "bar_ts+15m" in c["arrival_reference"]
    assert "ENTRY_BRACKET_INVALID_AT_FILL" in c["bracket_validity_at_fill"]


def test_acceptance_numbers(report):
    o = report["overall"]
    assert o["attempts"] == 63
    assert o["invalid_at_fill"] == 41
    assert o["fills"] == 22
    assert o["no_fills"] == 0
    assert o["net"] == pytest.approx(29.44)
    assert o["pf"] == pytest.approx(1.1362, abs=1e-4)
    assert o["label_contradictions"] == 0
    assert report["H1"]["fills"] == 9 and report["H1"]["net"] == pytest.approx(75.18)
    assert report["H2"]["fills"] == 13 and report["H2"]["net"] == pytest.approx(-45.74)


def test_every_arrival_is_exactly_the_decision_close(report):
    for row in report["rows"]:
        bar_ts = datetime.fromisoformat(row["bar_ts"])
        assert datetime.fromisoformat(row["decision_close_ts"]) == bar_ts + timedelta(minutes=15)
        if "arrival_ts" in row:
            assert datetime.fromisoformat(row["arrival_ts"]) == bar_ts + timedelta(minutes=15)


def test_no_filled_row_is_held_beyond_its_bracket(report):
    for row in report["rows"]:
        if row["status"] != "FILLED":
            continue
        if row["direction"] == "SHORT":
            assert row["target"] < row["fill"] < row["stop"]
        else:
            assert row["stop"] < row["fill"] < row["target"]
        assert row["result"] == ("WIN" if row["net"] > 0 else "LOSS" if row["net"] < 0 else "BREAKEVEN")


def test_retired_reference_would_fail_the_lookahead_guard():
    decision = datetime(2024, 8, 13, 14, 45, tzinfo=timezone.utc)
    late = {"status": "OK", "reference_ts": decision + timedelta(minutes=5), "reference_price_field": "next_bar_open"}
    with pytest.raises(LookaheadError):
        assert_decision_time_reference(late, decision_ts=decision)
    on_time = {"status": "OK", "reference_ts": decision, "reference_price_field": "arrival_bar_open"}
    assert assert_decision_time_reference(on_time, decision_ts=decision) is on_time


@pytest.mark.skipif(not replay.corpus_available(), reason="data/replay_polygon_5m is gitignored; regeneration needs the local corpus")
def test_regeneration_matches_committed_artifact(report):
    fresh = replay.build_report()
    for key in ("overall", "H1", "H2", "sessions", "population_fingerprint"):
        assert fresh[key] == report[key]
    assert [(r["bar_ts"], r["status"], r.get("net")) for r in fresh["rows"]] == [
        (r["bar_ts"], r["status"], r.get("net")) for r in report["rows"]
    ]
