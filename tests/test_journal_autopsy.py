from __future__ import annotations

import json

from scripts.journal_autopsy import pair_journal, summarize


def _trade(
    instrument: str,
    *,
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 90.0,
    target: float = 120.0,
) -> dict:
    return {
        "ts": "2026-06-01T14:00:00+00:00",
        "instrument": instrument,
        "session": "new_york",
        "decision": "TRADE",
        "risk_check": {"result": "APPROVED"},
        "setup": {
            "strategy": "orb_breakout",
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "contracts": 1,
        },
    }


def _outcome(instrument: str, result: str, **fields) -> dict:
    return {
        "ts": "2026-06-01T14:05:00+00:00",
        "type": "OUTCOME",
        "instrument": instrument,
        "outcome": {"result": result, **fields},
    }


def _write(path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_pair_journal_matches_trade_and_outcome_fifo_by_instrument(tmp_path):
    journal = tmp_path / "journal_2026-06-01.jsonl"
    _write(
        journal,
        [
            _trade("MNQ"),
            _trade("MES", entry=5000.0, stop=4990.0, target=5020.0),
            _outcome("MNQ", "WIN", exit_price=120.0, pnl_dollars=40.0),
            _outcome("MES", "LOSS", exit_price=4990.0, pnl_dollars=-50.0),
        ],
    )

    rows = pair_journal([journal])

    assert [row.instrument for row in rows] == ["MNQ", "MES"]
    assert rows[0].result == "WIN"
    assert rows[1].result == "LOSS"


def test_lost_winner_detected_only_when_loss_has_mfe_past_target(tmp_path):
    journal = tmp_path / "journal_2026-06-01.jsonl"
    _write(
        journal,
        [
            _trade("MNQ"),
            _outcome("MNQ", "LOSS", mfe_R=2.1, exit_price=90.0, pnl_dollars=-20.0),
            _trade("MNQ"),
            _outcome("MNQ", "LOSS", mfe_R=1.2, exit_price=90.0, pnl_dollars=-20.0),
        ],
    )

    summary = summarize(pair_journal([journal]))

    assert summary["MNQ"]["losses"] == 2
    assert summary["MNQ"]["losses_with_mfe_evidence"] == 2
    assert summary["MNQ"]["lost_winners"] == 1
    assert summary["MNQ"]["lost_winner_pct_of_losses_with_evidence"] == 50.0


def test_wins_report_mfe_past_target_when_journaled(tmp_path):
    journal = tmp_path / "journal_2026-06-01.jsonl"
    _write(
        journal,
        [
            _trade("MNQ"),
            _outcome("MNQ", "WIN", mfe_R=2.8, exit_price=120.0, pnl_dollars=40.0),
            _trade("MNQ"),
            _outcome("MNQ", "WIN", mfe_R=2.2, exit_price=120.0, pnl_dollars=40.0),
        ],
    )

    summary = summarize(pair_journal([journal]))

    assert summary["MNQ"]["wins_with_past_target_evidence"] == 2
    assert summary["MNQ"]["median_past_target_r_on_wins"] == 0.5
    assert summary["MNQ"]["max_past_target_r_on_wins"] == 0.8


def test_no_mfe_losses_are_reported_as_unverified_not_lost_winners(tmp_path):
    journal = tmp_path / "journal_2026-06-01.jsonl"
    _write(
        journal,
        [
            _trade("MES"),
            _outcome("MES", "LOSS", exit_price=90.0, pnl_dollars=-20.0),
        ],
    )

    summary = summarize(pair_journal([journal]))

    assert summary["MES"]["losses"] == 1
    assert summary["MES"]["losses_with_mfe_evidence"] == 0
    assert summary["MES"]["losses_without_mfe_evidence"] == 1
    assert summary["MES"]["lost_winners"] == 0
    assert summary["MES"]["lost_winner_pct_of_losses_with_evidence"] is None


def test_exit_past_target_used_when_no_mfe_field_exists(tmp_path):
    journal = tmp_path / "journal_2026-06-01.jsonl"
    _write(
        journal,
        [
            _trade("MNQ"),
            _outcome("MNQ", "WIN", exit_price=125.0, pnl_dollars=50.0),
        ],
    )

    summary = summarize(pair_journal([journal]))

    assert summary["MNQ"]["wins_with_past_target_evidence"] == 1
    assert summary["MNQ"]["median_past_target_r_on_wins"] == 0.5
