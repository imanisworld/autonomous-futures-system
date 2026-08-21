from __future__ import annotations

import json
from pathlib import Path

import pytest

from ops.project_check.trade_chain import (
    build_trade_chain_report,
    load_checkpoint,
    load_checkpoint_full,
    save_checkpoint,
)


@pytest.fixture(autouse=True)
def clean_tolerance_env(monkeypatch):
    for name in (
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES",
        "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ",
        "ENTRY_FILL_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _trade(ts: str, *, instrument: str = "MNQ", strategy: str = "orb_breakout", stop=29990.0, target=30025.0) -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {"direction": "LONG", "strategy": strategy, "entry": 30000.0, "stop": stop, "target": target, "contracts": 1},
    }


def _outcome(ts: str, *, instrument: str = "MNQ", result: str = "WIN", exit_reason: str = "target hit", pnl=25.0) -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "type": "OUTCOME",
        "outcome": {"result": result, "exit_reason": exit_reason, "pnl_dollars": pnl},
    }


def _order_ids(ts: str, *, instrument: str = "MNQ", order_id: str = "12345678") -> dict:
    return {"ts": ts, "instrument": instrument, "type": "ORDER_IDS", "order_ids": {"instrument": instrument, "entry": order_id}}


def test_clean_day_fill_and_cancel_passes(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(
        day,
        [
            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:30:00Z", result="WIN", pnl=25.0),
            _trade("2026-07-01T15:00:00Z"),
            _outcome("2026-07-01T15:10:00Z", result="CANCELLED", exit_reason="IOC limit expired"),
        ],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    s = report["summary"]
    assert s["attempts"] == 2
    assert s["fills"] == 1
    assert s["cancellations"] == 1
    assert s["orphans"] == 0
    assert s["duplicate_order_identities"] == 0
    assert s["naked_position_risk"] == 0
    assert s["unmatched_outcomes"] == 0
    assert report["accounting"]["attempts_identity_holds"] is True


def test_stale_unresolved_trade_from_a_prior_day_is_an_orphan(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    # MNQ trade on day 1 with no outcome ever, on either day. A same-instrument
    # FIFO pairer must not let it silently "steal" a later day's outcome meant
    # for a different instrument's trade -- it should surface as an orphan.
    _write_jsonl(journal_dir / "journal_2026-07-01.jsonl", [_trade("2026-07-01T14:00:00Z", instrument="MNQ")])
    _write_jsonl(
        journal_dir / "journal_2026-07-02.jsonl",
        [_trade("2026-07-02T14:00:00Z", instrument="MES"), _outcome("2026-07-02T14:30:00Z", instrument="MES")],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "FAIL"
    assert report["summary"]["orphans"] == 1
    assert report["detail"]["orphans"][0]["trade_ts"] == "2026-07-01T14:00:00Z"


def test_unresolved_trade_on_latest_day_is_unverified_open_not_a_fill(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_trade("2026-07-01T14:00:00Z"), _outcome("2026-07-01T14:30:00Z"), _trade("2026-07-01T15:00:00Z")],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["summary"]["orphans"] == 0
    assert report["summary"]["unverified_open_attempts"] == 1
    # The unresolved attempt must NOT be counted as a fill -- only the one
    # resolved WIN outcome counts. A TRADE row alone is an order attempt, not
    # proof of a fill.
    assert report["summary"]["fills"] == 1
    assert report["summary"]["resolved_fills"] == 1
    assert report["detail"]["unverified_open_attempts"][0]["category"] == "UNVERIFIED_OPEN_ATTEMPT"
    assert report["status"] == "PASS"


def test_naked_position_flagged_when_setup_missing_stop(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_trade("2026-07-01T14:00:00Z", stop=None), _outcome("2026-07-01T14:30:00Z")],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "FAIL"
    assert report["summary"]["naked_position_risk"] == 1


def test_duplicate_order_identity_detected(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            _order_ids("2026-07-01T14:00:05Z", order_id="99999999"),
            _outcome("2026-07-01T14:30:00Z"),
            _trade("2026-07-01T15:00:00Z"),
            _order_ids("2026-07-01T15:00:05Z", order_id="99999999"),
            _outcome("2026-07-01T15:30:00Z"),
        ],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "FAIL"
    assert report["summary"]["duplicate_order_identities"] == 1


def test_checkpoint_excludes_already_seen_attempts(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:30:00Z"),
            _trade("2026-07-01T15:00:00Z"),
            _outcome("2026-07-01T15:30:00Z"),
        ],
    )
    first = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert first["summary"]["attempts"] == 2
    assert load_checkpoint(tmp_path) == "2026-07-01T15:30:00Z"

    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            _outcome("2026-07-01T14:30:00Z"),
            _trade("2026-07-01T15:00:00Z"),
            _outcome("2026-07-01T15:30:00Z"),
            _trade("2026-07-01T16:00:00Z"),
            _outcome("2026-07-01T16:30:00Z"),
        ],
    )
    second = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert second["summary"]["attempts"] == 1


def test_risk_rejected_missing_reason_is_flagged(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [{"ts": "2026-07-01T14:00:00Z", "instrument": "MNQ", "decision": "RISK_REJECTED"}],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["summary"]["risk_rejected_missing_reason"] == 1
    assert report["status"] == "FAIL"


def test_checkpoint_never_advances_when_status_is_fail(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    # A duplicate order identity forces status=FAIL.
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            _order_ids("2026-07-01T14:00:05Z", order_id="1"),
            _outcome("2026-07-01T14:30:00Z"),
            _trade("2026-07-01T15:00:00Z"),
            _order_ids("2026-07-01T15:00:05Z", order_id="1"),
            _outcome("2026-07-01T15:30:00Z"),
        ],
    )
    report = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False, advance_checkpoint=True
    )
    assert report["status"] == "FAIL"
    assert report["window"]["checkpoint_advance_requested"] is True
    assert report["window"]["checkpoint_advanced"] is False
    assert report["window"]["checkpoint_skip_reason"] is not None
    assert load_checkpoint(tmp_path) is None

    # Re-running must still see everything -- nothing was silently skipped.
    rerun = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert rerun["summary"]["attempts"] == 2
    assert rerun["status"] == "FAIL"


