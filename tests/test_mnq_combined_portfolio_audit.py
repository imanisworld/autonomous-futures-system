from __future__ import annotations

import ast
from pathlib import Path

from research.mnq_combined_portfolio_audit import (
    PortfolioEvent,
    TIE_PRIORITY,
    deterministic_digest_payload,
    leave_one_out,
    replay_portfolio,
    summarize_replay,
)


def event(
    source_id: str,
    family: str,
    fill: str,
    exit_: str | None,
    *,
    day: str = "2026-01-05",
    pnl: float = 10.0,
    result: str | None = None,
) -> PortfolioEvent:
    return PortfolioEvent(
        family=family,
        source_id=source_id,
        signal_ts=fill,
        eligible_fill_ts=fill,
        exit_ts=exit_,
        observation_day=day,
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        result=result or ("WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"),
        net_pnl=pnl,
        session="new_york",
        source="unit",
    )


def test_frozen_same_timestamp_priority_is_deterministic():
    ts = "2026-01-05T15:00:00+00:00"
    end = "2026-01-05T15:30:00+00:00"
    rows = [
        event("st", "SUSTAINED_TREND_V1", ts, end),
        event("daily", "DAILY_22_COMPLETED_CLOSE", ts, end),
        event("4hr", "4HR_RETRIGGER", ts, end),
    ]
    replay = replay_portfolio(rows)
    assert [x.source_id for x in replay.fills] == ["4hr"]
    assert replay.exact_timestamp_collisions == 2
    skipped = {x.source_id: x.disposition for x in replay.decisions}
    assert skipped["daily"] == "SKIPPED_BUSY_PORTFOLIO"
    assert skipped["st"] == "SKIPPED_BUSY_PORTFOLIO"


def test_daily_multiday_position_blocks_other_families_across_days():
    rows = [
        event(
            "daily", "DAILY_22_COMPLETED_CLOSE",
            "2026-01-05T15:00:00+00:00",
            "2026-01-07T16:00:00+00:00",
            day="2026-01-05",
            pnl=100.0,
        ),
        event(
            "asia-next", "ASIA_D_EMA",
            "2026-01-06T02:00:00+00:00",
            "2026-01-06T03:00:00+00:00",
            day="2026-01-06",
        ),
        event(
            "322-next", "60M_322_FIRST_LIVE",
            "2026-01-07T15:00:00+00:00",
            "2026-01-07T15:30:00+00:00",
            day="2026-01-07",
        ),
        event(
            "after", "SUSTAINED_TREND_V1",
            "2026-01-07T16:01:00+00:00",
            "2026-01-07T16:20:00+00:00",
            day="2026-01-07",
        ),
    ]
    replay = replay_portfolio(rows)
    assert [x.source_id for x in replay.fills] == ["daily", "after"]
    dispositions = {x.source_id: x.disposition for x in replay.decisions}
    assert dispositions["asia-next"] == "SKIPPED_BUSY_PORTFOLIO"
    assert dispositions["322-next"] == "SKIPPED_BUSY_PORTFOLIO"


def test_global_three_fill_cap_is_shared_across_families():
    rows = [
        event("a", "4HR_RETRIGGER", "2026-01-05T14:00:00+00:00", "2026-01-05T14:10:00+00:00"),
        event("b", "60M_322_FIRST_LIVE", "2026-01-05T15:00:00+00:00", "2026-01-05T15:10:00+00:00"),
        event("c", "12HR_MIYAGI", "2026-01-05T16:00:00+00:00", "2026-01-05T16:10:00+00:00"),
        event("d", "SUSTAINED_TREND_V1", "2026-01-05T17:00:00+00:00", "2026-01-05T17:10:00+00:00"),
    ]
    replay = replay_portfolio(rows)
    assert [x.source_id for x in replay.fills] == ["a", "b", "c"]
    assert replay.decisions[-1].disposition == "SKIPPED_MAX_TRADES_PORTFOLIO"


def test_daily_capacity_resets_on_observation_day_not_strategy():
    rows = [
        event("a1", "4HR_RETRIGGER", "2026-01-05T14:00:00+00:00", "2026-01-05T14:01:00+00:00"),
        event("a2", "60M_322_FIRST_LIVE", "2026-01-05T15:00:00+00:00", "2026-01-05T15:01:00+00:00"),
        event("a3", "12HR_MIYAGI", "2026-01-05T16:00:00+00:00", "2026-01-05T16:01:00+00:00"),
        event("b1", "ASIA_D_EMA", "2026-01-06T00:30:00+00:00", "2026-01-06T00:40:00+00:00", day="2026-01-06"),
    ]
    replay = replay_portfolio(rows)
    assert [x.source_id for x in replay.fills] == ["a1", "a2", "a3", "b1"]


def test_equal_exit_and_next_fill_timestamp_stays_busy_pessimistically():
    rows = [
        event("first", "4HR_RETRIGGER", "2026-01-05T10:00:00+00:00", "2026-01-05T11:00:00+00:00"),
        event("equal", "60M_322_FIRST_LIVE", "2026-01-05T11:00:00+00:00", "2026-01-05T11:05:00+00:00"),
        event("later", "SUSTAINED_TREND_V1", "2026-01-05T11:01:00+00:00", "2026-01-05T11:10:00+00:00"),
    ]
    replay = replay_portfolio(rows)
    assert [x.source_id for x in replay.fills] == ["first", "later"]
    equal = next(x for x in replay.decisions if x.source_id == "equal")
    assert equal.disposition == "SKIPPED_BUSY_PORTFOLIO"
    assert equal.blocker_source_id == "first"


