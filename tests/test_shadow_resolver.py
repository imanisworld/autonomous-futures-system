"""Causal live shadow-candidate resolver (strategy/shadow_resolver.py).

The resolver closes the live evidence loop: candidates journaled on prior bars
get resolved against the bars ingested since, as SHADOW_OUTCOME rows. These
tests prove causality (no lookahead), idempotence, replay parity, journal
safety (additive rows never disturb daily state / bar claims), and the
evidence-readiness rollup.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone

from context.bar_history import BarHistory
from journal.journal_logger import JournalLogger
from ops.evidence_readiness import build_evidence_readiness
from strategy.shadow_resolver import resolve_pending_shadow_outcomes
from strategy.shadow_setups import ShadowSetupCandidate, resolve_shadow_candidate

DAY = date(2026, 7, 1)
NEXT_DAY = DAY + timedelta(days=1)


def _ts(day: date, hour: int, minute: int = 0) -> str:
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc
    ).isoformat()


def _journal_candidate_row(
    ts: str,
    *,
    instrument: str = "MNQ",
    strategy: str = "orb_false_break_fade",
    direction: str = "LONG",
    entry: float = 100.0,
    stop: float = 96.0,
    target: float = 108.0,
) -> dict:
    return {
        "ts": ts,
        "instrument": instrument,
        "decision": "NO_TRADE",
        "shadow_candidates": [
            {
                "strategy": strategy,
                "direction": direction,
                "entry": entry,
                "stop": stop,
                "target": target,
                "rr_ratio": 2.0,
                "risk_tier": "B",
                "size_multiplier": 0.5,
                "notes": "test",
            }
        ],
    }


def _write_journal(log_dir, day: date, rows: list[dict]) -> None:
    path = log_dir / f"journal_{day.isoformat()}.jsonl"
    with path.open("a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _record_bar(log_dir, ts: str, high: float, low: float, *, instrument="MNQ"):
    BarHistory(log_dir=str(log_dir)).record(
        instrument,
        ts=ts,
        open=(high + low) / 2,
        high=high,
        low=low,
        close=(high + low) / 2,
    )


def _resolve(log_dir, bar_ts: str, *, instrument="MNQ", for_date=None):
    return resolve_pending_shadow_outcomes(
        log_dir=str(log_dir),
        instrument=instrument,
        current_bar_ts=bar_ts,
        for_date=for_date,
    )


def _outcome_rows(log_dir, day: date) -> list[dict]:
    path = log_dir / f"journal_{day.isoformat()}.jsonl"
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [row for row in rows if row.get("type") == "SHADOW_OUTCOME"]


def test_win_resolves_only_after_fill_then_target(tmp_path):
    # Candidate at 09:00; entry 100 / stop 96 / target 108.
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])

    # 09:15 bar doesn't touch entry — nothing resolves.
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=104.0, low=101.0)
    assert _resolve(tmp_path, _ts(DAY, 9, 15), for_date=DAY) == []

    # 09:30 bar fills the entry — filled, but no exit yet: still pending.
    _record_bar(tmp_path, _ts(DAY, 9, 30), high=101.0, low=99.0)
    assert _resolve(tmp_path, _ts(DAY, 9, 30), for_date=DAY) == []

    # 09:45 bar tags the target — terminal WIN on this bar.
    _record_bar(tmp_path, _ts(DAY, 9, 45), high=109.0, low=101.0)
    appended = _resolve(tmp_path, _ts(DAY, 9, 45), for_date=DAY)
    assert len(appended) == 1
    record = appended[0]
    assert record["type"] == "SHADOW_OUTCOME"
    assert record["lane"] == "shadow_setups"
    assert record["shadow_outcome"]["result"] == "WIN"
    assert record["shadow_outcome"]["entry_filled"] is True
    assert record["resolved_at_bar_ts"] == _ts(DAY, 9, 45)
    # Additive-only contract: never carries the executable-trade keys.
    assert "outcome" not in record
    assert "decision" not in record
    assert _outcome_rows(tmp_path, DAY) == [record]


def test_idempotent_no_duplicate_outcomes(tmp_path):
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=101.0, low=99.0)  # fill
    _record_bar(tmp_path, _ts(DAY, 9, 30), high=109.0, low=101.0)  # target
    assert len(_resolve(tmp_path, _ts(DAY, 9, 30), for_date=DAY)) == 1
    # Re-running on the same and later bars appends nothing new.
    assert _resolve(tmp_path, _ts(DAY, 9, 30), for_date=DAY) == []
    _record_bar(tmp_path, _ts(DAY, 9, 45), high=120.0, low=90.0)
    assert _resolve(tmp_path, _ts(DAY, 9, 45), for_date=DAY) == []
    assert len(_outcome_rows(tmp_path, DAY)) == 1


def test_pessimistic_both_hit_is_loss(tmp_path):
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=101.0, low=99.0)  # fill
    # One bar straddles both stop and target → pessimistic LOSS.
    _record_bar(tmp_path, _ts(DAY, 9, 30), high=109.0, low=95.0)
    appended = _resolve(tmp_path, _ts(DAY, 9, 30), for_date=DAY)
    assert len(appended) == 1
    assert appended[0]["shadow_outcome"]["result"] == "LOSS"
    assert appended[0]["shadow_outcome"]["exit_reason"] == "STOP_HIT"


def test_no_lookahead_incremental_matches_replay_and_never_fires_early(tmp_path):
    """The core causality proof.

    Feed a full day's bars one at a time, calling the resolver after each bar
    exactly as the live runner does. The outcome must appear on precisely the
    bar replay attributes the exit to (bars_to_exit) — never earlier — and the
    terminal result must equal the replay-style full-window resolution.
    """
    cand_ts = _ts(DAY, 9)
    _write_journal(tmp_path, DAY, [_journal_candidate_row(cand_ts)])

    day_bars = [
        (_ts(DAY, 9, 15), 104.0, 101.0),  # no fill
        (_ts(DAY, 9, 30), 102.0, 99.5),   # fill
        (_ts(DAY, 9, 45), 105.0, 100.0),  # neither exit
        (_ts(DAY, 10, 0), 107.9, 101.0),  # target NOT reached (108)
        (_ts(DAY, 10, 15), 108.5, 103.0), # target hit → WIN here
        (_ts(DAY, 10, 30), 90.0, 80.0),   # later crash must NOT matter
    ]

    replay_outcome = resolve_shadow_candidate(
        ShadowSetupCandidate(
            strategy="orb_false_break_fade",
            direction="LONG",
            entry=100.0,
            stop=96.0,
            target=108.0,
            rr_ratio=2.0,
            risk_tier="B",
            size_multiplier=0.5,
            notes="",
        ),
        [(high, low) for _, high, low in day_bars],
        instrument="MNQ",
    )
    assert replay_outcome.result == "WIN"

    fired_at: list[int] = []
    for i, (ts, high, low) in enumerate(day_bars):
        _record_bar(tmp_path, ts, high=high, low=low)
        appended = _resolve(tmp_path, ts, for_date=DAY)
        if appended:
            fired_at.append(i)
            live_outcome = appended[0]["shadow_outcome"]

    # Fires exactly once, exactly on the replay exit bar (bars_to_exit is
    # 1-indexed into the forward window, which starts at day_bars[0]).
    assert fired_at == [replay_outcome.bars_to_exit - 1]
    assert live_outcome == replay_outcome.to_dict()


def test_no_lookahead_prewritten_future_bars_are_ignored(tmp_path):
    """Even if future bars exist on disk (backfill), resolution at bar N only
    uses bars <= N."""
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=101.0, low=99.0)   # fill
    _record_bar(tmp_path, _ts(DAY, 9, 30), high=105.0, low=101.0)  # no exit
    _record_bar(tmp_path, _ts(DAY, 9, 45), high=109.0, low=101.0)  # future WIN bar
    # Resolving AT the 09:30 bar must not see the 09:45 bar.
    assert _resolve(tmp_path, _ts(DAY, 9, 30), for_date=DAY) == []
    appended = _resolve(tmp_path, _ts(DAY, 9, 45), for_date=DAY)
    assert len(appended) == 1
    assert appended[0]["shadow_outcome"]["result"] == "WIN"


def test_no_fill_finalized_by_next_day_bar(tmp_path):
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=104.0, low=101.0)  # never touches 100
    assert _resolve(tmp_path, _ts(DAY, 9, 15), for_date=DAY) == []

    # First bar of the NEXT day closes the candidate's forward window.
    _record_bar(tmp_path, _ts(NEXT_DAY, 9), high=104.0, low=101.0)
    appended = _resolve(tmp_path, _ts(NEXT_DAY, 9), for_date=NEXT_DAY)
    assert len(appended) == 1
    assert appended[0]["shadow_outcome"]["result"] == "NO_FILL"
    assert appended[0]["candidate_day"] == DAY.isoformat()
    # The outcome row is appended to the RESOLUTION day's journal.
    assert len(_outcome_rows(tmp_path, NEXT_DAY)) == 1


def test_open_at_day_end_finalized_as_open(tmp_path):
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=101.0, low=99.0)  # fill, no exit
    assert _resolve(tmp_path, _ts(DAY, 9, 15), for_date=DAY) == []
    _record_bar(tmp_path, _ts(NEXT_DAY, 9), high=104.0, low=101.0)
    appended = _resolve(tmp_path, _ts(NEXT_DAY, 9), for_date=NEXT_DAY)
    assert len(appended) == 1
    assert appended[0]["shadow_outcome"]["result"] == "OPEN"
    assert appended[0]["shadow_outcome"]["entry_filled"] is True


def test_range_signal_lane_resolves_both_journal_keys(tmp_path):
    rows = [
        {
            "ts": _ts(DAY, 9),
            "instrument": "MNQ",
            "decision": "NO_TRADE",
            "range_signal": {
                "signal_type": "RANGE_REJECT",
                "direction": "SHORT",
                "entry_candidate": 100.0,
                "stop_candidate": 103.0,
                "target_candidate": 94.0,
            },
        },
        {
            "ts": _ts(DAY, 9, 15),
            "instrument": "MNQ",
            "decision": "TRADE",
            "shadow_range_signal": {
                "signal_type": "RANGE_BOUNCE",
                "direction": "LONG",
                "entry_candidate": 95.0,
                "stop_candidate": 92.0,
                "target_candidate": 101.0,
            },
        },
        {
            # Bracketless signal — observed but never resolvable.
            "ts": _ts(DAY, 9, 30),
            "instrument": "MNQ",
            "decision": "NO_TRADE",
            "range_signal": {
                "signal_type": "RANGE_MIDDLE_NO_TRADE",
                "direction": "NONE",
                "entry_candidate": None,
                "stop_candidate": None,
                "target_candidate": None,
            },
        },
    ]
    _write_journal(tmp_path, DAY, rows)
    _record_bar(tmp_path, _ts(DAY, 9, 45), high=100.5, low=94.5)  # fills both
    _record_bar(tmp_path, _ts(DAY, 10, 0), high=101.5, low=93.5)  # both exits hit
    appended = _resolve(tmp_path, _ts(DAY, 10, 0), for_date=DAY)
    assert {row["lane"] for row in appended} == {"range_signal"}
    assert len(appended) == 2
    by_strategy = {row["strategy"]: row for row in appended}
    # SHORT: exit bar reaches target 94 (low 93.5) but not stop 103 → WIN.
    assert by_strategy["range_reject"]["shadow_outcome"]["result"] == "WIN"
    # LONG: exit bar reaches target 101 (high 101.5) but not stop 92 → WIN.
    assert by_strategy["range_bounce"]["shadow_outcome"]["result"] == "WIN"


def test_other_instrument_bars_do_not_resolve(tmp_path):
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=200.0, low=50.0, instrument="MES")
    assert _resolve(tmp_path, _ts(DAY, 9, 15), instrument="MES", for_date=DAY) == []
    assert _outcome_rows(tmp_path, DAY) == []


def test_fail_soft_on_malformed_rows(tmp_path):
    path = tmp_path / f"journal_{DAY.isoformat()}.jsonl"
    path.write_text(
        "not json at all\n"
        + json.dumps({"ts": _ts(DAY, 9), "instrument": "MNQ",
                      "shadow_candidates": [{"direction": "LONG"}]}) + "\n"
        + json.dumps({"ts": "garbage-ts", "instrument": "MNQ",
                      "shadow_candidates": [{"direction": "LONG", "entry": 1,
                                             "stop": 0.5, "target": 2,
                                             "strategy": "x"}]}) + "\n"
    )
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=101.0, low=99.0)
    assert _resolve(tmp_path, _ts(DAY, 9, 15), for_date=DAY) == []


def test_outcome_rows_never_disturb_daily_state_or_bar_claims(tmp_path):
    """The additive-only safety contract for every existing journal reader."""
    journal = JournalLogger(log_dir=str(tmp_path))
    _write_journal(tmp_path, DAY, [_journal_candidate_row(_ts(DAY, 9))])
    _record_bar(tmp_path, _ts(DAY, 9, 15), high=101.0, low=99.0)
    _record_bar(tmp_path, _ts(DAY, 9, 30), high=109.0, low=101.0)
    before = journal.get_daily_state(DAY)
    appended = _resolve(tmp_path, _ts(DAY, 9, 30), for_date=DAY)
    assert len(appended) == 1
    after = journal.get_daily_state(DAY)
    assert after.trade_count == before.trade_count == 0
    assert after.has_open_position is before.has_open_position is False
    assert after.realized_pnl_dollars == before.realized_pnl_dollars == 0.0
    # A fresh bar claim at a later timestamp still succeeds.
    assert journal.claim_bar(
        instrument="MNQ", bar_ts=_ts(DAY, 9, 45), for_date=DAY
    ) is True


def test_evidence_readiness_counts_resolved_examples(tmp_path, config):
    _write_journal(
        tmp_path,
        DAY,
        [
            _journal_candidate_row(_ts(DAY, 9)),
            {
                "ts": _ts(DAY, 9, 15),
                "instrument": "MNQ",
                "decision": "NO_TRADE",
                "range_signal": {
                    "signal_type": "RANGE_REJECT",
                    "direction": "SHORT",
                    "entry_candidate": 100.0,
                    "stop_candidate": 103.0,
                    "target_candidate": 94.0,
                },
            },
        ],
    )
    _record_bar(tmp_path, _ts(DAY, 9, 30), high=101.0, low=99.0)
    _record_bar(tmp_path, _ts(DAY, 9, 45), high=109.0, low=101.0)
    resolved = _resolve(tmp_path, _ts(DAY, 9, 45), for_date=DAY)
    assert len(resolved) == 2  # shadow WIN + range short stopped/target

    report = build_evidence_readiness(tmp_path, days=5, through_date=DAY, config=config)
    shadow = next(t for t in report["tracks"] if t["key"] == "shadow_setups")
    assert shadow["observations"] == 1
    assert shadow["resolved_examples"] == 1
    assert shadow["outcome_resolution_available"] is True
    assert shadow["status"] == "INSUFFICIENT SAMPLE"  # resolved, below thresholds
    assert shadow["resolved_breakdown"] == {"WIN": 1}

    range_track = next(t for t in report["tracks"] if t["key"] == "range_signal")
    assert range_track["resolved_examples"] == 1
    assert range_track["status"] == "INSUFFICIENT SAMPLE"


def test_evidence_readiness_ready_for_review_at_thresholds(tmp_path, config):
    """30+ terminal outcomes across 10+ distinct days flips to READY FOR REVIEW."""
    journal = JournalLogger(log_dir=str(tmp_path))
    end = DAY
    for i in range(10):
        d = end - timedelta(days=9 - i)
        _write_journal(tmp_path, d, [_journal_candidate_row(_ts(d, 9))])
        for j in range(3):
            journal.log_shadow_outcome(
                {
                    "lane": "shadow_setups",
                    "instrument": "MNQ",
                    "strategy": "orb_false_break_fade",
                    "candidate_key": f"k-{d}-{j}",
                    "candidate_day": d.isoformat(),
                    "shadow_outcome": {"result": "WIN" if j else "LOSS",
                                       "pnl_ticks": 10.0 if j else -5.0},
                },
                for_date=d,
            )
    report = build_evidence_readiness(tmp_path, days=15, through_date=end, config=config)
    shadow = next(t for t in report["tracks"] if t["key"] == "shadow_setups")
    assert shadow["resolved_examples"] == 30
    assert shadow["resolved_terminal_examples"] == 30
    assert shadow["resolved_distinct_days"] == 10
    assert shadow["status"] == "READY FOR REVIEW"
    assert shadow["resolved_pnl_ticks_net"] == 150.0  # 20 wins*10 − 10 losses*5


def test_process_alert_wiring_resolves_prior_candidates_end_to_end(tmp_path):
    """Live-path integration: the runner itself resolves a prior bar's
    journaled candidate as later bars arrive, and stays fail-soft."""
    from dataclasses import replace as _replace

    from config.settings import load_config
    from tests.test_webhook import _base_payload
    from webhook.runner import process_alert

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    cfg = _replace(load_config(), max_staleness_seconds=10_000_000)
    day = date(2026, 5, 23)

    # Candidate journaled on an earlier bar: LONG 19500 / stop 19480 / target 19540.
    _write_journal(
        log_dir,
        day,
        [
            _journal_candidate_row(
                _ts(day, 14, 15),
                entry=19500.0,
                stop=19480.0,
                target=19540.0,
            )
        ],
    )

    # Bar 1 (14:30): trades through the entry but neither exit → still pending.
    payload = _base_payload(
        timestamp=_ts(day, 14, 30), high=19510.0, low=19495.0, close=19505.25
    )
    result = process_alert(payload, config=cfg, log_dir=str(log_dir), for_date=day)
    assert "shadow_outcomes_resolved" not in result

    # Bar 2 (14:45): tags the target → the runner appends the WIN outcome.
    payload = _base_payload(
        timestamp=_ts(day, 14, 45),
        open=19510.0, high=19545.0, low=19505.0, close=19540.0,
    )
    result = process_alert(payload, config=cfg, log_dir=str(log_dir), for_date=day)
    assert result["shadow_outcomes_resolved"] == 1
    rows = _outcome_rows(log_dir, day)
    assert len(rows) == 1
    assert rows[0]["shadow_outcome"]["result"] == "WIN"
    assert rows[0]["candidate_bar_ts"] == _ts(day, 14, 15)

    # Kill switch honored: with the flag off, nothing resolves.
    _write_journal(
        log_dir,
        day,
        [
            _journal_candidate_row(
                _ts(day, 14, 20),
                strategy="gap_fill",
                entry=19500.0,
                stop=19480.0,
                target=19541.0,
            )
        ],
    )
    cfg_off = _replace(cfg, shadow_resolver_enabled=False)
    payload = _base_payload(
        timestamp=_ts(day, 15, 0),
        open=19540.0, high=19560.0, low=19538.0, close=19555.0,
    )
    result = process_alert(payload, config=cfg_off, log_dir=str(log_dir), for_date=day)
    assert "shadow_outcomes_resolved" not in result
    assert len(_outcome_rows(log_dir, day)) == 1