def test_advance_checkpoint_defaults_to_false(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_trade("2026-07-01T14:00:00Z"), _outcome("2026-07-01T14:30:00Z")],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    assert report["window"]["checkpoint_advance_requested"] is False
    assert report["window"]["checkpoint_advanced"] is False
    assert load_checkpoint(tmp_path) is None


def test_unmatched_outcome_is_reported_not_dropped(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    # An OUTCOME with no preceding approved TRADE for that instrument in the
    # window -- must show up as unmatched, not silently vanish.
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_outcome("2026-07-01T14:30:00Z", result="WIN", pnl=10.0)],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["summary"]["unmatched_outcomes"] == 1
    assert report["detail"]["unmatched_outcomes"][0]["ts"] == "2026-07-01T14:30:00Z"
    assert report["status"] == "FAIL"


def test_unmatched_order_ids_is_reported_not_dropped(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_order_ids("2026-07-01T14:30:00Z", order_id="55555555")],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["summary"]["unmatched_order_ids"] == 1
    assert report["status"] == "FAIL"


def test_never_writes_journal_files(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(day, [_trade("2026-07-01T14:00:00Z"), _outcome("2026-07-01T14:30:00Z")])
    before = day.read_text()
    build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False, advance_checkpoint=True)
    after = day.read_text()
    assert before == after


# ---------------------------------------------------------------------------
# Required regression coverage: TRADE before the checkpoint boundary whose
# OUTCOME (or ORDER_IDS) arrives after it must reconcile correctly and must
# NOT be reported as unmatched -- pairing has to run over full journal
# history, not just the post-checkpoint slice.
# ---------------------------------------------------------------------------


def test_outcome_after_checkpoint_for_trade_before_checkpoint_reconciles(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T10:00:00Z"),  # before checkpoint, unresolved as of the checkpoint
            _outcome("2026-07-01T13:00:00Z", result="WIN", pnl=25.0),  # arrives after checkpoint
        ],
    )
    report = build_trade_chain_report(
        journal_dir=journal_dir,
        repo_root=tmp_path,
        since_ts="2026-07-01T12:00:00Z",  # simulates a checkpoint saved between the two rows
        use_checkpoint=False,
    )
    assert report["status"] == "PASS"
    assert report["summary"]["unmatched_outcomes"] == 0
    assert report["detail"]["unmatched_outcomes"] == []
    # Not a "new attempt" (the TRADE predates the checkpoint) and not a fill
    # counted under this run's attempts identity -- it shows up as a
    # carryover resolution instead.
    assert report["summary"]["attempts"] == 0
    assert report["summary"]["carryover_resolutions"] == 1
    carryover = report["detail"]["carryover_resolutions"][0]
    assert carryover["trade_ts"] == "2026-07-01T10:00:00Z"
    assert carryover["category"] == "filled_win_loss"
    assert carryover["fills_this_run"] is True


