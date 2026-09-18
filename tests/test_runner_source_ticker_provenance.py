from datetime import date
from pathlib import Path

import pytest

from context.bar_history import BarHistory
from tests.test_e2e_scenarios import _base_config, _base_payload
from webhook.runner import process_alert


@pytest.mark.parametrize(
    ("ticker", "root"),
    [("MNQ1!", "MNQ"), ("MES1!", "MES")],
)
def test_runner_persists_exact_source_ticker_without_changing_root_identity(
    tmp_path, ticker, root
):
    today = date(2026, 5, 23)
    cfg = _base_config(tmp_path)
    overrides = {"ticker": ticker}
    if root == "MES":
        overrides.update(
            open=5480.0,
            high=5510.0,
            low=5475.0,
            close=5505.25,
            vwap=5495.0,
            orb_high=5498.0,
            orb_low=5462.0,
            previous_day_high=5520.0,
            previous_day_low=5440.0,
            previous_day_close=5475.0,
            previous_bar_high=5500.0,
            previous_bar_low=5490.0,
        )
    payload = _base_payload(**overrides)

    result = process_alert(
        payload,
        config=cfg,
        log_dir=cfg.log_dir,
        for_date=today,
    )

    bars = BarHistory(log_dir=cfg.log_dir).recent(
        root, 1, for_date=today, lookback_days=1
    )
    assert len(bars) == 1
    assert bars[0]["source_ticker"] == ticker
    assert result["instrument"] == root
    assert not (
        Path(cfg.log_dir) / f"bars_{ticker}_{today.isoformat()}.jsonl"
    ).exists()
