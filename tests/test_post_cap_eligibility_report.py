from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from scripts.post_cap_eligibility_report import build_report


DAY = date(2026, 10, 5)


def _write(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _row(ts: str, *, eligible: bool, failed_rule: str | None = None) -> dict:
    risk = {
        "result": "APPROVED" if eligible else "REJECTED",
        "failed_rule": failed_rule,
        "reason": None,
    }
    return {
        "ts": ts,
        "instrument": "MNQ",
        "session": "new_york",
        "decision": "BLOCKED_MAX_TRADES",
        "observed_decision": "TRADE",
        "execution_block": {"code": "BLOCKED_MAX_TRADES", "limit": 3, "trade_count": 3},
        "setup": {
            "strategy": "orb_reclaim",
            "direction": "LONG",
            "entry": 20000.0,
            "stop": 19990.0,
            "target": 20020.0,
            "rr_ratio": 2.0,
        },
        "post_cap_eligibility": {
            "observation_only": True,
            "execution_reachable": False,
            "eligible_except_daily_cap": eligible,
            "risk_without_daily_cap": risk,
            "transformed_setup": {
                "strategy": "orb_reclaim",
                "direction": "LONG",
                "entry": 20000.0,
                "stop": 19990.0,
                "target": 20020.0,
                "rr_ratio": 2.0,
            },
        },
    }


def test_numbers_only_eligible_rows_as_shadow_trades(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    _write(
        log_dir / f"journal_{DAY.isoformat()}.jsonl",
        [
            _row("2026-10-05T14:00:00+00:00", eligible=True),
            _row(
                "2026-10-05T14:15:00+00:00",
                eligible=False,
                failed_rule="max_daily_loss",
            ),
            _row("2026-10-05T14:30:00+00:00", eligible=True),
        ],
    )

    report = build_report(log_dir, requested_date=DAY)

    assert report["outcome_scoring_enabled"] is False
    assert report["summary"] == {
        "raw_post_cap_candidates": 3,
        "eligible_except_cap": 2,
        "rejected_other_gate": 1,
        "unverified": 0,
        "errors": 0,
    }
    assert [r["shadow_trade_number"] for r in report["rows"]] == [4, None, 5]
    assert report["rows"][1]["failed_rule"] == "max_daily_loss"


def test_old_raw_candidate_without_eligibility_stays_unverified(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    row = _row("2026-10-05T14:00:00+00:00", eligible=True)
    row.pop("post_cap_eligibility")
    _write(log_dir / f"journal_{DAY.isoformat()}.jsonl", [row])

    report = build_report(log_dir, requested_date=DAY)

    assert report["summary"]["unverified"] == 1
    assert report["rows"][0]["status"] == "UNVERIFIED"
    assert report["rows"][0]["shadow_trade_number"] is None


def test_cap_mismatch_fails_closed(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    row = _row("2026-10-05T14:00:00+00:00", eligible=True)
    row["execution_block"]["limit"] = 4
    _write(log_dir / f"journal_{DAY.isoformat()}.jsonl", [row])

    with pytest.raises(ValueError, match="cap mismatch"):
        build_report(log_dir, requested_date=DAY, expected_cap=3)
