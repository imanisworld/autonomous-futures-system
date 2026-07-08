from __future__ import annotations

import json
from pathlib import Path

from ops.strategy_intent_audit import build_audit, format_report


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _candidate(
    strategy: str,
    *,
    selected: bool = False,
    attempted: bool = True,
    fallback_skipped: bool = False,
    reject_code: str | None = None,
) -> dict:
    return {
        "strategy": strategy,
        "direction": "LONG",
        "candidate_direction": "LONG",
        "entry": 100.0,
        "stop": 95.0,
        "target": 110.0,
        "rr_ratio": 2.0,
        "rank_score": 720.0,
        "rank_reason": "ranked candidate: confluence 7/10 A, R:R 2.00",
        "selected": selected,
        "winner": selected,
        "attempted": attempted,
        "fallback_enabled": False,
        "fallback_skipped": fallback_skipped,
        "skip_reason": "fallback_disabled_after_rejection" if fallback_skipped else None,
        "reject_code": reject_code,
        "reject_reason": "entry detached" if reject_code else None,
        "failed_gates": [reject_code] if reject_code else [],
        "context_ref": "journal.context",
        "market_condition": "TRENDING",
        "regime": "FULL_LONG",
        "stale_data_flags": ["zone_state_stale"] if strategy == "orb_reclaim" else [],
    }


def test_strategy_intent_audit_summarizes_candidates_fallback_and_shadows(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            {
                "ts": "2026-07-08T13:00:00+00:00",
                "instrument": "MNQ",
                "decision": "TRADE",
                "reason": "Setup qualified",
                "market_condition": "TRENDING",
                "regime": "FULL_LONG",
                "failed_gates": [],
                "setup": {"strategy": "vwap_reclaim", "direction": "LONG", "entry": 100.0},
                "candidate_audit": [
                    _candidate("vwap_reclaim", selected=True),
                    _candidate("orb_reclaim", attempted=False, fallback_skipped=True),
                ],
                "context": {
                    "trend": {"direction": "UP", "strength": "STRONG"},
                    "htf": {"daily_direction": "UP", "four_hour_direction": "UP"},
                    "close": 101.0,
                },
                "shadow_candidates": [
                    {
                        "strategy": "orb_false_break_fade",
                        "direction": "SHORT",
                        "entry": 99.0,
                        "outcome": {"result": "NO_FILL", "pnl_ticks": 0.0},
                    }
                ],
            },
            {
                "ts": "2026-07-08T13:15:00+00:00",
                "instrument": "MNQ",
                "decision": "NO_TRADE",
                "reason": "stale entry",
                "failed_gates": ["ENTRY_DETACHED_FROM_PRICE"],
                "candidate_audit": [
                    _candidate("orb_reclaim", reject_code="ENTRY_DETACHED_FROM_PRICE"),
                ],
            },
        ],
    )

    report = build_audit(paths=[journal])

    assert report["read_only"] is True
    assert report["summary"]["files_scanned"] == 1
    assert report["summary"]["decision_rows"] == 2
    assert report["summary"]["candidate_rows"] == 3
    assert report["summary"]["rows_missing_candidate_audit"] == 0
    assert report["summary"]["fallback_skipped_rows"] == 1
    assert report["summary"]["rows_with_stale_flags"] == 2
    assert report["summary"]["shadow_match_rows"] == 1
    assert report["summary"]["selected_strategy_counts"] == {"none": 1, "vwap_reclaim": 1}
    assert report["summary"]["fallback_skipped_strategy_counts"] == {"orb_reclaim": 1}
    assert report["summary"]["shadow_strategy_counts"] == {"orb_false_break_fade": 1}

    first = report["decisions"][0]
    assert first["selected_candidate"]["strategy"] == "vwap_reclaim"
    assert first["candidates"][1]["fallback_skipped"] is True
    assert first["market_context"]["context_ref"] == "journal.context"
    assert first["shadow_matches"][0]["strategy"] == "orb_false_break_fade"

    text = format_report(report)
    assert "Strategy Intent Audit" in text
    assert "fallback_skipped=True" in text
    assert "Shadow strategies: {'orb_false_break_fade': 1}" in text


def test_strategy_intent_audit_reports_missing_candidate_audit_and_rank(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            {
                "ts": "2026-07-08T14:00:00+00:00",
                "instrument": "MES",
                "decision": "TRADE",
                "setup": {"strategy": "orb_reclaim", "direction": "LONG"},
                "candidate_audit": [],
            },
            {
                "ts": "2026-07-08T14:15:00+00:00",
                "instrument": "MES",
                "decision": "NO_TRADE",
                "candidate_audit": [{"strategy": "vwap_hold", "direction": "SHORT"}],
            },
        ],
    )

    report = build_audit(paths=[journal])

    assert report["summary"]["rows_missing_candidate_audit"] == 1
    assert report["summary"]["candidate_rows_missing_rank_score"] == 1


def test_strategy_intent_audit_reports_parse_errors(tmp_path):
    journal = tmp_path / "journal_2026-07-08.jsonl"
    journal.write_text('{"decision": "WAIT"}\nnot-json\n', encoding="utf-8")

    report = build_audit(paths=[journal])

    assert report["summary"]["issue_count"] == 1
    assert report["issues"][0]["code"] == "journal_read_error"
