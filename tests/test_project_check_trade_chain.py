from __future__ import annotations

import json
from pathlib import Path

from ops.project_check.trade_chain import build_trade_chain_report, load_checkpoint, save_checkpoint


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
