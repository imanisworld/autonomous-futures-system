from __future__ import annotations

import json

from scripts.rematerialize_market_condition_corpus import rematerialize


def test_rematerialize_promotes_canonical_and_preserves_legacy(tmp_path):
    source = tmp_path / "source" / "MNQ"
    source.mkdir(parents=True)
    rows = [
        {
            "market_condition": "TRENDING",
            "reconstructed_market_condition": "DEAD",
            "reconstructed_market_condition_status": "RECONSTRUCTED_UNVALIDATED_INIT",
        },
        {
            "market_condition": "CHOPPY",
            "reconstructed_market_condition": "TRENDING",
            "reconstructed_market_condition_status": "RECONSTRUCTED",
        },
        {
            "market_condition": "TRENDING",
            "reconstructed_market_condition": None,
            "reconstructed_market_condition_status": "UNAVAILABLE_WARMUP",
        },
    ]
    source_path = source / "MNQ_2026-01-01.jsonl"
    source_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    output = tmp_path / "output"
    report = rematerialize(tmp_path / "source", output)
    materialized = [
        json.loads(line)
        for line in (output / "MNQ" / source_path.name).read_text().splitlines()
    ]

    assert [row["market_condition"] for row in materialized] == [
        "DEAD",
        "TRENDING",
        None,
    ]
    assert [row["legacy_market_condition"] for row in materialized] == [
        "TRENDING",
        "CHOPPY",
        "TRENDING",
    ]
    assert report["bars_compared"] == 3
    assert report["comparable_bars"] == 2
    assert report["initialization_or_missing_data_exclusions"] == 1
    assert report["mismatch_count_before"] == 2
    assert report["mismatch_count_after"] == 0
    assert report["trending_removed"] == 1
    assert report["trending_added"] == 1
