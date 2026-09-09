"""Regression tests for the three V1 evidence-accounting defects found by the
2026-09-09 market-hours smoke.

1. one distinct UNDERLYING setup is ONE evidence episode, however many times the
   scanner ticks inside that setup's own bar and whatever option contract the
   delta-targeted selection happens to pick on each tick;
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

from alert_ranker.paper_v1 import episode_bucket, setup_episode_key
from alert_ranker.storage import ScanStorage
from alert_ranker.v1_diagnostics import build_diagnostics_report, strategy_summary

CANDIDATE = "AAPL261120C00330000|4H_RTH|H4_222_REVERSAL|COUNTERFACTUAL"


def _key(moment, *, ticker="AAPL", lane="COUNTERFACTUAL", timeframe="4H_RTH",
         setup_type="H4_222_REVERSAL", direction="LONG", trigger=330.0):
    return setup_episode_key(
        ticker=ticker, lane=lane, timeframe=timeframe, setup_type=setup_type,
        direction=direction, trigger=trigger, moment=moment,
    )


# --- defect 1: one setup = one episode -------------------------------------

def test_repeated_ticks_inside_one_4h_block_are_one_episode():
    # The six real AAPL ticks from the 2026-09-09 smoke, 14:20Z..14:45Z.
    keys = {
        _key(datetime(2026, 9, 9, 14, minute, 53, tzinfo=timezone.utc))
        for minute in (20, 25, 30, 35, 40, 45)
    }
    assert len(keys) == 1, "one setup inside one 4H block must be one episode"


def test_daily_setup_is_one_episode_per_trading_date_not_per_tick():
    day = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    same_day = {
        _key(day + timedelta(minutes=step), lane="ACTIVE", timeframe="1D",
             setup_type="DAILY_222_CONTINUATION", trigger=314.9)
        for step in range(0, 300, 5)
    }
    assert len(same_day) == 1
    next_day = _key(day + timedelta(days=1), lane="ACTIVE", timeframe="1D",
                    setup_type="DAILY_222_CONTINUATION", trigger=314.9)
    assert next_day not in same_day, "a new trading date is a new episode"


def test_each_timeframe_buckets_on_its_own_bar():
    at = datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc)  # 10:20 ET
    assert episode_bucket("1D", at) == "1D:2026-09-09"
    assert episode_bucket("4H_RTH", at) == "4H_RTH:2026-09-09:0"
    assert episode_bucket("1H", at) == "1H:2026-09-09:0"  # 9:30-10:30 ET block
    assert episode_bucket("30m", at) == "30M:2026-09-09:10:00"
    # 30m grid advances on the half hour; the 4H bucket does not, and the 1H
    # bucket advances at 10:30 ET (session-anchored), not at the clock hour.
    later = datetime(2026, 9, 9, 14, 45, tzinfo=timezone.utc)
    assert episode_bucket("30m", later) == "30M:2026-09-09:10:30"
    assert episode_bucket("1H", later) == "1H:2026-09-09:1"  # 10:30-11:30 ET block
    # An unmapped timeframe keeps its own label so it cannot collide.
    assert episode_bucket("8H", at).startswith("8H:")


def _et(hour: int, minute: int) -> datetime:
    """2026-09-09 (EDT, UTC-4) wall-clock ET -> aware UTC datetime."""
    return datetime(2026, 9, 9, hour + 4, minute, 7, tzinfo=timezone.utc)


def test_1h_episode_is_anchored_to_the_rth_open_not_the_clock_hour():
    """_timeframe_series builds 1H evidence candles from session.open in
    one-hour spans (9:30-10:30, 10:30-11:30, ...).  The episode bucket must use
    the same anchor, or a setup at 10:00 is a second episode inside the 9:30
    candle and a genuinely new 10:30 setup is suppressed until 11:00."""
    first_candle = {episode_bucket("1H", _et(9, m)) for m in range(30, 60)}
    first_candle |= {episode_bucket("1H", _et(10, m)) for m in range(0, 30)}
    assert first_candle == {"1H:2026-09-09:0"}, "9:30-10:29 ET is ONE 1H episode"

    assert episode_bucket("1H", _et(10, 29)) == "1H:2026-09-09:0"
    assert episode_bucket("1H", _et(10, 30)) == "1H:2026-09-09:1"
    assert episode_bucket("1H", _et(10, 29)) != episode_bucket("1H", _et(10, 30)), (
        "10:29 and 10:30 ET belong to different 1H candles"
    )
    # 10:00 stays inside the 9:30 candle: the clock-hour boundary is NOT an edge.
    assert episode_bucket("1H", _et(9, 59)) == episode_bucket("1H", _et(10, 0))
    # Later blocks keep the 9:30 anchor.
    assert episode_bucket("1H", _et(11, 29)) == "1H:2026-09-09:1"
    assert episode_bucket("1H", _et(11, 30)) == "1H:2026-09-09:2"
    assert episode_bucket("1H", _et(15, 30)) == "1H:2026-09-09:6"  # final shortened 15:30-16:00 block
    # Pre-open ticks never share a bucket with the first RTH candle.
    assert episode_bucket("1H", _et(9, 29)) == "1H:2026-09-09:-1"


def test_1h_episode_key_dedupes_exactly_one_candle():
    inside = {_key(_et(10, m), timeframe="1H", setup_type="H1_222_CONTINUATION")
              for m in (0, 5, 10, 20, 25, 29)}
    assert len(inside) == 1
    assert _key(_et(10, 30), timeframe="1H", setup_type="H1_222_CONTINUATION") not in inside


def test_episode_duplicate_lookup_ignores_status(tmp_path):
    """The OPEN-only guard is why one setup became six rows: V1 episodes resolve
    on the very next tick, so the prior row is already closed."""
    storage = ScanStorage(tmp_path / "episode.sqlite")
    episode = _key(datetime(2026, 9, 9, 14, 20, tzinfo=timezone.utc))
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


# --- defect 1b: the option contract is NOT part of the episode identity ------

# The real 2026-09-09 rows.  Same Daily setup, same trigger, same trading date;
# the 0.40-delta pick drifted 305P -> 310P as AAPL rose 312.39 -> 316.64 and
# the contract-keyed identity counted the setup twice.
ROW_9161 = {"ticker": "AAPL", "lane": "COUNTERFACTUAL", "timeframe": "1D",
            "setup_type": "DAILY_222_CONTINUATION", "direction": "SHORT",
            "trigger": 314.9, "contract": "AAPL261120P00305000",
            "moment": datetime(2026, 9, 9, 18, 29, 36, 632173, tzinfo=timezone.utc)}
ROW_9162 = {**ROW_9161, "contract": "AAPL261120P00310000",
            "moment": datetime(2026, 9, 9, 18, 59, 36, 632407, tzinfo=timezone.utc)}


def _row_key(row):
    return setup_episode_key(
        ticker=row["ticker"], lane=row["lane"], timeframe=row["timeframe"],
        setup_type=row["setup_type"], direction=row["direction"],
        trigger=row["trigger"], moment=row["moment"],
    )


def test_same_daily_setup_with_drifted_strike_is_one_episode():
    k1, k2 = _row_key(ROW_9161), _row_key(ROW_9162)
    assert k1 == k2, "305P -> 310P on the same setup/trigger/date must be ONE episode"
    assert "P00305000" not in k1 and "P00310000" not in k2, "contract must not be in the identity"
    assert k1 == "AAPL|COUNTERFACTUAL|1D|DAILY_222_CONTINUATION|SHORT|314.9000@1D:2026-09-09"


def test_active_and_counterfactual_never_share_an_episode():
    active = _row_key({**ROW_9161, "lane": "ACTIVE"})
    assert active != _row_key(ROW_9161)
    # A missing lane defaults to ACTIVE, never to the observation lane.
    assert _row_key({**ROW_9161, "lane": None}) == active


def test_different_direction_or_trigger_is_a_different_episode():
    base = _row_key(ROW_9161)
    assert _row_key({**ROW_9161, "direction": "LONG"}) != base
    assert _row_key({**ROW_9161, "trigger": 315.15}) != base
    # Float noise in the same trigger does not split an episode.
    assert _row_key({**ROW_9161, "trigger": 314.90000001}) == base


def test_next_timeframe_bar_or_date_is_a_new_episode():
    assert _row_key({**ROW_9161, "moment": ROW_9161["moment"] + timedelta(days=1)}) != _row_key(ROW_9161)
    h4 = {**ROW_9161, "timeframe": "4H_RTH", "setup_type": "H4_222_CONTINUATION"}
    assert _row_key({**h4, "moment": _et(13, 29)}) != _row_key({**h4, "moment": _et(13, 30)})
    h1 = {**ROW_9161, "timeframe": "1H", "setup_type": "H1_222_CONTINUATION"}
    assert _row_key({**h1, "moment": _et(10, 29)}) != _row_key({**h1, "moment": _et(10, 30)})
    assert _row_key({**h1, "moment": _et(9, 59)}) == _row_key({**h1, "moment": _et(10, 0)})


def _insert_shadow(storage, row_id, ts, ticker, direction, status, selected):
    with storage._connect() as conn:
        conn.execute(
            """
            INSERT INTO options_shadow_journal (
                id, timestamp, scan_id, ticker, direction, score, pattern, status,
                setup_inputs_json, provider_snapshot_json, selected_contract_json,
                outcome_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (row_id, ts, row_id, ticker, direction, 8, "2-2-2", status, "{}", "{}",
             json.dumps(selected), "{}"),
        )


