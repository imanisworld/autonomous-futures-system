from __future__ import annotations

import json

from scripts.session_hold_location_report import _candidate_key, build_report


def _candidate_row(*, session: str, ts: str, direction: str = "LONG") -> dict:
    return {
        "ts": ts,
        "instrument": "MES",
        "session": session,
        "context": {
            "location_context": {
                "impulse": {"phase": "fresh"},
            }
        },
        "shadow_candidates": [
            {
                "strategy": "trend_consolidation_break_observed",
                "direction": direction,
                "entry": 100.0,
                "stop": 99.0,
                "target": 102.0,
                "location": {
                    "middle_of_range": False,
                    "direction_zone_alignment": "aligned",
                    "target_blocked_by_opposing_zone": False,
                },
            }
        ],
    }


def _outcome(row: dict, result: str) -> dict:
    candidate = row["shadow_candidates"][0]
    return {
        "type": "SHADOW_OUTCOME",
        "lane": "shadow_setups",
        "final": True,
        "candidate_key": _candidate_key(row, candidate),
        "shadow_outcome": {"result": result},
    }


def _write_rows(path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_report_keeps_opposite_asian_london_results_separate(tmp_path):
    asian = _candidate_row(session="asian", ts="2026-07-16T02:00:00+00:00")
    london = _candidate_row(session="london", ts="2026-07-16T08:00:00+00:00")
    path = tmp_path / "journal.jsonl"
    _write_rows(path, [asian, london, _outcome(asian, "WIN"), _outcome(london, "LOSS")])

    report = build_report([path], before=None)

    assert "**Verdict: MIXED.**" in report
    assert "opposite after-cost expectancy signs" in report
    assert "| asian | 1 | 1 | 1 | 1/0/0/0 |" in report
    assert "| london | 1 | 1 | 1 | 0/1/0/0 |" in report


def test_direct_hold_count_excludes_unrelated_shadow_no_order_rows(tmp_path):
    path = tmp_path / "journal.jsonl"
    _write_rows(
        path,
        [
            {
                "session": "asian",
                "decision": "SHADOW_NO_ORDER",
                "gate_reason": "demo_execution_hold session=asian",
            },
            {
                "session": "london",
                "decision": "SHADOW_NO_ORDER",
                "gate_reason": "schedule_mode=always_on_shadow",
            },
            {
                "session": "new_york",
                "decision": "SHADOW_NO_ORDER",
                "gate_reason": "demo_execution_hold session=new_york",
            },
        ],
    )

    report = build_report([path], before=None)

    assert "Direct hold-gate suppressions (`demo_execution_hold`): **1**." in report
