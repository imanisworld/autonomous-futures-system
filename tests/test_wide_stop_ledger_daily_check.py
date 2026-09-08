"""The daily check's parked-family exemption for the hypothetical-ledger lane.

Spec §5 requires the exemption to be explicit and tested, the way
`derived_lane_source_notes` is — a fill in the lane for a PARKED strategy is
expected, not drift, and must be *reported* rather than silently dropped.
"""
from __future__ import annotations

import pytest

from ops.project_check.daily import _strategy_source_of_truth

PARKED_4HR = (
    "BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS — signal and bracket real; "
    "PARKED below $4,000 equity (B+, 2026-09-07)"
)
PARKED_322 = (
    "BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS — signal and bracket real; "
    "PARKED below $6,000 equity (B+, 2026-09-07)"
)


def _inventory(tmp_path, *rows: tuple[str, str]):
    body = [
        "## Master Table",
        "",
        "| Strategy | Verdict |",
        "|---|---|",
        *[f"| {name} | **{verdict}** |" for name, verdict in rows],
        "",
        "## Next",
    ]
    path = tmp_path / "docs" / "strategy-rules"
    path.mkdir(parents=True)
    (path / "Strategy_Inventory.md").write_text("\n".join(body), encoding="utf-8")
    return tmp_path


def _active(*concepts: str, lane_mode: str | None = None) -> dict:
    lanes = {"active_lane_summary": {"MNQ": list(concepts)}}
    if lane_mode is not None:
        lanes["wide_stop_ledger"] = {"mode": lane_mode}
    return lanes


def test_a_parked_member_active_on_the_lane_is_a_note_not_a_finding(tmp_path):
    root = _inventory(tmp_path, ("4HR Re-Trigger", PARKED_4HR))
    report = _strategy_source_of_truth(
        repo_root=root,
        rules_active_lanes=_active("strat_4hr_retrigger", lane_mode="paper_sim"),
    )
    assert report["drift_findings"] == []
    notes = report["hypothetical_ledger_notes"]
    assert len(notes) == 1
    assert notes[0]["concept_key"] == "strat_4hr_retrigger"
    assert notes[0]["ledger"] == "wide_stop_4k"
    assert notes[0]["role"] == "fill_eligible"
    assert notes[0]["label"] == "hypothetical_ledger"
    assert "not a drift finding" in notes[0]["note"]


def test_the_322_member_routes_to_the_six_k_ledger(tmp_path):
    root = _inventory(tmp_path, ("60M 3-2-2 First Live", PARKED_322))
    report = _strategy_source_of_truth(
        repo_root=root,
        rules_active_lanes=_active("strat_322_first_live", lane_mode="paper_sim"),
    )
    assert report["drift_findings"] == []
    assert report["hypothetical_ledger_notes"][0]["ledger"] == "wide_stop_6k"


def test_the_exemption_does_not_apply_when_the_lane_is_off(tmp_path, monkeypatch):
    """If the strategy is active with the lane disabled, that IS drift."""
    monkeypatch.delenv("WIDE_STOP_LEDGER_MODE", raising=False)
    root = _inventory(tmp_path, ("4HR Re-Trigger", PARKED_4HR))
    report = _strategy_source_of_truth(
        repo_root=root,
        rules_active_lanes=_active("strat_4hr_retrigger", lane_mode="observe_only"),
    )
    assert report["hypothetical_ledger_notes"] == []
    assert len(report["drift_findings"]) == 1
    assert report["drift_findings"][0]["concept_key"] == "strat_4hr_retrigger"


def test_a_plain_broken_strategy_is_still_drift_even_with_the_lane_on(tmp_path):
    """The exemption keys on the PARKED wording, not on membership alone."""
    root = _inventory(tmp_path, ("VWAP Hold", "BROKEN — negative evidence"))
    report = _strategy_source_of_truth(
        repo_root=root,
        rules_active_lanes=_active("vwap_hold", lane_mode="paper_sim"),
    )
    assert report["hypothetical_ledger_notes"] == []
    assert len(report["drift_findings"]) == 1


def test_a_parked_row_that_is_not_active_produces_nothing(tmp_path):
    root = _inventory(tmp_path, ("4HR Re-Trigger", PARKED_4HR))
    report = _strategy_source_of_truth(
        repo_root=root, rules_active_lanes=_active(lane_mode="paper_sim")
    )
    assert report["drift_findings"] == []
    assert report["hypothetical_ledger_notes"] == []


def test_the_lane_mode_is_reported_so_the_exemption_is_auditable(tmp_path):
    root = _inventory(tmp_path, ("4HR Re-Trigger", PARKED_4HR))
    report = _strategy_source_of_truth(
        repo_root=root,
        rules_active_lanes=_active("strat_4hr_retrigger", lane_mode="paper_sim"),
    )
    assert report["wide_stop_ledger_mode"] == "paper_sim"


def test_lane_mode_falls_back_to_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("WIDE_STOP_LEDGER_MODE", "paper_sim")
    root = _inventory(tmp_path, ("4HR Re-Trigger", PARKED_4HR))
    report = _strategy_source_of_truth(
        repo_root=root, rules_active_lanes=_active("strat_4hr_retrigger")
    )
    assert report["wide_stop_ledger_mode"] == "paper_sim"
    assert report["drift_findings"] == []
