"""Frozen equal-time treatments for the MNQ account-admission prereg.

These tests do not score the historical account stream. Capacity is the
existing frozen replay count. Overlay cases are synthetic events that reuse
the frozen source ids.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research.mnq_account_admission_overlay import apply_account_admission
from research.mnq_combined_portfolio_audit import PortfolioEvent, replay_portfolio
from research.mnq_equal_time_admission_order import (
    DISTINCT_REQUEST_ORDER_UNKNOWN,
    EXIT_BEFORE_CANDIDATE_PROVEN,
    FROZEN_AUDIT_PATH,
    MODE_PRIMARY,
    MODE_SENSITIVITY,
    UNKNOWN_ORDER_BUSY_FIRST,
    UNKNOWN_ORDER_EXIT_FIRST,
    EqualTimeOrderError,
    compile_equal_time_order,
    load_frozen_equal_time_order,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT / "research/artifacts/pr915-six-family-fillable-events-5a9f14b.jsonl"
ARTIFACT_SHA256 = "d5f949fce98e80ab83fd1c2551b99e7644f4ddc0ad6381aaf978b3282be11942"
ORDER_MODULE = "mnq_equal_time_admission_order"

UNKNOWN_PAIRS = {
    (
        "daily22:2025-12-01:2025-11-30T23:00:00+00:00",
        "asia:2025-12-01T01:15:00+00:00:strat_22_continuation_observed:SHORT",
    ),
    (
        "asia:2026-02-05T02:30:00+00:00:strat_22_continuation_observed:SHORT",
        "daily22:2026-02-05:2026-02-05T12:40:00+00:00",
    ),
}
PROVEN_PAIR = (
    "asia:2025-07-24T00:15:00+00:00:ema_pullback_trend:LONG",
    "asia:2025-07-24T07:00:00+00:00:strat_22_continuation_observed:SHORT",
)
PREREG929 = (
    "docs/prereg929-evaluator.md",
    "research/prereg929_forward_corpus.py",
    "research/prereg929_forward_portfolio.py",
    "research/prereg929_step0.py",
    "scripts/prereg929_forward_corpus.py",
    "scripts/prereg929_forward_portfolio.py",
    "scripts/prereg929_step0_parity.py",
    "tests/test_prereg929_forward_portfolio.py",
)


def _audit_rows() -> list[dict]:
    return json.loads(FROZEN_AUDIT_PATH.read_text(encoding="utf-8"))


def _event(
    source_id: str,
    family: str,
    fill: str,
    exit_: str,
    pnl: float,
) -> PortfolioEvent:
    return PortfolioEvent(
        family=family,
        source_id=source_id,
        signal_ts=fill,
        eligible_fill_ts=fill,
        exit_ts=exit_,
        observation_day=fill[:10],
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        result="WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN",
        net_pnl=pnl,
        session="unit",
        source="unit",
    )


def _open(source_id: str, family: str, fill: str, exit_: str, pnl: float) -> PortfolioEvent:
    return _event(source_id, family, fill, exit_, pnl)


def _candidate(source_id: str, family: str, fill: str) -> PortfolioEvent:
    return _event(source_id, family, fill, fill, 0.0)


def test_frozen_audit_recognizes_exactly_45_proven_and_2_unknown_pairs() -> None:
    rows = _audit_rows()
    primary = load_frozen_equal_time_order(MODE_PRIMARY)
    sensitivity = load_frozen_equal_time_order(MODE_SENSITIVITY)
    proven = {
        (row["exiting_source"], row["candidate_source"])
        for row in rows
        if row["classification"] == EXIT_BEFORE_CANDIDATE_PROVEN
    }
    unknown = {
        (row["exiting_source"], row["candidate_source"])
        for row in rows
        if row["classification"] == DISTINCT_REQUEST_ORDER_UNKNOWN
    }
    assert len(proven) == 45
    assert unknown == UNKNOWN_PAIRS
    assert PROVEN_PAIR in proven
    assert {
        (pair.exiting_source, pair.candidate_source)
        for pair in primary.pairs
        if pair.audit_classification == EXIT_BEFORE_CANDIDATE_PROVEN
    } == proven
    assert {
        (pair.exiting_source, pair.candidate_source)
        for pair in primary.pairs
        if pair.treatment == UNKNOWN_ORDER_BUSY_FIRST
    } == UNKNOWN_PAIRS
    assert all(
        pair.treatment == EXIT_BEFORE_CANDIDATE_PROVEN
        for pair in primary.pairs
        if (pair.exiting_source, pair.candidate_source) in proven
    )
    assert {
        (pair.exiting_source, pair.candidate_source)
        for pair in sensitivity.pairs
        if pair.treatment == UNKNOWN_ORDER_EXIT_FIRST
    } == UNKNOWN_PAIRS
    assert {
        (pair.exiting_source, pair.candidate_source)
        for pair in sensitivity.pairs
        if pair.treatment == EXIT_BEFORE_CANDIDATE_PROVEN
    } == proven
    ids = {
        json.loads(line)["source_id"]
        for line in ARTIFACT.read_text(encoding="utf-8").splitlines()
        if line
    }
    load_frozen_equal_time_order(MODE_PRIMARY, known_source_ids=ids)


def test_primary_blocks_the_two_unknown_candidates_and_sensitivity_exits_first() -> None:
    rows = _audit_rows()
    wanted = {PROVEN_PAIR, *UNKNOWN_PAIRS}
    selected = [
        row
        for row in rows
        if (row["exiting_source"], row["candidate_source"]) in wanted
    ]
    assert len(selected) == 3
    dec_exit, dec_candidate = (
        "daily22:2025-12-01:2025-11-30T23:00:00+00:00",
        "asia:2025-12-01T01:15:00+00:00:strat_22_continuation_observed:SHORT",
    )
    feb_exit, feb_candidate = (
        "asia:2026-02-05T02:30:00+00:00:strat_22_continuation_observed:SHORT",
        "daily22:2026-02-05:2026-02-05T12:40:00+00:00",
    )
    events = [
        _open(PROVEN_PAIR[0], "ASIA_D_EMA", "2025-07-24T00:15:00+00:00", "2025-07-24T07:00:00+00:00", 0.0),
        _candidate(PROVEN_PAIR[1], "ASIA_D_EMA", "2025-07-24T07:00:00+00:00"),
        _open(dec_exit, "DAILY_22_COMPLETED_CLOSE", "2025-11-30T23:00:00+00:00", "2025-12-01T01:15:00+00:00", -150.0),
        _candidate(dec_candidate, "ASIA_D_EMA", "2025-12-01T01:15:00+00:00"),
        _open(feb_exit, "ASIA_D_EMA", "2026-02-05T02:30:00+00:00", "2026-02-05T12:45:00+00:00", -150.0),
        _candidate(feb_candidate, "DAILY_22_COMPLETED_CLOSE", "2026-02-05T12:45:00+00:00"),
        _open("other-exit", "4HR_RETRIGGER", "2026-03-01T00:00:00+00:00", "2026-03-01T01:00:00+00:00", -150.0),
        _candidate("other-candidate", "60M_322_FIRST_LIVE", "2026-03-01T01:00:00+00:00"),
    ]
    primary = compile_equal_time_order(selected, mode=MODE_PRIMARY)
    sensitivity = compile_equal_time_order(selected, mode=MODE_SENSITIVITY)
    primary_by_id = {
        item.source_id: item for item in apply_account_admission(events, equal_time_order=primary).decisions
    }
    sensitivity_by_id = {
        item.source_id: item
        for item in apply_account_admission(events, equal_time_order=sensitivity).decisions
    }

    assert primary_by_id[dec_candidate].disposition == "SKIPPED_BUSY_PORTFOLIO"
    assert primary_by_id[dec_candidate].equal_time_treatment == UNKNOWN_ORDER_BUSY_FIRST
    assert primary_by_id[feb_candidate].disposition == "SKIPPED_BUSY_PORTFOLIO"
    assert primary_by_id[feb_candidate].equal_time_treatment == UNKNOWN_ORDER_BUSY_FIRST
    assert sensitivity_by_id[dec_candidate].disposition == "FILLED"
    assert sensitivity_by_id[dec_candidate].equal_time_treatment == UNKNOWN_ORDER_EXIT_FIRST
    assert sensitivity_by_id[feb_candidate].disposition == "SKIPPED_DAILY_LOSS"
    assert sensitivity_by_id[feb_candidate].equal_time_treatment == UNKNOWN_ORDER_EXIT_FIRST
    assert primary_by_id[PROVEN_PAIR[1]].disposition == "FILLED"
    assert primary_by_id[PROVEN_PAIR[1]].equal_time_treatment == EXIT_BEFORE_CANDIDATE_PROVEN
    assert sensitivity_by_id[PROVEN_PAIR[1]].disposition == "FILLED"
    assert sensitivity_by_id[PROVEN_PAIR[1]].equal_time_treatment == EXIT_BEFORE_CANDIDATE_PROVEN
    assert primary_by_id["other-candidate"].disposition == "SKIPPED_BUSY_PORTFOLIO"
    assert primary_by_id["other-candidate"].equal_time_treatment is None
    assert sensitivity_by_id["other-candidate"].disposition == "SKIPPED_BUSY_PORTFOLIO"
    assert sensitivity_by_id["other-candidate"].equal_time_treatment is None


def test_default_overlay_keeps_equal_timestamps_strict_before() -> None:
    events = [
        _open(PROVEN_PAIR[0], "ASIA_D_EMA", "2025-07-24T00:15:00+00:00", "2025-07-24T07:00:00+00:00", -150.0),
        _candidate(PROVEN_PAIR[1], "ASIA_D_EMA", "2025-07-24T07:00:00+00:00"),
    ]
    decision = apply_account_admission(events).decisions[1]
    assert decision.disposition == "SKIPPED_BUSY_PORTFOLIO"
    assert decision.equal_time_treatment is None


def test_unknown_missing_duplicate_and_conflicting_overrides_fail_closed(tmp_path: Path) -> None:
    rows = _audit_rows()
    with pytest.raises(EqualTimeOrderError, match="unknown source id"):
        compile_equal_time_order(
            rows[:1],
            mode=MODE_PRIMARY,
            known_source_ids={"missing"},
        )
    with pytest.raises(EqualTimeOrderError, match="duplicate"):
        compile_equal_time_order([rows[0], rows[0]], mode=MODE_PRIMARY)
    conflict = dict(rows[0])
    conflict["candidate_source"] = "other-candidate"
    with pytest.raises(EqualTimeOrderError, match="conflicting"):
        compile_equal_time_order([rows[0], conflict], mode=MODE_PRIMARY)
    changed = dict(rows[0])
    changed["classification"] = DISTINCT_REQUEST_ORDER_UNKNOWN
    with pytest.raises(EqualTimeOrderError, match="conflicting"):
        compile_equal_time_order([rows[0], changed], mode=MODE_PRIMARY)
    with pytest.raises(EqualTimeOrderError, match="missing"):
        compile_equal_time_order(
            [{**rows[0], "exiting_source": "  "}],
            mode=MODE_PRIMARY,
        )
    short = tmp_path / "short.json"
    short.write_text(json.dumps(rows[:3]), encoding="utf-8")
    with pytest.raises(EqualTimeOrderError, match="45 proven"):
        load_frozen_equal_time_order(MODE_PRIMARY, audit_path=short)
    with pytest.raises(EqualTimeOrderError, match="unknown source id"):
        apply_account_admission(
            [
                _open(
                    "not-in-map",
                    "4HR_RETRIGGER",
                    "2026-03-01T00:00:00+00:00",
                    "2026-03-01T01:00:00+00:00",
                    0.0,
                )
            ],
            equal_time_order=load_frozen_equal_time_order(MODE_PRIMARY),
        )


def test_artifact_hash_stays_pinned_and_frozen_capacity_is_488() -> None:
    assert hashlib.sha256(ARTIFACT.read_bytes()).hexdigest() == ARTIFACT_SHA256
    events = [
        PortfolioEvent(**json.loads(line))
        for line in ARTIFACT.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(events) == 2257
    assert len(replay_portfolio(events).fills) == 488


def test_prereg929_files_do_not_reference_the_order_layer() -> None:
    layer = (ROOT / "research" / f"{ORDER_MODULE}.py").read_text(encoding="utf-8")
    overlay = (ROOT / "research/mnq_account_admission_overlay.py").read_text(encoding="utf-8")
    assert "prereg929" not in layer
    assert "prereg929" not in overlay
    for rel in PREREG929:
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert ORDER_MODULE not in text
        assert "equal_time_order" not in text


def test_runtime_code_does_not_import_the_order_layer() -> None:
    skip = ("research/", "tests/")
    hits: list[str] = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(skip) or "/.venv/" in f"/{rel}" or rel.startswith(".venv/"):
            continue
        if ORDER_MODULE in path.read_text(encoding="utf-8"):
            hits.append(rel)
    assert hits == []
