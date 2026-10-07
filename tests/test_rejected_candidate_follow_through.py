"""U6: prospective follow-through for rejected / blocked candidates."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ops import evidence_row as er
from ops import rejected_candidate_follow_through as rft

ASSUMPTIONS = {
    "entry_fill_model": "market",
    "same_bar_ambiguity_rule": "stop_first",
    "stop_handling": "fixed_stop",
    "target_handling": "fixed_limit",
    "slippage_assumption": "adverse_ticks=1",
    "commission": "usd_per_contract_per_side=0.74",
    "exchange_broker_fees": "usd_per_contract_per_side=0",
    "sizing_assumptions": "replay_risk_engine",
}
SIGNAL = "2026-05-18T14:30:00+00:00"


@pytest.fixture(autouse=True)
def _no_mirror(monkeypatch):
    monkeypatch.delenv("WEBULL_FUTURES_MIRROR_ENABLED", raising=False)


def _ts(start: str, minutes: int) -> str:
    dt = datetime.fromisoformat(start) + timedelta(minutes=minutes)
    return dt.astimezone(timezone.utc).isoformat()


def _bar(ts: str, o: float, h: float, low: float, c: float) -> dict:
    return {"ts": ts, "open": o, "high": h, "low": low, "close": c, "timeframe": "5m"}


def _blocked_row(*, ts=SIGNAL, strategy="orb_reclaim", entry=100.0, stop=99.0, target=103.0,
                 direction="LONG", instrument="MNQ") -> dict:
    return {
        "ts": ts,
        "decision": "NO_TRADE",
        "instrument": instrument,
        "timeframe_minutes": 5,
        "failed_gates": ["MARKET_CONDITION_NOT_TRENDING"],
        "reason": "not trending",
        "blocked_candidate_audit": {
            "blocking_gate": "MARKET_CONDITION_NOT_TRENDING",
            "observation_only": True,
            "candidates": [
                {
                    "strategy": strategy,
                    "instrument": instrument,
                    "direction": direction,
                    "entry": entry,
                    "stop": stop,
                    "target": target,
                    "blocking_gate": "MARKET_CONDITION_NOT_TRENDING",
                }
            ],
        },
    }


def _resolve(candidate, bars, *, horizon=10, assumptions=ASSUMPTIONS):
    from ops.research_experiment_adapters.futures_replay import parse_execution_assumptions

    return rft.resolve_candidate(
        candidate,
        bars,
        model=parse_execution_assumptions(assumptions),
        model_id=er.execution_model_id(assumptions),
        horizon_bars=horizon,
    )


def _one(row=None):
    (candidate,) = rft.extract_rejected_candidates([row or _blocked_row()])
    return candidate


# ─── Extraction ─────────────────────────────────────────────────────────────


def test_extracts_all_three_rejection_sources_and_keeps_reasons_verbatim():
    rows = [
        _blocked_row(),
        {
            "ts": "2026-05-18T14:35:00+00:00",
            "decision": "TRADE",
            "instrument": "MNQ",
            "candidate_audit": [
                {"strategy": "vwap_hold", "direction": "LONG", "entry": 100, "stop": 99,
                 "target": 102, "reject_code": "HTF_CONFLICT", "reject_reason": "4h down",
                 "selected": False, "attempted": False, "failed_gates": ["HTF_CONFLICT"]},
                {"strategy": "orb_reclaim", "direction": "LONG", "entry": 100, "stop": 99,
                 "target": 103, "reject_code": None, "selected": True, "attempted": True},
            ],
        },
        {
            "ts": "2026-05-18T14:40:00+00:00",
            "bar_ts": "2026-05-18T14:40:00+00:00",
            "decision": "RISK_REJECTED",
            "instrument": "MES",
            "setup": {"strategy": "pdh_reclaim", "direction": "SHORT", "entry": 5300.0,
                      "stop": 5305.0, "target": 5290.0},
            "risk_check": {"result": "REJECTED", "failed_rule": "MAX_TRADES", "reason": "cap"},
        },
        {"type": "OUTCOME", "outcome": {"result": "WIN"}},
    ]
    candidates = rft.extract_rejected_candidates(rows)
    by_source = {c.source: c for c in candidates}
    assert set(by_source) == {"blocked_candidate_audit", "candidate_audit", "risk_rejected"}
    assert by_source["blocked_candidate_audit"].rejection["blocking_gate"] == "MARKET_CONDITION_NOT_TRENDING"
    assert by_source["candidate_audit"].strategy == "vwap_hold"
    assert by_source["candidate_audit"].rejection["reject_code"] == "HTF_CONFLICT"
    assert by_source["risk_rejected"].rejection["failed_rule"] == "MAX_TRADES"
    assert by_source["risk_rejected"].original_decision == "RISK_REJECTED"
    # Selected/attempted candidates are trades, not rejections.
    assert all(c.strategy != "orb_reclaim" or c.source != "candidate_audit" for c in candidates)


def test_candidate_identity_is_deterministic_and_deduplicated():
    a = rft.extract_rejected_candidates([_blocked_row(), _blocked_row()])
    b = rft.extract_rejected_candidates([_blocked_row()])
    assert len(a) == 1 and a[0].candidate_id == b[0].candidate_id


# ─── Resolution through the real PaperBroker ───────────────────────────────


def test_win_resolves_with_declared_costs():
    bars = [_bar(SIGNAL, 99.5, 100.25, 99.5, 100.0), _bar(_ts(SIGNAL, 5), 100, 103.5, 99.8, 103)]
    obs = _resolve(_one(), bars)
    assert obs["terminal_state"] == "WIN"
    assert obs["fill_price"] == 100.25  # entry + 1 adverse tick
    assert obs["exit_price"] == 103.0
    assert obs["costs_fees"] == 1.48
    assert obs["net_pnl"] == pytest.approx(obs["gross_pnl"] - 1.48)
    assert obs["counterfactual"] is True
    assert obs["rejection"]["blocking_gate"] == "MARKET_CONDITION_NOT_TRENDING"
    assert obs["resolved_at_bar_ts"] == "2026-05-18T14:35:00Z"


def test_same_bar_ambiguity_is_pessimistic():
    bars = [_bar(SIGNAL, 99.5, 100.25, 99.5, 100.0), _bar(_ts(SIGNAL, 5), 100, 104, 98, 101)]
    assert _resolve(_one(), bars)["terminal_state"] == "LOSS"


def test_resolution_is_prospective_only():
    # A target touch BEFORE the signal bar must never resolve the candidate.
    bars = [
        _bar(_ts(SIGNAL, -5), 100, 110, 99.5, 100),
        _bar(SIGNAL, 99.5, 100.25, 99.5, 100.0),
    ]
    assert _resolve(_one(), bars, horizon=3)["terminal_state"] == "PENDING"


def test_follow_through_crosses_the_day_boundary():
    late = "2026-05-18T23:50:00+00:00"
    bars = [
        _bar(late, 99.5, 100.25, 99.5, 100.0),
        _bar("2026-05-18T23:55:00+00:00", 100, 101, 99.5, 100.5),
        _bar("2026-05-19T00:00:00+00:00", 100.5, 103.5, 100.0, 103),
    ]
    obs = _resolve(_one(_blocked_row(ts=late)), bars)
    assert obs["terminal_state"] == "WIN"
    assert obs["resolved_at_bar_ts"] == "2026-05-19T00:00:00Z"


def test_day_only_strategy_does_not_carry_past_its_session():
    late = "2026-05-18T19:40:00+00:00"  # 15:40 ET
    bars = [
        _bar(late, 99.5, 100.25, 99.5, 100.0),
        _bar("2026-05-19T13:30:00+00:00", 100, 101, 99.5, 100.5),
    ]
    obs = _resolve(_one(_blocked_row(ts=late, strategy="strat_322_first_live")), bars)
    assert obs["terminal_state"] == "EXPIRED"
    assert obs["expiry_reason"] == "EOD_BAR_MISSING"


def test_horizon_expiry_is_explicit_not_invented():
    bars = [_bar(SIGNAL, 99.5, 100.25, 99.5, 100.0)] + [
        _bar(_ts(SIGNAL, 5 * i), 100, 100.5, 99.6, 100) for i in range(1, 4)
    ]
    obs = _resolve(_one(), bars, horizon=3)
    assert obs["terminal_state"] == "EXPIRED"
    assert obs["expiry_reason"] == "HORIZON_REACHED"
    assert obs["position_state"] == "OPEN"
    assert "net_pnl" not in obs


def test_stop_market_entry_not_triggered_is_no_fill():
    model = dict(ASSUMPTIONS, entry_fill_model="stop_market")
    bars = [_bar(SIGNAL, 99.5, 99.9, 99.5, 99.8), _bar(_ts(SIGNAL, 5), 99.8, 99.9, 99.6, 99.7)]
    obs = _resolve(_one(), bars, assumptions=model)
    assert obs["terminal_state"] == "NO_FILL"


@pytest.mark.parametrize(
    "row,reason",
    [
        (_blocked_row(instrument="ZZZ"), "UNSUPPORTED_INSTRUMENT"),
        (_blocked_row(stop=101.0), "INVALID_GEOMETRY"),
        (_blocked_row(entry=None), "INCOMPLETE_GEOMETRY"),
    ],
)
def test_unavailable_by_rule(row, reason):
    obs = _resolve(_one(row), [_bar(SIGNAL, 99.5, 100.25, 99.5, 100.0)])
    assert obs["terminal_state"] == "UNAVAILABLE_BY_RULE"
    assert obs["unavailable_reason"] == reason


def test_missing_signal_bar_is_unavailable_not_guessed():
    obs = _resolve(_one(), [_bar(_ts(SIGNAL, 5), 100, 103.5, 99.8, 103)])
    assert obs["unavailable_reason"] == "SIGNAL_BAR_MISSING"


# ─── Run / ledger / CLI ─────────────────────────────────────────────────────


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_pending_carries_forward_then_resolves_once(tmp_path):
    journal = tmp_path / "logs/journal_2026-05-18.jsonl"
    _write_jsonl(journal, [_blocked_row()])
    bars_dir = tmp_path / "logs"
    _write_jsonl(bars_dir / "bars_MNQ_2026-05-18.jsonl", [_bar(SIGNAL, 99.5, 100.25, 99.5, 100.0)])
    assumptions_file = tmp_path / "assumptions.json"
    assumptions_file.write_text(json.dumps(ASSUMPTIONS))
    out = tmp_path / "out"
    args = [
        "--journal-dir", str(tmp_path / "logs"), "--bars-dir", str(bars_dir),
        "--out-dir", str(out), "--execution-assumptions", str(assumptions_file),
        "--horizon-bars", "12", "--since", "2026-05-18", "--until", "2026-05-19",
    ]
    journal_before = journal.read_bytes()
    assert rft.main(args) == 0
    assert not (out / rft.LEDGER_FILENAME).exists()  # PENDING is never written as terminal

    # Next session's bars arrive in the next day's file.
    _write_jsonl(
        bars_dir / "bars_MNQ_2026-05-19.jsonl",
        [_bar("2026-05-19T13:30:00+00:00", 100, 103.5, 99.8, 103)],
    )
    assert rft.main(args) == 0
    rows = [json.loads(line) for line in (out / rft.LEDGER_FILENAME).read_text().splitlines()]
    assert [r["terminal_state"] for r in rows] == ["WIN"]
    assert rft.main(args) == 0  # idempotent
    assert len((out / rft.LEDGER_FILENAME).read_text().splitlines()) == 1
    assert journal.read_bytes() == journal_before  # journals are never modified


@pytest.mark.parametrize(
    "kwargs,match",
    [
        ({"horizon_bars": 0}, "horizon_bars"),
        ({"horizon_bars": True}, "horizon_bars"),
        ({"assumptions": dict(ASSUMPTIONS, commission="$1.48 RT")}, "execution assumptions"),
        ({"assumptions": dict(ASSUMPTIONS, same_bar_ambiguity_rule="target_first")}, "execution assumptions"),
    ],
)
def test_run_fails_closed_on_undeclared_model_or_horizon(tmp_path, kwargs, match):
    params = {
        "journal_rows": [_blocked_row()],
        "bar_provider": lambda *_: [],
        "assumptions": ASSUMPTIONS,
        "horizon_bars": 5,
        "ledger": tmp_path / "ledger.jsonl",
    }
    params.update(kwargs)
    with pytest.raises(rft.FollowThroughError, match=match):
        rft.run_follow_through(**params)


def test_run_refuses_when_broker_mirror_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", "true")
    with pytest.raises(rft.FollowThroughError, match="mirror"):
        rft.run_follow_through(
            journal_rows=[], bar_provider=lambda *_: [], assumptions=ASSUMPTIONS,
            horizon_bars=5, ledger=tmp_path / "l.jsonl",
        )


def test_corrupt_ledger_or_journal_fails_closed(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text("{not json\n")
    with pytest.raises(rft.FollowThroughError, match="corrupt ledger"):
        rft.run_follow_through(
            journal_rows=[], bar_provider=lambda *_: [], assumptions=ASSUMPTIONS,
            horizon_bars=5, ledger=ledger,
        )
    (tmp_path / "j").mkdir()
    (tmp_path / "j/journal_2026-05-18.jsonl").write_text("{torn\n")
    from datetime import date

    with pytest.raises(rft.FollowThroughError, match="unreadable journal row"):
        rft.read_journal_rows(tmp_path / "j", since=date(2026, 5, 18), until=date(2026, 5, 18))
