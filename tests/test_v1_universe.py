from pathlib import Path

import pytest

from alert_ranker.v1_universe import (
    DEFAULT_SOURCE,
    EXPECTED_CANDIDATE_COUNT,
    LEGACY_V1,
    load_candidate_universe,
    ticker_list,
)


def test_candidate_universe_is_149_unique_and_preserves_legacy_v1():
    entries = load_candidate_universe()
    tickers = ticker_list(entries)
    assert len(tickers) == EXPECTED_CANDIDATE_COUNT == 149
    assert len(set(tickers)) == len(tickers)
    assert LEGACY_V1.issubset(tickers)


def test_stale_sq_is_normalized_to_xyz_and_vix_is_excluded():
    entries = load_candidate_universe()
    tickers = ticker_list(entries)
    assert "XYZ" in tickers
    assert "SQ" not in tickers
    assert "VIX" not in tickers
    xyz = next(row for row in entries if row.ticker == "XYZ")
    assert xyz.source_ticker == "SQ"


def test_loader_is_read_only(tmp_path: Path):
    source = tmp_path / "watchlist.csv"
    source.write_bytes(DEFAULT_SOURCE.read_bytes())
    before = source.read_bytes()
    load_candidate_universe(source)
    assert source.read_bytes() == before


def test_source_count_drift_fails_closed(tmp_path: Path):
    lines = DEFAULT_SOURCE.read_text(encoding="utf-8").splitlines()
    source = tmp_path / "watchlist.csv"
    source.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="source universe drift"):
        load_candidate_universe(source)


def test_alias_collision_fails_closed(tmp_path: Path):
    text = DEFAULT_SOURCE.read_text(encoding="utf-8")
    # Keep the 150-row count but make an unrelated row normalize to XYZ too.
    text = text.replace("PYPL,Financials,Fintech - momentum name", "XYZ,Financials,Fintech - momentum name")
    source = tmp_path / "watchlist.csv"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate ticker after alias normalization"):
        load_candidate_universe(source)
