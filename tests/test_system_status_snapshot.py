"""Tests for ops/system_status_snapshot.py.

Covers: deterministic schema generation, UNKNOWN when source data is missing,
source-of-truth conflict detection, atomic write behavior (including
preserving the last-known-good snapshot on a generation/validation failure),
no mutation of trading configuration, trade-chain accounting mismatch -> FAIL,
missing OUTCOME -> anomaly, stale feed -> anomaly, per-lane entry
model/tolerance preserved, and full-snapshot determinism.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ops.system_status_snapshot import (
    ALLOWED_CLASSIFICATIONS,
    UNKNOWN,
    active_env_gated_flags,
    build_env_gated_lanes,
    build_feed_liveness,
    build_runtime_lanes,
    build_strategy_evidence,
    build_system_status_snapshot,
    build_trade_chain_health,
    classify_no_trade_liveness,
    parse_strategy_inventory,
    validate_snapshot_schema,
    write_snapshot_atomic,
)

_INVENTORY_MD = """\
# STRATEGY INVENTORY
*Last updated: 2026-07-23*

## Master Table

| Strategy | Rules | Detector | Replay parity | Honest fills | Walk-forward | Slippage | Sample | Verdict |
|---|---|---|---|---|---|---|---|---|
| ORB Reclaim (MES) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ n=305 | **PAPER PROOF** |
| ORB Reclaim (MNQ) | ✅ | ✅ | Partial | ✅ | ❌ insufficient | ✅ | ⚠️ n=253 thin | **PROMISING BUT UNPROVEN** |
| PDH Reclaim | ✅ | ✅ | ✅ | ✅ | ❌ both halves neg | ❌ | ✅ n=67 | **RETIRE** |
| 4HR Re-Trigger (MES) | ✅ | ✅ | ✅ | ✅ | ❌ H2 erases H1 | ❌ | ⚠️ n=75 | **OVERFIT — excluded from runtime** |

## Detailed Strategy Profiles
### ORB Reclaim — MES
prose that must not be parsed as a table row -- heading intentionally does NOT
match its table row name "ORB Reclaim (MES)" (mirrors the real doc's older
em-dash profile-heading convention vs the table's parenthetical convention)

### ORB Reclaim (MNQ)
Forward evidence epoch: 2026-07-27T04:19:13Z
"""


# ── parse_strategy_inventory / classification mapping ───────────────────────

def test_parse_strategy_inventory_extracts_rows_and_last_updated():
    parsed = parse_strategy_inventory(_INVENTORY_MD)
    assert parsed["last_updated"] == "2026-07-23"
    assert parsed["rows"]["ORB Reclaim (MES)"] == "PAPER PROOF"
    assert parsed["rows"]["ORB Reclaim (MNQ)"] == "PROMISING BUT UNPROVEN"
    assert parsed["rows"]["PDH Reclaim"] == "RETIRE"
    assert "prose that must not be parsed as a table row" not in parsed["rows"]


def test_build_strategy_evidence_maps_verdict_and_preserves_raw():
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["orb_reclaim"],
        instruments=["MES", "MNQ"],
        disabled_concepts_per_instrument={},
    )
    by_instrument = {row["instrument"]: row for row in evidence}
    assert by_instrument["MES"]["classification"] == "PROMISING BUT UNPROVEN"
    assert by_instrument["MES"]["classification_raw"] == "PAPER PROOF"
    assert by_instrument["MES"]["classification"] in ALLOWED_CLASSIFICATIONS


def test_build_strategy_evidence_unknown_when_no_inventory_row_mapped():
    """orb_rejection has no Strategy_Inventory.md row -- must report UNKNOWN,
    never silently borrow a neighboring row's verdict."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["orb_rejection"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
    )
    assert evidence[0]["classification"] == UNKNOWN
    assert evidence[0]["classification_raw"] == UNKNOWN
    assert evidence[0]["current_blocker"]


