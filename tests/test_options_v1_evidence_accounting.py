"""Regression tests for the three V1 evidence-accounting defects found by the
2026-09-09 market-hours smoke.

1. one distinct setup/candidate is ONE evidence episode, however many times the
   scanner ticks inside that setup's own bar;
2. COUNTERFACTUAL/observation rows never count toward trade n, wins/losses,
   profit factor or expectancy;
3. the journal `status` column records an underlying-target event and must never
   be read as a financial WIN/LOSS.

These lock the accounting only.  No setup, DTE, stop, target, filter or risk rule
is exercised or changed here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from alert_ranker.paper_v1 import episode_bucket, episode_key
from alert_ranker.storage import ScanStorage
from alert_ranker.v1_diagnostics import build_diagnostics_report, strategy_summary

CANDIDATE = "AAPL261120C00330000|4H_RTH|H4_222_REVERSAL|COUNTERFACTUAL"


# --- defect 1: one setup = one episode -------------------------------------

def test_repeated_ticks_inside_one_4h_block_are_one_episode():
    # The six real AAPL ticks from the 2026-09-09 smoke, 14:20Z..14:45Z.
    keys = {
        episode_key(CANDIDATE, "4H_RTH", datetime(2026, 9, 9, 14, minute, 53, tzinfo=timezone.utc))
        for minute in (20, 25, 30, 35, 40, 45)
    }
    assert len(keys) == 1, "one setup inside one 4H block must be one episode"


def test_daily_setup_is_one_episode_per_trading_date_not_per_tick():
    day = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    same_day = {
        episode_key("K|1D|DAILY_222_CONTINUATION|ACTIVE", "1D", day + timedelta(minutes=step))
        for step in range(0, 300, 5)
    }
    assert len(same_day) == 1
    next_day = episode_key(
        "K|1D|DAILY_222_CONTINUATION|ACTIVE", "1D", day + timedelta(days=1)
    )
    assert next_day not in same_day, "a new trading date is a new episode"


def test_each_timeframe_buckets_on_its_own_bar():
    at = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc)  # 10:20 ET
    assert episode_bucket("1D", at) == "1D:2026-09-09"
    assert episode_bucket("4H_RTH", at) == "4H_RTH:2026-09-09:0"
    assert episode_bucket("1H", at) == "1H:2026-09-09:10"
    assert episode_bucket("30m", at) == "30M:2026-09-09:10:00"
    # 30m grid advances on the half hour, the 1H/4H buckets do not.
    later = datetime(2026, 9, 9, 14, 45, tzinfo=timezone.utc)
    assert episode_bucket("30m", later) == "30M:2026-09-09:10:30"
    assert episode_bucket("1H", later) == "1H:2026-09-09:10"
    # An unmapped timeframe keeps its own label so it cannot collide.
    assert episode_bucket("8H", at).startswith("8H:")


def test_episode_duplicate_lookup_ignores_status(tmp_path):
    """The OPEN-only guard is why one setup became six rows: V1 episodes resolve
    on the very next tick, so the prior row is already closed."""
    storage = ScanStorage(tmp_path / "episode.sqlite")
    episode = episode_key(CANDIDATE, "4H_RTH", datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc))
    selected = {"contract_key": CANDIDATE, "episode_key": episode}
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO options_shadow_journal (
                id, timestamp, scan_id, ticker, direction, score, pattern, status,
                setup_inputs_json, provider_snapshot_json, selected_contract_json,
                outcome_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "2026-09-09T14:20:53+00:00",
                1,
                "AAPL",
                "LONG",
                8,
                "2-2-2",
                "LOSS",  # already closed, exactly as the live rows were
                "{}",
                "{}",
                json.dumps(selected),
                "{}",
            ),
        )
    # The old OPEN-only guard does not see a closed row...
    assert storage.find_open_duplicate("AAPL", CANDIDATE) is None
    # ...the episode guard does, which is what stops the per-tick re-journalling.
    assert storage.find_episode_duplicate("AAPL", episode) == 1
    assert storage.find_episode_duplicate("AAPL", "some-other-episode") is None


# --- defects 2 and 3: counterfactuals are not trades, status is not P&L -----

def _trade(lane: str, status: str, recorded_pnl: float) -> dict:
    return {
        "paper_evidence_lane": lane,
        "setup_type": "H4_222_REVERSAL",
        "timeframe": "4H_RTH",
        "status": status,
        "underlying_target_event": status,
        "evidence_quality": "MEDIUM",
        "option_mae_percent": 4.0,
        "friction_pnl_dollars": {
            "RECORDED_EXECUTABLE": recorded_pnl,
            "FEE_STRESS_065": recorded_pnl - 1.3,
            "FEE_065_PLUS_1C_SLIPPAGE": recorded_pnl - 3.3,
        },
    }


def test_counterfactual_group_reports_no_trade_metrics():
    [summary] = strategy_summary([_trade("COUNTERFACTUAL", "WIN", -7.0)])
    assert summary["is_trade_population"] is False
    assert summary["trade_metrics_suppressed_reason"] == "COUNTERFACTUAL_NOT_A_TRADE_POPULATION"
    assert summary["n_observations"] == 1
    for scoreable in (
        "n_total",
        "n_closed_priced",
        "wins",
        "losses",
        "win_rate_percent",
        "expectancy_dollars_per_trade",
        "profit_factor",
        "trade_sequence_max_drawdown_dollars",
        "friction_total_pnl_dollars",
    ):
        assert summary[scoreable] is None, f"{scoreable} must not be scored for observations"
    # Observational value is still published.
    assert summary["median_option_mae_percent"] == 4.0
    assert summary["quality_counts"]["MEDIUM"] == 1


def test_active_group_still_reports_trade_metrics():
    [summary] = strategy_summary([_trade("ACTIVE", "WIN", 25.0)])
    assert summary["is_trade_population"] is True
    assert summary["trade_metrics_suppressed_reason"] is None
    assert summary["n_total"] == 1
    assert summary["wins"] == 1
    assert summary["expectancy_dollars_per_trade"] == 25.0


def test_wins_follow_pnl_not_the_journal_label():
    """A journal `WIN` whose option P&L is negative is a LOSS in the numbers."""
    [summary] = strategy_summary([_trade("ACTIVE", "WIN", -7.0)])
    assert summary["wins"] == 0
    assert summary["losses"] == 1


def test_financial_outcome_is_derived_from_pnl_only():
    from alert_ranker.v1_diagnostics import _financial_outcome

    # The exact live contradiction: journal says WIN, the money says loss.
    assert _financial_outcome(-7.0, "WIN") == "LOSS"
    assert _financial_outcome(25.0, "LOSS") == "PROFIT"
    assert _financial_outcome(0.0, "WIN") == "BREAKEVEN"
    assert _financial_outcome(None, "WIN") == "UNPRICED"
    assert _financial_outcome(None, "OPEN") == "OPEN"


def test_report_publishes_target_event_and_financial_outcome_separately(tmp_path):
    from tests.test_options_v1_diagnostics import _insert_shadow

    storage = ScanStorage(tmp_path / "labels.sqlite")
    t0 = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    _insert_shadow(
        storage,
        timestamp=t0,
        status="WIN",
        outcome={
            "resolved_at": (t0 + timedelta(minutes=5)).isoformat(),
            "exit_mark": 1.9,
            "option_bid_at_resolution": 1.9,
        },
    )
    [trade] = build_diagnostics_report(storage)["trades"]
    assert trade["underlying_target_event"] == "WIN"
    # Entry at ask 2.0, exit at bid 1.9 -> the "WIN" lost money.
    assert trade["friction_pnl_dollars"]["RECORDED_EXECUTABLE"] < 0
    assert trade["financial_outcome"] == "LOSS"
