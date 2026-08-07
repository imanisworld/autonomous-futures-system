from __future__ import annotations

from types import SimpleNamespace

from ops import project_check_runtime as pcr


def _config(**overrides):
    base = dict(
        mnq_orb_reclaim_proof_mode="observe_only",
        mnq_orb_breakout_proof_mode="observe_only",
        mnq_orb_breakout_inverse_mode="observe_only",
        mnq_vwap_hold_proof_mode="observe_only",
        mnq_strat_22_reversal_mode="observe_only",
        mnq_strat_22_continuation_mode="observe_only",
        mnq_strat_32_mode="observe_only",
        mnq_strat_322_mode="observe_only",
        mes_trend_consolidation_break_mode="observe_only",
        entry_refresh_mode="off",
        entry_refresh_instruments=("MNQ",),
        vwap_hold_early_mode="off",
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": 32.0, "MES": 16.0},
        max_contracts_per_instrument={"MNQ": 1},
        max_contracts_hard_cap=1,
        strategy_permission_gate_enabled=True,
        strategy_permission_default_status="SHADOW_ONLY",
        strategy_status={"orb_breakout": "PAPER_ELIGIBLE"},
        enabled_concepts=["orb_breakout"],
        strategy_selection_mode="ranked",
        disabled_concepts_per_instrument={},
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_active_lanes_empty_when_all_observe_only():
    assert pcr.active_lanes(_config()) == []


def test_active_lanes_reports_active_lane_with_context():
    config = _config(mnq_orb_breakout_inverse_mode="paper_sim")
    lanes = pcr.active_lanes(config)
    assert len(lanes) == 1
    lane = lanes[0]
    assert lane["lane"] == "mnq_orb_breakout_inverse"
    assert lane["execution_mode"] == "paper_sim"
    assert lane["instrument"] == "MNQ"
    assert lane["entry_fill_model"] == "ioc_limit"
    assert lane["effective_entry_tolerance_ticks"] == 32.0
    assert lane["contract_cap"] == 1


def test_active_lanes_none_config_returns_empty():
    assert pcr.active_lanes(None) == []


def test_entry_refresh_lane_uses_dynamic_instrument_list():
    config = _config(entry_refresh_mode="shadow", entry_refresh_instruments=("MES", "MNQ"))
    lanes = pcr.active_lanes(config)
    lane = next(l for l in lanes if l["lane"] == "entry_refresh")
    assert lane["instrument"] == "MES,MNQ"


def test_strategy_permission_snapshot_none_config():
    snap = pcr.strategy_permission_snapshot(None)
    assert snap["enabled"] == "UNKNOWN"
    assert snap["strategy_status"] == {}


def test_strategy_permission_snapshot_reports_config():
    snap = pcr.strategy_permission_snapshot(_config())
    assert snap["enabled"] is True
    assert snap["default_status"] == "SHADOW_ONLY"
    assert snap["strategy_status"] == {"orb_breakout": "PAPER_ELIGIBLE"}


def test_intended_release_identity_unset(monkeypatch):
    monkeypatch.delenv("EXPECTED_LIVE_BRANCH", raising=False)
    monkeypatch.delenv("EXPECTED_LIVE_COMMIT", raising=False)
    identity = pcr.intended_release_identity()
    assert identity["expected_branch"] == "UNKNOWN"
    assert identity["expected_commit"] == "UNKNOWN"


def test_intended_release_identity_set(monkeypatch):
    monkeypatch.setenv("EXPECTED_LIVE_BRANCH", "main")
    monkeypatch.setenv("EXPECTED_LIVE_COMMIT", "abc123")
    identity = pcr.intended_release_identity()
    assert identity["expected_branch"] == "main"
    assert identity["expected_commit"] == "abc123"


def test_parse_strategy_inventory_missing_file(tmp_path):
    result = pcr.parse_strategy_inventory(tmp_path / "does-not-exist.md")
    assert result["available"] is False
    assert result["rows"] == []


def test_parse_strategy_inventory_parses_master_table(tmp_path):
    doc = tmp_path / "Strategy_Inventory.md"
    doc.write_text(
        "\n".join(
            [
                "# STRATEGY INVENTORY",
                "",
                "## Master Table",
                "",
                "| Strategy | Rules | Verdict |",
                "|---|---|---|",
                "| ORB Reclaim (MES) | ✅ | **PAPER PROOF** |",
                "| VWAP Rejection | ❌ | **BROKEN — unreachable predicate** |",
                "",
                "## Some Other Section",
                "| Strategy | Verdict |",
                "|---|---|",
                "| Should Not Appear | **VALIDATED** |",
            ]
        ),
        encoding="utf-8",
    )
    result = pcr.parse_strategy_inventory(doc)
    assert result["available"] is True
    assert result["rows"] == [
        {"strategy": "ORB Reclaim (MES)", "verdict": "PAPER PROOF"},
        {"strategy": "VWAP Rejection", "verdict": "BROKEN — unreachable predicate"},
    ]
