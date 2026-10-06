"""One planned-risk definition, fail-closed open-risk state, no new legacy authority.

Planned risk is (premium entry - premium stop) x 100 x contracts. Full premium
debit is capital deployed, never planned risk.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from alert_ranker.contract_marks import aggregate_open_planned_risk
from alert_ranker.paper_v1 import (
    CONTRACT_MULTIPLIER,
    MAX_AGGREGATE_OPEN_RISK_DOLLARS,
    MAX_TRADE_RISK_DOLLARS,
    build_v1_contract_fields,
    choose_contract,
    choose_expiration,
)
from options_manager.validation.contract_quality_gate import DEFAULT_MAX_DOLLAR_RISK
from options_manager.validation.portfolio_risk_gate import (
    DEFAULT_MAX_TRADE_RISK_DOLLARS,
    planned_risk_from_premium_stop,
)
from tests.test_options_paper_v1 import GOOD_EXPIRY, NOW, quote

ROOT = Path(__file__).resolve().parents[1]


def _v1_inputs():
    expiry = choose_expiration([GOOD_EXPIRY], NOW).expiry
    contract = choose_contract((quote(),), option_type="CALL", underlying_price=500.0).contract
    return expiry, contract


def _storage(*rows):
    class Storage:
        def open_setups_after(self, after_id):
            return list(rows) if after_id == 0 else []

    return Storage()


def _active(risk, row_id=1):
    return SimpleNamespace(
        id=row_id,
        selected_contract={
            "paper_policy_id": "OPTIONS_PAPER_V1",
            "planned_risk_dollars": risk,
            "paper_evidence_lane": "ACTIVE",
            "risk_budget_consumed": True,
        },
    )


@pytest.mark.parametrize("damaged", [float("nan"), "NaN", "nan", float("inf"), "-inf", -1.0, None, "x"])
def test_damaged_active_risk_row_reads_as_infinite(damaged):
    assert aggregate_open_planned_risk(_storage(_active(100.0), _active(damaged, 2))) == float("inf")


@pytest.mark.parametrize("open_risk", [float("nan"), float("inf"), -5.0, None, "abc"])
def test_v1_refuses_unknown_open_risk(open_risk):
    expiry, contract = _v1_inputs()
    fields, reason = build_v1_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=497.0,
        target_1=510.0,
        aggregate_open_risk=open_risk,
    )
    assert fields is None
    assert reason == "open_risk_state_invalid"


def test_v1_and_canonical_compute_the_same_planned_risk():
    expiry, contract = _v1_inputs()
    fields, reason = build_v1_contract_fields(
        expiry=expiry,
        contract=contract,
        underlying_invalidation=497.0,
        target_1=510.0,
        aggregate_open_risk=0.0,
    )
    assert reason == ""
    canonical, error = planned_risk_from_premium_stop(
        entry_fill=fields["option_mark"],
        premium_stop=fields["premium_stop"],
        contracts=1,
        max_trade_risk_dollars=DEFAULT_MAX_TRADE_RISK_DOLLARS,
    )
    assert error is None
    assert math.isclose(canonical, fields["planned_risk_dollars"], abs_tol=0.01)
    full_debit = fields["option_mark"] * CONTRACT_MULTIPLIER
    assert fields["planned_risk_dollars"] < full_debit


def test_risk_caps_agree_across_layers():
    assert MAX_TRADE_RISK_DOLLARS == DEFAULT_MAX_TRADE_RISK_DOLLARS == DEFAULT_MAX_DOLLAR_RISK == 300.0
    assert MAX_AGGREGATE_OPEN_RISK_DOLLARS == 1000.0


# Modules that still hold a legacy full-debit cap. They are not planned-risk
# authority. Any *new* production importer is a second authority and must be
# reviewed against options_manager/validation/portfolio_risk_gate.py instead.
LEGACY_FULL_DEBIT_MODULES = {
    "options_manager.risk_gate": {
        # Legacy Phase 2-8 packet chain. It consumes RiskGateResult (full-debit
        # cap) but is not wired into any service; options_manager/app.py uses
        # the canonical advisory_decision path. Must be re-pointed at the
        # canonical portfolio gate before this chain is ever wired.
        "options_manager/paper_sim.py",
        "options_manager/fill_stress.py",
        "options_manager/dry_run_review.py",
    },
    "risk.options_risk_engine": {
        # Futures options companion; OPTIONS_COMPANION_ENABLED defaults False.
        "options_companion/evaluator.py",
        "options_companion/store.py",  # OptionsDailyState type only
    },
}


def _production_importers(module: str) -> set[str]:
    found: set[str] = set()
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(("tests/", ".venv/", "venv/")) or "/site-packages/" in rel:
            continue
        if rel == module.replace(".", "/") + ".py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        package = rel.rsplit("/", 1)[0].replace("/", ".")
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level:
                    base = package.split(".")[: len(package.split(".")) - node.level + 1]
                    name = ".".join([*base, name]) if name else ".".join(base)
                if name == module:
                    found.add(rel)
            elif isinstance(node, ast.Import):
                if any(alias.name == module for alias in node.names):
                    found.add(rel)
    return found


def test_canonical_service_does_not_use_legacy_risk_gate():
    tree = ast.parse((ROOT / "options_manager" / "app.py").read_text(encoding="utf-8"))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "risk_gate" not in imported
    assert "validation.advisory_decision" in imported


@pytest.mark.parametrize("module", sorted(LEGACY_FULL_DEBIT_MODULES))
def test_no_new_production_importer_of_legacy_full_debit_risk(module):
    assert _production_importers(module) == LEGACY_FULL_DEBIT_MODULES[module]


# ── manual Robinhood evaluator (/rh-options/evaluate) ───────────────────────

def _rh_inputs(**overrides):
    from dataclasses import fields

    from alert_ranker.rh_options import RHOptionsInput

    values = dict(
        ticker="SPY",
        direction="LONG",
        contract_type="CALL",
        signa_score=80.0,
        signa_grade="A",
        signa_daily_direction="BULLISH",
        signa_weekly_direction="BULLISH",
        gex_regime="POS_GAMMA",
        gex_support_wall=None,
        gex_resistance_wall=None,
        current_price=500.0,
        premium=2.40,
        expiry_date="2026-12-18",
        dte=60,
        strike=505.0,
        max_premium_per_contract=500.0,
        quantity=1,
        max_contracts=10,
    )
    values.update(overrides)
    names = {f.name for f in fields(RHOptionsInput)}
    return RHOptionsInput(**{k: v for k, v in values.items() if k in names})


def test_rh_evaluator_caps_planned_risk_not_full_debit():
    from alert_ranker.rh_options import _risk_check

    # Swing: stop = 0.5 x premium. 2 contracts at $2.40 -> debit $480,
    # planned risk (2.40 - 1.20) x 100 x 2 = $240 -> within the $300 cap.
    ok = _risk_check(_rh_inputs(quantity=2))
    assert ok["approved"] is True, ok
    # 3 contracts -> planned risk $360 > $300, though debit cap ($5,000) allows it.
    refused = _risk_check(_rh_inputs(quantity=3))
    assert refused["approved"] is False
    assert refused["failed_rule"] == "planned_risk_cap"


def test_rh_evaluator_refuses_non_finite_premium():
    from alert_ranker.rh_options import _risk_check

    out = _risk_check(_rh_inputs(premium=float("nan")))
    assert out["approved"] is False