def test_build_strategy_evidence_unknown_when_inventory_missing_entirely():
    evidence = build_strategy_evidence(
        None,
        enabled_concepts=["orb_reclaim"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
    )
    assert evidence[0]["classification"] == UNKNOWN


def test_build_strategy_evidence_disabled_per_instrument_stays_visible_but_ineligible():
    """A per-instrument exclusion (e.g. MES 4HR retrigger, OVERFIT) must remain
    a VISIBLE row with eligible=False, not disappear from the registry --
    disappearing is exactly the failure mode the operator flagged."""
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["orb_reclaim"],
        instruments=["MES", "MNQ"],
        disabled_concepts_per_instrument={"MNQ": ["orb_reclaim"]},
    )
    by_instrument = {row["instrument"]: row for row in evidence}
    assert {row["instrument"] for row in evidence} == {"MES", "MNQ"}
    assert by_instrument["MES"]["eligible"] is True
    assert by_instrument["MES"]["exclusion_reason"] is None
    assert by_instrument["MNQ"]["eligible"] is False
    assert by_instrument["MNQ"]["exclusion_reason"] == "disabled_concepts_per_instrument"
    # still classified even though excluded -- exclusion must not blank the evidence
    assert by_instrument["MNQ"]["classification"] == "PROMISING BUT UNPROVEN"


def test_build_strategy_evidence_excluded_overfit_strategy_stays_visible():
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=["strat_4hr_retrigger"],
        instruments=["MES"],
        disabled_concepts_per_instrument={"MES": ["strat_4hr_retrigger"]},
    )
    row = evidence[0]
    assert row["eligible"] is False
    assert row["classification"] == "OVERFIT"


def test_build_strategy_evidence_includes_active_env_gated_lane():
    evidence = build_strategy_evidence(
        _INVENTORY_MD,
        enabled_concepts=[],
        instruments=[],
        disabled_concepts_per_instrument={},
        env_gated_active=[("MNQ_ORB_BREAKOUT_INVERSE_MODE", "MNQ", "paper_sim")],
    )
    assert len(evidence) == 1
    assert evidence[0]["strategy"] == "MNQ_ORB_BREAKOUT_INVERSE_MODE"
    assert evidence[0]["eligible"] is True


# ── evidence-epoch extraction (must be the lane's own epoch, never the doc date) ─

def test_evidence_epoch_extracted_from_matching_prose_section():
    from ops.system_status_snapshot import _extract_evidence_epoch

    parsed = parse_strategy_inventory(_INVENTORY_MD)
    epoch = _extract_evidence_epoch(parsed["sections"], "ORB Reclaim (MNQ)")
    assert epoch == "2026-07-27T04:19:13Z"


def test_evidence_epoch_unknown_when_heading_does_not_match_table_row_name():
    """The doc's profile heading for this row uses a different naming
    convention than its table row -- must not fuzzy-match; must not fall back
    to the document's top-level 'Last updated' date either."""
    from ops.system_status_snapshot import _extract_evidence_epoch

    parsed = parse_strategy_inventory(_INVENTORY_MD)
    epoch = _extract_evidence_epoch(parsed["sections"], "ORB Reclaim (MES)")
    assert epoch is None