def test_leave_one_out_reports_freed_slot_consumer():
    rows = [
        event("4hr", "4HR_RETRIGGER", "2026-01-05T10:00:00+00:00", "2026-01-05T12:00:00+00:00", pnl=-50.0),
        event("st", "SUSTAINED_TREND_V1", "2026-01-05T11:00:00+00:00", "2026-01-05T11:30:00+00:00", pnl=100.0),
    ]
    result = leave_one_out(rows)
    four = result["4HR_RETRIGGER"]
    assert four["newly_available_fill_count_without_family"] == 1
    assert four["families_consuming_freed_slots"] == {"SUSTAINED_TREND_V1": 1}
    assert four["delta_full_minus_without"]["net"] == -150.0


def test_replay_is_order_independent_and_deterministic():
    rows = [
        event("a", "12HR_MIYAGI", "2026-01-05T12:00:00+00:00", "2026-01-05T12:10:00+00:00", pnl=20),
        event("b", "4HR_RETRIGGER", "2026-01-05T10:00:00+00:00", "2026-01-05T10:10:00+00:00", pnl=-10),
        event("c", "ASIA_D_EMA", "2026-01-05T11:00:00+00:00", "2026-01-05T11:10:00+00:00", pnl=5),
    ]
    a = replay_portfolio(rows)
    b = replay_portfolio(list(reversed(rows)))
    assert deterministic_digest_payload(a) == deterministic_digest_payload(b)
    assert summarize_replay(a) == summarize_replay(b)


def test_priority_order_is_exactly_preregistered():
    assert TIE_PRIORITY == (
        "4HR_RETRIGGER",
        "60M_322_FIRST_LIVE",
        "DAILY_22_COMPLETED_CLOSE",
        "12HR_MIYAGI",
        "ASIA_D_EMA",
        "SUSTAINED_TREND_V1",
    )


def test_portfolio_research_files_have_no_live_broker_or_webhook_imports():
    repo = Path(__file__).resolve().parents[1]
    paths = [
        repo / "research/mnq_combined_portfolio_audit.py",
        repo / "scripts/mnq_combined_portfolio_audit.py",
    ]
    forbidden = (
        "webhook",
        "execution.tradovate",
        "execution.webull",
        "broker_factory",
    )
    for path in paths:
        tree = ast.parse(path.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        assert not any(
            name.startswith(prefix)
            for name in imported
            for prefix in forbidden
        ), (path, imported)


def test_combined_runner_imports_without_side_effects():
    import importlib
    module = importlib.import_module("scripts.mnq_combined_portfolio_audit")
    assert module.START.isoformat() == "2025-07-24"
    assert module.END.isoformat() == "2026-06-26"


def test_unresolved_source_event_is_not_portfolio_fillable():
    # The canonical 4HR timing study treats EOD_BAR_MISSING as fail-closed /
    # excluded, not as an occupied portfolio fill.
    from scripts import mnq_combined_portfolio_audit as runner

    class Cand:
        direction = "LONG"
        stop = 90.0
        target = 120.0
        session = "new_york"

    class Bars:
        rows = [
            {"timestamp": "2026-01-05T15:00:00+00:00"},
            {"timestamp": "2026-01-05T15:05:00+00:00"},
        ]

    result = {
        "status": "UNRESOLVED",
        "fill_entry": 100.0,
        "exit_idx": 1,
        "net": 0.0,
    }
    assert runner._event_from_bracket(
        "4HR_RETRIGGER",
        "x",
        Cand(),
        Bars(),
        result,
        trigger_idx=0,
        source="unit",
    ) is None


def test_asia_standalone_control_does_not_inherit_portfolio_three_per_day_cap():
    from scripts import mnq_combined_portfolio_audit as runner

    rows = [
        event(
            f"asia-{i}",
            "ASIA_D_EMA",
            f"2026-01-05T1{i}:00:00+00:00",
            f"2026-01-05T1{i}:10:00+00:00",
            pnl=5.0,
        )
        for i in range(4)
    ]
    replay = replay_portfolio(
        rows,
        max_fills_per_day=runner.STANDALONE_DAILY_CAP["ASIA_D_EMA"],
    )
    assert len(replay.fills) == 4


def test_sustained_standalone_control_keeps_frozen_three_per_day_cap():
    from scripts import mnq_combined_portfolio_audit as runner

    rows = [
        event(
            f"st-{i}",
            "SUSTAINED_TREND_V1",
            f"2026-01-05T1{i}:00:00+00:00",
            f"2026-01-05T1{i}:10:00+00:00",
            pnl=5.0,
        )
        for i in range(4)
    ]
    replay = replay_portfolio(
        rows,
        max_fills_per_day=runner.STANDALONE_DAILY_CAP["SUSTAINED_TREND_V1"],
    )
    assert len(replay.fills) == 3
