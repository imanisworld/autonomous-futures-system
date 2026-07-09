from __future__ import annotations

import json
from pathlib import Path

from ops.direction_authority_audit import build_audit, format_report


def _write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _row(
    *,
    source: str = "live",
    ts: str = "2026-07-08T13:00:00+00:00",
    bar_ts: str | None = None,
    pine_direction: str = "LONG",
    current: str = "two_up",
    previous: str = "inside_bar",
    two_back: str = "two_up",
    pine_sequence: str = "strat_212",
    selected_direction: str | None = "LONG",
    selected_strategy: str = "vwap_reclaim",
    decision: str = "TRADE",
) -> dict:
    row = {
        "source": source,
        "ts": ts,
        "instrument": "MNQ",
        "session": "new_york",
        "decision": decision,
        "reason": "fixture",
        "context": {
            "timestamp": bar_ts or ts,
            "strat": {
                "current_bar_type": current,
                "previous_bar_type": previous,
                "two_bars_back_type": two_back,
                "strat_sequence": pine_sequence,
                "strat_trigger": "continuation",
                "strat_direction": pine_direction,
            },
        },
        "candidate_audit": [],
    }
    if bar_ts is not None:
        row["bar_ts"] = bar_ts
    if selected_direction is not None:
        row["setup"] = {
            "strategy": selected_strategy,
            "direction": selected_direction,
            "entry": 100.0,
            "stop": 95.0,
            "target": 110.0,
            "rr_ratio": 2.0,
        }
        row["candidate_audit"] = [
            {
                "strategy": selected_strategy,
                "direction": selected_direction,
                "candidate_direction": selected_direction,
                "selected": True,
                "winner": True,
                "rank_score": 700.0,
            }
        ]
    return row


def test_direction_authority_audit_flags_pine_local_mismatch_selection_impact(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _row(
                pine_direction="LONG",
                current="two_down",
                previous="inside_bar",
                two_back="two_down",
                pine_sequence="strat_212",
                selected_direction="LONG",
            )
        ],
    )

    report = build_audit(paths=[journal])

    assert report["read_only"] is True
    assert report["summary"]["decision_rows"] == 1
    assert report["summary"]["comparable_direction_rows"] == 1
    assert report["summary"]["direction_mismatch_rows"] == 1
    assert report["summary"]["selection_direction_changed_rows"] == 1
    assert report["summary"]["direction_mismatch_source_counts"] == {"live": 1}

    row = report["rows"][0]
    assert row["pine_direction"] == "LONG"
    assert row["local_direction"] == "SHORT"
    assert row["selection_direction"] == "LONG"
    assert row["selection_impact"] == "selected_pine_direction_over_local"
    assert row["direction_mismatch"] is True

    text = format_report(report)
    assert "Direction Authority Audit" in text
    assert "pine=LONG local=SHORT selected=vwap_reclaim/LONG" in text
    assert "impact=selected_pine_direction_over_local" in text


def test_direction_authority_audit_marks_metadata_only_difference_harmless(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _row(
                pine_direction="LONG",
                current="two_up",
                previous="inside_bar",
                two_back="two_down",
                pine_sequence="strat_212",
                selected_direction="LONG",
            )
        ],
    )

    report = build_audit(paths=[journal])

    assert report["summary"]["direction_mismatch_rows"] == 0
    assert report["summary"]["harmless_metadata_difference_rows"] == 1
    assert report["rows"][0]["status"] == "metadata_only_difference"
    assert report["rows"][0]["harmless_metadata_difference"] is True
    assert report["rows"][0]["metadata_differences"] == [
        {"field": "strat_sequence", "pine": "strat_212", "local": "strat_inside_break"},
        {"field": "strat_trigger", "pine": "continuation", "local": "breakout"},
    ]


def test_direction_authority_audit_groups_live_replay_disagreements(tmp_path):
    bar_ts = "2026-07-08T13:15:00+00:00"
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _row(source="live", ts=bar_ts, bar_ts=bar_ts, pine_direction="LONG", selected_direction="LONG"),
            _row(
                source="replay",
                ts=bar_ts,
                bar_ts=bar_ts,
                pine_direction="SHORT",
                current="two_down",
                previous="inside_bar",
                two_back="two_down",
                selected_direction="SHORT",
            ),
        ],
    )

    report = build_audit(paths=[journal])

    assert report["summary"]["source_counts"] == {"live": 1, "replay": 1}
    assert report["summary"]["live_replay_disagreement_count"] == 1
    disagreement = report["live_replay_disagreements"][0]
    assert disagreement["row_key"] == f"MNQ|{bar_ts}"
    assert disagreement["disagreement_fields"] == [
        "pine_direction",
        "local_direction",
        "selection_direction",
    ]
    assert {row["source"] for row in disagreement["rows"]} == {"live", "replay"}


def test_direction_authority_audit_keeps_no_selection_mismatch_separate(tmp_path):
    journal = _write_jsonl(
        tmp_path / "journal_2026-07-08.jsonl",
        [
            _row(
                decision="NO_TRADE",
                pine_direction="LONG",
                current="two_down",
                previous="inside_bar",
                two_back="two_down",
                selected_direction=None,
            )
        ],
    )

    report = build_audit(paths=[journal])

    assert report["summary"]["direction_mismatch_rows"] == 1
    assert report["summary"]["selection_direction_changed_rows"] == 0
    assert report["rows"][0]["selection_impact"] == "no_final_selection"