def test_replay_rows_9161_and_9162_collapse_to_one_episode(tmp_path):
    """Replay the live sequence: 9161 journalled at 18:29:36Z and already
    resolved WIN when 18:59:36Z evaluates the same setup on a drifted strike.
    Under setup identity the 18:59 candidate is a duplicate of 9161; the
    contract-specific candidate key still differs, so requotes stay exact."""
    storage = ScanStorage(tmp_path / "replay.sqlite")
    k9161 = _row_key(ROW_9161)
    contract_key_9161 = "AAPL261120P00305000|1D|DAILY_222_CONTINUATION|COUNTERFACTUAL"
    contract_key_9162 = "AAPL261120P00310000|1D|DAILY_222_CONTINUATION|COUNTERFACTUAL"
    _insert_shadow(storage, 9161, "2026-09-09T18:29:36.632173+00:00", "AAPL", "SHORT", "WIN",
                   {"contract_key": contract_key_9161, "candidate_key": contract_key_9161,
                    "contract": "AAPL261120P00305000", "episode_key": k9161,
                    "paper_evidence_lane": "COUNTERFACTUAL"})
    # The old OPEN-only guard sees nothing (row closed) and the contract keys differ...
    assert storage.find_open_duplicate("AAPL", contract_key_9162) is None
    assert contract_key_9161 != contract_key_9162
    # ...but the setup episode is the same row, so 9162 is never journalled.
    assert storage.find_episode_duplicate("AAPL", _row_key(ROW_9162)) == 9161
    # Lane isolation and the next trading date still open new episodes.
    assert storage.find_episode_duplicate("AAPL", _row_key({**ROW_9162, "lane": "ACTIVE"})) is None
    assert storage.find_episode_duplicate(
        "AAPL", _row_key({**ROW_9162, "moment": ROW_9162["moment"] + timedelta(days=1)})
    ) is None


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