def test_order_ids_after_checkpoint_for_trade_before_checkpoint_reconciles(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T10:00:00Z"),
            _order_ids("2026-07-01T13:00:00Z", order_id="77777777"),
        ],
    )
    report = build_trade_chain_report(
        journal_dir=journal_dir,
        repo_root=tmp_path,
        since_ts="2026-07-01T12:00:00Z",
        use_checkpoint=False,
    )
    assert report["summary"]["unmatched_order_ids"] == 0
    assert report["detail"]["unmatched_order_ids"] == []


def test_end_to_end_checkpoint_lifecycle_does_not_false_flag_late_outcome(tmp_path: Path) -> None:
    """The exact scenario from the review: TRADE id=ABC before a saved
    checkpoint, OUTCOME id=ABC arrives after it. Run 2 (which uses the saved
    checkpoint, not an explicit since_ts) must not report it unmatched."""
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(day, [_trade("2026-07-01T10:00:00Z")])

    run1 = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert run1["status"] == "PASS"  # unresolved-but-on-latest-day is not a failure
    assert load_checkpoint(tmp_path) == "2026-07-01T10:00:00Z"

    _write_jsonl(
        day, [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T13:00:00Z", result="WIN", pnl=25.0)]
    )
    run2 = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert run2["status"] == "PASS"
    assert run2["summary"]["unmatched_outcomes"] == 0
    assert run2["summary"]["carryover_resolutions"] == 1


# ---------------------------------------------------------------------------
# Journal-integrity fingerprint: detect rotation/truncation/rewrite, or a
# backdated append behind the checkpoint boundary, between runs.
# ---------------------------------------------------------------------------


def test_journal_integrity_ok_on_unchanged_history(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T10:30:00Z")],
    )
    run1 = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert run1["status"] == "PASS"

    _write_jsonl(
        journal_dir / "journal_2026-07-02.jsonl",
        [_trade("2026-07-02T10:00:00Z"), _outcome("2026-07-02T10:30:00Z")],
    )
    run2 = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert run2["journal_integrity"]["status"] == "OK"
    assert run2["status"] == "PASS"


def test_journal_integrity_detects_shrink_and_falls_back_to_full_scan(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(
        day,
        [
            _trade("2026-07-01T10:00:00Z"),
            _outcome("2026-07-01T10:30:00Z"),
            _trade("2026-07-01T11:00:00Z"),
            _outcome("2026-07-01T11:30:00Z"),
        ],
    )
    run1 = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert run1["status"] == "PASS"

    # Simulate truncation/rewrite: one previously-counted row behind the
    # checkpoint boundary disappears.
    _write_jsonl(day, [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T10:30:00Z")])
    run2 = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert run2["journal_integrity"]["status"] == "SHRUNK"
    assert run2["status"] == "FAIL"
    # The checkpoint boundary was not trusted -- everything remaining was rescanned.
    assert run2["window"]["effective_since_ts_exclusive"] is None


def test_journal_integrity_detects_backdated_append_behind_checkpoint(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(day, [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T10:30:00Z")])
    run1 = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert run1["status"] == "PASS"
    checkpoint_ts = load_checkpoint(tmp_path)

    # Simulate a late/backdated append: a row with a timestamp BEHIND the
    # checkpoint boundary shows up in an already-processed day file.
    _write_jsonl(
        day,
        [
            _trade("2026-07-01T09:00:00Z"),  # backdated, before checkpoint_ts
            _outcome("2026-07-01T09:30:00Z"),
            _trade("2026-07-01T10:00:00Z"),
            _outcome("2026-07-01T10:30:00Z"),
        ],
    )
    run2 = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert run2["journal_integrity"]["status"] == "GREW_BEHIND_CHECKPOINT"
    assert run2["status"] == "FAIL"
    assert checkpoint_ts is not None


def test_journal_integrity_unknown_for_legacy_checkpoint_without_fingerprint(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T10:30:00Z")],
    )
    # A checkpoint saved without the fingerprint field (as if written by a
    # pre-fix version of this tool).
    save_checkpoint(tmp_path, "2026-07-01T10:30:00Z")
    assert "entries_at_or_before_count" not in (load_checkpoint_full(tmp_path) or {})

    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert report["journal_integrity"]["status"] == "UNKNOWN"
    # Absence of a fingerprint alone must not force a FAIL.
    assert report["status"] == "PASS"


def test_journal_integrity_detects_same_count_content_rewrite(tmp_path: Path) -> None:
    """Required regression: a row count can stay identical while the actual
    content behind the checkpoint changes (e.g. WIN silently rewritten to
    LOSS with the same timestamp) -- a count-only fingerprint cannot see
    this. The content hash must."""
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(
        day,
        [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T10:30:00Z", result="WIN", pnl=25.0)],
    )
    run1 = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert run1["status"] == "PASS"

    # Rewrite the WIN row to LOSS, same timestamp, same total row count.
    _write_jsonl(
        day,
        [_trade("2026-07-01T10:00:00Z"), _outcome("2026-07-01T10:30:00Z", result="LOSS", pnl=-25.0)],
    )
    run2 = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert run2["journal_integrity"]["status"] == "MUTATED"
    assert run2["status"] == "FAIL"
    # Count-only comparison alone would have missed this.
    integrity = run2["journal_integrity"]
    assert integrity["current_at_or_before_count"] == integrity["recorded_at_or_before_count"]
    assert integrity["current_content_fingerprint"] != integrity["recorded_content_fingerprint"]
    # The checkpoint boundary was not trusted -- full rescan.
    assert run2["window"]["effective_since_ts_exclusive"] is None


def test_journal_integrity_detects_same_count_field_level_mutation(tmp_path: Path) -> None:
    """Same-count mutation of a single field (strategy name here; the
    reviewer also named order ID and P&L as representative) must trip the
    same MUTATED detection as a result-field rewrite."""
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    day = journal_dir / "journal_2026-07-01.jsonl"
    _write_jsonl(
        day,
        [_trade("2026-07-01T10:00:00Z", strategy="orb_breakout"), _outcome("2026-07-01T10:30:00Z")],
    )
    run1 = build_trade_chain_report(
        journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True, advance_checkpoint=True
    )
    assert run1["status"] == "PASS"

    _write_jsonl(
        day,
        [_trade("2026-07-01T10:00:00Z", strategy="vwap_hold"), _outcome("2026-07-01T10:30:00Z")],
    )
    run2 = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=True)
    assert run2["journal_integrity"]["status"] == "MUTATED"
    assert run2["status"] == "FAIL"


# ---------------------------------------------------------------------------
# Entry model / effective tolerance in trade-chain monitoring. The same
# lesson ops.project_check.promotion's execution-context check encodes for
# promotion evidence -- entry fill model and effective tolerance must be
# verified against the live runtime, not assumed -- applied here to actual
# fills.
# ---------------------------------------------------------------------------


def test_execution_context_reports_live_runtime_config(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_FILL_MODEL", "ioc_limit")
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    ec = report["execution_context"]
    assert ec["live_entry_fill_model"] == "ioc_limit"
    assert ec["live_entry_tolerance_ticks"]["MNQ"]["effective_replay_paper"] == 32.0


def test_fill_within_live_tolerance_reports_slippage_and_passes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    outcome_base = _outcome("2026-07-01T14:30:00Z")
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            {
                **outcome_base,
                "outcome": {
                    **outcome_base["outcome"],
                    "requested_entry": 30000.0,
                    "entry_price": 30002.0,
                    "ticks_moved_from_entry": 8.0,
                },
            },
        ],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    fill = report["detail"]["resolved_fills"][0]
    exec_ctx = fill["entry_execution"]
    assert exec_ctx["slippage_ticks"] == 8.0
    assert exec_ctx["within_tolerance"] is True
    assert report["summary"]["fills_exceeding_tolerance"] == 0


def test_fill_exceeding_live_tolerance_fails_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ", "32")
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    outcome_base = _outcome("2026-07-01T14:30:00Z")
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [
            _trade("2026-07-01T14:00:00Z"),
            {
                **outcome_base,
                "outcome": {
                    **outcome_base["outcome"],
                    "requested_entry": 30000.0,
                    "entry_price": 30050.0,
                    "ticks_moved_from_entry": 200.0,
                },
            },
        ],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "FAIL"
    assert report["summary"]["fills_exceeding_tolerance"] == 1
    flagged = report["detail"]["fills_exceeding_tolerance"][0]
    assert flagged["entry_execution"]["within_tolerance"] is False


def test_fill_missing_slippage_evidence_is_unknown_not_a_failure(tmp_path: Path) -> None:
    journal_dir = tmp_path / "logs"
    journal_dir.mkdir()
    _write_jsonl(
        journal_dir / "journal_2026-07-01.jsonl",
        [_trade("2026-07-01T14:00:00Z"), _outcome("2026-07-01T14:30:00Z")],
    )
    report = build_trade_chain_report(journal_dir=journal_dir, repo_root=tmp_path, use_checkpoint=False)
    assert report["status"] == "PASS"
    fill = report["detail"]["resolved_fills"][0]
    assert fill["entry_execution"]["slippage_ticks"] is None
    assert fill["entry_execution"]["within_tolerance"] is None
    assert report["summary"]["fills_exceeding_tolerance"] == 0
