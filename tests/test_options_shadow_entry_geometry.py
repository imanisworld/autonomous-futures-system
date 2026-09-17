"""Observer accounting (operator ruling 2026-09-17): a shadow row born with its
target or stop already consumed at first sight is a non-outcome, never WIN/LOSS."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

from alert_ranker.paper_v1 import (
    ENTRY_CONSUMED_STATES,
    ENTRY_GEOMETRY_AHEAD,
    STOP_CONSUMED_AT_ENTRY,
    TARGET_CONSUMED_AT_ENTRY,
    entry_geometry_state,
    entry_late_reason,
)
from alert_ranker.storage import ScanStorage
from alert_ranker.v1_evidence_hardening import build_v1_evidence_hardening, entry_geometry_of

NOW = datetime(2026, 9, 16, 16, 52, tzinfo=UTC)


def test_entry_geometry_state_matches_entry_late_comparisons():
    # LONG: stop 96.52, target 98.49 (INTC 9208 on 2026-09-16, price 101.86 at first sight)
    assert entry_geometry_state("LONG", 101.86, 96.5205, 98.49) == TARGET_CONSUMED_AT_ENTRY
    assert entry_late_reason("LONG", 101.86, 96.5205, 98.49) == "price_past_target"
    assert entry_geometry_state("LONG", 97.0, 96.5205, 98.49) == ENTRY_GEOMETRY_AHEAD
    assert entry_geometry_state("LONG", 96.0, 96.5205, 98.49) == STOP_CONSUMED_AT_ENTRY
    # SHORT: BAC 9211 (trigger 59.35, price 58.235, target 58.92, stop 59.92)
    assert entry_geometry_state("SHORT", 58.235, 59.92, 58.92) == TARGET_CONSUMED_AT_ENTRY
    assert entry_geometry_state("SHORT", 59.0, 59.92, 58.92) == ENTRY_GEOMETRY_AHEAD
    assert entry_geometry_state("SHORT", 60.0, 59.92, 58.92) == STOP_CONSUMED_AT_ENTRY
    assert entry_geometry_state("LONG", None, 1.0, 2.0) is None
    assert entry_geometry_state("FLAT", 1.5, 1.0, 2.0) is None
    assert ENTRY_CONSUMED_STATES == {TARGET_CONSUMED_AT_ENTRY, STOP_CONSUMED_AT_ENTRY}


class _Base:
    calls = 0

    async def _resolve_v1_candidate(self, setup, underlying_price, now, chain_cache):
        _Base.calls += 1
        return ("WIN", {"closed_reason": "target_hit", "option_bid_at_resolution": 6.9, "option_ask_at_resolution": 7.1})


def _resolve(setup, price=101.65):
    Hardened = build_v1_evidence_hardening(_Base)
    return asyncio.run(Hardened()._resolve_v1_candidate(setup, underlying_price=price, now=NOW, chain_cache={}))


def test_consumed_target_row_resolves_as_non_outcome_without_chain_call():
    _Base.calls = 0
    setup = SimpleNamespace(
        direction="LONG",
        selected_contract={"paper_entry_geometry": TARGET_CONSUMED_AT_ENTRY, "paper_entry_remaining_rr": -0.63, "stop": 96.5205, "target": 98.49, "premium_stop": 5.3},
        setup_inputs={"price": 101.86},
    )
    status, outcome = _resolve(setup)
    assert status == TARGET_CONSUMED_AT_ENTRY
    assert outcome["closed_reason"] == "target_consumed_at_entry"
    assert outcome["resolution_ambiguity"] == "NOT_AN_OUTCOME"
    assert outcome["paper_entry_remaining_rr"] == -0.63
    assert "pnl_dollars" not in outcome
    assert _Base.calls == 0  # no chain fetch, no mark, no WIN


def test_legacy_row_without_field_derives_geometry_from_first_sight_inputs():
    """Rows written before the field existed (epoch-2 rows 9208..9323) must not become WIN."""
    _Base.calls = 0
    setup = SimpleNamespace(
        direction="SHORT",
        selected_contract={"stop": 59.92, "target": 58.92, "premium_stop": 1.0},
        setup_inputs={"price": 58.235, "underlying_invalidation": 59.92, "target_1": 58.92},
    )
    assert entry_geometry_of(setup) == TARGET_CONSUMED_AT_ENTRY
    status, outcome = _resolve(setup, price=58.16)
    assert status == TARGET_CONSUMED_AT_ENTRY and _Base.calls == 0
    stop_row = SimpleNamespace(direction="LONG", selected_contract={"stop": 147.0, "target": 148.0}, setup_inputs={"price": 146.9})
    assert entry_geometry_of(stop_row) == STOP_CONSUMED_AT_ENTRY
    assert _resolve(stop_row, price=146.5)[0] == STOP_CONSUMED_AT_ENTRY


def test_ahead_row_still_resolves_through_the_normal_path():
    _Base.calls = 0
    setup = SimpleNamespace(
        direction="LONG",
        selected_contract={"paper_entry_geometry": ENTRY_GEOMETRY_AHEAD, "stop": 96.5205, "target": 98.49, "premium_stop": 5.3},
        setup_inputs={"price": 97.2},
    )
    status, outcome = _resolve(setup, price=98.6)
    assert status == "WIN" and _Base.calls == 1
    assert outcome["resolution_ambiguity"] == "PATH_UNOBSERVED_BETWEEN_SNAPSHOTS"


def test_shadow_summary_excludes_consumed_rows_from_closed_wins_and_pnl(tmp_path):
    storage = ScanStorage(tmp_path / "scanner.sqlite")
    with storage._connect() as conn:
        for status, outcome in (
            ("WIN", '{"pnl_dollars": 100.0, "pnl_percent": 10.0}'),
            ("LOSS", '{"pnl_dollars": -40.0, "pnl_percent": -4.0}'),
            (TARGET_CONSUMED_AT_ENTRY, '{"closed_reason": "target_consumed_at_entry"}'),
            (STOP_CONSUMED_AT_ENTRY, '{"closed_reason": "stop_consumed_at_entry"}'),
            ("OPEN", "{}"),
        ):
            conn.execute(
                "INSERT INTO options_shadow_journal (timestamp, scan_id, ticker, direction, score, pattern, status, setup_inputs_json, provider_snapshot_json, selected_contract_json, outcome_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (NOW.isoformat(), 0, "INTC", "LONG", 1.0, "x", status, "{}", "{}", '{"paper_evidence_lane": "ACTIVE"}', outcome),
            )
    summary = storage.shadow_summary()
    assert summary.total == 5 and summary.open == 1
    assert summary.entry_consumed == 2
    assert summary.closed == 2 and summary.wins == 1 and summary.losses == 1
    assert summary.win_rate_percent == 50.0 and summary.total_pnl_dollars == 60.0
