"""U9: standing replay ↔ paper ↔ demo same-candidate reconciliation."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from context.htf_loader import HTFLookup
from ops import candidate_reconciliation as rec
from ops.research_experiment_adapters import futures_replay as fr
from replay.replay_engine import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
WEEK = ROOT / "data/replay/week/manifest.json"
ASSUMPTIONS = {
    "entry_fill_model": "market",
    "same_bar_ambiguity_rule": "stop_first",
    "stop_handling": "fixed_stop",
    "target_handling": "fixed_limit",
    "slippage_assumption": "adverse_ticks=0",
    "commission": "usd_per_contract_per_side=0.74",
    "exchange_broker_fees": "usd_per_contract_per_side=0",
    "sizing_assumptions": "replay_risk_engine",
}


@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch):
    monkeypatch.delenv("WEBULL_FUTURES_MIRROR_ENABLED", raising=False)


def _engine_journal(config, log_dir: Path) -> Path:
    cfg = fr.build_arm_config(config, fr.parse_execution_assumptions(ASSUMPTIONS), {}, log_dir=str(log_dir))
    ReplayEngine(config=cfg, log_dir=str(log_dir), htf_lookup=HTFLookup()).run_manifest(WEEK)
    return log_dir


def _rows(log_dir: Path) -> list[dict]:
    return rec.read_journal_dir(log_dir, since=None, until=None)


# ─── Frozen fixture: genuine agreement is PASS ──────────────────────────────


def test_two_independent_journal_runs_of_the_same_candidates_pass(config, tmp_path):
    replay = rec.records_from_journal_rows("replay", _rows(_engine_journal(config, tmp_path / "a")))
    paper = rec.records_from_journal_rows("paper", _rows(_engine_journal(config, tmp_path / "b")))
    report = rec.reconcile({"replay": replay, "paper": paper})
    assert report["status"] == "PASS", report
    assert report["counts"] == {"PASS": 3, "DIVERGED": 0, "INCOMPLETE": 0}
    # paper_order_ids differ between runs; identity is the canonical key.
    assert {c["key"].split("|")[0] for c in report["candidates"]} == {"MNQ", "MES"}


def test_canonical_bundle_rows_reconcile_with_the_replay_journal(config, tmp_path, monkeypatch):
    from tests import test_futures_replay_adapter as u3

    spec_path = u3._seed(tmp_path / "repo")
    head = json.loads(spec_path.read_text())["baseline"]["commit_sha"]
    monkeypatch.setattr(fr, "CODE_SHA_PROVIDER", lambda: head)
    monkeypatch.setattr(fr, "BASE_CONFIG_PROVIDER", lambda: config)
    members = fr.run_futures_replay(u3._ctx(spec_path)).members
    canonical = rec.records_from_bundle_members("replay", members)
    paper = rec.records_from_journal_rows("paper", _rows(_engine_journal(config, tmp_path / "p")))
    report = rec.reconcile({"replay": canonical, "paper": paper})
    assert report["status"] == "PASS", report
    # Canonical-only fields are reported as not comparable, never as a pass.
    assert "costs_fees" in report["candidates"][0]["not_comparable"]
    assert "earliest_legal_order_ts" in report["candidates"][0]["not_comparable"]


# ─── Synthetic demo journal ─────────────────────────────────────────────────


@pytest.fixture
def week(config, tmp_path):
    return _rows(_engine_journal(config, tmp_path / "replay"))


def _demo(rows: list[dict], mutate=None) -> list[dict]:
    rows = copy.deepcopy(rows)
    if mutate:
        mutate(rows)
    return rows


def _first(rows, pred):
    return next(r for r in rows if pred(r))


def _is_trade(r):
    return r.get("decision") == "TRADE"


def _is_outcome(r):
    return r.get("type") == "OUTCOME"


def test_fill_slippage_is_diverged_unless_tolerated(week):
    def slip(rows):
        _first(rows, _is_outcome)["outcome"]["entry_price"] += 0.25

    replay = rec.records_from_journal_rows("replay", week)
    demo = rec.records_from_journal_rows("demo", _demo(week, slip))
    strict = rec.reconcile({"replay": replay, "demo": demo})
    assert strict["status"] == "DIVERGED"
    diverged = [c for c in strict["candidates"] if c["status"] == "DIVERGED"]
    assert diverged[0]["divergences"] == [{"field": "fill_price", "replay": 19498.5, "demo": 19498.75}]
    assert rec.reconcile({"replay": replay, "demo": demo}, tolerance_ticks=1)["status"] == "PASS"


@pytest.mark.parametrize(
    "field,value",
    [("direction", "SHORT"), ("stop", 19470.0), ("target", 19560.0)],
)
def test_bracket_or_direction_mismatch_is_diverged(week, field, value):
    def change(rows):
        _first(rows, _is_trade)["setup"][field] = value

    report = rec.reconcile({
        "replay": rec.records_from_journal_rows("replay", week),
        "demo": rec.records_from_journal_rows("demo", _demo(week, change)),
    })
    assert report["status"] == "DIVERGED"
    assert any(d["field"] == field for c in report["candidates"] for d in c["divergences"])


def test_outcome_and_exit_mismatch_is_diverged(week):
    def stopped(rows):
        out = _first(rows, _is_outcome)["outcome"]
        out.update(result="LOSS", exit_price=19478.5, exit_reason="STOP_HIT")

    report = rec.reconcile({
        "replay": rec.records_from_journal_rows("replay", week),
        "demo": rec.records_from_journal_rows("demo", _demo(week, stopped)),
    })
    fields = {d["field"] for c in report["candidates"] for d in c["divergences"]}
    assert {"outcome", "exit_price", "exit_reason"} <= fields


def test_risk_rejection_in_one_mode_is_diverged(week):
    def reject(rows):
        trade = _first(rows, _is_trade)
        trade["decision"] = "RISK_REJECTED"
        trade["risk_check"] = {"failed_rule": "MAX_TRADES"}

    report = rec.reconcile({
        "replay": rec.records_from_journal_rows("replay", week),
        "demo": rec.records_from_journal_rows("demo", _demo(week, reject)),
    })
    assert any(d["field"] == "fill_state" for c in report["candidates"] for d in c["divergences"])


def test_missing_candidate_in_a_mode_is_incomplete_not_pass(week):
    def drop(rows):
        trade = _first(rows, _is_trade)
        rows.remove(trade)
        rows[:] = [r for r in rows if not (_is_outcome(r)
                   and r["outcome"]["paper_order_id"] == trade["paper_order_id"])]

    report = rec.reconcile({
        "replay": rec.records_from_journal_rows("replay", week),
        "demo": rec.records_from_journal_rows("demo", _demo(week, drop)),
    })
    assert report["status"] == "INCOMPLETE"
    assert report["counts"]["INCOMPLETE"] == 1
    assert "absent in demo" in [c for c in report["candidates"] if c["status"] == "INCOMPLETE"][0]["incomplete_reasons"]


def test_unresolved_demo_position_is_incomplete(week):
    def open_position(rows):
        rows.remove(_first(rows, _is_outcome))

    report = rec.reconcile({
        "replay": rec.records_from_journal_rows("replay", week),
        "demo": rec.records_from_journal_rows("demo", _demo(week, open_position)),
    })
    reasons = [r for c in report["candidates"] for r in c["incomplete_reasons"]]
    assert report["status"] == "INCOMPLETE" and "outcome missing in demo" in reasons


def test_duplicate_identity_is_incomplete(week):
    def dup(rows):
        trade = copy.deepcopy(_first(rows, _is_trade))
        trade["paper_order_id"] = "PAPER-dup"
        rows.append(trade)

    report = rec.reconcile({
        "replay": rec.records_from_journal_rows("replay", week),
        "demo": rec.records_from_journal_rows("demo", _demo(week, dup)),
    })
    reasons = [r for c in report["candidates"] for r in c["incomplete_reasons"]]
    assert any("duplicate identity in demo" in r for r in reasons)


def test_unknown_contract_cannot_pass():
    a = [rec.CandidateRecord("replay", "ZZZ|s|2026-01-01T00:00:00Z", "ZZZ", "s", "2026-01-01T00:00:00Z",
                             {"direction": "LONG", "intended_entry": 1.0, "stop": 0.5, "target": 2.0,
                              "fill_state": "NO_FILL"})]
    b = [rec.CandidateRecord("demo", a[0].key, "ZZZ", "s", a[0].signal_ts, dict(a[0].fields))]
    report = rec.reconcile({"replay": a, "demo": b})
    assert report["status"] == "INCOMPLETE"
    assert "no contract metadata for ZZZ" in report["candidates"][0]["incomplete_reasons"]


def test_replay_bundle_must_pass_u4_identity_gate(tmp_path):
    from tests import test_evidence_identity_gate as u4

    root, report = u4._run(u4._seed(tmp_path / "repo"))
    assert report.status == "VALID"
    bundle = u4._bundle(root)
    assert rec.read_bundle_members(bundle, repo_root=root)

    raw = bundle / "candidate_raw.json"
    payload = json.loads(raw.read_text())
    payload["members"][0]["net_pnl"] = 999.0
    raw.write_text(json.dumps(payload))
    with pytest.raises(rec.ReconciliationError, match="INVALID, not PROMOTION_QUALITY"):
        rec.read_bundle_members(bundle, repo_root=root)


def test_malformed_relevant_journal_row_fails_closed():
    rows = [{
        "decision": "TRADE",
        "instrument": "MNQ",
        "bar_ts": "2026-05-18T14:30:00+00:00",
        "paper_order_id": "P1",
        "setup": {"strategy": "", "direction": "LONG", "entry": 100.0, "stop": 99.0, "target": 103.0},
    }]
    with pytest.raises(rec.ReconciliationError, match="missing canonical"):
        rec.records_from_journal_rows("demo", rows)


def test_unmatched_filled_outcome_cannot_disappear_into_pass():
    rows = [{"type": "OUTCOME", "instrument": "MNQ", "outcome": {
        "result": "WIN", "paper_order_id": "P1", "strategy": "orb_reclaim",
        "signal_timestamp": "2026-05-18T14:30:00+00:00",
        "entry_price": 19498.5, "exit_price": 19548.5, "exit_reason": "TARGET_HIT",
    }}]
    records = rec.records_from_journal_rows("demo", rows)
    assert len(records) == 1
    report = rec.reconcile({"replay": copy.deepcopy(records), "demo": records})
    assert report["status"] == "INCOMPLETE"
    assert any("direction missing" in reason for reason in report["candidates"][0]["incomplete_reasons"])


def test_void_outcome_is_no_fill_not_filled():
    rows = [
        {
            "decision": "TRADE", "instrument": "MNQ",
            "bar_ts": "2026-05-18T14:30:00+00:00", "paper_order_id": "P1",
            "setup": {"strategy": "orb_reclaim", "direction": "LONG", "entry": 19498.5,
                      "stop": 19478.5, "target": 19548.5},
        },
        {"type": "OUTCOME", "instrument": "MNQ", "outcome": {
            "result": "VOID", "paper_order_id": "P1", "strategy": "orb_reclaim",
            "signal_timestamp": "2026-05-18T14:30:00+00:00",
            "entry_price": 19498.5, "exit_reason": "VOID_GAP_DAY",
        }},
    ]
    (record,) = rec.records_from_journal_rows("demo", rows)
    assert record.fields["fill_state"] == "NO_FILL"


def test_cancelled_outcome_without_trade_row_is_a_no_fill_record():
    rows = [{"type": "OUTCOME", "instrument": "MNQ", "outcome": {
        "result": "CANCELLED", "paper_order_id": "P1", "strategy": "orb_reclaim",
        "signal_timestamp": "2026-05-18T14:30:00+00:00", "entry_price": 19498.5,
        "exit_reason": "ENTRY_NOT_FILLED"}}]
    (record,) = rec.records_from_journal_rows("demo", rows)
    assert record.fields["fill_state"] == "NO_FILL"
    assert record.key == "MNQ|orb_reclaim|2026-05-18T14:30:00Z"


@pytest.mark.parametrize(
    "by_mode,kwargs,match",
    [({"replay": []}, {}, "two modes"), ({"replay": [], "demo": []}, {"tolerance_ticks": -1}, "non-negative")],
)
def test_invalid_reconciliation_inputs(by_mode, kwargs, match):
    with pytest.raises(rec.ReconciliationError, match=match):
        rec.reconcile(by_mode, **kwargs)


def test_no_candidates_is_incomplete():
    assert rec.reconcile({"replay": [], "demo": []})["status"] == "INCOMPLETE"


def test_cli_is_read_only_and_exit_codes_encode_status(config, tmp_path):
    a = _engine_journal(config, tmp_path / "a")
    b = _engine_journal(config, tmp_path / "b")
    before = {p: p.read_bytes() for p in list(a.glob("journal_*")) + list(b.glob("journal_*"))}
    out = tmp_path / "report.json"
    assert rec.main(["--replay-journal", str(a), "--paper-journal", str(b), "--out", str(out)]) == 0
    assert json.loads(out.read_text())["status"] == "PASS"
    assert all(p.read_bytes() == data for p, data in before.items())
    # Corrupt one demo journal row: fail closed with an error exit.
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "journal_2026-05-18.jsonl").write_text("{torn\n")
    assert rec.main(["--replay-journal", str(a), "--demo-journal", str(bad)]) == 2
    # Missing demo candidates -> INCOMPLETE exit code.
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rec.main(["--replay-journal", str(a), "--demo-journal", str(empty)]) == 3