def test_runtime_lane_evidence_epoch_is_unknown_not_the_document_last_updated_date():
    """Regression guard for the exact bug: evidence_epoch must never silently
    become Strategy_Inventory.md's document-level 'Last updated' date."""
    lanes = build_runtime_lanes(
        enabled_concepts=["orb_reclaim"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
        entry_fill_model="legacy",
        entry_tolerance_ticks_by_root={"MES": 16.0},
        schedule_mode="current",
        contracts_by_instrument=None,
        repo_commit="deadbeef",
        evidence_epoch_lookup=lambda concept, instrument: UNKNOWN,
    )
    assert lanes[0]["evidence_epoch"] == UNKNOWN
    assert lanes[0]["evidence_epoch"] != "2026-07-23"  # the doc's Last-updated date


def test_runtime_lane_evidence_epoch_uses_provided_lookup():
    lanes = build_runtime_lanes(
        enabled_concepts=["orb_reclaim"],
        instruments=["MES"],
        disabled_concepts_per_instrument={},
        entry_fill_model="legacy",
        entry_tolerance_ticks_by_root={"MES": 16.0},
        schedule_mode="current",
        contracts_by_instrument=None,
        repo_commit="deadbeef",
        evidence_epoch_lookup=lambda concept, instrument: "2026-07-27T04:19:13Z",
    )
    assert lanes[0]["evidence_epoch"] == "2026-07-27T04:19:13Z"


# ── per-lane execution context (not a global instrument default) ────────────

def test_runtime_lane_reports_shared_tradovate_broker_context():
    lanes = build_runtime_lanes(
        enabled_concepts=["orb_breakout"],
        instruments=["MNQ"],
        disabled_concepts_per_instrument={},
        entry_fill_model="legacy",
        entry_tolerance_ticks_by_root={"MNQ": 32.0},
        schedule_mode="current",
        contracts_by_instrument=None,
        repo_commit="deadbeef",
        evidence_epoch_lookup=lambda c, i: UNKNOWN,
    )
    assert lanes[0]["broker"] == "tradovate"
    assert lanes[0]["entry_model"] == "legacy"
    assert lanes[0]["entry_tolerance_ticks"] == 32.0


def test_active_env_gated_flags_excludes_observe_only():
    """observe_only is the proof-mode contract's own audit-only value -- it
    must never be reported as an active trading lane."""
    flags = active_env_gated_flags({"MNQ_ORB_BREAKOUT_INVERSE_MODE": "observe_only"})
    assert flags == []


def test_active_env_gated_flags_includes_paper_sim():
    flags = active_env_gated_flags({"MNQ_ORB_BREAKOUT_INVERSE_MODE": "paper_sim"})
    assert flags == [("MNQ_ORB_BREAKOUT_INVERSE_MODE", "MNQ", "paper_sim")]


def test_env_gated_lane_resolves_verified_execution_context_distinct_from_tradovate_path():
    """The exact scenario the operator described: the frozen MNQ inverse ORB
    lane must report ITS OWN PaperBroker/8-tick/1-contract context, not the
    ordinary Tradovate legacy/32-tick lane's values."""
    lanes = build_env_gated_lanes({"MNQ_ORB_BREAKOUT_INVERSE_MODE": "paper_sim"})
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["broker"] == "PaperBroker"
    assert lane["entry_model"] == "marketable_ioc"
    assert lane["entry_tolerance_ticks"] == 8.0
    assert lane["contracts"] == 1


def test_env_gated_lane_without_verified_source_stays_unknown_not_borrowed():
    """A proof-mode lane this module has NOT verified against its own source
    must report UNKNOWN, never silently inherit the global Tradovate config."""
    lanes = build_env_gated_lanes({"MNQ_ORB_RECLAIM_PROOF_MODE": "paper_sim"})
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["entry_model"] == UNKNOWN
    assert lane["entry_tolerance_ticks"] == UNKNOWN
    assert lane["contracts"] == UNKNOWN


# ── trade chain accounting ───────────────────────────────────────────────────

def _approved_trade(instrument: str, ts: str) -> dict:
    return {"ts": ts, "decision": "TRADE", "instrument": instrument, "risk_check": {"result": "APPROVED"}, "outcome": None}


def _outcome(instrument: str, ts: str, result: str, *, no_fill_reason: str | None = None) -> dict:
    return {"ts": ts, "type": "OUTCOME", "instrument": instrument, "outcome": {"result": result, "no_fill_reason": no_fill_reason}}


def test_trade_chain_accounting_win_reconciles_pass():
    entries = [
        _approved_trade("MES", "2026-07-27T14:00:00+00:00"),
        _outcome("MES", "2026-07-27T14:05:00+00:00", "WIN"),
    ]
    result = build_trade_chain_health(entries, instruments=["MES"], broker_open_positions={"MES": False})
    counts = result["counts"]
    assert counts["attempts"] == 1
    assert counts["fills"] == 1
    assert counts["resolved"] == 1
    assert result["accounting"]["attempts_equation_holds"] is True
    assert result["accounting"]["fills_equation_holds"] is True


def test_trade_chain_known_no_fill_and_broker_reject_bucketed_separately():
    entries = [
        _approved_trade("MES", "2026-07-27T14:00:00+00:00"),
        _outcome("MES", "2026-07-27T14:01:00+00:00", "CANCELLED", no_fill_reason="NO_FILL_PRICE_MOVED_AWAY"),
        _approved_trade("MNQ", "2026-07-27T14:10:00+00:00"),
        _outcome("MNQ", "2026-07-27T14:11:00+00:00", "CANCELLED", no_fill_reason="NO_FILL_BROKER_REJECTED"),
    ]
    result = build_trade_chain_health(entries, instruments=["MES", "MNQ"], broker_open_positions={"MES": False, "MNQ": False})
    counts = result["counts"]
    assert counts["known_no_fills"] == 1
    assert counts["rejects"] == 1
    assert counts["attempts"] == 2
    assert result["accounting"]["attempts_equation_holds"] is True


def test_trade_chain_orphan_open_position_fails_broker_journal_parity():
    """Journal shows an open position the broker denies holding -> FAIL, not PASS."""
    entries = [_approved_trade("MES", "2026-07-27T14:00:00+00:00")]
    result = build_trade_chain_health(entries, instruments=["MES"], broker_open_positions={"MES": False})
    assert result["counts"]["orphan_count"] == 1
    assert result["broker_journal_parity"] == "FAIL"
    assert result["overall_state"] == "FAIL"
    assert "orphan" in (result["last_anomaly_summary"] or "")


def test_trade_chain_missing_broker_read_is_missing_outcome_not_orphan():
    """No broker read available (None) must not be misclassified as an orphan."""
    entries = [_approved_trade("MES", "2026-07-27T14:00:00+00:00")]
    result = build_trade_chain_health(entries, instruments=["MES"], broker_open_positions={})
    assert result["counts"]["orphan_count"] == 0
    assert result["counts"]["missing_outcome_count"] == 1


def test_trade_chain_duplicate_order_id_flagged():
    entries = [
        {"ts": "t1", "type": "ORDER_IDS", "instrument": "MES", "order_ids": {"stop": "abc123"}},
        {"ts": "t2", "type": "ORDER_IDS", "instrument": "MES", "order_ids": {"stop": "abc123"}},
    ]
    result = build_trade_chain_health(entries, instruments=["MES"])
    assert result["counts"]["duplicate_order_identity_count"] == 1
    assert result["overall_state"] == "FAIL"


def test_trade_chain_zero_activity_alone_does_not_imply_pass():
    """The exact failure mode flagged by the operator: zero attempts/fills/orphans
    must NOT read as a healthy PASS when liveness shows the system never ran."""
    stale_feed = {"5m": {"last_bar_ts": None, "stale": True}, "15m": {"last_bar_ts": None, "stale": True}}
    result = build_trade_chain_health(
        [], instruments=["MES"], feed_liveness_by_instrument={"MES": stale_feed},
    )
    assert result["counts"]["attempts"] == 0
    assert result["liveness"]["MES"]["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["overall_state"] != "PASS"


# ── no-trade liveness classification ─────────────────────────────────────────

_HEALTHY_FEED = {"5m": {"last_bar_ts": "t", "stale": False}, "15m": {"last_bar_ts": "t", "stale": False}}


def test_no_trade_healthy_when_legitimate_reason_and_feed_current():
    entries = [{"instrument": "MNQ", "decision": "NO_TRADE", "reason": "no valid setup"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "NO_TRADE_HEALTHY"
    assert result["reason_legitimate"] is True


def test_no_trade_system_failure_when_reason_is_a_system_fault():
    entries = [{"instrument": "MNQ", "decision": "NO_TRADE", "reason": "detector_exception: KeyError"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["reason_legitimate"] is False


def test_no_trade_system_failure_when_feed_stale_even_with_legitimate_reason():
    stale_feed = {"5m": {"last_bar_ts": None, "stale": True}}
    entries = [{"instrument": "MNQ", "decision": "NO_TRADE", "reason": "no valid setup"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=stale_feed)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"


def test_no_trade_system_failure_when_strategy_never_evaluated():
    result = classify_no_trade_liveness(instrument="MNQ", entries=[], feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE"
    assert result["strategy_evaluated"] is False


def test_traded_diagnosis_when_a_trade_decision_exists():
    entries = [{"instrument": "MNQ", "decision": "TRADE", "reason": "confluence A"}]
    result = classify_no_trade_liveness(instrument="MNQ", entries=entries, feed_liveness=_HEALTHY_FEED)
    assert result["diagnosis"] == "TRADED"


class _FakeBarHistory:
    """Mirrors context.bar_history.BarHistory's real read interface -- a flat,
    mixed-timeframe bar list per instrument/day, exactly like the real
    per-instrument-per-day JSONL file. Callers must filter by each bar's own
    `timeframe` field; nothing here pre-sorts by timeframe for them."""

    def __init__(self, bars: list[dict]):
        self._bars = bars

    def recent(self, instrument, n, for_date=None, lookback_days=3):
        return self._bars[-n:]


def test_feed_liveness_stale_when_bar_too_old():
    bars = [{"ts": "2026-07-27T00:00:00+00:00", "timeframe": "5"}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["5m"]["staleness_minutes"] == pytest.approx(300.0)


def test_feed_liveness_healthy_when_bar_recent():
    bars = [{"ts": "2026-07-27T04:58:00+00:00", "timeframe": "5"}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is False
    assert liveness["5m"]["bars_seen_this_timeframe"] == 1


def test_feed_liveness_missing_bar_is_stale_not_a_crash():
    liveness = build_feed_liveness(
        _FakeBarHistory([]), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["5m"]["last_bar_ts"] is None


def test_feed_liveness_bar_with_no_timeframe_field_is_dropped_not_guessed():
    """A bar with an unparseable/missing `timeframe` must not be silently
    attributed to any bucket -- that would let an untagged bar fill a gap it
    cannot actually prove is filled."""
    bars = [{"ts": "2026-07-27T04:58:00+00:00", "timeframe": None}]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5,),
    )
    assert liveness["5m"]["bars_seen_this_timeframe"] == 0
    assert liveness["5m"]["stale"] is True


def test_feed_liveness_fresh_15m_bar_cannot_mask_stale_5m_feed():
    """The exact bug: a fresh 15m bar must not make the 5m check look
    current, and vice versa -- each timeframe is judged only by bars actually
    labeled that timeframe."""
    bars = [
        {"ts": "2026-07-27T00:00:00+00:00", "timeframe": "5"},  # 5m: 5 hours stale
        {"ts": "2026-07-27T04:59:00+00:00", "timeframe": "15"},  # 15m: fresh
    ]
    liveness = build_feed_liveness(
        _FakeBarHistory(bars), "MNQ", for_date=date(2026, 7, 27),
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5, 15),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["15m"]["stale"] is False


def test_feed_liveness_real_bar_history_fresh_15m_cannot_mask_stale_5m(tmp_path):
    """Same proof as above, but against the ACTUAL context.bar_history.BarHistory
    class and its real on-disk mixed-timeframe file -- not a hand-rolled fake
    reproducing the same (buggy, pre-fix) interface the unit test's fake used."""
    from context.bar_history import BarHistory

    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    history = BarHistory(log_dir=str(log_dir))
    for_date = date(2026, 7, 27)

    history.record(
        "MNQ", ts="2026-07-27T00:00:00+00:00", open=1, high=1, low=1, close=1,
        timeframe="5", for_date=for_date,
    )
    history.record(
        "MNQ", ts="2026-07-27T04:59:00+00:00", open=1, high=1, low=1, close=1,
        timeframe="15", for_date=for_date,
    )

    liveness = build_feed_liveness(
        history, "MNQ", for_date=for_date,
        now=datetime(2026, 7, 27, 5, 0, tzinfo=timezone.utc), required_minutes=(5, 15),
    )
    assert liveness["5m"]["stale"] is True
    assert liveness["5m"]["last_bar_ts"] == "2026-07-27T00:00:00+00:00"
    assert liveness["15m"]["stale"] is False
    assert liveness["15m"]["last_bar_ts"] == "2026-07-27T04:59:00+00:00"


# ── atomic write / schema validation ────────────────────────────────────────

def _minimal_valid_snapshot() -> dict:
    return {
        "schema_version": "1.0.0", "generated_at": "t", "generator": {}, "repo": {},
        "deployed_sha": UNKNOWN, "runtime_drift": {}, "data_freshness": {}, "runtime_lanes": [],
        "strategy_evidence": [], "trade_chain": {}, "repo_health": {}, "blockers": [],
        "source_of_truth_conflict": False, "unknown_fields": [],
    }


def test_validate_snapshot_schema_accepts_minimal_valid_snapshot():
    assert validate_snapshot_schema(_minimal_valid_snapshot()) == []


def test_validate_snapshot_schema_rejects_missing_key():
    broken = _minimal_valid_snapshot()
    del broken["blockers"]
    errors = validate_snapshot_schema(broken)
    assert any("blockers" in e for e in errors)


def test_write_snapshot_atomic_writes_valid_json(tmp_path):
    target = tmp_path / "snapshot.json"
    write_snapshot_atomic(target, _minimal_valid_snapshot())
    assert json.loads(target.read_text())["schema_version"] == "1.0.0"


def test_write_snapshot_atomic_preserves_last_known_good_on_invalid_write(tmp_path):
    target = tmp_path / "snapshot.json"
    good = _minimal_valid_snapshot()
    write_snapshot_atomic(target, good)
    before = target.read_text()

    broken = dict(good)
    del broken["trade_chain"]
    with pytest.raises(ValueError):
        write_snapshot_atomic(target, broken)

    assert target.read_text() == before  # untouched, not blanked or partially written


def test_write_snapshot_atomic_never_leaves_a_temp_file_behind(tmp_path):
    target = tmp_path / "snapshot.json"
    write_snapshot_atomic(target, _minimal_valid_snapshot())
    leftovers = list(tmp_path.glob("*.tmp"))
    assert leftovers == []


# ── full-snapshot integration ────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _build_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "risk_rules.yaml").write_text(
        """
trading_mode:
  live_trading_enabled: false
  paper_mode: true
instruments:
  allowed: [MES, MNQ]
schedule:
  mode: current
strategy:
  enabled_concepts: [orb_reclaim]
  disabled_concepts_per_instrument: {}
""".lstrip(),
        encoding="utf-8",
    )
    inventory_dir = repo / "docs" / "strategy-rules"
    inventory_dir.mkdir(parents=True)
    (inventory_dir / "Strategy_Inventory.md").write_text(_INVENTORY_MD, encoding="utf-8")
    (repo / ".gitignore").write_text("logs/\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "risk_rules.yaml", "docs/strategy-rules/Strategy_Inventory.md", ".gitignore"],
        cwd=repo, check=True, stdout=subprocess.DEVNULL,
    )
    _git(repo, "commit", "-m", "init")
    (repo / "logs").mkdir()
    return repo


def test_build_system_status_snapshot_is_deterministic_and_schema_valid(tmp_path):
    repo = _build_repo(tmp_path)
    when = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)

    first = build_system_status_snapshot(
        repo_root=repo, log_dir="logs", for_date=date(2026, 7, 27), generated_at=when, env={},
    )
    second = build_system_status_snapshot(
        repo_root=repo, log_dir="logs", for_date=date(2026, 7, 27), generated_at=when, env={},
    )
    assert first == second
    assert validate_snapshot_schema(first) == []


def test_build_system_status_snapshot_marks_unknown_without_release_manifest(tmp_path):
    repo = _build_repo(tmp_path)
    snapshot = build_system_status_snapshot(repo_root=repo, log_dir="logs", env={})
    assert snapshot["deployed_sha"] == UNKNOWN
    assert "deployed_sha" in snapshot["unknown_fields"]


def test_build_system_status_snapshot_never_writes_to_risk_rules(tmp_path):
    repo = _build_repo(tmp_path)
    before = (repo / "risk_rules.yaml").read_text()
    build_system_status_snapshot(repo_root=repo, log_dir="logs", env={})
    after = (repo / "risk_rules.yaml").read_text()
    assert before == after


def test_build_system_status_snapshot_lane_preserves_per_instrument_entry_tolerance(tmp_path):
    repo = _build_repo(tmp_path)
    env = {"ENTRY_SLIPPAGE_TOLERANCE_TICKS_MES": "16", "ENTRY_SLIPPAGE_TOLERANCE_TICKS_MNQ": "32"}
    snapshot = build_system_status_snapshot(repo_root=repo, log_dir="logs", env=env)
    lanes_by_instrument = {
        lane["instrument"]: lane for lane in snapshot["runtime_lanes"] if lane["strategy"] == "orb_reclaim"
    }
    assert lanes_by_instrument["MES"]["entry_tolerance_ticks"] == 16.0
    assert lanes_by_instrument["MNQ"]["entry_tolerance_ticks"] == 32.0
