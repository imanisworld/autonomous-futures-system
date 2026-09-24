from datetime import date, datetime, timezone

import pytest

from research.cpi_shock_fade_shadow import (
    EvidenceError,
    append_record,
    evaluate_shadow,
    validate_forward_date,
)
from sources.polygon_client import PolygonBar


def _bar(ts, o, h=None, l=None, c=None):
    return PolygonBar(
        ts=datetime.fromisoformat(ts).astimezone(timezone.utc),
        open=float(o),
        high=float(o if h is None else h),
        low=float(o if l is None else l),
        close=float(o if c is None else c),
        volume=1.0,
        ticker="MNQZ6",
    )


def _bars_down_shock():
    # 2026-10-14 is EDT. 12:30Z = 08:30 ET.
    return [
        _bar("2026-10-14T12:30:00+00:00", 25000),
        _bar("2026-10-14T12:35:00+00:00", 24950),
        _bar("2026-10-14T12:40:00+00:00", 24940, h=24948, l=24930),
        _bar("2026-10-14T12:41:00+00:00", 24945, h=24955, l=24935),
        _bar("2026-10-14T12:42:00+00:00", 24950, h=24970, l=24940),
        _bar("2026-10-14T13:29:00+00:00", 24958, h=24965, l=24952),
        _bar("2026-10-14T13:30:00+00:00", 24960),
    ]


def _bars_up_shock():
    return [
        _bar("2026-10-14T12:30:00+00:00", 25000),
        _bar("2026-10-14T12:35:00+00:00", 25050),
        _bar("2026-10-14T12:40:00+00:00", 25040, h=25060, l=25030),
        _bar("2026-10-14T12:41:00+00:00", 25035, h=25050, l=25020),
        _bar("2026-10-14T12:42:00+00:00", 25030, h=25040, l=25010),
        _bar("2026-10-14T13:29:00+00:00", 25022, h=25030, l=25015),
        _bar("2026-10-14T13:30:00+00:00", 25020),
    ]


def test_forward_calendar_is_frozen():
    assert validate_forward_date("2026-10-14") == date(2026, 10, 14)
    assert validate_forward_date("2026-11-10") == date(2026, 11, 10)
    assert validate_forward_date("2026-12-10") == date(2026, 12, 10)
    with pytest.raises(EvidenceError, match="not in the frozen"):
        validate_forward_date("2026-10-15")


def test_down_shock_becomes_long_and_records_bar_proxy_limits():
    row = evaluate_shadow("2026-10-14", "MNQZ6", _bars_down_shock())

    assert row["signal"]["side"] == "LONG"
    assert row["shadow_trade"]["entry_price_proxy"] == 24940
    assert row["shadow_trade"]["exit_price_proxy"] == 24960
    assert row["shadow_trade"]["gross_points"] == 20
    assert row["shadow_trade"]["gross_dollars"] == 40
    assert row["shadow_trade"]["mae_dollars"] == 20
    assert row["shadow_trade"]["mfe_dollars"] == 60
    assert row["shadow_trade"]["cost_stress_net_dollars"]["20"] == 20
    assert row["evidence_quality"]["bid_ask_observed"] is False
    assert row["evidence_quality"]["fill_quality_verified"] is False
    assert row["safety"]["observation_only"] is True
    assert row["safety"]["execution_authorized"] is False
    assert row["safety"]["order_submission_possible"] is False


def test_up_shock_becomes_short():
    row = evaluate_shadow("2026-10-14", "MNQZ6", _bars_up_shock())

    assert row["signal"]["side"] == "SHORT"
    assert row["shadow_trade"]["gross_points"] == 20
    assert row["shadow_trade"]["gross_dollars"] == 40
    assert row["shadow_trade"]["mae_dollars"] == 40
    assert row["shadow_trade"]["mfe_dollars"] == 60


def test_missing_required_bar_rejects_evidence():
    bars = [bar for bar in _bars_down_shock() if bar.ts.minute != 40]
    with pytest.raises(EvidenceError, match="08:40"):
        evaluate_shadow("2026-10-14", "MNQZ6", bars)


def test_append_is_immutable_and_duplicate_safe(tmp_path):
    row = evaluate_shadow("2026-10-14", "MNQZ6", _bars_down_shock())
    path = tmp_path / "cpi.jsonl"

    append_record(row, path)
    assert path.read_text().count("\n") == 1

    with pytest.raises(EvidenceError, match="duplicate immutable"):
        append_record(row, path)
